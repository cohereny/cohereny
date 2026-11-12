"""
tests/integration/test_direct_call_risk_db.py

The risk class of direct model calls, on a real PostgreSQL (CHY-199, ADR-050).

What the unit tests cannot show: that migration 047 and the chain query agree.
The newest setting of a scope applies, also for two settings in one
transaction; the strictest applies along the chain; the application's role
can add a setting and cannot change or remove one. The grants themselves are
held against the inventory in test_app_role_db.py like every table's.
"""
from __future__ import annotations

from decimal import Decimal
from uuid import UUID, uuid4

import asyncpg
import pytest

from kernel import scopes
from kernel.errors import ScopeLoosensRiskClass
from kernel.identity import resolve_actor
from tests.integration._callers import caller

pytestmark = pytest.mark.integration

ARCHITECT = caller("architect")
#: minimal comes with a share since migration 048 (CHY-269).
SHARE = Decimal("0.1")


async def _child(pool, parent: UUID) -> UUID:
    child = uuid4()
    path = await pool.fetchval("SELECT scope_path::text FROM scope_nodes WHERE id = $1", parent)
    await pool.execute(
        "INSERT INTO scope_nodes (id, scope_path, node_type, name, parent_id) "
        "VALUES ($1, $2::ltree, 'project', 'it', $3)",
        child, f"{path}.project_{child.hex}", parent,
    )
    return child


async def test_the_newest_setting_applies(pool, scope):
    for risk_class in ("high", "limited", "minimal"):
        await scopes.set_direct_call_risk_class(
            pool, ARCHITECT, scope, risk_class, SHARE if risk_class == "minimal" else None)
    risk = await scopes.direct_call_risk_of(pool, scope)
    assert (risk.own, risk.effective) == ("minimal", "minimal")


async def test_two_settings_in_one_transaction_keep_their_order(pool, scope):
    """clock_timestamp(), not now(): within a transaction now() stands still,
    and the two rows would tie."""
    setter = await resolve_actor(pool, ARCHITECT)
    async with pool.acquire() as conn, conn.transaction():
        for risk_class in ("high", "limited"):
            await conn.execute(
                "INSERT INTO direct_call_risk_classes (scope_id, risk_class, set_by) "
                "VALUES ($1, $2, $3)", scope, risk_class, setter.id)
    assert (await scopes.direct_call_risk_of(pool, scope)).own == "limited"


async def test_the_strictest_along_the_chain_applies_and_none_inherits(pool, scope):
    child = await _child(pool, scope)
    await scopes.set_direct_call_risk_class(pool, ARCHITECT, scope, "limited")
    await scopes.set_direct_call_risk_class(pool, ARCHITECT, child, "high")
    assert (await scopes.direct_call_risk_of(pool, child)).effective == "high"

    await scopes.set_direct_call_risk_class(pool, ARCHITECT, child, None)
    risk = await scopes.direct_call_risk_of(pool, child)
    assert (risk.own, risk.effective) == (None, "limited")


async def test_a_looser_class_below_is_refused_and_nothing_is_written(pool, scope):
    child = await _child(pool, scope)
    await scopes.set_direct_call_risk_class(pool, ARCHITECT, scope, "limited")
    with pytest.raises(ScopeLoosensRiskClass):
        await scopes.set_direct_call_risk_class(pool, ARCHITECT, child, "minimal", SHARE)
    assert await pool.fetchval(
        "SELECT count(*) FROM direct_call_risk_classes WHERE scope_id = $1", child) == 0


async def test_every_setting_is_audited(pool, scope):
    await scopes.set_direct_call_risk_class(pool, ARCHITECT, scope, "minimal", SHARE)
    row = await pool.fetchrow(
        "SELECT payload->>'from' AS before, payload->>'to' AS after, "
        "       payload->>'effective' AS effective "
        "FROM audit_log WHERE action = 'scope.direct_call_risk_class_set' AND target_ref = $1",
        f"scope:{scope}")
    assert (row["before"], row["after"], row["effective"]) == (None, "minimal", "minimal")


@pytest.mark.parametrize("statement", [
    "UPDATE direct_call_risk_classes SET risk_class = 'minimal' WHERE scope_id = $1",
    "DELETE FROM direct_call_risk_classes WHERE scope_id = $1",
])
async def test_a_setting_cannot_be_changed_or_removed(pool, scope, statement):
    """Append-only for the application: how a scope came to its class stays."""
    await scopes.set_direct_call_risk_class(pool, ARCHITECT, scope, "high")
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await pool.execute(statement, scope)
