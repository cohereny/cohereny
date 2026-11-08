"""
contexts/learning/service.py

Use cases of the learning context (ADR-030, ADR-043).

Published interface, together with schema and errors: anchor.py, validators.py
and store.py are internal (guard rule context_encapsulation).

Two kinds of use case so far:

  - the activation check for calibration anchors, which the composition root
    passes to the repository as `before_activation` — the way the router's
    check for routing rules is passed, because a context may not import
    another (ADR-030)
  - reading benchmark runs and what they measured
"""
from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from contexts.learning import anchor as anchors
from contexts.learning import store
from contexts.learning.errors import AnchorNotApprovable, BenchmarkRunNotFound, MalformedAnchor
from contexts.learning.schema import (
    BenchmarkMeasurementOut,
    BenchmarkRunDetail,
    BenchmarkRunList,
    BenchmarkRunOut,
)
from kernel.access import require
from kernel.identity import Caller, resolve_actor
from kernel.retention import Periods, RetentionClass

#: Reading benchmark evidence, like reading a block's audit trail (CHY-90).
_READ_ROLE = "reviewer"


async def check_anchor_before_approval(conn: Any, row: Any) -> None:
    """Refuse to activate a calibration anchor that could never be measured.

    Every other block type passes untouched. Runs on both approval paths
    (ADR-042) — the explicit approve and the direct activation — because both
    go through the repository's single status change.

    `conn` is unused: the anchor is judged on its own row. It is in the
    signature because that is the shape `before_activation` has, and a check
    that later needs the scope chain should not have to change its callers.
    """
    del conn
    if row["block_type"] != "calibration_anchor":
        return
    try:
        anchors.parse(row)
    except MalformedAnchor as exc:
        raise AnchorNotApprovable(row["block_id"], exc.reason) from exc


async def list_runs(pool, caller: Caller, *, limit: int, offset: int) -> BenchmarkRunList:
    """Benchmark runs, newest first."""
    require(await resolve_actor(pool, caller), _READ_ROLE)
    rows, total = await store.count_and_page_runs(pool, limit=limit, offset=offset)
    return BenchmarkRunList(
        items=[BenchmarkRunOut(**dict(r)) for r in rows],
        total=total, limit=limit, offset=offset,
    )


async def get_run(pool, caller: Caller, run_id: UUID) -> BenchmarkRunDetail:
    """One run and every measurement it took."""
    require(await resolve_actor(pool, caller), _READ_ROLE)
    row = await store.run(pool, run_id)
    if row is None:
        raise BenchmarkRunNotFound(run_id)
    measurements = [_measurement(m) for m in await store.measurements_of(pool, run_id)]
    return BenchmarkRunDetail(**dict(row), measurements=measurements)


def _measurement(row: Any) -> BenchmarkMeasurementOut:
    m = dict(row)
    # NUMERIC arrives as Decimal; the parts as an object, or as JSON text from
    # rows written without the pool's codec.
    if m["score"] is not None:
        m["score"] = float(m["score"])
    if isinstance(m["scores"], str):
        m["scores"] = json.loads(m["scores"])
    return BenchmarkMeasurementOut(**m)


# ── retention (CHY-17, ADR-046) ───────────────────────────────────────────────

def _per_workflow(periods: Periods, retention_class: RetentionClass,
                  workflow_scopes: dict[UUID, UUID]) -> tuple[list[UUID], list[int]]:
    """The period of each workflow's scope, as parallel arrays; unlimited left out."""
    pairs = [(workflow_id, periods.by_scope.get(scope_id, {}).get(retention_class.value))
             for workflow_id, scope_id in workflow_scopes.items()]
    finite = [(workflow_id, days) for workflow_id, days in pairs if days is not None]
    return [p[0] for p in finite], [p[1] for p in finite]


async def purge_feedback(conn, periods: Periods,
                         workflow_scopes: dict[UUID, UUID]) -> dict[str, int]:
    """Apply the retention periods to feedback, in the caller's transaction.

    `workflow_scopes` comes from the orchestration context, which owns the
    workflows (CHY-231); this context only knows which workflow a row is about.
    """
    ids, days = _per_workflow(periods, RetentionClass.RUN_CONTENT, workflow_scopes)
    cleared = await store.clear_feedback(
        conn, ids, days, periods.installation_days(RetentionClass.RUN_CONTENT))
    ids, days = _per_workflow(periods, RetentionClass.RUN_TRACE, workflow_scopes)
    deleted = await store.delete_feedback(
        conn, ids, days, periods.installation_days(RetentionClass.RUN_TRACE))
    return {"feedback_events_cleared": cleared, "feedback_events_deleted": deleted}
