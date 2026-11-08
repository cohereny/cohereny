"""
api/v1/me.py

GET /v1/me — who the caller is to Cohereny (CHY-92).

Row-level security made scope memberships matter: a reviewer sees the runs of
the scopes they belong to, and an admin grants a membership by actor id. That
id was named nowhere in the API, so nobody could have granted one. This route
says it, together with the role and the memberships the caller holds.

On the author surface, with the scopes and their members: the people and the UI
who manage memberships work there (ADR-031).

Called by:
  - the UI and anyone who needs their own actor id
  - deploy/quickstart/seed.py (adds the example's reviewer to its project)
"""
import asyncpg
from fastapi import APIRouter, Depends

from api.deps import CurrentUser, require_auth
from core.db import get_pool
from kernel import scopes
from kernel.schema import MeOut

router = APIRouter()


@router.get("/me", response_model=MeOut)
async def me(
    actor: CurrentUser = Depends(require_auth),
    pool: asyncpg.Pool = Depends(get_pool),
) -> MeOut:
    """Your actor id, your role and the scopes you belong to.

    An admin adds you to a scope with this id (`POST /v1/scopes/{id}/members`);
    a member sees the runs of that scope and of every scope below it.
    """
    return await scopes.me(pool, actor)
