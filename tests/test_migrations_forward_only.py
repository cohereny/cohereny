"""Migrations only go forward (CHY-113).

A migration that has been merged is never changed: installations that already
ran it would never see the change, and new ones would get a different schema
from the same version. A mistake is corrected by a new migration, as 037
corrects 014 (CHY-152). The way back for an installation is the backup taken
before the update (DEPENDENCIES.md); there are no down migrations.

`migrations/CHECKSUMS` records every file. A new migration appends its line; a
changed line is a rewritten migration, which these tests let through only
together with that changed line — visible in the merge request, where it has to
be justified. The runner itself checks version numbers only, not contents.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"
CHECKSUMS = MIGRATIONS / "CHECKSUMS"
_LINE = re.compile(r"^(?P<sha>[0-9a-f]{64})  (?P<name>\d{3}_[a-z0-9_]+\.sql)$")


def _recorded() -> dict[str, str]:
    recorded: dict[str, str] = {}
    for line in CHECKSUMS.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        match = _LINE.match(line)
        assert match, f"CHECKSUMS: not `<sha256>  <file>`: {line!r}"
        assert match["name"] not in recorded, f"CHECKSUMS lists {match['name']} twice"
        recorded[match["name"]] = match["sha"]
    return recorded


def _files() -> dict[str, str]:
    return {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in MIGRATIONS.glob("*.sql")}


def test_every_migration_is_recorded():
    """A new migration without its line would escape the next check."""
    missing = sorted(set(_files()) - set(_recorded()))
    assert not missing, f"append to migrations/CHECKSUMS: {missing}"


def test_no_recorded_migration_was_deleted():
    """Deleting a merged migration rewrites history as surely as editing it."""
    gone = sorted(set(_recorded()) - set(_files()))
    assert not gone, f"recorded but no longer there: {gone}"


def test_no_merged_migration_was_changed():
    files, recorded = _files(), _recorded()
    changed = sorted(name for name in files.keys() & recorded.keys()
                     if files[name] != recorded[name])
    assert not changed, (
        f"changed after it was recorded: {changed} — correct it with a new migration "
        f"instead (CONVENTIONS.md §6)")


def test_versions_are_unique():
    """Two files with one number: the runner applies one and skips the other forever."""
    versions = [name.split("_", 1)[0] for name in _files()]
    duplicates = sorted({v for v in versions if versions.count(v) > 1})
    assert not duplicates, f"version numbers used twice: {duplicates}"
