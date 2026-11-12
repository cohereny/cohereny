"""
contexts/orchestration/votes.py

What the votes at a gate add up to, and how strict a gate is (CHY-176,
CHY-180, ADR-050).

Pure: no database, no clock. The database holds who may vote and how often
(migrations 027 and 043); this module only says what the votes that arrived
mean — so the rule reads in one place and is tested without a server, the
seven cases of Listing 10.2-1 among them.

Two kinds of gate:

- **without areas** — `approver` needs one approval, `four_eyes` two from two
  different people (CHY-176). A rejection ends the run at once
- **with areas** — one vote per area. Until every area has voted, the gate
  waits, a rejection included: the back office may not be overruled, and the
  front office may not be either. Then all approve → released, all reject →
  rejected, **the votes differ → escalated** to a higher authority (BTO 1.1
  Tz. 6), along the escalation edge of the gate
"""
from __future__ import annotations

import hashlib
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

PENDING = "pending"
APPROVED = "approved"
REJECTED = "rejected"
ESCALATED = "escalated"

#: How many different people must approve a gate without areas, by
#: `review_role`. A role not listed — `approver`, or none set — needs one.
APPROVALS_REQUIRED: dict[str | None, int] = {"four_eyes": 2}

#: From this many areas on, a gate's votes can differ: it needs somewhere to
#: escalate to, and it counts as four_eyes — two places, so two people.
AREAS_THAT_CAN_DIFFER = 2

#: The review levels, weakest first (ADR-050 point 1). At `sample` the run does
#: not wait, and a share of the passes is checked afterwards (CHY-223, 3b).
SAMPLE, APPROVER, FOUR_EYES = "sample", "approver", "four_eyes"
LEVELS: tuple[str, ...] = (SAMPLE, APPROVER, FOUR_EYES)

#: How strict a gate is, for comparing an escalation's target with its source
#: (ADR-050) and a gate with the minimum its data and its risk ask for (CHY-223).
#: A gate without a role asks for an approver.
_RANK: dict[str | None, int] = {None: 1, **{level: n for n, level in enumerate(LEVELS)}}

#: The minimum a data class asks (ADR-050 point 2): what may reach a model of
#: lower trust (ADR-041) needs fewer eyes on what comes back.
BY_DATA_CLASS: dict[str, str] = {
    "public": SAMPLE, "internal": SAMPLE, "confidential": APPROVER, "critical": FOUR_EYES,
}

#: The minimum a workflow's risk class asks. A workflow nobody classified counts
#: as `high`: a forgotten classification may not loosen anything.
BY_RISK_CLASS: dict[str | None, str] = {
    "minimal": SAMPLE, "limited": APPROVER, "high": FOUR_EYES, None: FOUR_EYES,
}


@dataclass(frozen=True)
class Minimum:
    """The least a gate must ask for here, and what set it."""

    level: str
    data_class: str
    risk_class: str | None
    #: Which of the two set the level — both, when they agree. Written to the
    #: audit entry, so "why four eyes?" has an answer per run.
    because: tuple[str, ...]

    @property
    def rank(self) -> int:
        return _RANK[self.level]


def minimum(data_class: str, risk_class: str | None) -> Minimum:
    """The stricter of what the data class and the risk class ask (ADR-050 point 2).

    An unknown data class counts as `critical`, as a scope without one does.
    """
    by_data = BY_DATA_CLASS.get(data_class, FOUR_EYES)
    by_risk = BY_RISK_CLASS.get(risk_class, FOUR_EYES)
    level = max(by_data, by_risk, key=LEVELS.index)
    because = tuple(source for source, asked in (("data_class", by_data),
                                                 ("risk_class", by_risk)) if asked == level)
    return Minimum(level, data_class, risk_class, because)


def applies(gate_rank: int, floor: Minimum, *, after_differing_votes: bool) -> str:
    """The level that applies when a run reaches a gate: the stricter of the
    gate and the minimum — unless the run came here because the areas of the
    gate before disagreed. Then they have looked already, and the higher
    authority decides as drawn (Thomas, 09.11.2026)."""
    rank = gate_rank if after_differing_votes else max(gate_rank, floor.rank)
    return LEVELS[rank]


def strictness(review_role: str | None, review_areas: Collection[str] | None) -> int:
    """How many people a gate asks for, as a rank. Two or more areas count as
    four_eyes whatever the role says: they are two people from two places."""
    can_differ = review_areas is not None and len(review_areas) >= AREAS_THAT_CAN_DIFFER
    by_areas = _RANK["four_eyes"] if can_differ else 0
    return max(_RANK.get(review_role, 1), by_areas)


def without_areas(review_role: str | None, decision: str, approvals: int) -> str:
    """The outcome after a vote at a gate without areas (CHY-176)."""
    if decision == REJECTED:
        return REJECTED
    return APPROVED if approvals >= APPROVALS_REQUIRED.get(review_role, 1) else PENDING


def by_areas(areas: Collection[UUID], votes: Mapping[UUID, str]) -> str:
    """The outcome of a gate with areas, from the votes cast so far.

    `votes` maps an area to the decision cast for it — one per area, which the
    database enforces. A vote for an area the gate does not name is not
    counted; the service refuses it before it is written.
    """
    cast = [votes[area] for area in areas if area in votes]
    if len(cast) < len(areas):
        return PENDING
    if all(decision == APPROVED for decision in cast):
        return APPROVED
    if all(decision == REJECTED for decision in cast):
        return REJECTED
    return ESCALATED


def drawn(execution_id: UUID, rate: Decimal | float) -> bool:
    """Whether this pass of a sample gate goes into the review queue (CHY-223).

    A hash of the pass, not a random generator: the same pass is drawn or not
    every time it is asked, so the audit entry that says so can be checked —
    and a test can name a pass that is drawn and one that is not. Over many
    passes the share drawn approaches `rate`; each loop lap is a pass of its
    own and is drawn on its own.
    """
    digest = hashlib.sha256(f"review-sample:{execution_id}".encode()).digest()
    return int.from_bytes(digest[:8], "big") / 2**64 < float(rate)
