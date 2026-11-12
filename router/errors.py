"""
router/errors.py

What can go wrong when a routing decision is made (CHY-87).

An errors module rather than an `Error` suffix on each class — the shape
`kernel/errors.py`, `core/errors.py` and every `contexts/*/errors.py` already
have. N818 is waived per file for exactly that reason: a convention that holds
everywhere except one package is not a convention.

The status codes are chosen once, in api/main.py. Nothing here knows them.
"""
from __future__ import annotations

from uuid import UUID


class RoutingError(Exception):
    """Base class, so the transport can catch the family in one handler."""


class NoRouteForTask(RoutingError):
    """Nobody has decided which model serves this task type.

    Refused rather than defaulted. Routing is a governance decision with data
    protection consequences; an installation that has not made it should say
    so, not pick something and let the choice look deliberate in the trace.

    The message names the scope as well as the task type, because "no rule"
    is almost always "no rule *here*" — the rule exists one level up, or in a
    sibling, and the caller asked from the wrong place.
    """

    def __init__(self, task_type: str, scope_id: UUID | None) -> None:
        super().__init__(
            f"no routing rule for task type '{task_type}' in scope {scope_id} "
            f"or any of its ancestors — a rule has to be created and approved "
            f"before this task can run"
        )


class MalformedRoutingRule(RoutingError):
    """A stored rule cannot be read.

    Raised rather than skipped. Skipping would fall through to an ancestor's
    rule, and the caller would be routed by a decision nobody made for them —
    with a perfectly plausible answer and nothing anywhere saying that the rule
    they approved was unreadable.
    """

    def __init__(self, block_id: UUID, reason: str) -> None:
        super().__init__(f"routing rule {block_id} is unusable: {reason}")


class RouteExceedsScopeClass(RoutingError):
    """A candidate of the rule may not see the data this scope holds (CHY-160).

    `check_chain` compares the candidates with each other; this compares them
    with the data. Every candidate is checked, not only the first: a fallback
    receives exactly the same request.

    Raised rather than skipped past to an ancestor's rule, for the reason
    `resolve` gives about a loosening rule: falling through would route the
    caller by a decision that was overruled.
    """

    def __init__(self, scope_id, level: str, too_open: list[str]) -> None:
        self.scope_id = scope_id
        self.level = level
        self.too_open = too_open
        super().__init__(
            f"scope {scope_id} holds {level!r} data, and {too_open} may not see "
            f"it. Either the rule names a model below the scope's class, or the "
            f"scope was classified after the rule was approved."
        )


class RoutingRuleNotApprovable(RoutingError):
    """A routing rule, as submitted, could never be used (CHY-166).

    The same defects `MalformedRoutingRule` reports, found at a different
    moment. At read time an unusable active rule is the server's problem — the
    governance that should have stopped it did not — and stays a 500. At
    approval a person is standing there with the rule in front of them, and the
    fix is theirs: until CHY-166 that case answered 500 as well, which told a
    reviewer the server had broken when their rule was simply incomplete.
    """

    def __init__(self, block_id: UUID, reason: str) -> None:
        self.block_id = block_id
        super().__init__(f"routing rule {block_id} cannot be approved: {reason}")


class TokenLimitAboveRule(RoutingError):
    """The caller asked for longer answers than the approved rule allows (CHY-166).

    Refused rather than lowered to the rule's limit. A request that silently
    got less than it asked for would look like a model that stopped early, and
    nothing would say that a rule decided it. The message names the limit, so
    the caller can ask again within it.
    """

    def __init__(self, task_type: str, requested: int, allowed: int) -> None:
        self.requested = requested
        self.allowed = allowed
        super().__init__(
            f"max_tokens {requested} is above the {allowed} the routing rule for "
            f"task type '{task_type}' allows — ask for at most {allowed}, or "
            f"change the rule through review"
        )
