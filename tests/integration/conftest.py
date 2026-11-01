"""
tests/integration/conftest.py

Tests against a real PostgreSQL (CHY-124).

Every other test in this repository runs against a pool double, and since
CHY-134 their docstrings have said the same sentence: *what this file cannot
see — the CHECK constraints, because no Postgres parses the DDL.* Three times
that gap was closed by hand, with a scratch database created, probed and
dropped for migrations 022, 023 and 024. This directory is the same probe,
every pipeline, without anyone remembering to do it.

Where the database comes from
-----------------------------
`DATABASE_URL`, set by the `integration` job in `.gitlab-ci.yml`, which starts
`postgres:16-alpine` as a service — the image dev runs — and applies every
migration from 001 before the tests start. The chain itself is therefore under
test on every run, not only the tables it leaves behind.

Deselected by default (`-m "not integration"` in pyproject.toml), so a checkout
without a database runs the unit suite as before. Run explicitly, the tests
**fail** without `DATABASE_URL` rather than skip: a guard that only runs where
the tool happens to exist is green for the wrong reason (the same argument the
`test` job makes for installing git).
"""
from __future__ import annotations

from uuid import UUID, uuid4

import asyncpg
import pytest

from api.config import settings
from core.db import _register_codecs
from tests.integration._redis import Generations


def _database_url() -> str:
    """The URL from `settings`, and only if it was actually configured.

    Read through `api.config` rather than `os.environ`: one place reads the
    environment (tests/test_internal_urls.py holds that line). And checked via
    `model_fields_set`, because the setting has a default pointing at
    localhost — without the check a missing variable would surface as a
    connection error that says nothing about what is missing.
    """
    if "database_url" not in settings.model_fields_set:
        pytest.fail(
            "DATABASE_URL is not set. Integration tests need a real PostgreSQL "
            "with every migration applied — run them through the `integration` "
            "job, or point DATABASE_URL at a scratch database."
        )
    return settings.database_url


@pytest.fixture
async def pool():
    """A pool per test, built the way the application builds its own.

    With the same connection init as `core.db` — the JSON codecs. A bare
    asyncpg pool rejects a dict for a JSONB parameter, and the first run of
    these tests failed on exactly that in `write_audit`: not a defect in the
    code, a test running against a different world than production. Reusing
    `_register_codecs` keeps the two from drifting.

    Function-scoped because the event loop is: a pool created on one loop and
    used on the next fails with a message about futures attached to a
    different loop, which says nothing about the test.
    """
    p = await asyncpg.create_pool(
        _database_url(), min_size=1, max_size=4, init=_register_codecs
    )
    try:
        yield p
    finally:
        await p.close()


@pytest.fixture
async def scope(pool) -> UUID:
    """A fresh root scope. Tests do not share rows; each writes its own tree."""
    scope_id = uuid4()
    await pool.execute(
        "INSERT INTO scope_nodes (id, scope_path, node_type, name) "
        "VALUES ($1, $2::ltree, 'org', 'it')",
        scope_id, f"org_{scope_id.hex}",
    )
    return scope_id


@pytest.fixture
async def workflow(pool, scope) -> UUID:
    """A draft workflow in its own scope, for node tests."""
    return await pool.fetchval(
        "INSERT INTO workflow_definitions (scope_id, name) VALUES ($1, 'it') RETURNING id",
        scope,
    )


@pytest.fixture
def redis() -> Generations:
    """Records the scope generations a block write bumps."""
    return Generations()
