"""
tests/integration/test_high_risk_retention_db.py

A high-risk workflow keeps its logs six months, on a real PostgreSQL (CHY-266).

Art. 26(6) AI Act: the deployer of a high-risk AI system keeps the logs it
generates for at least six months. Decided by Thomas, 10.11.2026: in a scope
with an active workflow classified `high` — or not classified, which counts as
`high` (ADR-050) — neither the run trace nor the run content is kept for less
than 183 days. What only the database shows:

- activation is refused while a period of the scope is too short, and taken
  once both are long enough; a `limited` workflow is not held to it
- a change of a period that would shorten it below six months in such a scope
  is refused — set in the scope, set further up the chain, or removed so a
  shorter one is inherited — and nothing of it is kept
- a scope already under the minimum may lengthen its period step by step, and a
  scope without a high-risk workflow takes any period
"""
from __future__ import annotations

from functools import partial
from uuid import UUID, uuid4

import pytest

from contexts.orchestration import service as orchestration
from contexts.orchestration.schema import WorkflowStatusPut
from kernel import retention
from kernel.errors import HighRiskPeriodTooShort
from kernel.schema import RetentionPolicySet
from tests.integration._callers import caller

pytestmark = pytest.mark.integration

ADMIN = caller("admin")
SIX_MONTHS = retention.HIGH_RISK_MIN_KEEP_DAYS
LIMITS = orchestration.DocumentLimits(max_bytes=10 * 1024 * 1024, max_pages=200)

_set = partial(retention.set_policy, high_risk_scopes=orchestration.high_risk_scopes)
_remove = partial(retention.remove_override, high_risk_scopes=orchestration.high_risk_scopes)


async def _period(pool, scope: UUID, retention_class: str, days: int | None) -> None:
    await _set(pool, ADMIN, retention_class, RetentionPolicySet(scope_id=scope, keep_days=days))


async def _child(pool, parent: UUID) -> UUID:
    child = uuid4()
    path = await pool.fetchval("SELECT scope_path::text FROM scope_nodes WHERE id = $1", parent)
    await pool.execute(
        "INSERT INTO scope_nodes (id, scope_path, node_type, name, parent_id) "
        "VALUES ($1, $2::ltree, 'project', 'it', $3)", child, f"{path}.p_{child.hex}", parent)
    return child


async def _workflow(pool, scope: UUID, risk_class: str | None, *, status: str = "draft") -> UUID:
    """Compose, a model call, a four-eyes gate — the three mandatory types
    (ADR-004); valid in any scope, as an unclassified scope counts as critical,
    which asks for four eyes."""
    workflow = await pool.fetchval(
        "INSERT INTO workflow_definitions (scope_id, name, status, risk_class) "
        "VALUES ($1, 'it', $2, $3) RETURNING id", scope, status, risk_class)
    compose = await pool.fetchval(
        "INSERT INTO workflow_nodes (workflow_id, node_ref, node_type) "
        "VALUES ($1, 'compose', 'context_compose') RETURNING id", workflow)
    draft = await pool.fetchval(
        "INSERT INTO workflow_nodes (workflow_id, node_ref, node_type, task_type) "
        "VALUES ($1, 'draft', 'llm_call', 'draft_reply') RETURNING id", workflow)
    gate = await pool.fetchval(
        "INSERT INTO workflow_nodes (workflow_id, node_ref, node_type, review_role) "
        "VALUES ($1, 'check', 'human_review', 'four_eyes') RETURNING id", workflow)
    for source, target in ((compose, draft), (draft, gate)):
        await pool.execute(
            "INSERT INTO workflow_connections (workflow_id, source_node_id, target_node_id) "
            "VALUES ($1, $2, $3)", workflow, source, target)
    return workflow


async def _activate(pool, workflow: UUID):
    return await orchestration.set_workflow_status(
        pool, caller("reviewer"), workflow, WorkflowStatusPut(status="active"), limits=LIMITS)


# ── activation ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("risk_class", ["high", None])
async def test_a_high_risk_workflow_is_not_activated_where_logs_go_sooner(
        pool, scope, risk_class):
    """The shipped run content period is 30 days: too short until raised."""
    workflow = await _workflow(pool, scope, risk_class)
    with pytest.raises(HighRiskPeriodTooShort) as refused:
        await _activate(pool, workflow)
    assert refused.value.shortfalls == {scope: {"run_content": 30}}
    assert await pool.fetchval(
        "SELECT status FROM workflow_definitions WHERE id = $1", workflow) == "draft"

    await _period(pool, scope, "run_content", SIX_MONTHS)
    assert (await _activate(pool, workflow)).status == "active"


async def test_a_limited_workflow_is_not_held_to_it(pool, scope):
    """Counter-probe: the same scope, the same 30 days, a limited workflow."""
    workflow = await _workflow(pool, scope, "limited")
    assert (await _activate(pool, workflow)).status == "active"


async def test_a_period_inherited_from_above_counts(pool, scope):
    project = await _child(pool, scope)
    await _period(pool, scope, "run_content", 400)
    workflow = await _workflow(pool, project, "high")
    assert (await _activate(pool, workflow)).status == "active"


# ── changing a period ─────────────────────────────────────────────────────────

async def _high_risk_scope(pool, scope: UUID) -> UUID:
    """A scope keeping both six months, with an active high-risk workflow."""
    await _period(pool, scope, "run_content", SIX_MONTHS)
    await _period(pool, scope, "run_trace", SIX_MONTHS)
    await _activate(pool, await _workflow(pool, scope, "high"))
    return scope


@pytest.mark.parametrize("retention_class", ["run_trace", "run_content"])
async def test_shortening_below_six_months_is_refused_and_kept_nowhere(
        pool, scope, retention_class):
    await _high_risk_scope(pool, scope)
    with pytest.raises(HighRiskPeriodTooShort) as refused:
        await _period(pool, scope, retention_class, 90)
    assert refused.value.shortfalls == {scope: {retention_class: 90}}
    assert await pool.fetchval(
        "SELECT keep_days FROM retention_policies WHERE retention_class = $1 AND scope_id = $2",
        retention_class, scope) == SIX_MONTHS


async def test_removing_an_override_that_hands_down_a_shorter_period_is_refused(pool, scope):
    project = await _high_risk_scope(pool, await _child(pool, scope))
    with pytest.raises(HighRiskPeriodTooShort) as refused:
        await _remove(pool, ADMIN, "run_content", project)
    assert refused.value.shortfalls == {project: {"run_content": 30}}
    assert await pool.fetchval(
        "SELECT keep_days FROM retention_policies "
        "WHERE retention_class = 'run_content' AND scope_id = $1", project) == SIX_MONTHS


async def test_a_parent_shortened_below_six_months_is_refused_for_the_child(pool, scope):
    project = await _child(pool, scope)
    await _period(pool, scope, "run_content", 400)
    await _period(pool, scope, "run_trace", 400)
    await _activate(pool, await _workflow(pool, project, "high"))
    with pytest.raises(HighRiskPeriodTooShort) as refused:
        await _period(pool, scope, "run_content", 60)
    assert refused.value.shortfalls == {project: {"run_content": 60}}


async def test_a_scope_under_the_minimum_may_lengthen_step_by_step(pool, scope):
    """An active high-risk workflow from before this rule, in a scope keeping
    30 days: lengthening to 60 is taken, shortening again is refused."""
    await _workflow(pool, scope, "high", status="active")
    await _period(pool, scope, "run_content", 60)
    with pytest.raises(HighRiskPeriodTooShort):
        await _period(pool, scope, "run_content", 20)


async def test_a_scope_without_a_high_risk_workflow_takes_any_period(pool, scope):
    """Counter-probe to the refusals: a limited workflow, a short period."""
    await _activate(pool, await _workflow(pool, scope, "limited"))
    await _period(pool, scope, "run_content", 7)
    await _period(pool, scope, "run_trace", 7)


async def test_an_unclassified_workflow_counts_as_high_risk_for_a_change_too(pool, scope):
    """Not classified counts as high (ADR-050) — on activation and when a period
    changes later; a forgotten classification may not shorten what is kept."""
    await _period(pool, scope, "run_content", SIX_MONTHS)
    await _activate(pool, await _workflow(pool, scope, None))
    with pytest.raises(HighRiskPeriodTooShort):
        await _period(pool, scope, "run_content", 90)
