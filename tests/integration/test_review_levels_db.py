"""
tests/integration/test_review_levels_db.py

How strict a gate must be, on a real PostgreSQL (CHY-223, ADR-050 step 3a).

What only the database shows:

- `risk_class` passes the CHECK of migration 044 and comes back on the definition
- the minimum reads the data class from the scope chain as it is *now*: a
  scope classified stricter after the release makes its gates stricter at
  once, without a new release — and the audit says which class did it
- activation refuses a gate below the minimum, reading the same chain
- the credit committee decides alone under `critical` after the areas
  disagreed (Thomas, 09.11.2026), and does not after a timeout
- a gate with areas that falls below the minimum through a reclassification
  is not decided until it is released again

The competence order and the credit workflow are those of
test_review_areas_db.py. The `scope` fixture declares no data class, so it
counts as critical.
"""
from __future__ import annotations

import json
from datetime import timedelta
from uuid import UUID, uuid4

import asyncpg
import pytest

from contexts.orchestration import conditions
from contexts.orchestration import service as orchestration
from contexts.orchestration.errors import GateBelowMinimum
from contexts.orchestration.schema import OversightDecision, WorkflowCreate, WorkflowRunStart
from tests.integration._callers import caller
from tests.integration.test_review_areas_db import (  # noqa: F401 -- fixture
    _audit,
    _gates,
    _run,
    _vote,
    _workflow,
    order,
)
from tests.integration.test_run_placeholders_db import _engine, people  # noqa: F401 -- fixture

pytestmark = pytest.mark.integration


async def _classify(owner_pool, scope: UUID, data_class: str) -> None:
    await owner_pool.execute(
        "UPDATE scope_nodes SET privacy_level = $2 WHERE id = $1", scope, data_class)


async def _gate_workflow(pool, scope: UUID, *, role: str | None = "approver",
                         risk_class: str | None = None, status: str = "active") -> UUID:
    """One gate, the entry: the smallest workflow a level can be read on."""
    workflow = await pool.fetchval(
        "INSERT INTO workflow_definitions (scope_id, name, status, risk_class) "
        "VALUES ($1, 'it', $2, $3) RETURNING id", scope, status, risk_class)
    await pool.execute(
        "INSERT INTO workflow_nodes (workflow_id, node_ref, node_type, review_role) "
        "VALUES ($1, 'approve', 'human_review', $2)", workflow, role)
    return workflow


async def _start(pool, system_pool, workflow: UUID) -> UUID:
    advance = await orchestration.start_workflow_run(
        pool, caller("contributor"), workflow,
        WorkflowRunStart(input_data={"brief": f"Entwurf {uuid4().hex[:8]}"}),
        engine=_engine(pool, system_pool))
    assert advance.run.status == "awaiting_oversight", advance.run
    return advance.run.id


async def _approve(pool, system_pool, who, run_id: UUID):
    return await orchestration.record_oversight(
        pool, who, run_id, OversightDecision(decision="approved"),
        engine=_engine(pool, system_pool))


async def _reached(system_pool, run_id: UUID) -> list[dict]:
    return [e["payload"] for e in
            await _audit(system_pool, "workflow.run_gate_reached", f"workflow_run:{run_id}")]


# ── the definition ────────────────────────────────────────────────────────────

async def test_a_workflow_keeps_its_risk_class(pool, scope):
    created = await orchestration.create_workflow(
        pool, caller("contributor"),
        WorkflowCreate(scope_id=scope, name="kredit", risk_class="high"))
    assert created.risk_class == "high"
    assert await pool.fetchval(
        "SELECT risk_class FROM workflow_definitions WHERE id = $1", created.id) == "high"


async def test_the_database_refuses_an_unknown_risk_class(pool, scope):
    """The CHECK of migration 044 — for a row that does not come through the API."""
    with pytest.raises(asyncpg.CheckViolationError):
        await _gate_workflow(pool, scope, risk_class="medium")


# ── activation ────────────────────────────────────────────────────────────────

async def _rules(pool, workflow: UUID) -> dict[str, str | None]:
    out = await orchestration.validate_workflow(
        pool, caller("consumer"), workflow,
        limits=orchestration.DocumentLimits(max_bytes=10 * 1024 * 1024, max_pages=200))
    return {v.rule: v.node_ref for v in out.violations}


async def test_activation_refuses_one_approver_in_an_unclassified_scope(pool, scope):
    workflow = await _gate_workflow(pool, scope, risk_class="minimal", status="draft")
    assert (await _rules(pool, workflow)).get("review_below_minimum") == "approve"


async def test_classified_low_one_approver_passes(pool, owner_pool, scope):
    """Counter-probe: the rule reads the chain, not a constant."""
    await _classify(owner_pool, scope, "internal")
    workflow = await _gate_workflow(pool, scope, risk_class="limited", status="draft")
    assert "review_below_minimum" not in await _rules(pool, workflow)


async def test_a_workflow_nobody_classified_is_refused_in_a_low_scope_too(
        pool, owner_pool, scope):
    await _classify(owner_pool, scope, "internal")
    workflow = await _gate_workflow(pool, scope, risk_class=None, status="draft")
    assert (await _rules(pool, workflow)).get("review_below_minimum") == "approve"


# ── at run time ───────────────────────────────────────────────────────────────

async def test_a_scope_classified_stricter_after_the_release_asks_for_more(
        pool, system_pool, owner_pool, scope, people):  # noqa: F811
    """Released with one approver in an internal scope; then the scope is
    classified critical. The next run asks for four eyes — no new release."""
    await _classify(owner_pool, scope, "internal")
    workflow = await _gate_workflow(pool, scope, risk_class="limited")
    await _classify(owner_pool, scope, "critical")

    run_id = await _start(pool, system_pool, workflow)
    assert (await _approve(pool, system_pool, people.reviewer, run_id)).run.status == (
        "awaiting_oversight")

    [reached] = await _reached(system_pool, run_id)
    assert reached == {"node_ref": "approve", "level": "four_eyes", "gate": "approver",
                       "minimum": "four_eyes", "because": ["data_class"],
                       "data_class": "critical", "risk_class": "limited", "areas": [],
                       "after_differing_votes": False}
    assert await system_pool.fetchval(
        "SELECT review_level FROM node_executions WHERE run_id = $1", run_id) == "four_eyes"


async def test_the_credit_committee_decides_alone_under_critical(pool, system_pool, order):  # noqa: F811
    """Unclassified scope, unclassified workflow: the minimum is four eyes.
    The two areas meet it; the committee after their differing votes decides
    with one (Thomas, 09.11.2026)."""
    run_id = await _run(pool, system_pool, order.scope)
    await _vote(pool, system_pool, order.markt, run_id, "approved")
    await _vote(pool, system_pool, order.marktfolge, run_id, "rejected")

    out = await _vote(pool, system_pool, order.kommission, run_id, "approved")

    assert out.run.status == "complete"
    votum, kommission = await _reached(system_pool, run_id)
    assert (votum["level"], votum["after_differing_votes"]) == ("four_eyes", False)
    assert (kommission["level"], kommission["minimum"], kommission["after_differing_votes"]) == (
        "approver", "four_eyes", True)


async def test_not_after_a_timeout(pool, system_pool, owner_pool, order):  # noqa: F811
    """The same committee, reached because nobody voted in time: nobody looked
    before it, so it would need four eyes — which one area cannot give. Not
    decided; activation refuses this definition in the first place."""
    run_id = await _run(pool, system_pool, order.scope)
    workflow = await system_pool.fetchval(
        "SELECT workflow_id FROM workflow_runs WHERE id = $1", run_id)
    await owner_pool.execute(
        "UPDATE workflow_nodes SET review_timeout = $2, on_timeout = 'escalate' "
        "WHERE workflow_id = $1 AND node_ref = 'votum'", workflow, timedelta(days=1))
    await owner_pool.execute(
        "UPDATE node_executions SET started_at = now() - interval '2 days' "
        "WHERE run_id = $1 AND status = 'running'", run_id)
    from cli.oversight import expire
    from tests.integration.conftest import _database_url
    counts = await expire(_database_url(), dry_run=False)
    assert counts["gates_escalated"] >= 1
    assert (await _gates(system_pool, run_id))["kommission"]["status"] == "running"

    with pytest.raises(GateBelowMinimum):
        await _vote(pool, system_pool, order.kommission, run_id, "approved")
    [_, kommission] = await _reached(system_pool, run_id)
    assert (kommission["level"], kommission["after_differing_votes"]) == ("four_eyes", False)


async def test_a_gate_with_areas_below_a_new_minimum_is_not_decided(
        pool, system_pool, owner_pool, order):  # noqa: F811
    """One area, released in an internal scope with a limited workflow; then the
    scope turns critical. Adding a vote nobody drew would decide who decides —
    so it waits for a stricter release, or a stop, or its expiry."""
    await _classify(owner_pool, order.scope, "internal")
    workflow = await pool.fetchval(
        "INSERT INTO workflow_definitions (scope_id, name, status, risk_class) "
        "VALUES ($1, 'it', 'active', 'limited') RETURNING id", order.scope)
    await pool.execute(
        "INSERT INTO workflow_nodes (workflow_id, node_ref, node_type, review_areas) "
        "VALUES ($1, 'kommission', 'human_review', $2)", workflow, ["kreditkommission"])
    await _classify(owner_pool, order.scope, "critical")
    run_id = await _start(pool, system_pool, workflow)

    with pytest.raises(GateBelowMinimum):
        await _vote(pool, system_pool, order.kommission, run_id, "approved")
    assert (await _gates(system_pool, run_id))["kommission"]["status"] == "running"


async def test_the_escalation_edge_of_the_credit_workflow_is_unchanged(pool, scope):
    """The credit workflow of the area tests, read back: the committee hangs on
    an escalation edge, which is what excepts it."""
    workflow = await _workflow(pool, scope, status="draft")
    edge = await pool.fetchval(
        "SELECT condition::text FROM workflow_connections WHERE workflow_id = $1", workflow)
    assert json.loads(edge) == dict(conditions.ESCALATION)
