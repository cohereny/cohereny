"""
api/v1/scopes.py

HTTP for the scope tree — and nothing else (ADR-030, CHY-121).

The scope tree belongs to the kernel: a scope is not content, it is *where*
content lives, and every context refers to one without owning it. This module
held eight SQL statements against `scope_nodes` and `scope_memberships` until
CHY-121; the work is in `kernel/scopes.py` now.

What is left is what exists because the protocol is HTTP: routes, status codes,
dependency injection, response models.

Called by:
  - clients managing the scope tree
"""
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, status

from api.deps import CurrentUser, require_auth
from api.v1._responses import CONFLICT, FORBIDDEN, NOT_FOUND
from core.db import get_pool
from kernel import scopes
from kernel.schema import ScopeCreate, ScopeMemberCreate, ScopeNode

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
