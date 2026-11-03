"""
tests/integration/test_review_votes_db.py

Four eyes are two people, and the database is what says so (CHY-176).

The unit tests in test_workflow_advance.py run the decision path against a
store double. What they cannot show is the rule itself: that the primary key of
`review_votes` (migration 027) refuses a second decision by the same person.
The service counts approvals inside the transaction and trusts that count; if
the table accepted a duplicate, one reviewer approving twice would pass a
four-eyes gate and every unit test would still be green.

Also here: a vote outlives an attempt to delete what it was cast on. It is
evidence that a person looked, and the same stance as transparency_records in
migration 015 applies.
"""
from __future__ import annotations

import asyncpg
import pytest

from contexts.orchestration import store
from contexts.orchestration.errors import ReviewerAlreadyDecided
from contexts.orchestration.schema import NodeCreate
from kernel.identity import resolve_actor
from tests.integration._callers import caller as _caller

pytestmark = pytest.mark.integration


async def _gate_execution(pool, workflow):
    """A four-eyes gate, a run and the execution that sits on it."""
    starter = await resolve_actor(pool, _caller("contributor"))
    gate = NodeCreate.model_construct(
        node_ref="gate", node_type="human_review", name=None, description=None,
        join_policy=None, config={}, context_requirements=None, task_type=None,
        target_workflow_id=None, target_workflow_version=None,
        input_mapping={}, output_mapping={}, review_role="four_eyes",
        review_timeout=None, on_timeout=None,
    )
    async with pool.acquire() as conn:
        node = await store.insert_node(conn, workflow, gate)
        run = await store.insert_run(conn, store.NewRun(
            workflow_id=workflow, workflow_version=1,
            actor_id=starter.id, input_data={},
        ))
        execution = await store.start_execution(conn, store.NewExecution(
            run_id=run["id"], node_id=node["id"], iteration=1,
            consumed=[], input_data={},
        ))
    return node["id"], execution["id"]


async def _reviewer(pool):
    return await resolve_actor(pool, _caller("reviewer"))


async def test_two_people_make_two_approvals(pool, workflow):
    """Counter-probe to the refusal below: different people are counted, or
    the refusal test would pass against a table that refuses everything."""
    node_id, execution_id = await _gate_execution(pool, workflow)
    first, second = await _reviewer(pool), await _reviewer(pool)
    async with pool.acquire() as conn:
        assert await store.review_role(conn, node_id) == "four_eyes"
        await store.record_vote(conn, execution_id, first.id, "approved", None)
        assert await store.approvals(conn, execution_id) == 1
        await store.record_vote(conn, execution_id, second.id, "approved", None)
        assert await store.approvals(conn, execution_id) == 2


async def test_the_same_person_cannot_decide_twice(pool, workflow):
    """The rule the whole feature rests on, enforced by the primary key."""
    _, execution_id = await _gate_execution(pool, workflow)
    only = await _reviewer(pool)
    async with pool.acquire() as conn:
        await store.record_vote(conn, execution_id, only.id, "approved", None)
        with pytest.raises(ReviewerAlreadyDecided):
            await store.record_vote(conn, execution_id, only.id, "approved", "again")
        assert await store.approvals(conn, execution_id) == 1


async def test_a_rejection_does_not_count_as_an_approval(pool, workflow):
    _, execution_id = await _gate_execution(pool, workflow)
    reviewer = await _reviewer(pool)
    async with pool.acquire() as conn:
        await store.record_vote(conn, execution_id, reviewer.id, "rejected", "no")
        assert await store.approvals(conn, execution_id) == 0


async def test_a_vote_is_not_deleted_with_its_execution(pool, workflow):
    """Evidence that a person looked must not disappear as a side effect."""
    _, execution_id = await _gate_execution(pool, workflow)
    reviewer = await _reviewer(pool)
    async with pool.acquire() as conn:
        await store.record_vote(conn, execution_id, reviewer.id, "approved", None)
        with pytest.raises(asyncpg.exceptions.ForeignKeyViolationError):
            await conn.execute("DELETE FROM node_executions WHERE id = $1", execution_id)
