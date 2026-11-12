"""
contexts/learning/store.py

The learning context's SQL (ADR-030 criterion 3): benchmark runs and their
measurements (migration 028).

Reading only, for now. The writer comes with the run itself (CHY-68); a write
path without a caller would be code nobody exercises.
"""
from __future__ import annotations

from uuid import UUID



async def count_and_page_runs(pool, *, limit: int, offset: int) -> tuple[list, int]:
    """Runs, newest first, and how many there are in total."""
    rows = await pool.fetch(
        "SELECT id, mode, status, triggered_by, review_needed, started_at, finished_at "
        "FROM benchmark_runs ORDER BY started_at DESC, id DESC LIMIT $1 OFFSET $2",
        limit, offset,
    )
    total = await pool.fetchval("SELECT count(*) FROM benchmark_runs")
    return list(rows), int(total)


async def run(pool, run_id: UUID):
    return await pool.fetchrow(
        "SELECT id, mode, status, triggered_by, review_needed, started_at, finished_at "
        "FROM benchmark_runs WHERE id = $1",
        run_id,
    )


async def measurements_of(pool, run_id: UUID) -> list:
    """Every measurement of a run, oldest first.

    The anchor is named by line and version in the measurement itself, not
    joined from context_blocks: that table belongs to the repository context,
    and the guard test_the_sql_lives_in_one_module holds it there (ADR-030).
    """
    return list(await pool.fetch(
        "SELECT id, anchor_block_id, anchor_version, model, variant, outcome, score, "
        "       scores, output, detail, latency_ms, tokens_in, tokens_out, created_at "
        "FROM benchmark_measurements WHERE run_id = $1 ORDER BY created_at, id",
        run_id,
    ))


# ── retention (CHY-17, ADR-046) ───────────────────────────────────────────────

async def clear_feedback(conn, workflow_ids: list[UUID], keep_days: list[int],
                         installation_days: int | None) -> int:
    """Clear feedback comments past their period; return how many rows changed.

    Feedback belongs to a workflow, and the workflow's scope decides the
    period — handed in as parallel arrays, since the workflow table is the
    orchestration context's (CHY-231). Feedback without a workflow takes the
    installation-wide period. Age counts from the feedback, not from a run:
    `execution_id` is a Langfuse trace id, not a reference to one.
    """
    changed = await conn.fetch(
        "UPDATE feedback_events f SET comment = NULL "
        "FROM unnest($1::uuid[], $2::int[]) AS p(workflow_id, keep_days) "
        "WHERE f.workflow_id = p.workflow_id AND f.comment IS NOT NULL "
        "  AND f.created_at < now() - make_interval(days => p.keep_days) RETURNING f.id",
        workflow_ids, keep_days)
    count = len(changed)
    if installation_days is not None:
        count += len(await conn.fetch(
            "UPDATE feedback_events SET comment = NULL "
            "WHERE workflow_id IS NULL AND comment IS NOT NULL "
            "  AND created_at < now() - make_interval(days => $1) RETURNING id",
            installation_days))
    return count


async def delete_feedback(conn, workflow_ids: list[UUID], keep_days: list[int],
                          installation_days: int | None) -> int:
    """Delete feedback past its trace period; return how many rows went."""
    deleted = await conn.fetch(
        "DELETE FROM feedback_events f USING unnest($1::uuid[], $2::int[]) "
        "  AS p(workflow_id, keep_days) "
        "WHERE f.workflow_id = p.workflow_id "
        "  AND f.created_at < now() - make_interval(days => p.keep_days) RETURNING f.id",
        workflow_ids, keep_days)
    count = len(deleted)
    if installation_days is not None:
        count += len(await conn.fetch(
            "DELETE FROM feedback_events WHERE workflow_id IS NULL "
            "  AND created_at < now() - make_interval(days => $1) RETURNING id",
            installation_days))
    return count
