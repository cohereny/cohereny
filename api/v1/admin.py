"""
api/v1/admin.py

HTTP for the administrative kernel operations — and nothing else.

POST   /admin/actors/{ref}/anonymize   GDPR Art. 17 erasure
GET    /admin/retention                how long what is kept (CHY-17, ADR-046)
PUT    /admin/retention/{class}        set a period, installation-wide or per scope
DELETE /admin/retention/{class}        remove a scope's own period

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
from fastapi import APIRouter, Depends, Query, status

from api.deps import CurrentUser, require_auth
from api.v1._responses import FORBIDDEN, NOT_FOUND, UNPROCESSABLE
from contexts.orchestration import service as orchestration
from core.db import get_pool
from kernel import identity, retention
from kernel.schema import (
    AnonymizeResult,
    ConfigurableRetentionClass,
    RetentionOverview,
    RetentionPolicy,
    RetentionPolicySet,
)

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


@router.get("/retention", responses={**FORBIDDEN, **NOT_FOUND})
async def get_retention(
    scope_id: UUID | None = Query(None),
    actor: CurrentUser = Depends(require_auth),
    pool: asyncpg.Pool = Depends(get_pool),
) -> RetentionOverview:
    """How long the installation keeps what (CHY-17, ADR-046).

    Every retention class with its tables, the columns that are content, and
    the periods that are set. With `scope_id`, also the period that applies in
    that scope and where it was declared. Readable from `reviewer` up: a
    supervisor needs to know the rule, not only whoever changes it.
    """
    return await retention.overview(pool, actor, scope_id)


#: Art. 26(6) AI Act (CHY-266): a change that would shorten the logs of a
#: high-risk workflow below six months.
_HIGH_RISK_PERIOD = {
    409: {"description": "The change would keep the run trace or content of an active "
                         "high-risk workflow (risk_class high, or none) for less than six "
                         "months (AI Act Art. 26(6), CHY-266)."},
}


@router.put(
    "/retention/{retention_class}",
    responses={**FORBIDDEN, **NOT_FOUND, **UNPROCESSABLE, **_HIGH_RISK_PERIOD},
)
async def set_retention(
    retention_class: ConfigurableRetentionClass,
    body: RetentionPolicySet,
    actor: CurrentUser = Depends(require_auth),
    pool: asyncpg.Pool = Depends(get_pool),
) -> RetentionPolicy:
    """Set a period, installation-wide (no `scope_id`) or for one scope. `admin`, audited.

    `keep_days: null` keeps without limit. Only `run_content` and `run_trace`
    take a period; the other classes say in GET why they do not.
    """
    return await retention.set_policy(pool, actor, retention_class, body,
                                      high_risk_scopes=orchestration.high_risk_scopes)


@router.delete(
    "/retention/{retention_class}", status_code=status.HTTP_204_NO_CONTENT,
    responses={**FORBIDDEN, **NOT_FOUND, **_HIGH_RISK_PERIOD},
)
async def remove_retention_override(
    retention_class: ConfigurableRetentionClass,
    scope_id: UUID = Query(...),
    actor: CurrentUser = Depends(require_auth),
    pool: asyncpg.Pool = Depends(get_pool),
) -> None:
    """Remove a scope's own period, so the inherited one applies again. `admin`, audited.

    The installation-wide period can be changed, not removed.
    """
    await retention.remove_override(pool, actor, retention_class, scope_id,
                                    high_risk_scopes=orchestration.high_risk_scopes)
