"""
api/v1/compose.py

Context composition endpoint — the read path of the context repository
(CHY-61, ADR-010).

POST /v1/compose takes a scope and returns the active context blocks that apply
there, assembled in composition order: organisation level first, then team,
then project, so a narrower block can qualify a broader one.

Where the work happens:
  The composition itself and its cache live in contexts/repository/. This module
  translates HTTP into that call and back — the same composition is needed by
  /v1/complete and by the write-through task on block activation, and none of
  them should reimplement it.

Cache behaviour (ADR-010):
  A hit is a single Redis GET. A miss reads PostgreSQL, compiles and writes
  back. The response says which happened, because a caller measuring latency
  otherwise cannot tell a slow cache from a cold one.

Called by:
  - Agentic clients that need context before an LLM call
  - Future: api/v1/complete.py, as the step before the LLM call

Dependencies:
  - api.deps        (require_auth, CurrentUser)
  - core.db         (get_pool)
  - core.redis      (get_redis)
  - contexts.repository.compose (compose — the actual read path)
  - contexts.repository.schema  (ComposeRequest, ComposedContext)
"""
import asyncpg
import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, status

from api.deps import CurrentUser, require_auth
from contexts.repository import service
from kernel.errors import ScopeNotFound
from core.db import get_pool
from api.v1._responses import NOT_FOUND
from core.redis import get_redis
from contexts.repository.schema import ComposedContext, ComposeRequest

router = APIRouter()


@router.post("/compose", response_model=ComposedContext, responses=NOT_FOUND)
async def compose_context(
    body: ComposeRequest,
    actor: CurrentUser = Depends(require_auth),
    pool: asyncpg.Pool = Depends(get_pool),
    redis: aioredis.Redis = Depends(get_redis),
) -> ComposedContext:
    """Compose the context for a scope from its ancestors.

    An empty result is a 200 with no blocks, not a 404. A scope that exists but
    has no active blocks yet is a normal state during setup, and answering with
    an error would make callers treat "nothing configured" as a failure.
    """
    try:
        return await service.compose_context(pool, redis, body)
    except ScopeNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
