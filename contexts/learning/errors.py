"""
contexts/learning/errors.py

What the learning context refuses (ADR-030, ADR-043).

Domain errors, not HTTP: the status codes are assigned once, in api/main.py,
like every other context's.
"""
from __future__ import annotations

from uuid import UUID


class LearningError(Exception):
    """Base of every refusal this context makes."""


class MalformedAnchor(LearningError):
    """A calibration anchor that cannot be read as a test case (CHY-67).

    Raised by the reading itself. At approval it is wrapped into
    AnchorNotApprovable — there a person can fix it. Met at run time, an active
    anchor that does not read is the governance failing, not the caller.
    """

    def __init__(self, block_id: UUID, reason: str) -> None:
        self.block_id = block_id
        self.reason = reason
        super().__init__(f"calibration anchor {block_id} is malformed: {reason}")


class AnchorNotApprovable(LearningError):
    """A calibration anchor, as submitted, could never be measured.

    The same defects MalformedAnchor reports, found while someone is approving
    it — the counterpart of RoutingRuleNotApprovable (CHY-166). Without the
    check the anchor would show as `active` and fail at the first benchmark
    run, found by a job at night rather than by the reviewer who could have
    caught it.
    """

    def __init__(self, block_id: UUID, reason: str) -> None:
        self.block_id = block_id
        super().__init__(f"calibration anchor {block_id} cannot be approved: {reason}")


class BenchmarkRunNotFound(LearningError):
    def __init__(self, run_id: UUID) -> None:
        self.run_id = run_id
        super().__init__(f"benchmark run {run_id} not found")
