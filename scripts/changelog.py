#!/usr/bin/env python3
"""
scripts/changelog.py

The changelog of the public snapshots, in English and German, held in step (CHY-226).

Each snapshot of the public repository gets a section in CHANGELOG.md and the
same section in CHANGELOG.de.md, headed by its date and grouped as

    ### Added / Neu   ### Changed / Geändert   ### Removed / Entfernt
    ### Fixed / Behoben   ### Security / Sicherheit

Two languages are two chances to drift apart, and the German file would be
the one nobody reads before a snapshot. So --check compares them: the same
dates in the same order, the same groups, the same number of entries per
group. It cannot judge a translation; it does notice a section, a group or an
entry that exists in one language only.

--release creates the GitHub release of a snapshot: tag `snapshot-<date>` of
the newest section, the two sections as its text, marked as a pre-release
(Cohereny has no 1.0 yet). It refuses when that tag already belongs to another
commit — which is what happens when a snapshot goes out without a section of
its own, and the previous date would otherwise be reused for new content.

Usage:
    python scripts/changelog.py --check
    GITHUB_TOKEN=... python scripts/changelog.py --release \\
        --repo cohereny/cohereny --commit <sha>

Standard library only: the publish branch runs it in a bare Python image.
"""
from __future__ import annotations

import argparse
import json
from http import HTTPStatus
import os
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EN_FILE = ROOT / "CHANGELOG.md"
DE_FILE = ROOT / "CHANGELOG.de.md"

#: The groups a section may have, English → German, in the order they appear.
GROUPS: dict[str, str] = {
    "Added": "Neu",
    "Changed": "Geändert",
    "Removed": "Entfernt",
    "Fixed": "Behoben",
    "Security": "Sicherheit",
}

GITHUB_API = "https://api.github.com"

_SECTION = re.compile(r"^## (\d{4}-\d{2}-\d{2})\s*$")
_GROUP = re.compile(r"^### (.+?)\s*$")


@dataclass
class Section:
    """One snapshot: its date, its groups with their entries, and its text."""

    date: str
    groups: dict[str, list[str]] = field(default_factory=dict)
    text: str = ""


def parse(markdown: str) -> list[Section]:
    """The sections of a changelog, in file order.

    Anything before the first `## ` heading is the introduction and is not a
    section. A `## ` heading that is not a date is an error rather than
    something to skip: a section the parser does not see is a section --check
    cannot compare.
    """
    sections: list[Section] = []
    current: Section | None = None
    group: str | None = None
    body: list[str] = []

    def close() -> None:
        if current is not None:
            current.text = "\n".join(body).strip()

    for line in markdown.splitlines():
        heading = _SECTION.match(line)
        if heading:
            close()
            current, group, body = Section(heading.group(1)), None, []
            sections.append(current)
            continue
        if line.startswith("## "):
            msg = f"a section heading must be a date (## YYYY-MM-DD): {line!r}"
            raise ValueError(msg)
        if current is None:
            continue
        body.append(line)
        sub = _GROUP.match(line)
        if sub:
            group = sub.group(1)
            current.groups.setdefault(group, [])
        elif line.startswith("- ") and group is not None:
            current.groups[group].append(line[2:])
    close()
    return sections


def problems(en: list[Section], de: list[Section]) -> list[str]:
    """Everything that keeps the two files from saying the same thing. Empty is good."""
    found: list[str] = []
    dates_en, dates_de = [s.date for s in en], [s.date for s in de]
    if not en:
        found.append("CHANGELOG.md has no section")
    if dates_en != dates_de:
        found.append(f"the sections differ: CHANGELOG.md {dates_en}, CHANGELOG.de.md {dates_de}")
    if dates_en != sorted(set(dates_en), reverse=True):
        found.append(f"sections must be newest first, one per date: {dates_en}")
    for e, d in zip(en, de, strict=False):
        if e.date == d.date:
            found.extend(_section_problems(e, d))
    return found


def _section_problems(e: Section, d: Section) -> list[str]:
    """What keeps one section from matching its translation."""
    unknown = [g for g in e.groups if g not in GROUPS] + \
              [g for g in d.groups if g not in GROUPS.values()]
    if unknown:
        return [f"{e.date}: unknown groups {unknown}; allowed are {list(GROUPS.items())}"]
    expected = [GROUPS[g] for g in e.groups]
    if list(d.groups) != expected:
        return [(f"{e.date}: the groups differ — English {list(e.groups)} "
                 f"needs German {expected}, found {list(d.groups)}")]
    found = [(f"{e.date} {g}/{GROUPS[g]}: {len(entries)} entries in English, "
              f"{len(d.groups[GROUPS[g]])} in German")
             for g, entries in e.groups.items() if len(entries) != len(d.groups[GROUPS[g]])]
    if not any(e.groups.values()):
        found.append(f"{e.date}: the section has no entries")
    return found


def release_body(en: Section, de: Section) -> str:
    """The text of a GitHub release: the English section, then the German one."""
    return f"{en.text}\n\n---\n\n**Deutsch**\n\n{de.text}\n"


def _github(method: str, url: str, token: str, body: dict | None = None) -> tuple[int, dict]:
    """One GitHub API call; an HTTP error is an answer, not an exception."""
    request = urllib.request.Request(
        url, method=method,
        data=json.dumps(body).encode("utf-8") if body is not None else None,
        headers={"Authorization": f"Bearer {token}",
                 "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def release(repo: str, commit: str, newest: tuple[Section, Section], token: str,
            call=_github) -> str:
    """Create the release of the newest section (English, German), or explain why not.

    Running twice for the same commit is fine — a retried pipeline must not
    fail on its own earlier success. The same tag on another commit is not:
    that is a snapshot without a section of its own.
    """
    en, de = newest
    tag = f"snapshot-{en.date}"
    status, existing = call("GET", f"{GITHUB_API}/repos/{repo}/releases/tags/{tag}", token)
    if status == HTTPStatus.OK:
        if existing.get("target_commitish") == commit:
            return f"release {tag} already exists for {commit[:8]} — nothing to do"
        msg = (f"release {tag} already exists for another commit. This snapshot needs a "
               f"section of its own in CHANGELOG.md and CHANGELOG.de.md.")
        raise SystemExit(msg)
    if status != HTTPStatus.NOT_FOUND:
        msg = f"GitHub answered {status} when looking up {tag}: {existing}"
        raise SystemExit(msg)

    status, created = call("POST", f"{GITHUB_API}/repos/{repo}/releases", token, {
        "tag_name": tag,
        "target_commitish": commit,
        "name": f"Snapshot {en.date}",
        "body": release_body(en, de),
        "prerelease": True,
    })
    if status != HTTPStatus.CREATED:
        msg = f"GitHub refused release {tag}: {status} {created}"
        raise SystemExit(msg)
    return f"release {tag} created: {created.get('html_url', '?')}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[1])
    ap.add_argument("--check", action="store_true", help="compare the two files (default)")
    ap.add_argument("--release", action="store_true",
                    help="create the GitHub release of the newest section (needs GITHUB_TOKEN)")
    ap.add_argument("--repo", default="cohereny/cohereny")
    ap.add_argument("--commit", help="the commit the release points at")
    args = ap.parse_args()

    en = parse(EN_FILE.read_text(encoding="utf-8"))
    de = parse(DE_FILE.read_text(encoding="utf-8"))
    found = problems(en, de)
    if found:
        for p in found:
            print(f"✗ {p}", file=sys.stderr)
        return 1
    print(f"changelog: {len(en)} sections in both languages, newest {en[0].date}")

    if args.release:
        token = os.environ.get("GITHUB_TOKEN", "")
        if not token or not args.commit:
            print("--release needs GITHUB_TOKEN in the environment and --commit", file=sys.stderr)
            return 2
        print(release(args.repo, args.commit, (en[0], de[0]), token))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
