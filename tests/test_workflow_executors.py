"""
tests/test_workflow_executors.py

The node executor interface (CHY-127 Schritt 2, ADR-028 Punkt 3).

What is worth testing here is mostly what the registry **refuses**, and why the
refusals are loud rather than quiet.

Two node types have no default executor — `llm_call` because the completion
path is only reachable over HTTP (CHY-129), `tool_call` because nothing defines
what a tool call is. Guarded edges have no evaluator because the condition
format is undecided (CHY-130).

In all three cases the alternative to raising is a default that quietly does
nothing, and a run that reaches `complete` having executed none of its work
looks exactly like a run that succeeded. That is the failure mode this project
keeps meeting under other names, so the tests below are mostly about making
sure it stays impossible.
"""
from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from api.v1 import workflows
from api.v1.workflows import get_executors
from contexts.orchestration.errors import (
    MalformedCondition,
    NodeExecutionFailed,
    NodeTypeNotExecutable,
)
from contexts.orchestration.execution import (
    ExecutorRegistry,
    NodeContext,
    NodeResult,
    evaluate_edge_condition,
)
from contexts.orchestration.schema import ContextRequirements
from core.completion import CompletionResult
from kernel.redaction import RedactionPolicy
from router.rules import RoutingRule


@pytest.fixture(autouse=True)
def _shipped_redaction_rules_only(monkeypatch):
    """The executors here run without a database (`pool=None`).

    Reading a scope's redaction rules needs one (CHY-63 part 2), so every test
    here runs under the shipped rules alone. What a scope's rules do to a model
    call is tested in tests/test_redaction_rules.py and through the real
    reading in tests/test_workflow_over_repository.py.
    """
    async def shipped_only(_pool, _scope_id):
        return RedactionPolicy()

    monkeypatch.setattr(workflows, "redaction_policy_for", shipped_only)


def _ctx(node_type: str, node_ref: str = "n", **kw) -> NodeContext:
    """A node context, with everything the caller did not pin made up.

    `workflow_id` is a keyword here rather than a fixed value because
    `node_ref` is unique only per workflow: the tests that care about that
    distinction need two contexts that differ in exactly this field.
    """
    return NodeContext(
        run_id=uuid4(), workflow_id=kw.pop("workflow_id", uuid4()),
        node_id=uuid4(), node_ref=node_ref,
        node_type=node_type, iteration=1, **kw,
    )


# ── gateways ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("node_type", ["fork_gateway", "join_gateway"])
async def test_a_gateway_completes_and_produces_nothing(node_type: str):
    """"Succeeds and produces nothing" is not "does nothing".

    Completing is what places tokens on the outgoing edges. A gateway that
    never completed would deadlock every fork in the estate.
    """
    executor = ExecutorRegistry().for_type(node_type, "gate")
    result = await executor(_ctx(node_type))
    assert result == NodeResult()


# ── what is refused, and how ──────────────────────────────────────────────────

class TestRefusals:
    @pytest.mark.parametrize("node_type", ["llm_call", "tool_call"])
    async def test_a_type_without_an_executor_raises(self, node_type: str):
        executor = ExecutorRegistry().for_type(node_type, "step")
        with pytest.raises(NodeTypeNotExecutable) as refused:
            await executor(_ctx(node_type, "step"))
        assert node_type in str(refused.value)

    async def test_llm_call_points_at_the_reason_it_cannot_be_built_here(self):
        """The message has to say *why*, or the next person writes the executor
        and duplicates the completion path."""
        executor = ExecutorRegistry().for_type("llm_call", "summarize")
        with pytest.raises(NodeTypeNotExecutable) as refused:
            await executor(_ctx("llm_call", "summarize"))
        assert "CHY-129" in str(refused.value)

    def test_human_review_gets_its_own_explanation(self):
        """It has no executor by design — the state machine stops before it.

        "No executor registered" would be true and would send whoever reads it
        looking for a missing registration instead of a bypassed plan.
        """
        with pytest.raises(NodeTypeNotExecutable) as refused:
            ExecutorRegistry().for_type("human_review", "approve")
        message = str(refused.value)
        assert "never executed" in message
        assert "plan was bypassed" in message

    async def test_a_malformed_guard_raises_rather_than_answering_false(self):
        """False would be worse than raising.

        The state machine reads False as "this arm did not match", the run
        takes the default arm or fails for want of one, and the message blames
        the workflow's shape for what is a typo in a guard (CHY-130).
        """
        with pytest.raises(MalformedCondition):
            await evaluate_edge_condition(uuid4(), {"op": "eq"}, NodeResult())

    async def test_an_unguarded_edge_is_always_taken(self):
        """Counter-probe: the evaluator must not refuse the common case. Most
        edges carry no guard at all."""
        assert await evaluate_edge_condition(uuid4(), None, NodeResult()) is True


# ── injection is the way in ───────────────────────────────────────────────────

class TestInjection:
    async def test_an_injected_executor_takes_over(self):
        """ADR-028 point 3: a node type stays delegable.

        The composition root can reach what this context cannot — calling a
        model, for one. That inversion is the whole reason the interface exists.
        """
        async def fake_llm(ctx: NodeContext) -> NodeResult:
            return NodeResult(output={"text": "hello"}, model_used="gemma4-e4b")

        registry = ExecutorRegistry({"llm_call": fake_llm})
        result = await registry.for_type("llm_call", "summarize")(_ctx("llm_call"))
        assert result.output == {"text": "hello"}
        assert result.model_used == "gemma4-e4b"

    async def test_registering_later_works_too(self):
        registry = ExecutorRegistry()
        assert not registry.has("tool_call")

        async def fake_tool(ctx: NodeContext) -> NodeResult:
            return NodeResult(output={"ok": True})

        registry.register("tool_call", fake_tool)
        assert registry.has("tool_call")
        assert (await registry.for_type("tool_call", "t")(_ctx("tool_call"))).output == {"ok": True}

    async def test_injecting_does_not_disturb_the_gateways(self):
        """Counter-probe: a registry built from a caller's mapping must not
        lose the routing defaults, or injecting an llm_call would deadlock
        every fork."""
        async def fake_llm(ctx: NodeContext) -> NodeResult:
            return NodeResult()

        registry = ExecutorRegistry({"llm_call": fake_llm})
        assert await registry.for_type("fork_gateway", "f")(_ctx("fork_gateway")) == NodeResult()


# ── what the executor may see ─────────────────────────────────────────────────

def test_the_context_carries_no_graph():
    """An executor that could see the graph would eventually route.

    Routing belongs to the state machine — one place, testable without a
    database. Asserted on the field list because the temptation to pass "just
    the graph, for context" is real and would be invisible in review.
    """
    fields = set(NodeContext.__dataclass_fields__)
    assert "graph" not in fields
    assert not {f for f in fields if "edge" in f or "connection" in f}
    assert {"run_id", "node_id", "node_ref", "iteration", "config"} <= fields


# ── what this deployment can actually run (CHY-133) ───────────────────────────

class TestTheDeploymentCanRunTheMandatoryCore:
    """The guard whose absence let `context_compose` go missing.

    Every valid workflow contains all three mandatory node types — the
    validator refuses to activate one that does not (ADR-004). Yet nothing
    checked that this deployment can *execute* them, so `context_compose` had
    no executor for as long as the runner existed and every real run would have
    died on its first node.

    Driven from `MANDATORY_NODE_TYPES` rather than a list written out here: a
    fourth mandatory type would otherwise be enforced by the validator and
    silently unrunnable, which is the same bug with a different name.
    """

    def test_every_mandatory_type_that_needs_an_executor_has_one(self):
        from contexts.orchestration.graph import MANDATORY_NODE_TYPES

        registry = get_executors(pool=None, redis=None)
        missing = [
            t for t in sorted(set(MANDATORY_NODE_TYPES) - {"human_review"})
            if not registry.has(t)
        ]
        assert missing == [], (
            f"the validator requires {missing} in every workflow, and this "
            f"deployment cannot execute them — every run dies on that node"
        )

    def test_human_review_is_deliberately_absent(self):
        """Counter-probe: the test above must not pass by registering
        everything. `human_review` is a wait state, and an executor for it
        would mean the state machine's plan was bypassed."""
        assert not get_executors(pool=None, redis=None).has("human_review")


class TestComposeExecutor:
    async def _run(self, monkeypatch, ctx, *, seen: list):
        from api.v1 import workflows

        async def fake_compose(_pool, _redis, req):
            seen.append(req)
            return SimpleNamespace(
                as_prompt=lambda: "composed prompt",
                block_versions={"b1": 3}, scope_generations={"s1": 7},
                cache_hit=False,
            )

        monkeypatch.setattr(workflows.repository, "compose_context", fake_compose)
        return await workflows._compose_for(None, None)(ctx)

    async def test_the_request_carries_the_workflow_and_not_just_the_ref(
        self, monkeypatch
    ):
        """`node_ref` is unique only per workflow.

        Without `workflow_id` in the request, two workflows that both name a
        node `compose` share one cache entry and each receives the other's
        context — and it looks exactly like a cache hit, not like a bug.
        """
        seen: list = []
        scope = uuid4()
        first, second = uuid4(), uuid4()

        for workflow_id in (first, second):
            await self._run(
                monkeypatch,
                _ctx("context_compose", "compose",
                     workflow_id=workflow_id, scope_id=scope),
                seen=seen,
            )

        assert [r.workflow_id for r in seen] == [first, second], (
            "both requests carried the same key, so the two workflows would "
            "share a composed context"
        )
        assert {r.node_ref for r in seen} == {"compose"}

    async def test_the_types_come_from_the_column_and_no_model_is_named(
        self, monkeypatch
    ):
        """Block types from the typed column (CHY-134), and no model at all.

        Until CHY-145 the compose node passed `config["model"]` as part of the
        cache key. That key is gone, and a compose node does not know which
        model its output will reach — the llm_call downstream resolves that.
        """
        seen: list = []
        await self._run(
            monkeypatch,
            _ctx("context_compose", "compose", scope_id=uuid4(),
                 context_requirements=ContextRequirements(
                     block_types=["system_prompt"])),
            seen=seen,
        )
        assert seen[0].model_id is None
        assert seen[0].block_types == ["system_prompt"]

    async def test_no_requirements_means_everything_the_scope_offers(
        self, monkeypatch
    ):
        """Absent, not empty.

        `None` reaches the store as "no filter", which is the ordinary case for
        a compose node. The empty list would be read the same way while saying
        the opposite, which is why migration 022 refuses it.
        """
        seen: list = []
        await self._run(
            monkeypatch, _ctx("context_compose", "compose", scope_id=uuid4()),
            seen=seen,
        )
        assert seen[0].block_types is None

    async def test_block_types_left_in_the_config_do_not_reach_the_request(
        self, monkeypatch
    ):
        """The counter-probe for the move (CHY-134).

        A row cannot carry this key any more — `NodeCreate` refuses it and so
        does the CHECK in 022 — so this asks the remaining question: if one
        ever did, would the executor still read it? It must not, or the key
        would keep working on the old rows and the move would be half done.
        """
        seen: list = []
        await self._run(
            monkeypatch,
            _ctx("context_compose", "compose", scope_id=uuid4(),
                 config={"block_types": ["system_prompt"]}),
            seen=seen,
        )
        assert seen[0].block_types is None

    async def test_the_output_records_what_the_composition_contained(
        self, monkeypatch
    ):
        """Not decoration. `block_versions` is what makes the run reproducible;
        a record naming only the result looks complete and cannot be checked."""
        result = await self._run(
            monkeypatch, _ctx("context_compose", "compose", scope_id=uuid4()),
            seen=[],
        )
        assert result.output["prompt"] == "composed prompt"
        assert result.output["block_versions"] == {"b1": 3}
        assert result.output["scope_generations"] == {"s1": 7}

    async def test_a_workflow_without_a_scope_is_refused_not_composed_empty(self):
        """An empty context reaches a model looking exactly like a real one."""
        from api.v1 import workflows

        with pytest.raises(NodeTypeNotExecutable) as refused:
            await workflows._compose_for(None, None)(
                _ctx("context_compose", "compose", scope_id=None)
            )
        assert "scope" in str(refused.value)

    async def test_llm_call_refuses_an_empty_prompt_instead_of_sending_one(self):
        """It used to default to `""`.

        Removing this check sends the request anyway — the counter-probe gets
        `ModelRefused` from `raise_for_status`, i.e. after the call went out.
        The failure was therefore never silent, but it named the gateway rather
        than the workflow. The message has to name the mapping, or whoever
        reads it goes looking for a broken edge instead of a missing one.
        """
        from api.v1 import workflows

        with pytest.raises(NodeTypeNotExecutable) as refused:
            await workflows._call_model_for(None)(
                _ctx("llm_call", "answer", task_type="summarize", scope_id=uuid4())
            )
        message = str(refused.value)
        assert "answer" in message
        assert "input_mapping" in message


class TestLlmCallExecutor:
    """What the executor does around the chain (CHY-145). The chain itself is
    driven end to end in tests/test_workflow_over_repository.py."""

    async def test_a_workflow_without_a_scope_cannot_be_routed(self):
        from api.v1 import workflows

        with pytest.raises(NodeTypeNotExecutable) as refused:
            await workflows._call_model_for(None)(
                _ctx("llm_call", "answer", task_type="summarize", scope_id=None,
                     config={"prompt": "hi"})
            )
        assert "scope" in str(refused.value)

    async def test_a_model_that_refuses_ends_the_node_as_failed_not_escaping(
        self, monkeypatch
    ):
        """`CompletionError` is not the runner's family. Raised as it is, it
        passed through and left the run at `running` (CHY-145). Translated, it
        is a failure the runner records — with the chain in the message."""
        from api.v1 import workflows
        from contexts.orchestration.errors import NodeExecutionFailed
        from core.completion import ModelUnreachable

        from router.rules import RoutingRule

        async def chain(*_a, **_k):
            return RoutingRule("summarize", ("gemma4-cloud", "gemma4-e4b"),
                               uuid4(), uuid4(), 1, max_tokens=256)

        async def unreachable(*_a, **_k):
            raise ModelUnreachable("gateway silent")

        monkeypatch.setattr(workflows, "route_for", chain)
        monkeypatch.setattr(workflows.completion, "complete_with_fallback", unreachable)
        with pytest.raises(NodeExecutionFailed) as failed:
            await workflows._call_model_for(None)(
                _ctx("llm_call", "answer", task_type="summarize", scope_id=uuid4(),
                     config={"prompt": "hi"})
            )
        message = str(failed.value)
        assert "summarize" in message
        assert "gemma4-cloud" in message


class TestTheModelGetsContextAndRequest:
    """`system` reaches the model through `input_mapping`, like `prompt` (CHY-189).

    Until CHY-189 the system message came from the node's config only. A
    workflow could hand the model the composed context *or* the run's input —
    the customer-reply example of the quickstart needs both, and `/v1/complete`
    has always sent both (system = composition, user = prompt).
    """

    @staticmethod
    def _capture(monkeypatch):
        from api.v1 import workflows
        from router.rules import RoutingRule

        sent = []

        async def chain(*_a, **_k):
            return RoutingRule("draft_reply", ("local-model",), uuid4(), uuid4(), 1,
                               max_tokens=256)

        async def answer(request, candidates, **_k):
            sent.append(request)
            return CompletionResult(content="draft", model_used=candidates[0])

        monkeypatch.setattr(workflows, "route_for", chain)
        monkeypatch.setattr(workflows.completion, "complete_with_fallback", answer)
        return workflows, sent

    async def test_a_mapped_system_message_reaches_the_request(self, monkeypatch):
        workflows, sent = self._capture(monkeypatch)
        await workflows._call_model_for(None)(
            _ctx("llm_call", "draft", task_type="draft_reply", scope_id=uuid4(),
                 input_data={"system": "Be polite.", "prompt": "Where is my order?"})
        )
        assert sent[0].system == "Be polite."
        assert sent[0].prompt == "Where is my order?"

    async def test_the_config_wins_over_the_input(self, monkeypatch):
        workflows, sent = self._capture(monkeypatch)
        await workflows._call_model_for(None)(
            _ctx("llm_call", "draft", task_type="draft_reply", scope_id=uuid4(),
                 config={"system": "Fixed."},
                 input_data={"system": "Mapped.", "prompt": "hi"})
        )
        assert sent[0].system == "Fixed."

    async def test_without_either_the_system_message_stays_empty(self, monkeypatch):
        workflows, sent = self._capture(monkeypatch)
        await workflows._call_model_for(None)(
            _ctx("llm_call", "draft", task_type="draft_reply", scope_id=uuid4(),
                 input_data={"prompt": "hi"})
        )
        assert sent[0].system == ""


class TestIdentifiersFromTheRunInput:
    """An `llm_call` takes `identifiers_to_redact` from the run input (CHY-63).

    From the run input, so a name listed once is replaced at every model call
    of the run -- not only at the nodes whose mapping someone extended.
    """

    @staticmethod
    def _capture(monkeypatch, **result):
        sent = []

        async def chain(*_a, **_k):
            return RoutingRule("draft_reply", ("local-model",), uuid4(), uuid4(), 1,
                               max_tokens=256)

        async def answer(request, candidates, **_k):
            sent.append(request)
            return CompletionResult(content="draft", model_used=candidates[0], **result)

        monkeypatch.setattr(workflows, "route_for", chain)
        monkeypatch.setattr(workflows.completion, "complete_with_fallback", answer)
        return workflows, sent

    async def test_the_run_inputs_identifiers_reach_the_request(self, monkeypatch):
        workflows, sent = self._capture(monkeypatch)
        await workflows._call_model_for(None)(
            _ctx("llm_call", "draft", task_type="draft_reply", scope_id=uuid4(),
                 input_data={"prompt": "Brief an Erika",
                             "run": {"identifiers_to_redact": {"PERSON": ["Erika"]}}})
        )
        assert sent[0].identifiers_to_redact == {"PERSON": ("Erika",)}

    async def test_a_run_without_a_list_still_gets_the_shipped_rules(self, monkeypatch):
        workflows, sent = self._capture(monkeypatch)
        await workflows._call_model_for(None)(
            _ctx("llm_call", "draft", task_type="draft_reply", scope_id=uuid4(),
                 input_data={"prompt": "hi", "run": {"message": "hi"}})
        )
        assert sent[0].identifiers_to_redact == {}
        assert sent[0].redaction_rules  # the shipped rules, not an empty tuple

    async def test_a_malformed_list_fails_the_node_before_any_model_is_asked(
        self, monkeypatch
    ):
        workflows, sent = self._capture(monkeypatch)
        with pytest.raises(NodeExecutionFailed, match="identifiers_to_redact"):
            await workflows._call_model_for(None)(
                _ctx("llm_call", "draft", task_type="draft_reply", scope_id=uuid4(),
                     input_data={"prompt": "hi",
                                 "run": {"identifiers_to_redact": {"PERSON": "Erika"}}})
            )
        assert sent == []

    async def test_the_counts_go_into_the_nodes_output(self, monkeypatch):
        workflows, _ = self._capture(
            monkeypatch, redactions={"PERSON": 2}, unresolved_placeholders=1
        )
        result = await workflows._call_model_for(None)(
            _ctx("llm_call", "draft", task_type="draft_reply", scope_id=uuid4(),
                 input_data={"prompt": "hi"})
        )
        assert result.output["redactions"] == {"PERSON": 2}
        assert result.output["unresolved_placeholders"] == 1


class TestARunThatKeepsItsPlaceholders:
    """A run started since CHY-234 hands its node the mapping, not a list.

    Every value the mapping holds is searched for, the request carries the
    mapping so the answer comes back with placeholders, and what the answer
    added goes back to the service, which seals it.
    """

    MAPPING = {"PERSON_1": "Erika Mustermann", "EMAIL_1": "erika@example.de"}

    async def test_every_value_of_the_mapping_is_searched_for(self, monkeypatch):
        """Not the list in the input: a run that keeps placeholders has none
        there, and a name given only in the input would then be searched by nobody."""
        workflows, sent = TestIdentifiersFromTheRunInput._capture(monkeypatch)
        await workflows._call_model_for(None)(
            _ctx("llm_call", "draft", task_type="draft_reply", scope_id=uuid4(),
                 run_mapping=self.MAPPING,
                 input_data={"prompt": "Brief an {{PERSON_1}}",
                             "run": {"identifiers_to_redact": {"PERSON": ["Stale"]}}})
        )
        assert sent[0].identifiers_to_redact == {"PERSON": ("Erika Mustermann",),
                                                 "EMAIL": ("erika@example.de",)}
        assert sent[0].run_mapping == self.MAPPING

    async def test_what_the_answer_added_goes_back_to_the_service(self, monkeypatch):
        extended = {**self.MAPPING, "PHONE_1": "0171 2345678"}
        workflows, _ = TestIdentifiersFromTheRunInput._capture(monkeypatch, run_mapping=extended)
        result = await workflows._call_model_for(None)(
            _ctx("llm_call", "draft", task_type="draft_reply", scope_id=uuid4(),
                 run_mapping=self.MAPPING, input_data={"prompt": "hi"})
        )
        assert result.run_mapping == extended

    async def test_a_run_started_before_it_sends_no_mapping(self, monkeypatch):
        """E6: such a run keeps its values; the answer is restored at the boundary."""
        workflows, sent = TestIdentifiersFromTheRunInput._capture(monkeypatch)
        result = await workflows._call_model_for(None)(
            _ctx("llm_call", "draft", task_type="draft_reply", scope_id=uuid4(),
                 input_data={"prompt": "hi"})
        )
        assert sent[0].run_mapping is None
        assert result.run_mapping is None
