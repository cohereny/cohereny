"""
contexts/orchestration/execution.py

What happens *at* a node, behind one interface (CHY-127, ADR-028 Punkt 3).

ADR-028 point 3 asks for this explicitly: node execution behind an interface,
so that individual node types stay delegable if the decision to build an own
runner turns out to have been wrong. The state machine in `runner.py` decides
*which* node runs; this decides what running it means, and the two never need
to know about each other's problems.

Two node types have no executor here, and that is deliberate
---------------------------------------------------------------
**`llm_call`** — the whole completion path lives in `api/v1/complete.py`, 211
lines with `httpx` in the route handler. There is no way to call a model that
does not go over HTTP (CHY-129). Writing one here would be a second truth about
how a model is called, how context reaches it and what lands in the
transparency record. So the registry takes an injected executor from the
composition root, and refuses **loudly** without one.

**`tool_call`** — no semantics are defined anywhere for what a tool call is.
An executor would be an invention, not an implementation.

Guards are refused the same way, for the same reason (CHY-130): nothing decides
how a condition is written, and returning `False` silently would read to the
state machine as "no branch matched" — a run failing with a message about a
modelling mistake when the real answer is that the capability is missing.

**Refusing loudly is the point.** A default that quietly does nothing is how a
runner reports success without having run anything.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol
from uuid import UUID

from contexts.orchestration import conditions
from contexts.orchestration.errors import NodeExecutionFailed, NodeTypeNotExecutable
from contexts.orchestration.schema import ContextRequirements
from core import ingress
import steps

#: Gateways route; they do not compute. A fork places tokens on its outgoing
#: edges and a join consumes tokens on its incoming ones, and both of those are
#: the state machine's job — so their executor is "succeed, produce nothing".
ROUTING_ONLY: frozenset[str] = frozenset({"fork_gateway", "join_gateway"})

#: `human_review` never reaches an executor: `runner.plan` stops the run before
#: it, and the gate becomes a row. Listed so the registry can say *why* rather
#: than falling through to "unknown node type", which would be true and useless.
NEVER_EXECUTED: frozenset[str] = frozenset({"human_review"})


@dataclass(frozen=True)
class NodeContext:
    """Everything a node needs, and nothing about how it was chosen.

    Deliberately without the graph. An executor that could see the graph would
    eventually make a routing decision, and routing belongs to the state
    machine — one place, testable without a database.
    """

    run_id: UUID
    #: Which workflow this run belongs to. Carried because `node_ref` is unique
    #: only *per workflow* (UNIQUE in 001_initial) — anything that keys off the
    #: ref alone would collide the moment a second workflow names a node the
    #: same way, and the collision looks like a cache hit (CHY-133).
    workflow_id: UUID
    node_id: UUID
    node_ref: str
    node_type: str
    iteration: int
    #: The node's own configuration from `workflow_nodes.config`.
    config: Mapping[str, Any] = field(default_factory=dict)
    #: What a `context_compose` node asks the repository for, from its own
    #: typed column (CHY-134, migration 022). `None` on every other node type
    #: — and on a compose node it means "everything the scope offers", which
    #: is why it is not spelled as an empty list.
    context_requirements: ContextRequirements | None = None
    #: What an `llm_call` routes by, from its own typed column (CHY-145,
    #: migration 023). `None` on every other node type. Never defaulted to the
    #: node type: that fallback used to route a typo as task "llm_call".
    task_type: str | None = None
    #: The JSON Schema an `llm_call`'s answer must match (CHY-175, migration
    #: 039). The service checks the answer; an executor reads this only to
    #: ask the gateway for JSON. `None` means "no shape asked for".
    output_schema: Mapping[str, Any] | None = None
    #: What a `compute` node runs (ADR-048, migration 040): a registered pure
    #: function and its pinned version. `None` on every other node type.
    function: str | None = None
    function_version: int | None = None
    #: What a `document` node hands on (ADR-049, migration 041): the run's
    #: document of the node's name — text with the run's placeholders, media
    #: type, pages, counted findings — read by the service at the claim. `None`
    #: on every other node type.
    document: Mapping[str, Any] | None = None
    #: The run input under `run`, each predecessor's output under its own
    #: `node_ref`, and whatever `input_mapping` selected on top (CHY-131).
    input_data: Mapping[str, Any] = field(default_factory=dict)
    #: The workflow's scope, from `workflow_definitions.scope_id`. It decides
    #: which context a `context_compose` node may see — the run does not carry
    #: one of its own, and a second scope per run would be a second answer to
    #: the question the definition already answers.
    scope_id: UUID | None = None
    #: Who started the run. Carried so a node can act as that actor rather
    #: than as the service — an LLM call recorded against nobody is a gap in
    #: exactly the trail Art. 14 asks about.
    actor_id: UUID | None = None
    #: The run's placeholder mapping as it stood when this pass was claimed
    #: (CHY-234), opened by the service — an executor never holds a key. `None`
    #: for a run started before CHY-234, whose content keeps its values (E6).
    run_mapping: Mapping[str, str] | None = None


@dataclass(frozen=True)
class NodeResult:
    """What a node produced.

    `model_used` is separate from `output` because it is not a result, it is a
    record: `node_executions.model_used` answers "which model saw this step",
    per node and per pass. "The run used gemma4-e4b" is not an answer for a
    workflow that routes different steps to different models.
    """

    output: Mapping[str, Any] = field(default_factory=dict)
    model_used: str | None = None
    #: The mapping as this pass leaves it: what it was handed plus whatever the
    #: model's answer added (CHY-234). The service merges it into the stored
    #: one under a row lock. `None` when the node replaced nothing.
    run_mapping: Mapping[str, str] | None = None


class NodeExecutor(Protocol):
    """One node type, executed."""

    async def __call__(self, ctx: NodeContext) -> NodeResult: ...


ConditionEvaluator = Callable[
    [UUID, Mapping[str, Any] | None, NodeResult], Awaitable[bool]
]
"""Decides whether one guarded edge may be taken.

Takes the edge id, the stored condition and the source node's result. Still an
injection point after CHY-130 gave it a default: a deployment that needs a
richer guard than the structured comparisons can substitute one, and the state
machine stays built to be *told* the answer rather than to work it out.
"""


async def _routing_only(_ctx: NodeContext) -> NodeResult:
    """A gateway succeeds and produces nothing.

    The leading underscore is not carelessness: the signature comes from
    `NodeExecutor`, and a routing node needs none of it. Renaming the parameter
    says that out loud instead of leaving a reader to wonder what was forgotten.

    Not "does nothing": it *completes*, which is what places tokens on its
    outgoing edges. A gateway that never completed would deadlock every fork.
    """
    return NodeResult()


async def _refuse(ctx: NodeContext) -> NodeResult:
    raise NodeTypeNotExecutable(ctx.node_type, ctx.node_ref)


async def _compute(ctx: NodeContext) -> NodeResult:
    """Run a registered pure function (ADR-048, CHY-173).

    Built in rather than injected: a step reaches nothing outside its input, so
    there is nothing for the composition root to supply (ADR-028 point 3 is
    about executors that need the outside world).

    The result is the output under `data`, as for an llm_call with a schema,
    so an edge or a successor reads both the same way. `function` records
    which version computed it — the step's `model_used`.

    A refused input fails the node with the step's reason, which names the
    field and never its value. Anything else a step raises is a bug in it and
    stays loud: the service records it and raises again.
    """
    step = steps.lookup(ctx.function, ctx.function_version)
    if step is None:
        # Activation refuses this (rule `unknown_function`). Reaching it means
        # the code lost a step a released definition still names.
        raise NodeExecutionFailed(
            ctx.node_ref,
            f"{ctx.function}@{ctx.function_version} is not a registered step",
        )
    try:
        data = step.run(ctx.input_data)
    except steps.StepInputError as exc:
        raise NodeExecutionFailed(ctx.node_ref, f"{step.ref}: {exc}") from exc
    return NodeResult(output={"data": data, "function": step.ref})


async def _document(ctx: NodeContext) -> NodeResult:
    """Hand one document of the run to this node's successors, fenced (ADR-049).

    Built in, like `compute`: the service read the document at the claim, and
    nothing else from outside is needed.

    **Fenced here, in the node's output**, not where a prompt is built: wherever
    the text goes, it goes as foreign data between markers (CHY-135). The scan
    that `fence` relies on ran when the document arrived, and its findings come
    along, counted. The documents are not in the run input, so the edges of
    this node are the only way a step sees one — the point of the node.
    """
    text = ctx.document.get("text") if ctx.document else None
    if text is None:
        # Unreachable while the run is the only way in: it starts with its last
        # document, and its content is cleared only once it is over. A run
        # stopped here says so rather than handing on an empty document.
        raise NodeExecutionFailed(ctx.node_ref, "the run holds no text for its document")
    return NodeResult(output={
        "text": ingress.fence(text),
        "name": ctx.document["name"],
        "media_type": ctx.document["media_type"],
        "pages": ctx.document["pages"],
        "findings": list(ctx.document["findings"]),
    })


async def evaluate_edge_condition(
    _edge_id: UUID, condition: Mapping[str, Any] | None, result: NodeResult
) -> bool:
    """The default guard evaluator (CHY-130).

    An unguarded edge is always taken; there is nothing to judge. A guarded one
    is judged against the source node's output — which the runner has in hand
    at exactly this moment, and which is why edge conditions did not have to
    wait for the general node-to-node data flow of CHY-131.

    A malformed condition raises rather than answering `False`. False would
    reach the state machine as "this arm did not match", the run would take the
    default arm or fail for want of one, and the message would blame the
    workflow's shape for what is a typo in a guard.
    """
    if condition is None:
        return True
    return conditions.evaluate(condition, result.output)


class ExecutorRegistry:
    """Which executor runs which node type.

    A class rather than a dict so the refusal for an unregistered type is a
    sentence naming the ticket, and so overriding one type in a test does not
    mean rebuilding the whole mapping.

    The composition root injects what it can reach: the API can call a model,
    this context cannot (CHY-129). That inversion is the point of ADR-028
    point 3 — a node type stays delegable.
    """

    def __init__(
        self,
        executors: Mapping[str, NodeExecutor] | None = None,
        *,
        evaluate_condition: ConditionEvaluator | None = None,
    ) -> None:
        self._executors: dict[str, NodeExecutor] = {
            **dict.fromkeys(ROUTING_ONLY, _routing_only),
            "compute": _compute,
            "document": _document,
            **(executors or {}),
        }
        #: How a guarded edge is judged. On the registry and not beside it: both
        #: answer the same question — what this deployment is able to run — and
        #: they were being threaded through three functions as a pair. A pair
        #: that always travels together is one object.
        self.evaluate_condition: ConditionEvaluator = (
            evaluate_condition or evaluate_edge_condition
        )

    def register(self, node_type: str, executor: NodeExecutor) -> None:
        self._executors[node_type] = executor

    def has(self, node_type: str) -> bool:
        return node_type in self._executors

    def for_type(self, node_type: str, node_ref: str) -> NodeExecutor:
        """The executor for this type, or a refusal that says why.

        `human_review` gets its own message. It has no executor by design — the
        state machine stops before it — so reaching here means the caller
        bypassed `plan`, and "no executor registered" would send whoever reads
        the error looking for the wrong thing.
        """
        if node_type in NEVER_EXECUTED:
            raise NodeTypeNotExecutable(node_type, node_ref, reason=(
                "A human_review node is never executed: the run stops before it "
                "and the gate becomes a row (ADR-028). Reaching an executor for "
                "it means the plan was bypassed."
            ))
        executor = self._executors.get(node_type)
        if executor is None:
            return _refuse
        return executor


