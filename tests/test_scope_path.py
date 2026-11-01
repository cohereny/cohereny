"""Tests for the ltree scope path convention (CHY-59, ADR-008).

The convention is `{node_type}_{uuid}` per label, hyphens replaced by
underscores, dots between levels:

    org_acme.team_it.project_study

It exists twice by necessity -- as `scope_label()` in Python for the API and as
`cc_scope_label()` in SQL for migrations and fixtures. Two implementations of
one rule drift, and a drifted scope path fails silently: the lookup returns no
rows rather than an error. The last test here reads both and compares them.

All tests run offline. Nothing here needs PostgreSQL.
"""

import re
from datetime import datetime, UTC
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from kernel.schema import ScopeNode, scope_label

REPO_ROOT = Path(__file__).resolve().parent.parent
MIGRATION = REPO_ROOT / "migrations" / "009_scope_blocks.sql"


def _node(path: str) -> ScopeNode:
    return ScopeNode(
        id=uuid4(),
        scope_path=path,
        node_type="team",
        name="whatever",
        created_at=datetime.now(UTC),
    )


class TestScopeLabel:
    def test_hyphens_become_underscores(self) -> None:
        uid = UUID("550e8400-e29b-41d4-a716-446655440000")
        assert scope_label("org", uid) == "org_550e8400_e29b_41d4_a716_446655440000"

    def test_result_is_a_single_label(self) -> None:
        """No dots -- a label is one level, never a path."""
        assert "." not in scope_label("project", uuid4())

    def test_accepts_string_ids(self) -> None:
        """Callers hold UUIDs as strings often enough to matter."""
        assert scope_label("team", "550e8400-e29b-41d4-a716-446655440000").startswith("team_550e8400")

    @pytest.mark.parametrize("node_type", ["org", "team", "workspace", "project", "sub_project", "personal"])
    def test_every_node_type_yields_a_valid_label(self, node_type: str) -> None:
        assert re.match(r"^[A-Za-z0-9_]+$", scope_label(node_type, uuid4()))


class TestScopePathValidation:
    @pytest.mark.parametrize(
        "path",
        ["org_acme", "org_acme.team_it", "org_acme.team_it.project_study.sub_x.personal_y"],
    )
    def test_accepts_valid_paths(self, path: str) -> None:
        assert _node(path).scope_path == path

    @pytest.mark.parametrize(
        "path,why",
        [
            ("org_acme.team-it", "hyphen — PostgreSQL 16 would accept this, our convention does not"),
            ("org:acme", "colon — the pre-migration TEXT[] spelling"),
            ("org_acme..team_it", "empty label between dots"),
            (".org_acme", "leading dot"),
            ("org_acme.", "trailing dot"),
            ("", "empty path"),
            ("org acme", "space"),
        ],
    )
    def test_rejects_invalid_paths(self, path: str, why: str) -> None:
        with pytest.raises(ValidationError):
            _node(path)

    def test_labels_splits_root_first(self) -> None:
        assert _node("org_acme.team_it.project_study").labels == [
            "org_acme", "team_it", "project_study",
        ]

    def test_depth_matches_nlevel(self) -> None:
        assert _node("org_acme").depth == 1
        assert _node("org_acme.team_it.project_study").depth == 3


class TestMigrationMatchesPython:
    """Guards the two implementations against drifting apart."""

    def test_migration_exists(self) -> None:
        assert MIGRATION.exists(), "migration 009 is missing"

    def test_sql_helper_uses_the_same_rule(self) -> None:
        """cc_scope_label must concatenate type, underscore, de-hyphenated uuid.

        Compares the SQL body rather than executing it -- keeping the suite
        offline. If the SQL is rewritten in a way this no longer recognises,
        the test fails and forces a look, which is the intent.
        """
        sql = MIGRATION.read_text(encoding="utf-8")
        assert "cc_scope_label" in sql, "SQL helper is gone — is the convention still shared?"
        assert "replace(node_id::TEXT, '-', '_')" in sql, (
            "cc_scope_label no longer replaces hyphens the way scope_label() does"
        )
        assert "node_type || '_' ||" in sql, (
            "cc_scope_label no longer joins type and id with an underscore"
        )

    def test_index_is_gist_not_gin(self) -> None:
        """GIN has no operator class for a plain ltree column.

        The ticket asked for GIN; using it here would fail at migration time.
        Pinned so a well-meaning change back to GIN is caught before deploy.
        """
        sql = MIGRATION.read_text(encoding="utf-8")
        assert "USING gist (scope_path)" in sql
        assert "USING gin (scope_path)" not in sql

    def test_extension_is_created_before_use(self) -> None:
        sql = MIGRATION.read_text(encoding="utf-8")
        assert sql.index("CREATE EXTENSION IF NOT EXISTS ltree") < sql.index("TYPE ltree")
