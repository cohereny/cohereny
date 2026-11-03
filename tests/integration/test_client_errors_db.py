"""
tests/integration/test_client_errors_db.py

The database raises what tests/test_client_errors.py assumes it raises (CHY-165).

The unit tests drive the API with doubles that raise a ForeignKeyViolationError
with `constraint_name` set by the test itself — the same weakness CHY-164
exposed for `NodeNotStorable`. These ask PostgreSQL: which constraint name it
actually sends, and which exception class a NUL actually produces.
"""
from __future__ import annotations

from uuid import uuid4

import asyncpg
import pytest

from contexts.orchestration import store as orchestration
from contexts.orchestration.errors import TargetWorkflowNotFound
from contexts.orchestration.schema import NodeCreate, WorkflowCreate
from contexts.repository import store as repository_store
from contexts.repository.errors import BlockNotStorable
from contexts.repository.schema import BlockCreate
from kernel.errors import ScopeNotFound
from kernel.identity import resolve_actor
from tests.integration._callers import caller

pytestmark = pytest.mark.integration


async def _author(pool):
    return (await resolve_actor(pool, caller("architect"))).id


async def test_a_block_in_an_unknown_scope_raises_scope_not_found(pool):
    with pytest.raises(ScopeNotFound):
        await repository_store.insert_block(pool, BlockCreate(
            scope_id=uuid4(), block_type="system_prompt", content="x"), await _author(pool))


async def test_an_incoherent_claim_raises_block_not_storable(pool, scope):
    body = BlockCreate.model_construct(
        scope_id=scope, block_type="system_prompt", content="x", metadata={}, tags=[],
        governance_key=None, privacy_impact=None, locked=True,
    )
    with pytest.raises(BlockNotStorable) as refused:
        await repository_store.insert_block(pool, body, await _author(pool))
    assert refused.value.constraint == "context_blocks_governance_coherent"


async def test_a_workflow_in_an_unknown_scope_raises_scope_not_found(pool):
    async with pool.acquire() as conn:
        with pytest.raises(ScopeNotFound):
            await orchestration.insert_workflow(
                conn, WorkflowCreate(scope_id=uuid4(), name="w"), await _author(pool))


@pytest.mark.parametrize(("content", "metadata", "raised"), [
    ("x\u0000y", {}, asyncpg.exceptions.CharacterNotInRepertoireError),
    ("x", {"k": "a\u0000b"}, asyncpg.exceptions.UntranslatableCharacterError),
], ids=["text", "jsonb"])
async def test_nul_is_refused_by_postgres_with_the_class_the_handler_maps(
    pool, scope, content, metadata, raised
):
    """The transport maps exactly these two classes to 422 (api/main.py). If
    PostgreSQL ever answered with a third, the handler would miss it."""
    with pytest.raises(raised):
        await repository_store.insert_block(pool, BlockCreate(
            scope_id=scope, block_type="system_prompt", content=content,
            metadata=metadata), await _author(pool))


# ── found on 25.09.2026, on the first run with everything merged ──────────────

async def test_an_incoherent_claim_on_a_draft_edit_raises_block_not_storable(pool, scope):
    """The insert was translated, the in-place edit of a draft was not."""
    row = await repository_store.insert_block(pool, BlockCreate(
        scope_id=scope, block_type="system_prompt", content="x"), await _author(pool))
    with pytest.raises(BlockNotStorable) as refused:
        await repository_store.edit_draft(pool, row["block_id"], {"privacy_impact": "permissive"})
    assert refused.value.constraint == "context_blocks_governance_coherent"


async def test_a_sub_workflow_pointing_nowhere_raises_target_workflow_not_found(pool, workflow):
    body = NodeCreate(node_ref="sub", node_type="sub_workflow",
                      target_workflow_id=uuid4(), target_workflow_version=1)
    async with pool.acquire() as conn:
        with pytest.raises(TargetWorkflowNotFound):
            await orchestration.insert_node(conn, workflow, body)


async def test_a_sub_workflow_pointing_at_a_workflow_is_stored(pool, scope, workflow):
    """Counter-probe: the node above fails only on its target. Pointed at a
    workflow that exists, the same node is written."""
    target = await pool.fetchval(
        "INSERT INTO workflow_definitions (scope_id, name) VALUES ($1, 'target') RETURNING id",
        scope,
    )
    body = NodeCreate(node_ref="sub", node_type="sub_workflow",
                      target_workflow_id=target, target_workflow_version=1)
    async with pool.acquire() as conn:
        row = await orchestration.insert_node(conn, workflow, body)
    assert row["target_workflow_id"] == target
