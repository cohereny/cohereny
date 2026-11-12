"""
tests/integration/test_app_role_db.py

The application's database role on a real PostgreSQL (CHY-85, ADR-009).

What the unit tests cannot show, because only the catalog knows it:

  - the suite really runs as the application role — otherwise every test in
    this directory proves what a superuser may do, which is everything
  - the grants in the database are the ones kernel/retention.py lists
  - the role cannot do what the triggers exist to stop: rewrite the audit log,
    switch a trigger off, replicate around it, create a table to shadow a name
  - setting `cc.superseding` yourself no longer supersedes a block (034), while
    cc_supersede_block still does
  - the check at start refuses the owner of the tables

Each refusal has its counter-probe nearby, in the same file or the trigger tests:
a role that could do nothing at all would pass every refusal here.
"""
from __future__ import annotations

import asyncpg
import pytest

from contexts.repository import service as repository
from contexts.repository.schema import BlockCreate
from kernel import least_privilege
from kernel.retention import INVENTORY
from tests.integration._callers import caller
from tests.integration._redis import Generations

pytestmark = pytest.mark.integration

_PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "DELETE")


async def test_the_suite_runs_as_the_application_role(pool):
    """If this fails, the job connects as more and the other tests prove nothing."""
    async with pool.acquire() as conn:
        await least_privilege.check(conn)


async def test_the_owner_is_refused_at_start(owner_pool):
    """The check against the catalog, not against a dictionary."""
    async with owner_pool.acquire() as conn:
        with pytest.raises(least_privilege.ExcessPrivilegeError, match="owns tables"):
            await least_privilege.check(conn)


@pytest.mark.parametrize("table", sorted(INVENTORY))
async def test_the_grants_are_the_inventory(pool, table):
    """Exactly what the inventory lists — a missing grant fails at run time, an
    extra one holds no line."""
    granted = set()
    for privilege in _PRIVILEGES:
        if await pool.fetchval(
            "SELECT has_table_privilege(current_user, $1, $2)", table, privilege
        ):
            granted.add(privilege)
    assert granted == set(INVENTORY[table].access)


@pytest.mark.parametrize("statement", [
    "UPDATE audit_log SET action = 'rewritten'",
    "DELETE FROM audit_log",
    "TRUNCATE audit_log",
    "DELETE FROM context_blocks",
    "TRUNCATE benchmark_measurements",
    "ALTER TABLE audit_log DISABLE TRIGGER USER",
    "SET session_replication_role = replica",
    "CREATE TABLE cc_probe (id int)",
    "CREATE TEMP TABLE cc_probe (id int)",
], ids=["update-log", "delete-log", "truncate-log", "delete-block", "truncate-evidence",
        "disable-trigger", "replication-role", "create", "create-temp"])
async def test_what_the_triggers_guard_is_out_of_reach(pool, statement):
    """Refused for want of a right, before any trigger is asked. The trigger
    tests show the triggers still refuse the owner; this shows the application
    is not the owner."""
    with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
        await pool.execute(statement)


class TestSupersession:
    """Migration 034: the session setting alone does not count any more."""

    async def _block(self, pool, scope):
        out = await repository.create_block(
            pool, Generations(), caller("architect"),
            BlockCreate(scope_id=scope, block_type="system_prompt", content="Antworte knapp."),
        )
        return out.block_id

    async def test_setting_the_flag_yourself_is_refused(self, pool, scope):
        """Until CHY-85 this passed: any session could set cc.superseding."""
        block_id = await self._block(pool, scope)
        async with pool.acquire() as conn, conn.transaction():
            await conn.execute("SET LOCAL cc.superseding = 'on'")
            with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError,
                               match="cc_supersede_block"):
                await conn.execute(
                    "UPDATE context_blocks SET superseded_by_version = 2 WHERE block_id = $1",
                    block_id,
                )

    async def test_the_function_still_supersedes(self, pool, scope):
        """Counter-probe: SECURITY DEFINER now bridges a real gap — the
        application calls it and the new version exists."""
        block_id = await self._block(pool, scope)
        new = await pool.fetchrow(
            "SELECT version FROM cc_supersede_block($1, 'Antworte ausführlich.')", block_id
        )
        assert new["version"] == 2
        assert await pool.fetchval(
            "SELECT superseded_by_version FROM context_blocks "
            "WHERE block_id = $1 AND version = 1", block_id,
        ) == 2
