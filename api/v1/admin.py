"""
api/v1/admin.py

HTTP for the administrative kernel operations — and nothing else.

POST /admin/actors/{ref}/anonymize   GDPR Art. 17 erasure

The erasure itself lives in `kernel/identity.py` since CHY-123: the kernel owns
`actors`, and a legal obligation that can only be discharged by an HTTP client
is an obligation with one door — the same objection as for the Art. 14 decision
in CHY-105.

Why pseudonymization instead of deletion (ADR-016, ADR-025) and why the last
admin is protected: both reasons are written down where the rules are, in
`kernel/identity.py` and `kernel/errors.py`.

Audit log reads live at GET /v1/audit-log (api/v1/audit.py, CHY-31). The
placeholder that used to sit here is gone rather than kept as an alias: it only
ever returned not_implemented, so nothing can depend on it.

Called by:
  - Administrators handling an Art. 17 request
"""
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends

from api.deps import CurrentUser, require_auth
from api.v1._responses import FORBIDDEN, NOT_FOUND
from core.db import get_pool
from kernel import identity
from kernel.schema import AnonymizeResult

router = APIRouter()


@router.post("/actors/{actor_ref}/anonymize", responses={**FORBIDDEN, **NOT_FOUND})
async def anonymize_actor(
    actor_ref: UUID,
    actor: CurrentUser = Depends(require_auth),
    pool: asyncpg.Pool = Depends(get_pool),
) -> AnonymizeResult:
    """GDPR Art. 17: sever an actor's personal identity, keep the record.

    Idempotent — repeating it on an already anonymized actor succeeds and
    reports already_anonymized. A requester must not get an error for asking
    twice, and the second call is not a state change.
    """
    return await identity.anonymize_actor(pool, actor, actor_ref)
