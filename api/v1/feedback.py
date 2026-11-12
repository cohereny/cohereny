"""
api/v1/feedback.py

Endpoints for user feedback and the learning loop.

Users can rate individual workflow executions (thumbs up/down + free text).
The system automatically aggregates feedback events into LearningProposals when:
  - Multiple negative ratings arrive for the same workflow node, or
  - A benchmark run detects a regression

LearningProposals must be manually approved by a reviewer before any change
to context blocks is made (human-in-the-loop).

execution_id in POST /executions/.../feedback is the Langfuse Trace ID —
this directly links feedback to the full execution trace so reviewers can
see exactly what went wrong.

Phase 2 — implementation pending.

Called by:
  - End-user clients (rating after workflow execution)
  - Admin clients (LearningProposal review)

Dependencies:
  - api.deps          (require_auth, CurrentUser)
  - contexts.learning.schema (FeedbackEvent, LearningProposal)
  - core.db           (get_pool)
"""
from fastapi import APIRouter, Depends
from api.deps import CurrentUser, require_auth
from api.v1._planned import not_implemented, planned

router = APIRouter()


@router.post("/executions/{execution_id}/feedback", **planned("CHY-40"))
async def add_feedback(execution_id: str, actor: CurrentUser = Depends(require_auth)) -> None:
    not_implemented("CHY-40")


@router.get("/learning-proposals", **planned("CHY-40"))
async def list_proposals(actor: CurrentUser = Depends(require_auth)) -> None:
    not_implemented("CHY-40")


@router.post("/learning-proposals/{proposal_id}/approve", **planned("CHY-40"))
async def approve_proposal(proposal_id: str, actor: CurrentUser = Depends(require_auth)) -> None:
    not_implemented("CHY-40")


@router.post("/learning-proposals/{proposal_id}/reject", **planned("CHY-40"))
async def reject_proposal(proposal_id: str, actor: CurrentUser = Depends(require_auth)) -> None:
    not_implemented("CHY-40")
