"""
tests/test_block_versioning.py

Unit tests for ContextBlock versioning (CHY-30).

PATCH now branches on the state of the current version: a draft is edited in
place, anything else is superseded into a new version via cc_supersede_block.
The tests are weighted towards the branch point and towards what a supersede
carries over — silently dropping a field the caller did not mention would be
data loss dressed as an edit.

The database side (the trigger, the function, the unique index) is covered by
tests/test_supersede_block.py and was verified against real PostgreSQL. Here the
pool is mocked.
"""
import json
from datetime import datetime, UTC
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from api.deps import CurrentUser, require_auth
from api.main import app
from core.db import get_pool
from core.redis import get_redis
from tests.conftest import DEFAULT_ACTOR_ID, actor_row, make_pool, make_redis

_stub_user = CurrentUser(sub="test-sub", username="tester")
_BLOCK_ID = uuid4()
_SCOPE_ID = uuid4()
_AUTHOR_ID = uuid4()
_CREATED = datetime(2026, 8, 12, 10, 0, tzinfo=UTC)


def _row(**overrides) -> dict:
    return {
        "id": uuid4(),
        "block_id": _BLOCK_ID,
        "version": 1,
        "superseded_by_version": None,
        "scope_id": _SCOPE_ID,
        "block_type": "system_prompt",
        "content": "original",
        "metadata": {"k": "v"},
        "tags": ["a", "b"],
        "author_id": _AUTHOR_ID,
        "status": "draft",
        # Selected by every query that feeds a BlockOut since CHY-62. A fixture
        # that omits them would make the API look like it drops governance claims —
        # or hide that it does.
        "governance_key": None,
        "privacy_impact": None,
        "locked": False,
        "created_at": _CREATED,
        **overrides,
    }


def _make_pool(current=None, returned=None, fetch_rows=None, role="admin") -> MagicMock:
    """Thin wrapper over the shared double.

    PATCH resolves an actor first (CHY-33). The double answers that lookup
    separately, so `current` and `returned` stay the block reads they were.
    `current` is read on the pool, `returned` answers the write — inside a
    transaction since ADR-042, where a direct scope activates what was written.
    The scope lookup there gets the double's default and counts as strict.
    """
    return make_pool(
        actor=actor_row(role=role),
        fetchrow=[current],
        conn_fetchrow=[returned if returned is not None else current],
        fetch=fetch_rows if fetch_rows is not None else [],
    )


def _write(pool):
    """The statement that wrote the block — edit or supersede."""
    return pool.conn.fetchrow.call_args_list[0]


def _client(pool) -> TestClient:
    app.dependency_overrides[require_auth] = lambda: _stub_user
    app.dependency_overrides[get_pool] = lambda: pool
    app.dependency_overrides[get_redis] = make_redis
    return TestClient(app)


def _cleanup():
    for dep in (require_auth, get_pool, get_redis):
        app.dependency_overrides.pop(dep, None)


# ── The branch point ──────────────────────────────────────────────────────────

class TestPatchBranches:
    def test_draft_is_edited_in_place(self):
        """200, same version — a draft was never published."""
        pool = _make_pool(current=_row(), returned=_row(content="edited"))
        client = _client(pool)
        try:
            resp = client.patch(f"/v1/blocks/{_BLOCK_ID}", json={"content": "edited"})
            assert resp.status_code == 200
            assert resp.json()["version"] == 1
            sql = _write(pool).args[0]
            assert "UPDATE context_blocks" in sql
            assert "cc_supersede_block" not in sql
        finally:
            _cleanup()

    @pytest.mark.parametrize("state", ["active", "deprecated"])
    def test_published_block_is_superseded(self, state):
        """201 and a new version number — the change is not live yet."""
        pool = _make_pool(
            current=_row(status=state),
            returned=_row(version=2, content="new", status="draft"),
        )
        client = _client(pool)
        try:
            resp = client.patch(f"/v1/blocks/{_BLOCK_ID}", json={"content": "new"})
            assert resp.status_code == 201
            assert resp.json()["version"] == 2
            assert resp.json()["status"] == "draft"
            assert "cc_supersede_block" in _write(pool).args[0]
        finally:
            _cleanup()

    def test_missing_block_is_404(self):
        pool = _make_pool(current=None)
        client = _client(pool)
        try:
            assert client.patch(f"/v1/blocks/{uuid4()}", json={"content": "x"}).status_code == 404
        finally:
            _cleanup()

    def test_empty_patch_on_draft_returns_current_unchanged(self):
        pool = _make_pool(current=_row())
        client = _client(pool)
        try:
            resp = client.patch(f"/v1/blocks/{_BLOCK_ID}", json={})
            assert resp.status_code == 200
            assert resp.json()["content"] == "original"
        finally:
            _cleanup()


# ── What a supersede carries over ─────────────────────────────────────────────

class TestSupersedeCarriesOver:
    def _call(self, body, current_overrides=None):
        current = _row(status="active", **(current_overrides or {}))
        pool = _make_pool(current=current, returned=_row(version=2))
        client = _client(pool)
        try:
            client.patch(f"/v1/blocks/{_BLOCK_ID}", json=body)
            return _write(pool).args
        finally:
            _cleanup()

    def test_unmentioned_content_is_kept(self):
        args = self._call({"tags": ["only-tags"]})
        assert args[2] == "original"

    def test_unmentioned_tags_are_kept(self):
        """Dropping them would be data loss dressed as an edit."""
        args = self._call({"content": "new"})
        assert args[4] == ["a", "b"]

    def test_unmentioned_metadata_is_kept(self):
        args = self._call({"content": "new"})
        assert json.loads(args[3]) == {"k": "v"}

    def test_metadata_arriving_as_json_string_is_parsed(self):
        """asyncpg returns JSONB as str on some versions."""
        args = self._call({"content": "new"}, {"metadata": json.dumps({"k": "v"})})
        assert json.loads(args[3]) == {"k": "v"}

    def test_explicit_values_win(self):
        args = self._call({"content": "new", "tags": ["x"], "metadata": {"m": 2}})
        assert args[2] == "new"
        assert args[4] == ["x"]
        assert json.loads(args[3]) == {"m": 2}

    def test_change_reason_is_passed_through(self):
        """It ends up in the audit entry cc_supersede_block writes."""
        args = self._call({"content": "new", "change_reason": "Tippfehler"})
        assert args[7] == "Tippfehler"

    def test_author_and_editor_are_separate_arguments(self):
        """Passing one value for both attributed every foreign edit to the
        previous author, in a table that cannot be corrected (migration 013)."""
        args = self._call({"content": "new"})
        assert args[5] == _AUTHOR_ID          # p_author_ref — owns the line
        assert args[6] == DEFAULT_ACTOR_ID    # p_editor_ref — made this change

    def test_author_is_carried_over(self):
        args = self._call({"content": "new"})
        assert args[5] == _AUTHOR_ID


class TestImmutableAcrossVersions:
    def test_changing_block_type_is_refused(self):
        """block_type is an identity column in migration 012. Refusing here
        gives a clear message instead of a new version that quietly kept the
        old type."""
        pool = _make_pool(current=_row(status="active"))
        client = _client(pool)
        try:
            resp = client.patch(
                f"/v1/blocks/{_BLOCK_ID}", json={"block_type": "tool_schema"}
            )
            assert resp.status_code == 409
            assert "immutable across versions" in resp.json()["detail"]
        finally:
            _cleanup()

    def test_resending_the_same_block_type_is_fine(self):
        """A client echoing the current value back is not asking for a change."""
        pool = _make_pool(current=_row(status="active"), returned=_row(version=2))
        client = _client(pool)
        try:
            resp = client.patch(
                f"/v1/blocks/{_BLOCK_ID}",
                json={"block_type": "system_prompt", "content": "new"},
            )
            assert resp.status_code == 201
        finally:
            _cleanup()


# ── History ───────────────────────────────────────────────────────────────────

class TestHistory:
    def _versions(self):
        return [
            {"id": uuid4(), "version": 3, "superseded_by_version": None,
             "status": "draft", "author_id": _AUTHOR_ID, "created_at": _CREATED,
             "governance_key": None, "privacy_impact": None, "locked": False},
            {"id": uuid4(), "version": 2, "superseded_by_version": 3,
             "status": "active", "author_id": _AUTHOR_ID, "created_at": _CREATED},
            {"id": uuid4(), "version": 1, "superseded_by_version": 2,
             "status": "active", "author_id": None, "created_at": _CREATED},
        ]

    def test_returns_all_versions_newest_first(self):
        pool = _make_pool(fetch_rows=self._versions())
        client = _client(pool)
        try:
            resp = client.get(f"/v1/blocks/{_BLOCK_ID}/history")
            assert resp.status_code == 200
            body = resp.json()
            assert body["total"] == 3
            assert [v["version"] for v in body["versions"]] == [3, 2, 1]
        finally:
            _cleanup()

    def test_exactly_one_version_is_current(self):
        pool = _make_pool(fetch_rows=self._versions())
        client = _client(pool)
        try:
            versions = client.get(f"/v1/blocks/{_BLOCK_ID}/history").json()["versions"]
            assert [v["is_current"] for v in versions] == [True, False, False]
        finally:
            _cleanup()

    def test_history_omits_content(self):
        """A timeline, not ten copies of the text."""
        pool = _make_pool(fetch_rows=self._versions())
        client = _client(pool)
        try:
            v = client.get(f"/v1/blocks/{_BLOCK_ID}/history").json()["versions"][0]
            assert "content" not in v
        finally:
            _cleanup()

    def test_unknown_block_is_404(self):
        pool = _make_pool(fetch_rows=[])
        client = _client(pool)
        try:
            assert client.get(f"/v1/blocks/{uuid4()}/history").status_code == 404
        finally:
            _cleanup()

    def test_requires_auth(self):
        resp = TestClient(app).get(f"/v1/blocks/{_BLOCK_ID}/history")
        assert resp.status_code in (401, 403)

    def test_history_route_is_not_shadowed_by_the_block_route(self):
        """FastAPI matches in declaration order; /{block_id} declared first
        would swallow 'history' and reject it as an invalid UUID."""
        paths = app.openapi()["paths"]
        assert "/v1/blocks/{block_id}/history" in paths
