"""
contexts/pilots/run.py

The rules of a pilot run — what may happen to it, and in which state.

This is the aggregate of ADR-030: it decides, it does not execute. Nothing in
here opens a connection, writes a row or knows that HTTP exists. That is the
whole point of the split. At 48 % coverage in api/v1/pilots.py every test of a
rule needed the full stack, because rule and execution were the same function;
the rules below are testable with a dict and a string.

"The aggregate decides, the use case executes" — service.py does the second half.
"""
from __future__ import annotations

import enum
from pathlib import Path
from uuid import UUID

from contexts.pilots.errors import (
    InvalidDecision,
    NotAwaitingOversight,
    RunNotCancellable,
    RunNotFound,
    RunNotOwned,
    UnknownPilot,
)
from contexts.pilots.schema import TERMINAL_STATES, CoverageOut, RunStatus

#: Pilots this deployment can start. Not configuration — each name corresponds
#: to an installed package (see Dockerfile) whose graph module is imported by
#: execution.py.
KNOWN_PILOTS = frozenset({"study", "banking"})

#: Decisions the oversight gate understands (EU AI Act Art. 14).
DECISIONS = frozenset({"approved", "rejected"})


class Disposition(enum.Enum):
    """What a cancel-or-delete request resolves to for a given run.

    The endpoint used to compute this inline across five branches interleaved
    with SQL, which is why none of the five were covered. Naming the outcomes
    makes the decision assertable on its own.
    """

    DELETE = "delete"
    """Terminal run — remove it, unless Art. 50 records hold it in place."""

    RELEASE_GATE = "release_gate"
    """Blocked on human oversight — release the gate so the thread can finish."""

    CANCEL_IN_FLIGHT = "cancel_in_flight"
    """Running and tracked in this process — signal the thread."""

    MARK_FAILED = "mark_failed"
    """Running per the database but untracked here — the process restarted."""


def check_pilot_known(name: str) -> None:
    """Raise UnknownPilot unless this deployment can run it."""
    if name not in KNOWN_PILOTS:
        raise UnknownPilot(name)


def check_ownership(*, owner_id: UUID | None, actor_id: UUID) -> None:
    """Raise RunNotOwned unless the actor may act on this run.

    A run with no owner predates CHY-88 and is allowed through: refusing those
    would make every old run undeletable by anybody, while every run created
    from now on is owned and protected.
    """
    if owner_id is not None and owner_id != actor_id:
        raise RunNotOwned


def check_decision(decision: str) -> None:
    """Raise InvalidDecision for anything but approved/rejected."""
    if decision not in DECISIONS:
        raise InvalidDecision(decision)


def check_awaiting_oversight(status: str) -> None:
    """Raise NotAwaitingOversight unless the run is at the gate."""
    if status != RunStatus.AWAITING_OVERSIGHT:
        raise NotAwaitingOversight(status)


def plan_disposition(*, status: str, in_flight: bool) -> Disposition:
    """Decide what cancelling or deleting a run in this state means.

    Order matters and is not arbitrary: terminal first (nothing to cancel), then
    the oversight gate (a blocked thread must be released before anything else
    can proceed), then the in-process cancel signal, and only then the fallback
    for a run the database calls running but this process has never heard of.

    Raises RunNotCancellable for a state none of those cover — pending without
    a thread, for instance.
    """
    if status in TERMINAL_STATES:
        return Disposition.DELETE
    if status == RunStatus.AWAITING_OVERSIGHT:
        return Disposition.RELEASE_GATE
    if in_flight:
        return Disposition.CANCEL_IN_FLIGHT
    if status == RunStatus.RUNNING:
        return Disposition.MARK_FAILED
    raise RunNotCancellable(status)


def contain_input_file(base_dir: str | Path, input_file: str) -> Path:
    """Resolve a caller-supplied filename inside the pilot input directory.

    The version before CHY-90 was `Path(input_dir) / body.input_file` followed
    by `.exists()`. Both parts of that are exploitable: `/` keeps an absolute
    right-hand side ("/data/examples" / "/etc/passwd" is "/etc/passwd"), and a
    relative "../../etc/passwd" simply walks out of the directory. The existence
    check then confirms the file and the pilot reads it — and a pilot reads a
    document in order to send it to a language model. Arbitrary file read with
    exfiltration attached, available to any authenticated caller.

    resolve() first, containment second. Checking the string for ".." before
    resolving would miss symlinks; checking after resolving does not — which is
    why this function consults the filesystem even though it decides a rule.

    Raises RunNotFound, deliberately worded as "not found" rather than
    "forbidden": a caller probing for /etc/passwd learns nothing from the
    response about whether it exists.

    A name the operating system cannot even look up -- a NUL byte, a lone
    surrogate that does not encode -- makes resolve() raise ValueError. No file
    can carry that name, so it gets the same answer as any other absent one,
    not a 500 (CHY-213, found by the contract fuzzer).
    """
    base = Path(base_dir).resolve()
    try:
        candidate = (base / input_file).resolve()
    except ValueError:
        candidate = None

    if candidate is None or not candidate.is_relative_to(base):
        raise RunNotFound(f"Input file not found: {input_file}")
    if not candidate.is_file():
        raise RunNotFound(f"Input file not found: {input_file}")
    return candidate


def coverage_from_rows(run_id: UUID, rows: list) -> CoverageOut:
    """Summarise question_bank coverage rows into the reported figures.

    Takes anything with ["covered"] and ["question"] — asyncpg Records in
    production, dicts in tests. The caller has already established that rows is
    non-empty; an empty list here would be a coverage of 0 out of 0, which is
    not a statement anybody wants to read.
    """
    total = len(rows)
    covered = sum(1 for r in rows if r["covered"])
    uncovered = [r["question"] for r in rows if not r["covered"]][:5]
    return CoverageOut(
        run_id=run_id,
        total_questions=total,
        covered_questions=covered,
        coverage_ratio=round(covered / total, 3) if total else 0.0,
        uncovered_samples=uncovered,
    )
