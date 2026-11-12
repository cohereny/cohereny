"""
kernel/scopes.py

The scope tree — the coordinate system everything else is addressed in
(ADR-030, CHY-121).

`scope_nodes` and `scope_memberships` belong to the kernel: a scope is not
content, it is *where* content lives, and every context refers to one without
owning it. Until CHY-121 two zones read the table directly — `api/v1/scopes.py`
with eight statements, and `contexts/repository/` for the ancestor chain a
composition walks. The second was the more telling: a context that knows the
ltree schema of a table it does not own has a dependency nobody declared.

Paths are ltree. `@>` is the ancestor operator and uses idx_scope_nodes_path
(GiST, CHY-59); `nlevel()` gives the depth, which doubles as the composition
order — an org-level block is laid down before the project-level one that
narrows it.

The path is always derived, never accepted from a caller: `scope_label()` in
kernel/schema.py mirrors the SQL function cc_scope_label(), and two spellings
of one path address different rows in a way that is invisible — the lookup
simply returns nothing.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from uuid import UUID, uuid4

import asyncpg

from kernel.access import require
from kernel.audit import (
    ACTION_SCOPE_CREATED,
    ACTION_SCOPE_DIRECT_CALL_RISK_CLASS_SET,
    ACTION_SCOPE_MEMBER_ADDED,
    target_ref,
    write_audit,
)
from kernel.errors import (
    ActorNotFound,
    MembershipExists,
    ParentScopeNotFound,
    ScopeLoosensPrivacy,
    ScopeLoosensRiskClass,
    ScopeLoosensSampleRate,
    ScopeNotFound,
)
from kernel.identity import Caller, resolve_actor
from kernel.schema import (
    PRIVACY_LEVELS,
    RISK_CLASSES,
    MeOut,
    MembershipOut,
    ScopeCreate,
    ScopeMemberCreate,
    ScopeNode,
    scope_label,
)

# The column list is written out in each query rather than kept in a constant:
# interpolating even a constant into a query string makes it a built query to
# any reader and to S608. Same call as in the two context stores.


# ── reading ───────────────────────────────────────────────────────────────────

async def list_scopes(pool) -> list[ScopeNode]:
    """Every scope, ordered by path so the tree reads top-down.

    No pagination and no filtering by membership. Both are deliberate: the tree
    is a handful of nodes, and filtering by who may see what is the open
    question in CHY-92 — answering it here, quietly, would settle a governance
    decision in a list endpoint.
    """
    rows = await pool.fetch(
        "SELECT id, scope_path::text AS scope_path, node_type, name, description, "
        "parent_id, privacy_level, created_by, created_at "
        "FROM scope_nodes ORDER BY scope_path"
    )
    return [ScopeNode(**dict(r)) for r in rows]


#: The class of a chain on which nobody declared one (CHY-160). The strictest,
#: on purpose: unclassified data goes nowhere a local model does not, and
#: loosening that is a decision somebody writes into a scope — not a default
#: anybody gets by forgetting.
UNCLASSIFIED = "critical"


def effective_privacy_level(chain) -> str:
    """The class of data in the scope at the end of `chain`.

    The strictest class declared anywhere on the chain, root to leaf; the
    UNCLASSIFIED default when nobody declared one. Strictest rather than
    deepest: a project cannot relax what its organisation declared, and taking
    the deepest value would let it — the same direction the privacy ratchet
    enforces for blocks (ADR-017).
    """
    declared = [row["privacy_level"] for row in chain if row["privacy_level"]]
    if not declared:
        return UNCLASSIFIED
    return max(declared, key=PRIVACY_LEVELS.index)


def effective_direct_call_risk_class(chain) -> str | None:
    """How risky direct model calls are in the scope at the end of `chain`.

    The strictest class declared on the chain, for the reason the data class is
    (CHY-199): a project cannot relax what its organisation decided. None when
    nobody classified; the review rule counts that as `high` (ADR-050), and it
    is returned as None rather than `high` so a reader can tell "decided high"
    from "never decided".
    """
    declared = [row["direct_call_risk_class"] for row in chain if row["direct_call_risk_class"]]
    return max(declared, key=RISK_CLASSES.index) if declared else None


def effective_direct_call_sample_rate(chain) -> Decimal | None:
    """The share of direct calls drawn for review in the scope at the end of `chain`.

    The highest share declared on the chain (CHY-269): checking more is the
    stricter setting, and a project cannot check less than its organisation
    decided. Only `minimal` carries a share, the one class that lets a direct
    call through. None when no scope set one — /v1/complete then refuses,
    because a missing share may not read as "no check".
    """
    declared = [row["direct_call_sample_rate"] for row in chain
                if row["direct_call_sample_rate"] is not None]
    return max(declared) if declared else None


@dataclass(frozen=True)
class DirectCallRisk:
    """A scope's classification of direct model calls, and what it adds up to."""

    scope_id: UUID
    #: The scope's own setting; None when it inherits.
    own: str | None
    #: The strictest along the chain; None when nobody classified.
    effective: str | None
    #: The scope's effective data class, the other half of the minimum.
    data_class: str
    #: The share drawn for review, the scope's own; None when it inherits (CHY-269).
    own_sample_rate: Decimal | None = None
    #: The highest share along the chain; None when no scope set one.
    sample_rate: Decimal | None = None


def direct_call_risk(chain) -> DirectCallRisk:
    """Read the classification off a loaded chain; the last row is the scope."""
    scope = chain[-1]
    return DirectCallRisk(
        scope_id=scope["id"],
        own=scope["direct_call_risk_class"],
        effective=effective_direct_call_risk_class(chain),
        data_class=effective_privacy_level(chain),
        own_sample_rate=scope["direct_call_sample_rate"],
        sample_rate=effective_direct_call_sample_rate(chain),
    )


class ReviewMode(StrEnum):
    """How a write to a block in a scope becomes live (ADR-011, ADR-042)."""

    STRICT = "strict"
    """Draft, submit, approval by a reviewer — the lifecycle of CHY-32."""

    DIRECT = "direct"
    """Active at once. The write still passes pending_review, because the
    database refuses draft → active (migration 013), and it is still audited
    and still judged by the privacy ratchet. What it skips is the second
    person."""


#: ADR-011, one entry per node type. Shared context — the organisation, a
#: team, a workspace — goes through review; the places where a domain expert
#: explores go live on write. `workspace` is strict as ADR-011's default; it
#: names the type as configurable, which is not built (CHY-65).
REVIEW_MODES: dict[str, ReviewMode] = {
    "org": ReviewMode.STRICT,
    "team": ReviewMode.STRICT,
    "workspace": ReviewMode.STRICT,
    "project": ReviewMode.DIRECT,
    "sub_project": ReviewMode.DIRECT,
    "personal": ReviewMode.DIRECT,
}


def review_mode(node_type: str | None) -> ReviewMode:
    """The review mode of a scope of this type.

    A type missing from the table is strict. The CHECK on scope_nodes and a
    test keep the table complete, so this is the answer for the case that
    should not happen — and there the safe side is review, not a write that
    goes live because nobody listed its scope type.
    """
    return REVIEW_MODES.get(node_type or "", ReviewMode.STRICT)


async def review_mode_of(conn, scope_id: UUID) -> ReviewMode:
    """The review mode of one scope, read on the caller's connection.

    The connection, not the pool: the repository asks inside the transaction
    that writes the block, so the answer and the write belong together.
    """
    return review_mode(await conn.fetchval(
        "SELECT node_type FROM scope_nodes WHERE id = $1", scope_id
    ))


async def ancestor_chain(pool, scope_id: UUID) -> list:
    """The scope and all its ancestors, root first, each with its depth.

    Published for the repository context, which composes a context out of the
    blocks along this chain. It held the query itself until CHY-121.

    Returns rows, not models: the caller needs `depth` and `scope_path` for
    ordering and cache keys, and wrapping them in ScopeNode would cost a
    conversion for data that is read once and discarded.

    Each row carries the scope's own classification of direct model calls and
    the share drawn for review, both from the newest setting (CHY-199,
    CHY-269): read with the chain, as the data class is, so no caller can check
    one and forget the other.
    """
    return await pool.fetch(
        "SELECT s.id, s.scope_path, s.node_type, s.name, s.privacy_level, "
        "       r.risk_class AS direct_call_risk_class, "
        "       r.sample_rate AS direct_call_sample_rate, "
        "       nlevel(s.scope_path) AS depth "
        "FROM scope_nodes s "
        "LEFT JOIN LATERAL (SELECT risk_class, sample_rate FROM direct_call_risk_classes "
        "                   WHERE scope_id = s.id ORDER BY set_at DESC LIMIT 1) r ON TRUE "
        "WHERE s.scope_path @> (SELECT scope_path FROM scope_nodes WHERE id = $1) "
        "ORDER BY nlevel(s.scope_path)",
        scope_id,
    )


async def exists(pool, scope_id: UUID) -> bool:
    return bool(await pool.fetchval("SELECT 1 FROM scope_nodes WHERE id = $1", scope_id))


async def direct_call_risk_of(pool, scope_id: UUID) -> DirectCallRisk:
    """A scope's classification of direct model calls (CHY-199)."""
    chain = await ancestor_chain(pool, scope_id)
    if not chain:
        raise ScopeNotFound(scope_id)
    return direct_call_risk(chain)


async def list_members(pool, scope_id: UUID) -> list[dict]:
    """Who holds which role in this scope.

    The membership rows, not an effective-permissions answer. What a membership
    actually grants is CHY-92 and undecided; presenting this as "permissions"
    would imply a decision nobody has taken.
    """
    if not await exists(pool, scope_id):
        raise ScopeNotFound(scope_id)
    rows = await pool.fetch(
        "SELECT actor_id, role, granted_at, granted_by FROM scope_memberships "
        "WHERE scope_id = $1 ORDER BY granted_at",
        scope_id,
    )
    return [dict(r) for r in rows]


# ── writing ───────────────────────────────────────────────────────────────────

async def me(pool, caller: Caller) -> MeOut:
    """Who the caller is to Cohereny: actor id, role and memberships (CHY-92).

    The id is what an admin passes to `add_member`. Before this, the API named
    it nowhere, so a membership could not be granted through it at all.
    """
    actor = await resolve_actor(pool, caller)
    rows = await pool.fetch(
        "SELECT scope_id, role FROM scope_memberships WHERE actor_id = $1 "
        "ORDER BY granted_at, scope_id",
        actor.id,
    )
    return MeOut(
        actor_id=actor.id, role=actor.role,
        is_service_account=actor.is_service_account,
        memberships=[MembershipOut(**dict(r)) for r in rows],
    )


async def create_scope(pool, caller: Caller, body: ScopeCreate) -> ScopeNode:
    """Create a scope. The path is derived from the parent, never given.

    `architect` and above, unlike the pilot endpoints where a role gate would
    have made an Article 14 decision unreachable in production. Here the
    opposite holds: creating scopes is structural work and should require the
    role, on every stage: the caller needs a token whose groups map to
    architect or admin (the chy-* groups, INFRA-92). There is no stage where
    it passes without one (CHY-192).
    """
    creator = await resolve_actor(pool, caller)
    require(creator, "architect")

    scope_id = uuid4()
    label = scope_label(body.node_type, scope_id)

    async with pool.acquire() as conn, conn.transaction():
        parent_path = None
        if body.parent_id is not None:
            parent_path = await conn.fetchval(
                "SELECT scope_path::text FROM scope_nodes WHERE id = $1", body.parent_id
            )
            if parent_path is None:
                raise ParentScopeNotFound(body.parent_id)
            if body.privacy_level is not None:
                inherited = effective_privacy_level(
                    await ancestor_chain(conn, body.parent_id)
                )
                if PRIVACY_LEVELS.index(body.privacy_level) < PRIVACY_LEVELS.index(inherited):
                    raise ScopeLoosensPrivacy(body.privacy_level, inherited)

        path = f"{parent_path}.{label}" if parent_path else label

        row = await conn.fetchrow(
            "INSERT INTO scope_nodes (id, scope_path, node_type, name, description, "
            "parent_id, privacy_level, created_by) "
            "VALUES ($1, $2::ltree, $3, $4, $5, $6, $7, $8) "
            "RETURNING id, scope_path::text AS scope_path, node_type, name, "
            "description, parent_id, privacy_level, created_by, created_at",
            scope_id, path, body.node_type, body.name, body.description,
            body.parent_id, body.privacy_level, creator.id,
        )
        await write_audit(
            conn,
            action=ACTION_SCOPE_CREATED,
            actor_id=creator.id,
            target=target_ref("scope", scope_id),
            payload={"scope_path": path, "node_type": body.node_type,
                     "parent_id": str(body.parent_id) if body.parent_id else None,
                     "privacy_level": body.privacy_level},
        )

    return ScopeNode(**dict(row))


async def add_member(pool, caller: Caller, scope_id: UUID,
                     body: ScopeMemberCreate) -> dict:
    """Grant an actor a role in this scope. Admin only.

    The member sees the runs of this scope's workflows and of every scope below
    it (CHY-92, migration 035). The actor's id comes from `GET /v1/me`.
    """
    granter = await resolve_actor(pool, caller)
    require(granter, "admin")

    async with pool.acquire() as conn, conn.transaction():
        if not await conn.fetchval("SELECT 1 FROM scope_nodes WHERE id = $1", scope_id):
            raise ScopeNotFound(scope_id)
        if not await conn.fetchval("SELECT 1 FROM actors WHERE id = $1", body.actor_id):
            raise ActorNotFound

        try:
            row = await conn.fetchrow(
                "INSERT INTO scope_memberships (id, scope_id, actor_id, role, granted_by) "
                "VALUES ($1, $2, $3, $4, $5) "
                "RETURNING actor_id, role, granted_at, granted_by",
                uuid4(), scope_id, body.actor_id, str(body.role), granter.id,
            )
        except asyncpg.UniqueViolationError:
            # UNIQUE (scope_id, actor_id) from migration 001. Checking first
            # would be a second round trip and a race; the constraint is the
            # rule and this only translates it.
            raise MembershipExists(body.actor_id) from None

        await write_audit(
            conn,
            action=ACTION_SCOPE_MEMBER_ADDED,
            actor_id=granter.id,
            target=target_ref("scope", scope_id),
            payload={"member_id": str(body.actor_id), "role": str(body.role)},
        )

    return dict(row)


async def set_direct_call_risk_class(pool, caller: Caller, scope_id: UUID,
                                     risk_class: str | None,
                                     sample_rate: Decimal | None = None) -> DirectCallRisk:
    """Classify direct model calls in a scope (CHY-199). `architect` and above.

    The role that creates scopes and declares their data class, the other half
    of the same decision. A new row, never an update: the newest applies, and
    the table keeps how a scope came to its class. None takes the scope's own
    setting back, so it inherits again.

    A class looser than the ancestors hold is refused: it would be stored and
    have no effect, as a looser data class would (CHY-160). A stricter one is
    taken, and a later, stricter setting further up overrides it the same way.

    `sample_rate` goes with `minimal` and only there (CHY-269) — the API model
    says so before this is called, and a CHECK in migration 048 holds it for
    every row written since. A share below the one inherited is refused for the
    reason a looser class is.
    """
    setter = await resolve_actor(pool, caller)
    require(setter, "architect")

    async with pool.acquire() as conn, conn.transaction():
        chain = await ancestor_chain(conn, scope_id)
        if not chain:
            raise ScopeNotFound(scope_id)
        inherited = effective_direct_call_risk_class(chain[:-1])
        if (risk_class is not None and inherited is not None
                and RISK_CLASSES.index(risk_class) < RISK_CLASSES.index(inherited)):
            raise ScopeLoosensRiskClass(risk_class, inherited)
        inherited_rate = effective_direct_call_sample_rate(chain[:-1])
        if (sample_rate is not None and inherited_rate is not None
                and sample_rate < inherited_rate):
            raise ScopeLoosensSampleRate(float(sample_rate), float(inherited_rate))

        before = chain[-1]["direct_call_risk_class"]
        rate_before = chain[-1]["direct_call_sample_rate"]
        await conn.execute(
            "INSERT INTO direct_call_risk_classes (scope_id, risk_class, sample_rate, set_by) "
            "VALUES ($1, $2, $3, $4)",
            scope_id, risk_class, sample_rate, setter.id,
        )
        after = direct_call_risk(
            [*chain[:-1], {**dict(chain[-1]), "direct_call_risk_class": risk_class,
                           "direct_call_sample_rate": sample_rate}]
        )
        await write_audit(
            conn,
            action=ACTION_SCOPE_DIRECT_CALL_RISK_CLASS_SET,
            actor_id=setter.id,
            target=target_ref("scope", scope_id),
            payload={"from": before, "to": risk_class, "effective": after.effective,
                     "sample_rate": {"from": _number(rate_before), "to": _number(sample_rate),
                                     "effective": _number(after.sample_rate)}},
        )

    return after


def _number(rate: Decimal | None) -> float | None:
    """A share as the audit's JSON can hold it."""
    return None if rate is None else float(rate)


async def all_paths(conn) -> list:
    """Every scope with its path as text — the whole tree in one read.

    For the retention purge (CHY-17), which needs the period of every scope at
    once and derives each scope's ancestors from its path rather than asking
    for one chain per scope.
    """
    return await conn.fetch("SELECT id, scope_path::text AS scope_path FROM scope_nodes")
