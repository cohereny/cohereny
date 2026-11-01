"""
tests/test_supersede_block.py

Guards for migration 012 — append-only on context_blocks (CHY-60, ADR-009).

The behaviour was verified against real PostgreSQL; a trigger that raises cannot
be exercised through a mock. What these tests protect is everything that could
be quietly undone later: the classification of every column, the two SQL bugs
this migration already hit once, and the action name shared between SQL and
Python.
"""
import re
from pathlib import Path

import pytest

from kernel.audit import ACTION_BLOCK_SUPERSEDED

_ROOT = Path(__file__).resolve().parents[1]
_MIGRATIONS = _ROOT / "migrations"
# Both the trigger function and cc_supersede_block were redefined by 013
# (CHY-32). Pinned to 012, every assertion below would keep passing against a
# function that is no longer the one installed — the tests would be green and
# meaningless.
_SQL_012 = (_MIGRATIONS / "012_supersede_block.sql").read_text(encoding="utf-8")


def _latest_definition_of(function: str) -> str:
    """The migration that defines this function *last*, and therefore installs it.

    This used to be a hardcoded filename, and the comment above it already
    named the danger: pinned to 012, every assertion here kept passing against
    a function 013 had replaced. Repinning to 013 only moved the trap one
    migration along — 016 redefines it again (CHY-62), and a test pinned to 013
    would have gone on guarding SQL that is not running anywhere.

    Computed rather than maintained, because a guard whose staleness is
    invisible is worse than no guard: it reports success without looking.
    """
    matches = [
        path for path in sorted(_MIGRATIONS.glob("[0-9]*.sql"))
        if f"CREATE OR REPLACE FUNCTION {function}" in path.read_text(encoding="utf-8")
    ]
    assert matches, f"no migration defines {function}"
    return matches[-1].read_text(encoding="utf-8")


_SQL = _latest_definition_of("cc_context_blocks_append_only")


# ── What the migration must contain ───────────────────────────────────────────

class TestMigrationShape:
    def test_drops_both_legacy_rules(self):
        """002 dropped only no_update and left no_delete behind, silently
        discarding deletes. Both go. Still asserted against 012, which is where
        the drop happened."""
        assert "DROP RULE IF EXISTS no_update_context_blocks" in _SQL_012
        assert "DROP RULE IF EXISTS no_delete_context_blocks" in _SQL_012

    @pytest.mark.parametrize("op", ["UPDATE", "DELETE", "TRUNCATE"])
    def test_all_three_operations_are_covered(self, op):
        """The triggers themselves were created in 012 and not touched by 013."""
        assert f"BEFORE {op} ON context_blocks" in _SQL_012

    def test_update_trigger_is_row_level(self):
        """It compares OLD and NEW column by column, so it needs the row."""
        update_stmt = _SQL_012.split("BEFORE UPDATE ON context_blocks")[1].split(";")[0]
        assert "FOR EACH ROW" in update_stmt

    def test_function_is_security_definer_with_pinned_search_path(self):
        """SECURITY DEFINER without a fixed search_path is a known escalation
        route — a caller could shadow a referenced object."""
        assert "SECURITY DEFINER" in _SQL
        assert "SET search_path = pg_catalog, public" in _SQL


# ── The two bugs this migration already hit ───────────────────────────────────

class TestRegressions:
    def test_flag_check_coalesces(self):
        """current_setting(..., true) is NULL when unset, and NULL = 'on' is
        NULL — not false. Without COALESCE the guard skips its own RAISE and a
        hand-written UPDATE on superseded_by_version succeeds on a fresh
        session. Verified against cohereny_dev before the fix."""
        assert "COALESCE(current_setting('cc.superseding', true), 'off') = 'on'" in _SQL

    def test_old_version_is_retired_before_the_new_one_is_inserted(self):
        """idx_blocks_one_active is a partial unique index on block_id WHERE
        superseded_by_version IS NULL, and unique indexes are checked per
        statement. Inserting first leaves two current rows and fails."""
        body = _SQL.split("CREATE OR REPLACE FUNCTION cc_supersede_block")[1]
        retire = body.index("SET superseded_by_version = v_current.version + 1")
        insert = body.index("INSERT INTO context_blocks (")
        assert retire < insert, "the UPDATE must precede the INSERT"

    def test_supersede_locks_the_current_row(self):
        body = _SQL.split("CREATE OR REPLACE FUNCTION cc_supersede_block")[1]
        assert "FOR UPDATE" in body.split("IF NOT FOUND")[0]


# ── Every column must be accounted for ────────────────────────────────────────

class TestColumnCoverage:
    """A column added to context_blocks later must not slip past the trigger.

    The guard names identity and content columns explicitly; anything it does
    not name is implicitly editable forever. This test fails when a migration
    adds a column without deciding which class it belongs to.
    """

    # Deliberately maintained by hand: adding a column here is the moment to
    # decide whether it is identity, content or lifecycle.
    IDENTITY = {"block_id", "version", "scope_id", "block_type", "author_id", "created_at"}
    CONTENT = {"content", "metadata", "tags"}
    LIFECYCLE = {"status", "superseded_by_version"}
    TECHNICAL = {"id"}  # surrogate primary key, never written after INSERT
    # Governance behaves exactly like content: settable while draft, frozen the
    # moment the block leaves it (migration 016, CHY-62). Kept as its own set
    # rather than folded into CONTENT so the trigger assertion below states
    # which rule each column follows and why.
    GOVERNANCE = {"governance_key", "privacy_impact", "locked"}

    def _declared_columns(self) -> set[str]:
        create = re.search(
            r"CREATE TABLE context_blocks \((.*?)\n\);",
            (_MIGRATIONS / "001_initial.sql").read_text(encoding="utf-8"),
            re.S,
        )
        assert create, "CREATE TABLE context_blocks not found in 001_initial"
        cols = {
            m.group(1)
            for line in create.group(1).splitlines()
            if (m := re.match(r"\s{4}([a-z_]+)\s+\S", line))
        }
        for path in sorted(_MIGRATIONS.glob("*.sql")):
            sql = path.read_text(encoding="utf-8")
            for block in re.findall(r"ALTER TABLE context_blocks(.*?);", sql, re.S):
                cols |= set(re.findall(r"ADD COLUMN(?:\s+IF NOT EXISTS)?\s+([a-z_]+)", block))
        return cols

    def test_every_column_is_classified(self):
        known = (self.IDENTITY | self.CONTENT | self.LIFECYCLE
                 | self.TECHNICAL | self.GOVERNANCE)
        unclassified = self._declared_columns() - known
        assert not unclassified, (
            f"context_blocks columns not classified in the append-only guard: "
            f"{sorted(unclassified)}. Decide whether each is identity, content "
            f"or lifecycle, add it to the trigger in migration 012, and list it here."
        )

    def test_identity_columns_are_all_guarded(self):
        guard = _SQL.split("Identity is immutable")[1].split("END IF")[0]
        missing = {c for c in self.IDENTITY if f"OLD.{c}" not in guard}
        assert not missing, f"identity columns missing from the trigger: {sorted(missing)}"

    @staticmethod
    def _freeze_clause() -> str:
        """The part of the trigger that freezes a row once it leaves draft.

        Anchored on the condition, not on the comment above it. The comment
        changed in 016 ("Content" became "Content and governance") and a split
        on prose would have returned the whole file — which contains every
        column name and would have made the assertion below vacuously true.
        """
        marker = "IF OLD.status <> 'draft'"
        assert marker in _SQL, "freeze condition not found in the trigger"
        return _SQL.split(marker)[1].split("END IF")[0]

    def test_content_columns_are_all_guarded(self):
        guard = self._freeze_clause()
        missing = {c for c in self.CONTENT if f"OLD.{c}" not in guard}
        assert not missing, f"content columns missing from the trigger: {sorted(missing)}"

    def test_governance_columns_are_all_guarded(self):
        """A locked block that can be unlocked by a plain UPDATE is not locked.

        This is the back door the ratchet would otherwise leave open: the block
        reaches `active` with locked = true, and one UPDATE later it is false
        (CHY-62).
        """
        guard = self._freeze_clause()
        missing = {c for c in self.GOVERNANCE if f"OLD.{c}" not in guard}
        assert not missing, f"governance columns missing from the trigger: {sorted(missing)}"

    def test_superseding_carries_governance_over(self):
        """The other back door: cc_supersede_block lists its INSERT columns, so
        a new column takes its default unless it is named. A successor with no
        claim would silently unlock a locked line — through the one function
        that is supposed to be the sanctioned path (CHY-62)."""
        sql = _latest_definition_of("cc_supersede_block")
        for column in sorted(self.GOVERNANCE):
            assert f"v_current.{column}" in sql, (
                f"cc_supersede_block does not carry {column} over; a superseded "
                f"block would lose its governance claim"
            )


# ── SQL and Python must agree ─────────────────────────────────────────────────

def test_audit_action_matches_the_python_constant():
    """cc_supersede_block writes the audit row itself, so the action name lives
    in SQL. Same drift guard as CHY-59 and CHY-34."""
    assert f"'{ACTION_BLOCK_SUPERSEDED}'" in _SQL


def test_supersede_writes_its_own_audit_entry():
    """The function is the only way to supersede, so an entry written inside it
    cannot be forgotten by a caller."""
    body = _SQL.split("CREATE OR REPLACE FUNCTION cc_supersede_block")[1]
    assert "INSERT INTO audit_log" in body


def test_migration_numbers_are_unique():
    numbers = [int(re.match(r"(\d+)_", p.name).group(1)) for p in _MIGRATIONS.glob("*.sql")]
    assert len(numbers) == len(set(numbers)), f"duplicate migration number in {sorted(numbers)}"
