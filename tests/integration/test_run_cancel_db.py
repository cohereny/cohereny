"""
tests/integration/test_run_cancel_db.py

Stopping a workflow run, on a real PostgreSQL (CHY-200, AI Act Art. 14(4)(e)).

What only the database shows: that `cancelled` passes the CHECKs of migration
038 for the run and for the node, that `completed_at` is stamped, that the
reason is stored nowhere as written, that row-level security decides who may
stop a run before the role is asked -- and that a node still working when the
stop arrives waits for the run's lock and then leaves the run alone.

The workflow, the echo model and the people are those of
test_run_placeholders_db.py: draft → revise → approve.
"""
from __future__ import annotations

import asyncio
import json
from uuid import UUID, uuid4

import pytest

from contexts.orchestration import service as orchestration
from contexts.orchestration.errors import RunAlreadyFinished, RunNotFound
from contexts.orchestration.execution import ExecutorRegistry, NodeContext, NodeResult
from contexts.orchestration.schema import RunCancelRequest, RunResolveRequest, WorkflowRunStart
from api.v1._redaction import mapping_keys
from kernel.errors import AccessDenied
from kernel.identity import resolve_actor
from tests.integration.test_run_placeholders_db import (  # noqa: F401 -- fixtures
    _engine,
    _start,
    _tables_holding,
    _workflow,
    echo,
    people,
)

pytestmark = pytest.mark.integration


async def _at_the_gate(pool, system_pool, scope, echo, starter) -> UUID:  # noqa: F811
    advance = await _start(pool, system_pool, scope, echo,
                           email=f"erika.{uuid4().hex[:8]}@example.de", starter=starter)
    assert advance.run.status == "awaiting_oversight", advance.run
    return advance.run.id


async def _audit(system_pool, action: str, run_id: UUID) -> list[dict]:
    rows = await system_pool.fetch(
        "SELECT actor_id, payload::text AS payload FROM audit_log "
        "WHERE action = $1 AND target_ref = $2", action, f"workflow_run:{run_id}")
    return [{"actor_id": r["actor_id"], "payload": json.loads(r["payload"])} for r in rows]


async def _cancel(pool, system_pool, who, run_id: UUID, reason: str | None = None):
    return await orchestration.cancel_run(pool, who, run_id, RunCancelRequest(reason=reason),
                                          engine=_engine(pool, system_pool))


async def test_at_the_gate_run_and_gate_end_cancelled_and_nobody_decided(
        pool, system_pool, scope, echo, people):  # noqa: F811
    run_id = await _at_the_gate(pool, system_pool, scope, echo, people.starter)

    out = await _cancel(pool, system_pool, people.starter, run_id,
                        reason=f"Falsche Akte, nicht {echo.name}")

    assert out.status == "cancelled"
    run = await system_pool.fetchrow(
        "SELECT status, completed_at FROM workflow_runs WHERE id = $1", run_id)
    assert run["status"] == "cancelled" and run["completed_at"] is not None
    gate = await system_pool.fetchrow(
        "SELECT e.status, e.completed_at FROM node_executions e "
        "JOIN workflow_nodes n ON n.id = e.node_id WHERE e.run_id = $1 AND n.node_ref = 'approve'",
        run_id)
    assert gate["status"] == "cancelled" and gate["completed_at"] is not None
    # Not a decision at the gate: no vote, no Art. 14 record.
    assert await system_pool.fetchval(
        "SELECT count(*) FROM review_votes v JOIN node_executions e ON e.id = v.execution_id "
        "WHERE e.run_id = $1", run_id) == 0
    assert await _audit(system_pool, "workflow.run_oversight_decided", run_id) == []

    entries = await _audit(system_pool, "workflow.run_cancelled", run_id)
    assert [e["payload"] for e in entries] == [
        {"from_status": "awaiting_oversight", "nodes": ["approve"], "started_by_canceller": True}]
    assert entries[0]["actor_id"] == (await resolve_actor(pool, people.starter)).id


async def test_the_reason_is_stored_with_placeholders(
        pool, system_pool, owner_pool, scope, echo, people):  # noqa: F811
    """E7, as at the gate: the whole database is searched for the name, and
    whoever may resolve the run reads the reason as it was written."""
    run_id = await _at_the_gate(pool, system_pool, scope, echo, people.starter)
    reason = f"Falsche Akte, nicht {echo.name}"

    await _cancel(pool, system_pool, people.starter, run_id, reason=reason)

    assert await _tables_holding(owner_pool, echo.name) == {}
    resolved = await orchestration.resolve_run(
        pool, people.starter, run_id, RunResolveRequest(nodes=["approve"]), keys=mapping_keys())
    assert resolved.nodes[0].error_message == reason


async def test_a_reviewer_who_sees_the_run_may_stop_it(
        pool, system_pool, scope, echo, people):  # noqa: F811
    run_id = await _at_the_gate(pool, system_pool, scope, echo, people.starter)

    assert (await _cancel(pool, system_pool, people.reviewer, run_id)).status == "cancelled"
    [entry] = await _audit(system_pool, "workflow.run_cancelled", run_id)
    assert entry["payload"]["started_by_canceller"] is False


async def test_without_the_role_refused_without_seeing_the_run_not_found(
        pool, system_pool, scope, echo, people):  # noqa: F811
    """The colleague sees the run as a member of its scope and is no reviewer;
    the stranger is a reviewer outside the scope and does not learn it exists."""
    run_id = await _at_the_gate(pool, system_pool, scope, echo, people.starter)

    with pytest.raises(AccessDenied):
        await _cancel(pool, system_pool, people.colleague, run_id)
    with pytest.raises(RunNotFound):
        await _cancel(pool, system_pool, people.stranger, run_id)

    assert await system_pool.fetchval(
        "SELECT status FROM workflow_runs WHERE id = $1", run_id) == "awaiting_oversight"
    assert await _audit(system_pool, "workflow.run_cancelled", run_id) == []


async def test_a_run_that_is_over_is_not_stopped_again(
        pool, system_pool, scope, echo, people):  # noqa: F811
    run_id = await _at_the_gate(pool, system_pool, scope, echo, people.starter)
    await _cancel(pool, system_pool, people.starter, run_id)

    with pytest.raises(RunAlreadyFinished):
        await _cancel(pool, system_pool, people.reviewer, run_id)
    assert len(await _audit(system_pool, "workflow.run_cancelled", run_id)) == 1


class _Held:
    """An llm_call that does not answer until it is let go."""

    def __init__(self) -> None:
        self.working = asyncio.Event()
        self.release = asyncio.Event()

    async def __call__(self, ctx: NodeContext) -> NodeResult:
        self.working.set()
        await self.release.wait()
        return NodeResult(output={"content": "zu spät"}, model_used="held")


async def test_a_node_still_working_when_the_stop_arrives_does_not_move_the_run(
        pool, system_pool, scope, people):  # noqa: F811
    """The run is stopped while `draft` is working. When `draft` answers, its
    result is dropped: the execution stays `cancelled`, `revise` never starts,
    and the call that started the run returns it stopped."""
    held = _Held()
    engine = orchestration.Engine(
        registry=ExecutorRegistry({"llm_call": held}), pool=system_pool,
        placeholders=_engine(pool, system_pool).placeholders)
    workflow = await _workflow(pool, scope)
    starting = asyncio.create_task(orchestration.start_workflow_run(
        pool, people.starter, workflow, WorkflowRunStart(input_data={"letter": "Hallo"}),
        engine=engine))
    await asyncio.wait_for(held.working.wait(), timeout=30)
    run_id = await system_pool.fetchval(
        "SELECT id FROM workflow_runs WHERE workflow_id = $1", workflow)

    await _cancel(pool, system_pool, people.starter, run_id)
    held.release.set()
    advance = await asyncio.wait_for(starting, timeout=30)

    assert advance.run.status == "cancelled"
    executions = await system_pool.fetch(
        "SELECT n.node_ref, e.status, e.output_data FROM node_executions e "
        "JOIN workflow_nodes n ON n.id = e.node_id WHERE e.run_id = $1", run_id)
    assert [(e["node_ref"], e["status"], e["output_data"]) for e in executions] == [
        ("draft", "cancelled", None)]
