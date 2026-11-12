"""
api/v1/scopes.py

HTTP for the scope tree — and nothing else (ADR-030, CHY-121).

The scope tree belongs to the kernel: a scope is not content, it is *where*
content lives, and every context refers to one without owning it. This module
held eight SQL statements against `scope_nodes` and `scope_memberships` until
CHY-121; the work is in `kernel/scopes.py` now.

What is left is what exists because the protocol is HTTP: routes, status codes,
dependency injection, response models.

Since CHY-180 also the competence order of a scope: the areas that vote at a
gate and who belongs to them (kernel/review_areas.py, ADR-050). Since CHY-199
the risk class of direct model calls, which decides with the data class whether
/v1/complete answers in a scope (api/v1/_direct_calls.py).

Called by:
  - clients managing the scope tree
"""
from decimal import Decimal
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, status

from api.deps import CurrentUser, require_auth
from api.v1._direct_calls import DirectCallClassification, DirectCallRiskClassSet, classification
from api.v1._responses import CONFLICT, FORBIDDEN, NOT_FOUND
from core.db import get_pool
from kernel import review_areas, scopes
from kernel.identity import resolve_actor
from kernel.schema import (
    ReviewArea,
    ReviewAreaCreate,
    ReviewAreaMember,
    ReviewAreaMemberCreate,
    ScopeCreate,
    ScopeMemberCreate,
    ScopeNode,
)

router = APIRouter()


@router.get("", response_model=list[ScopeNode])
async def list_scopes(
    actor: CurrentUser = Depends(require_auth),
    pool: asyncpg.Pool = Depends(get_pool),
) -> list[ScopeNode]:
    """Every scope, ordered by path so the tree reads top-down."""
    return await scopes.list_scopes(pool)


@router.post("", response_model=ScopeNode, status_code=status.HTTP_201_CREATED,
             responses=FORBIDDEN | NOT_FOUND)
async def create_scope(
    body: ScopeCreate,
    actor: CurrentUser = Depends(require_auth),
    pool: asyncpg.Pool = Depends(get_pool),
) -> ScopeNode:
    """Create a scope. The path is derived from the parent, never accepted."""
    return await scopes.create_scope(pool, actor, body)


@router.get("/{scope_id}/members", responses=NOT_FOUND)
async def list_members(
    scope_id: UUID,
    actor: CurrentUser = Depends(require_auth),
    pool: asyncpg.Pool = Depends(get_pool),
) -> list[dict]:
    """Who holds which role in this scope — the memberships, not permissions."""
    return await scopes.list_members(pool, scope_id)


@router.post("/{scope_id}/members", status_code=status.HTTP_201_CREATED,
             responses=FORBIDDEN | NOT_FOUND | CONFLICT)
async def add_member(
    scope_id: UUID,
    body: ScopeMemberCreate,
    actor: CurrentUser = Depends(require_auth),
    pool: asyncpg.Pool = Depends(get_pool),
) -> dict:
    """Grant an actor a role in this scope. Admin only."""
    return await scopes.add_member(pool, actor, scope_id, body)


# ── direct model calls (CHY-199, ADR-050) ─────────────────────────────────────

@router.get("/{scope_id}/direct-call-risk-class", response_model=DirectCallClassification,
            responses=FORBIDDEN | NOT_FOUND)
async def get_direct_call_risk_class(
    scope_id: UUID,
    actor: CurrentUser = Depends(require_auth),
    pool: asyncpg.Pool = Depends(get_pool),
) -> DirectCallClassification:
    """Whether /v1/complete answers in this scope, and what decides it."""
    # Resolved, not only authenticated: the request line of the application
    # log then names who asked (CHY-97), and the stored role follows the
    # token's groups as on every other call that knows its caller.
    await resolve_actor(pool, actor)
    return classification(await scopes.direct_call_risk_of(pool, scope_id))


@router.put("/{scope_id}/direct-call-risk-class", response_model=DirectCallClassification,
            responses=FORBIDDEN | NOT_FOUND)
async def set_direct_call_risk_class(
    scope_id: UUID,
    body: DirectCallRiskClassSet,
    actor: CurrentUser = Depends(require_auth),
    pool: asyncpg.Pool = Depends(get_pool),
) -> DirectCallClassification:
    """Classify direct model calls in this scope. Architect and above.

    Recorded with who and when, and in the audit log. The answer says whether
    /v1/complete now answers here: that depends on the data class as well.
    With minimal goes the share of direct calls judged afterwards (CHY-269).
    """
    rate = None if body.sample_rate is None else Decimal(str(body.sample_rate))
    return classification(
        await scopes.set_direct_call_risk_class(pool, actor, scope_id, body.risk_class, rate)
    )


# ── review areas (CHY-180, ADR-050) ───────────────────────────────────────────

@router.get("/{scope_id}/review-areas", response_model=list[ReviewArea],
            responses=NOT_FOUND)
async def list_review_areas(
    scope_id: UUID,
    actor: CurrentUser = Depends(require_auth),
    pool: asyncpg.Pool = Depends(get_pool),
) -> list[ReviewArea]:
    """The areas this scope defines and their members, revoked ones with the date.

    Inherited areas are listed where they are defined.
    """
    return await review_areas.list_areas(pool, actor, scope_id)


@router.post("/{scope_id}/review-areas", response_model=ReviewArea,
             status_code=status.HTTP_201_CREATED, responses=FORBIDDEN | NOT_FOUND | CONFLICT)
async def create_review_area(
    scope_id: UUID,
    body: ReviewAreaCreate,
    actor: CurrentUser = Depends(require_auth),
    pool: asyncpg.Pool = Depends(get_pool),
) -> ReviewArea:
    """Define an area that votes at a gate, for this scope and every scope below. Admin only."""
    return await review_areas.create_area(pool, actor, scope_id, body)


@router.post("/{scope_id}/review-areas/{name}/members", response_model=ReviewAreaMember,
             status_code=status.HTTP_201_CREATED, responses=FORBIDDEN | NOT_FOUND | CONFLICT)
async def add_review_area_member(
    scope_id: UUID,
    name: str,
    body: ReviewAreaMemberCreate,
    actor: CurrentUser = Depends(require_auth),
    pool: asyncpg.Pool = Depends(get_pool),
) -> ReviewAreaMember:
    """Add an actor to an area of this scope, or again after a revocation. Admin only."""
    return await review_areas.add_member(pool, actor, scope_id, name, body)


@router.delete("/{scope_id}/review-areas/{name}/members/{actor_id}",
               response_model=ReviewAreaMember, responses=FORBIDDEN | NOT_FOUND)
async def revoke_review_area_member(
    scope_id: UUID,
    name: str,
    actor_id: UUID,
    actor: CurrentUser = Depends(require_auth),
    pool: asyncpg.Pool = Depends(get_pool),
) -> ReviewAreaMember:
    """Revoke a membership, effective at once. The row stays with the date, so a
    vote cast before stays provable; a new one is refused. Admin only."""
    return await review_areas.revoke_member(pool, actor, scope_id, name, actor_id)
