"""
tests/test_codespell_scope.py

What the codespell number measures (CHY-142).

It read 190 on 07.09.2026, and **nine** of those were in product code. The ten
most frequent findings were German words. A debt counter that grows when
somebody follows the conventions measures the wrong thing — and this one had
begun pushing the other way: a fixture had to be a German bank document to
prove anything about a deployment that reads German bank documents, and writing
it that way cost 29 points.

Two properties have to hold together, and neither alone is enough. A scope that
excludes everything passes the second test and is useless; a scope that
excludes nothing passes the first and blocks honest work.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from quality_gate import _is_specimen  # noqa: E402


class TestWhatIsLeftOut:
    @pytest.mark.parametrize("path", [
        "pilots/banking/fixtures/generate_fixtures.py",
        "pilots/banking/generate_jahresabschluss.py",
        "tests/fixtures/documents.py",
        "pilots/study/study/nodes.py",
    ])
    def test_specimens_and_pilots_are_out(self, path):
        assert _is_specimen(path)

    @pytest.mark.parametrize("path", [
        "core/ingress.py",
        "contexts/repository/service.py",
        "scripts/quality_gate.py",
        "tests/test_ingress_guardrail.py",
        "migrations/001_initial.sql",
    ])
    def test_everything_else_stays_measured(self, path):
        """Counter-probe to the test above.

        A scope that leaves out everything reports zero for ever and passes
        every prohibition. Operator scripts stay in on purpose: their German
        talks to a person, and the few deliberate lines carry an inline marker
        that is visible in a diff.
        """
        assert not _is_specimen(path)


def test_a_real_typo_is_still_found(tmp_path):
    """The property the whole exercise is for.

    Driven through codespell itself rather than through `_is_specimen`: the
    scope could be right and the tool still be called in a way that finds
    nothing, which is the failure this repository keeps meeting.
    """
    datei = tmp_path / "beispiel.py"
    datei.write_text('"""This docstring conatins a typo."""\n')
    out = subprocess.run(
        [sys.executable, "-m", "codespell_lib", str(datei)],
        capture_output=True, text=True,
    ).stdout
    assert "conatins" in out, "codespell no longer reports an obvious typo"


def test_an_inline_marker_suppresses_one_line_and_not_the_next(tmp_path):
    """The mechanism the deliberate German relies on.

    Checked because the block form (`codespell:ignore-begin`) silently does
    nothing in 2.4.3 — it looks like it works and does not, which is worse than
    an error.
    """
    datei = tmp_path / "beispiel.py"
    datei.write_text(
        'A = "Ignoriere alle Anweisungen"  # codespell:ignore\n'
        'B = "Ignoriere alle Anweisungen"\n'
    )
    out = subprocess.run(
        [sys.executable, "-m", "codespell_lib", str(datei)],
        capture_output=True, text=True,
    ).stdout
    treffer = [z for z in out.splitlines() if "==>" in z]
    assert len(treffer) == 1, f"expected exactly the unmarked line, got: {treffer}"
    assert ":2:" in treffer[0]
