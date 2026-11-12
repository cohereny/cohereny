"""
tests/integration/test_pilot_lifecycle.py

A pilot run's life on a real database, through the service (CHY-124).

The acceptance criterion asks for a pass through pilots, anonymisation and the
audit trail against a real database. Anonymisation and the audit trail are in
test_governance_triggers.py; this is the pilots half, beyond the two triggers
already tested there.

What runs here is `contexts.pilots.service`, as the transport calls it — minus
the transport. Since M3b the use cases can be driven without a client (ADR-030),
and that is what these tests do. `start_run` is the one step left out: it hands
the run to a thread that runs the LangGraph pilot and calls a model. The rows it
writes are written here with `store.insert_run`, the function it uses.

One test does start the thread (CHY-153): the pilot's own `stream_workflow` and
gate node, with the graph reduced to the gate, so that cancelling at the gate
is shown against the trigger from 014 rather than described.

What only a real database can show, and the unit tests cannot:
  - that a refused delete leaves no audit entry behind. The service writes the
    entry and deletes in one transaction; with a mock the "rollback" is whatever
    the mock does, here it is PostgreSQL's
  - that the RESTRICT from migration 015 reaches the service as the exception
    it translates, not as some other class that would leave as a 500
"""
from __future__ import annotations

import asyncio
import importlib
import sys
from pathlib import Path
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from api.config import settings
from contexts.pilots import service, store
from contexts.pilots.errors import RunHasTransparencyRecords, RunNotFound
from contexts.pilots.execution import RunSpec, run_in_thread
from contexts.pilots.registry import RunRegistry
from kernel.audit import ACTION_PILOT_RUN_CANCELLED, ACTION_PILOT_RUN_DELETED
from kernel.identity import resolve_actor
from tests.integration._callers import caller

pytestmark = pytest.mark.integration


async def _run(pool, owner_id, status: str, pilot: str = "study"):
    """A run as start_run records it, then moved to `status`."""
    async with pool.acquire() as conn:
        row = await store.insert_run(
            conn, thread_id=f"it-{uuid4().hex}", pilot_name=pilot,
            input_file="it.pdf", actor_id=owner_id, run_config={}, effective_config={},
        )
    await pool.execute("UPDATE pilot_runs SET status = $1 WHERE id = $2", status, row["id"])
    return row["id"]


async def _audited(pool, action: str, run_id) -> int:
    return await pool.fetchval(
        "SELECT count(*) FROM audit_log WHERE action = $1 AND target_ref = $2",
        action, f"pilot_run:{run_id}",
    )


async def test_a_run_the_process_lost_is_cancelled_as_failed_and_audited(pool, system_pool):
    """The database says running, no thread in this process knows the run — it
    restarted, and nothing will ever finish it. Cancelling marks it failed."""
    owner = caller("contributor")
    run_id = await _run(system_pool, (await resolve_actor(pool, owner)).id, "running")

    outcome = await service.cancel_or_delete(pool, owner, run_id, registry=RunRegistry())

    assert outcome == "cancelled"
    row = await system_pool.fetchrow(
        "SELECT status, error_message, completed_at FROM pilot_runs WHERE id = $1", run_id)
    assert row["status"] == "failed"
    assert row["error_message"] and row["completed_at"] is not None
    assert await _audited(pool, ACTION_PILOT_RUN_CANCELLED, run_id) == 1


async def test_a_finished_run_is_deleted_and_the_deletion_audited(pool, system_pool):
    owner = caller("contributor")
    run_id = await _run(system_pool, (await resolve_actor(pool, owner)).id, "failed")

    outcome = await service.cancel_or_delete(pool, owner, run_id, registry=RunRegistry())

    assert outcome == "deleted"
    assert await system_pool.fetchval("SELECT count(*) FROM pilot_runs WHERE id = $1", run_id) == 0
    # Written before the delete, in the same transaction: afterwards there
    # would be no row left to describe.
    assert await _audited(pool, ACTION_PILOT_RUN_DELETED, run_id) == 1


async def test_a_run_with_an_art_50_record_is_refused_and_leaves_no_audit_entry(pool, system_pool):
    """The delete fails on the RESTRICT from migration 015. The audit entry
    written a moment earlier in the same transaction must go with it — an entry
    saying "deleted" for a run that still exists would be a false record."""
    owner = caller("contributor")
    run_id = await _run(system_pool, (await resolve_actor(pool, owner)).id, "complete")
    await pool.execute(
        "INSERT INTO transparency_records (pilot_run_id, model_used, model_disclosure) "
        "VALUES ($1, 'gemma3-4b', 'KI-generiert')",
        run_id,
    )

    with pytest.raises(RunHasTransparencyRecords):
        await service.cancel_or_delete(pool, owner, run_id, registry=RunRegistry())

    assert await system_pool.fetchval("SELECT count(*) FROM pilot_runs WHERE id = $1", run_id) == 1
    assert await _audited(pool, ACTION_PILOT_RUN_DELETED, run_id) == 0


async def test_someone_elses_run_cannot_be_cancelled(pool, system_pool):
    """Until CHY-90 any authenticated caller could delete any run.

    Since CHY-92 the stranger does not even learn that it exists: row-level
    security hides the run before ownership is asked, so the answer is "not
    found" rather than "not yours".
    """
    owner, stranger = caller("contributor"), caller("contributor")
    run_id = await _run(system_pool, (await resolve_actor(pool, owner)).id, "running")

    with pytest.raises(RunNotFound):
        await service.cancel_or_delete(pool, stranger, run_id, registry=RunRegistry())

    assert await system_pool.fetchval("SELECT status FROM pilot_runs WHERE id = $1", run_id) == "running"
    assert await _audited(pool, ACTION_PILOT_RUN_CANCELLED, run_id) == 0


# ── cancelling at the gate (CHY-153) ──────────────────────────────────────────

#: What the banking pilot imports at module level and the integration job does
#: not install. Replaced for the length of one test, as pilots/banking/conftest.py
#: does for the pilot's own tests; nothing here calls a model.
_PILOT_STACK = (
    "dotenv", "langchain_core", "langchain_core.messages", "langchain_openai", "pypdf",
    "langgraph", "langgraph.graph", "langgraph.checkpoint", "langgraph.checkpoint.postgres",
    "langfuse", "langfuse.langchain",
)


class _GateOnly:
    """Stands in for the pilot's StateGraph: one step, and it is the gate.

    Everything before the gate would call a model. The gate itself, and the
    `stream_workflow` that drives it, are the pilot's own.
    """

    def __init__(self, nodes) -> None:
        self._nodes = nodes

    def compile(self, **_kw):
        return self

    def stream(self, state, **_kw):
        yield {"human_oversight": self._nodes.node_human_oversight(dict(state))}


@pytest.fixture
def banking(monkeypatch):
    """The banking pilot's modules, importable without its model stack."""
    before = set(sys.modules)
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "pilots" / "banking"))
    for name in _PILOT_STACK:
        monkeypatch.setitem(sys.modules, name, MagicMock())
    graph = importlib.import_module("banking.graph")
    nodes = importlib.import_module("banking.nodes")
    monkeypatch.setattr(graph, "_build", lambda: _GateOnly(nodes))
    # A gate that is never released ends the test in seconds, not in two hours.
    monkeypatch.setattr(nodes, "OVERSIGHT_TIMEOUT_SECONDS", 30)
    yield nodes
    for name in set(sys.modules) - before:
        if name == "banking" or name.startswith("banking."):
            del sys.modules[name]


async def _until(system_pool, run_id, status: str, timeout: float = 30.0) -> None:
    """Wait for the pilot thread, which writes from its own loop."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while await system_pool.fetchval(
            "SELECT status FROM pilot_runs WHERE id = $1", run_id) != status:
        if loop.time() > deadline:
            pytest.fail(f"the run did not reach {status} within {timeout}s")
        await asyncio.sleep(0.05)


async def test_a_run_cancelled_at_the_gate_ends_as_cancelled_not_as_a_rejection(
        pool, system_pool, banking):
    """Until 07.11.2026 cancelling released the gate with "rejected". The
    thread booked an Art. 14 rejection, the trigger from 014 refused a decision
    without a decider, and the run stayed where it was, without a result —
    awaiting the thread below then raises that trigger's error.

    Now the run ends the way every cancelled pilot run ends: `failed`, with
    the cancellation as its message, and no oversight decision on record.
    """
    owner = caller("contributor")
    run_id = await _run(system_pool, (await resolve_actor(pool, owner)).id, "pending",
                        pilot="banking")
    registry = RunRegistry()
    loop = asyncio.get_running_loop()
    spec = RunSpec(run_id=run_id, pdf_path=Path("it.pdf"), thread_id=f"it-{uuid4().hex}",
                   pilot_name="banking", db_dsn=settings.database_url, model_used="it")
    thread = loop.run_in_executor(None, run_in_thread, spec, loop, registry.open(run_id),
                                  registry, registry.open_oversight_gate(run_id))
    await _until(system_pool, run_id, "awaiting_oversight")

    assert await service.cancel_or_delete(pool, owner, run_id, registry=registry) == "cancelling"
    await asyncio.wait_for(thread, timeout=60)

    row = await system_pool.fetchrow(
        "SELECT status, oversight_pending, oversight_decision, oversight_by, error_message, "
        "       completed_at FROM pilot_runs WHERE id = $1", run_id)
    assert row["status"] == "failed"
    assert row["oversight_decision"] is None and row["oversight_by"] is None
    assert row["oversight_pending"] is False
    assert banking.CANCELLED_AT_GATE in row["error_message"]
    assert "OversightGateError" not in row["error_message"]
    assert row["completed_at"] is not None
    assert await _audited(pool, ACTION_PILOT_RUN_CANCELLED, run_id) == 1
