"""
tests/integration/test_review_samples_db.py

The review level `sample` and its queue, on a real PostgreSQL (CHY-223, ADR-050 3b).

What only the database shows:

- the CHECKs of migration 045: a share only on a sample gate and required
  there, and a sample gate without areas or time limit
- a run passes a sample gate without waiting; a drawn pass lands in
  `review_samples`, and the audit entry says so with the share
- the queue is visible to whoever may see the run and to nobody else (RLS)
- a verdict is given once, from `reviewer` up, audited; its reason is stored
  with the run's placeholders and cleared with the run's content; the row goes
  with the run
- a scope classified stricter after the release makes a sample gate wait for a
  person — without a new release

The workflow and the echo model are those of test_run_placeholders_db.py:
draft → revise, and here a sample gate after them.
"""
from __future__ import annotations

import json
from uuid import UUID

import asyncpg
import pytest

from contexts.orchestration import service as orchestration
from contexts.orchestration import store
from contexts.orchestration.errors import RunContentPurged, SampleAlreadyJudged
from contexts.orchestration.schema import ReviewSampleVerdict, WorkflowRunStart
from kernel import acting
from kernel.errors import AccessDenied
from kernel.identity import resolve_actor
from tests.integration._callers import caller
from tests.integration.test_run_placeholders_db import (  # noqa: F401 -- fixtures
    _engine,
    _tables_holding,
    echo,
    people,
)

pytestmark = pytest.mark.integration


async def _classify(owner_pool, scope: UUID, data_class: str) -> None:
    await owner_pool.execute(
        "UPDATE scope_nodes SET privacy_level = $2 WHERE id = $1", scope, data_class)


async def _workflow(pool, scope: UUID, *, rate: float = 1.0, risk_class: str = "minimal",
                    status: str = "active") -> UUID:
    """draft → check (sample): one model call, then a gate that does not wait."""
    workflow = await pool.fetchval(
        "INSERT INTO workflow_definitions (scope_id, name, status, risk_class) "
        "VALUES ($1, 'it', $2, $3) RETURNING id", scope, status, risk_class)
    draft = await pool.fetchval(
        "INSERT INTO workflow_nodes (workflow_id, node_ref, node_type, task_type, "
        "input_mapping) VALUES ($1, 'draft', 'llm_call', 'draft_reply', $2::text::jsonb) "
        "RETURNING id", workflow, json.dumps({"prompt": "run.letter"}))
    check = await pool.fetchval(
        "INSERT INTO workflow_nodes (workflow_id, node_ref, node_type, review_role, "
        "review_sample_rate) VALUES ($1, 'check', 'human_review', 'sample', $2) RETURNING id",
        workflow, rate)
    await pool.execute(
        "INSERT INTO workflow_connections (workflow_id, source_node_id, target_node_id) "
        "VALUES ($1, $2, $3)", workflow, draft, check)
    return workflow


async def _start(pool, system_pool, workflow: UUID, starter, echo_model):
    return await orchestration.start_workflow_run(
        pool, starter, workflow,
        # The name is named, as a caller that knows it does (CHY-234): a name
        # nobody lists stays as written, everywhere, the reason included.
        WorkflowRunStart(input_data={"letter": f"Bitte an {echo_model.name} schreiben.",
                                     "identifiers_to_redact": {"PERSON": [echo_model.name]}}),
        engine=_engine(pool, system_pool))


@pytest.fixture
async def drawn(pool, system_pool, owner_pool, scope, echo, people):  # noqa: F811
    """A run through a sample gate of share 1, in an internal scope: drawn."""
    await _classify(owner_pool, scope, "internal")
    advance = await _start(pool, system_pool, await _workflow(pool, scope),
                           people.starter, echo)
    assert advance.run.status == "complete", advance.run
    execution = await system_pool.fetchval(
        "SELECT execution_id FROM review_samples WHERE run_id = $1", advance.run.id)
    return advance.run.id, execution


async def _judge(pool, system_pool, who, execution_id: UUID, verdict="objected", reason=None):
    return await orchestration.judge_sample(
        pool, who, execution_id, ReviewSampleVerdict(verdict=verdict, reason=reason),
        placeholders=_engine(pool, system_pool).placeholders)


# ── passing the gate ──────────────────────────────────────────────────────────

async def test_the_run_passes_and_the_drawn_pass_is_queued(system_pool, drawn):
    run_id, execution_id = drawn
    assert execution_id is not None
    output = await system_pool.fetchval(
        "SELECT output_data FROM node_executions WHERE id = $1", execution_id)
    assert output == {"decision": "sampled", "drawn": True}
    entry = json.loads(await system_pool.fetchval(
        "SELECT payload::text FROM audit_log WHERE action = 'workflow.run_gate_reached' "
        "AND target_ref = $1", f"workflow_run:{run_id}"))
    assert (entry["level"], entry["sample_rate"], entry["drawn"]) == ("sample", 1.0, True)


async def test_a_stricter_scope_makes_the_sample_gate_wait(
        pool, system_pool, owner_pool, scope, echo, people):  # noqa: F811
    """Released for an internal scope; then it turns confidential. The next run
    waits for a person at the same gate — no new release."""
    await _classify(owner_pool, scope, "internal")
    workflow = await _workflow(pool, scope)
    await _classify(owner_pool, scope, "confidential")

    advance = await _start(pool, system_pool, workflow, people.starter, echo)

    assert advance.run.status == "awaiting_oversight"
    assert not await system_pool.fetchval(
        "SELECT count(*) FROM review_samples WHERE run_id = $1", advance.run.id)


async def test_activation_refuses_a_sample_gate_above_a_low_minimum(
        pool, owner_pool, scope):
    from tests.integration.test_review_levels_db import _rules
    await _classify(owner_pool, scope, "internal")
    workflow = await _workflow(pool, scope, risk_class="limited", status="draft")
    assert (await _rules(pool, workflow)).get("review_below_minimum") == "check"


# ── the CHECKs ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(("role", "rate", "areas"), [
    ("sample", None, None),           # a sample gate without its share
    ("approver", 0.5, None),          # a share on a gate that waits anyway
    ("sample", 1.5, None),            # more than all
    ("sample", 0.5, ["markt"]),       # areas at a gate that decides nothing
])
async def test_the_database_refuses_a_malformed_sample_gate(pool, workflow, role, rate, areas):
    with pytest.raises(asyncpg.CheckViolationError):
        await pool.execute(
            "INSERT INTO workflow_nodes (workflow_id, node_ref, node_type, review_role, "
            "review_sample_rate, review_areas) VALUES ($1, 'check', 'human_review', $2, $3, $4)",
            workflow, role, rate, areas)


# ── the queue ─────────────────────────────────────────────────────────────────

async def test_who_sees_the_run_sees_the_sample_and_nobody_else(pool, people, drawn):  # noqa: F811
    _, execution_id = drawn
    for who in (people.starter, people.reviewer):
        assert execution_id in {s.sample_id for s in
                                await orchestration.list_samples(pool, who)}
    assert execution_id not in {s.sample_id for s in
                                await orchestration.list_samples(pool, people.stranger)}


async def test_the_table_itself_hides_the_sample(pool, people, drawn):  # noqa: F811
    """Row security on `review_samples` of its own, not only through the join on
    the run's steps: a query that reads the table alone, as somebody who may not
    see the run, finds nothing (CHY-92)."""
    _, execution_id = drawn
    counts = {}
    for name in ("reviewer", "stranger"):
        actor = await resolve_actor(pool, getattr(people, name))
        async with acting.acting_as(pool, actor.id) as conn:
            counts[name] = await conn.fetchval(
                "SELECT count(*) FROM review_samples WHERE execution_id = $1", execution_id)
    assert counts == {"reviewer": 1, "stranger": 0}


async def test_a_verdict_once_from_reviewer_up_audited(pool, system_pool, people, drawn):  # noqa: F811
    run_id, execution_id = drawn
    with pytest.raises(AccessDenied):
        await _judge(pool, system_pool, people.colleague, execution_id)

    out = await _judge(pool, system_pool, people.reviewer, execution_id, verdict="objected")

    assert (out.verdict, out.node_ref) == ("objected", "check")
    with pytest.raises(SampleAlreadyJudged):
        await _judge(pool, system_pool, people.reviewer, execution_id, verdict="confirmed")
    assert out.sample_id not in {
        s.sample_id for s in await orchestration.list_samples(pool, people.reviewer)}
    row = await system_pool.fetchrow(
        "SELECT actor_id, payload::text AS payload FROM audit_log "
        "WHERE action = 'workflow.sample_reviewed' AND target_ref = $1", f"workflow_run:{run_id}")
    assert json.loads(row["payload"]) == {"node_ref": "check", "verdict": "objected"}


async def test_the_reason_is_stored_with_placeholders(
        pool, system_pool, owner_pool, people, drawn, echo):  # noqa: F811
    _, execution_id = drawn
    await _judge(pool, system_pool, people.reviewer, execution_id,
                 reason=f"Der Entwurf nennt {echo.name} falsch")
    assert await _tables_holding(owner_pool, echo.name) == {}


async def test_the_reason_goes_with_the_content_and_the_row_with_the_run(
        pool, system_pool, people, drawn):  # noqa: F811
    run_id, execution_id = drawn
    await _judge(pool, system_pool, people.reviewer, execution_id, reason="zu knapp")
    async with system_pool.acquire() as conn, conn.transaction():
        await store.clear_run_content(conn, [run_id])
    assert await system_pool.fetchval(
        "SELECT reason FROM review_samples WHERE execution_id = $1", execution_id) is None

    async with system_pool.acquire() as conn, conn.transaction():
        await store.delete_runs(conn, [run_id])
    assert not await system_pool.fetchval(
        "SELECT count(*) FROM review_samples WHERE execution_id = $1", execution_id)


async def test_a_run_whose_content_is_cleared_has_nothing_to_judge(
        pool, system_pool, people, drawn):  # noqa: F811
    run_id, execution_id = drawn
    async with system_pool.acquire() as conn, conn.transaction():
        await store.clear_run_content(conn, [run_id])
    with pytest.raises(RunContentPurged):
        await _judge(pool, system_pool, people.reviewer, execution_id)


async def test_an_open_sample_can_be_found_by_its_run(pool, people, scope, drawn):  # noqa: F811
    run_id, execution_id = drawn
    [sample] = [s for s in await orchestration.list_samples(pool, people.reviewer)
                if s.run_id == run_id]
    assert sample.verdict is None and sample.node_ref == "check"
    # One queue, two sources (CHY-269): a pass says where it came from.
    assert (sample.source, sample.sample_id, sample.scope_id) == ("workflow", execution_id, scope)
    detail = await orchestration.sample_detail(pool, people.reviewer, execution_id)
    assert (detail.run_id, detail.prompt, detail.answer) == (run_id, None, None)


async def test_a_judge_who_does_not_see_the_run_does_not_find_it(
        pool, system_pool, drawn):
    from contexts.orchestration.errors import SampleNotFound
    _, execution_id = drawn
    with pytest.raises(SampleNotFound):
        await _judge(pool, system_pool, caller("reviewer"), execution_id)
