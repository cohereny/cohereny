"""
contexts/repository/block.py

The rules of a context block — what may happen to it, and in which state.

The aggregate of ADR-030: it decides, it does not execute. Nothing here opens a
connection, writes a row or knows that HTTP exists.

**The state machine used to be four sets of function arguments.** submit,
withdraw, approve and reject each passed `to_status`, `allowed_from`, `action`
and a role into a shared `_transition` helper, so the transition table existed
only in the reader's head — assembled from four call sites 80 lines apart. It
is one table now, which is also what makes it checkable against migration 013
instead of merely consistent with it by habit.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Any

from contexts.repository.schema import EDITABLE_STATUS, BlockStatus
from contexts.repository.errors import (
    BlockTypeImmutable,
    InvalidTransition,
    NothingPublished,
    PromotionTargetInvalid,
    UnknownStatus,
    VersionConflict,
)
from kernel.audit import (
    ACTION_BLOCK_APPROVED,
    ACTION_BLOCK_REJECTED,
    ACTION_BLOCK_SUBMITTED,
    ACTION_BLOCK_WITHDRAWN,
)
from kernel.scopes import ReviewMode, review_mode

#: Every status a block row can carry. Derived, not repeated (CHY-95): this
#: used to be a second list of the same five strings, and two lists of one
#: vocabulary agree until the day one of them is extended.
ALL_STATUSES: tuple[str, ...] = tuple(BlockStatus)

#: What a list without an explicit filter shows. A positive list, not
#: `!= 'deprecated'`: the blacklist would have started returning pending_review
#: and rejected blocks the moment migration 013 landed, without any client
#: asking for them.
#:
#: Deliberately *not* derived — this is a policy about what a caller sees by
#: default, not the vocabulary. Deriving it would tie an editorial decision to
#: the shape of the enum and change what listings return whenever a state is
#: added.
LISTED_BY_DEFAULT: tuple[str, ...] = (
    BlockStatus.DRAFT, BlockStatus.PENDING_REVIEW, BlockStatus.ACTIVE,
)


@dataclass(frozen=True)
class Transition:
    """One move in the review workflow (CHY-32)."""

    to: str
    allowed_from: tuple[str, ...]
    #: Minimum role. Writes need one because a consumer could otherwise retire
    #: the active version of an org-wide prompt — a gate with an open side door
    #: is not a gate (CHY-33).
    role: str
    action: str


#: The review workflow, in one place.
TRANSITIONS: dict[str, Transition] = {
    # Hand a draft to review. Also the way back in after a rejection.
    "submit": Transition(
        to=BlockStatus.PENDING_REVIEW,
        allowed_from=(BlockStatus.DRAFT, BlockStatus.REJECTED),
        role="contributor", action=ACTION_BLOCK_SUBMITTED,
    ),
    # Take a submission back before anyone has decided on it. Without this the
    # only way to cancel a review would be DELETE — retiring the block, which
    # is a different thing entirely.
    "withdraw": Transition(
        to=BlockStatus.DRAFT, allowed_from=(BlockStatus.PENDING_REVIEW,),
        role="contributor", action=ACTION_BLOCK_WITHDRAWN,
    ),
    # The only way content reaches a live prompt.
    "approve": Transition(
        to=BlockStatus.ACTIVE, allowed_from=(BlockStatus.PENDING_REVIEW,),
        role="reviewer", action=ACTION_BLOCK_APPROVED,
    ),
    # Send it back with a reason. The reason lands in the audit entry, which
    # the author may read — it is the only artefact of the decision they get.
    "reject": Transition(
        to=BlockStatus.REJECTED, allowed_from=(BlockStatus.PENDING_REVIEW,),
        role="reviewer", action=ACTION_BLOCK_REJECTED,
    ),
}


class PatchPlan(enum.Enum):
    """What a PATCH means for a block in a given state (CHY-30)."""

    EDIT = "edit"
    """Draft — changed in place. It was never published, so there is no
    history to protect."""

    SUPERSEDE = "supersede"
    """Anything else — a new version is published as a draft and the previous
    one retires. The caller sees the difference as 200 versus 201."""


class DeletePlan(enum.Enum):
    ALREADY_RETIRED = "already_retired"
    """Repeating a DELETE is not a second decision. Succeed without writing:
    each repeat would otherwise append another unremovable audit entry."""

    RETIRE = "retire"


def check_version(*, current: int, expected: int) -> None:
    if current != expected:
        raise VersionConflict(sent=expected, current=current)


def check_transition(*, current_status: str, transition: Transition) -> None:
    if current_status not in transition.allowed_from:
        raise InvalidTransition(current_status, transition.to, transition.allowed_from)


def check_status_filter(value: str) -> str:
    if value not in ALL_STATUSES:
        raise UnknownStatus(value, ALL_STATUSES)
    return value


def affects_composition(from_status: str, to_status: str) -> bool:
    """Whether this status change alters what the scope composes.

    Only a move into or out of `active` does, and only those need to invalidate
    the compiled-context cache. Anything else would bump a generation counter
    for a change nobody can observe.
    """
    return BlockStatus.ACTIVE in (from_status, to_status)


def plan_patch(current_status: str) -> PatchPlan:
    return PatchPlan.EDIT if current_status == EDITABLE_STATUS else PatchPlan.SUPERSEDE


def check_block_type(*, current: str, requested: str | None) -> None:
    if requested is not None and requested != current:
        raise BlockTypeImmutable


def plan_deletion(versions: list) -> tuple[DeletePlan, list[int]]:
    """Decide what deleting a block line means, and which versions it touches.

    Takes every row of the line, newest first — not just the current one, and
    that is the whole point. Several versions can be `active` at once by
    design: superseding leaves the predecessor active so it keeps being
    composed while its successor is in review. Retiring only the current
    version therefore made the *previous* one win the "newest active" query,
    and a retired block reappeared with its old content. Found by the
    end-to-end run on 2026-08-13, not by any unit test.

    Returns the plan and the versions that were still composable, so the audit
    entry can say what was actually taken out of service.
    """
    active = [r["version"] for r in versions if r["status"] == BlockStatus.ACTIVE]
    if active:
        return DeletePlan.RETIRE, active
    if versions[0]["status"] == BlockStatus.DEPRECATED:
        return DeletePlan.ALREADY_RETIRED, []
    raise NothingPublished(versions[0]["status"])


def merge_patch(current: dict, *, content, metadata, tags) -> dict[str, Any]:
    """Fields the caller did not send are carried over, not cleared.

    A PATCH names what should change; everything it stays silent about is meant
    to stay as it was. A new version that silently dropped the tags of the old
    one would be data loss dressed as an edit.
    """
    return {
        "content": content if content is not None else current["content"],
        "metadata": metadata if metadata is not None else current["metadata"],
        "tags": tags if tags is not None else list(current["tags"]),
    }


def check_promotion(chain, *, source_scope_id, target_scope_id) -> None:
    """Promote goes up, into a scope that reviews (ADR-042 point 3).

    `chain` is the block's scope and its ancestors, as kernel.scopes returns
    it. Three refusals, each with the sentence a caller can act on:

    - the block's own scope: a copy next to the original is a second source
      for the same content, not a promotion
    - a scope not above it: a sibling or a descendant is not where proven
      content is shared, and a scope that does not exist is not above anything
    - a direct scope: the copy would sit there as a draft in a scope that
      never leaves one standing — writing the block there is the direct way
    """
    if target_scope_id == source_scope_id:
        raise PromotionTargetInvalid(target_scope_id, "it is the block's own scope")
    target = next((r for r in chain if r["id"] == target_scope_id), None)
    if target is None:
        raise PromotionTargetInvalid(target_scope_id, "it is not a scope above the block")
    if review_mode(target["node_type"]) is not ReviewMode.STRICT:
        raise PromotionTargetInvalid(
            target_scope_id,
            f"a {target['node_type']} scope activates on write, so create the block there instead",
        )
