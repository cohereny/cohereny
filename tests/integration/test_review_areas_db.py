"""
tests/integration/test_review_areas_db.py

Areas at the gate, on a real PostgreSQL (CHY-180, ADR-050).

The seven cases of Listing 10.2-1 of the book, through the service this time
and not against a sketch: one vote waits, two approvals release, two rejections
reject, differing votes escalate — and three refusals. Each refusal twice: once
through `record_oversight`, which answers with a readable reason, and once by a
direct insert past the service, because the acceptance of CHY-180 is that the
**database** refuses a vote without membership.

And what the sketch did not have:

- the escalation goes along the edge to the credit committee, which decides
  with one vote — a higher authority by its own area (Thomas, 09.11.2026)
- a membership is revoked with a date (Thomas, 09.11.2026): no new vote under
  it, a vote cast before it stays and counts
- areas apply below the scope that defines them, the nearest definition wins
- the competence order is kept by `admin` only, and every change is audited

The organisation is the one of BTO 1.1: front office (`markt`), back office
(`marktfolge`), credit committee (`kreditkommission`). The workflow starts at
its gate; no model is called, nothing here is about what a step produces.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from uuid import UUID, uuid4

import asyncpg
import pytest

from contexts.orchestration import conditions
from contexts.orchestration import service as orchestration
from contexts.orchestration.errors import (
    AreaAlreadyVoted,
    NotInReviewArea,
    ReviewAreaAmbiguous,
    ReviewAreaNotAtGate,
    ReviewerAlreadyDecided,
)
from contexts.orchestration.schema import OversightDecision, WorkflowRunStart
from kernel import review_areas
from kernel.errors import (
    AccessDenied,
    AreaMembershipExists,
    AreaMembershipNotFound,
    ReviewAreaExists,
)
from kernel.identity import resolve_actor
from kernel.schema import ReviewAreaCreate, ReviewAreaMemberCreate, scope_label
from tests.integration._callers import caller
from tests.integration.test_run_placeholders_db import _engine

pytestmark = pytest.mark.integration

ADMIN = caller("admin")


@dataclass
class _Person:
    caller: object
    id: UUID


@dataclass
class _Order:
    """A scope's competence order and the people in it."""

    scope: UUID
    markt: _Person          # front office
    marktfolge: _Person     # back office
    beide: _Person          # listed in both — the person Listing 10.2-1 stops
    kommission: _Person     # credit committee
    niemand: _Person        # a reviewer in the scope, in no area


async def _person(pool, scope: UUID) -> _Person:
    """A reviewer who sees the runs of the scope (CHY-92)."""
    who = caller("reviewer")
    actor = await resolve_actor(pool, who)
    await pool.execute(
        "INSERT INTO scope_memberships (id, scope_id, actor_id, role) "
        "VALUES ($1, $2, $3, 'reviewer')", uuid4(), scope, actor.id)
    return _Person(who, actor.id)


async def _area(pool, scope: UUID, name: str, *members: _Person) -> None:
    await review_areas.create_area(pool, ADMIN, scope, ReviewAreaCreate(name=name))
    for member in members:
        await review_areas.add_member(pool, ADMIN, scope, name,
                                      ReviewAreaMemberCreate(actor_id=member.id))


@pytest.fixture
async def order(pool, scope) -> _Order:
    people = [await _person(pool, scope) for _ in range(5)]
    markt, marktfolge, beide, kommission, niemand = people
    await _area(pool, scope, "markt", markt, beide)
    await _area(pool, scope, "marktfolge", marktfolge, beide)
    await _area(pool, scope, "kreditkommission", kommission)
    return _Order(scope, markt, marktfolge, beide, kommission, niemand)


async def _workflow(pool, scope: UUID, *, status: str = "active") -> UUID:
    """votum (markt, marktfolge) — escalation → kommission (kreditkommission)."""
    workflow = await pool.fetchval(
        "INSERT INTO workflow_definitions (scope_id, name, status) VALUES ($1, 'kredit', $2) "
        "RETURNING id", scope, status)
    ids = {}
    for ref, areas in (("votum", ["markt", "marktfolge"]), ("kommission", ["kreditkommission"])):
        ids[ref] = await pool.fetchval(
            "INSERT INTO workflow_nodes (workflow_id, node_ref, node_type, review_areas) "
            "VALUES ($1, $2, 'human_review', $3) RETURNING id", workflow, ref, areas)
    await pool.execute(
        "INSERT INTO workflow_connections (workflow_id, source_node_id, target_node_id, "
        "condition) VALUES ($1, $2, $3, $4::text::jsonb)",
        workflow, ids["votum"], ids["kommission"], json.dumps(dict(conditions.ESCALATION)))
    return workflow


async def _run(pool, system_pool, scope: UUID) -> UUID:
    advance = await orchestration.start_workflow_run(
        pool, caller("contributor"), await _workflow(pool, scope),
        WorkflowRunStart(input_data={"antrag": f"Kredit {uuid4().hex[:8]}"}),
        engine=_engine(pool, system_pool))
    assert advance.run.status == "awaiting_oversight", advance.run
    return advance.run.id


async def _vote(pool, system_pool, who: _Person, run_id: UUID, decision: str,
                area: str | None = None):
    return await orchestration.record_oversight(
        pool, who.caller, run_id, OversightDecision(decision=decision, area=area),
        engine=_engine(pool, system_pool))


async def _gates(pool, run_id: UUID) -> dict[str, dict]:
    rows = await pool.fetch(
        "SELECT n.node_ref, e.status, e.output_data FROM node_executions e "
        "JOIN workflow_nodes n ON n.id = e.node_id WHERE e.run_id = $1", run_id)
    return {r["node_ref"]: dict(r) for r in rows}


async def _audit(pool, action: str, target: str) -> list[dict]:
    rows = await pool.fetch(
        "SELECT actor_id, payload::text AS payload FROM audit_log "
        "WHERE action = $1 AND target_ref = $2 ORDER BY created_at", action, target)
    return [{"actor_id": r["actor_id"], "payload": json.loads(r["payload"])} for r in rows]


# ── the four outcomes ─────────────────────────────────────────────────────────

async def test_one_vote_waits(pool, system_pool, order):
    run_id = await _run(pool, system_pool, order.scope)

    out = await _vote(pool, system_pool, order.markt, run_id, "approved")

    assert out.run.status == "awaiting_oversight"
    assert (await _gates(system_pool, run_id))["votum"]["status"] == "running"
    [entry] = await _audit(system_pool, "workflow.run_oversight_decided",
                           f"workflow_run:{run_id}")
    assert entry["actor_id"] == order.markt.id
    assert entry["payload"]["area"] == "markt"
    assert entry["payload"]["votes"] == {"markt": "approved"}


async def test_both_areas_approve_and_the_gate_releases(pool, system_pool, order):
    run_id = await _run(pool, system_pool, order.scope)

    await _vote(pool, system_pool, order.markt, run_id, "approved")
    out = await _vote(pool, system_pool, order.marktfolge, run_id, "approved")

    assert out.run.status == "complete"
    assert "kommission" not in await _gates(system_pool, run_id), "a release never escalates"


async def test_both_areas_reject_and_the_run_is_rejected(pool, system_pool, order):
    run_id = await _run(pool, system_pool, order.scope)

    await _vote(pool, system_pool, order.markt, run_id, "rejected")
    out = await _vote(pool, system_pool, order.marktfolge, run_id, "rejected")

    assert out.run.status == "rejected"


async def test_a_rejection_alone_does_not_close_the_gate(pool, system_pool, order):
    """The acceptance of CHY-180: the back office says no first, and the run
    still waits for the front office — at a gate without areas it would end."""
    run_id = await _run(pool, system_pool, order.scope)

    out = await _vote(pool, system_pool, order.marktfolge, run_id, "rejected")

    assert out.run.status == "awaiting_oversight"


async def test_differing_votes_go_up_to_the_credit_committee(pool, system_pool, order):
    """BTO 1.1 Tz. 6: neither area overrules the other; the decision moves to
    a higher authority, which decides with one vote (Thomas, 09.11.2026)."""
    run_id = await _run(pool, system_pool, order.scope)

    await _vote(pool, system_pool, order.markt, run_id, "approved")
    out = await _vote(pool, system_pool, order.marktfolge, run_id, "rejected")

    assert out.run.status == "awaiting_oversight"
    gates = await _gates(system_pool, run_id)
    assert gates["votum"]["status"] == "complete"
    assert gates["votum"]["output_data"] == {"decision": "escalated", "reason": "votes_differ"}
    assert gates["kommission"]["status"] == "running", "claimed, so a decision has a row"
    [entry] = await _audit(system_pool, "workflow.run_escalated", f"workflow_run:{run_id}")
    assert entry["actor_id"] is None
    assert entry["payload"] == {"node_ref": "votum", "reason": "votes_differ",
                                "to": ["kommission"],
                                "votes": {"markt": "approved", "marktfolge": "rejected"}}

    final = await _vote(pool, system_pool, order.kommission, run_id, "approved")
    assert final.run.status == "complete"


# ── the three refusals, through the service ───────────────────────────────────

async def test_one_person_does_not_vote_for_both_areas(pool, system_pool, order):
    run_id = await _run(pool, system_pool, order.scope)

    with pytest.raises(ReviewAreaAmbiguous):
        await _vote(pool, system_pool, order.beide, run_id, "approved")
    await _vote(pool, system_pool, order.beide, run_id, "approved", area="markt")
    with pytest.raises(ReviewerAlreadyDecided):
        await _vote(pool, system_pool, order.beide, run_id, "approved", area="marktfolge")


async def test_a_front_office_member_does_not_vote_for_the_back_office(
        pool, system_pool, order):
    run_id = await _run(pool, system_pool, order.scope)

    with pytest.raises(NotInReviewArea):
        await _vote(pool, system_pool, order.markt, run_id, "approved", area="marktfolge")
    with pytest.raises(NotInReviewArea):
        await _vote(pool, system_pool, order.niemand, run_id, "approved")
    with pytest.raises(ReviewAreaNotAtGate):
        await _vote(pool, system_pool, order.kommission, run_id, "approved",
                    area="kreditkommission")


async def test_an_area_votes_once(pool, system_pool, order):
    run_id = await _run(pool, system_pool, order.scope)
    await _vote(pool, system_pool, order.markt, run_id, "approved")

    with pytest.raises(AreaAlreadyVoted):
        await _vote(pool, system_pool, order.beide, run_id, "rejected", area="markt")
    assert (await _gates(system_pool, run_id))["votum"]["status"] == "running"


# ── the same refusals, past the service ───────────────────────────────────────

async def _insert_vote(system_pool, run_id: UUID, actor_id: UUID, area: str, scope: UUID):
    """A vote written straight into the table, as a path around the service would."""
    area_id = (await review_areas.resolve(system_pool, scope, [area]))[area].id
    execution_id = await system_pool.fetchval(
        "SELECT id FROM node_executions WHERE run_id = $1 AND status = 'running'", run_id)
    await system_pool.execute(
        "INSERT INTO review_votes (execution_id, actor_id, decision, area_id) "
        "VALUES ($1, $2, 'approved', $3)", execution_id, actor_id, area_id)


async def test_the_database_refuses_a_vote_without_membership(pool, system_pool, order):
    run_id = await _run(pool, system_pool, order.scope)
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await _insert_vote(system_pool, run_id, order.markt.id, "marktfolge", order.scope)


async def test_the_database_refuses_a_second_vote_for_an_area(pool, system_pool, order):
    run_id = await _run(pool, system_pool, order.scope)
    await _insert_vote(system_pool, run_id, order.markt.id, "markt", order.scope)
    with pytest.raises(asyncpg.UniqueViolationError):
        await _insert_vote(system_pool, run_id, order.beide.id, "markt", order.scope)


async def test_the_database_refuses_one_person_for_two_areas(pool, system_pool, order):
    run_id = await _run(pool, system_pool, order.scope)
    await _insert_vote(system_pool, run_id, order.beide.id, "markt", order.scope)
    with pytest.raises(asyncpg.UniqueViolationError):
        await _insert_vote(system_pool, run_id, order.beide.id, "marktfolge", order.scope)


async def test_the_database_accepts_a_member(pool, system_pool, order):
    """Counter-probe to the three above: a member's vote for their area goes in."""
    run_id = await _run(pool, system_pool, order.scope)
    await _insert_vote(system_pool, run_id, order.marktfolge.id, "marktfolge", order.scope)


# ── revoked with a date (Thomas, 09.11.2026) ──────────────────────────────────

async def test_a_revoked_member_votes_no_more(pool, system_pool, order):
    """Somebody moved from the front office to the back office: the front-office
    vote goes at once, the identity provider never notices."""
    run_id = await _run(pool, system_pool, order.scope)
    revoked = await review_areas.revoke_member(pool, ADMIN, order.scope, "markt", order.markt.id)
    assert revoked.revoked_at is not None

    with pytest.raises(NotInReviewArea):
        await _vote(pool, system_pool, order.markt, run_id, "approved")
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await _insert_vote(system_pool, run_id, order.markt.id, "markt", order.scope)


async def test_a_vote_cast_before_the_revocation_stays_and_counts(pool, system_pool, order):
    run_id = await _run(pool, system_pool, order.scope)
    await _vote(pool, system_pool, order.markt, run_id, "approved")
    await review_areas.revoke_member(pool, ADMIN, order.scope, "markt", order.markt.id)

    out = await _vote(pool, system_pool, order.marktfolge, run_id, "approved")

    assert out.run.status == "complete"
    row = await system_pool.fetchrow(
        "SELECT m.revoked_at, v.cast_at FROM review_votes v "
        "JOIN review_area_members m ON (m.area_id, m.actor_id) = (v.area_id, v.actor_id) "
        "JOIN node_executions e ON e.id = v.execution_id "
        "WHERE e.run_id = $1 AND v.actor_id = $2", run_id, order.markt.id)
    assert row["cast_at"] < row["revoked_at"], "the vote stays provable under its membership"


async def test_added_again_after_a_revocation_votes_again(pool, system_pool, order):
    await review_areas.revoke_member(pool, ADMIN, order.scope, "markt", order.markt.id)
    again = await review_areas.add_member(pool, ADMIN, order.scope, "markt",
                                          ReviewAreaMemberCreate(actor_id=order.markt.id))
    assert again.revoked_at is None
    run_id = await _run(pool, system_pool, order.scope)

    assert (await _vote(pool, system_pool, order.markt, run_id,
                        "approved")).run.status == "awaiting_oversight"
    entries = await _audit(system_pool, "review_area.member_added", f"scope:{order.scope}")
    assert [e["payload"]["again"] for e in entries
            if e["payload"]["member_id"] == str(order.markt.id)] == [False, True]
    [revoked] = await _audit(system_pool, "review_area.member_revoked", f"scope:{order.scope}")
    assert revoked["payload"] == {"area": "markt", "member_id": str(order.markt.id)}


# ── the scope tree ────────────────────────────────────────────────────────────

async def _child(pool, parent: UUID) -> UUID:
    child = uuid4()
    path = await pool.fetchval("SELECT scope_path::text FROM scope_nodes WHERE id = $1", parent)
    await pool.execute(
        "INSERT INTO scope_nodes (id, scope_path, node_type, name, parent_id) "
        "VALUES ($1, $2::ltree, 'team', 'it', $3)",
        child, f"{path}.{scope_label('team', child)}", parent)
    return child


async def test_areas_apply_below_and_the_nearest_definition_wins(pool, system_pool, order):
    """The workflow lives a scope below the competence order and finds it. Then
    the team defines its own `markt`: its member votes there, the one of the
    organisation no longer does."""
    team = await _child(pool, order.scope)
    run_id = await _run(pool, system_pool, team)
    await _vote(pool, system_pool, order.markt, run_id, "approved")

    eigener = await _person(pool, team)
    await _area(pool, team, "markt", eigener)
    second = await _run(pool, system_pool, team)
    with pytest.raises(NotInReviewArea):
        await _vote(pool, system_pool, order.markt, second, "approved")
    assert (await _vote(pool, system_pool, eigener, second,
                        "approved")).run.status == "awaiting_oversight"


# ── who keeps the competence order ────────────────────────────────────────────

async def test_only_admin_keeps_the_competence_order(pool, scope):
    with pytest.raises(AccessDenied):
        await review_areas.create_area(pool, caller("architect"), scope,
                                       ReviewAreaCreate(name="markt"))
    await review_areas.create_area(pool, ADMIN, scope, ReviewAreaCreate(name="markt"))
    with pytest.raises(ReviewAreaExists):
        await review_areas.create_area(pool, ADMIN, scope, ReviewAreaCreate(name="markt"))

    member = await _person(pool, scope)
    body = ReviewAreaMemberCreate(actor_id=member.id)
    with pytest.raises(AccessDenied):
        await review_areas.add_member(pool, caller("reviewer"), scope, "markt", body)
    await review_areas.add_member(pool, ADMIN, scope, "markt", body)
    with pytest.raises(AreaMembershipExists):
        await review_areas.add_member(pool, ADMIN, scope, "markt", body)
    with pytest.raises(AccessDenied):
        await review_areas.revoke_member(pool, caller("reviewer"), scope, "markt", member.id)
    await review_areas.revoke_member(pool, ADMIN, scope, "markt", member.id)
    with pytest.raises(AreaMembershipNotFound):
        await review_areas.revoke_member(pool, ADMIN, scope, "markt", member.id)

    [area] = await review_areas.list_areas(pool, caller("consumer"), scope)
    assert area.name == "markt" and area.members[0].revoked_at is not None
    assert await _audit(pool, "review_area.created", f"scope:{scope}")


async def test_the_application_cannot_delete_a_membership(system_pool, order):
    """Revoked, never deleted: the grant of migration 043 has no DELETE."""
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await system_pool.execute(
            "DELETE FROM review_area_members WHERE actor_id = $1", order.markt.id)


async def test_a_membership_somebody_voted_under_cannot_be_deleted_at_all(
        pool, system_pool, owner_pool, order):
    """What the foreign key adds to the trigger: the trigger refuses a vote
    without a current membership, the key keeps the membership a vote was cast
    under — even against the owner of the table, who may delete."""
    run_id = await _run(pool, system_pool, order.scope)
    await _vote(pool, system_pool, order.markt, run_id, "approved")

    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await owner_pool.execute(
            "DELETE FROM review_area_members WHERE actor_id = $1", order.markt.id)


# ── activation reads the competence order ─────────────────────────────────────

async def _rules(pool, workflow: UUID) -> dict[str, str | None]:
    out = await orchestration.validate_workflow(
        pool, caller("consumer"), workflow,
        limits=orchestration.DocumentLimits(max_bytes=10 * 1024 * 1024, max_pages=200))
    return {v.rule: v.node_ref for v in out.violations}


_AREA_RULES = {"unknown_review_area", "review_area_without_members",
               "review_role_contradicts_areas", "escalation_without_edge",
               "escalation_not_to_gate", "escalation_to_weaker_gate"}


async def test_the_credit_workflow_passes_the_gate_rules(pool, order):
    rules = await _rules(pool, await _workflow(pool, order.scope, status="draft"))
    assert not set(rules) & _AREA_RULES, rules


async def test_activation_refuses_areas_the_scope_chain_does_not_define(pool, scope):
    rules = await _rules(pool, await _workflow(pool, scope, status="draft"))
    assert rules.get("unknown_review_area") in {"votum", "kommission"}


async def test_activation_refuses_an_area_nobody_belongs_to(pool, scope):
    for name in ("markt", "marktfolge", "kreditkommission"):
        await review_areas.create_area(pool, ADMIN, scope, ReviewAreaCreate(name=name))
    rules = await _rules(pool, await _workflow(pool, scope, status="draft"))
    assert rules.get("review_area_without_members") in {"votum", "kommission"}
