"""
tests/test_retention.py

How long Cohereny keeps what (CHY-17, ADR-046).

The inventory is code and the periods are configuration. What must hold:

  - every table the migrations leave behind is in the inventory, exactly once,
    and every column called content exists in its table. A new table without
    an entry makes this file fail: otherwise it would escape every period
    without anybody deciding so
  - the classes that take a period are the same in Python and in the CHECK of
    migration 030
  - the period that applies in a scope is the nearest declaration on its
    chain, not the strictest
  - reading needs `reviewer`, changing needs `admin`, and every change is
    audited with the value before and after

The pool is a double. The table itself, its uniqueness with NULL scopes and
the defaults are in tests/integration/test_retention_db.py.
"""
from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import get_args
from uuid import uuid4

import pytest

from kernel.audit import ACTION_RETENTION_POLICY_CHANGED
from kernel.retention import (
    CONFIGURABLE,
    EXTERNAL_TABLES,
    INVENTORY,
    RULES,
    RetentionClass,
    effective_keep_days,
)
from kernel.schema import ConfigurableRetentionClass
from tests.conftest import actor_row, client_for, make_pool

_ROOT = Path(__file__).resolve().parents[1]
_MIGRATIONS = sorted((_ROOT / "migrations").glob("*.sql"))


def _tables_and_columns() -> dict[str, set[str]]:
    """What the migrations leave behind: table → columns, dropped ones removed."""
    columns: dict[str, set[str]] = {}
    for path in _MIGRATIONS:
        sql = path.read_text(encoding="utf-8")
        for match in re.finditer(r"CREATE TABLE (?:IF NOT EXISTS )?(\w+)\s*\((.*?)\n\);", sql, re.S):
            body = match.group(2)
            names = {
                line.strip().split()[0] for line in body.splitlines()
                if line.strip() and not line.strip().startswith(
                    ("--", "PRIMARY", "UNIQUE", "CONSTRAINT", "CHECK", "FOREIGN", ")"))
            }
            columns.setdefault(match.group(1), set()).update(names)
        for table, column in re.findall(
                r"ALTER TABLE (\w+)\s+ADD COLUMN (?:IF NOT EXISTS )?(\w+)", sql):
            columns.setdefault(table, set()).add(column)
        for table in re.findall(r"DROP TABLE (?:IF EXISTS )?(\w+)", sql):
            columns.pop(table, None)
    return columns


# ── the inventory ─────────────────────────────────────────────────────────────

class TestInventory:
    def test_every_table_of_the_migrations_has_an_entry(self):
        migrated = set(_tables_and_columns())
        listed = set(INVENTORY) - {"schema_migrations"}
        assert migrated - listed == set(), "tables without a retention class"
        assert listed - migrated == set(), "entries for tables that no longer exist"

    def test_the_runners_own_table_is_listed_because_the_runner_creates_it(self):
        runner = (_ROOT / "migrations" / "runner.py").read_text(encoding="utf-8")
        assert "CREATE TABLE IF NOT EXISTS schema_migrations" in runner
        assert "schema_migrations" in INVENTORY

    def test_every_content_column_exists_in_its_table(self):
        """A typo here would leave a column of content uncleared, silently."""
        columns = _tables_and_columns()
        for table, entry in INVENTORY.items():
            missing = set(entry.content_columns) - columns.get(table, set())
            assert not missing, f"{table}: no column {sorted(missing)}"

    def test_only_run_traces_carry_content_columns(self):
        """Content is cleared while the row stays as trace; anywhere else the
        pair would mean nothing."""
        for table, entry in INVENTORY.items():
            if entry.content_columns:
                assert entry.row_class is RetentionClass.RUN_TRACE, table

    def test_external_tables_are_not_also_migrated(self):
        assert not EXTERNAL_TABLES & set(_tables_and_columns())

    def test_every_class_has_a_rule_and_says_why_when_it_takes_no_period(self):
        assert set(RULES) == set(RetentionClass)
        for retention_class, rule in RULES.items():
            assert rule.configurable == (rule.reason is None), retention_class


class TestVocabulary:
    def test_the_configurable_classes_are_the_two_run_classes(self):
        assert {c.value for c in CONFIGURABLE} == {"run_content", "run_trace"}

    def test_python_and_the_check_of_migration_030_agree(self):
        sql = (_ROOT / "migrations" / "030_retention_policies.sql").read_text(encoding="utf-8")
        check = re.search(r"CHECK \(retention_class IN \(([^)]*)\)\)", sql)
        assert check, "the CHECK on retention_policies.retention_class moved"
        in_sql = set(re.findall(r"'(\w+)'", check.group(1)))
        assert in_sql == {c.value for c in CONFIGURABLE} == set(get_args(ConfigurableRetentionClass))


# ── which period applies ──────────────────────────────────────────────────────

_ORG, _TEAM, _PROJECT = uuid4(), uuid4(), uuid4()
_CHAIN = [_ORG, _TEAM, _PROJECT]


def _policy(scope_id, keep_days, retention_class="run_content"):
    return {"retention_class": retention_class, "scope_id": scope_id, "keep_days": keep_days}


class TestEffectivePeriod:
    def test_without_any_scope_row_the_installation_period_applies(self):
        assert effective_keep_days([_policy(None, 30)], _CHAIN, "run_content") == (30, None)

    def test_the_nearest_declaration_wins(self):
        policies = [_policy(None, 30), _policy(_ORG, 3650), _policy(_TEAM, 90)]
        assert effective_keep_days(policies, _CHAIN, "run_content") == (90, _TEAM)

    def test_a_shorter_period_above_does_not_override_a_longer_one_below(self):
        """Not the strictest: for retention neither direction is stricter."""
        policies = [_policy(None, 30), _policy(_ORG, 7), _policy(_PROJECT, 3650)]
        assert effective_keep_days(policies, _CHAIN, "run_content") == (3650, _PROJECT)

    def test_unlimited_below_is_a_declaration_too(self):
        policies = [_policy(None, 30), _policy(_PROJECT, None)]
        assert effective_keep_days(policies, _CHAIN, "run_content") == (None, _PROJECT)

    def test_rows_of_the_other_class_are_ignored(self):
        policies = [_policy(None, 30), _policy(_PROJECT, 5, "run_trace")]
        assert effective_keep_days(policies, _CHAIN, "run_content") == (30, None)


# ── the admin API ─────────────────────────────────────────────────────────────

_NOW = datetime(2026, 11, 5, 10, 0, tzinfo=UTC)


def _stored(retention_class, scope_id, keep_days):
    return {"retention_class": retention_class, "scope_id": scope_id, "keep_days": keep_days,
            "set_by": None, "set_at": _NOW}


_DEFAULTS = [_stored("run_content", None, 30), _stored("run_trace", None, 183)]


def _chain_rows():
    return [{"id": scope_id, "scope_path": "x", "node_type": "org", "name": "x",
             "depth": i + 1, "privacy_level": None} for i, scope_id in enumerate(_CHAIN)]


def _pool(role: str, *, stored=_DEFAULTS, conn_rows=None):
    def fetch(sql, *args):
        if "FROM retention_policies" in sql:
            return stored
        if "scope_path @>" in sql:
            return _chain_rows()
        return []

    pool = make_pool(actor=actor_row(role=role), fetch=fetch, conn_fetchrow=conn_rows)
    pool.conn.fetchval.return_value = uuid4()  # an audit entry's id
    return pool


def _audit(pool) -> list[tuple[str, dict]]:
    return [(c.args[2], json.loads(c.args[4]))
            for c in pool.conn.fetchval.call_args_list if "audit_log" in c.args[0]]


class TestReading:
    def test_a_reviewer_reads_every_class_with_its_tables(self):
        with client_for(_pool("reviewer")) as client:
            response = client.get("/v1/admin/retention")
        assert response.status_code == 200, response.text
        classes = {c["retention_class"]: c for c in response.json()["classes"]}
        assert set(classes) == {c.value for c in RetentionClass}
        content = classes["run_content"]
        assert content["configurable"] and content["action"] == "clear"
        assert {"table": "node_executions", "owner": "orchestration",
                "content_columns": ["input_data", "output_data", "error_message"]} in content["tables"]
        assert classes["audit"]["reason"] and not classes["audit"]["configurable"]
        assert response.json()["external_stores"], "the stores outside the tables are listed"

    def test_for_a_scope_it_says_which_period_applies_and_where_it_comes_from(self):
        stored = [*_DEFAULTS, _stored("run_content", _TEAM, 90)]
        with client_for(_pool("reviewer", stored=stored)) as client:
            response = client.get("/v1/admin/retention", params={"scope_id": str(_PROJECT)})
        classes = {c["retention_class"]: c for c in response.json()["classes"]}
        assert classes["run_content"]["effective_keep_days"] == 90
        assert classes["run_content"]["effective_from_scope"] == str(_TEAM)
        assert classes["run_trace"]["effective_keep_days"] == 183
        assert classes["run_trace"]["effective_from_scope"] is None

    def test_a_contributor_does_not_read_it(self):
        with client_for(_pool("contributor")) as client:
            assert client.get("/v1/admin/retention").status_code == 403


class TestChanging:
    def test_an_admin_sets_a_scope_period_and_the_change_is_audited(self):
        row = _stored("run_content", _TEAM, 90)
        pool = _pool("admin", conn_rows=[None, row])
        with client_for(pool) as client:
            response = client.put("/v1/admin/retention/run_content",
                                  json={"scope_id": str(_TEAM), "keep_days": 90})
        assert response.status_code == 200, response.text
        assert response.json()["keep_days"] == 90
        (action, payload), = _audit(pool)
        assert action == ACTION_RETENTION_POLICY_CHANGED
        assert payload == {"retention_class": "run_content", "scope_id": str(_TEAM),
                           "from_keep_days": None, "had_policy": False, "to_keep_days": 90}

    def test_changing_the_installation_period_records_the_old_value(self):
        pool = _pool("admin", conn_rows=[{"keep_days": 30}, _stored("run_content", None, 60)])
        with client_for(pool) as client:
            response = client.put("/v1/admin/retention/run_content", json={"keep_days": 60})
        assert response.status_code == 200, response.text
        (_, payload), = _audit(pool)
        assert (payload["from_keep_days"], payload["to_keep_days"]) == (30, 60)
        assert payload["scope_id"] is None

    def test_unlimited_is_said_explicitly(self):
        pool = _pool("admin", conn_rows=[{"keep_days": 183}, _stored("run_trace", None, None)])
        with client_for(pool) as client:
            response = client.put("/v1/admin/retention/run_trace", json={"keep_days": None})
        assert response.status_code == 200, response.text
        assert response.json()["keep_days"] is None

    @pytest.mark.parametrize("body", [{}, {"keep_days": 0}, {"keep_days": -5},
                                      {"keep_days": 36_501}, {"keep_days": 1_204_274_781_580}])
    def test_a_missing_or_impossible_period_is_refused(self, body):
        """Leaving keep_days out must not mean "unlimited" by accident."""
        pool = _pool("admin")
        with client_for(pool) as client:
            assert client.put("/v1/admin/retention/run_content", json=body).status_code == 422
        assert _audit(pool) == []

    def test_a_class_without_a_period_cannot_be_given_one(self):
        with client_for(_pool("admin")) as client:
            response = client.put("/v1/admin/retention/audit", json={"keep_days": 365})
        assert response.status_code == 422

    def test_a_reviewer_reads_but_does_not_change(self):
        pool = _pool("reviewer")
        with client_for(pool) as client:
            response = client.put("/v1/admin/retention/run_content", json={"keep_days": 1})
        assert response.status_code == 403
        assert _audit(pool) == []


class TestRemovingAnOverride:
    def test_an_admin_removes_a_scope_period_and_it_is_audited(self):
        pool = _pool("admin", conn_rows=[{"keep_days": 90}])
        with client_for(pool) as client:
            response = client.delete("/v1/admin/retention/run_content",
                                     params={"scope_id": str(_TEAM)})
        assert response.status_code == 204, response.text
        (_, payload), = _audit(pool)
        assert payload["removed"] is True and payload["from_keep_days"] == 90

    def test_removing_what_is_not_there_is_a_404(self):
        pool = _pool("admin", conn_rows=[None])
        with client_for(pool) as client:
            response = client.delete("/v1/admin/retention/run_content",
                                     params={"scope_id": str(_TEAM)})
        assert response.status_code == 404
        assert _audit(pool) == []

    def test_the_installation_period_cannot_be_removed(self):
        """scope_id is required: without the row a scope nobody configured
        would keep without limit by omission."""
        with client_for(_pool("admin")) as client:
            assert client.delete("/v1/admin/retention/run_content").status_code == 422


def test_a_century_is_the_longest_finite_period_and_is_accepted():
    """Counter-probe to the refusals above: the bound itself is a valid period."""
    from kernel.schema import MAX_KEEP_DAYS

    assert MAX_KEEP_DAYS == 36_500
    pool = _pool("admin", conn_rows=[None, _stored("run_trace", _TEAM, MAX_KEEP_DAYS)])
    with client_for(pool) as client:
        response = client.put("/v1/admin/retention/run_trace",
                              json={"scope_id": str(_TEAM), "keep_days": MAX_KEEP_DAYS})
    assert response.status_code == 200, response.text
