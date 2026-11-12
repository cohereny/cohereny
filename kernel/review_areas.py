"""
kernel/review_areas.py

The competence order of a scope: which areas vote at a gate, and who belongs
to them (CHY-180, ADR-050).

In the kernel, not in a context, for the reason scope memberships are: an area
is not content and not a step of a workflow, it is part of *who* decides in an
organisation. Orchestration refers to it without owning it — a vote in
`review_votes` points at a membership here by foreign key.

What the database holds, not this module (migration 043):

- one vote per area at a gate execution (UNIQUE)
- a vote only from a member (FOREIGN KEY)
- only from a **current** member (trigger). Membership is revoked with a date,
  never deleted, so a vote cast before stays provable (Thomas, 09.11.2026)

An area applies to the scope that defines it and to every scope below, like a
membership. A gate names areas; a name resolves to the nearest definition on
the chain of the workflow's scope, so a team may define its own `markt` and
its workflows then mean that one.

Maintained by `admin`, as memberships are, and audited: who was added, revoked,
or added again, by whom.
"""
from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

import asyncpg

from kernel import scopes
from kernel.access import require
from kernel.audit import (
    ACTION_REVIEW_AREA_CREATED,
    ACTION_REVIEW_AREA_MEMBER_ADDED,
    ACTION_REVIEW_AREA_MEMBER_REVOKED,
    target_ref,
    write_audit,
)
from kernel.errors import (
    ActorNotFound,
    AreaMembershipExists,
    AreaMembershipNotFound,
    ReviewAreaExists,
    ReviewAreaNotFound,
    ScopeNotFound,
)
from kernel.identity import Caller, resolve_actor
from kernel.schema import ReviewArea, ReviewAreaCreate, ReviewAreaMember, ReviewAreaMemberCreate


@dataclass(frozen=True)
class Resolved:
    """An area a gate names, as found on the workflow's scope chain."""

    id: UUID
    scope_id: UUID
    name: str
    #: Current members, revoked ones not counted. Activation refuses an area
    #: nobody can vote for.
    members: int


# ── reading ───────────────────────────────────────────────────────────────────

async def list_areas(pool, caller: Caller, scope_id: UUID) -> list[ReviewArea]:
    """The areas this scope defines, each with its members — revoked ones too.

    Only those defined here, not the inherited ones: this is where the competence
    order is maintained, and an inherited area is maintained where it is defined.
    Any role reads it, as the scope tree; an account that is gone does not.
    """
    require(await resolve_actor(pool, caller), "consumer")
    if not await scopes.exists(pool, scope_id):
        raise ScopeNotFound(scope_id)
    areas = await pool.fetch(
        "SELECT id, scope_id, name, created_at, created_by FROM review_areas "
        "WHERE scope_id = $1 ORDER BY name",
        scope_id,
    )
    members = await pool.fetch(
        "SELECT area_id, actor_id, granted_at, granted_by, revoked_at, revoked_by "
        "FROM review_area_members WHERE area_id = ANY($1::uuid[]) "
        "ORDER BY granted_at",
        [a["id"] for a in areas],
    )
    by_area: dict[UUID, list[ReviewAreaMember]] = {}
    for m in members:
        by_area.setdefault(m["area_id"], []).append(
            ReviewAreaMember(**{k: m[k] for k in ReviewAreaMember.model_fields}))
    return [ReviewArea(**dict(a), members=by_area.get(a["id"], [])) for a in areas]


async def resolve(conn, scope_id: UUID, names: list[str]) -> dict[str, Resolved]:
    """The areas of these names that apply in this scope, nearest definition first.

    A name that resolves to nothing is missing from the result; the caller says
    what that means — activation refuses it, a vote cannot be cast for it.

    The chain comes from kernel/scopes.py, which alone reads `scope_nodes`.
    """
    depth = {row["id"]: row["depth"] for row in await scopes.ancestor_chain(conn, scope_id)}
    rows = await conn.fetch(
        "SELECT a.id, a.scope_id, a.name, "
        "       (SELECT count(*) FROM review_area_members m "
        "        WHERE m.area_id = a.id AND m.revoked_at IS NULL) AS members "
        "FROM review_areas a "
        "WHERE a.name = ANY($2::text[]) AND a.scope_id = ANY($1::uuid[])",
        list(depth), list(names),
    )
    nearest: dict[str, Resolved] = {}
    for r in sorted(rows, key=lambda r: depth[r["scope_id"]]):
        nearest[r["name"]] = Resolved(r["id"], r["scope_id"], r["name"], r["members"])
    return nearest


async def current_areas(conn, actor_id: UUID, area_ids: list[UUID]) -> frozenset[UUID]:
    """Of these areas, the ones this actor currently belongs to."""
    rows = await conn.fetch(
        "SELECT area_id FROM review_area_members "
        "WHERE actor_id = $1 AND area_id = ANY($2::uuid[]) AND revoked_at IS NULL",
        actor_id, list(area_ids),
    )
    return frozenset(r["area_id"] for r in rows)


# ── writing ───────────────────────────────────────────────────────────────────

async def _area_on(conn, scope_id: UUID, name: str) -> UUID:
    """The area of this name defined on exactly this scope."""
    if not await scopes.exists(conn, scope_id):
        raise ScopeNotFound(scope_id)
    area_id = await conn.fetchval(
        "SELECT id FROM review_areas WHERE scope_id = $1 AND name = $2", scope_id, name)
    if area_id is None:
        raise ReviewAreaNotFound(scope_id, name)
    return area_id


async def create_area(pool, caller: Caller, scope_id: UUID,
                      body: ReviewAreaCreate) -> ReviewArea:
    """Define an area on this scope. Admin only, audited."""
    creator = await resolve_actor(pool, caller)
    require(creator, "admin")

    async with pool.acquire() as conn, conn.transaction():
        if not await scopes.exists(conn, scope_id):
            raise ScopeNotFound(scope_id)
        try:
            row = await conn.fetchrow(
                "INSERT INTO review_areas (scope_id, name, created_by) VALUES ($1, $2, $3) "
                "RETURNING id, scope_id, name, created_at, created_by",
                scope_id, body.name, creator.id,
            )
        except asyncpg.UniqueViolationError:
            # UNIQUE (scope_id, name), migration 043 — the rule, translated.
            raise ReviewAreaExists(body.name) from None
        await write_audit(
            conn, action=ACTION_REVIEW_AREA_CREATED, actor_id=creator.id,
            target=target_ref("scope", scope_id),
            payload={"area": body.name, "area_id": str(row["id"])},
        )
    return ReviewArea(**dict(row))


async def add_member(pool, caller: Caller, scope_id: UUID, name: str,
                     body: ReviewAreaMemberCreate) -> ReviewAreaMember:
    """Add an actor to an area of this scope, or again after a revocation.

    Added again, the same row is current again, with the new date and granter;
    the earlier period stays in the audit log. A current member is refused:
    adding twice usually means the wrong person or the wrong area.
    """
    granter = await resolve_actor(pool, caller)
    require(granter, "admin")

    async with pool.acquire() as conn, conn.transaction():
        area_id = await _area_on(conn, scope_id, name)
        if not await conn.fetchval("SELECT 1 FROM actors WHERE id = $1", body.actor_id):
            raise ActorNotFound
        # The conflicting row is updated only when it is revoked; a current one
        # makes the statement return nothing. `xmax = 0` tells an insert from
        # an update in the same statement.
        row = await conn.fetchrow(
            "INSERT INTO review_area_members (area_id, actor_id, granted_by) "
            "VALUES ($1, $2, $3) "
            "ON CONFLICT (area_id, actor_id) DO UPDATE "
            "  SET granted_at = now(), granted_by = EXCLUDED.granted_by, "
            "      revoked_at = NULL, revoked_by = NULL "
            "  WHERE review_area_members.revoked_at IS NOT NULL "
            "RETURNING actor_id, granted_at, granted_by, revoked_at, revoked_by, "
            "          (xmax = 0) AS inserted",
            area_id, body.actor_id, granter.id,
        )
        if row is None:
            raise AreaMembershipExists(body.actor_id, name)
        await write_audit(
            conn, action=ACTION_REVIEW_AREA_MEMBER_ADDED, actor_id=granter.id,
            target=target_ref("scope", scope_id),
            payload={"area": name, "member_id": str(body.actor_id),
                     "again": not row["inserted"]},
        )
    return ReviewAreaMember(**{k: row[k] for k in ReviewAreaMember.model_fields})


async def revoke_member(pool, caller: Caller, scope_id: UUID, name: str,
                        actor_id: UUID) -> ReviewAreaMember:
    """Revoke a membership. The row stays, with the date; new votes are refused.

    Effective at once, also for a gate waiting right now: a vote cast before the
    revocation counts, one after it does not — the trigger of migration 043
    checks at the moment the vote is written.
    """
    revoker = await resolve_actor(pool, caller)
    require(revoker, "admin")

    async with pool.acquire() as conn, conn.transaction():
        area_id = await _area_on(conn, scope_id, name)
        row = await conn.fetchrow(
            "UPDATE review_area_members SET revoked_at = now(), revoked_by = $3 "
            "WHERE area_id = $1 AND actor_id = $2 AND revoked_at IS NULL "
            "RETURNING actor_id, granted_at, granted_by, revoked_at, revoked_by",
            area_id, actor_id, revoker.id,
        )
        if row is None:
            raise AreaMembershipNotFound(actor_id, name)
        await write_audit(
            conn, action=ACTION_REVIEW_AREA_MEMBER_REVOKED, actor_id=revoker.id,
            target=target_ref("scope", scope_id),
            payload={"area": name, "member_id": str(actor_id)},
        )
    return ReviewAreaMember(**dict(row))
