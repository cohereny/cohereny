"""
contexts/pilots/store.py

Data access for the pilots context — the only place with SQL (ADR-030, CHY-104).

Twenty statements were spread across api/v1/pilots.py, several of them inside
branches of a function that also framed SSE and chose status codes. The
architecture ratchet counts SQL below the transport layer and it was right to;
collecting them here is what makes that count go to zero for this context.

Two families, and the difference is not cosmetic:

  * request scope — an asyncpg pool, injected per request
  * thread scope  — a DSN, one connection per statement

The second exists because the background thread runs its own event loop
(execution.py) and an asyncpg pool belongs to the loop that created it. Sharing
one across loops is the kind of bug that only shows up under load.
"""
from __future__ import annotations

import json
from datetime import datetime, UTC
from typing import Any
from uuid import UUID

import asyncpg

_RUN_COLUMNS = (
    "id, thread_id, pilot_name, input_file, status, started_at, "
    "completed_at, error_message, created_at"
)


# ── request scope: pool ───────────────────────────────────────────────────────

async def recent_runs(pool, limit: int = 50) -> list:
    return await pool.fetch(
        f"SELECT {_RUN_COLUMNS} FROM pilot_runs ORDER BY created_at DESC LIMIT {int(limit)}"
    )


async def full_run(pool, run_id: UUID):
    """Every column — the detail endpoint returns the whole row."""
    return await pool.fetchrow("SELECT * FROM pilot_runs WHERE id=$1", run_id)


async def run_status_and_owner(pool, run_id: UUID):
    return await pool.fetchrow(
        "SELECT status, actor_id FROM pilot_runs WHERE id=$1", run_id
    )


async def run_stream_state(pool, run_id: UUID):
    """What the SSE endpoint needs to serve a run this process does not know."""
    return await pool.fetchrow(
        "SELECT status, result_json, events_json FROM pilot_runs WHERE id=$1", run_id
    )


async def run_exists(pool, run_id: UUID) -> bool:
    return await pool.fetchrow("SELECT id FROM pilot_runs WHERE id=$1", run_id) is not None


# ── the question bank ─────────────────────────────────────────────────────────
#
# The column list is written out in each query rather than kept in a constant.
# Interpolating even a constant into a query string makes it a built query to
# any reader and to S608, and the finding is worth reserving for the places
# where something really varies. Same call as in the repository store.

async def questions(pool, *, topic: str | None, limit: int, offset: int) -> list:
    """A page of the bank, newest first, optionally narrowed to one topic.

    Read through this context since CHY-122: `question_bank` belongs to
    `pilots`, and api/v1/questions.py was still querying it directly. Missed at
    CHY-104, which named the table as owned here — the same oversight that
    CHY-105 caught for `context_blocks` and fixed there.
    """
    if topic:
        return await pool.fetch(
            "SELECT id, question, topic, source, difficulty FROM question_bank "
            "WHERE topic = $1 ORDER BY created_at DESC LIMIT $2 OFFSET $3",
            topic, limit, offset,
        )
    return await pool.fetch(
        "SELECT id, question, topic, source, difficulty FROM question_bank "
        "ORDER BY created_at DESC LIMIT $1 OFFSET $2",
        limit, offset,
    )


async def question(pool, question_id: UUID):
    return await pool.fetchrow(
        "SELECT id, question, topic, source, difficulty FROM question_bank "
        "WHERE id = $1",
        question_id,
    )


async def coverage_rows(pool, run_id: UUID) -> list:
    async with pool.acquire() as conn:
        return await conn.fetch(
            "SELECT rc.covered, qb.question "
            "FROM run_coverage rc "
            "JOIN question_bank qb ON rc.question_id = qb.id "
            "WHERE rc.run_id = $1",
            run_id,
        )


async def insert_run(conn, *, thread_id: str, pilot_name: str,
                     input_file: str, actor_id: UUID,
                     run_config: dict, effective_config: dict):
    """Insert inside a caller-owned transaction, so the audit entry shares it.

    effective_config is written here and not at the transition to running,
    which is what CHY-42 originally described. The composition is decided at
    this moment — the values are already in the RunSpec handed to the thread —
    and writing it later would leave a window in which a run exists with no
    record of what it was going to use, plus a second write path into a column
    a trigger allows to be written exactly once.
    """
    return await conn.fetchrow(
        "INSERT INTO pilot_runs "
        "(thread_id, pilot_name, input_file, actor_id, run_config, effective_config) "
        "VALUES ($1, $2, $3, $4, $5::text::jsonb, $6::text::jsonb) RETURNING *",
        thread_id, pilot_name, input_file, actor_id,
        json.dumps(run_config), json.dumps(effective_config),
    )


async def delete_run(conn, run_id: UUID) -> None:
    """Delete inside a caller-owned transaction.

    Raises asyncpg.ForeignKeyViolationError when transparency_records still
    reference the run — migration 015 made that constraint RESTRICT. The service
    translates it; no "does it have records" query first, because that would be
    a second round trip and a check-then-act a concurrent insert can slip past.
    """
    await conn.execute("DELETE FROM pilot_runs WHERE id=$1", run_id)


async def clear_oversight_gate(conn, run_id: UUID) -> None:
    """Return a gated run to running without recording a decision.

    Used when a *cancellation* releases the gate. Writing "rejected" into
    oversight_decision here would manufacture an Art. 14 record for a decision
    nobody made.
    """
    await conn.execute(
        "UPDATE pilot_runs SET status='running', oversight_pending=FALSE WHERE id=$1",
        run_id,
    )


async def record_oversight(conn, run_id: UUID, *, decision: str, decided_by: UUID) -> None:
    """Decision, approver and time in one statement.

    Migration 014 refuses the UPDATE if the approver or the time is missing —
    recording that "a human decided" without naming the human is not evidence
    of oversight (CHY-88).
    """
    await conn.execute(
        "UPDATE pilot_runs SET status='running', oversight_pending=FALSE, "
        "oversight_decision=$1, oversight_by=$2, oversight_at=now() WHERE id=$3",
        decision, decided_by, run_id,
    )


async def mark_failed(conn, run_id: UUID, message: str) -> None:
    await conn.execute(
        "UPDATE pilot_runs SET status='failed', completed_at=$1, error_message=$2 WHERE id=$3",
        datetime.now(UTC), message, run_id,
    )


# ── thread scope: DSN, one connection per statement ───────────────────────────

# ── workflow configuration (CHY-42) ───────────────────────────────────────────

async def workflow_config(pool, pilot_name: str):
    """The stored configuration of one pilot, or None."""
    return await pool.fetchrow(
        "SELECT pilot_name, version, model_map, params, updated_at "
        "FROM workflow_configs WHERE pilot_name = $1",
        pilot_name,
    )


async def all_workflow_configs(pool) -> list:
    return await pool.fetch(
        "SELECT pilot_name, version, model_map, params, updated_at "
        "FROM workflow_configs ORDER BY pilot_name"
    )


async def upsert_workflow_config(pool, pilot_name: str, *,
                                 model_map: dict | None, params: dict | None):
    """Replace the named sections, leave the others, bump the version.

    COALESCE on the excluded value rather than two statements: a section the
    caller did not send keeps what is stored, and the whole thing stays one
    round trip and one row lock. Sending `{}` therefore *clears* a section,
    which is the difference between "leave it alone" and "make it empty" —
    a PUT needs both, and a merge on the server would offer neither.

    The version is bumped here rather than by the caller. It is a property of
    the row having changed, and a caller that computes it can compute it wrong.
    """
    return await pool.fetchrow(
        "INSERT INTO workflow_configs (pilot_name, model_map, params) "
        "VALUES ($1, COALESCE($2::text::jsonb, '{}'::jsonb), COALESCE($3::text::jsonb, '{}'::jsonb)) "
        # $2/$3 directly, not EXCLUDED: EXCLUDED carries the value the INSERT
        # would have written, and that has already been COALESCEd to '{}'. It
        # is therefore never NULL here, and "leave this section alone" would
        # have cleared it instead.
        "ON CONFLICT (pilot_name) DO UPDATE SET "
        "  model_map  = COALESCE($2::text::jsonb, workflow_configs.model_map), "
        "  params     = COALESCE($3::text::jsonb, workflow_configs.params), "
        "  version    = workflow_configs.version + 1, "
        "  updated_at = now() "
        "RETURNING pilot_name, version, model_map, params, updated_at",
        pilot_name,
        json.dumps(model_map) if model_map is not None else None,
        json.dumps(params) if params is not None else None,
    )


async def _execute(dsn: str, sql: str, *args: Any) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(sql, *args)
    finally:
        await conn.close()


async def t_mark_running(dsn: str, run_id: UUID) -> None:
    await _execute(
        dsn, "UPDATE pilot_runs SET status='running', started_at=$1 WHERE id=$2",
        datetime.now(UTC), run_id,
    )


async def t_mark_awaiting_oversight(dsn: str, run_id: UUID) -> None:
    await _execute(
        dsn,
        "UPDATE pilot_runs SET status='awaiting_oversight', oversight_pending=TRUE WHERE id=$1",
        run_id,
    )


async def t_mark_complete(dsn: str, run_id: UUID, result: dict) -> None:
    await _execute(
        dsn,
        "UPDATE pilot_runs SET status='complete', completed_at=$1, "
        "result_json=$2::text::jsonb WHERE id=$3",
        datetime.now(UTC), json.dumps(result, ensure_ascii=False), run_id,
    )


async def t_mark_rejected(dsn: str, run_id: UUID, *, outcome: str,
                          error_text: str, result: dict) -> None:
    await _execute(
        dsn,
        "UPDATE pilot_runs SET status='rejected', completed_at=$1, "
        "oversight_pending=FALSE, oversight_decision=$2, "
        "error_message=$3, result_json=$4::text::jsonb WHERE id=$5",
        datetime.now(UTC), outcome, error_text,
        json.dumps(result, ensure_ascii=False), run_id,
    )


async def t_mark_failed(dsn: str, run_id: UUID, error_text: str) -> None:
    await _execute(
        dsn,
        "UPDATE pilot_runs SET status='failed', completed_at=$1, error_message=$2 WHERE id=$3",
        datetime.now(UTC), error_text, run_id,
    )


async def t_store_events(dsn: str, run_id: UUID, events: list[dict]) -> None:
    await _execute(
        dsn, "UPDATE pilot_runs SET events_json=$1::text::jsonb WHERE id=$2",
        json.dumps(events, ensure_ascii=False), run_id,
    )


async def t_insert_transparency_record(dsn: str, run_id: UUID, *,
                                       model_used: str, disclosure: str) -> None:
    """EU AI Act Art. 50 record for a completed run."""
    await _execute(
        dsn,
        "INSERT INTO transparency_records (pilot_run_id, model_used, model_disclosure) "
        "VALUES ($1, $2, $3)",
        run_id, model_used, disclosure,
    )
