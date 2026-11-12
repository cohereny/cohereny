"""
api/v1/pilots.py

HTTP for the pilots context — and nothing else (ADR-030, CHY-104).

What is left here is what exists because the protocol is HTTP: route
declarations, SSE framing, dependency injection, response models. Every rule,
every SQL statement and every status-code decision moved to
contexts/pilots/ — see that package's __init__ for the layout and
cohereny-arc42 §5.7 for why.

The file was 846 lines with three functions over the complexity threshold and
54 % coverage. It carried findings A1, A2, A4, B4, B7 and D1 of the 14.08.
audit at once, which was not a coincidence: where the most happens and the
least is tested, everything collects.

SSE (GET /v1/pilots/runs/{id}/stream):
  The service yields event dicts, or None when nothing has happened. This
  layer turns the first into `data: <json>\n\n` and the second into a
  heartbeat comment — keeping a socket alive is an HTTP concern, noticing that
  a run is quiet is not.

  X-Accel-Buffering: no is required so nginx does not buffer the stream.

Called by:
  - cohereny-ui (Astro SSR frontend)
"""
import json
from collections.abc import AsyncIterator
from uuid import UUID

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from api.config import settings
from api.deps import CurrentUser, require_auth
from api.v1._responses import FORBIDDEN, NOT_FOUND
from contexts.pilots import service
from contexts.pilots.schema import (
    RunEnvironment,
    WorkflowConfigListOut,
    WorkflowConfigOut,
    WorkflowConfigPut,
    CoverageOut,
    OversightRequest,
    PilotRunOut,
    PilotRunRequest,
)
from core.db import get_pool
from core.redis import get_redis

# Deprecated since 2026-11-08: the LangGraph pilots call the model with the
# values and store them, unlike a workflow run. They are rebuilt as workflows
# on the runner (CHY-172); until then the contract says so on every operation.
router = APIRouter(deprecated=True)


@router.get("/runs", response_model=list[PilotRunOut])
async def list_runs(
    actor: CurrentUser = Depends(require_auth),
    pool=Depends(get_pool),
) -> list[PilotRunOut]:
    return await service.list_runs(pool, actor)


@router.post(
    "/runs", response_model=PilotRunOut, status_code=201, responses=FORBIDDEN
)
async def start_run(
    body: PilotRunRequest,
    actor: CurrentUser = Depends(require_auth),
    pool=Depends(get_pool),
    redis: aioredis.Redis = Depends(get_redis),
) -> PilotRunOut:
    return await service.start_run(
        pool, redis, actor, body,
        RunEnvironment(
            input_dir=settings.pilot_input_dir,
            db_dsn=settings.database_url,
        # model_used is no longer passed in (CHY-42). It used to be
        # f"{settings.model_small}, {settings.model_medium}" — the deployment's
        # models, which is a different claim from "the models this run used",
        # and would have gone on disagreeing silently the moment a run
        # overrode one. It is derived from the effective configuration now, and
        # recorded alongside it.
        #
        # From settings, not from a COHERENY_DEV_MODE the executor used to read
        # for itself (CHY-96): that variable existed in no file of this
        # repository, only in the dev stage's .env. Its own switch since
        # CHY-192 — before, measuring token usage came bundled with turning
        # authentication off.
            node_metrics=settings.pilot_node_metrics,
        ),
    )


#: Sentinel for "the stream ended before it yielded anything" — distinct from a
#: None tick, which means "still connected, nothing to report".
_EXHAUSTED = object()


@router.get(
    "/runs/{run_id}/stream",
    # Declared as an event stream, not as JSON (CHY-28). FastAPI infers
    # application/json from the absence of anything else, so the contract
    # promised a JSON body for a route that never sends one — a generated
    # client would try to parse the first chunk and fail on a working stream.
    response_class=StreamingResponse,
    responses={
        200: {
            "content": {"text/event-stream": {}},
            "description": "SSE stream: node_complete, done, error.",
        },
        **NOT_FOUND,
    },
)
async def stream_run(
    run_id: UUID,
    actor: CurrentUser = Depends(require_auth),
    pool=Depends(get_pool),
) -> StreamingResponse:
    """SSE stream for a pilot run.

    Reconnect-safe: a client that reloads gets the full history replayed from
    the beginning, live from memory or from events_json for a run this process
    no longer holds.
    """
    stream = service.stream_events(pool, actor, run_id)

    # Pull the first item here, before the response exists. An async generator
    # does not run until it is iterated, so a RunNotFound raised on the first
    # step would otherwise happen inside StreamingResponse — after the status
    # line has gone out. The client would read HTTP 200 and a body explaining
    # that the run does not exist.
    first = await anext(stream, _EXHAUSTED)

    async def framed() -> AsyncIterator[str]:
        if first is not _EXHAUSTED:
            yield _frame(first)
            async for event in stream:
                yield _frame(event)

    return StreamingResponse(
        framed(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


def _frame(event: dict | None) -> str:
    """One service event as an SSE frame; None becomes a heartbeat comment."""
    if event is None:
        return ": heartbeat\n\n"
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


@router.get("/files")
async def list_files(
    pilot: str | None = None,
    actor: CurrentUser = Depends(require_auth),
) -> list[str]:
    """Available PDF filenames, optionally filtered to one pilot's prefix."""
    return service.list_input_files(settings.pilot_input_dir, pilot)


@router.get("/runs/{run_id}", response_model=PilotRunOut, responses=NOT_FOUND)
async def get_run(
    run_id: UUID,
    actor: CurrentUser = Depends(require_auth),
    pool=Depends(get_pool),
) -> PilotRunOut:
    return await service.get_run(pool, actor, run_id)


@router.get(
    "/runs/{run_id}/coverage", response_model=CoverageOut, responses=NOT_FOUND
)
async def get_run_coverage(
    run_id: UUID,
    actor: CurrentUser = Depends(require_auth),
    pool=Depends(get_pool),
) -> CoverageOut:
    """question_bank coverage for a completed study run.

    404 both when the run does not exist and when it has no coverage data
    (a banking run, or no questions ingested yet).
    """
    return await service.get_coverage(pool, actor, run_id)


@router.delete(
    "/runs/{run_id}", status_code=202, responses={**FORBIDDEN, **NOT_FOUND}
)
async def cancel_or_delete_run(
    run_id: UUID,
    actor: CurrentUser = Depends(require_auth),
    pool=Depends(get_pool),
) -> dict:
    """Cancel a running run or delete a terminal one — audited, and only your own.

    - running or blocked on oversight → 202 {status: cancelling}
    - running but not in flight (restart) → 202 {status: cancelled}
    - failed/complete/rejected → 202 {status: deleted}
    - carrying Art. 50 transparency records → 409
    """
    return {"status": await service.cancel_or_delete(pool, actor, run_id)}


@router.post(
    "/runs/{run_id}/oversight", status_code=200, responses={**FORBIDDEN, **NOT_FOUND}
)
async def submit_oversight(
    run_id: UUID,
    body: OversightRequest,
    actor: CurrentUser = Depends(require_auth),
    pool=Depends(get_pool),
) -> dict:
    """Submit a human oversight decision for a banking run (EU AI Act Art. 14).

    The run must be awaiting oversight; the decision must be approved or
    rejected. Recording it releases the background thread.
    """
    decision = await service.submit_oversight(
        pool, actor, run_id, body.decision
    )
    return {"status": "ok", "decision": decision}

# ── workflow configuration (CHY-42) ───────────────────────────────────────────
#
# `/v1/pilots/configs`, not `/v1/pilots/workflows` as CHY-42 wrote it.
# `/v1/workflows` already exists for workflow *definitions* — the graphs of
# CHY-82 — and two different resources under one word is the duplication
# CONVENTIONS.md §8 names: a second name for one thing is as bad as two sources
# for one truth, and this would have been one name for two things, which is the
# same mistake pointing the other way.

@router.get("/configs", response_model=WorkflowConfigListOut)
async def list_pilot_configs(
    actor: CurrentUser = Depends(require_auth),
    pool=Depends(get_pool),
) -> WorkflowConfigListOut:
    """Every pilot's stored configuration.

    Straight from the database, not from the cache: a listing is rare, and a
    caller comparing pilots wants one consistent answer rather than a mix of
    cached and fresh rows.
    """
    return await service.list_workflow_configs(pool, actor)


@router.get(
    "/configs/{pilot_name}",
    response_model=WorkflowConfigOut,
    responses=NOT_FOUND,
)
async def get_pilot_config(
    pilot_name: str,
    actor: CurrentUser = Depends(require_auth),
    pool=Depends(get_pool),
    redis: aioredis.Redis = Depends(get_redis),
) -> WorkflowConfigOut:
    return await service.get_workflow_config(pool, redis, pilot_name, actor)


@router.put(
    "/configs/{pilot_name}",
    response_model=WorkflowConfigOut,
    responses={**FORBIDDEN, **NOT_FOUND},
)
async def put_pilot_config(
    pilot_name: str,
    body: WorkflowConfigPut,
    actor: CurrentUser = Depends(require_auth),
    pool=Depends(get_pool),
    redis: aioredis.Redis = Depends(get_redis),
) -> WorkflowConfigOut:
    """Replace the named sections. Omitted sections are left as they are.

    Replace and not merge: merging leaves no way to *remove* a key, and "send
    the section you want" is a rule a caller can hold in their head. Sending
    an empty object therefore clears a section, which is a different intent
    from omitting it.

    Requires `architect`. The model map decides which model sees which data —
    in the banking pilot it is the local/cloud boundary — so this is a
    governance write, not a setting.
    """
    return await service.put_workflow_config(pool, redis, actor, pilot_name, body)
