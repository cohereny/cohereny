"""
core/audit.py

Write path for the append-only audit log (CHY-31).

Every state change that a reviewer, an auditor or a supervisory authority might
later ask about goes through here. The table itself is physically append-only
since migration 011 — UPDATE, DELETE and TRUNCATE all raise — so this module is
the only way anything ever gets in.

Why a helper rather than an INSERT at each call site:
  Two reasons that only show up later. Consistent action naming ("block.created",
  not "created_block"), and the ability to add a field — a request id, a source
  system — in one place instead of thirty. The audit log is also the one table
  where a forgotten column is not a bug you can fix afterwards: entries written
  without it stay written without it.

Why the caller passes its own connection:
  The audit entry must share the transaction with the change it records. Writing
  it on a separate connection means a rollback can undo the change and leave the
  entry, or commit the change and lose the entry. Both make the log a liar, and
  a log that is occasionally wrong is harder to use than none at all.

Called by:
  - api/v1/admin.py   (actor anonymization)
  - further call sites as governance lands (CHY-30, CHY-32, CHY-60, CHY-65)

Dependencies:
  - asyncpg
"""
from __future__ import annotations

import json

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel

import asyncpg

# Action naming: "{entity}.{past participle}", lowercase, dot-separated. The
# constants exist so a typo is a NameError at import time rather than a row that
# silently never matches a filter.
ACTION_ACTOR_PROVISIONED = "actor.provisioned"
ACTION_ACTOR_ROLE_CHANGED = "actor.role_changed"
ACTION_ACTOR_ANONYMIZED = "actor.anonymized"
ACTION_BLOCK_CREATED = "block.created"
ACTION_BLOCK_SUPERSEDED = "block.superseded"
ACTION_BLOCK_DEPRECATED = "block.deprecated"
ACTION_BLOCK_SUBMITTED = "block.submitted"
ACTION_BLOCK_WITHDRAWN = "block.withdrawn"
ACTION_BLOCK_APPROVED = "block.approved"
ACTION_BLOCK_REJECTED = "block.rejected"
# The first entry of a block that was copied upward. Names the line and version
# it came from; the block itself carries no link back (ADR-042, CHY-65).
ACTION_BLOCK_PROMOTED = "block.promoted"
ACTION_SCOPE_CREATED = "scope.created"
ACTION_SCOPE_MEMBER_ADDED = "scope.member_added"
# EU AI Act Art. 14. The decision was already stored on the run; without an
# entry here there was no record of who made it (CHY-88).
ACTION_PILOT_RUN_STARTED = "pilot.run_started"
ACTION_PILOT_OVERSIGHT_DECIDED = "pilot.oversight_decided"
ACTION_PILOT_RUN_CANCELLED = "pilot.run_cancelled"
ACTION_PILOT_RUN_DELETED = "pilot.run_deleted"
# A fallback fired: the model that answered is not the one the routing table
# chose. Audited because the routing table records what was INTENDED, and only
# this row records what happened -- a DORA or MaRisk reviewer asks the second
# question, and it cannot be reconstructed afterwards (CHY-87).
#
# Only on the fallback, not on every call. An entry per completion would drown
# the log and say nothing: "the first candidate answered" is the normal case,
# and a log that records the normal case hides the exception.
ACTION_ROUTING_FELL_BACK = "routing.fell_back"
# Which model answers at which tier is a governance decision, not a setting:
# in the banking pilot the map is the local/cloud boundary. It is audited for
# the same reason a block approval is (CHY-42).
ACTION_WORKFLOW_CONFIG_CHANGED = "workflow.config_changed"
# Releasing a graph is the moment a workflow becomes something that runs
# against real data — audited like a block approval, and for the same reason
# (CHY-82).
ACTION_WORKFLOW_CREATED = "workflow.created"
ACTION_WORKFLOW_ACTIVATED = "workflow.activated"
ACTION_WORKFLOW_DEPRECATED = "workflow.deprecated"
# A run and the decision at its gate. The second is an Art. 14 record: it has
# to say who approved, or it is not a record (CHY-127).
ACTION_RUN_STARTED = "workflow.run_started"
ACTION_RUN_OVERSIGHT = "workflow.run_oversight_decided"


def target_ref(entity_type: str, entity_id: UUID | str) -> str:
    """Build the `{type}:{uuid}` reference used by audit_log.target_ref.

    The schema stores one text column rather than the entity_type/entity_id pair
    the original ticket described. That is deliberate and predates this change:
    a pair cannot be covered by a foreign key either (see ADR-025 on polymorphic
    references), so splitting it buys nothing and costs an index.
    """
    return f"{entity_type}:{entity_id}"


async def write_audit(
    conn: asyncpg.Connection,
    *,
    action: str,
    actor_id: UUID | None = None,
    target: str | None = None,
    payload: dict[str, Any] | None = None,
) -> UUID:
    """Append one entry. Returns its id.

    actor_id is nullable because some entries have no human author — a scheduled
    benchmark, a system migration. It is *not* nullable as a convenience for call
    sites that did not bother to resolve the actor; those should resolve it.

    Deliberately no try/except. If the audit write fails, the surrounding
    transaction must fail with it: a state change that could not be recorded is
    exactly the change that must not silently happen.
    """
    return await conn.fetchval(
        "INSERT INTO audit_log (actor_id, action, target_ref, payload) "
        "VALUES ($1, $2, $3, $4::text::jsonb) RETURNING id",
        actor_id,
        action,
        target,
        json.dumps(payload or {}),
    )


# Moved here from domain/governance.py with CHY-106. It describes audit_log,
# which the kernel owns; it lived under `governance` only because merge-request
# approvals were the first thing anybody audited. A schema in the wrong module
# is how a table acquires a second owner.
#
# A comment and not part of the docstring: Pydantic publishes the docstring as
# the schema description, and where a file used to live is no business of an
# API consumer. Caught by the contract diff.
class AuditLogEntry(BaseModel):
    """A single audit log entry — immutable, append-only.

    action:     action identifier (e.g. "block.created", "block.approved")
    target_ref: reference to the affected object (e.g. "block:{uuid}")
    payload:    full context at the time of the action (who, what, with what)

    Important: entries are never updated or deleted. Corrections are written
    as new entries referencing the erroneous original entry. This is a hard
    invariant for GDPR compliance.
    """

    id: UUID
    actor_id: UUID | None = None
    action: str
    target_ref: str | None = None
    payload: dict[str, Any] = {}
    created_at: datetime


async def read_entries(pool, *, target: str | None = None,
                       actor_id: UUID | None = None,
                       limit: int = 100, offset: int = 0) -> list[AuditLogEntry]:
    """A page of the log, newest first, optionally narrowed to one target or actor.

    The only place the log is read from. api/v1/audit.py assembled the query
    itself until CHY-123 — a transport module holding the schema of a
    kernel-owned table.

    **Who may read what is not decided here**, and that is deliberate rather
    than an omission. The rule is "reviewer or higher, or the author of the
    block in question", and authorship belongs to the repository context. The
    kernel uses nobody (ADR-030 zone 3), so it cannot ask. The composition
    stays in the transport layer, which is the only one allowed to see both.
    """
    where, params = "", []
    if target is not None:
        where = "WHERE target_ref = $1"
        params = [target]
    elif actor_id is not None:
        where = "WHERE actor_id = $1"
        params = [actor_id]

    rows = await pool.fetch(
        "SELECT id, actor_id, action, target_ref, payload, created_at "
        f"FROM audit_log {where} "
        f"ORDER BY created_at DESC, id DESC "
        f"LIMIT ${len(params) + 1} OFFSET ${len(params) + 2}",
        *params, limit, offset,
    )
    return [AuditLogEntry(**dict(r)) for r in rows]
