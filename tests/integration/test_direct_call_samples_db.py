"""
tests/integration/test_direct_call_samples_db.py

A spot check of direct calls, on a real PostgreSQL (CHY-269, ADR-050, migration 048).

What only the database shows:

- the CHECKs: a share goes with `minimal` and only there, between 0 and 1; a
  drawn call holds its content until the period clears it, and nothing of it after
- along the chain the highest share applies, and a lower one below is refused
- a drawn call is kept with placeholders, visible to its caller, to members of
  its scope and to admin, and to nobody else (RLS) — also not by writing one
  in someone else's name
- one queue: a drawn call is listed with its source, its detail shows what the
  model saw and gave; a verdict once, from `reviewer` up, audited, its reason
  with placeholders
- the periods: content cleared after `run_content`, the verdict kept; the row
  deleted after `run_trace`; a call whose content is gone has nothing to judge
"""
from __future__ import annotations

from decimal import Decimal
from uuid import UUID, uuid4

import asyncpg
import pytest

from contexts.orchestration import service as orchestration
from contexts.orchestration.errors import (
    SampleAlreadyJudged,
    SampleContentPurged,
    SampleNotFound,
    ServiceAccountMayNotDecide,
)
from contexts.orchestration.schema import ReviewSampleVerdict
from kernel import acting, retention, scopes
from kernel.errors import AccessDenied, ScopeLoosensSampleRate
from kernel.identity import resolve_actor
from tests.integration._callers import Caller, caller
from tests.integration.test_direct_call_risk_db import _child
from tests.integration.test_run_placeholders_db import _engine, people  # noqa: F401 -- fixture

pytestmark = pytest.mark.integration

ARCHITECT = caller("architect")
PROMPT = "Schreib {{PERSON_1}} ({{EMAIL_1}}) eine Zusage."
ANSWER = "Liebe {{PERSON_1}}, Ihr Termin steht."


async def _draw(pool, scope: UUID, who) -> UUID:
    """A direct call in `scope` by `who`, drawn at share 1."""
    actor = await resolve_actor(pool, who)
    sample_id = await orchestration.draw_direct_call(
        pool,
        orchestration.DirectCall(scope_id=scope, called_by=actor.id, task_type="summarize",
                                 model_used="gemma3-4b", block_versions={str(uuid4()): 3},
                                 prompt=PROMPT, answer=ANSWER),
        Decimal("1"),
    )
    assert sample_id is not None, "share 1 draws every call"
    return sample_id


async def _judge(pool, system_pool, who, sample_id: UUID, verdict="objected", reason=None):
    return await orchestration.judge_sample(
        pool, who, sample_id, ReviewSampleVerdict(verdict=verdict, reason=reason),
        placeholders=_engine(pool, system_pool).placeholders)


async def _purge(system_pool) -> dict[str, int]:
    async with system_pool.acquire() as conn, conn.transaction():
        return await orchestration.purge_direct_samples(
            conn, await retention.current_periods(conn))


# ── the CHECKs ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(("risk_class", "rate"), [
    ("minimal", None),      # minimal needs a share
    ("limited", 0.1),       # no other class takes one
    (None, 0.1),            # nor does taking the setting back
    ("minimal", 0),         # above 0
    ("minimal", 1.5),       # at most 1
])
async def test_the_database_pairs_the_share_with_minimal(pool, scope, risk_class, rate):
    setter = await resolve_actor(pool, ARCHITECT)
    with pytest.raises(asyncpg.CheckViolationError):
        await pool.execute(
            "INSERT INTO direct_call_risk_classes (scope_id, risk_class, sample_rate, set_by) "
            "VALUES ($1, $2, $3, $4)", scope, risk_class, rate, setter.id)


@pytest.mark.parametrize(("risk_class", "rate"), [("minimal", 0.05), ("minimal", 1),
                                                  ("limited", None), (None, None)])
async def test_and_takes_what_belongs_together(pool, scope, risk_class, rate):
    """Counter-probe to the refusals above."""
    setter = await resolve_actor(pool, ARCHITECT)
    await pool.execute(
        "INSERT INTO direct_call_risk_classes (scope_id, risk_class, sample_rate, set_by) "
        "VALUES ($1, $2, $3, $4)", scope, risk_class, rate, setter.id)


@pytest.mark.parametrize(("prompt", "purged", "verdict", "reason"), [
    (None, False, None, None),          # content missing, not cleared
    ("p", True, None, None),            # cleared, content still there
    ("p", False, "objected", None),     # a verdict without its author
    ("p", False, None, "warum"),        # a reason without a verdict
])
async def test_the_database_refuses_a_malformed_sample(
        owner_pool, pool, scope, prompt, purged, verdict, reason):
    actor = await resolve_actor(pool, caller("contributor"))
    with pytest.raises(asyncpg.CheckViolationError):
        await owner_pool.execute(
            "INSERT INTO direct_call_samples (id, scope_id, called_by, sample_rate, task_type, "
            "model_used, prompt, answer, content_purged_at, verdict, reason) "
            "VALUES ($1, $2, $3, 0.5, 't', 'm', $4, 'a', "
            "        CASE WHEN $5::bool THEN now() END, $6, $7)",
            uuid4(), scope, actor.id, prompt, purged, verdict, reason)


# ── the share along the chain ─────────────────────────────────────────────────

async def test_the_highest_share_applies_and_a_lower_one_below_is_refused(pool, scope):
    child = await _child(pool, scope)
    await scopes.set_direct_call_risk_class(pool, ARCHITECT, scope, "minimal", Decimal("0.2"))
    assert (await scopes.direct_call_risk_of(pool, child)).sample_rate == Decimal("0.2")

    with pytest.raises(ScopeLoosensSampleRate):
        await scopes.set_direct_call_risk_class(pool, ARCHITECT, child, "minimal", Decimal("0.1"))

    await scopes.set_direct_call_risk_class(pool, ARCHITECT, child, "minimal", Decimal("0.3"))
    risk = await scopes.direct_call_risk_of(pool, child)
    assert (risk.own_sample_rate, risk.sample_rate) == (Decimal("0.3"), Decimal("0.3"))
    row = await pool.fetchrow(
        "SELECT payload->'sample_rate' AS rate FROM audit_log "
        "WHERE action = 'scope.direct_call_risk_class_set' AND target_ref = $1", f"scope:{child}")
    assert row["rate"] == {"from": None, "to": 0.3, "effective": 0.3}


# ── the drawn call and who sees it ────────────────────────────────────────────

async def test_a_drawn_call_is_kept_with_placeholders(system_pool, pool, scope, people):  # noqa: F811
    sample_id = await _draw(pool, scope, people.starter)
    row = await system_pool.fetchrow(
        "SELECT prompt, answer, sample_rate, task_type, model_used, block_versions "
        "FROM direct_call_samples WHERE id = $1", sample_id)
    assert (row["prompt"], row["answer"]) == (PROMPT, ANSWER)
    assert row["sample_rate"] == Decimal("1")
    assert (row["task_type"], row["model_used"]) == ("summarize", "gemma3-4b")
    assert list(row["block_versions"].values()) == [3]


async def test_the_caller_the_scope_and_admin_see_it_and_nobody_else(
        pool, scope, people):  # noqa: F811
    sample_id = await _draw(pool, scope, people.starter)
    for who in (people.starter, people.reviewer, people.colleague, caller("admin")):
        [entry] = [s for s in await orchestration.list_samples(pool, who)
                   if s.sample_id == sample_id]
        assert (entry.source, entry.scope_id, entry.task_type) == ("direct_call", scope, "summarize")
    assert sample_id not in {s.sample_id for s in
                             await orchestration.list_samples(pool, people.stranger)}
    with pytest.raises(SampleNotFound):
        await orchestration.sample_detail(pool, people.stranger, sample_id)


async def test_its_history_goes_to_the_same_people(pool, scope, people):  # noqa: F811
    """What the audit log asks before it hands out a drawn call's entries (CHY-303)."""
    sample_id = await _draw(pool, scope, people.starter)
    for who in (people.starter, people.reviewer, people.colleague, caller("admin")):
        actor = await resolve_actor(pool, who)
        assert await orchestration.direct_sample_visible(pool, actor.id, sample_id)
    stranger = await resolve_actor(pool, people.stranger)
    assert not await orchestration.direct_sample_visible(pool, stranger.id, sample_id)


async def test_the_detail_shows_what_the_model_saw_and_gave(pool, scope, people):  # noqa: F811
    sample_id = await _draw(pool, scope, people.starter)
    detail = await orchestration.sample_detail(pool, people.reviewer, sample_id)
    assert (detail.prompt, detail.answer, detail.content_purged_at) == (PROMPT, ANSWER, None)
    assert detail.run_id is None and detail.node_ref is None


async def test_the_table_itself_hides_it_and_refuses_a_call_in_another_name(
        pool, scope, people):  # noqa: F811
    """RLS, not a filter in the query: a forgotten WHERE sees nothing."""
    sample_id = await _draw(pool, scope, people.starter)
    stranger = await resolve_actor(pool, people.stranger)
    starter = await resolve_actor(pool, people.starter)
    async with acting.acting_as(pool, stranger.id) as conn:
        assert await conn.fetchval(
            "SELECT count(*) FROM direct_call_samples WHERE id = $1", sample_id) == 0
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.execute(
                "INSERT INTO direct_call_samples (id, scope_id, called_by, sample_rate, "
                "task_type, model_used, prompt, answer) "
                "VALUES ($1, $2, $3, 1, 't', 'm', 'p', 'a')", uuid4(), scope, starter.id)


# ── the verdict ───────────────────────────────────────────────────────────────

async def test_a_verdict_once_from_reviewer_up_audited(
        pool, system_pool, scope, people):  # noqa: F811
    sample_id = await _draw(pool, scope, people.starter)
    with pytest.raises(AccessDenied):
        await _judge(pool, system_pool, people.colleague, sample_id)

    out = await _judge(pool, system_pool, people.reviewer, sample_id, verdict="objected")
    assert (out.sample_id, out.source, out.verdict) == (sample_id, "direct_call", "objected")
    with pytest.raises(SampleAlreadyJudged):
        await _judge(pool, system_pool, people.reviewer, sample_id, verdict="confirmed")
    assert sample_id not in {
        s.sample_id for s in await orchestration.list_samples(pool, people.reviewer)}

    reviewer = await resolve_actor(pool, people.reviewer)
    entry = await system_pool.fetchrow(
        "SELECT actor_id, payload FROM audit_log "
        "WHERE action = 'direct_call.sample_reviewed' AND target_ref = $1",
        f"direct_call_sample:{sample_id}")
    assert entry["actor_id"] == reviewer.id
    assert entry["payload"] == {"scope_id": str(scope), "task_type": "summarize",
                                "verdict": "objected"}


async def test_a_technical_account_does_not_judge(pool, system_pool, scope, people):  # noqa: F811
    """A member of the scope with the role, and still refused: not a person (CHY-275).

    The verdict stays open for a person to give.
    """
    sample_id = await _draw(pool, scope, people.starter)
    account = Caller(sub=f"it-ci-{uuid4().hex}", username="it ci",
                     groups=["chy-reviewers", "chy-service-accounts"])
    actor = await resolve_actor(pool, account)
    await pool.execute(
        "INSERT INTO scope_memberships (id, scope_id, actor_id, role) VALUES ($1, $2, $3, 'reviewer')",
        uuid4(), scope, actor.id)
    assert (await orchestration.sample_detail(pool, account, sample_id)).sample_id == sample_id
    with pytest.raises(ServiceAccountMayNotDecide):
        await _judge(pool, system_pool, account, sample_id, verdict="confirmed")
    assert await system_pool.fetchval(
        "SELECT verdict FROM direct_call_samples WHERE id = $1", sample_id) is None
    out = await _judge(pool, system_pool, people.reviewer, sample_id, verdict="confirmed")
    assert out.verdict == "confirmed", "a person still can"


async def test_the_reason_is_kept_with_placeholders(
        pool, system_pool, scope, people):  # noqa: F811
    """A reviewer easily writes the value into the reason. The scope's rules and
    the shipped patterns replace it; there is no mapping to turn it back."""
    sample_id = await _draw(pool, scope, people.starter)
    email = f"erika.{uuid4().hex[:8]}@example.de"
    await _judge(pool, system_pool, people.reviewer, sample_id,
                 reason=f"Die Antwort nennt {email} nicht")
    reason = await system_pool.fetchval(
        "SELECT reason FROM direct_call_samples WHERE id = $1", sample_id)
    assert email not in reason
    assert reason == "Die Antwort nennt {{EMAIL_1}} nicht"


# ── the periods ───────────────────────────────────────────────────────────────

async def test_the_periods_clear_the_content_then_delete_the_row(
        pool, system_pool, owner_pool, scope, people):  # noqa: F811
    judged = await _draw(pool, scope, people.starter)
    open_one = await _draw(pool, scope, people.starter)
    await _judge(pool, system_pool, people.reviewer, judged, reason="zu knapp")

    # Past the shipped run_content period (30 days), within run_trace (183).
    await owner_pool.execute(
        "UPDATE direct_call_samples SET drawn_at = now() - interval '40 days' "
        "WHERE id = ANY($1::uuid[])", [judged, open_one])
    await _purge(system_pool)
    rows = {r["id"]: r for r in await system_pool.fetch(
        "SELECT id, prompt, answer, reason, verdict, content_purged_at "
        "FROM direct_call_samples WHERE id = ANY($1::uuid[])", [judged, open_one])}
    for row in rows.values():
        assert (row["prompt"], row["answer"], row["reason"]) == (None, None, None)
        assert row["content_purged_at"] is not None
    assert rows[judged]["verdict"] == "objected", "the verdict is the trace and stays"
    with pytest.raises(SampleContentPurged):
        await _judge(pool, system_pool, people.reviewer, open_one)

    # Past run_trace: the row goes.
    await owner_pool.execute(
        "UPDATE direct_call_samples SET drawn_at = now() - interval '200 days' "
        "WHERE id = ANY($1::uuid[])", [judged, open_one])
    await _purge(system_pool)
    assert await system_pool.fetchval(
        "SELECT count(*) FROM direct_call_samples WHERE id = ANY($1::uuid[])",
        [judged, open_one]) == 0


async def test_a_call_within_its_period_is_left_alone(
        pool, system_pool, scope, people):  # noqa: F811
    """Counter-probe to the purge above."""
    sample_id = await _draw(pool, scope, people.starter)
    await _purge(system_pool)
    assert await system_pool.fetchval(
        "SELECT prompt FROM direct_call_samples WHERE id = $1", sample_id) == PROMPT
