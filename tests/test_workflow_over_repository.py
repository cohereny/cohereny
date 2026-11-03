"""
tests/test_workflow_over_repository.py

A workflow run composes its context out of `context_blocks` (CHY-143).

This is the M4 gate criterion, and it used to read "Banking läuft über das
echte Repository". The banking pilot does not read from the repository at all —
its system prompts are string literals — and making it would be a rebuild of a
pilot that ADR-028 point 4 keeps out of rebuilds. So the criterion moved to the
runner, where it became possible with CHY-133.

**What is asserted is the whole chain, not a call.** Rows in `context_blocks`
→ `compose_context` → the `context_compose` node's output → the `input_mapping`
→ what `llm_call` actually receives. Every earlier bug in this area sat in one
of those joins while both ends were correct: `NodeContext` was built without
four of its fields (CHY-131), and `context_compose` had no executor at all
(CHY-133).

The counter-probe is the second half: withdraw the block, and the next run's
prompt has to change. Without it, "composes from the repository" is a claim
about one line of code.

**What this file cannot see**, stated so nobody has to find out: the pool is a
double, so a change to the SQL in `store.active_blocks_in_scopes` does not
reach these tests. Breaking the composable-type filter there leaves every test
here green — checked with a counter-probe, and that guarantee is covered in
`tests/test_composable_block_types.py`, next to the query it belongs to. What
is covered here is everything *between* the rows and the prompt.
"""
from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from api.v1 import workflows as transport
from contexts.orchestration import service
from contexts.orchestration.execution import ExecutorRegistry, NodeContext, NodeResult
from tests.test_workflow_advance import (
    FakeDb,
    _NullPool,
    _conn_row,
    _node,
    _run,
)

_SCOPE = uuid4()
_NOW = datetime(2026, 9, 7, tzinfo=UTC)


def _block(content: str, block_type: str = "system_prompt", version: int = 1) -> dict:
    return {
        "block_id": uuid4(), "version": version, "block_type": block_type,
        "content": content, "scope_id": _SCOPE, "tags": [], "created_at": _NOW,
        "governance_key": None, "privacy_impact": None, "locked": False,
    }


def _repository(blocks: list[dict]):
    """A pool that answers what composition asks, and nothing else.

    Dispatched on the statement: composition reads the ancestor chain first and
    the blocks second, and one fixed list would answer both the same way —
    green for a reason that has nothing to do with the repository.
    """
    chain = [{"id": _SCOPE, "scope_path": "org_bank", "node_type": "org",
              "name": "bank", "depth": 1, "privacy_level": None}]

    async def _fetch(sql, *args):
        if "scope_nodes" in sql:
            return chain
        if "block_type = 'routing_rule'" in sql:
            # The routing query of CHY-87 asks only for routing rules, and
            # asks it of the same table. Answered separately because the branch
            # below would otherwise hand every block back as a rule — a double
            # that is right about composition and wrong about routing (CHY-145).
            return [b for b in blocks if b["block_type"] == "routing_rule"]
        if "context_blocks" in sql:
            # The type filter is applied here because it is applied in the
            # query (ADR-037). A double that returned every row regardless
            # would make the run look like it composes a routing rule when the
            # database never hands one over — the first version of this file
            # did exactly that, and the counter-probe caught it.
            allowed = set(args[1]) if len(args) > 1 else None
            return [b for b in blocks
                    if allowed is None or b["block_type"] in allowed]
        return []

    pool = MagicMock()
    pool.fetch = AsyncMock(side_effect=_fetch)
    return pool


def _routing_rule(task_type: str, candidates: list[str], max_tokens: int = 256) -> dict:
    """An approved routing rule in the scope, shaped like the row CHY-87 reads."""
    return {
        "block_id": uuid4(), "version": 1, "block_type": "routing_rule",
        "content": "so entschieden", "scope_id": _SCOPE, "tags": [],
        "created_at": _NOW, "governance_key": None, "privacy_impact": None,
        "locked": False,
        "metadata": {"task_type": task_type, "candidates": candidates, "max_tokens": max_tokens},
    }


def _redis():
    r = MagicMock()
    r.get = AsyncMock(return_value=None)
    r.mget = AsyncMock(side_effect=lambda keys: [None] * len(keys))
    r.set = AsyncMock(return_value=True)
    r.setex = AsyncMock(return_value=True)
    return r


def _mandatory_core() -> tuple[dict, dict, dict]:
    """`context_compose → llm_call → human_review`, the shape ADR-004 requires."""
    compose = _node("compose", "context_compose")
    call = _node("answer", "llm_call")
    call["input_mapping"] = {"prompt": "compose.prompt"}
    # No `config["model"]` since CHY-145: the model comes from the scope's
    # routing rule for the node's task type, and migration 023 refuses the key.
    gate = _node("approve", "human_review")
    return compose, call, gate


async def _drive(monkeypatch, blocks: list[dict],
                 requirements: dict | None = None) -> tuple[FakeDb, dict, list[str]]:
    """One run through the real compose executor. Returns db, the llm node, prompts.

    `requirements` is written into the compose node's `context_requirements`
    column exactly as the database stores it (CHY-134), so the run reads it the
    way a real one does — through `node_details`, not through a keyword.
    """
    compose, call, gate = _mandatory_core()
    compose["context_requirements"] = requirements
    db = FakeDb(nodes=[compose, call, gate],
                connections=[_conn_row(compose, call), _conn_row(call, gate)])
    db.install(monkeypatch)
    db.scope_id = _SCOPE
    run_id = _run(db)

    prompts: list[str] = []

    async def _model(ctx: NodeContext) -> NodeResult:
        prompts.append(ctx.config.get("prompt") or ctx.input_data.get("prompt", ""))
        return NodeResult(output={"content": "…"}, model_used="gemma3-4b")

    registry = ExecutorRegistry({
        # The real one, not a stub. A stub here would test the test.
        "context_compose": transport._compose_for(_repository(blocks), _redis()),
        "llm_call": _model,
    })
    await service.advance_run(_NullPool(), run_id, registry=registry)
    return db, call, prompts


class TestTheChainFromBlockToPrompt:
    async def test_a_block_in_the_scope_reaches_the_model(self, monkeypatch):
        _, _, prompts = await _drive(
            monkeypatch, [_block("Du bist ein Kreditanalyst der Musterbank.")]
        )
        assert prompts, "the model was never called"
        assert "Kreditanalyst" in prompts[0], (
            "the composed context did not reach the prompt"
        )

    async def test_what_the_node_saw_is_written_down(self, monkeypatch):
        """`node_executions.input_data` is the record the acceptance asks for —
        not 'the run completed'."""
        db, call, _ = await _drive(
            monkeypatch, [_block("Du bist ein Kreditanalyst.")]
        )
        row = next(e for e in db.executions if e["node_id"] == call["id"])
        assert "Kreditanalyst" in row["input_data"]["prompt"]
        assert "Kreditanalyst" in row["input_data"]["compose"]["prompt"]

    async def test_the_composition_record_names_the_block_versions(self, monkeypatch):
        """Without it a run is not reproducible: the same block id says little
        when it has six versions and one of them carried the sentence."""
        block = _block("Du bist ein Kreditanalyst.", version=3)
        db, _, _ = await _drive(monkeypatch, [block])
        compose_row = next(
            e for e in db.executions
            if e["output_data"] and "block_versions" in e["output_data"]
        )
        assert compose_row["output_data"]["block_versions"] == {
            str(block["block_id"]): 3
        }

    async def test_the_run_stops_at_the_gate(self, monkeypatch):
        db, _, _ = await _drive(monkeypatch, [_block("Du bist ein Kreditanalyst.")])
        run = next(iter(db.runs.values()))
        assert run["status"] == "awaiting_oversight"


class TestTheCounterProbe:
    async def test_withdrawing_the_block_changes_the_prompt(self, monkeypatch):
        """The half that makes the claim more than a line of code.

        If the prompt were the same with and without the block, everything
        above would pass while the context came from somewhere else.
        """
        _, _, mit = await _drive(
            monkeypatch, [_block("Du bist ein Kreditanalyst der Musterbank.")]
        )
        _, _, ohne = await _drive(monkeypatch, [])
        assert mit[0] != ohne[0], "the prompt does not depend on the blocks at all"
        assert "Kreditanalyst" not in ohne[0]

    async def test_a_second_block_is_composed_too(self, monkeypatch):
        """Counter-probe to the counter-probe: composition must not stop at one."""
        _, _, prompts = await _drive(monkeypatch, [
            _block("Du bist ein Kreditanalyst."),
            _block("Antworte in ganzen Sätzen.", block_type="user_context"),
        ])
        assert "Kreditanalyst" in prompts[0]
        assert "ganzen Sätzen" in prompts[0]

    async def test_a_routing_rule_block_never_reaches_the_prompt(self, monkeypatch):
        """ADR-037, driven through a whole run rather than through the query.

        The store filters by composable type, and this asserts the consequence
        where it matters: a routing rule in the same scope stays out of what a
        model reads.
        """
        _, _, prompts = await _drive(monkeypatch, [
            _block("Du bist ein Kreditanalyst."),
            _block("GEHEIME ROUTING-REGEL", block_type="routing_rule"),
        ])
        assert "Kreditanalyst" in prompts[0]
        assert "ROUTING" not in prompts[0], (
            "a routing rule was composed into a prompt (ADR-037)"
        )

    async def test_a_requirement_narrows_what_is_composed(self, monkeypatch):
        """The column reaches the query, end to end (CHY-134).

        Same two blocks as above, and the node asks for one type. What arrives
        at the model is the difference between the two runs — which is the only
        way to see that the requirement was read rather than defaulted away.
        """
        _, _, both = await _drive(monkeypatch, [
            _block("Du bist ein Kreditanalyst."),
            _block("Antworte in ganzen Sätzen.", block_type="user_context"),
        ])
        _, _, narrowed = await _drive(monkeypatch, [
            _block("Du bist ein Kreditanalyst."),
            _block("Antworte in ganzen Sätzen.", block_type="user_context"),
        ], requirements={"block_types": ["user_context"]})

        assert "Kreditanalyst" in both[0] and "ganzen Sätzen" in both[0]
        assert "ganzen Sätzen" in narrowed[0]
        assert "Kreditanalyst" not in narrowed[0], (
            "context_requirements did not reach the query — the node composed "
            "everything the scope offers"
        )


# ── The llm_call routes like /v1/complete does (CHY-145) ─────────────────────

async def _drive_routed(monkeypatch, blocks: list[dict], *, answers=None):
    """One run through the REAL llm_call executor and the real resolution.

    Real `route_for`, real `rules.resolve`, the shipped routing-config.yaml.
    Replaced is only the network: `complete_with_fallback` answers from here,
    so no gateway is needed — and records which chain it was handed, which is
    the thing under test.

    `answers` picks which candidate answers; the default is the first.
    Returns the db, the chains the model call saw, and the audit rows written.
    """
    compose, call, gate = _mandatory_core()
    db = FakeDb(nodes=[compose, call, gate],
                connections=[_conn_row(compose, call), _conn_row(call, gate)])
    db.install(monkeypatch)
    db.scope_id = _SCOPE
    run_id = _run(db)

    chains: list[tuple[str, ...]] = []
    audits: list[dict] = []
    # The length limit each model call was handed (CHY-166). On the db rather
    # than in the return value, so the tests that do not look at it keep their
    # four-tuple.
    db.max_tokens_seen = []

    async def fake_fallback(request, candidates, **_kw):
        chains.append(tuple(candidates))
        db.max_tokens_seen.append(request.max_tokens)
        answered = answers or candidates[0]
        attempts = [
            SimpleNamespace(model=m, outcome="refused", detail="429 quota")
            for m in candidates[: candidates.index(answered)]
        ] + [SimpleNamespace(model=answered, outcome="answered", detail="")]
        return SimpleNamespace(content="…", model_used=answered,
                               trace_id="t", attempts=tuple(attempts))

    async def fake_audit(_pool, **kw):
        audits.append(kw)

    monkeypatch.setattr(transport.completion, "complete_with_fallback", fake_fallback)
    monkeypatch.setattr(transport, "write_audit", fake_audit)

    pool = _repository(blocks)
    registry = ExecutorRegistry({
        "context_compose": transport._compose_for(pool, _redis()),
        "llm_call": transport._call_model_for(pool),
    })
    await service.advance_run(_NullPool(), run_id, registry=registry)
    return db, run_id, chains, audits


class TestTheModelIsRouted:
    async def test_a_node_without_a_model_runs_on_the_scopes_rule(self, monkeypatch):
        """Acceptance 1 of CHY-145: it used to fail — `config["model"]` was
        the only way a node learned its model."""
        db, run_id, chains, _ = await _drive_routed(monkeypatch, [
            _block("Du bist ein Kreditanalyst."),
            _routing_rule("summarize", ["gemma4-e4b", "gemma3-4b"]),
        ])
        assert chains == [("gemma4-e4b", "gemma3-4b")], (
            "the model call did not receive the chain of the scope's rule"
        )
        used = [e["model_used"] for e in db.executions if e["model_used"]]
        assert used == ["gemma4-e4b"]
        assert db.runs[run_id]["status"] == "awaiting_oversight"

    async def test_the_rules_length_limit_reaches_the_model_call(self, monkeypatch):
        """CHY-166: a node has no limit of its own; the rule's is the one a run
        gets. Without it a run node could still produce an unbounded answer."""
        db, _, chains, _ = await _drive_routed(monkeypatch, [
            _block("Du bist ein Kreditanalyst."),
            _routing_rule("summarize", ["gemma4-e4b"], max_tokens=64),
        ])
        assert chains, "no model was called"
        assert db.max_tokens_seen == [64]

    async def test_without_a_rule_the_run_fails_and_names_the_task_type(
        self, monkeypatch
    ):
        """Acceptance 3, the counter-probe: no rule, no model — and no silent
        default. The run fails, says which task type found no decision, and
        the model is never called."""
        db, run_id, chains, _ = await _drive_routed(monkeypatch, [
            _block("Du bist ein Kreditanalyst."),
        ])
        assert chains == [], "a model was called although nobody decided one"
        assert db.runs[run_id]["status"] == "failed"
        assert "summarize" in db.runs[run_id]["error_message"]

    async def test_a_rule_for_another_task_type_does_not_answer(self, monkeypatch):
        """Counter-probe to the first test: the rule is found by task type,
        not merely because it is the only one in the scope."""
        db, run_id, chains, _ = await _drive_routed(monkeypatch, [
            _block("Du bist ein Kreditanalyst."),
            _routing_rule("classify", ["gemma4-e4b"]),
        ])
        assert chains == []
        assert db.runs[run_id]["status"] == "failed"

    async def test_a_fallback_inside_a_run_is_audited_with_run_and_node(
        self, monkeypatch
    ):
        """Acceptance 4. The chain's first candidate refuses — a spent Free
        Plan quota answers 429 — and the second answers. Without this record
        the run shows a model name and nothing says it was not the chosen one.
        """
        _, run_id, _, audits = await _drive_routed(monkeypatch, [
            _block("Du bist ein Kreditanalyst."),
            _routing_rule("summarize", ["gemma4-e4b", "gemma3-4b"]),
        ], answers="gemma3-4b")
        assert len(audits) == 1
        entry = audits[0]
        assert entry["target"] == f"workflow_run:{run_id}"
        assert entry["payload"]["node_ref"] == "answer"
        assert entry["payload"]["chosen"] == "gemma4-e4b"
        assert entry["payload"]["answered"] == "gemma3-4b"
        assert [a["outcome"] for a in entry["payload"]["attempts"]] == [
            "refused", "answered"
        ]

    async def test_no_fallback_writes_no_fallback_record(self, monkeypatch):
        """Counter-probe: an audit written on every call would make the
        fallback record meaningless by making it constant."""
        _, _, _, audits = await _drive_routed(monkeypatch, [
            _block("Du bist ein Kreditanalyst."),
            _routing_rule("summarize", ["gemma4-e4b", "gemma3-4b"]),
        ])
        assert audits == []
