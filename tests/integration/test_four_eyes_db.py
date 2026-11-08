"""
tests/integration/test_four_eyes_db.py

Four eyes where data changes place, on a real database (CHY-163, ADR-045).

tests/test_four_eyes.py hands the rule its audit entries. What only PostgreSQL
can show is that those entries are really written — `block.created` and
`block.edited` did not exist until this change, and the rule reads nothing
else — and that migration 029 marks technical accounts:

  - a reviewer who edited another person's draft cannot approve it; a third
    person can, and the approval records how four eyes was satisfied
  - a technical account, marked by its group on first sight, approves no
    data-flow block
  - in a personal scope a cloud rule waits in review instead of going live

The single operator is not tested here: this database is shared by every
integration test, so the number of people who may approve is never one.
tests/test_four_eyes.py covers it.
"""
from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from api.v1._activation import HOOKS
from contexts.repository import service as repository
from contexts.repository.errors import SecondPersonRequired, ServiceAccountMayNotApprove
from contexts.repository.schema import BlockCreate, BlockPatch
from kernel.audit import ACTION_BLOCK_APPROVED
from kernel.identity import resolve_actor
from kernel.schema import scope_label
from tests.integration._callers import Caller, caller

pytestmark = pytest.mark.integration

_CLOUD, _LOCAL = "gemma4-cloud", "gemma4-e4b"


async def _public_org(pool) -> UUID:
    """A root scope classified `public`, so a hosted model may serve it at all."""
    scope_id = uuid4()
    await pool.execute(
        "INSERT INTO scope_nodes (id, scope_path, node_type, name, privacy_level) "
        "VALUES ($1, $2::ltree, 'org', 'it', 'public')",
        scope_id, f"org_{scope_id.hex}",
    )
    return scope_id


async def _personal_below(pool, parent: UUID) -> UUID:
    scope_id = uuid4()
    parent_path = await pool.fetchval(
        "SELECT scope_path::text FROM scope_nodes WHERE id = $1", parent)
    await pool.execute(
        "INSERT INTO scope_nodes (id, scope_path, node_type, name, parent_id) "
        "VALUES ($1, $2::ltree, 'personal', 'it', $3)",
        scope_id, f"{parent_path}.{scope_label('personal', scope_id)}", parent,
    )
    return scope_id


def _rule(scope_id: UUID, *candidates: str) -> BlockCreate:
    return BlockCreate(scope_id=scope_id, block_type="routing_rule", content="so entschieden",
                       metadata={"task_type": f"it-{uuid4().hex[:8]}",
                                 "candidates": list(candidates), "max_tokens": 256})


async def _actions(pool, block_id: UUID) -> list[str]:
    rows = await pool.fetch(
        "SELECT action FROM audit_log WHERE target_ref = $1 ORDER BY created_at, id",
        f"block:{block_id}")
    return [r["action"] for r in rows]


async def test_whoever_edited_the_draft_cannot_approve_it_and_a_third_person_can(pool, redis):
    org = await _public_org(pool)
    author, editor, third = caller("contributor"), caller("reviewer"), caller("reviewer")

    created = await repository.create_block(pool, redis, author, _rule(org, _LOCAL), hooks=HOOKS)
    rule = await pool.fetchval(
        "SELECT metadata FROM context_blocks WHERE block_id = $1", created.block_id)
    edited, _ = await repository.patch_block(
        pool, redis, editor, created.block_id,
        BlockPatch(metadata={**rule, "candidates": [_CLOUD, _LOCAL]}), hooks=HOOKS)
    assert edited.status == "draft"
    await repository.transition(pool, redis, author, created.block_id, "submit",
                                expected_version=1)

    with pytest.raises(SecondPersonRequired):
        await repository.approve(pool, redis, editor, created.block_id,
                                 expected_version=1, hooks=HOOKS)
    approval = await repository.approve(pool, redis, third, created.block_id,
                                        expected_version=1, hooks=HOOKS)

    assert approval.block.status == "active"
    assert approval.four_eyes is repository.Verdict.SECOND_PERSON
    assert await _actions(pool, created.block_id) == [
        "block.created", "block.edited", "block.submitted", "block.approved"]
    recorded = await pool.fetchval(
        "SELECT payload->>'four_eyes' FROM audit_log WHERE target_ref = $1 AND action = $2",
        f"block:{created.block_id}", ACTION_BLOCK_APPROVED)
    assert recorded == "second_person"


async def test_a_technical_account_is_marked_by_its_group_and_approves_no_cloud_rule(
        pool, redis):
    ci = Caller(sub=f"it-ci-{uuid4().hex}", username="it ci",
                groups=["chy-architects", "chy-service-accounts"])
    marked = await resolve_actor(pool, ci)
    assert marked.is_service_account
    assert await pool.fetchval(
        "SELECT is_service_account FROM actors WHERE id = $1", marked.id) is True
    person = await resolve_actor(pool, caller("reviewer"))
    assert await pool.fetchval(
        "SELECT is_service_account FROM actors WHERE id = $1", person.id) is False

    org = await _public_org(pool)
    author = caller("contributor")
    created = await repository.create_block(
        pool, redis, author, _rule(org, _CLOUD, _LOCAL), hooks=HOOKS)
    await repository.transition(pool, redis, author, created.block_id, "submit",
                                expected_version=1)
    with pytest.raises(ServiceAccountMayNotApprove):
        await repository.approve(pool, redis, ci, created.block_id,
                                 expected_version=1, hooks=HOOKS)
    status = await pool.fetchval(
        "SELECT status FROM context_blocks WHERE block_id = $1", created.block_id)
    assert status == "pending_review"


async def test_a_cloud_rule_in_a_personal_scope_waits_in_review(pool, redis):
    personal = await _personal_below(pool, await _public_org(pool))
    writer = caller("contributor")

    out = await repository.create_block(
        pool, redis, writer, _rule(personal, _CLOUD, _LOCAL), hooks=HOOKS)

    assert out.status == "pending_review"
    assert await _actions(pool, out.block_id) == ["block.created", "block.submitted"]


async def test_a_local_rule_in_a_personal_scope_is_still_live_at_once(pool, redis):
    """Counter-probe on the database: ADR-042 point 2 holds for everything else."""
    personal = await _personal_below(pool, await _public_org(pool))

    out = await repository.create_block(
        pool, redis, caller("contributor"), _rule(personal, _LOCAL), hooks=HOOKS)

    assert out.status == "active"
