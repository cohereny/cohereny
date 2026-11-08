"""
tests/test_retention_purge.py

The parts of the purge that need no database (CHY-17, ADR-046).

What the purge does to rows is in tests/integration/test_retention_purge_db.py.
Here: that the SET clause comes from the inventory and covers every content
column, that the period of every scope is derived from the scope tree the
same way the API derives it for one scope, and that `cc retention purge`
passes `--dry-run` through.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from typer.testing import CliRunner

from cli.main import app
from kernel.retention import (
    CLEARED_VALUES,
    INVENTORY,
    RetentionClass,
    clear_assignments,
    effective_keep_days,
    periods_from,
)


class TestClearing:
    @pytest.mark.parametrize("table", [t for t, e in INVENTORY.items() if e.content_columns])
    def test_the_set_clause_names_every_content_column_once(self, table):
        clause = clear_assignments(table)
        for column in INVENTORY[table].content_columns:
            assert clause.count(f"{column} = ") == 1, column

    def test_a_not_null_column_gets_its_cleared_value_not_null(self):
        assert "input_data = '{}'::jsonb" in clear_assignments("workflow_runs")
        assert "input_file = '[purged]'" in clear_assignments("pilot_runs")
        assert "output_data = NULL" in clear_assignments("node_executions")

    def test_every_cleared_value_belongs_to_a_content_column(self):
        """A value for a column the inventory does not call content would never be used."""
        for table, column in CLEARED_VALUES:
            assert column in INVENTORY[table].content_columns, (table, column)

    def test_a_table_without_content_has_nothing_to_clear(self):
        with pytest.raises(ValueError):
            clear_assignments("loop_counters")


_ORG, _TEAM, _PROJECT, _OTHER = uuid4(), uuid4(), uuid4(), uuid4()
_SCOPES = [
    {"id": _ORG, "scope_path": "org_a"},
    {"id": _TEAM, "scope_path": "org_a.team_b"},
    {"id": _PROJECT, "scope_path": "org_a.team_b.project_c"},
    {"id": _OTHER, "scope_path": "org_z"},
]


def _policy(scope_id, keep_days, retention_class="run_content"):
    return {"retention_class": retention_class, "scope_id": scope_id, "keep_days": keep_days}


class TestPeriodsForEveryScope:
    def test_each_scope_gets_the_nearest_declaration_on_its_path(self):
        policies = [_policy(None, 30), _policy(None, 183, "run_trace"),
                    _policy(_TEAM, 3650), _policy(_PROJECT, None, "run_trace")]
        periods = periods_from(policies, _SCOPES)
        assert periods.by_scope[_ORG]["run_content"] == 30
        assert periods.by_scope[_PROJECT]["run_content"] == 3650
        assert periods.by_scope[_PROJECT]["run_trace"] is None
        assert periods.by_scope[_OTHER] == {"run_content": 30, "run_trace": 183}
        assert periods.installation == {"run_content": 30, "run_trace": 183}

    def test_it_agrees_with_the_answer_for_one_scope(self):
        """The purge and GET /v1/admin/retention must not disagree about a scope."""
        policies = [_policy(None, 30), _policy(_ORG, 7), _policy(_TEAM, 90)]
        periods = periods_from(policies, _SCOPES)
        chain = [_ORG, _TEAM, _PROJECT]
        assert periods.by_scope[_PROJECT]["run_content"] == \
            effective_keep_days(policies, chain, "run_content")[0]

    def test_unlimited_scopes_are_left_out_of_the_arrays(self):
        policies = [_policy(None, 30), _policy(_OTHER, None)]
        scope_ids, keep_days = periods_from(policies, _SCOPES).finite(RetentionClass.RUN_CONTENT)
        assert _OTHER not in scope_ids
        assert len(scope_ids) == len(keep_days) == 3


class TestCommand:
    @pytest.mark.parametrize("args, dry_run", [([], False), (["--dry-run"], True)])
    def test_it_passes_dry_run_through_and_prints_the_counts(self, args, dry_run):
        fake = AsyncMock(return_value={"workflow_runs_cleared": 3})
        with patch("cli.retention.purge", fake):
            result = CliRunner().invoke(
                app, ["retention", "purge", *args, "--database-url", "postgresql://x/y"])
        assert result.exit_code == 0, result.output
        fake.assert_awaited_once_with("postgresql://x/y", dry_run=dry_run)
        assert "workflow_runs_cleared" in result.output and "3" in result.output


class TestOneTransaction:
    """The orchestration of the purge, with every context answering zero.

    In the shared integration database a purge always finds something left
    by another test, so "nothing expired" cannot be staged there — and a
    run that found nothing must still leave its audit entry: otherwise a
    quiet installation and a dead timer look the same.
    """

    def _connection(self):
        transaction = AsyncMock()
        conn = AsyncMock()
        conn.transaction = lambda: transaction
        return conn, transaction

    async def _purge(self, *, dry_run: bool):
        from cli import retention as purge_module

        conn, transaction = self._connection()
        zero = AsyncMock(return_value={"workflow_runs_cleared": 0})
        # The role check has its own test (tests/test_app_privileges.py); a
        # mocked connection would answer it with truthy mocks.
        with patch.object(purge_module.asyncpg, "connect", AsyncMock(return_value=conn)), \
             patch.object(purge_module.least_privilege, "check", AsyncMock()), \
             patch.object(purge_module.retention, "current_periods", AsyncMock()), \
             patch.object(purge_module.retention, "record_purge", AsyncMock()) as record, \
             patch.object(purge_module.orchestration, "purge_runs", zero), \
             patch.object(purge_module.orchestration, "workflow_scopes", AsyncMock(return_value={})), \
             patch.object(purge_module.pilots, "purge_runs", AsyncMock(return_value={})), \
             patch.object(purge_module.learning, "purge_feedback", AsyncMock(return_value={})):
            await purge_module.purge("postgresql://x/y", dry_run=dry_run)
        return record, transaction

    @pytest.mark.asyncio
    async def test_a_run_that_found_nothing_is_recorded(self):
        record, transaction = await self._purge(dry_run=False)
        record.assert_awaited_once()
        assert record.await_args.args[1] == {"workflow_runs_cleared": 0}
        transaction.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_a_dry_run_rolls_back_what_it_did(self):
        _, transaction = await self._purge(dry_run=True)
        transaction.rollback.assert_awaited_once()
        transaction.commit.assert_not_awaited()


class TestTheStoresClearWhatTheInventorySays:
    """The SET clauses are static SQL in each owner's store; this holds them to the inventory.

    One test per table: the owner's store.py must hold an UPDATE of that table
    whose SET clause is exactly clear_assignments(table), whitespace aside. A
    column added to the inventory makes it fail until the store clears it.
    """

    @staticmethod
    def _normalised(text: str) -> str:
        return " ".join(text.split()).replace(" ,", ",")

    @pytest.mark.parametrize("table", [t for t, e in INVENTORY.items() if e.content_columns])
    def test_the_owner_clears_exactly_the_content_columns(self, table):
        import ast
        from pathlib import Path

        from tests.test_kernel_scopes import _code_strings

        store = Path(__file__).resolve().parents[1] / "contexts" / INVENTORY[table].owner / "store.py"
        statements = [self._normalised(s) for s in _code_strings(ast.parse(store.read_text()))
                      if self._normalised(s).startswith(f"UPDATE {table} ")]
        clause = self._normalised(clear_assignments(table))
        assert any(f"SET {clause}" in s for s in statements), (
            f"no UPDATE {table} in {store.name} clears exactly: {clause}\n{statements}")
