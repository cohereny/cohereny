"""
contexts/repository/ratchet.py

The privacy ratchet (CHY-62, ADR-017): a privacy rule may be tightened as it
moves down the scope tree, never loosened.

Why this is a declaration and not an analysis
---------------------------------------------
Composition does not override anything. `ComposedContext.as_prompt` joins every
block shallow to deep; the deeper one merely comes later in the text. Whether
an LLM then follows the org's "never send this to a cloud model" or the
project's "cloud routing is fine" is not something this system decides — and a
control that depends on how a model weighs two paragraphs is not a control.

A contradiction between two pieces of prose is also not machine-detectable. So
the rule is not inferred from the text: a block *states* which rule it speaks
to (`governance_key`) and in which direction (`privacy_impact`). Only then can
two blocks be known to be about the same thing, and only then is a conflict a
fact rather than a guess.

ADR-017 puts the field on `routing_rule.action` and calls it derived. There was
no routing_rule when CHY-62 was built — `router/` was a YAML config, not rows —
so the field went on blocks and was made declared. The change is recorded in
the ADR.

Since CHY-87 there *are* routing_rule blocks (ADR-037), and nothing here had to
change for the ratchet to cover them: this module checks no block type. A
deeper scope may tighten a routing rule and may not loosen it, by the same
declaration every other block makes.

Why two enforcement points
--------------------------
`check_claim` runs when a block is approved, and again when a scope composes.

The first is the real gate: a human is standing there and the fix is cheap.
The second is not redundant. Several versions of a block can be `active` at
once by design — superseding leaves the predecessor active so it keeps being
composed while its successor is in review — and rows reach `active` by paths
that do not pass through the approve endpoint. On 2026-08-13 exactly that gap
let a deleted block reappear with its old content, found by an end-to-end run
and not by any unit test.

The aggregate of ADR-030: it decides, it does not execute. Nothing here opens a
connection or knows that HTTP exists.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from contexts.repository.errors import (
    GovernanceKeyLocked,
    GovernanceNotEditable,
    PrivacyRatchetViolation,
    UnknownGovernanceKey,
)

#: The two directions a governance claim can point. Mirrors the CHECK on
#: context_blocks.privacy_impact (migration 016); a test holds them together.
PRIVACY_IMPACTS: tuple[str, ...] = ("restrictive", "permissive")

RESTRICTIVE = "restrictive"
PERMISSIVE = "permissive"


@dataclass(frozen=True)
class GovernanceClaim:
    """What one block says about one rule, and where it says it.

    `depth` is the block's distance from the root of the scope tree — the same
    value composition sorts by. It is what makes "further down" a comparison
    rather than an opinion.
    """

    block_id: UUID
    scope_id: UUID
    depth: int
    governance_key: str
    privacy_impact: str
    locked: bool = False

    @property
    def is_restrictive(self) -> bool:
        return self.privacy_impact == RESTRICTIVE


def claim_from_row(row: Any, depth: int) -> GovernanceClaim | None:
    """Read a claim off a block row, or None when the block makes none.

    The overwhelming majority of blocks make none. Returning None rather than a
    claim with empty fields keeps "says nothing" and "says something neutral"
    distinguishable — a block that accidentally read as neutral would take part
    in comparisons it was never meant to be in.
    """
    key = row["governance_key"]
    if key is None:
        return None
    return GovernanceClaim(
        block_id=row["block_id"],
        scope_id=row["scope_id"],
        depth=depth,
        governance_key=key,
        privacy_impact=row["privacy_impact"],
        locked=row["locked"],
    )


def check_key_known(value: str | None, known: tuple[str, ...]) -> None:
    """Refuse a governance key that is not in the vocabulary.

    The foreign key in migration 016 refuses it too. This exists so the caller
    gets a sentence naming the alternatives instead of a constraint violation
    arriving as a 500 — and so the refusal happens before anything is written,
    which is where a typo is cheapest to fix.
    """
    if value is not None and value not in known:
        raise UnknownGovernanceKey(value, known)


def check_governance_editable(*, plan_supersedes: bool, requested: dict) -> None:
    """Refuse a governance change that rides along with a supersede.

    The database freezes the three columns outside `draft` (migration 016), so
    this could not succeed anyway — but it would fail as a plpgsql exception
    after the new version was already written, which is a confusing way to
    learn that the claim did not change.

    The two-step shape is the point, not an inconvenience: publishing a new
    version and restating what it governs are separate decisions, and a
    reviewer should see the second one as a change rather than as a side effect
    of the first.
    """
    if not plan_supersedes:
        return
    named = [k for k, v in requested.items() if v is not None]
    if named:
        raise GovernanceNotEditable(named)


def check_claim(claim: GovernanceClaim | None, existing: list[GovernanceClaim]) -> None:
    """Refuse a claim that loosens or restates a rule settled further up.

    `existing` is every active claim in the scope chain, the block's own scope
    included. The block being checked must not be in it — comparing a claim
    with itself would refuse every re-approval.

    Two rules, in this order, because `locked` is the stronger statement and a
    caller should hear the real reason first:

    1. **locked** — an ancestor declared the key settled. No scope below may
       state it at all, not even more strictly. Restating an org rule one level
       down creates a second place where one rule lives, and two sources for
       one truth drift apart. A genuine tightening belongs in the locked
       block's own scope, or under its own key.

    2. **the ratchet** — a restrictive statement at the same depth or above
       cannot be loosened by a permissive one. Tightening is always allowed;
       that is the whole point, and a check that refused it would be a wall
       rather than a ratchet.

    Why `locked` only counts strictly above: locked speaks about descendants. A
    second block in the *same* scope is a matter for that scope's own review,
    not for this rule — refusing it here would make a scope unable to revise
    its own governance without an error nobody can act on.
    """
    if claim is None:
        return

    same_key = [c for c in existing if c.governance_key == claim.governance_key]

    for other in same_key:
        if other.locked and other.depth < claim.depth:
            raise GovernanceKeyLocked(
                key=claim.governance_key,
                ancestor_scope=other.scope_id,
                ancestor_block=other.block_id,
            )

    if claim.privacy_impact != PERMISSIVE:
        return

    for other in same_key:
        if other.is_restrictive and other.depth <= claim.depth:
            raise PrivacyRatchetViolation(
                key=claim.governance_key,
                ancestor_scope=other.scope_id,
                ancestor_block=other.block_id,
            )


def check_composition(claims: list[GovernanceClaim]) -> None:
    """Verify a whole composed chain, deepest claim last.

    The backstop described in the module docstring. Each claim is checked
    against everything shallower than itself, which is the same comparison
    `check_claim` makes at approval time — written once, in terms of the same
    function, so the two enforcement points cannot drift into disagreeing about
    what a conflict is.

    Sorted by depth first because a claim may only be judged against what is
    above it. Unsorted input would compare a shallow claim against a deep one
    and refuse the org block for contradicting the project.
    """
    ordered = sorted(claims, key=lambda c: (c.depth, c.block_id))
    for i, claim in enumerate(ordered):
        check_claim(claim, ordered[:i])
