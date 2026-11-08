"""
api/v1/_responses.py

Declared error responses for the OpenAPI contract (CHY-28).

Before this, `responses=` appeared **nowhere** in the API: every 403, 404 and
409 the routes raise was invisible in the generated spec, which described a
world where the only failure was a 422 from request validation.

That matters most for 409. `POST /v1/blocks/{id}/approve` answers 409 when the
block is not in `pending_review`, or when the version in the body is not the
one on the server — those are not edge cases, they *are* the review gate
(CHY-32). A client that does not handle 409 has not implemented the workflow,
and until now the contract gave it no reason to know.

Deliberately not applied to all 54 operations. Declaring 401 on every route
that requires auth is true and useless; the value is in the errors a caller has
to *act* on, so this covers the block lifecycle, composition and the audit log.
The rest keeps FastAPI's defaults.

Called by:
  - api/v1/blocks.py, compose.py, complete.py, audit.py, admin.py

Dependencies:
  - fastapi, pydantic
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ErrorDetail(BaseModel):
    """FastAPI's HTTPException body. Named so the spec can point at a schema
    instead of repeating an inline object twelve times."""

    detail: str = Field(..., examples=["Block not found"])


def _err(description: str) -> dict[str, Any]:
    return {"model": ErrorDetail, "description": description}


class FieldError(BaseModel):
    """One entry of FastAPI's request-validation 422."""

    loc: list[str | int]
    msg: str
    type: str


class UnprocessableDetail(BaseModel):
    """A 422 that is either request validation or a rule refusing the request.

    FastAPI documents 422 as a list of field errors, and that is what request
    validation returns. A domain rule that refuses the request says one
    sentence instead — the shape of every other error here. Declared as either,
    because a contract that promises only the list is wrong the first time the
    sentence comes back, and a client generated from it fails to parse exactly
    the error it most needs to show (CHY-166).
    """

    detail: str | list[FieldError]


FORBIDDEN = {403: _err("The actor's role does not permit this.")}

#: Only on routes where a rule, not the schema, can refuse the request.
UNPROCESSABLE = {
    422: {
        "model": UnprocessableDetail,
        "description": (
            "The request is invalid: a field failed validation (detail is a "
            "list), or a rule refused it (detail is a sentence) — for example "
            "a max_tokens above what the routing rule allows, or a routing rule "
            "that could never be used submitted for approval."
        ),
    }
}

# Deliberately not a list of object types. It said "No such block, scope or
# actor" and was already used by four routes that answer for none of those; a
# shared description that enumerates its callers is wrong the moment a fifth
# one uses it, and nobody notices because the contract still generates (CHY-122).
NOT_FOUND = {404: _err("The requested object does not exist.")}

# The 409s the block lifecycle produces. Kept in one entry because OpenAPI has
# one slot per status code, and a caller distinguishes them by the message, not
# by the code.
#
# Not enumerated as a list of causes for its own sake: the point of naming the
# ratchet here is that a client which only handles "wrong version" will retry a
# governance refusal forever. Those two need different handling, and a contract
# that says only "conflict" hides that (CHY-62).
CONFLICT = {
    409: _err(
        "The transition is not allowed from the block's current status, or the "
        "version in the body is not the current one (CHY-32) — or approving "
        "the block would loosen a privacy rule stated further up the scope "
        "tree, which is refused outright and is not resolved by retrying "
        "(ADR-017, CHY-62). In a direct scope a create or an edit approves "
        "the block itself, so it can meet the second refusal too (ADR-042)."
    )
}

# Promote copies what is live (ADR-042). A block without an active version has
# nothing to copy — a 409, because the block's state refuses it, not the body.
PROMOTE_CONFLICT = {
    409: _err(
        "The block has no active version. Only what is in use can be promoted; "
        "a draft in a strict scope is submitted where it is (ADR-042)."
    )
}

# What a routing decision can refuse (CHY-124). Found by the E2E's contract
# check on its first run: /v1/complete answered 409 for a task type without a
# rule, and the contract listed 200, 403, 404 and 422. A client generated from
# that contract had no way to know the most important refusal of ADR-038.
ROUTING_CONFLICT = {
    409: _err(
        "No approved routing rule covers this task type in the scope or any of "
        "its ancestors (ADR-038) — or a candidate of the winning rule may not "
        "see the class of data the scope holds (ADR-041, CHY-160). Neither is "
        "resolved by retrying: a rule has to be created, approved or changed."
    )
}

# The model gateway, when it is the thing that failed (ADR-035). Documented
# because the E2E accepts exactly these two as "the gateway is not reachable
# from here" — a status a caller is told to expect must be in the contract.
GATEWAY = {
    502: _err("Every candidate of the routing chain refused (ADR-035)."),
    503: _err("The model gateway did not answer."),
}

# The combinations the routes actually use, so a call site reads as one name.
BLOCK_READ = {**NOT_FOUND}
BLOCK_WRITE = {**FORBIDDEN, **NOT_FOUND}
BLOCK_TRANSITION = {**FORBIDDEN, **NOT_FOUND, **CONFLICT}

#: The header that tells a client a data-flow block was approved by the person
#: who wrote it (ADR-045). A header, not a field: the notice belongs to this one
#: response, and a field on BlockOut would be empty on every other read.
SELF_APPROVAL_HEADER = "Cohereny-Notice"

#: Approve, which four eyes can refuse besides the role (CHY-163), and whose
#: success may carry the self-approval notice.
BLOCK_APPROVE = {
    **BLOCK_TRANSITION,
    **UNPROCESSABLE,
    403: _err(
        "The actor's role does not permit this — or four eyes refuses it: the "
        "block decides where data may go, and the approver wrote this version "
        "while somebody else could approve it, or is a technical account "
        "(ADR-045)."
    ),
    200: {
        "description": "The block is active.",
        "headers": {
            SELF_APPROVAL_HEADER: {
                "description": (
                    "Present when a single operator approved a data-flow block "
                    "they wrote: nobody second checked whether data may leave "
                    "the installation (ADR-045). Absent otherwise."
                ),
                "schema": {"type": "string"},
            },
        },
    },
}
# A create can meet the ratchet since ADR-042 (direct scopes approve on write),
# and a rule it cannot store or approve is a 422 (CHY-165, CHY-166).
BLOCK_CREATE = {**FORBIDDEN, **NOT_FOUND, **CONFLICT, **UNPROCESSABLE}
