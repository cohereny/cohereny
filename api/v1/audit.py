"""
api/v1/audit.py

Read access to the append-only audit log (CHY-31).

GET /v1/audit-log
  ?target=block:{uuid}   history of one entity      — reviewer or above
  ?actor_id={uuid}       everything one actor did   — admin only
  (no filter)            the whole log              — admin only

Why the filter decides the required role:
  The history of a context block is review material — a reviewer needs to see
  who changed what before approving it. The complete set of actions by one named
  person is something else: a record of an individual's behaviour over time, and
  handing that to every reviewer would turn an audit trail into surveillance.
  Same table, different question, different bar.

Writes go through core.audit, never through this module. The table is physically
append-only since migration 011.

Called by:
  - Reviewers inspecting an entity before approval
  - Administrators answering an audit or a GDPR Art. 15 request

Dependencies:
  - api.deps      (require_auth, CurrentUser)
  - core.db       (get_pool)
  - kernel.access   (require)
  - kernel.audit (AuditLogEntry)
"""
from __future__ import annotations

from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query

from api.deps import CurrentUser, require_auth
from api.v1._responses import FORBIDDEN
from kernel.access import require
from kernel.identity import resolve_actor
from contexts.repository import service as repository
from core.db import get_pool
from kernel import audit
from kernel.audit import AuditLogEntry
from kernel.schema import PG_INT_MAX

router = APIRouter()


async def _is_author(pool: asyncpg.Pool, target: str, actor_id) -> bool:
    """Did this actor write any version of the block `target` refers to?

    Any version, not just the current one: the entry a rejected author needs to
    read is attached to the version that was rejected, which a later edit may
    already have superseded.
    """
    try:
        block_id = UUID(target.split(":", 1)[1])
    except (IndexError, ValueError):
        return False
    # Through the repository context since CHY-105: this module owns
    # audit_log, not context_blocks, and asking the owner is the difference
    # between reading a table and knowing its schema.
    return await repository.is_author(pool, block_id, actor_id)

# A page bound exists so a single request cannot pull the entire log into memory.
# 100 is the default rather than the maximum: the common case is "the last few
# things that happened to this block".
_DEFAULT_LIMIT = 100
_MAX_LIMIT = 1000


@router.get("/audit-log", response_model=list[AuditLogEntry], responses=FORBIDDEN)
async def list_audit_log(
    target: str | None = Query(
        default=None,
        description="Entity reference, e.g. 'block:0f2c…'. Reviewer or above.",
    ),
    actor_id: UUID | None = Query(
        default=None, description="Actions by one actor. Admin only."
    ),
    limit: int = Query(default=_DEFAULT_LIMIT, ge=1, le=_MAX_LIMIT),
    offset: int = Query(default=0, ge=0, le=PG_INT_MAX),
    caller: CurrentUser = Depends(require_auth),
    pool: asyncpg.Pool = Depends(get_pool),
) -> list[AuditLogEntry]:
    """Newest first. Sorted by created_at DESC, with id as the tie-breaker.

    The tie-breaker matters: created_at has microsecond resolution but two
    entries written in one transaction can share a timestamp, and without a
    second sort key their relative order varies between calls — which makes
    offset paging skip or repeat rows.
    """
    if target is not None and actor_id is not None:
        raise HTTPException(
            status_code=400,
            detail=(
                "Use either target or actor_id, not both. They answer different "
                "questions and carry different role requirements."
            ),
        )

    # Resolve the bar before touching the log, so an under-privileged caller
    # cannot learn from timing whether an entity has entries at all.
    #
    # One exception, for blocks: their author may read their own block's
    # entries whatever their role. Otherwise a contributor whose block was
    # rejected gets a 403 when they try to find out why — and the reason lives
    # only in the audit payload, so that would make the review unanswerable
    # (CHY-32).
    if target is not None and target.startswith("block:"):
        actor = await resolve_actor(pool, caller)
        if not actor.has_at_least("reviewer") and not await _is_author(pool, target, actor.id):
            raise HTTPException(
                status_code=403,
                detail=(
                    "Role 'reviewer' or higher required, or authorship of this block."
                ),
            )
    else:
        require(await resolve_actor(pool, caller), "reviewer" if target is not None else "admin")

    return await audit.read_entries(
        pool, target=target, actor_id=actor_id, limit=limit, offset=offset
    )
