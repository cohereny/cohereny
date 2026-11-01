"""
api/v1/blocks.py

HTTP for the repository context — and nothing else (ADR-030, CHY-105).

What is left here is what exists because the protocol is HTTP: route
declarations, status codes, dependency injection, response models. The rules,
the SQL and the review workflow moved to contexts/repository/ — see that
package's __init__ for the layout and cohereny-arc42 §5.7 for why.

The file was 621 lines carrying the entire approval lifecycle, and that
lifecycle was reachable **only** over HTTP: no CLI, no background job and no
runner (ADR-028) could submit, approve or reject a block. "What is approved" is
the central fact of this product, and it had exactly one door.

The 200-versus-201 distinction on PATCH stays here, because it is the HTTP
half of a decision the context makes: a draft is edited in place, anything else
is superseded. A client has to be able to tell whether its change is live or
waiting for review, and the status code is how it finds out.

Called by:
  - Clients managing context blocks
  - api/v1/compose.py and api/v1/complete.py read through the same context
"""
from uuid import UUID

import asyncpg
import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, Query, Response, status

from api.deps import CurrentUser, require_auth
from api.v1._responses import (
    BLOCK_CREATE,
    BLOCK_READ,
    BLOCK_TRANSITION,
    BLOCK_WRITE,
    PROMOTE_CONFLICT,
    UNPROCESSABLE,
)
from api.v1._activation import check_before_activation
from contexts.repository import service
from contexts.repository.schema import (
    BlockCreate,
    BlockHistoryOut,
    BlockListOut,
    BlockOut,
    BlockPatch,
    BlockPromote,
    BlockQuery,
    BlockRejection,
    BlockTransition,
    BlockType,
)
from core.db import get_pool
from core.redis import get_redis
from kernel.schema import PG_INT_MAX

router = APIRouter()


@router.get("/{block_id}/history", response_model=BlockHistoryOut, responses=BLOCK_READ)
async def get_block_history(
    block_id: UUID,
    actor: CurrentUser = Depends(require_auth),
    pool: asyncpg.Pool = Depends(get_pool),
) -> BlockHistoryOut:
    """All versions of a block, newest first (CHY-30).

    Registered before GET /{block_id} on purpose: FastAPI matches routes in
    declaration order, and "history" would otherwise be parsed as a block_id
    and rejected as an invalid UUID.
    """
    return await service.get_history(pool, block_id)


# ── Review workflow (CHY-32) ──────────────────────────────────────────────────
#
# Four routes over one use case. They differ only in which entry of
# block.TRANSITIONS they name — which is the point of having that table: the
# allowed pre-states, the target status, the required role and the audit action
# used to be four sets of arguments spread over 80 lines.


@router.post("/{block_id}/submit", response_model=BlockOut, responses=BLOCK_TRANSITION)
async def submit_block(
    block_id: UUID,
    body: BlockTransition,
    actor: CurrentUser = Depends(require_auth),
    pool: asyncpg.Pool = Depends(get_pool),
    redis: aioredis.Redis = Depends(get_redis),
) -> BlockOut:
    """Hand a draft to review. Also the way back in after a rejection."""
    return await service.transition(
        pool, redis, actor, block_id, "submit", expected_version=body.version
    )


@router.post("/{block_id}/withdraw", response_model=BlockOut, responses=BLOCK_TRANSITION)
async def withdraw_block(
    block_id: UUID,
    body: BlockTransition,
    actor: CurrentUser = Depends(require_auth),
    pool: asyncpg.Pool = Depends(get_pool),
    redis: aioredis.Redis = Depends(get_redis),
) -> BlockOut:
    """Take a submission back before anyone has decided on it.

    Without this the only way to cancel a review would be DELETE, i.e. retiring
    the block — which is a different thing entirely.
    """
    return await service.transition(
        pool, redis, actor, block_id, "withdraw", expected_version=body.version
    )


# 422 besides the transition errors: a routing rule that could never be used
# is refused at approval, the approver's to fix (CHY-166).
@router.post(
    "/{block_id}/approve", response_model=BlockOut,
    responses={**BLOCK_TRANSITION, **UNPROCESSABLE},
)
async def approve_block(
    block_id: UUID,
    body: BlockTransition,
    actor: CurrentUser = Depends(require_auth),
    pool: asyncpg.Pool = Depends(get_pool),
    redis: aioredis.Redis = Depends(get_redis),
) -> BlockOut:
    """Approve a block. This is the only way content reaches a live prompt.

    A routing rule is additionally held against its scope (CHY-160): every
    candidate must be allowed to see the data the scope holds. The check runs
    inside the transition, on the locked row — not before it, where it would
    cost a second read and judge a row that might not be the one approved.
    """
    return await service.transition(
        pool, redis, actor, block_id, "approve", expected_version=body.version,
        before_activation=check_before_activation,
    )


@router.post(
    "/{block_id}/promote", response_model=BlockOut, status_code=status.HTTP_201_CREATED,
    responses={**BLOCK_WRITE, **PROMOTE_CONFLICT, **UNPROCESSABLE},
)
async def promote_block(
    block_id: UUID,
    body: BlockPromote,
    actor: CurrentUser = Depends(require_auth),
    pool: asyncpg.Pool = Depends(get_pool),
) -> BlockOut:
    """Copy the block's live version into a strict scope above it, as a draft (ADR-042).

    The original stays unchanged and active. The copy is a new block, reviewed
    in its new scope like any other.
    """
    return await service.promote_block(pool, actor, block_id, body)


@router.post("/{block_id}/reject", response_model=BlockOut, responses=BLOCK_TRANSITION)
async def reject_block(
    block_id: UUID,
    body: BlockRejection,
    actor: CurrentUser = Depends(require_auth),
    pool: asyncpg.Pool = Depends(get_pool),
    redis: aioredis.Redis = Depends(get_redis),
) -> BlockOut:
    """Send a block back with a reason.

    The reason lands in the audit entry, which the block's author may read —
    see api/v1/audit.py. It is the only artefact of the decision they get.
    """
    return await service.transition(
        pool, redis, actor, block_id, "reject",
        expected_version=body.version, reason=body.reason,
    )


@router.get("", response_model=BlockListOut)
async def list_blocks(
    scope_id: UUID | None = Query(None),
    block_type: BlockType | None = Query(None),
    status_filter: str | None = Query(None, alias="status"),
    page: int = Query(1, ge=1, le=PG_INT_MAX),
    page_size: int = Query(20, ge=1, le=100),
    actor: CurrentUser = Depends(require_auth),
    pool: asyncpg.Pool = Depends(get_pool),
) -> BlockListOut:
    return await service.list_blocks(pool, BlockQuery(
        scope_id=scope_id, block_type=block_type, status=status_filter,
        page=page, page_size=page_size,
    ))


@router.get("/{block_id}", response_model=BlockOut, responses=BLOCK_READ)
async def get_block(
    block_id: UUID,
    actor: CurrentUser = Depends(require_auth),
    pool: asyncpg.Pool = Depends(get_pool),
) -> BlockOut:
    return await service.get_block(pool, block_id)


@router.post("", response_model=BlockOut, status_code=status.HTTP_201_CREATED,
             responses=BLOCK_CREATE)
async def create_block(
    body: BlockCreate,
    actor: CurrentUser = Depends(require_auth),
    pool: asyncpg.Pool = Depends(get_pool),
    redis: aioredis.Redis = Depends(get_redis),
) -> BlockOut:
    """Create a block — a draft in a strict scope, active in a direct one (ADR-042)."""
    return await service.create_block(
        pool, redis, actor, body, before_activation=check_before_activation,
    )


@router.patch(
    "/{block_id}", response_model=BlockOut,
    responses={
        **BLOCK_TRANSITION,
        **UNPROCESSABLE,
        # The second success. Editing a published block does not change it,
        # it creates the next version — and the contract listed only 200
        # until the E2E's contract check saw a 201 (CHY-124).
        201: {"model": BlockOut,
              "description": "The block was published: its edit became a new version — "
                             "a draft in a strict scope, active in a direct one (ADR-042)."},
    },
)
async def patch_block(
    block_id: UUID,
    body: BlockPatch,
    response: Response,
    actor: CurrentUser = Depends(require_auth),
    pool: asyncpg.Pool = Depends(get_pool),
    redis: aioredis.Redis = Depends(get_redis),
) -> BlockOut:
    """Edit a block. 200 if the draft was changed, 201 if a version was published.

    In a direct scope the result is active at once, either way (ADR-042).
    """
    block, superseded = await service.patch_block(
        pool, redis, actor, block_id, body, before_activation=check_before_activation,
    )
    if superseded:
        response.status_code = status.HTTP_201_CREATED
    return block


@router.delete("/{block_id}", status_code=status.HTTP_204_NO_CONTENT,
               responses=BLOCK_WRITE)
async def delete_block(
    block_id: UUID,
    actor: CurrentUser = Depends(require_auth),
    pool: asyncpg.Pool = Depends(get_pool),
    redis: aioredis.Redis = Depends(get_redis),
) -> None:
    """Retire a published block — the whole version line.

    Repeating it succeeds without writing: a second DELETE is not a second
    decision. A block that never went live cannot be retired; a draft or a
    submission is cancelled with withdraw.
    """
    await service.retire_block(pool, redis, actor, block_id)
