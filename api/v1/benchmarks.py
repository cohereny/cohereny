"""
api/v1/benchmarks.py

Benchmark runs and what they measured (ADR-043).

A run measures the active calibration anchors against the configured models;
each measurement is evidence, kept append-only (migration 028). Reading is
implemented (CHY-67). Starting a run comes with CHY-68 — on demand, through the
CLI and a systemd unit on the application server, not on a schedule yet (CHY-181). The report and
its approval by a reviewer come with CHY-64: a regression is not released by a
job, a person confirms it (ADR-004).

Surface `observe`. Reading needs `reviewer`, like a block's audit trail.
"""
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, Query

from api.deps import CurrentUser, require_auth
from api.v1._planned import not_implemented, planned
from api.v1._responses import FORBIDDEN, NOT_FOUND
from contexts.learning import service
from contexts.learning.schema import BenchmarkRunDetail, BenchmarkRunList
from core.db import get_pool
from kernel.schema import PG_INT_MAX

router = APIRouter()

#: A page bound, so one request cannot pull every run into memory — the same
#: reason the audit log has one.
_DEFAULT_LIMIT = 50
_MAX_LIMIT = 200


@router.get("", response_model=BenchmarkRunList, responses=FORBIDDEN)
async def list_runs(
    limit: int = Query(default=_DEFAULT_LIMIT, ge=1, le=_MAX_LIMIT),
    offset: int = Query(default=0, ge=0, le=PG_INT_MAX),
    actor: CurrentUser = Depends(require_auth),
    pool: asyncpg.Pool = Depends(get_pool),
) -> BenchmarkRunList:
    """Benchmark runs, newest first."""
    return await service.list_runs(pool, actor, limit=limit, offset=offset)


@router.post("/run", **planned("CHY-68"))
async def trigger_run(actor: CurrentUser = Depends(require_auth)) -> None:
    not_implemented("CHY-68")


@router.get("/{run_id}", response_model=BenchmarkRunDetail,
            responses={**FORBIDDEN, **NOT_FOUND})
async def get_run(
    run_id: UUID,
    actor: CurrentUser = Depends(require_auth),
    pool: asyncpg.Pool = Depends(get_pool),
) -> BenchmarkRunDetail:
    """One run and every measurement it took.

    A measurement without a score is an outage, not a result: its outcome
    says which (unreachable, refused by the data class, error) and `detail`
    why (migration 028).
    """
    return await service.get_run(pool, actor, run_id)


@router.get("/{run_id}/report", **planned("CHY-64"))
async def get_report(run_id: str, actor: CurrentUser = Depends(require_auth)) -> None:
    not_implemented("CHY-64")


@router.post("/{run_id}/approve", **planned("CHY-64"))
async def approve_report(run_id: str, actor: CurrentUser = Depends(require_auth)) -> None:
    """Human oversight approval after a detected regression."""
    not_implemented("CHY-64")
