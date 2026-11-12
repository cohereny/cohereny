"""
api/v1/samples.py

The review queue: what was drawn for a check afterwards, and the verdict on it
(ADR-050, CHY-223, CHY-269).

At the review level `sample` the output goes on and a share of it is judged
later. Two places draw: a sample gate in a workflow, for a share of its passes,
and /v1/complete, for a share of the direct calls in a scope whose minimum is
sample. One queue for both (decided by Thomas, 10.11.2026), so a reviewer finds
everything drawn in one place and a verdict means the same wherever it came
from. Until CHY-269 the queue was the workflows' alone, at /v1/workflows/samples.

On the author surface, where reviewers work; /v1/complete, on serve, only draws.

Called by:
  - reviewers' clients (the reference UI, scripts)
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends

from api.deps import CurrentUser, require_auth
from api.v1._responses import FORBIDDEN, NOT_FOUND
from api.v1.workflows import get_placeholders
from contexts.orchestration import service
from contexts.orchestration.schema import (
    ReviewSampleDetail,
    ReviewSampleOut,
    ReviewSampleVerdict,
)
from core.db import get_pool

router = APIRouter()

_JUDGED = {
    409: {"description": "The sample has its verdict already: one per sample."},
    410: {"description": "The content was cleared at the end of its period (CHY-17): "
                         "there is nothing left to judge."},
}


@router.get("", response_model=list[ReviewSampleOut])
async def list_samples(
    open_only: bool = True,
    actor: CurrentUser = Depends(require_auth),
    pool=Depends(get_pool),
) -> list[ReviewSampleOut]:
    """Everything drawn for a check afterwards that the caller may see, oldest first.

    Workflow passes (`source=workflow`) of runs the caller may see, and direct
    calls (`source=direct_call`) the caller made or that belong to a scope they
    are a member of. `open_only=false` lists judged ones too.
    """
    return await service.list_samples(pool, actor, open_only=open_only)


@router.get("/{sample_id}", response_model=ReviewSampleDetail, responses=NOT_FOUND)
async def get_sample(
    sample_id: UUID,
    actor: CurrentUser = Depends(require_auth),
    pool=Depends(get_pool),
) -> ReviewSampleDetail:
    """One sample with what there is to judge.

    A direct call: the prompt and the answer as the model saw and gave them —
    placeholders, never the values — and the block versions of its context,
    until the scope's content period clears them. A workflow pass: its output is
    the run's, `GET /v1/workflows/runs/{run_id}/nodes`.
    """
    return await service.sample_detail(pool, actor, sample_id)


@router.post("/{sample_id}/verdict", response_model=ReviewSampleOut,
             responses={**FORBIDDEN, **NOT_FOUND, **_JUDGED})
async def judge_sample(
    sample_id: UUID,
    body: ReviewSampleVerdict,
    actor: CurrentUser = Depends(require_auth),
    pool=Depends(get_pool),
    placeholders: service.Placeholders = Depends(get_placeholders),
) -> ReviewSampleOut:
    """Judge a sample: `confirmed` or `objected`, with a reason. Requires `reviewer`.

    Once per sample, audited as `workflow.sample_reviewed` or
    `direct_call.sample_reviewed`. An objection recalls nothing — the output
    went on — it is counted.
    """
    return await service.judge_sample(pool, actor, sample_id, body,
                                      placeholders=placeholders)
