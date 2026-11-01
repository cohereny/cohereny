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

What only a real database can show, and the unit tests cannot:
  - that a refused delete leaves no audit entry behind. The service writes the
    entry and deletes in one transaction; with a mock the "rollback" is whatever
    the mock does, here it is PostgreSQL's
  - that the RESTRICT from migration 015 reaches the service as the exception
    it translates, not as some other class that would leave as a 500
"""
from __future__ import annotations

from uuid import uuid4

import pytest

from contexts.pilots import service, store
from contexts.pilots.errors import RunHasTransparencyRecords, RunNotOwned
from contexts.pilots.registry import RunRegistry
from kernel.audit import ACTION_PILOT_RUN_CANCELLED, ACTION_PILOT_RUN_DELETED
from kernel.identity import resolve_actor
from tests.integration._callers import caller

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


async def _run(pool, owner_id, status: str):
    """A run as start_run records it, then moved to `status`."""
    async with pool.acquire() as conn:
        row = await store.insert_run(
            conn, thread_id=f"it-{uuid4().hex}", pilot_name="study",
            input_file="it.pdf", actor_id=owner_id, run_config={}, effective_config={},
        )
    await pool.execute("UPDATE pilot_runs SET status = $1 WHERE id = $2", status, row["id"])
    return row["id"]


async def _audited(pool, action: str, run_id) -> int:
    return await pool.fetchval(
        "SELECT count(*) FROM audit_log WHERE action = $1 AND target_ref = $2",
        action, f"pilot_run:{run_id}",
    )


async def test_a_run_the_process_lost_is_cancelled_as_failed_and_audited(pool):
    """The database says running, no thread in this process knows the run — it
    restarted, and nothing will ever finish it. Cancelling marks it failed."""
    owner = caller("contributor")
    run_id = await _run(pool, (await resolve_actor(pool, owner)).id, "running")

    outcome = await service.cancel_or_delete(pool, owner, run_id, registry=RunRegistry())

    assert outcome == "cancelled"
    row = await pool.fetchrow(
        "SELECT status, error_message, completed_at FROM pilot_runs WHERE id = $1", run_id)
    assert row["status"] == "failed"
    assert row["error_message"] and row["completed_at"] is not None
    assert await _audited(pool, ACTION_PILOT_RUN_CANCELLED, run_id) == 1


async def test_a_finished_run_is_deleted_and_the_deletion_audited(pool):
    owner = caller("contributor")
    run_id = await _run(pool, (await resolve_actor(pool, owner)).id, "failed")

    outcome = await service.cancel_or_delete(pool, owner, run_id, registry=RunRegistry())

    assert outcome == "deleted"
    assert await pool.fetchval("SELECT count(*) FROM pilot_runs WHERE id = $1", run_id) == 0
    # Written before the delete, in the same transaction: afterwards there
    # would be no row left to describe.
    assert await _audited(pool, ACTION_PILOT_RUN_DELETED, run_id) == 1


async def test_a_run_with_an_art_50_record_is_refused_and_leaves_no_audit_entry(pool):
    """The delete fails on the RESTRICT from migration 015. The audit entry
    written a moment earlier in the same transaction must go with it — an entry
    saying "deleted" for a run that still exists would be a false record."""
    owner = caller("contributor")
    run_id = await _run(pool, (await resolve_actor(pool, owner)).id, "complete")
    await pool.execute(
        "INSERT INTO transparency_records (pilot_run_id, model_used, model_disclosure) "
        "VALUES ($1, 'gemma3-4b', 'KI-generiert')",
        run_id,
    )

    with pytest.raises(RunHasTransparencyRecords):
        await service.cancel_or_delete(pool, owner, run_id, registry=RunRegistry())

    assert await pool.fetchval("SELECT count(*) FROM pilot_runs WHERE id = $1", run_id) == 1
    assert await _audited(pool, ACTION_PILOT_RUN_DELETED, run_id) == 0


async def test_someone_elses_run_cannot_be_cancelled(pool):
    """Until CHY-90 any authenticated caller could delete any run."""
    owner, stranger = caller("contributor"), caller("contributor")
    run_id = await _run(pool, (await resolve_actor(pool, owner)).id, "running")

    with pytest.raises(RunNotOwned):
        await service.cancel_or_delete(pool, stranger, run_id, registry=RunRegistry())

    assert await pool.fetchval("SELECT status FROM pilot_runs WHERE id = $1", run_id) == "running"
    assert await _audited(pool, ACTION_PILOT_RUN_CANCELLED, run_id) == 0
