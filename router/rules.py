"""
router/rules.py

Stored routing rules, turned into an ordered list of candidates (CHY-87).

The rules live in `context_blocks` as `routing_rule` blocks (ADR-037), so they
arrive with everything a block has: a version, an approval, a scope, and the
privacy ratchet from CHY-62. This module does not re-implement any of that. It
answers one question — given the rules visible from a scope, which models serve
this task type, best first — and refuses when nobody has decided.

Why the reading lives here and the query lives in the repository context:
  `context_blocks` belongs to `contexts/repository`, and owning a table means
  owning access to it (CHY-105, CHY-122, CHY-123). Interpreting a routing rule
  is not the repository's business, though — it is the router's. So the rows
  come from there and the meaning is made here.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from router.config import PRIVACY_LEVELS, RouterConfig
from router.errors import (
    MalformedRoutingRule,
    NoRouteForTask,
    RouteExceedsScopeClass,
    TokenLimitAboveRule,
)


@dataclass(frozen=True)
class RoutingRule:
    """One stored decision: this task type, these candidates, best first — and
    how long an answer may be (CHY-166)."""

    task_type: str
    candidates: tuple[str, ...]
    scope_id: UUID
    block_id: UUID
    version: int
    #: Upper bound for the answer, in tokens, and the default when a caller
    #: names none. Required: until CHY-166 nothing bounded an answer, and on
    #: 25.09.2026 the word "ping" took a throttled Raspberry Pi five minutes —
    #: past the API's own 300 s. It lives in the rule because the rule already
    #: goes through review; a length limit decides cost and latency, and those
    #: are the same kind of decision as which model answers.
    max_tokens: int


def _as_mapping(raw: Any) -> dict:
    """asyncpg returns JSONB as str on some versions and as dict on others."""
    if raw is None:
        return {}
    if isinstance(raw, str):
        return json.loads(raw)
    return dict(raw)


def parse(row: Any) -> RoutingRule:
    """One stored block as a rule.

    The rule is in `metadata`, not in `content`. `content` is what a reviewer
    reads — the reason for this chain — and for every other block type it is
    prompt material. Putting a machine-readable rule there would mean parsing
    prose, which CHY-130 already decided against for edge conditions.
    """
    meta = _as_mapping(row["metadata"])
    task_type = meta.get("task_type")
    candidates = meta.get("candidates")

    if not task_type or not isinstance(task_type, str):
        raise MalformedRoutingRule(row["block_id"], "metadata has no task_type")
    if not isinstance(candidates, list) or not candidates:
        raise MalformedRoutingRule(
            row["block_id"], "metadata has no non-empty candidates list"
        )
    if not all(isinstance(c, str) for c in candidates):
        raise MalformedRoutingRule(row["block_id"], "candidates must be strings")

    max_tokens = meta.get("max_tokens")
    # bool is an int in Python; `true` in a JSON rule is a typo, not a limit.
    if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or max_tokens < 1:
        raise MalformedRoutingRule(
            row["block_id"],
            "metadata has no max_tokens (a positive integer: the longest answer "
            "this rule allows, and the default when a caller names none)",
        )

    return RoutingRule(
        task_type=task_type,
        candidates=tuple(candidates),
        scope_id=row["scope_id"],
        block_id=row["block_id"],
        version=row["version"],
        max_tokens=max_tokens,
    )


def token_limit(rule: RoutingRule, requested: int | None) -> int:
    """The answer length a call may use: the caller's, if within the rule.

    The rule's `max_tokens` is both the default and the ceiling. A caller may
    ask for less — a one-word classification needs no 512 tokens — never for
    more; that would be a way around a reviewed decision.
    """
    if requested is None:
        return rule.max_tokens
    if requested > rule.max_tokens:
        raise TokenLimitAboveRule(rule.task_type, requested, rule.max_tokens)
    return requested


def check_chain(rule: RoutingRule, config: RouterConfig) -> None:
    """A fallback may not lower the privacy level.

    The same rule as in step 1 of CHY-87, now applied to a stored decision
    instead of a file. Measured against the **first** candidate: it is the one
    the author chose for this task, so its ceiling is the floor every other
    candidate has to clear. Comparing neighbours would reject a sound chain —
    [hosted, local, hosted] keeps every candidate at or above the floor.

    Checked at read time and not only at approval, for the reason ratchet.py
    gives about its own second enforcement point: rows reach `active` by paths
    that do not pass through the approve endpoint, and on 2026-08-13 exactly
    that gap let a deleted block reappear.
    """
    floor = config.privacy_rank(rule.candidates[0])
    descends = [m for m in rule.candidates[1:] if config.privacy_rank(m) < floor]
    if descends:
        raise MalformedRoutingRule(
            rule.block_id,
            f"falls back from {rule.candidates[0]} to {descends}, "
            f"which may see less protected data",
        )


def check_ceiling(
    candidates: tuple[str, ...], level: str, config: RouterConfig, scope_id
) -> None:
    """Every candidate may see data of this class (CHY-160).

    The absolute check next to the relative one. `check_chain` stops a chain
    from descending below its first model; this stops the first model — and
    each after it — from sitting below the data. Until CHY-157 every model was
    `critical` and the question could not arise.
    """
    floor = PRIVACY_LEVELS.index(level)
    too_open = [m for m in candidates if config.privacy_rank(m) < floor]
    if too_open:
        raise RouteExceedsScopeClass(scope_id, level, too_open)


def resolve(
    rows: list, scope_order: list[UUID], task_type: str, config: RouterConfig
) -> RoutingRule:
    """The rule for this task type, from the nearest scope that decided.

    Returns the whole rule rather than its candidates since CHY-166: the
    answer's length limit comes from the same decision, and handing it over
    separately would be a second lookup that could pick a different rule.

    `scope_order` runs root first, leaf last — the same order composition uses.
    The deepest rule wins, which is what a scope tree is for: an organisation
    sets a default and a project sharpens it.

    A rule that loosens the privacy level cannot win, because it cannot be
    read: `check_chain` raises. That is deliberate — falling through to the
    ancestor would route the caller by a decision that was overruled, and the
    trace would show a model nobody chose for them.
    """
    rank = {scope: i for i, scope in enumerate(scope_order)}
    best: RoutingRule | None = None

    for row in rows:
        rule = parse(row)
        if rule.task_type != task_type:
            continue
        check_chain(rule, config)
        if best is None or rank.get(rule.scope_id, -1) > rank.get(best.scope_id, -1):
            best = rule

    if best is None:
        raise NoRouteForTask(task_type, scope_order[-1] if scope_order else None)
    return best
