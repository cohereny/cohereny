"""
contexts/orchestration/conditions.py

Guards on edges, as data (CHY-130, arc42 §5.6).

A conditional edge decides which arm of an XOR branch a run takes. Nothing
evaluated one before this, and nothing said how one is written — the model said
JSONB and the schema said TEXT, and with no evaluator the disagreement was
invisible.

Why data and not an expression
-------------------------------
A structured condition is **checkable before it runs**. The validator refuses a
malformed one at activation, which is the moment somebody is looking, instead
of failing a run half way through, which is a moment nobody chose. There is no
parser to get wrong and no sandbox to get right.

The cost is expressiveness, and it is paid on purpose. An expression language
cannot be taken back: the day one workflow uses a corner of it, the corner is a
contract. Arithmetic is left out for the same reason — the moment a condition
can compute, a reader can no longer tell what a workflow does by reading it.

The shape::

    {"field": "risk_level", "op": "eq", "value": "high"}
    {"all": [ … ]}    {"any": [ … ]}    {"not": { … }}

`field` addresses the source node's output. Dotted paths reach into nested
values; a missing path is not an error but a miss, because "the field is not
there" and "the field does not match" are the same answer to a branch.

The aggregate of ADR-030: it decides, it does not execute.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from contexts.orchestration.dataflow import MISSING, resolve_path
from contexts.orchestration.errors import MalformedCondition

#: What a comparison may do. Deliberately short, and every entry earns its
#: place in a branch somebody would actually draw.
OPERATORS: tuple[str, ...] = (
    "eq", "ne", "lt", "lte", "gt", "gte", "in", "contains", "exists",
)

#: How conditions combine. `not` takes one, the others take a list.
COMBINATORS: tuple[str, ...] = ("all", "any", "not")

#: Nesting deeper than this is a condition nobody will read correctly. The
#: limit is arbitrary in its exact value and not in its existence: without one,
#: a hand-written payload can make the validator recurse until the process
#: gives up, and a workflow definition is user input.
MAX_DEPTH = 8

#: What a gate's output says when it escalates instead of releasing (ADR-050).
ESCALATED = "escalated"

#: The guard of an escalation edge: the way a gate goes when it escalates —
#: on a timeout with `on_timeout = escalate`, and from CHY-180 on when votes
#: differ. An ordinary condition on the gate's output, so the graph shows the
#: higher authority and a reviewer releases it with the definition. Exactly this
#: guard and no other: a broader one would fire on the wrong outcome, and the
#: validator could no longer tell which edge is the escalation.
ESCALATION: Mapping[str, Any] = {"op": "eq", "field": "decision", "value": ESCALATED}


def is_escalation(condition: Mapping[str, Any] | None) -> bool:
    """Whether a guard is the escalation guard, exactly."""
    return condition is not None and dict(condition) == ESCALATION


#: Re-exported from dataflow rather than defined twice. "What does `a.b.c`
#: mean" has to mean one thing: the day a guard and an input mapping read the
#: same address differently is a bug nobody looks for, because the two features
#: feel unrelated (CHY-131).
_MISSING = MISSING


def _ordered(op: str, actual: Any, expected: Any) -> bool:
    """The four orderings, wrapped.

    Comparing a string to a number raises in Python, and a workflow author's
    typo must not surface as a TypeError from inside the runner. It is a
    comparison that does not hold.
    """
    try:
        return {
            "lt": lambda: actual < expected,
            "lte": lambda: actual <= expected,
            "gt": lambda: actual > expected,
            "gte": lambda: actual >= expected,
        }[op]()
    except TypeError:
        return False


#: Comparisons that work on any two values. A table rather than a chain of
#: returns: the chain reached twelve exits, and a reader counting them cannot
#: tell whether every operator in OPERATORS is actually reachable. A mapping
#: can be checked against that tuple — and a test does.
_DIRECT: dict[str, Any] = {
    "eq": lambda actual, expected: actual == expected,
    "ne": lambda actual, expected: actual != expected,
    "in": lambda actual, expected: (
        isinstance(expected, list | tuple | str) and actual in expected
    ),
    "contains": lambda actual, expected: (
        isinstance(actual, list | tuple | str) and expected in actual
    ),
}


def _compare(op: str, actual: Any, expected: Any) -> bool:
    if op == "exists":
        return (actual is not _MISSING) is bool(expected)
    if actual is _MISSING:
        # Every other operator is a statement about a value. A field that is
        # not there does not match — it does not raise, because a branch asking
        # "is the risk high" about a node that produced no risk has a perfectly
        # good answer: no.
        return False
    if op in _DIRECT:
        return _DIRECT[op](actual, expected)
    return _ordered(op, actual, expected)


def _check_combinator(condition: Mapping[str, Any], depth: int) -> bool:
    """Check `all`, `any` or `not` if this is one. Says whether it was.

    Split out because `check` was one function expressing two rules — and the
    rule it was expressing badly is the interesting one: **a condition is
    either a combinator or a comparison, never both.** Two functions and a
    boolean say that; a chain of early returns says it only to whoever reads
    all of it.
    """
    for combinator in ("all", "any"):
        if combinator in condition:
            branches = condition[combinator]
            if not isinstance(branches, list) or not branches:
                raise MalformedCondition.empty_combinator(combinator)
            for branch in branches:
                check(branch, depth=depth + 1)
            return True

    if "not" in condition:
        check(condition["not"], depth=depth + 1)
        return True
    return False


def _check_comparison(condition: Mapping[str, Any]) -> None:
    missing = {"field", "op"} - set(condition)
    if missing:
        raise MalformedCondition.missing_keys(missing, list(condition))
    if condition["op"] not in OPERATORS:
        raise MalformedCondition.unknown_operator(condition["op"], OPERATORS)
    if not isinstance(condition["field"], str) or not condition["field"]:
        raise MalformedCondition.bad_field()


def check(condition: Mapping[str, Any], *, depth: int = 0) -> None:
    """Refuse a condition that is not well formed.

    Called by the validator before a workflow is activated, which is the whole
    point of choosing data over an expression: the answer arrives while someone
    is looking at the workflow, not while a run is half finished.
    """
    if depth > MAX_DEPTH:
        raise MalformedCondition.too_deep(MAX_DEPTH)
    if not isinstance(condition, Mapping) or not condition:
        raise MalformedCondition.not_an_object()
    if _check_combinator(condition, depth):
        return
    _check_comparison(condition)


def evaluate(condition: Mapping[str, Any], data: Mapping[str, Any]) -> bool:
    """Does this guard hold for that output?

    Assumes the condition is well formed — `check` runs at activation, so a
    stored condition has already been through it. Evaluating an unchecked
    condition raises rather than guessing, because a guard that silently
    answers `False` sends the run down the default arm and looks like a
    deliberate branch.
    """
    check(condition)
    return _evaluate(condition, data)


def _evaluate(condition: Mapping[str, Any], data: Mapping[str, Any]) -> bool:
    if "all" in condition:
        return all(_evaluate(b, data) for b in condition["all"])
    if "any" in condition:
        return any(_evaluate(b, data) for b in condition["any"])
    if "not" in condition:
        return not _evaluate(condition["not"], data)
    return _compare(
        condition["op"], resolve_path(condition["field"], data), condition.get("value")
    )
