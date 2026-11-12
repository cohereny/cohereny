"""
contexts/repository/four_eyes.py

Who may approve a block that lets data leave the installation (CHY-163, ADR-045).

The aggregate's half: it decides, it does not read. The service gathers the
facts inside the approving transaction — who wrote the version, how many people
may approve, whether the approver is a technical account — and asks here.

**A data-flow block** is one whose approval decides where data may go:

  - a routing rule naming a model below `critical`. Only the router knows a
    model's ceiling, and the repository may not import it (ADR-030), so the
    composition root hands that answer in, as it does `before_activation`
  - a block with `privacy_impact = permissive`, which loosens a privacy rule
    (ADR-017). The repository knows this one itself and asks nobody

Everything else — prompt material, tightening claims, anchors — is approved as
before. Four eyes on every system prompt would make approval a ritual, and a
ritual is clicked through.

**Who wrote a version** is everyone who created, promoted, superseded into or
edited it since the line was last approved. Not only the line's author: a
reviewer who adds a cloud model to someone else's draft and then approves it
has approved their own change. Until CHY-171 neither a create nor a draft edit
left a record, which is why this module could not exist before it.
"""
from __future__ import annotations

import enum
from collections.abc import Callable, Iterable
from typing import Any
from uuid import UUID

from contexts.repository.errors import SecondPersonRequired, ServiceAccountMayNotApprove
from kernel.audit import (
    ACTION_BLOCK_APPROVED,
    ACTION_BLOCK_CREATED,
    ACTION_BLOCK_EDITED,
    ACTION_BLOCK_PROMOTED,
    ACTION_BLOCK_SUPERSEDED,
)

#: The router's answer, handed in by the composition root: does this block name
#: a model below `critical`? Blocks it does not understand answer False —
#: whatever is wrong with them is for the activation check to report.
RoutesBelowCritical = Callable[[Any], bool]

#: Entries that put text into a version.
WRITING_ACTIONS: tuple[str, ...] = (
    ACTION_BLOCK_CREATED, ACTION_BLOCK_PROMOTED, ACTION_BLOCK_SUPERSEDED, ACTION_BLOCK_EDITED,
)
#: What the service reads from the audit log to answer who wrote a version:
#: the writing entries, and the approvals that end a review round.
ACTIONS_READ_FOR_FOUR_EYES: tuple[str, ...] = (*WRITING_ACTIONS, ACTION_BLOCK_APPROVED)


class Verdict(enum.StrEnum):
    """How an approval of a data-flow block satisfied four eyes. Lands in the audit."""

    SECOND_PERSON = "second_person"
    """The approver wrote nothing in this round. Four eyes held."""

    SELF_APPROVED = "self_approved"
    """The approver wrote it and is the only person who may approve. Allowed so a
    one-person installation can work at all — and marked, because nobody second
    checked whether the data may leave."""


def is_data_flow(block: Any, routes_below_critical: RoutesBelowCritical | None) -> bool:
    """Whether approving this block decides where data may go."""
    if block["privacy_impact"] == "permissive":
        return True
    return routes_below_critical is not None and routes_below_critical(block)


def _version_of(entry) -> int:
    """The version an audit entry is about.

    `block.superseded` names the version it created as `to_version`; the
    others say `version`. A promotion says neither — it is always the first
    version of the copy.
    """
    payload = entry.payload or {}
    return int(payload.get("to_version") or payload.get("version") or 1)


def writers_since_last_approval(entries: Iterable, *,
                                line_author_id: UUID | None) -> frozenset[UUID]:
    """Everyone who put text into the line since it was last approved.

    `entries` are the line's audit entries of ACTIONS_READ_FOR_FOUR_EYES, in
    any order. A round starts after the newest approved version: a reviewer who
    wrote version 2, which someone else approved, may approve version 3 written
    by a third person.

    The line's author counts while nothing was ever approved. Lines created
    before CHY-171 have no `block.created` entry, and their first version was
    written by the author all the same.
    """
    entries = list(entries)
    approved_versions = [
        _version_of(entry) for entry in entries if entry.action == ACTION_BLOCK_APPROVED
    ]
    last_approved_version = max(approved_versions, default=0)
    writer_ids = {
        entry.actor_id for entry in entries
        if entry.action in WRITING_ACTIONS
        and entry.actor_id is not None
        and _version_of(entry) > last_approved_version
    }
    if last_approved_version == 0 and line_author_id is not None:
        writer_ids.add(line_author_id)
    return frozenset(writer_ids)


def judge_approval(*, approver_id: UUID, approver_is_service_account: bool,
                   writer_ids: frozenset[UUID], people_who_may_approve: int) -> Verdict:
    """Decide an approval of a data-flow block, or refuse it.

    `people_who_may_approve` counts the approver too; technical accounts never
    count. One is a single operator.
    """
    if approver_is_service_account:
        raise ServiceAccountMayNotApprove
    if approver_id not in writer_ids:
        return Verdict.SECOND_PERSON
    if people_who_may_approve <= 1:
        return Verdict.SELF_APPROVED
    raise SecondPersonRequired(people_who_may_approve)
