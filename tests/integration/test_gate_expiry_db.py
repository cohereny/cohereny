"""
tests/integration/test_gate_expiry_db.py

A gate nobody decides, on a real PostgreSQL (CHY-254, ADR-050).

What only the database shows:

- that `expired` passes the CHECKs of migration 042, for the run and the gate,
  and that `completed_at` is stamped
- that `cc oversight expire` finds a gate by its age — its own time limit, or
  the content period of its scope — and leaves one alone that is not due
- that past the content period the content goes in the same step, every column
  the retention inventory names, the message included, because the purge would
  count the run from its new `completed_at` and keep it a whole period more
- that an escalated run waits at the next gate, claimed there like any other
- that a decision which came first wins over a stale finding
- that the audit entries name no person, because none acted
- that activation reads the time limit and the escalation edge from the rows

Ages are set by moving `started_at` of the gate into the past, as the owner.
The workflows start at their gate: no model is called, nothing here is about
what a step produces.
"""
from __future__ import annotations

import json
from datetime import timedelta
from uuid import UUID, uuid4

import pytest

from cli.oversight import expire
from cli.retention import purge
from contexts.orchestration import conditions
from contexts.orchestration import service as orchestration
from contexts.orchestration import store
from contexts.orchestration.schema import OversightDecision, WorkflowRunStart
from kernel import retention
from tests.integration._callers import caller
from tests.integration.conftest import _database_url
from tests.integration.test_retention_purge_db import _assert_cleared, _scope
from tests.integration.test_run_placeholders_db import _engine, people  # noqa: F401 -- fixture

pytestmark = pytest.mark.integration

_CONTENT_DAYS = 10
DAY = timedelta(days=1)


#: A gate's time limit and what happens when it passes: `review_timeout`, `on_timeout`.
Limit = tuple[timedelta | None, str | None]
NO_LIMIT: Limit = (None, None)


async def _gate(pool, workflow: UUID, ref: str, role: str, limit: Limit = NO_LIMIT) -> UUID:
    timeout, action = limit
    return await pool.fetchval(
        "INSERT INTO workflow_nodes (workflow_id, node_ref, node_type, review_role, "
        "review_timeout, on_timeout) VALUES ($1, $2, 'human_review', $3, $4, $5) RETURNING id",
        workflow, ref, role, timeout, action)


async def _edge(pool, workflow: UUID, source: UUID, target: UUID, *, escalation=False) -> None:
    await pool.execute(
        "INSERT INTO workflow_connections (workflow_id, source_node_id, target_node_id, "
        "condition) VALUES ($1, $2, $3, $4::text::jsonb)",
        workflow, source, target,
        json.dumps(dict(conditions.ESCALATION)) if escalation else None)


async def _workflow(pool, scope: UUID, *, limit: Limit = NO_LIMIT, higher=False,
                    status: str = "active") -> UUID:
    """check [→ escalation → higher]. `check` is the entry and the gate."""
    workflow = await pool.fetchval(
        "INSERT INTO workflow_definitions (scope_id, name, status) VALUES ($1, 'it', $2) "
        "RETURNING id", scope, status)
    check = await _gate(pool, workflow, "check", "approver", limit)
    if higher:
        top = await _gate(pool, workflow, "higher", "four_eyes")
        await _edge(pool, workflow, check, top, escalation=True)
    return workflow


async def _classified(owner_pool, scope: UUID, workflow: UUID) -> UUID:
    """An internal scope and a limited workflow: the minimum is one approver,
    so one approval releases the gate as drawn (CHY-223). Left unclassified,
    scope and workflow would ask for four eyes."""
    await owner_pool.execute(
        "UPDATE scope_nodes SET privacy_level = 'internal' WHERE id = $1", scope)
    await owner_pool.execute(
        "UPDATE workflow_definitions SET risk_class = 'limited' WHERE id = $1", workflow)
    return workflow


async def _at_the_gate(pool, system_pool, workflow: UUID, *, waited: timedelta,
                       owner_pool) -> UUID:
    """A run waiting at `check` for `waited`, with content in its input."""
    advance = await orchestration.start_workflow_run(
        pool, caller("contributor"), workflow,
        WorkflowRunStart(input_data={"letter": f"Akte {uuid4().hex[:8]}"}),
        engine=_engine(pool, system_pool))
    assert advance.run.status == "awaiting_oversight", advance.run
    await owner_pool.execute(
        "UPDATE node_executions SET started_at = now() - $2::interval "
        "WHERE run_id = $1 AND status = 'running'", advance.run.id, waited)
    return advance.run.id


async def _run(pool, run_id: UUID):
    return await pool.fetchrow(
        "SELECT status, completed_at, error_message, input_data, content_purged_at "
        "FROM workflow_runs WHERE id = $1", run_id)


async def _gates(pool, run_id: UUID) -> dict[str, dict]:
    rows = await pool.fetch(
        "SELECT n.node_ref, e.status, e.output_data, e.completed_at FROM node_executions e "
        "JOIN workflow_nodes n ON n.id = e.node_id WHERE e.run_id = $1", run_id)
    return {r["node_ref"]: dict(r) for r in rows}


async def _audit(pool, action: str, run_id: UUID) -> list[dict]:
    rows = await pool.fetch(
        "SELECT actor_id, payload::text AS payload FROM audit_log "
        "WHERE action = $1 AND target_ref = $2", action, f"workflow_run:{run_id}")
    return [{"actor_id": r["actor_id"], "payload": json.loads(r["payload"])} for r in rows]


# ── the time limit ────────────────────────────────────────────────────────────

async def test_past_its_time_limit_a_rejecting_gate_ends_the_run_expired(
        pool, system_pool, owner_pool, scope):
    workflow = await _workflow(pool, scope, limit=(DAY, "reject"))
    run_id = await _at_the_gate(pool, system_pool, workflow, waited=2 * DAY,
                                owner_pool=owner_pool)

    counts = await expire(_database_url(), dry_run=False)

    assert counts["gates_expired"] >= 1
    run = await _run(system_pool, run_id)
    assert run["status"] == "expired" and run["completed_at"] is not None
    assert "time limit" in run["error_message"]
    gate = (await _gates(system_pool, run_id))["check"]
    assert gate["status"] == "expired" and gate["completed_at"] is not None
    # Not a decision: no vote, no Art. 14 record of one.
    assert await system_pool.fetchval(
        "SELECT count(*) FROM review_votes v JOIN node_executions e ON e.id = v.execution_id "
        "WHERE e.run_id = $1", run_id) == 0
    assert await _audit(system_pool, "workflow.run_oversight_decided", run_id) == []
    [entry] = await _audit(system_pool, "workflow.run_expired", run_id)
    assert entry["actor_id"] is None
    assert entry["payload"]["node_ref"] == "check" and entry["payload"]["reason"] == "timeout"
    # The content stays like that of any finished run: its period starts now.
    assert run["content_purged_at"] is None and run["input_data"] != {}


async def test_before_its_time_limit_the_gate_waits(pool, system_pool, owner_pool, scope):
    """Counter-probe: the command acts on age, not on every gate it sees."""
    workflow = await _workflow(pool, scope, limit=(DAY, "reject"))
    run_id = await _at_the_gate(pool, system_pool, workflow, waited=timedelta(hours=1),
                                owner_pool=owner_pool)

    await expire(_database_url(), dry_run=False)

    assert (await _run(system_pool, run_id))["status"] == "awaiting_oversight"
    assert (await _gates(system_pool, run_id))["check"]["status"] == "running"


async def test_a_dry_run_counts_and_changes_nothing(pool, system_pool, owner_pool, scope):
    workflow = await _workflow(pool, scope, limit=(DAY, "reject"))
    run_id = await _at_the_gate(pool, system_pool, workflow, waited=2 * DAY,
                                owner_pool=owner_pool)

    counts = await expire(_database_url(), dry_run=True)

    assert counts["gates_expired"] >= 1
    assert (await _run(system_pool, run_id))["status"] == "awaiting_oversight"
    assert await _audit(system_pool, "workflow.run_expired", run_id) == []


# ── escalation ────────────────────────────────────────────────────────────────

async def test_an_escalating_gate_hands_the_run_to_the_higher_gate(
        pool, system_pool, owner_pool, scope):
    workflow = await _workflow(pool, scope, limit=(DAY, "escalate"), higher=True)
    run_id = await _at_the_gate(pool, system_pool, workflow, waited=2 * DAY,
                                owner_pool=owner_pool)

    counts = await expire(_database_url(), dry_run=False)

    assert counts["gates_escalated"] >= 1
    assert (await _run(system_pool, run_id))["status"] == "awaiting_oversight"
    gates = await _gates(system_pool, run_id)
    assert gates["check"]["status"] == "complete"
    assert gates["check"]["output_data"] == {"decision": "escalated", "reason": "timeout"}
    assert gates["higher"]["status"] == "running", "claimed, so a decision has a row"
    [entry] = await _audit(system_pool, "workflow.run_escalated", run_id)
    assert entry["actor_id"] is None
    assert entry["payload"] == {"node_ref": "check", "reason": "timeout", "to": ["higher"]}


async def test_the_higher_gate_does_not_inherit_the_time_limit(
        pool, system_pool, owner_pool, scope):
    """The escalated run waits at `higher`, which has no limit of its own. A
    second pass must leave it there — not expire it on the age of `check`."""
    workflow = await _workflow(pool, scope, limit=(DAY, "escalate"), higher=True)
    run_id = await _at_the_gate(pool, system_pool, workflow, waited=2 * DAY,
                                owner_pool=owner_pool)
    await expire(_database_url(), dry_run=False)

    await expire(_database_url(), dry_run=False)

    assert (await _run(system_pool, run_id))["status"] == "awaiting_oversight"
    assert len(await _audit(system_pool, "workflow.run_escalated", run_id)) == 1


async def test_an_escalation_with_nowhere_to_go_fails_the_run_saying_why(
        pool, system_pool, owner_pool, scope):
    """Activation refuses this definition; here it is written past the
    validator, as a definition changed underneath a run would be."""
    workflow = await _workflow(pool, scope, limit=(DAY, "escalate"))
    run_id = await _at_the_gate(pool, system_pool, workflow, waited=2 * DAY,
                                owner_pool=owner_pool)

    counts = await expire(_database_url(), dry_run=False)

    assert counts["gates_failed"] >= 1
    run = await _run(system_pool, run_id)
    assert run["status"] == "failed" and "no escalation edge" in run["error_message"]


async def test_approval_at_a_gate_whose_only_edge_escalates_completes_the_run(
        pool, system_pool, owner_pool, scope, people):  # noqa: F811
    """The case `excluding` exists for, through the service: before it, this
    approval failed the run — a guarded edge that did not hold, no default."""
    workflow = await _classified(owner_pool, scope, await _workflow(
        pool, scope, limit=(DAY, "escalate"), higher=True))
    run_id = await _at_the_gate(pool, system_pool, workflow, waited=timedelta(hours=1),
                                owner_pool=owner_pool)

    out = await orchestration.record_oversight(
        pool, people.reviewer, run_id, OversightDecision(decision="approved"),
        engine=_engine(pool, system_pool))

    assert out.run.status == "complete"
    assert "higher" not in await _gates(system_pool, run_id)


# ── the content period ────────────────────────────────────────────────────────

async def test_past_the_content_period_the_run_expires_and_its_content_goes(
        pool, system_pool, owner_pool):
    """A gate without a time limit used to keep its run, and its content, for
    good. The content goes in the same step: the purge would count the run
    from its new completed_at and keep it a whole period more."""
    scope = await _scope(system_pool, content_days=_CONTENT_DAYS, trace_days=10 * _CONTENT_DAYS)
    workflow = await _workflow(pool, scope)
    run_id = await _at_the_gate(pool, system_pool, workflow, waited=(_CONTENT_DAYS + 1) * DAY,
                                owner_pool=owner_pool)

    await expire(_database_url(), dry_run=False)

    run = await _run(system_pool, run_id)
    assert run["status"] == "expired" and run["completed_at"] is not None
    await _assert_cleared(system_pool, run_id)
    assert (await _gates(system_pool, run_id))["check"]["status"] == "expired"
    # The message went with the content; why the run ended is the audit's.
    [entry] = await _audit(system_pool, "workflow.run_expired", run_id)
    assert entry["payload"]["reason"] == "content_period" and entry["actor_id"] is None


async def test_within_the_content_period_a_gate_without_limit_waits(
        pool, system_pool, owner_pool):
    """Counter-probe: no limit of its own means waiting for a person, as long
    as the content may be kept."""
    scope = await _scope(system_pool, content_days=_CONTENT_DAYS, trace_days=10 * _CONTENT_DAYS)
    workflow = await _workflow(pool, scope)
    run_id = await _at_the_gate(pool, system_pool, workflow, waited=(_CONTENT_DAYS - 1) * DAY,
                                owner_pool=owner_pool)

    await expire(_database_url(), dry_run=False)

    assert (await _run(system_pool, run_id))["status"] == "awaiting_oversight"


async def test_the_content_period_wins_over_an_escalation(pool, system_pool, owner_pool):
    """An escalation would only keep the content waiting at the next gate."""
    scope = await _scope(system_pool, content_days=_CONTENT_DAYS, trace_days=10 * _CONTENT_DAYS)
    workflow = await _workflow(pool, scope, limit=(DAY, "escalate"), higher=True)
    run_id = await _at_the_gate(pool, system_pool, workflow, waited=(_CONTENT_DAYS + 1) * DAY,
                                owner_pool=owner_pool)

    await expire(_database_url(), dry_run=False)

    assert (await _run(system_pool, run_id))["status"] == "expired"
    assert "higher" not in await _gates(system_pool, run_id)
    assert await _audit(system_pool, "workflow.run_escalated", run_id) == []


async def test_an_expired_run_is_purged_like_a_finished_one(pool, system_pool, owner_pool):
    """`expired` is one of the states the periods apply to. Without it in the
    list, a run that expired on its time limit would keep its content for good
    — the gap CHY-254 closes, reopened one state later."""
    scope = await _scope(system_pool, content_days=_CONTENT_DAYS, trace_days=10 * _CONTENT_DAYS)
    workflow = await _workflow(pool, scope, limit=(DAY, "reject"))
    run_id = await _at_the_gate(pool, system_pool, workflow, waited=2 * DAY,
                                owner_pool=owner_pool)
    await expire(_database_url(), dry_run=False)
    await owner_pool.execute(
        "UPDATE workflow_runs SET completed_at = now() - make_interval(days => $2) "
        "WHERE id = $1", run_id, _CONTENT_DAYS + 1)

    await purge(_database_url(), dry_run=False)

    await _assert_cleared(system_pool, run_id)


# ── a person was faster ───────────────────────────────────────────────────────

async def test_a_decision_that_came_first_wins(pool, system_pool, owner_pool, scope,
                                               people):  # noqa: F811
    """The command finds the gate, then a reviewer decides, then the command
    acts on what it found. Checked again under the run's lock, the decision
    stands and nothing is expired."""
    workflow = await _classified(owner_pool, scope,
                                 await _workflow(pool, scope, limit=(DAY, "reject")))
    run_id = await _at_the_gate(pool, system_pool, workflow, waited=2 * DAY,
                                owner_pool=owner_pool)
    async with system_pool.acquire() as conn:
        found = [g for g in await store.waiting_gates(conn, [], []) if g["run_id"] == run_id]
    assert found, "the gate is due"

    await orchestration.record_oversight(
        pool, people.reviewer, run_id, OversightDecision(decision="approved"),
        engine=_engine(pool, system_pool))

    async def stale(*_a, **_k):
        return found

    async with system_pool.acquire() as conn:
        periods = await retention.current_periods(conn)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(store, "waiting_gates", stale)
        counts = await orchestration.expire_gates(system_pool, periods)

    assert counts == {"gates_expired": 0, "gates_escalated": 0, "gates_failed": 0}
    assert (await _run(system_pool, run_id))["status"] == "complete"
    assert await _audit(system_pool, "workflow.run_expired", run_id) == []


# ── activation reads the rows ─────────────────────────────────────────────────

async def _rules(pool, workflow: UUID) -> dict[str, str | None]:
    out = await orchestration.validate_workflow(
        pool, caller("consumer"), workflow,
        limits=orchestration.DocumentLimits(max_bytes=10 * 1024 * 1024, max_pages=200))
    return {v.rule: v.node_ref for v in out.violations}


async def test_activation_refuses_an_escalation_without_an_edge(pool, scope):
    workflow = await _workflow(pool, scope, limit=(DAY, "escalate"), status="draft")
    assert (await _rules(pool, workflow)).get("escalation_without_edge") == "check"


async def test_activation_refuses_a_time_limit_that_says_nothing(pool, scope):
    workflow = await _workflow(pool, scope, limit=(DAY, None), status="draft")
    assert (await _rules(pool, workflow)).get("timeout_without_action") == "check"


async def test_a_well_formed_escalation_passes_the_gate_rules(pool, scope):
    """Counter-probe: the rules read the rows and find nothing to refuse."""
    workflow = await _workflow(pool, scope, limit=(DAY, "escalate"), higher=True,
                               status="draft")
    rules = await _rules(pool, workflow)
    assert not set(rules) & {"timeout_without_action", "action_without_timeout",
                             "escalation_without_edge", "escalation_not_to_gate",
                             "escalation_to_weaker_gate"}
