"""
tests/integration/test_retention_db.py

Retention periods on a real database (CHY-17, ADR-046).

tests/test_retention.py reads the migrations as text. What only PostgreSQL
shows:

  - every table that exists after all migrations is in the inventory — the
    database, not a regex over SQL, is the last word on what is there
  - migration 030 ships the defaults, and NULLS NOT DISTINCT really allows one
    installation-wide row per class
  - the CHECKs refuse a class without a period and a period below one day
  - a period set on a scope applies below it, removing it restores the
    inherited one, and both changes are audited with before and after

No test changes the installation-wide rows: this database is shared by every
integration test.
"""
from __future__ import annotations

from uuid import UUID, uuid4

import asyncpg
import pytest

from kernel import retention
from kernel.errors import RetentionOverrideNotFound, ScopeNotFound
from kernel.schema import RetentionPolicySet, scope_label
from tests.integration._callers import caller

pytestmark = pytest.mark.integration


async def _child(pool, parent: UUID, node_type: str) -> UUID:
    scope_id = uuid4()
    parent_path = await pool.fetchval(
        "SELECT scope_path::text FROM scope_nodes WHERE id = $1", parent)
    await pool.execute(
        "INSERT INTO scope_nodes (id, scope_path, node_type, name, parent_id) "
        "VALUES ($1, $2::ltree, $3, 'it', $4)",
        scope_id, f"{parent_path}.{scope_label(node_type, scope_id)}", node_type, parent,
    )
    return scope_id


def _class(overview, name: str):
    return next(c for c in overview.classes if c.retention_class == name)


async def test_every_table_in_the_database_is_in_the_inventory(pool):
    tables = {r["table_name"] for r in await pool.fetch(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'public' AND table_type = 'BASE TABLE'")}
    unlisted = tables - set(retention.INVENTORY) - retention.EXTERNAL_TABLES
    assert unlisted == set(), "tables without a retention class"


async def test_the_defaults_are_shipped(pool):
    rows = await pool.fetch(
        "SELECT retention_class, keep_days FROM retention_policies WHERE scope_id IS NULL")
    assert {r["retention_class"]: r["keep_days"] for r in rows} == {
        "run_content": 30, "run_trace": 183}


async def test_one_installation_wide_row_per_class(pool):
    with pytest.raises(asyncpg.exceptions.UniqueViolationError):
        await pool.execute(
            "INSERT INTO retention_policies (retention_class, scope_id, keep_days) "
            "VALUES ('run_content', NULL, 1)")


@pytest.mark.parametrize("retention_class, keep_days", [("audit", 365), ("run_content", 0)])
async def test_the_checks_refuse(pool, scope, retention_class, keep_days):
    with pytest.raises(asyncpg.exceptions.CheckViolationError):
        await pool.execute(
            "INSERT INTO retention_policies (retention_class, scope_id, keep_days) "
            "VALUES ($1, $2, $3)", retention_class, scope, keep_days)


async def test_a_scope_period_applies_below_and_its_removal_restores_the_inherited_one(
        pool, scope):
    team = await _child(pool, scope, "team")
    project = await _child(pool, team, "project")
    admin = caller("admin")

    await retention.set_policy(pool, admin, "run_content",
                               RetentionPolicySet(scope_id=team, keep_days=3650))
    seen = _class(await retention.overview(pool, admin, project), "run_content")
    assert (seen.effective_keep_days, seen.effective_from_scope) == (3650, team)

    await retention.remove_override(pool, admin, "run_content", team)
    seen = _class(await retention.overview(pool, admin, project), "run_content")
    assert (seen.effective_keep_days, seen.effective_from_scope) == (30, None)

    entries = await pool.fetch(
        "SELECT payload FROM audit_log WHERE action = 'retention.policy_changed' "
        "AND payload->>'scope_id' = $1 ORDER BY created_at, id", str(team))
    assert [(e["payload"]["from_keep_days"], e["payload"].get("to_keep_days"),
             e["payload"].get("removed", False)) for e in entries] == [
        (None, 3650, False), (3650, None, True)]


async def test_changing_a_period_twice_records_the_first_value(pool, scope):
    admin = caller("admin")
    await retention.set_policy(pool, admin, "run_trace",
                               RetentionPolicySet(scope_id=scope, keep_days=400))
    await retention.set_policy(pool, admin, "run_trace",
                               RetentionPolicySet(scope_id=scope, keep_days=None))
    rows = await pool.fetch(
        "SELECT keep_days FROM retention_policies "
        "WHERE retention_class = 'run_trace' AND scope_id = $1", scope)
    assert [r["keep_days"] for r in rows] == [None], "upserted, not duplicated"
    last = await pool.fetchval(
        "SELECT payload FROM audit_log WHERE action = 'retention.policy_changed' "
        "AND payload->>'scope_id' = $1 ORDER BY created_at DESC, id DESC LIMIT 1", str(scope))
    assert (last["from_keep_days"], last["to_keep_days"]) == (400, None)


async def test_an_unknown_scope_and_a_missing_override_are_refused(pool, scope):
    admin = caller("admin")
    with pytest.raises(ScopeNotFound):
        await retention.set_policy(pool, admin, "run_content",
                                   RetentionPolicySet(scope_id=uuid4(), keep_days=10))
    with pytest.raises(RetentionOverrideNotFound):
        await retention.remove_override(pool, admin, "run_content", scope)
