"""
contexts/pilots/schema.py

Transport shapes of the pilots context (ADR-030, CHY-104).

Moved here from domain/pilots.py: these models describe how a run enters and
leaves the system, and they belong to the context that owns pilot_runs,
question_bank, run_coverage and transparency_records — not to a shared
domain/ package that every zone reached into.

Published interface of a context is service, schema, errors. This is the
"schema" half: zone 1 may import from here, no other zone may.

Dependencies:
  - pydantic (BaseModel, field validation)
"""
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field


# Mirrors the CHECK on pilot_runs.status (migrations 003 and 006);
# tests/test_status_vocabularies.py holds the two together, because SQL cannot
# import Python.
#
# This was the one vocabulary of fifteen with no Python counterpart at all —
# the six values lived in SQL and as loose strings in five places.
#
# A StrEnum rather than a Literal because these values are *read* in code and
# not only sent over the wire: `status == RunStatus.AWAITING_OVERSIGHT` is a
# typo the interpreter catches, `status == "awaiting_oversight"` is one it does
# not.
#
# The notes stay in a comment. Pydantic publishes a model's docstring as the
# schema description in the OpenAPI contract, and migration numbers and test
# paths are not something a client should read (CHY-105).
class RunStatus(StrEnum):
    """The state of a pilot run."""

    PENDING = "pending"
    """Accepted, not started."""

    RUNNING = "running"

    AWAITING_OVERSIGHT = "awaiting_oversight"
    """Stopped at a human gate. The graph is suspended, not finished — a run
    left here is unreachable until somebody decides (see contexts/pilots/errors)."""

    COMPLETE = "complete"
    FAILED = "failed"

    REJECTED = "rejected"
    """A human refused it at the gate. Deliberately not `failed`: nothing went
    wrong, and conflating the two would make a governance decision look like an
    incident in every report that counts failures."""


#: The states from which nothing further happens. A frozenset rather than a
#: comparison chain, because "is this run over" is asked in four places.
TERMINAL_STATES: frozenset[RunStatus] = frozenset(
    {RunStatus.COMPLETE, RunStatus.FAILED, RunStatus.REJECTED}
)


class PilotRunRequest(BaseModel):
    pilot_name: str = "study"
    input_file: str
    thread_id: str | None = None
    run_config: dict[str, Any] = Field(
        default_factory=dict,
        description="Overrides for this run, by section. Only sections that "
                    "take effect are accepted — an override that would be "
                    "ignored is refused instead (CHY-42).",
    )


class PilotRunOut(BaseModel):
    id: UUID
    thread_id: str
    pilot_name: str
    input_file: str
    status: RunStatus
    started_at: datetime | None
    completed_at: datetime | None
    result_json: dict | None = None
    error_message: str | None
    created_at: datetime
    oversight_pending: bool = False
    oversight_decision: str | None = None
    content_purged_at: datetime | None = Field(
        default=None,
        description="When the retention purge cleared this run's content "
                    "(ADR-046). Null while the content is there.",
    )
    run_config: dict[str, Any] = Field(
        default_factory=dict, description="What the caller asked to override."
    )
    effective_config: dict[str, Any] | None = Field(
        default=None,
        description="What this run actually used. Written once at the start of "
                    "the run and never changed (EU AI Act Art. 12). Null while "
                    "the run is still pending.",
    )


class OversightRequest(BaseModel):
    """The Art. 14 decision at a pilot's gate: the decision and nothing else.

    Unknown fields are refused, not ignored (CHYUI-18): a form posted as
    text/plain can be shaped into JSON with a second field that carries the
    rest of the form, and a body that only looked like a decision passed.
    """

    model_config = ConfigDict(extra="forbid")

    decision: str  # "approved" or "rejected"


# CHY-23: question_bank + coverage models

class QuestionOut(BaseModel):
    id: UUID
    question: str
    topic: str | None
    source: str | None
    difficulty: int


class CoverageOut(BaseModel):
    run_id: UUID
    total_questions: int
    covered_questions: int
    coverage_ratio: float
    uncovered_samples: list[str]  # first 5 uncovered question texts


# ── Workflow configuration (CHY-42) ──────────────────────────────────────────

# The notes stay in comments: pydantic publishes a model docstring as the
# schema description in the OpenAPI contract (CHY-105).
class WorkflowConfigOut(BaseModel):
    pilot_name: str
    version: int = Field(description="Bumped on every write. A run records the "
                                     "version it composed from.")
    model_map: dict[str, str] = Field(
        description="Tier → model id. Takes effect: the pilots read this per run."
    )
    params: dict[str, Any] = Field(
        description="Thresholds and limits. Stored and served, but not yet in "
                    "effect — the pilots still use their own constants "
                    "(ADR-028). Deliberately absent from a run's "
                    "effective_config until CHY-82 wires it through."
    )
    updated_at: datetime


class WorkflowConfigListOut(BaseModel):
    items: list[WorkflowConfigOut]
    total: int


# A PUT that replaces a section wholesale rather than merging into it: merging
# would make it impossible to *remove* a key, and "send the section you want"
# is the rule a caller can hold in their head. Omitting a section leaves it
# untouched, which is what makes changing only the models a one-line request.
class WorkflowConfigPut(BaseModel):
    model_map: dict[str, str] | None = None
    params: dict[str, Any] | None = None


# ── what the deployment tells the context ─────────────────────────────────────

@dataclass(frozen=True)
class RunEnvironment:
    """Where this deployment keeps things, for a context that must not ask.

    Three values that always travel together and always come from the same
    place — `api.config.settings`. Bundling them is the same move CHY-96 made
    with RunSpec, for the same reason: a context reaching into `api.config`
    would be a zone violation, so the transport hands them over, and eight
    parameters on one function is where that starts to hurt.

    In `schema.py` and not next to RunSpec in `execution.py`, although that is
    where it reads most naturally. Only service, schema and errors are
    published; the transport constructs this, so putting it in `execution.py`
    made `api/v1/pilots.py` import a module it may not see. The boundary guard
    said so within the minute — and the quality gate did not, which is its own
    finding (CHY-42).
    """

    input_dir: str | Path
    db_dsn: str
    node_metrics: bool = False
