"""
tests/test_routing_rules.py

Stored routing rules become candidates (CHY-87 step 2b).

The rules moved out of `router/routing-config.yaml` and into `context_blocks`
as `routing_rule` blocks (ADR-037). What is left in the file is what is not a
decision: which models this installation can reach and what each may see.

These tests drive `router.rules` directly. The query that fetches the rows
belongs to the repository context and is covered where that context is; what is
covered here is the reading — which scope wins, what a broken rule does, and
what happens when nobody decided.
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from uuid import uuid4

import pytest
import yaml

from router.config import RouterConfig, RoutingConfigError
from router.errors import MalformedRoutingRule, NoRouteForTask, TokenLimitAboveRule
from router.rules import parse, resolve, token_limit

_LOCAL_MODEL = {"runs_on": "local-raspberry-pi", "max_privacy_level": "critical"}
_HOSTED_MODEL = {"runs_on": "cloud", "max_privacy_level": "internal"}


def _config(models: dict | None = None) -> RouterConfig:
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        yaml.dump({"models": models or {
            "local": _LOCAL_MODEL, "local2": _LOCAL_MODEL, "hosted": _HOSTED_MODEL,
        }}, f)
        return RouterConfig(config_path=Path(f.name))


def _row(task_type, candidates, scope_id, **kw):
    return {
        "block_id": kw.get("block_id", uuid4()),
        "version": kw.get("version", 1),
        "scope_id": scope_id,
        "content": "weil es so entschieden wurde",
        "metadata": {"task_type": task_type, "candidates": candidates,
                     "max_tokens": kw.get("max_tokens", 256)},
    }


class TestTheDeploymentDeclaration:
    def test_a_file_without_models_does_not_load(self):
        """A deployment that reaches nothing cannot route, and a rule naming a
        model would have nothing to name."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump({"models": {}}, f)
            with pytest.raises(RoutingConfigError, match="no models"):
                RouterConfig(config_path=Path(f.name))

    def test_the_routes_section_is_gone(self):
        """Counter-probe against a half-finished move: a leftover `routes:` in
        the shipped file would be a second source for a decision that now lives
        in the database."""
        import router.config as rc

        shipped = yaml.safe_load(rc._DEFAULT_CONFIG.read_text())
        assert "routes" not in shipped, (
            "routing-config.yaml still carries routes — two sources for one "
            "decision, and the file wins silently"
        )
        assert shipped["models"], "the models declaration went missing with them"


class TestParsing:
    def test_a_rule_is_read_from_metadata_not_from_content(self):
        """`content` is what a reviewer reads. Parsing prose is what CHY-130
        decided against for edge conditions."""
        rule = parse(_row("summarize", ["local", "local2"], uuid4()))
        assert rule.task_type == "summarize"
        assert rule.candidates == ("local", "local2")

    @pytest.mark.parametrize("meta", [
        {}, {"task_type": "x"}, {"candidates": ["local"]},
        {"task_type": "x", "candidates": []},
        {"task_type": "x", "candidates": "local"},
        {"task_type": "x", "candidates": [1, 2]},
    ])
    def test_an_unreadable_rule_raises_instead_of_being_skipped(self, meta):
        """Skipping would fall through to an ancestor, and the caller would be
        routed by a decision nobody made for them — with a plausible answer and
        nothing saying the approved rule was unreadable."""
        row = _row("x", ["local"], uuid4())
        row["metadata"] = meta
        with pytest.raises(MalformedRoutingRule):
            parse(row)


class TestWhichScopeWins:
    def test_the_deepest_scope_that_decided_wins(self):
        root, leaf = uuid4(), uuid4()
        rows = [
            _row("summarize", ["local"], root),
            _row("summarize", ["local2"], leaf),
        ]
        assert resolve(rows, [root, leaf], "summarize", _config()).candidates == ("local2",)

    def test_an_ancestor_rule_applies_where_no_deeper_one_exists(self):
        """Counter-probe: precedence must not mean 'only the leaf counts'."""
        root, leaf = uuid4(), uuid4()
        rows = [_row("summarize", ["local"], root)]
        assert resolve(rows, [root, leaf], "summarize", _config()).candidates == ("local",)

    def test_a_rule_for_another_task_type_does_not_answer(self):
        root = uuid4()
        rows = [_row("extract", ["local"], root)]
        with pytest.raises(NoRouteForTask, match="summarize"):
            resolve(rows, [root], "summarize", _config())


class TestWhenNobodyDecided:
    def test_no_rule_refuses_and_names_the_scope(self):
        """Not a default. An installation that has not decided should say so,
        not pick something and let the choice look deliberate in the trace."""
        leaf = uuid4()
        with pytest.raises(NoRouteForTask) as refused:
            resolve([], [uuid4(), leaf], "summarize", _config())
        assert str(leaf) in str(refused.value)
        assert "summarize" in str(refused.value)


class TestThePrivacyRatchetOnStoredRules:
    def test_a_chain_that_lowers_the_level_cannot_be_read(self):
        root = uuid4()
        rows = [_row("summarize", ["local", "hosted"], root)]
        with pytest.raises(MalformedRoutingRule, match="less protected"):
            resolve(rows, [root], "summarize", _config())

    def test_a_chain_may_ascend_or_stay_level(self):
        """Counter-probe: the check must not refuse the normal case."""
        root = uuid4()
        assert resolve([_row("s", ["local", "local2"], root)], [root], "s",
                       _config()).candidates == ("local", "local2")
        assert resolve([_row("s", ["hosted", "local"], root)], [root], "s",
                       _config()).candidates == ("hosted", "local")

    def test_a_loosening_rule_does_not_fall_through_to_the_ancestor(self):
        """It raises rather than being ignored.

        Falling through would route the caller by a decision that was
        overruled, and the trace would show a model nobody chose for them.
        """
        root, leaf = uuid4(), uuid4()
        rows = [
            _row("s", ["local"], root),
            _row("s", ["local", "hosted"], leaf),
        ]
        with pytest.raises(MalformedRoutingRule):
            resolve(rows, [root, leaf], "s", _config())

    def test_an_unreadable_rule_does_not_fall_through_to_the_ancestor(self):
        """Driven through `resolve`, not through `parse`.

        The claim that a broken rule raises instead of being skipped lives in
        the loop, and a test that calls `parse` directly cannot see it: the
        counter-probe wrapped the parse call in a `try/except: continue` and
        every test stayed green. Skipping means the caller is routed by the
        ancestor's decision, which is not the one that was approved for them —
        with a plausible answer and nothing saying the rule was unusable.
        """
        root, leaf = uuid4(), uuid4()
        broken = _row("s", ["local"], leaf)
        broken["metadata"] = {"task_type": "s"}  # keine Kandidaten
        rows = [_row("s", ["local2"], root), broken]

        with pytest.raises(MalformedRoutingRule):
            resolve(rows, [root, leaf], "s", _config())

    def test_a_rule_naming_an_undeclared_model_raises(self):
        root = uuid4()
        rows = [_row("s", ["gibtsnicht"], root)]
        with pytest.raises(RoutingConfigError, match="not declared"):
            resolve(rows, [root], "s", _config())


# ── The shipped declaration, not a fixture (CHY-157) ──────────────────────────

class TestTheShippedDeclarationCanRefuse:
    """Every test above runs against a made-up config with a model called
    `hosted`. They prove the check works. They do not prove the shipped file
    gives it anything to refuse — and until CHY-157 it did not: five models,
    all local, all `critical`. The check compared equal ranks and could never
    say no.

    These run against `router/routing-config.yaml` as deployed. The models are
    picked by rank rather than by name, so a renamed model does not break the
    test, but a file in which every model is `critical` again does.
    """

    @staticmethod
    def _by_rank() -> tuple[RouterConfig, list[str], list[str]]:
        config = RouterConfig()
        ranks = {m: config.privacy_rank(m) for m in config.models()}
        top = max(ranks.values())
        strictest = sorted(m for m, r in ranks.items() if r == top)
        looser = sorted(m for m, r in ranks.items() if r < top)
        return config, strictest, looser

    def test_the_shipped_file_declares_something_below_the_strictest_level(self):
        """The structural half. Without a model below the top rank, every
        chain is level, and the privacy ratchet on routing is decoration."""
        _, strictest, looser = self._by_rank()
        assert looser, (
            f"every declared model sits at the same privacy level "
            f"({strictest}) — check_chain has nothing it could ever refuse"
        )

    def test_a_local_first_chain_falling_back_to_the_cloud_is_refused(self):
        """The counter-probe CHY-157 asked for, against the real file."""
        config, strictest, looser = self._by_rank()
        root = uuid4()
        rows = [_row("summarize", [strictest[0], looser[0]], root)]
        with pytest.raises(MalformedRoutingRule, match="less protected"):
            resolve(rows, [root], "summarize", config)

    def test_cloud_first_with_a_local_fallback_is_allowed(self):
        """The shape an exhausted quota needs (CHY-159).

        A hosted model first, a local one behind it: when the Free Plan's
        limit is spent, Ollama answers 429 and the chain moves on to a model
        that may see at least as much. Refusing this would make the only safe
        use of a hosted model impossible.
        """
        config, strictest, looser = self._by_rank()
        root = uuid4()
        chain = [looser[0], strictest[0]]
        assert resolve([_row("summarize", chain, root)], [root], "summarize",
                       config).candidates == tuple(chain)


# ── the answer length (CHY-166) ───────────────────────────────────────────────

class TestTheAnswerLength:
    """A rule bounds its answers. Until CHY-166 nothing did, and on 25.09.2026
    the word "ping" took a throttled Raspberry Pi five minutes — past the API's 300 s."""

    def test_the_limit_is_read_from_metadata(self):
        assert parse(_row("s", ["local"], uuid4(), max_tokens=64)).max_tokens == 64

    @pytest.mark.parametrize("value", [None, 0, -1, True, "256", 256.0])
    def test_a_rule_without_a_usable_limit_is_unreadable(self, value):
        """Required, not defaulted: a rule without a limit is the state CHY-166
        ended. `True` is an int in Python and a typo in a rule."""
        row = _row("s", ["local"], uuid4())
        if value is None:
            del row["metadata"]["max_tokens"]
        else:
            row["metadata"]["max_tokens"] = value
        with pytest.raises(MalformedRoutingRule, match="max_tokens"):
            parse(row)

    def test_the_limit_is_the_winning_rules(self):
        """The limit travels with the chain it belongs to — the deeper scope's,
        not the ancestor's."""
        root, leaf = uuid4(), uuid4()
        rows = [_row("s", ["local"], root, max_tokens=512),
                _row("s", ["local2"], leaf, max_tokens=32)]
        rule = resolve(rows, [root, leaf], "s", _config())
        assert (rule.candidates, rule.max_tokens) == (("local2",), 32)

    def test_without_a_request_the_rules_limit_applies(self):
        rule = parse(_row("s", ["local"], uuid4(), max_tokens=64))
        assert token_limit(rule, None) == 64

    @pytest.mark.parametrize("requested", [1, 16, 64])
    def test_a_caller_may_ask_for_less_or_the_same(self, requested):
        rule = parse(_row("s", ["local"], uuid4(), max_tokens=64))
        assert token_limit(rule, requested) == requested

    def test_a_caller_may_not_ask_for_more(self):
        """Refused, not lowered: an answer silently cut to the rule's length
        would look like a model that stopped early."""
        rule = parse(_row("s", ["local"], uuid4(), max_tokens=64))
        with pytest.raises(TokenLimitAboveRule, match="at most 64"):
            token_limit(rule, 65)


def test_every_seeded_route_proposes_a_readable_limit():
    """The seed writes drafts a reviewer approves; since CHY-166 a draft
    without max_tokens could never be approved. Same keys, and every one
    parses — read through the real parser, not by inspecting the dict."""
    from scripts.seed_routing_rules import FORMER_ROUTES, PROPOSED_MAX_TOKENS

    assert set(PROPOSED_MAX_TOKENS) == set(FORMER_ROUTES)
    for task_type, candidates in FORMER_ROUTES.items():
        row = _row(task_type, candidates, uuid4(), max_tokens=PROPOSED_MAX_TOKENS[task_type])
        assert parse(row).max_tokens == PROPOSED_MAX_TOKENS[task_type]
