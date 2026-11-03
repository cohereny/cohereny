"""
tests/test_workflow_advance.py

Advancing a run, against a store that remembers (CHY-127 Schritt 3).

The load-bearing test in this file is
`test_a_run_interrupted_and_resumed_ends_up_where_an_uninterrupted_one_does`.

Everything else here checks a rule. That one checks the property the whole
design rests on: **resuming is the same operation as starting.** If the state
rebuilt from rows differs from the state a continuous run carried, then an
interrupted run behaves differently from an uninterrupted one — and the
difference shows up at a review gate, days later, in the one workflow that
matters.

Why a fake store rather than mocks
-----------------------------------
`advance_run` reads state, writes it, and reads it back within one call. A mock
that returns a fixed answer cannot express that, and a test built on one would
pass against a runner that never records anything. The fake below keeps the
same dictionaries the database would keep rows in, which is the smallest thing
that can tell "wrote it down" from "seemed to".

It deliberately does *not* emulate SQL. Whether the statements are right is a
different question from whether the orchestration is, and answering both in one
test answers neither well.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from contexts.orchestration import runner, service
from contexts.orchestration.errors import (
    NodeTypeNotExecutable,
    OversightGateMissing,
    ReviewerAlreadyDecided,
    RunNotAwaitingOversight,
)
from contexts.orchestration.execution import ExecutorRegistry, NodeContext, NodeResult
from contexts.orchestration.schema import OversightDecision
from tests.conftest import stub_user


# ── a store that remembers ────────────────────────────────────────────────────

@dataclass
class FakeDb:
    """The rows the orchestration store would write, in dictionaries."""

    nodes: list[dict] = field(default_factory=list)
    connections: list[dict] = field(default_factory=list)
    runs: dict[UUID, dict] = field(default_factory=dict)
    executions: list[dict] = field(default_factory=list)
    traversals: dict[tuple[UUID, UUID], int] = field(default_factory=dict)
    #: One decision per person and gate execution, as `review_votes` (027).
    votes: list[dict] = field(default_factory=list)
    #: The workflow's scope. On the definition, not on the run — which is why
    #: `_claim` has to read the definition to fill `NodeContext.scope_id`.
    scope_id: UUID = field(default_factory=uuid4)

    def install(self, monkeypatch) -> None:
        """Replace the store module's functions, keeping their signatures."""
        async def nodes(_conn, workflow_id):
            return [n for n in self.nodes if n["workflow_id"] == workflow_id]

        async def connections(_conn, workflow_id):
            return [c for c in self.connections if c["workflow_id"] == workflow_id]

        async def lock_run(_conn, run_id):
            return self.runs.get(run_id)

        async def executions(_conn, run_id):
            return [e for e in self.executions if e["run_id"] == run_id]

        async def traversal_rows(_conn, run_id):
            return [
                {"connection_id": conn_id, "traversals": count}
                for (r, conn_id), count in self.traversals.items() if r == run_id
            ]

        async def start_execution(_conn, new):
            row = {
                "id": uuid4(), "run_id": new.run_id, "node_id": new.node_id,
                "iteration": new.iteration, "status": "running",
                "consumed_edges": list(new.consumed), "output_data": None,
                "model_used": None, "input_data": new.input_data,
            }
            self.executions.append(row)
            return row

        async def complete_execution(_conn, execution_id, *, output, model_used):
            for row in self.executions:
                if row["id"] == execution_id:
                    row.update(status="complete", output_data=output,
                               model_used=model_used, error_message=None)

        async def fail_execution(_conn, execution_id, error):
            for row in self.executions:
                if row["id"] == execution_id:
                    row.update(status="failed", error_message=error)

        async def record_traversals(_conn, run_id, edge_ids):
            for edge_id in edge_ids:
                key = (run_id, edge_id)
                self.traversals[key] = self.traversals.get(key, 0) + 1

        async def node_details(_conn, workflow_id):
            return [
                {"id": n["id"], "config": n["config"],
                 "context_requirements": n["context_requirements"],
                 "task_type": n["task_type"],
                 "input_mapping": n["input_mapping"]}
                for n in self.nodes if n["workflow_id"] == workflow_id
            ]

        async def predecessor_outputs(_conn, run_id, node_ids):
            """The latest *completed* pass per node, as the SQL does.

            Written out rather than returning everything, because the ordering
            is the behaviour under test: a node in a loop must read what its
            predecessor produced on the current lap, not on the first.
            """
            wanted, latest = set(node_ids), {}
            for row in self.executions:
                if (row["run_id"] != run_id or row["node_id"] not in wanted
                        or row["status"] != "complete"):
                    continue
                best = latest.get(row["node_id"])
                if best is None or row["iteration"] >= best["iteration"]:
                    latest[row["node_id"]] = row
            return [
                {"node_id": nid, "output_data": row["output_data"]}
                for nid, row in latest.items()
            ]

        async def workflow(_conn, workflow_id):
            return {"id": workflow_id, "scope_id": self.scope_id,
                    "status": "active", "version": 1}

        async def set_run_status(_conn, run_id, status, *, result=None, error=None):
            row = self.runs[run_id]
            row.update(status=status, result=result, error_message=error)
            return row

        async def review_role(_conn, node_id):
            return next(n["review_role"] for n in self.nodes if n["id"] == node_id)

        async def record_vote(_conn, execution_id, actor_id, decision, reason):
            """The primary key of 027, in a list: one decision per person."""
            if any(v["execution_id"] == execution_id and v["actor_id"] == actor_id
                   for v in self.votes):
                raise ReviewerAlreadyDecided(actor_id)
            self.votes.append({"execution_id": execution_id, "actor_id": actor_id,
                               "decision": decision, "reason": reason})

        async def approvals(_conn, execution_id):
            return sum(1 for v in self.votes
                       if v["execution_id"] == execution_id and v["decision"] == "approved")

        for name, fn in (
            ("review_role", review_role), ("record_vote", record_vote),
            ("approvals", approvals),
            ("nodes", nodes), ("connections", connections), ("lock_run", lock_run),
            ("executions", executions), ("traversals", traversal_rows),
            ("start_execution", start_execution),
            ("complete_execution", complete_execution), ("fail_execution", fail_execution),
            ("record_traversals", record_traversals), ("set_run_status", set_run_status),
            ("node_details", node_details), ("predecessor_outputs", predecessor_outputs),
            ("workflow", workflow),
        ):
            monkeypatch.setattr(service.store, name, fn)


class _NullTx:
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False


class _NullConn:
    def transaction(self): return _NullTx()


class _NullPool:
    """`async with pool.acquire() as conn` and nothing else."""

    def acquire(self):
        return _NullTx.__class__("Acquire", (), {
            "__aenter__": lambda s: _acquire(),
            "__aexit__": lambda s, *a: _noop(),
        })()


async def _acquire():
    return _NullConn()


async def _noop():
    return False


# ── building a workflow ───────────────────────────────────────────────────────

_WF = uuid4()

#: Named because the assertions read as arithmetic, not as magic: two passes
#: through one node, three laps of one loop.
_TWO_PASSES = 2
_THREE_LAPS = 3


def _node(ref: str, node_type: str = "tool_call", join_policy: str | None = None) -> dict:
    """A row the database would accept.

    An `llm_call` always carries a task type, because migration 023 refuses one
    without: a double that produced rows the table cannot hold would test a
    runner against a world that does not exist.
    """
    return {
        "id": uuid4(), "workflow_id": _WF, "node_ref": ref, "node_type": node_type,
        "name": None, "description": None, "join_policy": join_policy, "config": {},
        "context_requirements": None,
        "task_type": "summarize" if node_type == "llm_call" else None,
        "target_workflow_id": None, "target_workflow_version": None,
        "input_mapping": {}, "output_mapping": {},
        "review_role": None, "review_timeout": None, "on_timeout": None,
    }


def _conn_row(a: dict, b: dict, **kw) -> dict:
    return {
        "id": uuid4(), "workflow_id": _WF, "source_node_id": a["id"],
        "target_node_id": b["id"], "condition": None, "label": None,
        "execution_mode": "sequential", "loop_limit": None, **kw,
    }


def _run(db: FakeDb) -> UUID:
    run_id = uuid4()
    db.runs[run_id] = {
        "id": run_id, "workflow_id": _WF, "workflow_version": 1, "status": "running",
        "actor_id": uuid4(), "input_data": {}, "result": None, "error_message": None,
        "started_at": None, "completed_at": None,
        "created_at": datetime(2026, 9, 4, tzinfo=UTC),
        "parent_run_id": None, "parent_node_id": None,
    }
    return run_id


async def _ok(ctx: NodeContext) -> NodeResult:
    return NodeResult(output={"node": ctx.node_ref, "iteration": ctx.iteration})


def _registry() -> ExecutorRegistry:
    return ExecutorRegistry({"tool_call": _ok, "context_compose": _ok, "llm_call": _ok})


# ── advancing ─────────────────────────────────────────────────────────────────

async def test_a_straight_line_runs_to_completion(monkeypatch):
    a, b, c = _node("a"), _node("b"), _node("c")
    db = FakeDb(nodes=[a, b, c], connections=[_conn_row(a, b), _conn_row(b, c)])
    db.install(monkeypatch)
    run_id = _run(db)

    result = await service.advance_run(_NullPool(), run_id, registry=_registry())

    assert result.run.status == "complete"
    assert result.executed == ("a", "b", "c")


async def test_a_gate_stops_the_run_and_says_so(monkeypatch):
    a = _node("a")
    gate = _node("approve", "human_review")
    db = FakeDb(nodes=[a, gate], connections=[_conn_row(a, gate)])
    db.install(monkeypatch)
    run_id = _run(db)

    result = await service.advance_run(_NullPool(), run_id, registry=_registry())

    assert result.run.status == "awaiting_oversight"
    assert result.executed == ("a",)


async def test_a_failing_node_stops_the_run_with_its_message(monkeypatch):
    """A swallowed failure produces a run that completed without doing its
    work, which is indistinguishable from success."""
    a, b = _node("a"), _node("b", "llm_call")
    db = FakeDb(nodes=[a, b], connections=[_conn_row(a, b)])
    db.install(monkeypatch)
    run_id = _run(db)

    async def boom(ctx: NodeContext) -> NodeResult:
        raise NodeTypeNotExecutable(ctx.node_type, ctx.node_ref)

    result = await service.advance_run(
        _NullPool(), run_id, registry=ExecutorRegistry({"tool_call": _ok, "llm_call": boom})
    )
    assert result.run.status == "failed"
    assert "llm_call" in db.runs[run_id]["error_message"]


async def test_a_foreign_exception_ends_the_run_instead_of_leaving_it_running(monkeypatch):
    """Found in CHY-145, shown before the fix with exactly this setup.

    The runner caught only its own error family. An `llm_call` calls into
    `core.completion`, whose `ModelRefused` is not one of them — it passed
    straight through `advance_run`, and since the execution had already been
    claimed in its own committed transaction, run and execution stayed at
    `running` for good. Nothing would ever advance them again.

    Raised again after recording: an unexpected error must stay loud. But the
    state has to be true first.
    """
    from core.completion import ModelRefused

    a = _node("ask", "llm_call")
    db = FakeDb(nodes=[a], connections=[])
    db.install(monkeypatch)
    run_id = _run(db)

    async def refused(_ctx: NodeContext) -> NodeResult:
        raise ModelRefused(400, "unknown model")

    with pytest.raises(ModelRefused):
        await service.advance_run(
            _NullPool(), run_id, registry=ExecutorRegistry({"llm_call": refused})
        )
    assert db.runs[run_id]["status"] == "failed"
    assert "ModelRefused" in db.runs[run_id]["error_message"]
    assert [e["status"] for e in db.executions] == ["failed"], (
        "the claimed execution is still running — nothing will ever end it"
    )


async def test_advancing_a_finished_run_changes_nothing(monkeypatch):
    """A retrying client must not restart a completed run."""
    a = _node("a")
    db = FakeDb(nodes=[a], connections=[])
    db.install(monkeypatch)
    run_id = _run(db)
    db.runs[run_id]["status"] = "complete"

    result = await service.advance_run(_NullPool(), run_id, registry=_registry())
    assert result.executed == ()
    assert db.executions == []


# ── the property the design rests on ──────────────────────────────────────────

async def test_a_run_interrupted_and_resumed_ends_up_where_an_uninterrupted_one_does(
    monkeypatch,
):
    """Resuming is the same operation as starting — proved, not asserted.

    Two runs of the same workflow. One is advanced once and reaches its gate;
    the gate is released and it is advanced again — which is what a restart
    between the two calls would look like, because nothing is carried in
    memory. The other is released before it ever stops and runs through.

    They must end in the same place, having executed the same nodes. If the
    state rebuilt from rows differs from the state a continuous run carried,
    this is where it shows — and if it does not show here, it shows at a gate,
    days later, in the one workflow that matters.
    """
    def build():
        a = _node("a")
        gate = _node("approve", "human_review")
        after = _node("after")
        db = FakeDb(
            nodes=[a, gate, after],
            connections=[_conn_row(a, gate), _conn_row(gate, after)],
        )
        return db, gate

    # ── interrupted ──
    db_i, gate_i = build()
    db_i.install(monkeypatch)
    run_i = _run(db_i)
    first = await service.advance_run(_NullPool(), run_i, registry=_registry())
    assert first.run.status == "awaiting_oversight"

    # Releasing the gate, exactly as `record_oversight` does it: the execution
    # the advance *claimed* is completed — not a second row appended.
    #
    # The first version of this test appended one, and it went green until the
    # gate started being claimed. Two rows for one visit to one node is not a
    # detail: `rebuild_state` counts completions, and the run would have
    # believed it passed the gate twice.
    gate_row = next(
        e for e in db_i.executions
        if e["run_id"] == run_i and e["node_id"] == gate_i["id"]
    )
    gate_row.update(status="complete", output_data={"decision": "approved"})
    db_i.traversals[(run_i, db_i.connections[1]["id"])] = 1
    db_i.runs[run_i]["status"] = "running"
    second = await service.advance_run(_NullPool(), run_i, registry=_registry())

    # ── uninterrupted: the same release, before it ever stopped ──
    #
    # The gate is pre-recorded as already passed, so this run never has a
    # reason to halt. Same rows, one advance instead of two.
    db_u, gate_u = build()
    db_u.install(monkeypatch)
    run_u = _run(db_u)
    db_u.executions.append({
        "id": uuid4(), "run_id": run_u, "node_id": gate_u["id"], "iteration": 1,
        "status": "complete", "consumed_edges": [db_u.connections[0]["id"]],
        "output_data": {"decision": "approved"}, "model_used": None,
    })
    db_u.traversals[(run_u, db_u.connections[1]["id"])] = 1
    straight = await service.advance_run(_NullPool(), run_u, registry=_registry())

    # The *sum* of the interrupted run's advances, against the single advance
    # of the other. Comparing only the last advance was the first version of
    # this assertion and it was wrong: the interrupted run did node `a` before
    # it stopped, so the two halves were never the same measurement. The
    # property under test is that the same work happens, not that the last call
    # looks the same.
    assert second.run.status == straight.run.status == "complete"
    assert first.executed + second.executed == straight.executed == ("a", "after")

    def finished(db, run_id):
        return sorted(
            (e["node_id"], e["iteration"])
            for e in db.executions if e["run_id"] == run_id and e["status"] == "complete"
        )
    assert len(finished(db_i, run_i)) == len(finished(db_u, run_u))


# ── the reconstruction, on its own ────────────────────────────────────────────

class TestRebuildState:
    def test_a_completed_node_counts_its_passes(self):
        node = uuid4()
        state = runner.rebuild_state(
            [{"node_id": node, "status": "complete", "consumed_edges": []},
             {"node_id": node, "status": "complete", "consumed_edges": []}],
            [],
        )
        assert state.completed[node] == _TWO_PASSES

    def test_a_running_node_is_in_flight_and_not_completed(self):
        node = uuid4()
        state = runner.rebuild_state(
            [{"node_id": node, "status": "running", "consumed_edges": []}], []
        )
        assert node in state.in_flight
        assert node not in state.completed

    def test_a_failed_node_is_neither(self):
        """Counting a failed node as completed would let the run walk on as
        though its work had been done."""
        node = uuid4()
        state = runner.rebuild_state(
            [{"node_id": node, "status": "failed", "consumed_edges": []}], []
        )
        assert node not in state.completed
        assert node not in state.in_flight

    def test_a_token_is_a_traversal_that_was_not_consumed(self):
        edge = uuid4()
        state = runner.rebuild_state([], [{"connection_id": edge, "traversals": 1}])
        assert state.tokens[edge] == 1

    def test_a_consumed_traversal_leaves_no_token(self):
        edge = uuid4()
        state = runner.rebuild_state(
            [{"node_id": uuid4(), "status": "complete", "consumed_edges": [edge]}],
            [{"connection_id": edge, "traversals": 1}],
        )
        assert state.tokens[edge] == 0

    def test_first_wins_consuming_an_untraversed_edge_does_not_go_negative(self):
        """The clamp is arithmetic, not defensiveness.

        `first_wins` consumes incoming edges that never carried a token — that
        is how it discards the stragglers. When the late branch finally
        arrives, its traversal is cancelled by the consumption already on
        record, and the gateway does not fire twice.
        """
        edge = uuid4()
        consumed_early = runner.rebuild_state(
            [{"node_id": uuid4(), "status": "complete", "consumed_edges": [edge]}], []
        )
        assert consumed_early.tokens.get(edge, 0) == 0

        straggler_arrives = runner.rebuild_state(
            [{"node_id": uuid4(), "status": "complete", "consumed_edges": [edge]}],
            [{"connection_id": edge, "traversals": 1}],
        )
        assert straggler_arrives.tokens[edge] == 0

        # The case that actually needs the clamp, and the reason this block is
        # longer than it looks like it should be: consumption *exceeding*
        # traversal. The two assertions above happen to give zero with or
        # without max(0, …) — a probe that removed the clamp left them green,
        # which is a test that describes the rule without exercising it.
        # A gateway that discarded two stragglers before either arrived:
        over_consumed = runner.rebuild_state(
            [{"node_id": uuid4(), "status": "complete", "consumed_edges": [edge, edge]}],
            [{"connection_id": edge, "traversals": 1}],
        )
        assert over_consumed.tokens[edge] == 0, (
            "a negative token count would make the edge permanently unarmable"
        )

    def test_traversals_survive_for_the_loop_limit(self):
        """Tokens go away when consumed; the traversal count must not, or a
        loop would be allowed to run forever.

        Three traversals, three consumptions: no tokens left, and the counter
        still reads three. The first version of this test consumed once and
        expected zero tokens — which would have meant two traversals vanishing
        into nothing.
        """
        edge = uuid4()
        state = runner.rebuild_state(
            [{"node_id": uuid4(), "status": "complete", "consumed_edges": [edge]}
             for _ in range(_THREE_LAPS)],
            [{"connection_id": edge, "traversals": _THREE_LAPS}],
        )
        assert state.tokens[edge] == 0
        assert state.traversals[edge] == _THREE_LAPS

    def test_an_unconsumed_traversal_leaves_its_token(self):
        """Counter-probe to the test above: consumption has to be counted, not
        assumed. Three laps and one firing leaves two tokens waiting."""
        edge = uuid4()
        state = runner.rebuild_state(
            [{"node_id": uuid4(), "status": "complete", "consumed_edges": [edge]}],
            [{"connection_id": edge, "traversals": _THREE_LAPS}],
        )
        assert state.tokens[edge] == _THREE_LAPS - 1


# ── the decision at the gate ──────────────────────────────────────────────────

async def _at_the_gate(monkeypatch, review_role: str | None = None) -> tuple[FakeDb, UUID, dict]:
    """A run stopped at a human_review, with the gate claimed."""
    a = _node("a")
    gate = {**_node("approve", "human_review"), "review_role": review_role}
    after = _node("after")
    db = FakeDb(nodes=[a, gate, after],
                connections=[_conn_row(a, gate), _conn_row(gate, after)])
    db.install(monkeypatch)
    run_id = _run(db)

    async def running_execution(_conn, rid):
        rows = [e for e in db.executions if e["run_id"] == rid and e["status"] == "running"]
        return rows[-1] if rows else None

    # `store.workflow` is not stubbed here: FakeDb.install already provides it,
    # and a second stub for one function is a second answer to one question —
    # this one was missing `scope_id` and hid it behind whichever was installed
    # last.
    monkeypatch.setattr(service.store, "running_execution", running_execution)
    monkeypatch.setattr(service, "write_audit", _no_audit)
    # The role lookup belongs to the kernel and has its own tests; reproducing
    # it here would test kernel twice and the decision path once.
    monkeypatch.setattr(service, "resolve_actor", _stub_actor)
    monkeypatch.setattr(service, "require", lambda *_a: None)

    await service.advance_run(_NullPool(), run_id, registry=_registry())
    return db, run_id, gate


async def _no_audit(_conn, **_kw):
    return None


async def _stub_actor(_pool, _caller):
    return SimpleNamespace(id=uuid4(), role="reviewer")


class TestOversight:
    async def test_the_gate_is_claimed_when_the_run_stops(self, monkeypatch):
        """Without the claim the decision has nothing to attach to.

        Three things hang off it: the audit trail says the run *reached* the
        gate, its tokens are spent so a second advance does not re-plan it, and
        releasing has a row to complete.
        """
        db, run_id, gate = await _at_the_gate(monkeypatch)
        claimed = [e for e in db.executions if e["node_id"] == gate["id"]]
        assert len(claimed) == 1
        assert claimed[0]["status"] == "running"
        assert claimed[0]["consumed_edges"] == [db.connections[0]["id"]]

    async def test_approving_continues_the_run(self, monkeypatch):
        db, run_id, _ = await _at_the_gate(monkeypatch)
        result = await service.record_oversight(
            _NullPool(), _caller(), run_id,
            OversightDecision(decision="approved"), registry=_registry(),
        )
        assert result.run.status == "complete"
        assert result.executed == ("after",)

    async def test_rejecting_ends_the_run_as_rejected_not_failed(self, monkeypatch):
        """Nothing went wrong: a person looked and said no.

        Conflating the two would make a governance decision show up as an
        incident in every report that counts failures (CHY-83).
        """
        db, run_id, _ = await _at_the_gate(monkeypatch)
        result = await service.record_oversight(
            _NullPool(), _caller(), run_id,
            OversightDecision(decision="rejected", reason="numbers look wrong"),
            registry=_registry(),
        )
        assert result.run.status == "rejected"
        assert db.runs[run_id]["error_message"] == "numbers look wrong"
        assert result.executed == ()

    async def test_a_decision_for_a_run_that_is_not_waiting_is_refused(self, monkeypatch):
        """A decision recorded against a run that already moved on is recorded
        against nothing."""
        db, run_id, _ = await _at_the_gate(monkeypatch)
        db.runs[run_id]["status"] = "running"
        with pytest.raises(RunNotAwaitingOversight):
            await service.record_oversight(
                _NullPool(), _caller(), run_id,
                OversightDecision(decision="approved"), registry=_registry(),
            )

    async def test_a_waiting_run_with_no_claimed_node_is_refused(self, monkeypatch):
        """The run says it is waiting and no node is.

        Approving would let it continue from a position nobody recorded — and
        the record of where a run stood is the whole reason the gate is a row.
        """
        db, run_id, gate = await _at_the_gate(monkeypatch)
        for row in db.executions:
            if row["node_id"] == gate["id"]:
                row["status"] = "complete"
        with pytest.raises(OversightGateMissing):
            await service.record_oversight(
                _NullPool(), _caller(), run_id,
                OversightDecision(decision="approved"), registry=_registry(),
            )


class TestFourEyes:
    """A `four_eyes` gate completes on the second approval of a *different*
    person (CHY-176). Until 2026-09-30 the value was stored and never read, and
    the first approval closed every gate — the first test below failed then."""

    @staticmethod
    def _as(monkeypatch, *people):
        """Each call to `resolve_actor` is the next person in line."""
        queue = list(people)

        async def resolve(_pool, _caller):
            return queue.pop(0)

        monkeypatch.setattr(service, "resolve_actor", resolve)

    @staticmethod
    def _person():
        return SimpleNamespace(id=uuid4(), role="reviewer")

    async def _decide(self, run_id, decision="approved"):
        return await service.record_oversight(
            _NullPool(), _caller(), run_id,
            OversightDecision(decision=decision), registry=_registry(),
        )

    async def test_the_first_approval_leaves_the_run_at_the_gate(self, monkeypatch):
        db, run_id, gate = await _at_the_gate(monkeypatch, "four_eyes")
        self._as(monkeypatch, self._person())
        result = await self._decide(run_id)
        assert result.run.status == "awaiting_oversight"
        assert result.executed == ()
        claimed = [e for e in db.executions if e["node_id"] == gate["id"]]
        assert claimed[0]["status"] == "running"

    async def test_the_same_person_cannot_approve_twice(self, monkeypatch):
        db, run_id, _ = await _at_the_gate(monkeypatch, "four_eyes")
        one = self._person()
        self._as(monkeypatch, one, one)
        await self._decide(run_id)
        with pytest.raises(ReviewerAlreadyDecided):
            await self._decide(run_id)
        assert db.runs[run_id]["status"] == "awaiting_oversight"

    async def test_a_second_person_completes_the_gate(self, monkeypatch):
        db, run_id, _ = await _at_the_gate(monkeypatch, "four_eyes")
        self._as(monkeypatch, self._person(), self._person())
        await self._decide(run_id)
        result = await self._decide(run_id)
        assert result.run.status == "complete"
        assert result.executed == ("after",)

    async def test_a_rejection_ends_the_gate_at_once(self, monkeypatch):
        """One no is enough — four eyes add a second yes, not a second no."""
        db, run_id, _ = await _at_the_gate(monkeypatch, "four_eyes")
        self._as(monkeypatch, self._person(), self._person())
        await self._decide(run_id)
        result = await self._decide(run_id, "rejected")
        assert result.run.status == "rejected"

    @pytest.mark.parametrize("role", ["approver", None])
    async def test_one_approval_still_completes_any_other_gate(self, monkeypatch, role):
        """Counter-probe: the rule must not turn every gate into four eyes."""
        db, run_id, _ = await _at_the_gate(monkeypatch, role)
        self._as(monkeypatch, self._person())
        result = await self._decide(run_id)
        assert result.run.status == "complete"


def _caller():
    """Whoever is asking. `Caller` is a Protocol, so there is nothing to
    instantiate — the shared stub satisfies it structurally, which is the point
    of a Protocol and the reason the kernel declares one."""
    return stub_user(sub="reviewer-sub", username="reviewer")


# ── what a node actually receives (CHY-131) ───────────────────────────────────

class _Recorder:
    """An executor that keeps the context it was handed.

    The assertions below are about the *context*, not about the output, and a
    node that returns the right thing while having seen the wrong thing is
    exactly the failure that went unnoticed until now.
    """

    def __init__(self) -> None:
        self.seen: dict[str, NodeContext] = {}

    async def __call__(self, ctx: NodeContext) -> NodeResult:
        self.seen[f"{ctx.node_ref}#{ctx.iteration}"] = ctx
        # `pass` carries the iteration into the output on purpose. Without it
        # every lap of a loop produces an identical value, and a test asserting
        # "the predecessor output arrived" cannot tell the current lap from the
        # first — it would stay green while reading a stale one.
        return NodeResult(
            output={"text": f"{ctx.node_ref} produced this", "pass": ctx.iteration}
        )


class TestWhatANodeReceives:
    async def test_a_node_sees_its_predecessors_output(self, monkeypatch):
        """The bug this ticket exists for.

        `NodeContext.input_data` carried the run input for every node, so the
        second node could not read the first. `context_compose` assembled a
        context that `llm_call` had no way to receive — the mandatory core of
        ADR-004 was not connected.
        """
        a, b = _node("a"), _node("b")
        db = FakeDb(nodes=[a, b], connections=[_conn_row(a, b)])
        db.install(monkeypatch)
        run_id = _run(db)
        db.runs[run_id]["input_data"] = {"topic": "ZFS"}
        rec = _Recorder()

        await service.advance_run(
            _NullPool(), run_id, registry=ExecutorRegistry({"tool_call": rec})
        )

        received = rec.seen["b#1"].input_data
        assert received["a"] == {"text": "a produced this", "pass": 1}
        assert received["run"] == {"topic": "ZFS"}

    async def test_the_first_node_sees_the_run_input_and_no_predecessor(self, monkeypatch):
        a, b = _node("a"), _node("b")
        db = FakeDb(nodes=[a, b], connections=[_conn_row(a, b)])
        db.install(monkeypatch)
        run_id = _run(db)
        db.runs[run_id]["input_data"] = {"topic": "ZFS"}
        rec = _Recorder()

        await service.advance_run(
            _NullPool(), run_id, registry=ExecutorRegistry({"tool_call": rec})
        )

        assert rec.seen["a#1"].input_data == {"run": {"topic": "ZFS"}}

    async def test_an_input_mapping_selects_and_renames(self, monkeypatch):
        a, b = _node("a"), _node("b")
        b["input_mapping"] = {"prompt": "a.text"}
        db = FakeDb(nodes=[a, b], connections=[_conn_row(a, b)])
        db.install(monkeypatch)
        run_id = _run(db)
        rec = _Recorder()

        await service.advance_run(
            _NullPool(), run_id, registry=ExecutorRegistry({"tool_call": rec})
        )

        assert rec.seen["b#1"].input_data["prompt"] == "a produced this"

    async def test_the_node_gets_its_own_config(self, monkeypatch):
        """Without this the llm_call executor can never find a model.

        It reads `ctx.config["model"]`; the field defaulted to empty because
        nothing passed it, and an empty configuration is indistinguishable
        from a node nobody configured.
        """
        a = _node("a")
        a["config"] = {"model": "gemma3-4b", "system": "be brief"}
        db = FakeDb(nodes=[a], connections=[])
        db.install(monkeypatch)
        run_id = _run(db)
        rec = _Recorder()

        await service.advance_run(
            _NullPool(), run_id, registry=ExecutorRegistry({"tool_call": rec})
        )

        assert rec.seen["a#1"].config == {"model": "gemma3-4b", "system": "be brief"}

    async def test_the_node_knows_the_actor_and_the_scope(self, monkeypatch):
        """Both are recorded against the model call, so both must arrive.

        `actor_id` comes from the run, `scope_id` from the definition — a run
        does not carry a scope of its own, and inventing a second one would be
        a second answer to a question the definition already answers.
        """
        a = _node("a")
        db = FakeDb(nodes=[a], connections=[])
        db.install(monkeypatch)
        run_id = _run(db)
        rec = _Recorder()

        await service.advance_run(
            _NullPool(), run_id, registry=ExecutorRegistry({"tool_call": rec})
        )

        assert rec.seen["a#1"].actor_id == db.runs[run_id]["actor_id"]
        assert rec.seen["a#1"].scope_id == db.scope_id

    async def test_what_the_node_saw_is_written_down(self, monkeypatch):
        """`node_executions.input_data` is the record, not a reconstruction.

        Rebuilding it later would read whatever the predecessor produced most
        recently, which on a loop is a different lap than the one that ran.
        """
        a, b = _node("a"), _node("b")
        db = FakeDb(nodes=[a, b], connections=[_conn_row(a, b)])
        db.install(monkeypatch)
        run_id = _run(db)

        await service.advance_run(_NullPool(), run_id, registry=_registry())

        row = next(e for e in db.executions if e["node_id"] == b["id"])
        assert row["input_data"]["a"] == {"node": "a", "iteration": 1}

    async def test_a_node_in_a_loop_reads_the_current_lap_not_the_first(self, monkeypatch):
        """The reason `predecessor_outputs` orders by iteration.

        A quiz node that re-reads the first draft on every lap would improve
        nothing while producing a full audit trail of improvement. The output
        each lap carries its own iteration number, so the assertion is not
        "something arrived" but "the right pass arrived".
        """
        # A start node is not decoration: with only `gen → refine → gen` the
        # back edge makes `gen` a node with an incoming edge, so the graph has
        # no entry point and nothing ever runs.
        start, gen, refine = _node("start"), _node("gen"), _node("refine")
        db = FakeDb(
            nodes=[start, gen, refine],
            connections=[
                _conn_row(start, gen),
                _conn_row(gen, refine),
                _conn_row(refine, gen, loop_limit=_THREE_LAPS),
            ],
        )
        db.install(monkeypatch)
        run_id = _run(db)
        rec = _Recorder()

        await service.advance_run(
            _NullPool(), run_id, registry=ExecutorRegistry({"tool_call": rec})
        )

        # The loop ran; the later passes are what this test is about.
        assert f"refine#{_TWO_PASSES}" in rec.seen, sorted(rec.seen)

        # Second lap of `gen` reads the *first* pass of `refine` — that is the
        # pass that traversed the back edge and armed it.
        assert rec.seen[f"gen#{_TWO_PASSES}"].input_data["refine"]["pass"] == 1

        # And the second lap of `refine` reads the second pass of `gen`, not
        # the first. This is the assertion the ordering in
        # `store.predecessor_outputs` exists for: drop the `iteration DESC` and
        # it reads 1 here.
        assert rec.seen[f"refine#{_TWO_PASSES}"].input_data["gen"]["pass"] == _TWO_PASSES

    async def test_a_pass_that_has_not_finished_is_not_a_predecessor_output(
        self, monkeypatch
    ):
        """Only `complete` counts, and the counter-probe for the filter.

        A claimed-but-unfinished pass has `output_data` NULL. Read anyway, the
        predecessor would appear as an empty mapping instead of being absent —
        and a node would run on `{}` while every row says its input was there.

        Driven through `_node_input` rather than through a whole run: reaching
        this state from `advance_run` means a fork with one branch in flight,
        which would test the join policy, not the filter.
        """
        a, b = _node("a"), _node("b")
        db = FakeDb(nodes=[a, b], connections=[_conn_row(a, b)])
        db.install(monkeypatch)
        run_id = _run(db)
        db.executions.append({
            "id": uuid4(), "run_id": run_id, "node_id": a["id"], "iteration": 1,
            "status": "running", "consumed_edges": [], "output_data": None,
            "model_used": None, "input_data": {},
        })

        conn = _NullConn()
        graph = await service._load_graph(conn, _WF)
        details = await service._load_node_details(conn, _WF)
        received = await service._node_input(
            conn, graph, db.runs[run_id], b["id"], details
        )

        assert "a" not in received, "an unfinished pass must not look like an input"
        assert received == {"run": {}}


# ── the mandatory core, end to end (CHY-133) ──────────────────────────────────

class TestTheMandatoryCoreRuns:
    """`context_compose → llm_call → human_review`, the shape ADR-004 requires.

    Until CHY-133 this could not run at all: `context_compose` had no executor,
    so every valid workflow died on its first node. And until CHY-131 it could
    not have worked even with one, because the composed context had no way to
    reach the model call.
    """

    @staticmethod
    def _core(mapping: dict | None = None) -> tuple:
        compose = _node("compose", "context_compose")
        call = _node("answer", "llm_call")
        call["input_mapping"] = mapping if mapping is not None else {
            "prompt": "compose.prompt"
        }
        gate = _node("approve", "human_review")
        return compose, call, gate

    @staticmethod
    def _registry(seen: dict) -> ExecutorRegistry:
        async def compose(ctx: NodeContext) -> NodeResult:
            return NodeResult(output={
                "prompt": "you are a helpful assistant",
                "block_versions": {"b1": 3},
            })

        async def call(ctx: NodeContext) -> NodeResult:
            prompt = ctx.config.get("prompt") or ctx.input_data.get("prompt", "")
            if not prompt:
                # Mirrors the shape of the real refusal in api/v1/workflows.py,
                # which names the node — the message is that executor's own
                # responsibility and is asserted in test_workflow_executors.py.
                raise NodeTypeNotExecutable(
                    ctx.node_type, ctx.node_ref,
                    reason=f"Node {ctx.node_ref} has no prompt.",
                )
            seen["prompt"] = prompt
            return NodeResult(output={"content": "…"}, model_used="gemma3-4b")

        return ExecutorRegistry({"context_compose": compose, "llm_call": call})

    async def test_the_composed_context_reaches_the_model_and_the_run_waits(
        self, monkeypatch
    ):
        compose, call, gate = self._core()
        db = FakeDb(nodes=[compose, call, gate],
                    connections=[_conn_row(compose, call), _conn_row(call, gate)])
        db.install(monkeypatch)
        run_id = _run(db)
        seen: dict = {}

        result = await service.advance_run(
            _NullPool(), run_id, registry=self._registry(seen)
        )

        assert seen["prompt"] == "you are a helpful assistant"
        assert result.executed == ("compose", "answer")
        assert result.run.status == "awaiting_oversight"

    async def test_what_the_composition_contained_is_written_down(
        self, monkeypatch
    ):
        """`block_versions` in `node_executions.output_data` is what makes the
        run reproducible later."""
        compose, call, gate = self._core()
        db = FakeDb(nodes=[compose, call, gate],
                    connections=[_conn_row(compose, call), _conn_row(call, gate)])
        db.install(monkeypatch)
        run_id = _run(db)

        await service.advance_run(_NullPool(), run_id, registry=self._registry({}))

        row = next(e for e in db.executions if e["node_id"] == compose["id"])
        assert row["output_data"]["block_versions"] == {"b1": 3}

    async def test_without_the_mapping_the_run_fails_loudly(self, monkeypatch):
        """The counter-probe, and the reason the mapping is explicit.

        Nothing connects `compose` to `answer` by convention — the edge carries
        control, the mapping carries data. Drop the mapping and the model must
        not be called with an empty prompt: that reaches a model, gets billed
        and traced, and comes back with an answer to no question.
        """
        compose, call, gate = self._core(mapping={})
        db = FakeDb(nodes=[compose, call, gate],
                    connections=[_conn_row(compose, call), _conn_row(call, gate)])
        db.install(monkeypatch)
        run_id = _run(db)
        seen: dict = {}

        result = await service.advance_run(
            _NullPool(), run_id, registry=self._registry(seen)
        )

        assert result.run.status == "failed"
        assert "prompt" not in seen, "the model was called without a prompt"
        assert "answer" in db.runs[run_id]["error_message"]
