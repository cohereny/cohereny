"""
api/v1/_redaction.py

Putting a scope's redaction rules together (CHY-63 part 2).

The same meeting as `_routing.py`, for the same reason: the scope chain comes
from the kernel, the stored rules from the repository context, the reading from
`kernel.redaction` -- and a context does not import a context (ADR-030). So they
meet in the composition root.

Two callers read the result (`/v1/complete` and the runner's `llm_call`), and
one hook checks a rule before it becomes active.

And the keys a run's mapping is sealed with (CHY-234): configuration, so read
here and handed to the orchestration context with the rules.
"""
from __future__ import annotations

import functools
from typing import Any
from uuid import UUID

from api.config import settings
from contexts.repository import service as repository
from kernel import mapping_cipher, redaction, scopes
from kernel.errors import MalformedRedactionRule, RedactionRuleNotApprovable, ScopeNotFound


@functools.cache
def mapping_keys() -> mapping_cipher.KeyRing:
    """The installation's keys for run mappings, read once (CHY-234).

    Called by the lifespan before anything else, so a missing or malformed key
    stops the API at start (E5, decided 06.11.2026) instead of failing the
    first run that needs it.
    """
    return mapping_cipher.keyring_from(settings.pii_mapping_keys)


async def policy_or_shipped(pool, scope_id: UUID | None) -> redaction.RedactionPolicy:
    """As `redaction_policy_for`; a workflow without a scope gets the shipped rules.

    Such a workflow cannot route a model call (ADR-038), but its input is
    stored all the same and is replaced like any other.
    """
    if scope_id is None:
        return redaction.RedactionPolicy()
    return await redaction_policy_for(pool, scope_id)


async def redaction_policy_for(pool, scope_id: UUID) -> redaction.RedactionPolicy:
    """The rules a model call in this scope runs under.

    Shipped rules, minus what the top of the tree switched off, plus every rule
    the chain adds. Raises `ScopeNotFound` for a scope that does not exist, and
    `MalformedRedactionRule` for an active rule that cannot be read -- the call
    is refused then, not made without the rule.
    """
    chain = await scopes.ancestor_chain(pool, scope_id)
    if not chain:
        raise ScopeNotFound
    scope_order = [row["id"] for row in chain]
    rows = await repository.redaction_rules_for(pool, scope_order)
    return redaction.policy_from(rows, scope_order)


async def check_redaction_rule_before_activation(conn: Any, row: Any) -> None:
    """Refuse a `redaction_rule` block that may not become active.

    Handed to the repository's `before_activation` seam with the routing and
    anchor checks, so it runs on approve and on writing in a direct scope.
    Every other block type passes untouched.
    """
    if row["block_type"] != "redaction_rule":
        return
    chain = await scopes.ancestor_chain(conn, row["scope_id"])
    if not chain:
        raise ScopeNotFound
    try:
        redaction.check_rule_block(row, top_level=len(chain) == 1)
    except MalformedRedactionRule as exc:
        # The approver's to fix, so 422 rather than the 500 a stored rule that
        # cannot be read gets at resolution.
        raise RedactionRuleNotApprovable(row["block_id"], exc.reason) from exc
