"""
tests/test_public_snapshot.py

The public snapshot and the check that guards it (CHY-188).

Two kinds of test. The first two run against this repository in every pipeline, so the
tree stays publishable: a new internal host name in a public file fails here, at the
commit, not on release day. The rest show that the check finds what it claims to find —
a scan that reports nothing proves something only once it has been seen to report.

Every test that empties a directory uses a temporary repository. A test that pointed
the export at this checkout would, the day the guard broke, delete the repository it is
testing.
"""
from __future__ import annotations

import datetime
import subprocess
from pathlib import Path

import pytest

from scripts import public_snapshot as ps

ROOT = ps.ROOT


# ── This repository ───────────────────────────────────────────────────────────

def test_every_tracked_file_is_classified():
    """A new file must be declared public or internal, not fall into either by default."""
    _, unclassified = ps.classify(ps.tracked_files(ROOT))
    assert unclassified == [], f"classify in scripts/public_snapshot.py: {unclassified}"


def test_the_public_files_hold_nothing_internal():
    public, _ = ps.classify(ps.tracked_files(ROOT))
    findings = ps.scan(ROOT, public, ps.load_patterns(ROOT))
    assert findings == [], "\n".join(str(f) for f in findings)


def test_the_check_and_the_publish_ci_are_where_they_must_be():
    """The patterns file must never be published; the publish CI is published and must
    therefore name nothing internal itself."""
    assert ps._matches(ps.PATTERNS_FILE, ps.INTERNAL)
    assert not ps._matches(ps.PATTERNS_FILE, ps.PUBLIC)
    assert ps._matches(ps.PUBLISH_CI, ps.INTERNAL)
    if not (ROOT / ps.PUBLISH_CI).exists():
        pytest.skip("this is the snapshot: the publish CI sits at its root, scanned there")
    findings = ps.scan(ROOT, [ps.PUBLISH_CI], ps.load_patterns(ROOT))
    assert findings == [], "\n".join(str(f) for f in findings)


# ── The scan finds what it claims to ──────────────────────────────────────────

def _scan_text(tmp_path: Path, text: str, patterns=None) -> list[ps.Finding]:
    (tmp_path / "f.txt").write_text(text, encoding="utf-8")
    return ps.scan(tmp_path, ["f.txt"], patterns or ps.load_patterns(tmp_path))


# Assembled at runtime. Written out, these lines would be findings in this very file,
# and the snapshot would fail its own leak check — which the export dry run showed on
# 29.10.2026.
_DOT = "."
_LEAKS = [
    "DATABASE=postgresql://u:p@" + _DOT.join(["192", "168", "7", "20"]) + ":5432/x",
    "gateway " + _DOT.join(["10", "0", "4", "2"]),
    _DOT.join(["172", "20", "1", "9"]) + " is a docker network",
    "-----BEGIN OPENSSH " + "PRIVATE KEY-----",
    "token = glpat" + "-" + "a" * 24,
    "GITHUB=ghp" + "_" + "a" * 36,
]


@pytest.mark.parametrize("text", _LEAKS)
def test_a_generic_leak_is_found(tmp_path, text):
    assert _scan_text(tmp_path, text)


@pytest.mark.parametrize("text", [
    "listen on 127.0.0.1:8000 or 0.0.0.0",
    "https://auth.example.com/application/o",
    "version 10.2 of the arc42 template",
])
def test_ordinary_text_is_not(tmp_path, text):
    """The counter-probe: a check that flags everything finds every leak too."""
    assert _scan_text(tmp_path, text) == []


def test_internal_names_come_from_the_patterns_file(tmp_path):
    (tmp_path / ps.PATTERNS_FILE).write_text("# comment\n\\bhidden-host\\b\n", encoding="utf-8")
    assert _scan_text(tmp_path, "deployed on Hidden-Host behind the proxy")
    assert _scan_text(tmp_path, "deployed on a shared host") == []


def test_or_from_the_environment(tmp_path, monkeypatch):
    """The publish branch has no patterns file; its CI hands the list in."""
    monkeypatch.setenv("LEAK_PATTERNS", "hidden-host\nother-host")
    assert _scan_text(tmp_path, "see other-host:4000")


def test_binary_files_are_skipped(tmp_path):
    (tmp_path / "logo.png").write_bytes(b"\x89PNG\0\0 " + _DOT.join(["192", "168", "1", "1"]).encode())
    assert ps.scan(tmp_path, ["logo.png"], ps.load_patterns(tmp_path)) == []


# ── The export ────────────────────────────────────────────────────────────────

def _git(path: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=path, check=True, capture_output=True)


def _source(tmp_path: Path) -> Path:
    src = tmp_path / "src"
    (src / "api").mkdir(parents=True)
    (src / "publish").mkdir()
    (src / "README.md").write_text("readme", encoding="utf-8")
    (src / "api" / "main.py").write_text("app = None", encoding="utf-8")
    (src / "compose.yaml").write_text("internal stack", encoding="utf-8")
    (src / "publish" / "gitlab-ci.yml").write_text("stages: [check]", encoding="utf-8")
    _git(src, "init", "-q")
    _git(src, "add", ".")
    return src


def _target(tmp_path: Path, branch: str = "publish") -> Path:
    dst = tmp_path / "dst"
    dst.mkdir()
    _git(dst, "init", "-q", "-b", branch)
    (dst / "stale.txt").write_text("from an earlier snapshot", encoding="utf-8")
    return dst


def test_export_writes_exactly_the_snapshot(tmp_path):
    src, dst = _source(tmp_path), _target(tmp_path)
    written = ps.export(src, dst, docs=None)

    assert written == [".gitlab-ci.yml", "README.md", "api/main.py"]
    assert (dst / ".gitlab-ci.yml").read_text(encoding="utf-8") == "stages: [check]"
    assert not (dst / "compose.yaml").exists(), "an internal file reached the snapshot"
    assert not (dst / "stale.txt").exists(), "a file from an earlier snapshot lingered"
    assert (dst / ".git").is_dir()


def test_export_takes_the_documents_along(tmp_path):
    src, dst = _source(tmp_path), _target(tmp_path)
    docs = tmp_path / "docs-repo"
    for name in ps.DOCS:
        (docs / name).parent.mkdir(parents=True, exist_ok=True)
        (docs / name).write_text(name, encoding="utf-8")

    written = ps.export(src, dst, docs=docs)

    assert all(f"docs/{name}" in written for name in ps.DOCS)
    assert (dst / "docs" / "cohereny-arc42.md").exists()


def test_export_through_main_works_on_the_publish_branch(tmp_path):
    """The positive case, through the real entry point and this repository. Without it
    the refusals below could pass because main() refuses everything — which is what
    happened on 29.10.2026, when a target without a first commit was refused for that
    reason and not for its branch."""
    if not (ROOT / ps.PUBLISH_CI).exists():
        pytest.skip("this is the snapshot: exports are made from dev, not from here")
    dst = _target(tmp_path, branch="publish")
    assert ps.main(["--export", str(dst)]) == 0
    assert (dst / "README.md").exists() and not (dst / "stale.txt").exists()


def test_export_refuses_a_checkout_of_another_branch(tmp_path):
    dst = _target(tmp_path, branch="main")
    assert ps._branch(dst) == "main", "the refusal must be about the branch"
    assert ps.main(["--export", str(dst)]) == 2
    assert (dst / "stale.txt").exists(), "the refused target was emptied anyway"


def test_export_refuses_a_directory_without_git(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "keep.txt").write_text("x", encoding="utf-8")
    assert ps.main(["--export", str(plain)]) == 2
    assert (plain / "keep.txt").exists()


# ── The scan the publish CI runs ──────────────────────────────────────────────

def _tracked_repo(tmp_path: Path, *, add: bool = True) -> Path:
    repo = tmp_path / "snap"
    repo.mkdir()
    (repo / "README.md").write_text("a clean public file", encoding="utf-8")
    _git(repo, "init", "-q")
    if add:
        _git(repo, "add", ".")
    return repo


@pytest.fixture
def no_patterns_file(tmp_path, monkeypatch):
    """main() as on the publish branch: no .leak-patterns next to the script."""
    root = tmp_path / "root"
    root.mkdir()
    monkeypatch.setattr(ps, "ROOT", root)
    monkeypatch.delenv("LEAK_PATTERNS", raising=False)


def test_scan_passes_with_files_and_internal_patterns(tmp_path, monkeypatch, capsys, no_patterns_file):
    """The positive case the two refusals below are measured against."""
    monkeypatch.setenv("LEAK_PATTERNS", "hidden-host")
    assert ps.main(["--scan", str(_tracked_repo(tmp_path))]) == 0
    assert "1 files scanned against 1 internal" in capsys.readouterr().out


def test_scan_refuses_when_nothing_is_tracked(tmp_path, monkeypatch, capsys, no_patterns_file):
    """Before the first `git add` the scan used to say "0 files scanned, no finding"."""
    monkeypatch.setenv("LEAK_PATTERNS", "hidden-host")
    assert ps.main(["--scan", str(_tracked_repo(tmp_path, add=False))]) == 1
    assert "nothing was scanned" in capsys.readouterr().err


def test_scan_refuses_without_internal_patterns(tmp_path, capsys, no_patterns_file):
    """A missing or misnamed LEAK_PATTERNS must stop the mirror, not shrink the check."""
    assert ps.main(["--scan", str(_tracked_repo(tmp_path))]) == 1
    assert "LEAK_PATTERNS" in capsys.readouterr().err


def test_the_mirror_pushes_to_a_full_refname():
    """The first push into the empty GitHub repository failed with `HEAD:main`: the CI
    checkout is a detached HEAD, and with no `main` on the far side git cannot guess."""
    ci = ROOT / ps.PUBLISH_CI
    if not ci.exists():
        ci = ROOT / ".gitlab-ci.yml"          # in the snapshot the publish CI sits at the root
    text = ci.read_text(encoding="utf-8")
    assert "HEAD:refs/heads/main" in text
    assert "HEAD:main " not in text


# ── Visibility (CHY-132) ──────────────────────────────────────────────────────

_CUTOFF = datetime.date(2026, 11, 1)


def _repo(private: bool):
    return lambda _name: {"private": private}


def test_public_before_the_date_stops_the_mirror():
    ok, message = ps.visibility("o/r", _CUTOFF, datetime.date(2026, 10, 31), _repo(False))
    assert not ok and "PUBLIC" in message


def test_private_before_the_date_passes():
    ok, _ = ps.visibility("o/r", _CUTOFF, datetime.date(2026, 10, 31), _repo(True))
    assert ok


def test_from_the_date_on_it_only_reports():
    """Going public is a deliberate act (CHY-1); the check does not force it."""
    for private in (True, False):
        ok, _ = ps.visibility("o/r", _CUTOFF, _CUTOFF, _repo(private))
        assert ok
