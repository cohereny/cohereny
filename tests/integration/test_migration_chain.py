"""
tests/integration/test_migration_chain.py

The chain the job just applied is the chain in the repository (CHY-124).

`tests/test_status_vocabularies.py` checks the numbering on disk. This checks
what a database made of it: every file ran, none twice, none extra. Without it
the job could apply a subset — a runner that stopped early and exited 0 — and
every test below would run against a schema that is not the one shipped.
"""
from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

_MIGRATIONS = Path(__file__).resolve().parents[2] / "migrations"


async def test_every_migration_file_was_applied_once(pool):
    on_disk = sorted(int(p.name[:3]) for p in _MIGRATIONS.glob("[0-9]*.sql"))
    applied = [r["version"] for r in await pool.fetch(
        "SELECT version FROM schema_migrations ORDER BY version"
    )]
    assert applied == on_disk, (
        f"applied {applied}, on disk {on_disk} — the database is not the schema "
        f"the repository ships"
    )
