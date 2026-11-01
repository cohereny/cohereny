"""
contexts/repository/store.py

Data access for the repository context — the only place with SQL
(ADR-030, CHY-105).

Twenty statements were spread over four files: thirteen in `api/v1/blocks.py`,
four in `core/compose.py`, and one each in `api/v1/audit.py` and
`api/v1/complete.py` reading a table they do not own. The last two are the
reason criterion 6 of ADR-030 said `context_blocks` was touched in four places:
owning a table means owning the access to it, not only most of it.

`scope_nodes` used to be read from here — the ancestor chain and a join in the
composition query, both knowing the ltree schema of a table this context does
not own. CHY-121 moved the chain to `kernel/scopes.ancestor_chain` and removed
the join outright: the caller already holds the chain, so `scope_path` and
`depth` are a dictionary lookup rather than a second table in the query.
"""
from __future__ import annotations

import json
from uuid import UUID, uuid4

import asyncpg

from contexts.repository.errors import BlockNotStorable
from contexts.repository.schema import COMPOSABLE_BLOCK_TYPES, BlockQuery
from kernel.errors import ScopeNotFound

# "superseded_by_version IS NULL" identifies the current version of a line —
# exactly one row per line carries it (idx_blocks_one_active). It is written out
# in each query rather than kept in a constant: assembling even a constant into
# a query string makes it a built query to any reader and to S608, and the
# finding is worth reserving for the two places where something really varies.


# ── blocks: reading ───────────────────────────────────────────────────────────

async def current_block(conn_or_pool, block_id: UUID):
    """The current version of a line, every column."""
    return await conn_or_pool.fetchrow(
        "SELECT * FROM context_blocks WHERE block_id = $1 "
        "AND superseded_by_version IS NULL",
        block_id,
    )


async def lock_current_block(conn, block_id: UUID):
    """The fields a transition needs, locked for the length of the transaction.

    `content` is among them since CHY-67: an activation check reads it — a
    calibration anchor's reference answer is its content. The direct path
    hands the check a full row (RETURNING *), this path did not, and the
    difference would have surfaced as a KeyError on approval in strict scopes
    only.
    """
    return await conn.fetchrow(
        "SELECT id, block_id, version, status, scope_id, author_id, content, "
        "       governance_key, privacy_impact, locked, block_type, metadata "
        "FROM context_blocks WHERE block_id = $1 "
        "AND superseded_by_version IS NULL FOR UPDATE",
        block_id,
    )


async def lock_all_versions(conn, block_id: UUID) -> list:
    """Every version of a line, newest first, locked.

    The whole line, not just the current row — see block.plan_deletion for what
    went wrong when only the current one was considered.
    """
    return await conn.fetch(
        "SELECT id, version, status, scope_id FROM context_blocks "
        "WHERE block_id = $1 ORDER BY version DESC FOR UPDATE",
        block_id,
    )


async def is_author(pool, block_id: UUID, actor_id: UUID) -> bool:
    """Whether this actor wrote any version of the line.

    Read by the audit endpoint to decide who may see a block's entries. It used
    to ask `context_blocks` itself, from a module that owns `audit_log`.
    """
    return bool(await pool.fetchval(
        "SELECT EXISTS (SELECT 1 FROM context_blocks "
        "WHERE block_id = $1 AND author_id = $2)",
        block_id, actor_id,
    ))


async def all_versions(pool, block_id: UUID) -> list:
    return await pool.fetch(
        "SELECT id, version, superseded_by_version, status, author_id, created_at "
        "FROM context_blocks WHERE block_id = $1 ORDER BY version DESC",
        block_id,
    )


async def count_and_page(pool, query: BlockQuery, statuses: tuple[str, ...]):
    """Current versions matching the filter, plus the total for the pager.

    The status set is always a positive list — the caller resolves "no filter"
    to block.LISTED_BY_DEFAULT before getting here, so this function never has
    to decide what a missing filter means.

    The WHERE clause is assembled, which is what S608 flags. Every *value* is a
    bound parameter; what the f-string interpolates is a placeholder number and
    a column name this module chose.

    The finding stays counted rather than silenced. A `# noqa: S608` stood here
    briefly and was the worse of two options twice over: it contradicted the
    sentence above it, and it does not even remove a number — S is ratcheted
    rather than enforced, so the directive is unused in the default run and
    trades one S608 for one RUF100. An assembled query deserves a second look
    even when this one is sound.
    """
    conditions = ["superseded_by_version IS NULL"]
    args: list = []
    if query.scope_id is not None:
        args.append(query.scope_id)
        conditions.append(f"scope_id = ${len(args)}")
    if query.block_type is not None:
        args.append(query.block_type)
        conditions.append(f"block_type = ${len(args)}")
    args.append(list(statuses))
    conditions.append(f"status = ANY(${len(args)}::text[])")

    where = " AND ".join(conditions)
    total = await pool.fetchval(
        f"SELECT COUNT(*) FROM context_blocks WHERE {where}", *args
    )
    args.extend([query.page_size, (query.page - 1) * query.page_size])
    rows = await pool.fetch(
        f"SELECT * FROM context_blocks WHERE {where} "
        f"ORDER BY created_at DESC LIMIT ${len(args) - 1} OFFSET ${len(args)}",
        *args,
    )
    return rows, total


async def active_blocks_by_id(pool, block_ids: list[UUID]) -> list:
    """The newest active version of each named block, in composition order.

    Read by /v1/complete for an explicit block list, as opposed to composing a
    whole scope chain. `scope_id` and `block_type` are selected so the caller
    can refuse blocks outside the request's scope chain (CHY-196).
    """
    return await pool.fetch(
        """
        SELECT block_id, version, content, scope_id, block_type FROM (
            SELECT DISTINCT ON (block_id)
                   block_id, version, content, scope_id, block_type, created_at
            FROM context_blocks
            WHERE block_id = ANY($1) AND status = 'active'
            ORDER BY block_id, version DESC
        ) x ORDER BY x.block_type, x.created_at
        """,
        block_ids,
    )



async def newest_active_version(conn, block_id: UUID):
    """The newest active version of one line, whole — the row it composes as.

    Not `current_block`: the current version may be a draft in review while
    its predecessor is still the one in use, and what is promoted is what is
    in use (ADR-042).
    """
    return await conn.fetchrow(
        "SELECT * FROM context_blocks WHERE block_id = $1 AND status = 'active' "
        "ORDER BY version DESC LIMIT 1",
        block_id,
    )

# ── blocks: writing ───────────────────────────────────────────────────────────

async def insert_block(pool, body, author_id: UUID):
    """Insert version 1 of a new line from a BlockCreate.

    The three governance columns are written here rather than patched on
    afterwards (CHY-62): a block that reaches `draft` without its claim would
    be reviewable in a state it never meant to be in, and the CHECK that keeps
    the three coherent would have nothing to check.

    The table's refusals are translated here, where the constraint names are
    known (CHY-165): an unknown scope is a 404, an incoherent claim a 422.
    Both used to leave as a 500 for a mistake that was the caller's.
    """
    try:
        return await _insert_block(pool, body, author_id)
    except asyncpg.exceptions.ForeignKeyViolationError as exc:
        if exc.constraint_name == "context_blocks_scope_id_fkey":
            raise ScopeNotFound(body.scope_id) from exc
        raise
    except asyncpg.exceptions.CheckViolationError as exc:
        raise BlockNotStorable(exc.constraint_name or "an unnamed constraint") from exc


async def _insert_block(pool, body, author_id: UUID):
    return await pool.fetchrow(
        "INSERT INTO context_blocks "
        "(id, block_id, version, scope_id, block_type, content, metadata, tags, author_id, status, "
        " governance_key, privacy_impact, locked) "
        "VALUES ($1, $2, 1, $3, $4, $5, $6::text::jsonb, $7, $8, 'draft', $9, $10, $11) RETURNING *",
        uuid4(), uuid4(), body.scope_id, body.block_type, body.content,
        json.dumps(body.metadata), body.tags, author_id,
        body.governance_key, body.privacy_impact, body.locked,
    )


async def set_status(conn, row_id: UUID, to_status: str):
    return await conn.fetchrow(
        "UPDATE context_blocks SET status = $1 WHERE id = $2 RETURNING *",
        to_status, row_id,
    )


async def deprecate_active(conn, block_id: UUID) -> None:
    await conn.execute(
        "UPDATE context_blocks SET status = 'deprecated' "
        "WHERE block_id = $1 AND status = 'active'",
        block_id,
    )


async def edit_draft(pool, block_id: UUID, updates: dict):
    """In-place edit of a draft. Empty updates never reach here.

    Translates an incoherent governance claim the way `insert_block` does. It
    was left out when CHY-165 covered the insert, and a PATCH that set
    `privacy_impact` without a `governance_key` still answered 500 — found by
    Schemathesis on 25.09.2026, on the first run with everything merged.
    """
    try:
        return await _edit_draft(pool, block_id, updates)
    except asyncpg.exceptions.CheckViolationError as exc:
        raise BlockNotStorable(exc.constraint_name or "an unnamed constraint") from exc


async def _edit_draft(pool, block_id: UUID, updates: dict):
    cols = list(updates)
    vals = [json.dumps(v) if c == "metadata" else v for c, v in updates.items()]
    # metadata goes as JSON text with an explicit cast, like every other JSONB
    # parameter (CHY-164). Without the cast Postgres infers `jsonb` from the
    # column, the pool's codec encodes the already-serialised string a second
    # time, and a JSON string lands where an object belongs.
    set_clause = ", ".join(
        f"{c} = ${i + 1}::text::jsonb" if c == "metadata" else f"{c} = ${i + 1}"
        for i, c in enumerate(cols)
    )
    vals.append(block_id)
    return await pool.fetchrow(
        f"UPDATE context_blocks SET {set_clause} "
        f"WHERE block_id = ${len(vals)} "
        "AND superseded_by_version IS NULL RETURNING *",
        *vals,
    )


async def supersede(pool, block_id: UUID, fields: dict, *,
                    author_id: UUID | None, editor_id: UUID | None,
                    change_reason: str | None):
    """Publish a new version and retire the previous one, atomically.

    The line keeps its author; the person making this edit is recorded
    separately. Passing one value for both attributed every foreign edit to the
    previous author (migration 013).

    block_type is deliberately not a parameter: it is an identity column,
    immutable across the version line (migration 012).
    """
    return await pool.fetchrow(
        "SELECT * FROM cc_supersede_block($1, $2, $3::text::jsonb, $4::text[], $5, $6, $7)",
        block_id, fields["content"], json.dumps(fields["metadata"]), fields["tags"],
        author_id, editor_id, change_reason,
    )


async def active_blocks_in_scopes(pool, scope_ids: list[UUID],
                                  block_types: list[str] | None) -> list:
    """The newest approved version of each block across a scope chain.

    Only `active`. A draft is somebody's work in progress, a pending_review
    block is waiting for a decision, a rejected one was turned down and a
    deprecated one retired on purpose — feeding any of them into a live prompt
    would make the composed context depend on what happens to be lying around.

    Why "newest active version" and not "the current version, if it is active":
      The current version is the one with superseded_by_version IS NULL, and
      editing a block moves that marker to a fresh draft. Filtering on both
      therefore dropped a block out of every composition the moment somebody
      touched it, for as long as the review took. With an hour of cache TTL
      that showed up as an hour of stale content followed by a silent
      disappearance, with no error anywhere and enough delay that nobody would
      connect the two.

      The consequence is worth stating: a block can be composed while a newer
      version of it exists. That is the point — the newer one is unreviewed.

    Ordered by block_id here, arranged into composition order by the caller.
    The query used to JOIN scope_nodes for `scope_path` and `depth` and sort by
    them; CHY-121 dropped the join, because the caller already holds the
    ancestor chain those two values come from. One table less, one ownership
    boundary less, and the same answer.

    DISTINCT ON needs its expression first in ORDER BY — the inner sort picks
    the version, and only that is the query's job now.
    """
    # Always filtered by type, and that is the change CHY-87 made. Before it,
    # an absent filter meant "everything active in the scope" — correct while
    # every block type was prompt material, and a leak the moment one is not
    # (ADR-037). Asking for nothing in particular now means asking for what may
    # be composed, never for everything.
    wanted = (set(block_types) & COMPOSABLE_BLOCK_TYPES) if block_types \
        else set(COMPOSABLE_BLOCK_TYPES)
    args: list = [scope_ids, sorted(wanted)]
    where = [
        "scope_id = ANY($1::uuid[])",
        "status = 'active'",
        "block_type = ANY($2::text[])",
    ]

    return await pool.fetch(
        "SELECT DISTINCT ON (block_id) "
        "       block_id, version, block_type, content, scope_id, tags, created_at, "
        "       governance_key, privacy_impact, locked "
        "FROM context_blocks "
        f"WHERE {' AND '.join(where)} "
        "ORDER BY block_id, version DESC",
        *args,
    )


async def active_governance_claims(pool, scope_ids: list[UUID],
                                  governance_key: str,
                                  exclude_block_id: UUID | None = None) -> list:
    """Active blocks in these scopes that speak to one governance key (CHY-62).

    Only the named key, because the ratchet compares like with like — loading
    every claim in the chain to filter in Python would read rows that can never
    matter and grow with the vocabulary rather than with the question.

    `exclude_block_id` leaves the block being judged out of its own comparison.
    Without it, re-approving a block after a rejection would find its own
    predecessor version — still active, by the design documented in
    active_blocks_in_scopes — and refuse it for contradicting itself.

    Every version, not DISTINCT ON: several versions of a line can be active at
    once while a successor is in review, and a locked claim in any of them is
    still in force. Taking only the newest would let an unlocked successor hide
    a locked predecessor that is being composed right now.
    """
    # Written out rather than assembled from a list of conditions. The two
    # neighbouring queries build their WHERE clause because the block_type
    # filter genuinely varies; here only one value is optional, and the
    # NULL-or-match idiom expresses that without turning a constant query into
    # a built one — which is a third S608 for no gain in flexibility.
    return await pool.fetch(
        "SELECT block_id, scope_id, governance_key, privacy_impact, locked "
        "FROM context_blocks "
        "WHERE scope_id = ANY($1::uuid[]) "
        "  AND governance_key = $2 "
        "  AND status = 'active' "
        "  AND ($3::uuid IS NULL OR block_id <> $3)",
        scope_ids, governance_key, exclude_block_id,
    )


async def known_governance_keys(pool) -> tuple[str, ...]:
    """The vocabulary, for a refusal that names the alternatives.

    The foreign key already refuses an unknown value; this exists so the
    refusal can be a sentence instead of a constraint violation surfacing as a
    500 — the database is the wall, not the explanation.
    """
    rows = await pool.fetch("SELECT key FROM governance_keys ORDER BY key")
    return tuple(r["key"] for r in rows)


def row_to_dict(row: asyncpg.Record | dict) -> dict:
    """A row with its JSONB column parsed.

    asyncpg returns JSONB as str on some versions and as dict on others, and
    every reader of a block row had its own two-line workaround for that.
    """
    d = dict(row)
    if isinstance(d.get("metadata"), str):
        d["metadata"] = json.loads(d["metadata"])
    return d


async def active_routing_rules(pool, scope_ids: list[UUID]) -> list:
    """Approved routing rules across a scope chain, deepest scope last.

    A separate query and not a `block_types` filter on the composition path,
    and that separation is the point of ADR-037: composition is for prompt
    material, and a routing rule is configuration. Two ways into one table,
    asking different questions, with no way to confuse them — the composition
    query cannot return these rows even when asked.

    Only `active`, for the same reason composition takes only active blocks: a
    draft is somebody's work in progress and a rejected rule was turned down.
    Routing on either would mean routing on what happens to be lying around.

    Ordering is the caller's answer to "which scope wins". The rows come back
    by scope and then by version so the caller can walk the chain; deciding
    precedence here would put a routing rule into a module that owns storage.
    """
    return await pool.fetch(
        "SELECT DISTINCT ON (block_id) "
        "       block_id, version, scope_id, content, metadata, "
        "       governance_key, privacy_impact, locked, created_at "
        "FROM context_blocks "
        "WHERE scope_id = ANY($1::uuid[]) "
        "  AND status = 'active' "
        "  AND block_type = 'routing_rule' "
        "ORDER BY block_id, version DESC",
        scope_ids,
    )
