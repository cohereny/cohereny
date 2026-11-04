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

DE = """# Änderungsprotokoll

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
        with pytest.raises(ValueError, match="must be a date"):
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
