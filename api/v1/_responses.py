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
  - api/main.py (document_unprocessable, on the whole schema)

Dependencies:
  - fastapi, pydantic
"""
from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field


class ErrorDetail(BaseModel):
    """FastAPI's HTTPException body. Named so the spec can point at a schema
    instead of repeating an inline object twelve times."""

    detail: str = Field(..., examples=["Block not found"])


def _err(description: str) -> dict[str, Any]:
    return {"model": ErrorDetail, "description": description}


class FieldError(BaseModel):
    """One field that failed request validation: where, why, and what was sent."""

    loc: list[str | int]
    msg: str
    type: str
    # Both were in FastAPI's own ValidationError, which this model replaces in
    # the whole contract since CHY-168; left out, the contract would stop
    # mentioning two fields the answer carries. Optional, because `ctx` comes
    # only with some errors.
    input: Any = None
    ctx: dict[str, Any] | None = None


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

#: On routes whose rules have something specific to say about their 422. Every
#: other operation that takes input gets the general wording from
#: document_unprocessable() below; the shape is the same everywhere.
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

#: What FastAPI documents on its own for every operation that takes input: the
#: list of field errors, and nothing else.
_FASTAPI_422 = "#/components/schemas/HTTPValidationError"
_UNPROCESSABLE_REF = {"$ref": "#/components/schemas/UnprocessableDetail"}

UNPROCESSABLE_ANYWHERE = (
    "The request is invalid: a field failed validation (detail is a list), or "
    "a rule or the database refused a value in it (detail is a sentence) — a "
    "NUL character in any text field is refused that way on every operation."
)


def document_unprocessable(spec: dict[str, Any]) -> dict[str, Any]:
    """Document every 422 in the shape that actually comes back (CHY-168).

    FastAPI adds a 422 to every operation that takes input and documents it as
    a list of field errors. That is true for request validation and false for
    everything else that answers 422 with a sentence: a domain rule mapped in
    api/main.py, and the NUL handler there, which any text field of any
    request can reach (CHY-165). A client generated from the list-only
    contract fails to parse exactly the message it should show.

    One rule, applied to the generated schema, instead of a declaration on
    every route: the cause is global, so its documentation is too, and a new
    route is right without anyone remembering it. tests/test_unprocessable.py
    checks the result in the contract, not this mechanism — if FastAPI ever
    renames its schema and this finds nothing to replace, that test fails.

    Changes `spec` in place and returns it. A second call changes nothing,
    which matters because FastAPI caches the schema and hands back the same
    dict.
    """
    for item in spec.get("paths", {}).values():
        for operation in item.values():
            response = operation.get("responses", {}).get("422")
            if response is None:
                continue
            schema = response.get("content", {}).get("application/json", {}).get("schema", {})
            if schema.get("$ref") == _FASTAPI_422:
                response["description"] = UNPROCESSABLE_ANYWHERE
            elif "content" in response:
                continue                    # the route's own model, e.g. UNPROCESSABLE
            # Replaced, or declared with a description only: keep the words,
            # give them the shape.
            response["content"] = {"application/json": {"schema": dict(_UNPROCESSABLE_REF)}}

    schemas = spec.setdefault("components", {}).setdefault("schemas", {})
    if "UnprocessableDetail" not in schemas:
        # Present whenever a route uses UNPROCESSABLE; added here so the rule
        # does not depend on one of them still doing so.
        own = UnprocessableDetail.model_json_schema(ref_template="#/components/schemas/{model}")
        schemas.update(own.pop("$defs", {}))
        schemas["UnprocessableDetail"] = own
    # FastAPI's two models, once nothing points at them any more. Checked in
    # that order: ValidationError is referenced only by HTTPValidationError.
    for name in ("HTTPValidationError", "ValidationError"):
        rest = {k: v for k, v in spec.items() if k != "components"}
        rest["schemas"] = {k: v for k, v in schemas.items() if k != name}
        if f'"#/components/schemas/{name}"' not in json.dumps(rest):
            schemas.pop(name, None)
    return spec

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

# What /v1/complete refuses besides the routing decision (CHY-199). One slot per
# status code: the routing refusals and the scope's refusal share the 409, and
# both are resolved by a decision about the scope, never by retrying.
COMPLETE_CONFLICT = {
    409: _err(
        "No approved routing rule covers this task type in the scope or any of "
        "its ancestors (ADR-038) — or a candidate of the winning rule may not "
        "see the class of data the scope holds (ADR-041, CHY-160) — or an "
        "answer in this scope needs review before it is used: its data class or "
        "the risk class of direct calls asks for more than sample, and "
        "/v1/complete has no gate (ADR-050, CHY-199). None of these is resolved "
        "by retrying; the last by a workflow with a review gate, or by an "
        "architect classifying direct calls in the scope."
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
