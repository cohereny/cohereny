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

import typing
from dataclasses import dataclass, field
from types import SimpleNamespace
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from contexts.orchestration import runner, service
from contexts.orchestration.errors import (
    DocumentAlreadyReceived,
    NodeExecutionFailed,
    NodeTypeNotExecutable,
    OversightGateMissing,
    ReviewerAlreadyDecided,
    RunAlreadyFinished,
    RunNotAwaitingOversight,
    ServiceAccountMayNotDecide,
)
from contexts.orchestration.execution import ExecutorRegistry, NodeContext, NodeResult
from contexts.orchestration.schema import OversightDecision, RunCancelRequest, RunStatus
from kernel import mapping_cipher
from kernel.redaction import RedactionPolicy
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
    #: Sealed mappings per run, as `pii_mappings` (036, CHY-234). A run with
    #: no entry is one started before CHY-234: it advances without keys.
    mappings: dict[UUID, dict] = field(default_factory=dict)
    #: What arrived for each run, as `workflow_run_documents` (041, ADR-049).
    documents: list[dict] = field(default_factory=list)
    #: The data class of the workflow's scope and the workflow's risk class
    #: (CHY-223). Classified so the minimum is `approver` and every gate asks
    #: for what it was drawn with; the tests of the minimum set them otherwise.
    data_class: str = "internal"
    risk_class: str | None = "limited"
    #: Audit entries, as `audit_log` — the engine writes one when a run
    #: reaches a gate (CHY-223).
    audits: list[dict] = field(default_factory=list)
    #: Passes of sample gates drawn for review, as `review_samples` (045).
    samples: list[dict] = field(default_factory=list)

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

        async def cancel_open_executions(_conn, run_id, reason):
            stopped = []
            for row in self.executions:
                if row["run_id"] == run_id and row["status"] in ("pending", "running"):
                    row.update(status="cancelled", error_message=reason)
                    stopped.append(next(n["node_ref"] for n in self.nodes
                                        if n["id"] == row["node_id"]))
            return sorted(stopped)

        async def record_traversals(_conn, run_id, edge_ids):
            for edge_id in edge_ids:
                key = (run_id, edge_id)
                self.traversals[key] = self.traversals.get(key, 0) + 1

        async def node_details(_conn, workflow_id):
            return [
                {"id": n["id"], "config": n["config"],
                 "context_requirements": n["context_requirements"],
                 "task_type": n["task_type"],
                 "output_schema": n["output_schema"],
                 "function": n["function"], "function_version": n["function_version"],
                 "document_name": n["document_name"],
                 "document_max_bytes": n["document_max_bytes"],
                 "document_max_pages": n["document_max_pages"],
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
                    "status": "active", "version": 1, "risk_class": self.risk_class}

        async def data_class(_conn, _scope_id):
            return self.data_class

        async def set_review_level(_conn, execution_id, level):
            next(e for e in self.executions if e["id"] == execution_id)["review_level"] = level

        async def review_level(_conn, execution_id):
            return next(e for e in self.executions if e["id"] == execution_id).get("review_level")

        async def write_audit(_conn, **entry):
            self.audits.append(entry)

        async def review_sample_rate(_conn, node_id):
            return next(n for n in self.nodes if n["id"] == node_id).get("review_sample_rate")

        async def insert_sample(_conn, execution_id, run_id):
            self.samples.append({"execution_id": execution_id, "run_id": run_id})

        async def set_run_status(_conn, run_id, status, *, result=None, error=None):
            row = self.runs[run_id]
            row.update(status=status, result=result, error_message=error)
            return row

        async def review_role(_conn, node_id):
            return next(n["review_role"] for n in self.nodes if n["id"] == node_id)

        async def review_areas(_conn, node_id):
            # Gates without areas only: those with areas need the competence
            # order of migration 043, and their tests run against PostgreSQL
            # (tests/integration/test_review_areas_db.py).
            return next(n.get("review_areas") for n in self.nodes if n["id"] == node_id)

        async def record_vote(_conn, execution_id, vote):
            """The primary key of 027, in a list: one decision per person."""
            if any(v["execution_id"] == execution_id and v["actor_id"] == vote.actor_id
                   for v in self.votes):
                raise ReviewerAlreadyDecided(vote.actor_id)
            self.votes.append({"execution_id": execution_id, "actor_id": vote.actor_id,
                               "decision": vote.decision, "reason": vote.reason})

        async def approvals(_conn, execution_id):
            return sum(1 for v in self.votes
                       if v["execution_id"] == execution_id and v["decision"] == "approved")

        async def pii_mapping(_conn, run_id):
            return self.mappings.get(run_id)

        async def write_pii_mapping(_conn, run_id, *, key_id, nonce, ciphertext):
            self.mappings[run_id] = {"key_id": key_id, "nonce": nonce, "ciphertext": ciphertext}

        async def document_nodes(_conn, workflow_id):
            return [{"document_name": n["document_name"],
                     "document_max_bytes": n["document_max_bytes"],
                     "document_max_pages": n["document_max_pages"]}
                    for n in sorted(self.nodes, key=lambda n: n["document_name"] or "")
                    if n["workflow_id"] == workflow_id and n["node_type"] == "document"]

        async def run_documents(_conn, run_id):
            return sorted((d for d in self.documents if d["run_id"] == run_id),
                          key=lambda d: d["name"])

        async def run_document(_conn, run_id, name):
            return next((d for d in self.documents
                         if d["run_id"] == run_id and d["name"] == name), None)

        async def insert_document(_conn, new):
            """The primary key of 041, in a list: one document per name and run."""
            if any(d["run_id"] == new.run_id and d["name"] == new.name for d in self.documents):
                raise DocumentAlreadyReceived(new.name)
            self.documents.append({**new.__dict__, "received_at": datetime.now(UTC)})

        async def start_waiting_run(_conn, run_id):
            row = self.runs[run_id]
            if row["status"] != "awaiting_documents":
                return None
            row.update(status="running")
            return row

        for name, fn in (
            ("review_role", review_role), ("review_areas", review_areas),
            ("record_vote", record_vote),
            ("approvals", approvals),
            ("nodes", nodes), ("connections", connections), ("lock_run", lock_run),
            ("executions", executions), ("traversals", traversal_rows),
            ("start_execution", start_execution),
            ("complete_execution", complete_execution), ("fail_execution", fail_execution),
            ("cancel_open_executions", cancel_open_executions),
            ("record_traversals", record_traversals), ("set_run_status", set_run_status),
            ("node_details", node_details), ("predecessor_outputs", predecessor_outputs),
            ("workflow", workflow), ("data_class", data_class),
            ("set_review_level", set_review_level), ("review_level", review_level),
            ("review_sample_rate", review_sample_rate), ("insert_sample", insert_sample),
            # No SQL, so no lock to take: a lock is shown against PostgreSQL
            # (tests/integration/test_run_placeholders_db.py).
            ("pii_mapping", pii_mapping), ("lock_pii_mapping", pii_mapping),
            ("insert_pii_mapping", write_pii_mapping), ("update_pii_mapping", write_pii_mapping),
            ("document_nodes", document_nodes), ("run_documents", run_documents),
            ("run_document", run_document), ("insert_document", insert_document),
            ("start_waiting_run", start_waiting_run),
            # Reading without the lock reads the same rows.
            ("run", lock_run),
        ):
            monkeypatch.setattr(service.store, name, fn)
        monkeypatch.setattr(service, "write_audit", write_audit)


class _NullTx:
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False


class _NullConn:
    def transaction(self): return _NullTx()

    # kernel.acting sets and clears the acting person on the connection (CHY-92).
    async def execute(self, *a): return None


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
        "output_schema": None,
        "function": None, "function_version": None,
        "document_name": None, "document_max_bytes": None, "document_max_pages": None,
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


#: The keys a test run's mapping is sealed with (CHY-234).
KEYS = mapping_cipher.keyring_from(mapping_cipher.generate_key("test"))


async def _shipped_rules_only(_scope_id):
    return RedactionPolicy()


def _engine(registry: ExecutorRegistry | None = None) -> service.Engine:
    return service.Engine(registry or _registry(), _NullPool(),
                          service.Placeholders(policy_for=_shipped_rules_only, keys=KEYS))


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

    from kernel import current_call

    current_call.begin("abc123")
    with pytest.raises(ModelRefused):
        await service.advance_run(
            _NullPool(), run_id, registry=ExecutorRegistry({"llm_call": refused})
        )
    assert db.runs[run_id]["status"] == "failed"
    message = db.runs[run_id]["error_message"]
    assert "ModelRefused" in message
    # Not the exception's own text: a library's can name an address or a
    # value, and the run is read by everyone who may read it (CHY-97).
    assert "unknown model" not in message and "status 400" not in message
    assert message.endswith("(request ID abc123)")
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

async def _at_the_gate(monkeypatch, review_role: str | None = None,
                       **classification) -> tuple[FakeDb, UUID, dict]:
    """A run stopped at a human_review, with the gate claimed.

    `classification` sets the FakeDb's `data_class` and `risk_class` (CHY-223).
    """
    a = _node("a")
    gate = {**_node("approve", "human_review"), "review_role": review_role}
    after = _node("after")
    db = FakeDb(nodes=[a, gate, after],
                connections=[_conn_row(a, gate), _conn_row(gate, after)], **classification)
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
    # The audit entries go to `db.audits`, installed with the rest.
    # The role lookup belongs to the kernel and has its own tests; reproducing
    # it here would test kernel twice and the decision path once.
    monkeypatch.setattr(service, "resolve_actor", _stub_actor)
    monkeypatch.setattr(service, "require", lambda *_a: None)

    await service.advance_run(_NullPool(), run_id, registry=_registry())
    return db, run_id, gate


async def _stub_actor(_pool, _caller):
    return SimpleNamespace(id=uuid4(), role="reviewer", is_service_account=False)


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
            OversightDecision(decision="approved"), engine=_engine(),
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
            engine=_engine(),
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
                OversightDecision(decision="approved"), engine=_engine(),
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
                OversightDecision(decision="approved"), engine=_engine(),
            )


class TestCancel:
    """A person stops a run (CHY-200, AI Act Art. 14(4)(e)).

    The two tests in the middle are the ones the store cannot show: a node that
    is still working when the stop arrives. Its result must not move the run
    on, and its failure must not turn the stop into an incident. Without the
    check in `_record` and `_record_failure` the first ends with the execution
    `complete`, the second with the run `failed`.
    """

    async def test_at_the_gate_run_and_gate_end_cancelled_and_nobody_decided(self, monkeypatch):
        db, run_id, gate = await _at_the_gate(monkeypatch)
        out = await service.cancel_run(_NullPool(), _caller(), run_id,
                                       RunCancelRequest(reason="wrong file"), engine=_engine())
        assert out.status == "cancelled"
        assert db.runs[run_id]["error_message"] == "wrong file"
        claimed = next(e for e in db.executions if e["node_id"] == gate["id"])
        assert claimed["status"] == "cancelled"
        assert db.votes == [], "stopping is not a decision at the gate"

    @staticmethod
    def _stopping_executor(db: FakeDb, run_id: UUID, *, then_fail: bool):
        """A node during which a person stops the run, as cancel_run leaves it."""
        async def work(ctx: NodeContext) -> NodeResult:
            db.runs[run_id]["status"] = "cancelled"
            for row in db.executions:
                if row["run_id"] == run_id and row["status"] == "running":
                    row["status"] = "cancelled"
            if then_fail:
                raise NodeExecutionFailed(ctx.node_ref, "model refused")
            return NodeResult(output={"late": True})
        return work

    async def test_a_result_that_arrives_after_the_stop_is_dropped(self, monkeypatch):
        a, b = _node("a"), _node("b")
        db = FakeDb(nodes=[a, b], connections=[_conn_row(a, b)])
        db.install(monkeypatch)
        run_id = _run(db)
        registry = ExecutorRegistry({"tool_call": self._stopping_executor(db, run_id,
                                                                          then_fail=False)})

        result = await service.advance_run(_NullPool(), run_id, registry=registry)

        assert result.run.status == "cancelled"
        assert result.executed == ("a",), "b never starts"
        assert [(e["status"], e["output_data"]) for e in db.executions] == [("cancelled", None)]

    async def test_a_failure_after_the_stop_does_not_turn_it_into_one(self, monkeypatch):
        a = _node("a")
        db = FakeDb(nodes=[a])
        db.install(monkeypatch)
        run_id = _run(db)
        registry = ExecutorRegistry({"tool_call": self._stopping_executor(db, run_id,
                                                                          then_fail=True)})

        result = await service.advance_run(_NullPool(), run_id, registry=registry)

        assert result.run.status == "cancelled"
        assert db.runs[run_id]["error_message"] is None
        assert db.executions[0]["status"] == "cancelled"

    async def test_a_run_that_is_over_is_not_stopped_again(self, monkeypatch):
        db, run_id, _ = await _at_the_gate(monkeypatch)
        db.runs[run_id]["status"] = "complete"
        with pytest.raises(RunAlreadyFinished):
            await service.cancel_run(_NullPool(), _caller(), run_id, RunCancelRequest(),
                                     engine=_engine())
        assert db.runs[run_id]["status"] == "complete"


def test_every_end_is_an_end_everywhere():
    """Three places decide that a run is over, and they must agree (CHY-200).

    The runner stops advancing it, the purge clears it at the end of its period
    (`store._RETAINED_RUN_STATES`), and every state is either one of those ends
    or one of those a run passes through. `cancelled` missing from the purge
    list would keep a stopped run's content for good — no error, no warning,
    just a run that never expires.

    One state the run passes through is in the purge list too, and only one:
    `awaiting_documents`. Such a run does no work, and one whose documents
    never come would otherwise keep what did arrive for good (ADR-049). A run
    at a gate is not in it — its content is what the reviewer reads (CHY-254).
    """
    passing_through = {"pending", "running", "awaiting_oversight", "awaiting_documents"}
    retained = set(service.store._RETAINED_RUN_STATES)
    assert retained == service._TERMINAL_RUN_STATES | {"awaiting_documents"}
    assert service._TERMINAL_RUN_STATES | passing_through == set(typing.get_args(RunStatus))
    assert not service._TERMINAL_RUN_STATES & passing_through


class TestTechnicalAccountsDoNotDecide:
    """A technical account never decides at a gate (CHY-275).

    Its role may suffice — cohereny-ci is architect — and it is still not a
    person. Refused before the run is read, so the answer is the same whether
    the run exists or not, and nothing is recorded.
    """

    async def test_it_is_refused_and_the_gate_stays_as_it_was(self, monkeypatch):
        db, run_id, gate = await _at_the_gate(monkeypatch)
        account = SimpleNamespace(id=uuid4(), role="architect", is_service_account=True)
        monkeypatch.setattr(service, "resolve_actor", lambda _pool, _caller: _resolved(account))
        with pytest.raises(ServiceAccountMayNotDecide):
            await service.record_oversight(_NullPool(), _caller(), run_id,
                                           OversightDecision(decision="approved"), engine=_engine())
        claimed = [e for e in db.executions if e["node_id"] == gate["id"]]
        assert claimed[0]["status"] == "running", "the gate is still waiting"
        assert db.runs[run_id]["status"] == "awaiting_oversight"

    async def test_a_person_with_the_same_role_decides(self, monkeypatch):
        """Counter-check: the mark refuses, not the role."""
        _db, run_id, _gate = await _at_the_gate(monkeypatch)
        person = SimpleNamespace(id=uuid4(), role="architect", is_service_account=False)
        monkeypatch.setattr(service, "resolve_actor", lambda _pool, _caller: _resolved(person))
        result = await service.record_oversight(_NullPool(), _caller(), run_id,
                                                OversightDecision(decision="approved"),
                                                engine=_engine())
        assert result.run.status == "complete"


async def _resolved(actor):
    return actor


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
        return SimpleNamespace(id=uuid4(), role="reviewer", is_service_account=False)

    async def _decide(self, run_id, decision="approved"):
        return await service.record_oversight(
            _NullPool(), _caller(), run_id,
            OversightDecision(decision=decision), engine=_engine(),
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


class TestALoopThatRunsOut:
    """A bounded loop that never meets its criterion (CHY-150, Thomas 09.11.2026).

    `draft → check`, back to `draft` while the check wants more work, at most
    twice; the default leads to the review. Until CHY-150 the run ended as
    `complete` once the loop ran out — without the review, and with nothing
    recorded but a counter. Driven through the service, because the runner's
    own tests passed throughout: they built the firing and never asked what
    the run did with it.
    """

    #: The check's output is `{"node": "check", ...}`; this guard always holds,
    #: so the check always wants another lap.
    WANTS_MORE = {"field": "node", "op": "eq", "value": "check"}

    def _loop(self, monkeypatch, *, with_review: bool) -> tuple[FakeDb, UUID]:
        start, draft, check = _node("start"), _node("draft"), _node("check")
        nodes = [start, draft, check]
        edges = [_conn_row(start, draft), _conn_row(draft, check),
                 _conn_row(check, draft, loop_limit=2,
                           condition=self.WANTS_MORE if with_review else None)]
        if with_review:
            review = _node("review", "human_review")
            nodes.append(review)
            edges.append(_conn_row(check, review, label="default"))
        db = FakeDb(nodes=nodes, connections=edges)
        db.install(monkeypatch)
        return db, _run(db)

    def _exhausted(self, db: FakeDb) -> list[dict]:
        return [a["payload"] for a in db.audits if a["action"] == "workflow.loop_exhausted"]

    async def test_it_goes_on_to_the_review_and_says_the_budget_ran_out(self, monkeypatch):
        db, run_id = self._loop(monkeypatch, with_review=True)

        result = await service.advance_run(_NullPool(), run_id, registry=_registry())

        assert result.run.status == "awaiting_oversight"
        assert result.executed.count("check") == _THREE_LAPS
        assert self._exhausted(db) == [{
            "node_ref": "check", "to": "draft", "loop_limit": 2,
            "went_on_to": ["review"], "run_failed": False}]

    async def test_with_no_way_on_the_run_fails_saying_why(self, monkeypatch):
        db, run_id = self._loop(monkeypatch, with_review=False)

        result = await service.advance_run(_NullPool(), run_id, registry=_registry())

        assert result.run.status == "failed"
        assert "loop_limit of 2" in db.runs[run_id]["error_message"]
        assert self._exhausted(db) == [{
            "node_ref": "check", "to": "draft", "loop_limit": 2,
            "went_on_to": [], "run_failed": True}]

    async def test_a_check_that_passes_leaves_nothing_in_the_log(self, monkeypatch):
        """Counter-probe: the entry is about running out, not about looping."""
        db, run_id = self._loop(monkeypatch, with_review=True)
        passes = {**self.WANTS_MORE, "value": "never"}
        db.connections[2]["condition"] = passes

        result = await service.advance_run(_NullPool(), run_id, registry=_registry())

        assert result.run.status == "awaiting_oversight"
        assert result.executed.count("check") == 1
        assert self._exhausted(db) == []


class TestJoinsOfAStoredWorkflow:
    """The join policy of a node as the table holds it (CHY-260).

    tests/test_workflow_runner.py proves `wait_all` and `first_wins` on graphs
    built by hand. The graph a run is advanced on is loaded from the table, and
    until 09.11.2026 the load dropped `join_policy`: every join behaved as
    `wait_any`. The credit workflow (CHY-178) is where it showed — a step with
    its prompt and three upstream results started as soon as the prompt was
    composed, read the rest as missing, and failed.

    The shape is the one that tells the policies apart: one branch is ready at
    once, the other needs a step more. A join that fires on the first token
    runs before the second branch has finished.
    """

    def _fork(self, monkeypatch, policy: str):
        prompt, doc, figures = _node("prompt"), _node("doc"), _node("figures")
        step = _node("step", join_policy=policy)
        db = FakeDb(nodes=[prompt, doc, figures, step], connections=[
            _conn_row(prompt, step), _conn_row(doc, figures), _conn_row(figures, step)])
        db.install(monkeypatch)
        return _run(db)

    async def test_wait_all_runs_once_and_reads_every_branch(self, monkeypatch):
        run_id = self._fork(monkeypatch, "wait_all")
        rec = _Recorder()

        result = await service.advance_run(
            _NullPool(), run_id, registry=ExecutorRegistry({"tool_call": rec}))

        assert result.run.status == "complete"
        assert sorted(k for k in rec.seen if k.startswith("step#")) == ["step#1"]
        assert {"prompt", "figures"} <= set(rec.seen["step#1"].input_data)

    async def test_first_wins_runs_once_and_drops_the_straggler(self, monkeypatch):
        run_id = self._fork(monkeypatch, "first_wins")
        rec = _Recorder()

        await service.advance_run(
            _NullPool(), run_id, registry=ExecutorRegistry({"tool_call": rec}))

        assert sorted(k for k in rec.seen if k.startswith("step#")) == ["step#1"]

    async def test_without_a_policy_a_second_arrival_fires_again(self, monkeypatch):
        """Counter-probe: the shape does tell them apart. With no policy (and
        so `wait_any`) the step runs on each arrival — the behaviour every
        stored join had until the fix."""
        run_id = self._fork(monkeypatch, None)
        rec = _Recorder()

        await service.advance_run(
            _NullPool(), run_id, registry=ExecutorRegistry({"tool_call": rec}))

        assert sorted(k for k in rec.seen if k.startswith("step#")) == ["step#1", "step#2"]
        assert "figures" not in rec.seen["step#1"].input_data


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


# ── an llm_call's answer, checked against its schema (CHY-175) ────────────────

_RISK = {
    "type": "object",
    "required": ["risk_level"],
    "properties": {"risk_level": {"enum": ["low", "high"]}},
}


def _answering(text: str):
    """An executor that returns the model's answer as it came, unchecked.

    It does not check anything itself, and that is the point: the check is the
    service's, so an injected executor cannot skip it (ADR-028 point 3).
    """
    async def call(_ctx: NodeContext) -> NodeResult:
        return NodeResult(output={"content": text})
    return call


class TestOutputSchema:
    async def test_a_matching_answer_is_in_the_output_as_data(self, monkeypatch):
        ask = {**_node("ask", "llm_call"), "output_schema": _RISK}
        db = FakeDb(nodes=[ask], connections=[])
        db.install(monkeypatch)
        run_id = _run(db)

        await service.advance_run(_NullPool(), run_id, registry=ExecutorRegistry(
            {"llm_call": _answering('```json\n{"risk_level": "high"}\n```')}))

        [execution] = db.executions
        assert execution["status"] == "complete"
        assert execution["output_data"]["data"] == {"risk_level": "high"}

    async def test_a_mismatch_fails_the_run_and_says_where_not_what(self, monkeypatch):
        ask = {**_node("ask", "llm_call"), "output_schema": _RISK}
        db = FakeDb(nodes=[ask], connections=[])
        db.install(monkeypatch)
        run_id = _run(db)

        result = await service.advance_run(_NullPool(), run_id, registry=ExecutorRegistry(
            {"llm_call": _answering('{"risk_level": "erika.mustermann@example.de"}')}))

        assert result.run.status == "failed"
        [execution] = db.executions
        assert execution["status"] == "failed"
        for message in (execution["error_message"], db.runs[run_id]["error_message"]):
            assert "at $.risk_level: enum" in message
            assert "erika" not in message

    async def test_without_a_schema_the_same_answer_passes(self, monkeypatch):
        """The counter-probe of the acceptance: the check is the schema's doing."""
        ask = _node("ask", "llm_call")
        db = FakeDb(nodes=[ask], connections=[])
        db.install(monkeypatch)
        run_id = _run(db)

        result = await service.advance_run(_NullPool(), run_id, registry=ExecutorRegistry(
            {"llm_call": _answering('{"risk_level": "erika.mustermann@example.de"}')}))

        assert result.run.status == "complete"
        assert "data" not in db.executions[0]["output_data"]

    async def test_an_edge_reads_what_was_checked(self, monkeypatch):
        ask = {**_node("ask", "llm_call"), "output_schema": _RISK}
        high, low = _node("high"), _node("low")
        guard = {"field": "data.risk_level", "op": "eq"}
        db = FakeDb(nodes=[ask, high, low], connections=[
            _conn_row(ask, high, condition={**guard, "value": "high"}),
            _conn_row(ask, low, condition={**guard, "value": "low"}),
        ])
        db.install(monkeypatch)
        run_id = _run(db)

        result = await service.advance_run(_NullPool(), run_id, registry=ExecutorRegistry(
            {"llm_call": _answering('{"risk_level": "high"}'), "tool_call": _ok}))

        assert result.run.status == "complete"
        assert result.executed == ("ask", "high")

    async def test_the_executor_learns_that_a_shape_was_asked_for(self, monkeypatch):
        """So it can ask the gateway for JSON; checking stays with the service."""
        ask = {**_node("ask", "llm_call"), "output_schema": _RISK}
        db = FakeDb(nodes=[ask], connections=[])
        db.install(monkeypatch)
        run_id = _run(db)
        seen = []

        async def call(ctx: NodeContext) -> NodeResult:
            seen.append(ctx.output_schema)
            return NodeResult(output={"content": '{"risk_level": "low"}'})

        await service.advance_run(_NullPool(), run_id, registry=ExecutorRegistry({"llm_call": call}))
        assert seen == [_RISK]


# ── compute steps (ADR-048, CHY-173) ──────────────────────────────────────────

_FIGURES = {
    "bilanzsumme_current": 5_000_000, "eigenkapital_current": 1_250_000,
    "umlaufvermoegen_current": 1_500_000, "kurzfristige_verbindlichkeiten_current": 2_200_000,
    "kurzfristige_verbindlichkeiten_prior": 1_400_000, "umsatz_current": 6_000_000,
    "ebit_current": 150_000, "abschreibungen_current": 200_000,
}


def _reading(figures: dict):
    """Stands for an llm_call whose answer was checked into `data` (CHY-175)."""
    async def read(_ctx: NodeContext) -> NodeResult:
        return NodeResult(output={"data": figures})
    return read


def _compute_node(ref: str, function: str, version: int, mapping: dict) -> dict:
    return {**_node(ref, "compute"), "function": function, "function_version": version,
            "input_mapping": mapping}


class TestCompute:
    """The executor is built in: the registry needs no entry for it."""

    async def test_a_step_computes_from_what_its_mapping_hands_it(self, monkeypatch):
        read = _node("extract", "llm_call")
        calc = _compute_node("ratios", "banking.ratios", 1, {"kpis": "extract.data"})
        db = FakeDb(nodes=[read, calc], connections=[_conn_row(read, calc)])
        db.install(monkeypatch)
        run_id = _run(db)

        result = await service.advance_run(_NullPool(), run_id, registry=ExecutorRegistry(
            {"llm_call": _reading(_FIGURES)}))

        assert result.run.status == "complete"
        out = next(e for e in db.executions if e["node_id"] == calc["id"])["output_data"]
        assert out["function"] == "banking.ratios@1"
        assert out["data"]["current_ratio"] == pytest.approx(1_500_000 / 2_200_000)

    async def test_a_missing_figure_fails_the_run_with_the_field(self, monkeypatch):
        read = _node("extract", "llm_call")
        calc = _compute_node("ratios", "banking.ratios", 1, {"kpis": "extract.data"})
        db = FakeDb(nodes=[read, calc], connections=[_conn_row(read, calc)])
        db.install(monkeypatch)
        run_id = _run(db)
        figures = {k: v for k, v in _FIGURES.items() if k != "umsatz_current"}

        result = await service.advance_run(_NullPool(), run_id, registry=ExecutorRegistry(
            {"llm_call": _reading(figures)}))

        assert result.run.status == "failed"
        assert ("Node ratios failed: banking.ratios@1: kpis.umsatz_current is missing"
                in db.runs[run_id]["error_message"])

    async def test_an_edge_reads_what_a_step_computed(self, monkeypatch):
        read = _node("extract", "llm_call")
        calc = _compute_node("ratios", "banking.ratios", 1, {"kpis": "extract.data"})
        flag = _compute_node("anomalies", "banking.anomalies", 1, {"ratios": "ratios.data"})
        look, done = _node("look_closer"), _node("done")
        db = FakeDb(nodes=[read, calc, flag, look, done], connections=[
            _conn_row(read, calc), _conn_row(calc, flag),
            _conn_row(flag, look, condition={"field": "data.count", "op": "gt", "value": 0}),
            _conn_row(flag, done, condition={"field": "data.count", "op": "eq", "value": 0}),
        ])
        db.install(monkeypatch)
        run_id = _run(db)

        result = await service.advance_run(_NullPool(), run_id, registry=ExecutorRegistry(
            {"llm_call": _reading(_FIGURES), "tool_call": _ok}))

        assert result.run.status == "complete"
        assert result.executed == ("extract", "ratios", "anomalies", "look_closer")

    async def test_a_step_the_code_does_not_have_fails_the_node(self, monkeypatch):
        """Activation refuses it (integration test); this is the run-time backstop."""
        calc = _compute_node("ratios", "banking.ratios", 99, {})
        db = FakeDb(nodes=[calc], connections=[])
        db.install(monkeypatch)
        run_id = _run(db)

        result = await service.advance_run(_NullPool(), run_id, registry=ExecutorRegistry({}))

        assert result.run.status == "failed"
        assert "banking.ratios@99 is not a registered step" in db.runs[run_id]["error_message"]
