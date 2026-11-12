"""
tests/test_edge_conditions.py

Guards on edges (CHY-130, arc42 §5.6).

The decision this file defends is **data, not an expression**. A structured
condition is checkable before it runs, so the validator refuses a malformed one
at activation — while somebody is looking at the workflow — instead of failing
a run half way through, which is a moment nobody chose.

The two rules that carry the most weight are not the comparisons. They are:

  * a **missing field is a miss, not an error** — a branch asking "is the risk
    high" about a node that produced no risk has a perfectly good answer, and
    raising would turn a normal graph into a broken run;
  * a **malformed condition raises rather than answering False** — False would
    reach the state machine as "this arm did not match", the run would take the
    default arm or fail for want of one, and the message would blame the
    workflow's shape for what is a typo in a guard.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from contexts.orchestration.conditions import MAX_DEPTH, OPERATORS, check, evaluate
from contexts.orchestration.errors import MalformedCondition

_RESULT = {
    "risk_level": "high",
    "score": 7,
    "flags": ["pep", "sanctions"],
    "nested": {"country": "DE"},
    "empty": None,
}


def _holds(condition: dict) -> bool:
    return evaluate(condition, _RESULT)


# ── the comparisons ───────────────────────────────────────────────────────────

class TestComparisons:
    @pytest.mark.parametrize(
        ("condition", "expected"),
        [
            ({"field": "risk_level", "op": "eq", "value": "high"}, True),
            ({"field": "risk_level", "op": "eq", "value": "low"}, False),
            ({"field": "risk_level", "op": "ne", "value": "low"}, True),
            ({"field": "score", "op": "gt", "value": 5}, True),
            ({"field": "score", "op": "gt", "value": 7}, False),
            ({"field": "score", "op": "gte", "value": 7}, True),
            ({"field": "score", "op": "lt", "value": 10}, True),
            ({"field": "score", "op": "lte", "value": 7}, True),
            ({"field": "risk_level", "op": "in", "value": ["high", "critical"]}, True),
            ({"field": "flags", "op": "contains", "value": "pep"}, True),
            ({"field": "flags", "op": "contains", "value": "none"}, False),
            ({"field": "nested.country", "op": "eq", "value": "DE"}, True),
        ],
    )
    def test_each_operator(self, condition: dict, expected: bool):
        assert _holds(condition) is expected

    def test_every_operator_in_the_list_is_implemented(self):
        """The list and the implementation are one thing in two places.

        An operator that passes `check` and then falls through `_compare` to
        `False` would be accepted at activation and silently never match — the
        worst of both.
        """
        for op in OPERATORS:
            condition = {"field": "score", "op": op, "value": 7}
            check(condition)
            assert isinstance(evaluate(condition, _RESULT), bool)


# ── absence ───────────────────────────────────────────────────────────────────

class TestMissingFields:
    def test_a_missing_field_does_not_match(self):
        """Not an error. A branch asking about something a node did not produce
        has a perfectly good answer: no.

        This one **documents** the rule; it does not protect it. A probe that
        removed the absence check left it green — the sentinel compares unequal
        to everything, so `eq` answers False either way. The test below is what
        actually bites, and the two are kept apart so nobody mistakes the first
        for cover.
        """
        assert _holds({"field": "nope", "op": "eq", "value": "x"}) is False

    def test_a_missing_field_does_not_match_ne_either(self):
        """The trap in the rule above, and the test that protects it.

        `ne` on an absent field reads as "not equal, therefore true" — which
        would make a branch fire *because* a value was missing. Absence answers
        no to every comparison, and only this case can tell whether that rule
        is still there.
        """
        assert _holds({"field": "nope", "op": "ne", "value": "x"}) is False

    def test_exists_can_ask_about_absence_on_purpose(self):
        assert _holds({"field": "risk_level", "op": "exists", "value": True}) is True
        assert _holds({"field": "nope", "op": "exists", "value": True}) is False
        assert _holds({"field": "nope", "op": "exists", "value": False}) is True

    def test_a_null_value_is_present(self):
        """`None` is a value a node can legitimately produce, and "absent" and
        "null" answer differently to `exists`."""
        assert _holds({"field": "empty", "op": "exists", "value": True}) is True

    def test_comparing_a_string_to_a_number_does_not_raise(self):
        """A workflow author's typo must not surface as a TypeError from inside
        the runner. It is a comparison that does not hold."""
        assert _holds({"field": "risk_level", "op": "gt", "value": 5}) is False


# ── nesting ───────────────────────────────────────────────────────────────────

class TestCombinators:
    def test_all_needs_every_branch(self):
        assert _holds({"all": [
            {"field": "risk_level", "op": "eq", "value": "high"},
            {"field": "score", "op": "gt", "value": 5},
        ]}) is True
        assert _holds({"all": [
            {"field": "risk_level", "op": "eq", "value": "high"},
            {"field": "score", "op": "gt", "value": 99},
        ]}) is False

    def test_any_needs_one(self):
        assert _holds({"any": [
            {"field": "risk_level", "op": "eq", "value": "low"},
            {"field": "score", "op": "gt", "value": 5},
        ]}) is True

    def test_not_inverts(self):
        assert _holds({"not": {"field": "risk_level", "op": "eq", "value": "low"}}) is True


# ── what the validator refuses ────────────────────────────────────────────────

class TestCheck:
    def test_a_well_formed_condition_passes(self):
        """The counter-probe the rest of this class rests on. A checker that
        refused everything would satisfy every case below and make conditional
        edges unusable."""
        check({"field": "risk_level", "op": "eq", "value": "high"})

    @pytest.mark.parametrize(
        ("condition", "expected_in_message"),
        [
            ({}, "non-empty object"),
            ({"op": "eq", "value": 1}, "field"),
            ({"field": "x", "value": 1}, "op"),
            ({"field": "x", "op": "matches", "value": 1}, "unknown operator"),
            ({"field": "", "op": "eq", "value": 1}, "non-empty string"),
            ({"all": []}, "non-empty list"),
            ({"any": "not a list"}, "non-empty list"),
        ],
    )
    def test_a_malformed_condition_says_what_was_expected(
        self, condition, expected_in_message: str
    ):
        """"Invalid condition" makes the author guess, and guessing at a branch
        condition is how a workflow takes the wrong arm."""
        with pytest.raises(MalformedCondition) as refused:
            check(condition)
        assert expected_in_message in str(refused.value)

    def test_a_malformed_branch_inside_a_combinator_is_caught(self):
        with pytest.raises(MalformedCondition):
            check({"all": [{"field": "x", "op": "eq", "value": 1}, {"op": "eq"}]})

    def test_nesting_beyond_the_limit_is_refused(self):
        """A workflow definition is user input. Without a limit a hand-written
        payload makes the validator recurse until the process gives up — which
        is a denial of service dressed as a configuration."""
        deep = {"field": "x", "op": "eq", "value": 1}
        for _ in range(MAX_DEPTH + 2):
            deep = {"not": deep}
        with pytest.raises(MalformedCondition) as refused:
            check(deep)
        assert "nested" in str(refused.value)

    def test_nesting_up_to_the_limit_is_fine(self):
        """Counter-probe: the limit must not refuse a condition anyone would
        actually write."""
        deep = {"field": "x", "op": "eq", "value": 1}
        for _ in range(MAX_DEPTH - 1):
            deep = {"not": deep}
        check(deep)


# ── the model and the schema agree again ──────────────────────────────────────

def test_the_column_is_jsonb():
    """arc42 §5.6 said JSONB and 001_initial said TEXT, from day one.

    With nothing evaluating a condition both readings behaved identically,
    which is why it went unnoticed. Migration 020 settles it — asserted here so
    a later migration cannot quietly settle it the other way.
    """
    sql = Path("migrations/020_edge_conditions.sql").read_text(encoding="utf-8")
    sql = "\n".join(line.split("--")[0] for line in sql.split("\n"))
    assert re.search(
        r"ALTER TABLE workflow_connections\s+ALTER COLUMN condition TYPE JSONB", sql
    ), "migration 020 no longer changes the column type"
