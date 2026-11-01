"""
kernel/identity.py

Who is calling, and how a caller becomes an actor (ADR-030, CHY-120).

Two things live here because they are the same subject:

  Caller             what the kernel needs to know about whoever is asking
  the lifecycle      resolving that to an actors row, provisioning it on first
                     sight, and keeping the role in step with the IdP

CHY-101 put only the protocol here and left the lifecycle in access.py, which
made a role check need a database. arc42 §5.7 had described this split since
v2.9; CHY-120 implements it.

Why the protocol rather than a class:
  The kernel says what it needs; whoever calls it brings something that fits.
  `api.deps.CurrentUser` already does, without knowing this file exists and
  without a line changed in it — that is what structural typing buys, and it
  is why the inversion costs no runtime indirection. A CLI caller, a background
  job or the workflow runner (ADR-028) satisfy it just as well. None of them
  should have to construct an HTTP concept to ask whether someone may act.

Why the IdP is the authority for roles:
  `actors.role` is a cached projection of the caller's Authentik groups, not an
  independently administered field. Removing someone from chy-admins therefore
  takes effect on their next request rather than requiring a second, manual
  revocation that somebody has to remember.

  The exception is a token with no groups claim at all. That means the IdP has
  nothing to say — not that the caller belongs to nothing — so the stored role
  stands. `Caller.groups` carries that distinction down here, and
  role_from_groups is where it is honoured.

Anonymized actors and re-provisioning:
  An erased actor has `idp_sub = NULL` (ADR-016, migration 010), so neither the
  lookup nor the ON CONFLICT can match it. A person who exercised Art. 17 and
  then keeps working gets a *new* actor row, and their past actions stay
  severed from it. That is the intended reading — erasure applies to what was
  recorded, not to the right to carry on — and it assumes the erasure is
  accompanied by deprovisioning in Authentik. See kernel/schema.py.

Called by:
  - api/v1/{admin,audit,blocks,complete,pilots,scopes}.py

Dependencies:
  - asyncpg
  - kernel.access (ResolvedActor — the value this produces)
  - kernel.audit  (provisioning and role changes are auditable acts)
  - kernel.errors (domain errors; the HTTP mapping lives in api/main.py)
  - kernel.roles  (the vocabulary and its ranking)
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol, runtime_checkable
from uuid import UUID

import asyncpg

from kernel.access import ResolvedActor, require
from kernel.audit import (
    ACTION_ACTOR_ANONYMIZED,
    ACTION_ACTOR_PROVISIONED,
    ACTION_ACTOR_ROLE_CHANGED,
    target_ref,
    write_audit,
)
from kernel.errors import (
    ActorAnonymized,
    ActorGone,
    ActorNotFound,
    ActorStateInconsistent,
    LastAdminProtected,
    UnknownRole,
)
from kernel.schema import ANONYMIZED_DISPLAY_NAME, AnonymizeResult
from kernel.roles import DEFAULT_ROLE, GROUP_ROLE_MAP, ROLE_NAMES, ROLE_ORDER


@runtime_checkable
class Caller(Protocol):
    """The three facts the kernel needs about whoever is asking."""

    #: Immutable identity from the identity provider. Maps to actors.idp_sub.
    sub: str

    #: Display name. Never empty — actors.display_name is NOT NULL.
    username: str

    #: Group memberships, or None when the token carried no groups claim.
    #:
    #: The distinction matters and must survive down here: None means the IdP
    #: had nothing to say and the stored role stands; an empty list means the
    #: caller was removed from every group, which is a deprovisioning signal.
    #: Collapsing both to [] made an offboarded admin indistinguishable from a
    #: fresh account (CHY-33).
    groups: list[str] | None


def role_from_groups(groups: list[str] | None) -> str | None:
    """Map Authentik groups to a role. None when the token said nothing.

    Returns the *highest* role among the matching groups: being added to a
    group must never take a privilege away, and a caller who is both
    chy-reviewers and chy-admins is an admin.

    An empty list is not the same as None — it means the caller was removed
    from every group, which is a deprovisioning signal and yields the default
    role rather than "leave things as they are".
    """
    if groups is None:
        return None
    matched = [GROUP_ROLE_MAP[g] for g in groups if g in GROUP_ROLE_MAP]
    if not matched:
        return DEFAULT_ROLE
    return max(matched, key=lambda r: ROLE_ORDER.get(r, -1))


async def resolve_actor(pool: asyncpg.Pool, caller: Caller) -> ResolvedActor:
    """Map the token subject to an actor row, creating or updating it as needed.

    Three paths, in order of how often they run:

      known caller, nothing to change   one SELECT, no write
      known caller, groups moved        role refreshed, audited
      first sight                       row provisioned, audited

    The read stays a plain pooled SELECT so the common case costs one query.
    Both write paths take a connection and a transaction, because they write an
    audit entry and that entry must not survive a failed write — `write_audit`
    would happily run on a pooled connection and autocommit, which is the exact
    failure its own docstring warns about.
    """
    row = await pool.fetchrow(
        "SELECT id, role FROM actors WHERE idp_sub = $1 AND is_anonymized = FALSE",
        caller.sub,
    )
    derived = role_from_groups(caller.groups)

    if row is None:
        return await _provision(pool, caller, derived or DEFAULT_ROLE)

    if derived is not None and derived != row["role"]:
        return await _refresh_role(pool, row["id"], derived)

    return ResolvedActor(row["id"], row["role"])


async def _provision(
    pool: asyncpg.Pool, caller: Caller, role: str
) -> ResolvedActor:
    """Create the actor row for a caller seen for the first time.

    ON CONFLICT rather than a second SELECT: two parallel first requests from
    the same person would otherwise race on the unique index on idp_sub. DO
    UPDATE rather than DO NOTHING because DO NOTHING returns no row on
    conflict, which would need the extra round trip anyway.

    The WHERE guard keeps an anonymized row out of reach even if one somehow
    still carried an idp_sub — the CHECK in migration 010 says it cannot, and
    this is the cheap belt to that braces.
    """
    async with pool.acquire() as conn, conn.transaction():
        row = await conn.fetchrow(
            "INSERT INTO actors (idp_sub, display_name, role) VALUES ($1, $2, $3) "
            "ON CONFLICT (idp_sub) DO UPDATE SET display_name = EXCLUDED.display_name "
            "WHERE actors.is_anonymized = FALSE "
            "RETURNING id, role",
            caller.sub,
            # username is preferred_username, falling back to sub in
            # deps.py — never empty, which display_name NOT NULL requires.
            caller.username,
            role,
        )
        if row is None:
            # The conflict target existed but the WHERE excluded it. Only
            # reachable if an anonymized row kept its idp_sub, which the
            # CHECK forbids — so this is a corrupted state, not a race.
            raise ActorStateInconsistent
        await write_audit(
            conn,
            action=ACTION_ACTOR_PROVISIONED,
            actor_id=row["id"],
            target=target_ref("actor", row["id"]),
            payload={"role": row["role"], "source": "oidc"},
        )
    return ResolvedActor(row["id"], row["role"])


async def _refresh_role(
    pool: asyncpg.Pool, actor_id: UUID, derived: str
) -> ResolvedActor:
    """Bring the stored role in line with the caller's current groups.

    FOR UPDATE, and a second comparison inside the lock: a client firing
    several requests in parallel right after a group change would otherwise
    write one identical `actor.role_changed` entry per request, into a table
    that has been append-only since migration 011.
    """
    async with pool.acquire() as conn, conn.transaction():
        current = await conn.fetchrow(
            "SELECT role FROM actors WHERE id = $1 FOR UPDATE", actor_id
        )
        if current is None:
            raise ActorGone
        if current["role"] == derived:
            return ResolvedActor(actor_id, derived)

        await conn.execute(
            "UPDATE actors SET role = $1 WHERE id = $2", derived, actor_id
        )
        await write_audit(
            conn,
            action=ACTION_ACTOR_ROLE_CHANGED,
            actor_id=actor_id,
            target=target_ref("actor", actor_id),
            payload={"from": current["role"], "to": derived, "source": "oidc_groups"},
        )
    return ResolvedActor(actor_id, derived)


# ── erasure (GDPR Art. 17) ────────────────────────────────────────────────────

async def anonymize_actor(pool, caller: Caller, actor_ref: UUID) -> AnonymizeResult:
    """Sever an actor's personal identity and keep the record.

    Idempotent. Repeating the request on an already anonymized actor succeeds
    and reports already_anonymized — a requester must not get an error for
    asking twice, and the second call is not a state change.

    Why pseudonymization instead of deletion (ADR-016, ADR-025): deletion would
    make the audit log inconsistent — actions without an actor. Eleven tables
    carry a foreign key to actors(id); dropping the row would break or orphan
    all of them. Clearing the personal attributes removes the personal data
    without removing the record that an action took place.

    Moved out of api/v1/admin.py with CHY-123. An Art. 17 erasure that can only
    be performed by an HTTP client is a legal obligation with one door — the
    same objection as for the Art. 14 decision in CHY-105.
    """
    performer = await resolve_actor(pool, caller)
    require(performer, "admin")

    async with pool.acquire() as conn, conn.transaction():
        # FOR UPDATE: two concurrent requests for the same actor would
        # otherwise both read "not yet anonymized" and both write a timestamp,
        # and the second would overwrite the real erasure date.
        row = await conn.fetchrow(
            "SELECT id, role, is_anonymized, anonymized_at "
            "FROM actors WHERE id = $1 FOR UPDATE",
            actor_ref,
        )
        if row is None:
            raise ActorNotFound

        if row["is_anonymized"]:
            return AnonymizeResult(
                actor_id=row["id"],
                anonymized_at=row["anonymized_at"],
                already_anonymized=True,
            )

        if row["role"] == "admin":
            remaining = await conn.fetchval(
                "SELECT count(*) FROM actors "
                "WHERE role = 'admin' AND is_anonymized = FALSE AND id <> $1",
                actor_ref,
            )
            if remaining == 0:
                raise LastAdminProtected

        now = datetime.now(UTC)
        await conn.execute(
            "UPDATE actors SET idp_sub = NULL, display_name = $1, "
            "is_anonymized = TRUE, anonymized_at = $2 WHERE id = $3",
            ANONYMIZED_DISPLAY_NAME, now, actor_ref,
        )

        # Inside the transaction on purpose: an erasure without a record, or a
        # record without the erasure, would both make the log wrong. The
        # payload deliberately carries no trace of who the person was — that is
        # what was just removed.
        await write_audit(
            conn,
            action=ACTION_ACTOR_ANONYMIZED,
            actor_id=performer.id,
            target=target_ref("actor", actor_ref),
            payload={"legal_basis": "GDPR Art. 17", "previous_role": row["role"]},
        )

    return AnonymizeResult(actor_id=actor_ref, anonymized_at=now)


# ── bootstrap ─────────────────────────────────────────────────────────────────

async def set_role(dsn: str, idp_sub: str, role: str) -> tuple[UUID, bool]:
    """Set an actor's role directly, creating the actor if the subject is new.

    Returns (actor_id, created).

    This is the way the first admin comes into existence, before anyone has
    ever called the API — so it takes a DSN rather than a request-scoped pool
    and opens its own connection. `cc actor set-role` is the only caller.

    Moved out of cli/main.py with CHY-123. The command held the INSERT, the
    audit entry *and* a hand-written copy of the five role names, three years
    of drift waiting to happen: `kernel/roles.py` already publishes ROLE_NAMES,
    and a second list is a second answer to "which roles exist".

    Refuses to reactivate an anonymized identity. The ON CONFLICT clause is
    guarded by `is_anonymized = FALSE`, so the UPDATE matches nothing and the
    row comes back None — an erased subject must not come back through the
    administrative door (GDPR Art. 17, ADR-025).
    """
    if role not in ROLE_NAMES:
        raise UnknownRole(role)

    conn = await asyncpg.connect(dsn)
    try:
        async with conn.transaction():
            row = await conn.fetchrow(
                "INSERT INTO actors (idp_sub, display_name, role) VALUES ($1, $1, $2) "
                "ON CONFLICT (idp_sub) DO UPDATE SET role = EXCLUDED.role "
                "WHERE actors.is_anonymized = FALSE "
                "RETURNING id, role, (xmax = 0) AS created",
                idp_sub, role,
            )
            if row is None:
                raise ActorAnonymized(idp_sub)

            # Recorded like any other role change: the audit log must not have
            # a hole where the administrative path was used.
            await write_audit(
                conn,
                action=ACTION_ACTOR_ROLE_CHANGED,
                actor_id=row["id"],
                target=target_ref("actor", row["id"]),
                payload={"to": role, "source": "cli"},
            )
        return row["id"], row["created"]
    finally:
        await conn.close()
