"""
contexts/repository/errors.py

What can go wrong in the repository context, expressed without HTTP
(ADR-030, CHY-105).

api/v1/blocks.py raised `HTTPException` nine times, and every one of them put a
status code inside a rule. The consequence was not cosmetic: **the whole
approval lifecycle was reachable only over HTTP.** Neither the CLI, nor a
background job, nor the runner of ADR-028 could submit, approve or reject a
block — although "what is approved" is the central fact of this product.

The status codes are unchanged. Only the place where they are chosen has moved
to api/main.py, where HTTP exists.
"""
from __future__ import annotations

from typing import ClassVar



class RepositoryError(Exception):
    """Base class, so the transport layer can catch the family in one handler."""


class BlockNotFound(RepositoryError):
    def __init__(self) -> None:
        super().__init__("Block not found")



class VersionConflict(RepositoryError):
    """The caller decided about a version that is no longer the current one.

    The reason this check exists at all: a reviewer approves a specific text.
    If the author patches the block between the reviewer's GET and their POST,
    the patch supersedes the row that was read — and the reviewer would approve
    something they never saw.
    """

    def __init__(self, sent: int, current: int) -> None:
        self.sent = sent
        self.current = current
        super().__init__(
            f"Block has moved on: you sent version {sent}, the current one is "
            f"{current}. Re-read it before deciding."
        )


class InvalidTransition(RepositoryError):
    """The block is not in a state this transition can start from.

    Migration 013 enforces the same table. This exists to produce a
    comprehensible refusal rather than a plpgsql exception surfacing as a 500 —
    the database is the wall, not the explanation.
    """

    def __init__(self, current: str, to_status: str, allowed_from: tuple[str, ...]) -> None:
        self.current = current
        self.to_status = to_status
        self.allowed_from = allowed_from
        super().__init__(
            f"Block is {current}, so it cannot become {to_status}. "
            f"Expected one of: {', '.join(allowed_from)}."
        )


class BlockTypeImmutable(RepositoryError):
    """block_type is an identity column, constant across a version line.

    Migration 012. Carrying a changed type into a new version would quietly
    keep the old one, so the refusal is explicit.
    """

    def __init__(self) -> None:
        super().__init__(
            "block_type is immutable across versions of a block. Create a new "
            "block instead of changing the type of an existing one."
        )


class NothingPublished(RepositoryError):
    """Retiring a block that never went live.

    A draft or a submission is cancelled with withdraw, not retired — those are
    different acts and the audit log must not conflate them.
    """

    def __init__(self, current: str) -> None:
        self.current = current
        super().__init__(
            f"Only a published block can be retired; the current version is "
            f"{current}. Use withdraw to cancel a submission."
        )


class NothingToPromote(RepositoryError):
    """Promoting a block that has no live version.

    Promote carries upward what proved itself in use (ADR-011). A block that
    was never active has proved nothing yet — and a draft in a strict scope is
    submitted where it is, not copied elsewhere.
    """

    def __init__(self, current: str) -> None:
        self.current = current
        super().__init__(
            f"Only a block with an active version can be promoted; the current "
            f"version is {current}."
        )


class PromotionTargetInvalid(RepositoryError):
    """The target of a promotion is not a strict scope above the block (ADR-042)."""

    def __init__(self, target, reason: str) -> None:
        self.target = target
        super().__init__(f"Cannot promote into scope {target}: {reason}.")


class UnknownStatus(RepositoryError):
    """A status filter value that is not one of the five.

    Passed through before, which made `?status=Active` return zero rows in
    silence instead of saying the value is wrong.
    """

    def __init__(self, value: str, known: tuple[str, ...]) -> None:
        self.value = value
        super().__init__(
            f"Unknown status {value!r}. Expected one of: {', '.join(known)}"
        )


class UnknownGovernanceKey(RepositoryError):
    """A governance_key that is not in the vocabulary (CHY-62).

    The whole reason the key is a foreign key rather than free text: a typo
    would otherwise make two blocks look like they discuss different rules,
    and the ratchet would compare nothing while reporting success. The database
    refuses it too — this exists so the refusal arrives as a sentence instead
    of a foreign-key violation surfacing as a 500.
    """

    def __init__(self, value: str, known: tuple[str, ...]) -> None:
        self.value = value
        super().__init__(
            f"Unknown governance key {value!r}. Expected one of: "
            f"{', '.join(known)}. New keys are added by migration, deliberately."
        )


class BlockNotStorable(RepositoryError):
    """The table refused this block, and can say why (CHY-165).

    The same move `NodeNotStorable` made for `workflow_nodes` in CHY-134: a
    CHECK that spans two columns cannot live in the request model, and without
    this it arrived as a `CheckViolationError` and left as a 500. Schemathesis
    found `context_blocks_governance_coherent` on its first run: a
    `governance_key` without a `privacy_impact` is a claim that points nowhere.
    """

    REASONS: ClassVar[dict[str, str]] = {
        "context_blocks_governance_coherent":
            "governance_key, privacy_impact and locked belong together — either "
            "all empty, or a key with its impact (ADR-017). A key without an "
            "impact is a claim about a rule that says nothing, and `locked` "
            "without a key is locked against nothing.",
    }

    def __init__(self, constraint: str) -> None:
        self.constraint = constraint
        reason = self.REASONS.get(
            constraint, f"the row violates {constraint}, which this block does not satisfy"
        )
        super().__init__(f"This block cannot be stored: {reason}")


class BlockOutsideScope(RepositoryError):
    """An explicit block list named a block the request's scope may not compose.

    /v1/complete decides the model from `scope_id` and checks it against that
    scope's data class. An explicit `context_block_ids` list used to be loaded
    without any scope filter, so a caller could name a scope whose rule allows
    a hosted model and attach blocks from a `critical` scope elsewhere in the
    tree — their content went to the hosted model (CHY-196). The same goes for
    a block type composition never serves, such as a routing rule.

    Refused rather than silently dropped: a caller who asked for a block and
    got an answer without it would not know the answer was built from less
    than they sent.
    """

    def __init__(self, count: int) -> None:
        self.count = count
        super().__init__(
            f"context_block_ids may only name composable blocks from the scope "
            f"chain of scope_id; {count} of the named blocks are not. The model "
            f"is chosen and checked for scope_id, so its context must come from there."
        )


class PrivacyRatchetViolation(RepositoryError):
    """A permissive block below a restrictive ancestor with the same key.

    The rule of ADR-017: privacy statements may be tightened as they move down
    the scope tree, never loosened. The worked example is a project block
    switching cloud routing back on against a healthcare template that switched
    it off.

    Refused rather than resolved by precedence, because composition has no
    precedence to apply: `as_prompt` concatenates every block shallow to deep
    and the deeper one merely comes later. Whichever way an LLM read the two,
    the org-level promise would no longer hold — and a promise that depends on
    how a model weighs two paragraphs is not a control.
    """

    def __init__(self, *, key: str, ancestor_scope, ancestor_block) -> None:
        self.key = key
        self.ancestor_scope = ancestor_scope
        self.ancestor_block = ancestor_block
        super().__init__(
            f"Cannot loosen {key!r}: block {ancestor_block} in scope "
            f"{ancestor_scope} states it restrictively, and a privacy rule may "
            f"only be tightened further down the tree (ADR-017). Change it "
            f"where it is stated, or state a different rule."
        )


class GovernanceKeyLocked(RepositoryError):
    """An ancestor declared this key settled, and settled means settled.

    Stronger than the ratchet on purpose: `locked` refuses a descendant claim
    on the same key even when it would tighten. A team restating an org rule —
    however strictly — creates a second place where one rule lives, and two
    sources for one truth drift. A tightening that is genuinely needed belongs
    in the locked block's own scope, or under a key of its own.
    """

    def __init__(self, *, key: str, ancestor_scope, ancestor_block) -> None:
        self.key = key
        self.ancestor_scope = ancestor_scope
        self.ancestor_block = ancestor_block
        super().__init__(
            f"Governance key {key!r} is locked by block {ancestor_block} in "
            f"scope {ancestor_scope}. No scope below may state it — not even "
            f"more strictly. Change it there, or use a different key."
        )



class GovernanceNotEditable(RepositoryError):
    """Governance was changed in the same patch that superseded the block.

    Migration 016 freezes the three columns outside `draft`, so this could not
    have succeeded — but it would have failed after the new version was already
    written, which is a confusing way to learn that the claim did not change.

    The two-step shape is deliberate. Publishing a new version and restating
    what it governs are separate decisions, and a reviewer should see the
    second as a change rather than as a side effect of the first.
    """

    def __init__(self, fields: list[str]) -> None:
        self.fields = fields
        super().__init__(
            f"Cannot change {', '.join(sorted(fields))} while superseding a "
            f"published block. Patch the content first — that produces a draft "
            f"— then change the governance on that draft."
        )


class SecondPersonRequired(RepositoryError):
    """The approver wrote this data-flow block, and someone else could approve it.

    Four eyes where data changes place (CHY-163, ADR-045): a routing rule that
    names a model below `critical`, or a block that loosens a privacy rule, is
    approved by a person who wrote nothing in this round. A single operator is
    exempt — the refusal says how many people could approve instead.
    """

    def __init__(self, people_who_may_approve: int) -> None:
        self.people_who_may_approve = people_who_may_approve
        super().__init__(
            "You wrote this version, and it decides where data may go: a routing "
            "rule with a model below 'critical' or a block that loosens a privacy "
            f"rule. {people_who_may_approve} people may approve in this installation — "
            "one who did not write it has to (ADR-045)."
        )


class ServiceAccountMayNotApprove(RepositoryError):
    """A technical account tried to approve a data-flow block (CHY-163).

    A CI account holds the role its jobs need, and it is still not a person.
    Approving where data may go is a decision a person answers for.
    """

    def __init__(self) -> None:
        super().__init__(
            "A technical account (group chy-service-accounts) cannot approve a "
            "block that decides where data may go. A person has to (ADR-045)."
        )
