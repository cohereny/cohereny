"""
api/v1/_planned.py

Routes that exist in the contract but not yet in the code (CHY-28).

Five routers — workflows, merge_requests (removed in CHY-65), feedback,
benchmarks, scopes — were
written as placeholders during Phase 1 and answered:

    HTTP 200  {"status": "not_implemented"}

200 means "here is your result". A generated client parses that body as a
successful response, a Prism mock serves it as the contract, and a caller has
no way to tell a working endpoint from an empty shell. That was 29 of the
API's 54 operations, which is to say: more than half of the published contract
claimed to work and did not.

501 is the status HTTP already has for exactly this — the server recognises the
method and has not implemented it. The routes stay rather than being deleted:
their docstrings are real design work (workflows.py explains the workflow-as-
data decision), and a contract that shows what is planned is more useful to the
UI than one that pretends the surface does not exist. Each one names the ticket
that will implement it, in the response body and in the spec.

Why a helper instead of writing it out 29 times:
  Because it would be written out 29 times, and the thirtieth would differ.

Called by:
  - api/v1/{workflows,feedback,benchmarks,scopes}.py

Dependencies:
  - fastapi
"""
from __future__ import annotations

from typing import Any, NoReturn

from fastapi import HTTPException, status
from pydantic import BaseModel, Field


class NotImplementedDetail(BaseModel):
    """Body of a 501. Names the ticket so the caller can look up the plan."""

    detail: str = Field(..., examples=["Not implemented yet — see CHY-82"])
    ticket: str = Field(..., examples=["CHY-82"])


_PLANNED_RESPONSES: dict[int | str, dict[str, Any]] = {
    501: {
        "model": NotImplementedDetail,
        "description": "Planned, not implemented. The body names the ticket.",
    }
}


def planned(ticket: str) -> dict[str, Any]:
    """Decorator arguments for a route that is in the contract, not in the code.

    Used as `@router.get("", **planned("CHY-82"))`. Sets the declared status to
    501 so the generated spec carries no 200, and marks the operation with a
    vendor extension — `x-` keys are the sanctioned way to say something OpenAPI
    has no field for, and "this operation is planned" is such a thing.
    """
    return {
        "status_code": status.HTTP_501_NOT_IMPLEMENTED,
        "responses": _PLANNED_RESPONSES,
        "openapi_extra": {
            "x-cohereny-status": "planned",
            "x-cohereny-ticket": ticket,
        },
    }


def not_implemented(ticket: str) -> NoReturn:
    """Raise the 501. Typed NoReturn so a body that forgets to return is fine."""
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail={"detail": f"Not implemented yet — see {ticket}", "ticket": ticket},
    )
