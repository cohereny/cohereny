"""Row-level security on the run tables, without a database (CHY-92).

The integration suite shows the policies at work (tests/integration/
test_run_isolation_db.py). What is held here:

  - the migrations (035 and every table after it) cover exactly the tables
    that hold run content, as kernel/retention.py lists them — a new content
    table without a policy would be readable by everyone signed in
  - the system context, which sees every run, is used only where the engine
    works for the installation: a request handler reaching for it would undo
    the isolation for its route without anyone noticing
  - acting_as says for whom and clears it again, also when the work fails
  - GET /v1/me names the actor id an admin needs to grant a membership
"""
from __future__ import annotations

import ast
import re
from http import HTTPStatus
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from api.v1.router import SURFACE_PATHS
from kernel import acting
from kernel.retention import INVENTORY
from tests.conftest import DEFAULT_ACTOR_ID, client_for, make_pool

ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS = ROOT / "migrations"

#: Where the system context may be reached for, and why.
SYSTEM_CONTEXT_ALLOWED = {
    "kernel/acting.py",           # defines it
    "core/db.py",                 # the engine's pool
    "api/v1/workflows.py",        # hands the engine's pool to the engine after the check
    "contexts/pilots/store.py",   # the pilot thread's statements
    "cli/retention.py",           # the purge
    "cli/oversight.py",           # gates nobody decided, expired or escalated (ADR-050)
}
_SYSTEM_NAMES = {"get_system_pool", "as_system", "connect_as_system"}


def _sql() -> str:
    """Every migration: 035 secured the tables there were, a later one secures
    the table it creates (036)."""
    return "\n".join(path.read_text(encoding="utf-8")
                     for path in sorted(MIGRATIONS.glob("[0-9]*.sql")))


class TestTheMigrationsCoverTheContent:
    def test_every_content_table_gets_row_security(self):
        content = {t for t, e in INVENTORY.items() if e.content_columns}
        secured = set(re.findall(r"ALTER TABLE (\w+) ENABLE ROW LEVEL SECURITY", _sql()))
        assert content, "the inventory names no content tables"
        assert secured == content

    def test_every_secured_table_has_a_policy_for_the_application(self):
        secured = set(re.findall(r"ALTER TABLE (\w+) ENABLE ROW LEVEL SECURITY", _sql()))
        policies = set(re.findall(r"CREATE POLICY \w+ ON (\w+) FOR ALL TO cohereny_app", _sql()))
        assert policies == secured


def _uses_system_context(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in _SYSTEM_NAMES:
            return True
        if isinstance(node, ast.Name) and node.id in _SYSTEM_NAMES:
            return True
        if isinstance(node, ast.alias) and node.name in _SYSTEM_NAMES:
            return True
    return False


class TestTheSystemContextStaysNarrow:
    def _users(self) -> set[str]:
        found = set()
        for path in ROOT.rglob("*.py"):
            rel = path.relative_to(ROOT).as_posix()
            if rel.startswith(("tests/", ".venv/")) or "/tests/" in rel:
                continue
            if _uses_system_context(path):
                found.add(rel)
        return found

    def test_only_the_engine_reaches_for_it(self):
        assert self._users() <= SYSTEM_CONTEXT_ALLOWED

    def test_the_scan_finds_the_known_users(self):
        """Counter-probe: a scan that found nothing would pass the test above."""
        assert {"core/db.py", "cli/retention.py", "api/v1/workflows.py"} <= self._users()


class TestActingAs:
    def _pool(self):
        conn = MagicMock()
        conn.execute = AsyncMock()

        class _CM:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *exc):
                return False

        pool = MagicMock()
        pool.acquire = MagicMock(return_value=_CM())
        return pool, conn

    async def test_it_says_for_whom_and_clears_it(self):
        pool, conn = self._pool()
        actor = uuid4()
        async with acting.acting_as(pool, actor):
            pass
        calls = [c.args for c in conn.execute.call_args_list]
        assert calls[0] == ("SELECT set_config($1, $2, false)", acting.ACTOR_SETTING, str(actor))
        assert calls[-1] == ("SELECT set_config($1, '', false)", acting.ACTOR_SETTING)

    async def test_it_clears_it_when_the_work_fails(self):
        pool, conn = self._pool()
        with pytest.raises(RuntimeError):
            async with acting.acting_as(pool, uuid4()):
                raise RuntimeError("boom")
        assert conn.execute.call_args_list[-1].args == (
            "SELECT set_config($1, '', false)", acting.ACTOR_SETTING)


class TestMe:
    def test_it_names_the_actor_id_and_the_memberships(self):
        scope = uuid4()
        pool = make_pool(fetch=[{"scope_id": scope, "role": "reviewer"}])
        with client_for(pool) as client:
            resp = client.get("/v1/me")
        assert resp.status_code == HTTPStatus.OK
        body = resp.json()
        assert body["actor_id"] == str(DEFAULT_ACTOR_ID)
        assert body["role"] == "admin"
        assert body["memberships"] == [{"scope_id": str(scope), "role": "reviewer"}]

    def test_it_is_on_the_author_surface(self):
        assert "/v1/me" in SURFACE_PATHS["author"]
