"""The changelog in two languages, and the release made from it (CHY-226).

Two files saying the same thing drift apart unless something compares them.
These tests hold the committed changelog to scripts/changelog.py's rules and
show that each rule fires: a section, a group or an entry in one language only
is reported, and a snapshot that would reuse the previous section's tag is
refused instead of released.
"""
from __future__ import annotations

import pytest

from scripts import changelog as cl

EN = """# Changelog

Intro.

## 2026-11-04

### Added
- One thing.
- Another thing.

### Security
- A safer thing.

## 2026-11-01

### Added
- First publication.
"""

DE = """# Changelog

Einleitung.

## 2026-11-04

### Neu
- Eine Sache.
- Noch eine Sache.

### Sicherheit
- Eine sicherere Sache.

## 2026-11-01

### Neu
- Erstveröffentlichung.
"""


def _problems(en: str = EN, de: str = DE) -> list[str]:
    return cl.problems(cl.parse(en), cl.parse(de))


class TestTheCommittedChangelog:
    def test_both_languages_say_the_same_thing(self):
        en = cl.parse(cl.EN_FILE.read_text(encoding="utf-8"))
        de = cl.parse(cl.DE_FILE.read_text(encoding="utf-8"))
        assert cl.problems(en, de) == []

    def test_the_newest_section_comes_first(self):
        dates = [s.date for s in cl.parse(cl.EN_FILE.read_text(encoding="utf-8"))]
        assert dates == sorted(dates, reverse=True)


class TestParity:
    def test_matching_files_pass(self):
        """Counter-probe to the refusals below: they must not fire on agreement."""
        assert _problems() == []

    def test_a_section_in_one_language_only(self):
        de = DE.replace("## 2026-11-01\n\n### Neu\n- Erstveröffentlichung.\n", "")
        assert any("sections differ" in p for p in _problems(de=de))

    def test_an_entry_in_one_language_only(self):
        de = DE.replace("- Noch eine Sache.\n", "")
        assert any("2 entries in English, 1 in German" in p for p in _problems(de=de))

    def test_a_group_in_one_language_only(self):
        de = DE.replace("### Sicherheit\n- Eine sicherere Sache.\n", "")
        assert any("groups differ" in p for p in _problems(de=de))

    def test_an_unknown_group(self):
        en = EN.replace("### Security", "### Improved")
        assert any("unknown groups" in p for p in _problems(en=en))

    def test_sections_out_of_order(self):
        swapped = "# C\n\n## 2026-11-01\n\n### Added\n- a\n\n## 2026-11-04\n\n### Added\n- b\n"
        swapped_de = "# C\n\n## 2026-11-01\n\n### Neu\n- a\n\n## 2026-11-04\n\n### Neu\n- b\n"
        assert any("newest first" in p for p in _problems(swapped, swapped_de))

    def test_a_heading_that_is_not_a_date_is_an_error(self):
        """Skipping it would leave a section --check never compares."""
        with pytest.raises(ValueError, match="must be a version and a date"):
            cl.parse(EN.replace("## 2026-11-04", "## Unreleased"))

    def test_an_empty_section(self):
        en = "# C\n\n## 2026-11-04\n"
        de = "# C\n\n## 2026-11-04\n"
        assert any("no entries" in p for p in _problems(en, de))


class TestRelease:
    """The GitHub release of the newest section, with the API replaced."""

    @staticmethod
    def _sections():
        return cl.parse(EN)[0], cl.parse(DE)[0]

    @staticmethod
    def _github(answers: dict[str, tuple[int, dict]]):
        sent = []

        def call(method, url, _token, body=None):
            sent.append((method, url, body))
            return answers[method]
        return call, sent

    def test_a_new_snapshot_becomes_a_prerelease_with_both_languages(self):
        en, de = self._sections()
        call, sent = self._github({"GET": (404, {}), "POST": (201, {"html_url": "u"})})
        assert "created" in cl.release("o/r", "abc123", (en, de), "t", call=call)
        method, url, body = sent[-1]
        assert (method, url) == ("POST", f"{cl.GITHUB_API}/repos/o/r/releases")
        assert body["tag_name"] == "snapshot-2026-11-04"
        assert body["target_commitish"] == "abc123"
        assert body["prerelease"] is True
        assert "One thing." in body["body"] and "Eine Sache." in body["body"]

    def test_a_retried_pipeline_changes_nothing(self):
        en, de = self._sections()
        call, sent = self._github({"GET": (200, {"target_commitish": "abc123"})})
        assert "nothing to do" in cl.release("o/r", "abc123", (en, de), "t", call=call)
        assert [m for m, _, _ in sent] == ["GET"]

    def test_a_snapshot_without_its_own_section_is_refused(self):
        """The guard against forgetting the changelog: the newest section's tag
        already belongs to the previous snapshot."""
        en, de = self._sections()
        call, sent = self._github({"GET": (200, {"target_commitish": "older"})})
        with pytest.raises(SystemExit, match="needs a section of its own"):
            cl.release("o/r", "abc123", (en, de), "t", call=call)
        assert [m for m, _, _ in sent] == ["GET"]

    def test_an_unexpected_answer_stops_rather_than_guessing(self):
        en, de = self._sections()
        call, _ = self._github({"GET": (401, {"message": "Bad credentials"})})
        with pytest.raises(SystemExit, match="401"):
            cl.release("o/r", "abc123", (en, de), "t", call=call)


# ── versions (CHY-271) ────────────────────────────────────────────────────────

VERSIONED_EN = EN.replace("## 2026-11-04", "## 0.2.0 — 2026-11-14\n\n### Changed\n- A change.\n\n## 2026-11-04")
VERSIONED_DE = DE.replace("## 2026-11-04", "## 0.2.0 — 2026-11-14\n\n### Geändert\n- Eine Änderung.\n\n## 2026-11-04")


class TestVersions:
    def test_a_versioned_section_above_dated_ones_passes(self):
        en = cl.parse(VERSIONED_EN)
        assert (en[0].version, en[0].date, en[0].tag) == ("0.2.0", "2026-11-14", "v0.2.0")
        assert _problems(VERSIONED_EN, VERSIONED_DE) == []

    def test_a_dated_section_above_a_versioned_one_is_reported(self):
        """Once versions begin, a section without one is history, never news."""
        en = VERSIONED_EN.replace("## 0.2.0 — 2026-11-14", "## 2026-11-20\n\n### Added\n- x\n\n## 0.2.0 — 2026-11-14")
        de = VERSIONED_DE.replace("## 0.2.0 — 2026-11-14", "## 2026-11-20\n\n### Neu\n- x\n\n## 0.2.0 — 2026-11-14")
        assert any("must not follow" in p for p in _problems(en, de))

    def test_versions_out_of_order_are_reported(self):
        en = cl.parse(VERSIONED_EN.replace("## 0.2.0 — 2026-11-14",
                                           "## 0.2.0 — 2026-11-14\n\n### Fixed\n- x\n\n## 0.3.0 — 2026-11-10"))
        assert any("versions must be newest first" in p for p in cl._version_order_problems(en))

    def test_pyproject_may_be_ahead_of_the_changelog_but_not_behind(self):
        sections = cl.parse(VERSIONED_EN)
        assert cl.version_problems(sections, "0.2.0") == []      # just released
        assert cl.version_problems(sections, "0.3.0") == []      # the next one in the making
        assert cl.version_problems(sections, "0.1.0") == [
            "pyproject.toml says 0.1.0, but CHANGELOG.md already has 0.2.0"]

    def test_a_release_needs_the_section_of_its_version(self):
        newest = cl.parse(VERSIONED_EN)[0]
        assert cl.release_problems(newest, "0.2.0") == []
        assert "needs a section headed '## 0.3.0" in cl.release_problems(newest, "0.3.0")[0]
        dated = cl.parse(EN)[0]
        assert cl.release_problems(dated, "0.2.0"), "a dated section is no release of 0.2.0"

    @pytest.mark.parametrize(("version", "pre"), [("0.2.0", True), ("1.0.0", False)])
    def test_a_release_is_tagged_by_version_and_a_prerelease_below_one(self, version, pre):
        en = cl.parse(VERSIONED_EN.replace("0.2.0", version))[0]
        de = cl.parse(VERSIONED_DE.replace("0.2.0", version))[0]
        sent = []

        def call(method, url, _token, body=None):
            sent.append(body)
            return (404, {}) if method == "GET" else (201, {"html_url": "u"})

        cl.release("o/r", "abc123", (en, de), "t", call=call)
        body = sent[-1]
        assert (body["tag_name"], body["name"], body["prerelease"]) == (
            f"v{version}", f"Cohereny {version}", pre)

    def test_the_committed_changelog_is_not_ahead_of_pyproject(self):
        en = cl.parse(cl.EN_FILE.read_text(encoding="utf-8"))
        assert cl.version_problems(en, cl.project_version()) == []


class TestOnlyGitHubIsAsked:
    """Bandit B310 (CHY-273): urlopen opens any scheme, file:// included. _github
    refuses every URL that is not the GitHub API before anything is opened."""

    @pytest.mark.parametrize("url", [
        "file:///etc/passwd",
        "http://api.github.com/repos/o/r",                 # not https
        "https://api.github.com.example.org/repos/o/r",    # a lookalike host
        "https://example.org/repos/o/r",
    ])
    def test_another_url_is_refused_before_anything_is_opened(self, url, monkeypatch):
        opened = []
        monkeypatch.setattr(cl.urllib.request, "urlopen", lambda *a, **k: opened.append(a))
        with pytest.raises(ValueError, match="not a GitHub API URL"):
            cl._github("GET", url, "t")
        assert opened == []

    def test_the_github_api_is_asked(self, monkeypatch):
        """Counter-probe: the one URL the release uses goes through."""
        class Answer:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return b'{"ok": true}'
        asked = []
        monkeypatch.setattr(cl.urllib.request, "urlopen",
                            lambda request, timeout: asked.append(request.full_url) or Answer())
        url = f"{cl.GITHUB_API}/repos/o/r/releases/tags/v0.2.0"
        assert cl._github("GET", url, "t") == (200, {"ok": True})
        assert asked == [url]
