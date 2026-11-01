"""
contexts/learning/schema.py

Transport shapes of the learning context (ADR-030, CHY-108).

Two files merged here — `domain/feedback.py` and `domain/benchmark.py`. They are
two ends of the same loop, and the layout in contexts/__init__.py calls for one
`schema.py`, not one per subject.

Why an explicit learning mechanism:
  Context blocks are not static — they should improve when humans give feedback
  or benchmarks detect a regression. The loop makes that improvement traceable:

    FeedbackEvent -> LearningProposal -> block draft -> review -> active

  Through the block's own review, not a separate merge request (ADR-042).

  No block is ever overwritten without a human approving it. That is not
  caution for its own sake: an automatic edit path would let a bad measurement
  rewrite a released prompt, and nothing downstream could tell the difference
  between a considered change and drift.

Why regular benchmarks:
  LLM output degrades through model updates, context changes and prompt drift
  without anyone noticing. A benchmark run compares current output against
  calibration_anchor blocks and flags a regression before it reaches production.
  On a regression the affected workflows pause and a LearningProposal is raised.

The loop crosses contexts and does so through published interfaces: the
block draft and its review belong to `repository` (ADR-042), the approval role
to `kernel`. This context owns the *evidence* — what was observed,
what was proposed — and not the decision.

Published interface of a context is service, schema, errors — this is `schema`,
and for now the only one.

Dependencies:
  - pydantic (validation and serialization)
"""
from __future__ import annotations
from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID
from pydantic import BaseModel

ProposalStatus = Literal["pending", "approved", "rejected"]
ProposalSource = Literal["feedback", "benchmark"]


class FeedbackEvent(BaseModel):
    """A single piece of user feedback on a workflow execution.

    execution_id: Langfuse Trace ID — links the feedback to the full execution
                  trace in Langfuse.
    node_ref:     which workflow node produced the criticized output.
    rating:       numeric rating (e.g. 1-5 or thumbs up/down as 1/-1).
    comment:      optional free text with a concrete improvement suggestion.
    """

    id: UUID
    execution_id: str
    node_ref: str
    workflow_id: UUID | None = None
    actor_id: UUID | None = None
    rating: int | None = None
    comment: str | None = None
    created_at: datetime


class LearningProposal(BaseModel):
    """Proposal to improve a context block, generated from feedback or a benchmark.

    proposed_diff: structured change proposal — what should be changed on the block.
    source_type:   "feedback" if aggregated from FeedbackEvents,
                   "benchmark" if triggered by a detected regression.
    source_id:     UUID of the triggering FeedbackEvent or BenchmarkReport.
    """

    id: UUID
    source_type: ProposalSource
    source_id: UUID
    proposed_diff: dict[str, Any]
    status: ProposalStatus = "pending"
    reviewed_by: UUID | None = None
    reviewed_at: datetime | None = None
    created_at: datetime


# -- Benchmarks: quality measurement and regression detection ---------------

ReportStatus = Literal["pending", "approved", "regression_flagged"]


class BenchmarkReport(BaseModel):
    """Result of a benchmark run for a workflow.

    summary: run metrics — e.g. BLEU/ROUGE scores, latencies,
             comparison with calibration_anchor blocks.
    langfuse_trace_id: trace ID in Langfuse for full
                       execution details including token counts and intermediate results.
    approved_by: UUID of the reviewer who confirmed/approved the regression.
    """

    id: UUID
    workflow_id: UUID | None = None
    run_date: date
    status: ReportStatus = "pending"
    summary: dict[str, Any] = {}
    langfuse_trace_id: str | None = None
    approved_by: UUID | None = None
    approved_at: datetime | None = None
    created_at: datetime


# -- Benchmark runs and measurements (ADR-043, CHY-67) ---------------------
#
# The vocabularies mirror the CHECKs of migration 028;
# tests/test_status_vocabularies.py holds the two together.

BenchmarkMode = Literal["drift", "ab_adaptation"]
BenchmarkRunStatus = Literal["running", "complete", "failed"]
MeasurementVariant = Literal["composed", "raw"]
MeasurementOutcome = Literal["measured", "unreachable", "refused", "error"]


class BenchmarkRunOut(BaseModel):
    """One benchmark run over the active calibration anchors.

    review_needed: the drift comparison found a regression (CHY-68) and no
                   reviewer has settled it yet (CHY-64).
    triggered_by:  the person who started it; empty for the systemd unit.
    """

    id: UUID
    mode: BenchmarkMode
    status: BenchmarkRunStatus
    triggered_by: UUID | None = None
    review_needed: bool
    started_at: datetime
    finished_at: datetime | None = None


class BenchmarkMeasurementOut(BaseModel):
    """One model answering one anchor version, in one variant.

    score is set exactly when outcome is `measured` — an outage is not a
    measurement, and a 0 would read as the worst regression in the table
    (migration 028 enforces it). detail says why there is no score.
    """

    id: UUID
    anchor_block_id: UUID
    anchor_version: int
    model: str
    variant: MeasurementVariant
    outcome: MeasurementOutcome
    score: float | None = None
    scores: dict[str, float] = {}
    output: str | None = None
    detail: str | None = None
    latency_ms: int | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    created_at: datetime


class BenchmarkRunDetail(BenchmarkRunOut):
    """A run with everything it measured, in the order it measured it."""

    measurements: list[BenchmarkMeasurementOut]


class BenchmarkRunList(BaseModel):
    items: list[BenchmarkRunOut]
    total: int
    limit: int
    offset: int
