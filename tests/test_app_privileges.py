"""The application's database role, without a database (CHY-85, ADR-009).

Three things are held together here, and the integration suite holds the same
things against a real catalog (tests/integration/test_app_role_db.py):

  - the migrations grant exactly what kernel/retention.py lists, table by table —
    033 and every migration after it, read in order like the runner applies them
  - the check at start refuses each excess right on its own, and lets a clean
    role through — a check that refused everything would pass every refusal
  - each place that connects with the application's URL runs the check: the
    API pool, the purge, `cc actor set-role`

And one rule that keeps it so: no application code runs DDL. The pilots used to
create the LangGraph tables on every run; migrations/runner.py does it now.
"""
from __future__ import annotations

import ast
import inspect
import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kernel import least_privilege
from kernel.retention import EXTERNAL_TABLE_ACCESS, INVENTORY
from migrations import roles

ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS = ROOT / "migrations"

#: The three statements the migrations use on the application's grants, in the
#: order they appear. Anything else that names cohereny_app fails the parser test.
_STATEMENT = re.compile(
    r"^(?P<reset>REVOKE ALL ON ALL TABLES IN SCHEMA public FROM cohereny_app;)"
    r"|^(?P<verb>GRANT|REVOKE)\s+(?P<privileges>[A-Z, ]+?)\s+ON\s+(?P<table>\w+)\s+"
    r"(?:TO|FROM)\s+cohereny_app;",
    re.M,
)

_CLEAN = {"role": "cohereny_runtime", "superuser": False, "bypass_rls": False,
          "owns_objects": False, "can_create": False, "can_temp": False}


# ── the migrations are the inventory ──────────────────────────────────────────

def _migrations() -> list[Path]:
    return sorted(MIGRATIONS.glob("[0-9]*.sql"), key=lambda p: int(p.name.split("_")[0]))


def _granted() -> dict[str, set[str]]:
    """The grants the migrations leave behind, replayed in order."""
    grants: dict[str, set[str]] = {}
    for path in _migrations():
        for m in _STATEMENT.finditer(path.read_text(encoding="utf-8")):
            if m["reset"]:
                grants.clear()
                continue
            privileges = {p.strip() for p in m["privileges"].split(",")}
            held = grants.setdefault(m["table"], set())
            if m["verb"] == "GRANT":
                held |= privileges
            else:
                held -= privileges
    return {t: p for t, p in grants.items() if p}


class TestTheMigrationsAreTheInventory:
    def test_the_parser_finds_the_grants(self):
        """Counter-probe: a parser that finds nothing would make the next test
        compare two empty sets."""
        assert len(_granted()) >= 20

    def test_the_parser_understands_every_statement_on_the_role(self):
        """A GRANT written another way would be skipped, and the table would
        look ungranted — or, worse, a REVOKE would be skipped and it would look
        granted."""
        for path in _migrations():
            text = path.read_text(encoding="utf-8")
            named = [ln for ln in text.splitlines()
                     if "cohereny_app" in ln and not ln.lstrip().startswith("--")
                     and re.match(r"\s*(GRANT|REVOKE)\b", ln)]
            understood = {m.group(0).splitlines()[0] for m in _STATEMENT.finditer(text)}
            for line in named:
                if "ON SCHEMA public" in line:
                    continue  # the schema, not a table
                assert line.strip() in {u.strip() for u in understood}, f"{path.name}: {line}"

    @pytest.mark.parametrize("table", sorted(INVENTORY))
    def test_each_table_is_granted_what_the_inventory_says(self, table):
        expected = set(INVENTORY[table].access)
        assert _granted().get(table, set()) == expected

    def test_nothing_is_granted_that_the_inventory_does_not_know(self):
        assert set(_granted()) <= set(INVENTORY)

    def test_only_the_four_row_privileges_exist(self):
        """No TRUNCATE, REFERENCES or TRIGGER: TRUNCATE skips row triggers, and
        TRIGGER would let the application add its own."""
        used = set().union(*(set(e.access) for e in INVENTORY.values()), EXTERNAL_TABLE_ACCESS)
        assert used <= {"SELECT", "INSERT", "UPDATE", "DELETE"}

    def test_append_only_tables_are_never_updated_or_deleted(self):
        """The tables whose triggers refuse UPDATE and DELETE get neither grant:
        two locks, so a trigger that someone drops still leaves one."""
        for table in ("audit_log", "benchmark_measurements"):
            assert not {"UPDATE", "DELETE"} & set(INVENTORY[table].access), table
        assert "DELETE" not in INVENTORY["context_blocks"].access


# ── the check at start ────────────────────────────────────────────────────────

class TestTheCheck:
    def test_a_clean_role_passes(self):
        assert least_privilege.problems(_CLEAN) == []

    @pytest.mark.parametrize(("fact", "words"), [
        ("superuser", "superuser"),
        ("bypass_rls", "BYPASSRLS"),
        ("owns_objects", "owns tables"),
        ("can_create", "CREATE"),
        ("can_temp", "temporary"),
    ])
    def test_each_excess_right_is_named(self, fact, words):
        found = least_privilege.problems({**_CLEAN, fact: True})
        assert len(found) == 1
        assert words in found[0]

    async def test_check_raises_with_the_role_and_the_way_out(self):
        conn = AsyncMock()
        conn.fetchrow.return_value = {**_CLEAN, "role": "cohereny", "superuser": True}
        with pytest.raises(least_privilege.ExcessPrivilegeError) as raised:
            await least_privilege.check(conn)
        assert "'cohereny'" in str(raised.value)
        assert "migrations.roles" in str(raised.value)


# ── every place that connects as the application runs it ──────────────────────

def _superuser_conn() -> AsyncMock:
    conn = AsyncMock()
    conn.fetchrow.return_value = {**_CLEAN, "role": "cohereny", "superuser": True}
    return conn


class TestEveryEntryChecks:
    async def test_the_api_refuses_to_start_and_closes_its_pool(self):
        from core import db

        conn = _superuser_conn()
        acquired = MagicMock()
        acquired.__aenter__ = AsyncMock(return_value=conn)
        acquired.__aexit__ = AsyncMock(return_value=False)
        pool = MagicMock()
        pool.acquire.return_value = acquired
        pool.close = AsyncMock()
        with patch.object(db, "_pool", None), \
             patch.object(db.asyncpg, "create_pool", AsyncMock(return_value=pool)):
            with pytest.raises(least_privilege.ExcessPrivilegeError):
                await db.create_pool()
            pool.close.assert_awaited_once()
            assert db._pool is None, "a refused pool must not be handed to the routes"

    async def test_the_purge_refuses_and_touches_nothing(self):
        from cli import retention as purge_module

        conn = _superuser_conn()
        with patch.object(purge_module.asyncpg, "connect", AsyncMock(return_value=conn)), \
             pytest.raises(least_privilege.ExcessPrivilegeError):
            await purge_module.purge("postgresql://x/y", dry_run=False)
        conn.transaction.assert_not_called()
        conn.close.assert_awaited_once()

    async def test_set_role_refuses_and_writes_nothing(self):
        from kernel import identity

        conn = _superuser_conn()
        with patch.object(identity.asyncpg, "connect", AsyncMock(return_value=conn)), \
             pytest.raises(least_privilege.ExcessPrivilegeError):
            await identity.set_role("postgresql://x/y", "sub", "admin")
        conn.fetchrow.assert_awaited_once()  # the facts, and nothing after them
        conn.close.assert_awaited_once()


# ── the roles script ──────────────────────────────────────────────────────────

class TestTheRolesScript:
    def test_names_and_variables_follow_the_database(self):
        assert roles.role_names("cohereny_dev") == ("cohereny_dev_migrator", "cohereny_dev_runtime")
        assert roles.password_variables("cohereny_dev") == (
            "COHERENY_DEV_MIGRATOR_PASSWORD", "COHERENY_DEV_RUNTIME_PASSWORD")

    @pytest.mark.parametrize("name", ["Cohereny", "cohereny-dev", "x; DROP ROLE y", "", "1abc"])
    async def test_an_unexpected_database_name_is_refused_before_connecting(self, name):
        with patch.object(roles.asyncpg, "connect", AsyncMock()) as connect, \
             pytest.raises(ValueError, match="database name"):
            await roles.provision("postgresql://x/y", name, "m", "a")
        connect.assert_not_called()

    def test_every_kind_the_catalog_names_has_a_template(self):
        """The ownership query names a kind; a kind without a template would be
        a KeyError in the middle of a transition."""
        source = inspect.getsource(roles._transfer_ownership)
        kinds = set(re.findall(r"(?:THEN|ELSE|SELECT) '([A-Z ]+)'", source))
        assert kinds, "the parser found no kinds"
        assert kinds <= set(roles._OWNER_TO)

    def test_no_password_reaches_the_command_line(self):
        """Passwords come from the environment, never from arguments `ps` shows."""
        parser_source = inspect.getsource(roles.main)
        assert "password" not in "".join(re.findall(r"add_argument\(([^)]*)\)", parser_source)).lower()


# ── no application code runs DDL ──────────────────────────────────────────────

class TestNoDdlAtRunTime:
    @pytest.mark.parametrize("path", [
        "pilots/study/study/graph.py",
        "pilots/banking/banking/graph.py",
    ])
    def test_the_pilots_do_not_create_their_tables(self, path):
        tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
        calls = [n for n in ast.walk(tree)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                 and n.func.attr == "setup"]
        assert not calls, f"{path} calls setup() — DDL belongs to migrations/runner.py"

    def test_the_runner_creates_them_instead(self):
        source = (ROOT / "migrations" / "runner.py").read_text(encoding="utf-8")
        assert "checkpointer.setup()" in source
