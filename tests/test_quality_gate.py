"""The gate that measures the code has to be measured too (CHY-84).

On 2026-08-16 `scripts/quality_gate.py` was run outside the venv and reported
every ruff family at 0, `unused_actor` down from 40 and codespell down from 16.
Nothing had been fixed. `python -m ruff` writes its "No module named" to stderr
and leaves stdout empty, and the collector only read stdout — so an absent tool
produced the same output as a clean codebase, and the ratchet read it as
progress.

That is the failure this file pins down. A gate that fails open does not merely
stop protecting: it manufactures evidence that the protection worked.

The tests deliberately exercise the guards rather than the counters. The
counters need ruff, a checkout and a few seconds; the guards are where the
silence came from.
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))

quality_gate = importlib.import_module("quality_gate")


class TestMissingToolIsNotZeroFindings:
    """The original defect: absence scored as cleanliness."""

    def test_a_missing_tool_raises_instead_of_counting_zero(self):
        with pytest.raises(quality_gate.ToolMissingError) as excinfo:
            quality_gate._require("a_linter_that_does_not_exist")

        # The message has to name the interpreter. The tool was installed --
        # just not in the Python that ran the script, and that is the whole
        # confusion the message exists to resolve.
        assert sys.executable in str(excinfo.value)

    def test_an_installed_tool_passes_quietly(self):
        quality_gate._require("json")

    @pytest.mark.parametrize("collector", ["ruff_counts", "unused_actor", "codespell_count"])
    def test_every_tool_backed_collector_checks_first(self, collector, monkeypatch):
        """Not just `_require` — each collector must actually call it.

        The guard existing somewhere in the module is worth nothing; the first
        version of this fix protected `ruff_counts` and left `unused_actor`
        counting zero from an empty stdout.
        """
        called: list[str] = []
        monkeypatch.setattr(quality_gate, "_require", lambda m: called.append(m))
        monkeypatch.setattr(quality_gate, "_run", lambda cmd: "")

        getattr(quality_gate, collector)()

        assert called, f"{collector} misst, ohne das Werkzeug zu prüfen"

    def test_a_missing_git_aborts_rather_than_scanning_the_directories(self, monkeypatch):
        """git is a measuring tool here, so its absence must abort like a linter's.

        codespell counts tracked files. Without git the tempting fallback is
        "then scan the directories" — which is exactly the mode that counted six
        locally generated fixture PDFs and made the same commit score 172 on a
        developer machine and 146 in CI. A ratchet compares counts over time,
        which means nothing if the ruler changes with the machine.
        """
        monkeypatch.setattr(quality_gate.shutil, "which", lambda _name: None)
        with pytest.raises(SystemExit) as excinfo:
            quality_gate._require_git()
        assert "tracked files" in str(excinfo.value)

    def test_the_collector_is_the_one_that_checks(self, monkeypatch):
        """Driven through codespell_count, not through the guard.

        The first version of the test above called `_require_git()` directly,
        so deleting the call from `codespell_count` left the suite green — the
        guard was tested, its use was not. Third time this month that a removal
        probe found a test describing the code instead of checking it.
        """
        monkeypatch.setattr(quality_gate.shutil, "which", lambda _name: None)
        monkeypatch.setattr(quality_gate, "_run", lambda cmd: "")
        with pytest.raises(SystemExit):
            quality_gate.codespell_count()

    def test_codespell_counts_only_what_is_committed(self, monkeypatch):
        """The count must not see an untracked file lying in the tree."""
        seen: list[list[str]] = []

        def fake_run(cmd):
            seen.append(cmd)
            return "a.py\n" if cmd[0] == "git" else ""

        monkeypatch.setattr(quality_gate, "_run", fake_run)
        quality_gate.codespell_count()

        assert seen[0][:2] == ["git", "ls-files"], "the file list has to come from git"
        assert seen[1][-1] == "a.py", "codespell must be handed that list, not the directories"


class TestMetricsCannotVanishSilently:
    """A metric that stops being collected must not read as 'nothing found'."""

    def test_a_baseline_metric_absent_from_the_run_is_reported(self):
        base = {"codespell": 16, "coverage_percent": 73.6, "ruff": {"B": 92}}
        current = {"codespell": 16, "ruff": {"B": 92}}

        assert quality_gate.unmeasured(current, base) == ["coverage_percent"]

    def test_compare_alone_would_have_missed_it(self):
        """Why the extra function exists at all.

        `compare` skips any key missing on either side, which is correct for
        comparing but wrong for reporting: it cannot distinguish "measured, no
        change" from "not measured".
        """
        base = {"coverage_percent": 73.6, "ruff": {}}
        current = {"ruff": {}}

        worse, better = quality_gate.compare(current, base)
        assert not worse and not better

    def test_ruff_is_not_reported_as_missing(self):
        """`ruff` is a nested dict handled by compare's own loop, not a scalar."""
        assert quality_gate.unmeasured({"ruff": {}}, {"ruff": {"B": 1}}) == []


class TestTheRulerMustNotChangeUnderTheMeasurement:
    """CI failed pipeline #244 on a regression nobody had written.

    `ruff>=0.4` resolved to 0.15.20 locally and 0.16.3 in CI, and the same
    commit scored PL 52 here and 55 there — three rules that did not exist in
    the older version. A ratchet compares counts over time, which only means
    something while the ruler stays the same.

    pyproject.toml pins the tools; these tests cover the case that pinning
    cannot reach, an environment someone assembled by hand.
    """

    def test_a_different_version_is_reported(self):
        drift = quality_gate.tool_drift(
            {"_tools": {"ruff": "0.16.3", "codespell": "2.4.3"}},
            {"_tools": {"ruff": "0.15.20", "codespell": "2.4.3"}},
        )
        assert len(drift) == 1
        # Both numbers have to appear: "versions differ" without saying which
        # leaves the reader exactly where they started.
        assert "0.15.20" in drift[0]
        assert "0.16.3" in drift[0]

    def test_matching_versions_are_silent(self):
        same = {"_tools": {"ruff": "0.16.3", "codespell": "2.4.3"}}
        assert quality_gate.tool_drift(same, same) == []

    def test_an_older_baseline_without_versions_says_so(self):
        drift = quality_gate.tool_drift({"_tools": {"ruff": "0.16.3"}}, {"ruff": {}})
        assert drift, "eine Grundlinie ohne Versionen darf nicht stillschweigend durchgehen"

    def test_a_missing_tool_in_the_run_counts_as_drift(self):
        drift = quality_gate.tool_drift(
            {"_tools": {"ruff": "0.16.3"}},
            {"_tools": {"ruff": "0.16.3", "codespell": "2.4.3"}},
        )
        assert any("codespell" in d for d in drift)

    def test_the_version_block_is_not_mistaken_for_a_metric(self):
        """`_tools` lives beside the counts and must not be compared as one."""
        base = {"_tools": {"ruff": "0.16.3"}, "ruff": {}}
        assert quality_gate.unmeasured({"ruff": {}}, base) == []


class TestTheRatchetStillRatchets:
    """Guard rails on the comparison itself, so the guards above have a point."""

    def test_a_growing_count_fails(self):
        worse, _ = quality_gate.compare({"ruff": {"B": 93}}, {"ruff": {"B": 92}})
        assert worse

    def test_a_shrinking_count_passes_and_is_noted(self):
        worse, better = quality_gate.compare({"ruff": {"B": 91}}, {"ruff": {"B": 92}})
        assert not worse
        assert better

    def test_coverage_has_slack_but_not_unlimited_slack(self):
        small = {"coverage_percent": 73.3, "ruff": {}}
        large = {"coverage_percent": 71.0, "ruff": {}}
        base = {"coverage_percent": 73.6, "ruff": {}}

        assert not quality_gate.compare(small, base)[0]
        assert quality_gate.compare(large, base)[0]
