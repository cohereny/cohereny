"""
tests/test_redaction_rules.py

A scope's own redaction rules, as `redaction_rule` blocks (CHY-63 part 2).

Decided 05.11.2026: blocks rather than a table with an admin API, so that what
decides which data leaves goes through the same review as what decides where it
goes. These tests hold the three things that decision rests on:

  - rules add up down the tree, and nothing added above is taken away below
  - switching a shipped rule off happens only at the top, and only by a block
    that declares itself a loosening -- which is what sends it to four eyes
  - a rule that runs over every prompt cannot be one that hangs every prompt

The database half -- migration 032, the ratchet, four eyes on a real approval
-- is in tests/integration/test_redaction_rules_db.py.
"""
from __future__ import annotations

import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from api.v1 import _redaction
from kernel.errors import MalformedRedactionRule, RedactionRuleNotApprovable
from kernel.redaction import (
    MAX_PATTERN_LENGTH,
    SHIPPED_RULES,
    check_rule_block,
    merged,
    policy_from,
    redact,
    scope_rule_from,
)

_ORG, _PROJECT = uuid4(), uuid4()
_T0 = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)


def _row(metadata, *, scope_id=_ORG, key=None, impact=None, at=0) -> dict:
    return {
        "block_id": uuid4(), "version": 1, "scope_id": scope_id,
        "block_type": "redaction_rule", "metadata": metadata,
        "governance_key": key, "privacy_impact": impact,
        "created_at": _T0 + timedelta(minutes=at),
    }


def _switch_off(*categories: str, **kw) -> dict:
    kw.setdefault("key", "pii_redaction")
    kw.setdefault("impact", "permissive")
    return _row({"disable": list(categories)}, **kw)


def _categories(policy) -> list[str]:
    return [r.category for r in policy.rules]


# ── reading one block ─────────────────────────────────────────────────────────

class TestReading:
    def test_texts_become_named_values(self):
        rule = scope_rule_from(_row({"category": "STAFF", "texts": [" Dr. Schmidt "]}))
        assert (rule.category, rule.texts) == ("STAFF", ("Dr. Schmidt",))

    def test_a_pattern_becomes_a_rule(self):
        rule = scope_rule_from(_row({"category": "CASE_NUMBER",
                                     "pattern": r"\b\d+ [A-Z] \d+/\d{2}\b"}))
        assert rule.rule.category == "CASE_NUMBER"
        assert rule.rule.pattern.search("Akte 4 C 123/26")

    def test_metadata_as_json_text_is_read_too(self):
        # asyncpg hands JSONB over as str on some versions.
        rule = scope_rule_from(_row('{"disable": ["PHONE"]}'))
        assert rule.disabled == {"PHONE"}

    @pytest.mark.parametrize(("metadata", "reason"), [
        ({}, "exactly one"),
        ({"category": "X", "texts": ["ab"], "pattern": "ab"}, "exactly one"),
        ([], "must be an object"),
        ({"category": "staff", "texts": ["ab"]}, "upper case"),
        ({"category": "STAFF", "texts": []}, "at least one"),
        ({"category": "STAFF", "texts": "Dr. Schmidt"}, "must be a list"),
        ({"category": "CODE", "pattern": "x" * (MAX_PATTERN_LENGTH + 1)}, "characters"),
        ({"category": "CODE", "pattern": "(unclosed"}, "does not compile"),
        ({"category": "CODE", "pattern": r"\d*"}, "empty text"),
        ({"category": "CODE", "pattern": r"(?=x)"}, "empty text"),
        ({"disable": ["FAX"]}, "no shipped rule named FAX"),
        ({"disable": []}, "non-empty list"),
        ({"disable": "PHONE"}, "non-empty list"),
    ])
    def test_what_cannot_be_used_is_refused_with_the_reason(self, metadata, reason):
        with pytest.raises(MalformedRedactionRule, match=reason):
            scope_rule_from(_row(metadata))


class TestPatternsThatWouldHangEveryPrompt:
    """Python's re has no timeout, and a scope's rule runs over every prompt."""

    @pytest.mark.parametrize("pattern", [
        r"(a+)+b", r"(a|aa)*b", r"(\d+){1,1000}", r"(?:\.\d+)+", r"(a{2,}){2}", r"(x?y){3}",
    ])
    def test_a_repeated_group_that_varies_inside_is_refused(self, pattern: str):
        with pytest.raises(MalformedRedactionRule, match="upper bound"):
            scope_rule_from(_row({"category": "CODE", "pattern": pattern}))

    def test_the_check_refuses_before_it_runs_the_pattern(self):
        """The check must not run what it is about to refuse (CHY-320).

        It tried every pattern on its probe text first, and `(\\w+)+§` never
        finishes there: the probe is a run of word characters with no `§`. The
        patterns above match the probe at once and never showed the order. In a
        child process with a time limit, so a regression fails here instead of
        holding the suite.
        """
        code = (
            "from kernel.redaction import _checked_pattern\n"
            "try:\n"
            "    _checked_pattern(r'(\\w+)+\u00a7')\n"
            "except ValueError as exc:\n"
            "    print(exc)\n"
        )
        try:
            result = subprocess.run(
                [sys.executable, "-c", code], cwd=Path(__file__).resolve().parent.parent,
                capture_output=True, text=True, timeout=20,
            )
        except subprocess.TimeoutExpired:
            pytest.fail("the check ran the pattern before refusing it and held for 20 s")
        assert result.returncode == 0, result.stderr
        assert "upper bound" in result.stdout

    @pytest.mark.parametrize("pattern", [
        r"(?:\d{3}){1,5}", r"(?:[A-Z]{2}\d{2}){2}", r"(?P<year>\d{4})-\d{2}",
        r"\(\d+\)+", r"[(]+x", r"Az\.? ?\d+",
    ])
    def test_fixed_counts_and_literal_parentheses_pass(self, pattern: str):
        assert scope_rule_from(_row({"category": "CODE", "pattern": pattern})).rule


# ── what may become active ────────────────────────────────────────────────────

class TestCheckBeforeActivation:
    def test_switching_off_at_the_top_with_the_declaration_passes(self):
        check_rule_block(_switch_off("PHONE"), top_level=True)

    def test_switching_off_below_the_top_is_refused(self):
        with pytest.raises(MalformedRedactionRule, match="without a parent"):
            check_rule_block(_switch_off("PHONE", scope_id=_PROJECT), top_level=False)

    @pytest.mark.parametrize(("key", "impact"), [
        (None, None), ("pii_redaction", None), ("pii_redaction", "restrictive"),
        ("cloud_routing", "permissive"),
    ])
    def test_switching_off_without_declaring_the_loosening_is_refused(self, key, impact):
        # Undeclared, it would not count as a data-flow block and would skip the
        # second person (ADR-045).
        with pytest.raises(MalformedRedactionRule, match="loosening"):
            check_rule_block(_switch_off("PHONE", key=key, impact=impact), top_level=True)

    def test_a_rule_that_adds_protection_may_not_call_itself_permissive(self):
        row = _row({"category": "STAFF", "texts": ["Dr. Schmidt"]},
                   key="pii_redaction", impact="permissive")
        with pytest.raises(MalformedRedactionRule, match="adds protection"):
            check_rule_block(row, top_level=True)

    @pytest.mark.parametrize("top_level", [True, False])
    def test_a_rule_that_adds_protection_passes_anywhere(self, top_level: bool):
        row = _row({"category": "STAFF", "texts": ["Dr. Schmidt"]},
                   key="pii_redaction", impact="restrictive")
        check_rule_block(row, top_level=top_level)


# ── what a scope chain asks for ───────────────────────────────────────────────

class TestPolicy:
    def test_no_rules_means_the_shipped_ones(self):
        policy = policy_from([], [_ORG, _PROJECT])
        assert policy.rules == SHIPPED_RULES
        assert policy.identifiers == {}

    def test_the_top_may_switch_a_shipped_rule_off(self):
        policy = policy_from([_switch_off("PHONE", "IBAN")], [_ORG, _PROJECT])
        assert "PHONE" not in _categories(policy)
        assert "IBAN" not in _categories(policy)
        assert "EMAIL" in _categories(policy)

    def test_a_switch_off_below_the_top_is_ignored_even_when_it_got_through(self):
        # The second fence: a row may become active by a path that skipped the
        # check. Ignoring it is the safe direction to be wrong in.
        policy = policy_from([_switch_off("PHONE", scope_id=_PROJECT)], [_ORG, _PROJECT])
        assert "PHONE" in _categories(policy)

    def test_an_undeclared_switch_off_at_the_top_is_ignored(self):
        policy = policy_from([_switch_off("PHONE", key=None, impact=None)], [_ORG])
        assert "PHONE" in _categories(policy)

    def test_rules_add_up_down_the_tree(self):
        rows = [
            _row({"category": "CASE_NUMBER", "pattern": r"\b\d+ C \d+/\d{2}\b"},
                 scope_id=_PROJECT),
            _row({"category": "STAFF", "texts": ["Dr. Schmidt"]}),
            _row({"category": "STAFF", "texts": ["Schwester Anna"]}, scope_id=_PROJECT),
        ]
        policy = policy_from(rows, [_ORG, _PROJECT])
        assert _categories(policy)[-1] == "CASE_NUMBER"
        assert policy.identifiers == {"STAFF": ("Dr. Schmidt", "Schwester Anna")}

    def test_the_order_follows_the_tree_not_the_database(self):
        org_rule = _row({"category": "ORG_CODE", "pattern": "ORG-[0-9]{4}"}, at=5)
        project_rule = _row({"category": "PROJECT_CODE", "pattern": "PRJ-[0-9]{4}"},
                            scope_id=_PROJECT, at=0)
        for rows in ([org_rule, project_rule], [project_rule, org_rule]):
            assert _categories(policy_from(rows, [_ORG, _PROJECT]))[-2:] == [
                "ORG_CODE", "PROJECT_CODE"]

    def test_an_active_rule_that_cannot_be_read_refuses_the_call(self):
        # Skipped, it would protect nothing while looking configured.
        with pytest.raises(MalformedRedactionRule):
            policy_from([_row({"category": "CODE", "pattern": "(a+)+"})], [_ORG])

    def test_the_policy_does_what_it_says_in_redact(self):
        rows = [_switch_off("PHONE"),
                _row({"category": "STAFF", "texts": ["Dr. Schmidt"]}, scope_id=_PROJECT)]
        policy = policy_from(rows, [_ORG, _PROJECT])
        out = redact({"prompt": "Dr. Schmidt, Tel. 069 1234567, a@example.de"},
                     identifiers=policy.identifiers, rules=policy.rules)
        assert out.texts["prompt"] == "{{STAFF_1}}, Tel. 069 1234567, {{EMAIL_1}}"


def test_merged_keeps_every_source_per_category():
    assert merged({"PERSON": ("Anna",)}, {"PERSON": ["Erika"], "CASE": ["4 C 1/26"]}) == {
        "PERSON": ("Anna", "Erika"), "CASE": ("4 C 1/26",)}


# ── the composition root ──────────────────────────────────────────────────────

def _pool(rows: list[dict], *, chain_ids=(_ORG, _PROJECT)) -> MagicMock:
    chain = [{"id": scope_id, "depth": i + 1} for i, scope_id in enumerate(chain_ids)]

    async def fetch(sql, *_args):
        if "scope_nodes" in sql:
            return chain
        if "block_type = 'redaction_rule'" in sql:
            return rows
        pytest.fail(f"unexpected query: {sql[:80]}")

    pool = MagicMock()
    pool.fetch = AsyncMock(side_effect=fetch)
    return pool


class TestCompositionRoot:
    async def test_the_policy_is_read_along_the_chain_root_first(self):
        pool = _pool([_switch_off("PHONE")])
        policy = await _redaction.redaction_policy_for(pool, _PROJECT)
        assert "PHONE" not in _categories(policy)

    async def test_other_block_types_pass_the_activation_check_untouched(self):
        pool = _pool([])
        await _redaction.check_redaction_rule_before_activation(
            pool, {"block_type": "routing_rule", "scope_id": _PROJECT})
        pool.fetch.assert_not_called()

    async def test_a_refusal_at_activation_is_the_approvers_to_fix(self):
        # 422 with the reason, not the 500 of a stored rule that cannot be read.
        pool = _pool([], chain_ids=(_ORG, _PROJECT))
        with pytest.raises(RedactionRuleNotApprovable, match="without a parent"):
            await _redaction.check_redaction_rule_before_activation(
                pool, _switch_off("PHONE", scope_id=_PROJECT))

    async def test_the_top_of_the_tree_is_a_chain_of_one(self):
        pool = _pool([], chain_ids=(_ORG,))
        await _redaction.check_redaction_rule_before_activation(pool, _switch_off("PHONE"))
