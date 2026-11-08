"""
tests/integration/test_retention_purge_db.py

The purge on a real database (CHY-17, ADR-046).

Everything here is about rows, so everything is here and not in a double:

  - the content of a finished run past its `run_content` period is cleared —
    every content column the inventory names, in the run, its node executions
    and its votes — and the run is marked; its trace stays
  - a finished run past its `run_trace` period is deleted with everything that
    hangs on it, votes included, which have no cascade
  - a recent run and an unfinished one are never touched, however old
  - a run whose sub-run is kept longer is held, not deleted from under it
  - pilot runs and their transparency records, feedback with and without a
    workflow, by the installation-wide or the workflow's period
  - `--dry-run` changes nothing and writes no audit entry; a real run writes
    one, and a second run finds nothing left to clear

The periods of the test scope are set on the scope, so the installation-wide
defaults (30 / 183 days) stay as they are for every other test in this shared
database. Ages are set by writing timestamps in the past.
"""
from __future__ import annotations

import json
from uuid import UUID, uuid4

import asyncpg
import pytest

from cli.retention import purge
from kernel.retention import CLEARED_VALUES, INVENTORY
from kernel.schema import scope_label
from tests.integration.conftest import _database_url

pytestmark = pytest.mark.integration

_CONTENT_DAYS, _TRACE_DAYS = 10, 20


async def _scope(pool, *, content_days, trace_days, parent: UUID | None = None) -> UUID:
    """A scope of its own periods. Root unless a parent is given."""
    scope_id = uuid4()
    if parent is None:
        path = f"org_{scope_id.hex}"
        node_type = "org"
    else:
        parent_path = await pool.fetchval(
            "SELECT scope_path::text FROM scope_nodes WHERE id = $1", parent)
        path = f"{parent_path}.{scope_label('team', scope_id)}"
        node_type = "team"
    await pool.execute(
        "INSERT INTO scope_nodes (id, scope_path, node_type, name, parent_id) "
        "VALUES ($1, $2::ltree, $3, 'it', $4)", scope_id, path, node_type, parent)
    await pool.execute(
        "INSERT INTO retention_policies (retention_class, scope_id, keep_days) "
        "VALUES ('run_content', $1, $2), ('run_trace', $1, $3)",
        scope_id, content_days, trace_days)
    return scope_id


async def _workflow(pool, scope_id: UUID) -> tuple[UUID, UUID]:
    workflow = await pool.fetchval(
        "INSERT INTO workflow_definitions (scope_id, name) VALUES ($1, 'it') RETURNING id",
        scope_id)
    node = await pool.fetchval(
        "INSERT INTO workflow_nodes (workflow_id, node_ref, node_type, context_requirements) "
        "VALUES ($1, 'c', 'context_compose', $2::text::jsonb) RETURNING id",
        workflow, json.dumps({"block_types": ["system_prompt"]}))
    return workflow, node


async def _run(pool, workflow: UUID, node: UUID, *, days_ago: int, status="complete",
               parent: UUID | None = None, voter: UUID | None = None) -> UUID:
    """A run finished `days_ago` days ago, with one execution and its content."""
    finished = None if status in ("pending", "running", "awaiting_oversight") else days_ago
    run_id = await pool.fetchval(
        "INSERT INTO workflow_runs (workflow_id, workflow_version, status, input_data, result, "
        "  error_message, created_at, completed_at, parent_run_id) "
        "VALUES ($1, 1, $2, $3::text::jsonb, $4::text::jsonb, 'trace of the input', "
        "  now() - make_interval(days => $5), "
        "  CASE WHEN $6::int IS NULL THEN NULL ELSE now() - make_interval(days => $6::int) END, "
        "  $7) RETURNING id",
        workflow, status, json.dumps({"customer": "Erika Mustermann"}),
        json.dumps({"draft": "Dear Ms Mustermann"}), days_ago, finished, parent)
    execution = await pool.fetchval(
        "INSERT INTO node_executions (run_id, node_id, status, input_data, output_data) "
        "VALUES ($1, $2, 'complete', $3::text::jsonb, $4::text::jsonb) RETURNING id",
        run_id, node, json.dumps({"name": "Erika"}), json.dumps({"text": "Erika"}))
    await pool.execute(
        "INSERT INTO pii_mappings (run_id, key_id, nonce, ciphertext) "
        "VALUES ($1, 'k1', '\\x000102030405060708090a0b', '\\xdeadbeef')", run_id)
    if voter is not None:
        await pool.execute(
            "INSERT INTO review_votes (execution_id, actor_id, decision, reason) "
            "VALUES ($1, $2, 'approved', 'checked against the file of Ms Mustermann')",
            execution, voter)
    return run_id


async def _voter(pool) -> UUID:
    return await pool.fetchval(
        "INSERT INTO actors (idp_sub, display_name, role) VALUES ($1, 'it', 'reviewer') "
        "RETURNING id", f"it-purge-{uuid4().hex}")


def _cleared(value, table: str, column: str) -> bool:
    expected = CLEARED_VALUES.get((table, column))
    if expected is None:
        return value is None
    return value in ({}, [], "[purged]")


async def _content_of_run(pool, run_id: UUID) -> dict:
    run = await pool.fetchrow("SELECT * FROM workflow_runs WHERE id = $1", run_id)
    execution = await pool.fetchrow("SELECT * FROM node_executions WHERE run_id = $1", run_id)
    vote = await pool.fetchrow(
        "SELECT v.* FROM review_votes v JOIN node_executions e ON e.id = v.execution_id "
        "WHERE e.run_id = $1", run_id)
    mapping = await pool.fetchrow("SELECT * FROM pii_mappings WHERE run_id = $1", run_id)
    return {"workflow_runs": run, "node_executions": execution, "review_votes": vote,
            "pii_mappings": mapping}


async def _assert_cleared(pool, run_id: UUID) -> None:
    rows = await _content_of_run(pool, run_id)
    assert rows["workflow_runs"]["content_purged_at"] is not None
    for table, row in rows.items():
        if row is None:
            continue
        for column in INVENTORY[table].content_columns:
            assert _cleared(row[column], table, column), f"{table}.{column} = {row[column]!r}"


async def _purges(pool) -> int:
    return await pool.fetchval(
        "SELECT count(*) FROM audit_log WHERE action = 'retention.purged'")


async def test_content_expires_before_the_trace_and_unfinished_runs_are_left_alone(system_pool):
    scope = await _scope(system_pool, content_days=_CONTENT_DAYS, trace_days=_TRACE_DAYS)
    workflow, node = await _workflow(system_pool, scope)
    voter = await _voter(system_pool)
    content_due = await _run(system_pool, workflow, node, days_ago=15, voter=voter)
    trace_due = await _run(system_pool, workflow, node, days_ago=25, voter=voter, status="failed")
    recent = await _run(system_pool, workflow, node, days_ago=2)
    running = await _run(system_pool, workflow, node, days_ago=400, status="running")

    before = await _purges(system_pool)
    counts = await purge(_database_url(), dry_run=False)

    await _assert_cleared(system_pool, content_due)
    assert await system_pool.fetchval("SELECT 1 FROM workflow_runs WHERE id = $1", content_due)
    assert not await system_pool.fetchval("SELECT 1 FROM workflow_runs WHERE id = $1", trace_due)
    assert not await system_pool.fetchval(
        "SELECT 1 FROM node_executions WHERE run_id = $1", trace_due), "executions went with it"
    assert not await system_pool.fetchval(
        "SELECT 1 FROM pii_mappings WHERE run_id = $1", trace_due), "the mapping went with it"
    assert (await system_pool.fetchrow(
        "SELECT key_id FROM pii_mappings WHERE run_id = $1", content_due))["key_id"] == "k1", (
        "a cleared mapping keeps its row until the run goes")
    for untouched in (recent, running):
        row = await system_pool.fetchrow(
            "SELECT input_data, content_purged_at FROM workflow_runs WHERE id = $1", untouched)
        assert row["content_purged_at"] is None and row["input_data"] != {}

    assert counts["workflow_runs_cleared"] >= 1 and counts["workflow_runs_deleted"] >= 1
    assert await _purges(system_pool) == before + 1

    # A second run leaves a cleared run alone: its mark keeps the first date.
    marked = await system_pool.fetchval(
        "SELECT content_purged_at FROM workflow_runs WHERE id = $1", content_due)
    await purge(_database_url(), dry_run=False)
    assert await system_pool.fetchval(
        "SELECT content_purged_at FROM workflow_runs WHERE id = $1", content_due) == marked
    assert await _purges(system_pool) == before + 2, "every real run is recorded"


async def test_a_run_whose_sub_run_is_kept_longer_is_held(system_pool):
    short = await _scope(system_pool, content_days=_CONTENT_DAYS, trace_days=_TRACE_DAYS)
    unlimited = await _scope(system_pool, content_days=_CONTENT_DAYS, trace_days=None)
    parent_workflow, parent_node = await _workflow(system_pool, short)
    child_workflow, child_node = await _workflow(system_pool, unlimited)
    parent = await _run(system_pool, parent_workflow, parent_node, days_ago=25)
    child = await _run(system_pool, child_workflow, child_node, days_ago=25, parent=parent)

    counts = await purge(_database_url(), dry_run=False)

    assert await system_pool.fetchval("SELECT 1 FROM workflow_runs WHERE id = $1", child)
    assert await system_pool.fetchval("SELECT 1 FROM workflow_runs WHERE id = $1", parent), \
        "deleted from under its sub-run"
    assert counts["workflow_runs_held"] >= 1


async def test_a_scope_period_applies_to_the_scopes_below_it(system_pool):
    parent = await _scope(system_pool, content_days=_CONTENT_DAYS, trace_days=_TRACE_DAYS)
    team = uuid4()
    parent_path = await system_pool.fetchval("SELECT scope_path::text FROM scope_nodes WHERE id = $1",
                                      parent)
    await system_pool.execute(
        "INSERT INTO scope_nodes (id, scope_path, node_type, name, parent_id) "
        "VALUES ($1, $2::ltree, 'team', 'it', $3)",
        team, f"{parent_path}.{scope_label('team', team)}", parent)
    workflow, node = await _workflow(system_pool, team)
    run_id = await _run(system_pool, workflow, node, days_ago=15)

    await purge(_database_url(), dry_run=False)

    await _assert_cleared(system_pool, run_id)


async def test_pilot_runs_and_their_transparency_records(system_pool):
    content_due = await system_pool.fetchval(
        "INSERT INTO pilot_runs (thread_id, input_file, status, result_json, completed_at) "
        "VALUES ($1, 'Arztbrief_Mustermann.pdf', 'complete', '{\"summary\": \"x\"}', "
        "  now() - interval '40 days') RETURNING id", f"it-{uuid4().hex}")
    trace_due = await system_pool.fetchval(
        "INSERT INTO pilot_runs (thread_id, input_file, status, completed_at) "
        "VALUES ($1, 'old.pdf', 'complete', now() - interval '200 days') RETURNING id",
        f"it-{uuid4().hex}")
    await system_pool.execute(
        "INSERT INTO transparency_records (pilot_run_id, model_used, model_disclosure) "
        "VALUES ($1, 'gemma3-4b', 'KI-generiert')", trace_due)

    await purge(_database_url(), dry_run=False)

    row = await system_pool.fetchrow("SELECT * FROM pilot_runs WHERE id = $1", content_due)
    assert row["content_purged_at"] is not None
    for column in INVENTORY["pilot_runs"].content_columns:
        assert _cleared(row[column], "pilot_runs", column), f"pilot_runs.{column}"
    assert not await system_pool.fetchval("SELECT 1 FROM pilot_runs WHERE id = $1", trace_due)
    assert not await system_pool.fetchval(
        "SELECT 1 FROM transparency_records WHERE pilot_run_id = $1", trace_due)


async def test_feedback_by_its_workflow_and_without_one(system_pool, owner_pool):
    scope = await _scope(system_pool, content_days=_CONTENT_DAYS, trace_days=_TRACE_DAYS)
    workflow, _ = await _workflow(system_pool, scope)

    # Written as the migration role: nothing in the application writes feedback
    # yet (CHY-40), so its role may only clear and delete it — which the purge
    # below does as the application role.
    async def feedback(workflow_id, days_ago):
        return await owner_pool.fetchval(
            "INSERT INTO feedback_events (execution_id, node_ref, workflow_id, comment, created_at) "
            "VALUES ('trace', 'c', $1, 'Ms Mustermann was unhappy', "
            "  now() - make_interval(days => $2)) RETURNING id", workflow_id, days_ago)

    cleared = await feedback(workflow, 15)
    deleted = await feedback(workflow, 25)
    unscoped_cleared = await feedback(None, 40)

    await purge(_database_url(), dry_run=False)

    assert await system_pool.fetchval(
        "SELECT comment FROM feedback_events WHERE id = $1", cleared) is None
    assert not await system_pool.fetchval("SELECT 1 FROM feedback_events WHERE id = $1", deleted)
    assert await system_pool.fetchval(
        "SELECT comment FROM feedback_events WHERE id = $1", unscoped_cleared) is None


async def test_a_dry_run_changes_nothing_and_writes_no_audit_entry(system_pool):
    scope = await _scope(system_pool, content_days=_CONTENT_DAYS, trace_days=_TRACE_DAYS)
    workflow, node = await _workflow(system_pool, scope)
    content_due = await _run(system_pool, workflow, node, days_ago=15)
    trace_due = await _run(system_pool, workflow, node, days_ago=25)
    before = await _purges(system_pool)

    counts = await purge(_database_url(), dry_run=True)

    assert counts["workflow_runs_cleared"] >= 1 and counts["workflow_runs_deleted"] >= 1
    assert await system_pool.fetchval(
        "SELECT content_purged_at FROM workflow_runs WHERE id = $1", content_due) is None
    assert await system_pool.fetchval("SELECT 1 FROM workflow_runs WHERE id = $1", trace_due)
    assert await _purges(system_pool) == before


async def test_the_longest_finite_period_does_not_break_the_purge(system_pool):
    """`now() - make_interval(days => keep_days)` must stay inside the timestamp
    range for every period the API accepts — Schemathesis found the field
    unbounded on 05.11.2026."""
    from kernel.schema import MAX_KEEP_DAYS

    scope = await _scope(system_pool, content_days=MAX_KEEP_DAYS, trace_days=MAX_KEEP_DAYS)
    workflow, node = await _workflow(system_pool, scope)
    kept = await _run(system_pool, workflow, node, days_ago=400)

    await purge(_database_url(), dry_run=False)

    assert await system_pool.fetchval(
        "SELECT content_purged_at FROM workflow_runs WHERE id = $1", kept) is None


async def test_a_mapping_is_cleared_whole_never_half(system_pool):
    """A nonce without its ciphertext, or the other way round, would be a
    mapping neither readable nor visibly gone (migration 036, CHY-234)."""
    scope = await _scope(system_pool, content_days=_CONTENT_DAYS, trace_days=_TRACE_DAYS)
    workflow, node = await _workflow(system_pool, scope)
    run_id = await _run(system_pool, workflow, node, days_ago=1)

    with pytest.raises(asyncpg.exceptions.CheckViolationError,
                       match="pii_mappings_sealed_or_cleared"):
        await system_pool.execute(
            "UPDATE pii_mappings SET ciphertext = NULL WHERE run_id = $1", run_id)
