"""
tests/integration/test_jsonb_lands_as_objects.py

What a write path stores, asked of the database (CHY-164).

The double encoding was invisible from Python: every reader decoded a string a
second time and got its dict back. Only `jsonb_typeof` — or a CHECK that looks
inside — tells an object from a string that contains one. So each path is
driven through the code the API uses, and the stored type is read back in SQL.

Plus the opposite failure on a bare connection: `set_role` bootstraps the first
admin over `asyncpg.connect` without a codec, and a dict payload there raised
a DataError before CHY-164. After a rebuild from zero (CHY-161) that is the
first thing anyone runs.
"""
from __future__ import annotations

from uuid import uuid4

import pytest

from api.config import settings
from contexts.orchestration import store as orchestration
from contexts.orchestration.schema import ConnectionCreate, ContextRequirements, NodeCreate
from contexts.repository import service as repository
from contexts.repository.schema import BlockCreate
from kernel.audit import target_ref, write_audit
from kernel.identity import resolve_actor, set_role
from tests.integration._callers import caller as _caller

pytestmark = pytest.mark.integration


async def _type(pool, sql: str, *args) -> str:
    return await pool.fetchval(sql, *args)


async def test_block_metadata_is_an_object(pool, redis, scope):
    out = await repository.create_block(pool, redis, _caller("architect"), BlockCreate(
        scope_id=scope, block_type="system_prompt", content="x", metadata={"lang": "de"},
    ))
    assert await _type(
        pool, "SELECT jsonb_typeof(metadata) FROM context_blocks WHERE block_id = $1",
        out.block_id,
    ) == "object"
    assert await pool.fetchval(
        "SELECT metadata->>'lang' FROM context_blocks WHERE block_id = $1", out.block_id
    ) == "de", "SQL that reads into metadata got nothing back"


async def test_node_config_and_requirements_are_objects(pool, workflow):
    """The case that refused a valid node before CHY-164."""
    async with pool.acquire() as conn:
        await orchestration.insert_node(conn, workflow, NodeCreate(
            node_ref="compose", node_type="context_compose", config={"note": "x"},
            context_requirements=ContextRequirements(block_types=["system_prompt"]),
        ))
    row = await pool.fetchrow(
        "SELECT jsonb_typeof(config) c, jsonb_typeof(context_requirements) r, "
        "       jsonb_typeof(input_mapping) i FROM workflow_nodes "
        "WHERE workflow_id = $1 AND node_ref = 'compose'", workflow,
    )
    assert (row["c"], row["r"], row["i"]) == ("object", "object", "object")


async def test_an_edge_condition_is_an_object(pool, workflow):
    async with pool.acquire() as conn:
        a = await orchestration.insert_node(conn, workflow, NodeCreate(node_ref="a", node_type="tool_call"))
        b = await orchestration.insert_node(conn, workflow, NodeCreate(node_ref="b", node_type="tool_call"))
        edge = await orchestration.insert_connection(
            conn, workflow, source_id=a["id"], target_id=b["id"],
            body=ConnectionCreate(source_node_ref="a", target_node_ref="b",
                                  condition={"op": "eq", "field": "risk", "value": "high"}),
        )
    assert await _type(
        pool, "SELECT jsonb_typeof(condition) FROM workflow_connections WHERE id = $1",
        edge["id"],
    ) == "object"


async def test_an_audit_payload_is_an_object(pool):
    actor = await resolve_actor(pool, _caller("admin"))
    entry = await write_audit(pool, action="it.payload", actor_id=actor.id,
                              target=target_ref("it", uuid4()), payload={"k": "v"})
    assert await _type(
        pool, "SELECT jsonb_typeof(payload) FROM audit_log WHERE id = $1", entry
    ) == "object"


async def test_the_bootstrap_path_writes_its_audit_entry_on_a_bare_connection(pool):
    """`set_role` connects without the codec. With a dict payload that raised
    DataError — the first admin of a fresh installation could not be created."""
    sub = f"it-bootstrap-{uuid4().hex}"
    actor_id, created = await set_role(settings.database_url, sub, "admin")
    assert created
    entries = await pool.fetch(
        "SELECT jsonb_typeof(payload) AS t FROM audit_log WHERE actor_id = $1", actor_id
    )
    assert entries, "set_role wrote no audit entry"
    assert {e["t"] for e in entries} == {"object"}
