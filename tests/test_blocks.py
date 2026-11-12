"""
tests/test_blocks.py

Unit tests for ContextBlock CRUD endpoints (Issue #11).

DB is mocked via dependency override — no real PostgreSQL needed.
Tests cover: request validation, response schema, auth, error cases.

Mock pool returns predefined FakeRecord dicts for each scenario.
"""
import json
from datetime import datetime, UTC
from uuid import uuid4
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from api.main import app
from api.deps import require_auth, CurrentUser
from core.db import get_pool
from core.redis import get_redis
from tests.conftest import actor_row, as_caller, make_pool, make_redis

# --- Test fixtures ---

_stub_user = CurrentUser(sub="test-sub", username="testuser")

_BLOCK_ID = uuid4()
_SCOPE_ID = uuid4()
_ROW_ID = uuid4()
_NOW = datetime(2026, 7, 3, 10, 0, 0, tzinfo=UTC)

_SAMPLE_ROW = {
    "id": _ROW_ID,
    "block_id": _BLOCK_ID,
    "version": 1,
    "superseded_by_version": None,
    "scope_id": _SCOPE_ID,
    "block_type": "system_prompt",
    "content": "Du bist ein hilfreicher Assistent.",
    "metadata": {"lang": "de"},
    "tags": ["german", "assistant"],
    "author_id": None,
    "status": "draft",
    # Selected by every query that feeds a BlockOut since CHY-62. A fixture
    # that omits them would make the API look like it drops governance claims —
    # or hide that it does.
    "governance_key": None,
    "privacy_impact": None,
    "locked": False,
    "created_at": _NOW,
}


def _make_pool(
    fetch_return=None,
    fetchrow_return=None,
    fetchval_return=0,
    execute_return="UPDATE 1",
    role="admin",
    conn_fetchrow_return=None,
) -> MagicMock:
    """Thin wrapper over the shared double.

    The write routes resolve an actor before touching a block (CHY-33), so the
    pool has to answer two different kinds of query. `role` is what the caller
    is; the default is admin because these tests are about the block logic, not
    about the gate.

    `conn_fetchrow_return` answers the writes, which run in a transaction since
    ADR-042 — a direct scope activates what was written in the same one.
    """
    return make_pool(
        actor=actor_row(role=role),
        fetchrow=fetchrow_return,
        fetch=fetch_return or [],
        fetchval=fetchval_return,
        execute=execute_return,
        conn_fetchrow=conn_fetchrow_return,
    )


def _make_client(pool: MagicMock) -> TestClient:
    app.dependency_overrides[require_auth] = as_caller(_stub_user)
    app.dependency_overrides[get_pool] = lambda: pool
    # DELETE bumps the scope generation counter so the compiled-context cache
    # stops serving a block that was just retired (CHY-61, ADR-010).
    app.dependency_overrides[get_redis] = lambda: _make_redis()
    return TestClient(app)


def _make_redis() -> MagicMock:
    return make_redis()


def _cleanup():
    app.dependency_overrides.pop(require_auth, None)
    app.dependency_overrides.pop(get_pool, None)
    app.dependency_overrides.pop(get_redis, None)


# --- GET /v1/blocks ---

def test_list_blocks_empty():
    pool = _make_pool(fetch_return=[], fetchval_return=0)
    client = _make_client(pool)
    try:
        resp = client.get("/v1/blocks")
        assert resp.status_code == 200
        data = resp.json()
        assert data["items"] == []
        assert data["total"] == 0
        assert data["page"] == 1
    finally:
        _cleanup()


def test_list_blocks_returns_items():
    pool = _make_pool(fetch_return=[_SAMPLE_ROW], fetchval_return=1)
    client = _make_client(pool)
    try:
        resp = client.get("/v1/blocks")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) == 1
        assert data["items"][0]["block_type"] == "system_prompt"
        assert data["items"][0]["status"] == "draft"
    finally:
        _cleanup()


def test_list_blocks_requires_auth():
    pool = _make_pool()
    app.dependency_overrides[get_pool] = lambda: pool
    client = TestClient(app)
    try:
        resp = client.get("/v1/blocks")
        assert resp.status_code in (401, 403)
    finally:
        app.dependency_overrides.pop(get_pool, None)


def test_list_blocks_pagination_params():
    pool = _make_pool(fetch_return=[], fetchval_return=0)
    client = _make_client(pool)
    try:
        resp = client.get("/v1/blocks?page=2&page_size=5")
        assert resp.status_code == 200
        data = resp.json()
        assert data["page"] == 2
        assert data["page_size"] == 5
    finally:
        _cleanup()


# --- GET /v1/blocks/{block_id} ---

def test_get_block_found():
    pool = _make_pool(fetchrow_return=_SAMPLE_ROW)
    client = _make_client(pool)
    try:
        resp = client.get(f"/v1/blocks/{_BLOCK_ID}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["block_id"] == str(_BLOCK_ID)
        assert data["content"] == "Du bist ein hilfreicher Assistent."
        assert data["metadata"] == {"lang": "de"}
        assert data["tags"] == ["german", "assistant"]
    finally:
        _cleanup()


def test_get_block_not_found():
    pool = _make_pool(fetchrow_return=None)
    client = _make_client(pool)
    try:
        resp = client.get(f"/v1/blocks/{uuid4()}")
        assert resp.status_code == 404
    finally:
        _cleanup()


# --- POST /v1/blocks ---

def test_create_block():
    pool = _make_pool(conn_fetchrow_return=_SAMPLE_ROW)
    client = _make_client(pool)
    try:
        resp = client.post("/v1/blocks", json={
            "scope_id": str(_SCOPE_ID),
            "block_type": "system_prompt",
            "content": "Du bist ein hilfreicher Assistent.",
            "metadata": {"lang": "de"},
            "tags": ["german"],
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["status"] == "draft"
        assert data["version"] == 1
    finally:
        _cleanup()


def test_create_block_invalid_type():
    pool = _make_pool()
    client = _make_client(pool)
    try:
        resp = client.post("/v1/blocks", json={
            "scope_id": str(_SCOPE_ID),
            "block_type": "invalid_type",
            "content": "test",
        })
        assert resp.status_code == 422
    finally:
        _cleanup()


def test_create_block_missing_required_field():
    pool = _make_pool()
    client = _make_client(pool)
    try:
        resp = client.post("/v1/blocks", json={
            "block_type": "system_prompt",
            "content": "test",
            # scope_id missing
        })
        assert resp.status_code == 422
    finally:
        _cleanup()


# --- PATCH /v1/blocks/{block_id} ---

def test_patch_block_draft():
    updated_row = {**_SAMPLE_ROW, "content": "Neuer Inhalt."}
    # The pre-check reads on the pool, the UPDATE returns inside the
    # transaction. The actor lookup is answered separately by the shared double.
    pool = _make_pool(fetchrow_return=_SAMPLE_ROW, conn_fetchrow_return=updated_row)
    client = _make_client(pool)
    try:
        resp = client.patch(f"/v1/blocks/{_BLOCK_ID}", json={"content": "Neuer Inhalt."})
        assert resp.status_code == 200
    finally:
        _cleanup()


def test_patch_block_not_found():
    pool = _make_pool(fetchrow_return=None)
    client = _make_client(pool)
    try:
        resp = client.patch(f"/v1/blocks/{uuid4()}", json={"content": "test"})
        assert resp.status_code == 404
    finally:
        _cleanup()


def test_patch_block_non_draft_creates_a_new_version():
    """Was test_patch_block_non_draft_rejected, asserting 409.

    CHY-30 replaced the refusal: a published block is no longer a dead end, it
    is superseded into a new draft version. The 201 is what tells the caller
    a new version exists — live or not is its status (ADR-042). Detailed coverage in test_block_versioning.py.
    """
    active_row = {**_SAMPLE_ROW, "status": "active"}
    pool = _make_pool(fetchrow_return=active_row,
                      conn_fetchrow_return={**_SAMPLE_ROW, "version": 2})
    client = _make_client(pool)
    try:
        resp = client.patch(f"/v1/blocks/{_BLOCK_ID}", json={"content": "test"})
        assert resp.status_code == 201
    finally:
        _cleanup()


def test_patch_block_empty_body_returns_current():
    pool = _make_pool(fetchrow_return=_SAMPLE_ROW)
    client = _make_client(pool)
    try:
        resp = client.patch(f"/v1/blocks/{_BLOCK_ID}", json={})
        assert resp.status_code == 200
        assert resp.json()["content"] == _SAMPLE_ROW["content"]
    finally:
        _cleanup()


# --- DELETE /v1/blocks/{block_id} ---

def test_delete_block():
    """Retiring runs in a transaction now, reads FOR UPDATE and writes an audit
    entry (CHY-32) — so the row comes from the transaction connection."""
    pool = make_pool(
        actor=actor_row(role="reviewer"),
        conn_fetch=[{"id": _ROW_ID, "version": 1, "status": "active", "scope_id": _SCOPE_ID}],
    )
    client = _make_client(pool)
    try:
        assert client.delete(f"/v1/blocks/{_BLOCK_ID}").status_code == 204
    finally:
        _cleanup()


def test_delete_retires_every_active_version_of_the_line():
    """Superseding leaves the predecessor active so it keeps being composed
    during review. Retiring only the current version therefore let the previous
    one win the "newest active" query — a retired block reappeared with its old
    content. Found by the end-to-end run, not by a unit test."""
    pool = make_pool(
        actor=actor_row(role="reviewer"),
        conn_fetch=[
            {"id": uuid4(), "version": 2, "status": "active", "scope_id": _SCOPE_ID},
            {"id": _ROW_ID, "version": 1, "status": "active", "scope_id": _SCOPE_ID},
        ],
    )
    client = _make_client(pool)
    try:
        assert client.delete(f"/v1/blocks/{_BLOCK_ID}").status_code == 204
        sql = pool.conn.execute.call_args.args[0]
        assert "WHERE block_id = $1 AND status = 'active'" in sql
        payload = json.loads(next(
            c for c in pool.conn.fetchval.call_args_list if "audit_log" in c.args[0]
        ).args[4])
        assert payload["retired_versions"] == [2, 1]
    finally:
        _cleanup()


def test_delete_block_is_idempotent_without_a_second_audit_entry():
    """Repeating a DELETE is not a second decision. Each repeat would otherwise
    append another unremovable audit entry and bump the cache again."""
    pool = make_pool(
        actor=actor_row(role="reviewer"),
        conn_fetch=[{"id": _ROW_ID, "version": 1, "status": "deprecated", "scope_id": _SCOPE_ID}],
    )
    client = _make_client(pool)
    try:
        assert client.delete(f"/v1/blocks/{_BLOCK_ID}").status_code == 204
        pool.conn.execute.assert_not_called()
        # No audit entry. Not "no fetchval": since CHY-170 the scope's review
        # mode is read on the same connection.
        assert not [c for c in pool.conn.fetchval.call_args_list if "audit_log" in c.args[0]]
    finally:
        _cleanup()


def test_delete_refuses_a_block_that_was_never_active():
    """A submission is cancelled with withdraw, not retired."""
    pool = make_pool(
        actor=actor_row(role="reviewer"),
        conn_fetch=[{"id": _ROW_ID, "version": 1, "status": "pending_review", "scope_id": _SCOPE_ID}],
    )
    client = _make_client(pool)
    try:
        resp = client.delete(f"/v1/blocks/{_BLOCK_ID}")
        assert resp.status_code == 409
        assert "withdraw" in resp.json()["detail"]
    finally:
        _cleanup()


def test_delete_block_not_found():
    pool = make_pool(actor=actor_row(role="reviewer"), conn_fetch=[])
    client = _make_client(pool)
    try:
        resp = client.delete(f"/v1/blocks/{uuid4()}")
        assert resp.status_code == 404
    finally:
        _cleanup()


def test_delete_block_invalidates_the_compiled_cache():
    """Retiring an active block changes what its scope composes. Without the
    generation bump the cache would keep serving it for up to an hour."""
    pool = make_pool(
        actor=actor_row(role="reviewer"),
        conn_fetch=[{"id": _ROW_ID, "version": 1, "status": "active", "scope_id": _SCOPE_ID}],
    )
    redis = _make_redis()
    app.dependency_overrides[require_auth] = as_caller(_stub_user)
    app.dependency_overrides[get_pool] = lambda: pool
    app.dependency_overrides[get_redis] = lambda: redis
    try:
        client = TestClient(app)
        assert client.delete(f"/v1/blocks/{_BLOCK_ID}").status_code == 204
        redis.incr.assert_awaited_once_with(f"cc:scope_gen:{_SCOPE_ID}")
    finally:
        _cleanup()


def test_delete_block_missing_does_not_bump_the_cache():
    """Nothing changed, so nothing should be invalidated."""
    pool = make_pool(actor=actor_row(role="reviewer"), conn_fetch=[])
    redis = _make_redis()
    app.dependency_overrides[require_auth] = as_caller(_stub_user)
    app.dependency_overrides[get_pool] = lambda: pool
    app.dependency_overrides[get_redis] = lambda: redis
    try:
        TestClient(app).delete(f"/v1/blocks/{uuid4()}")
        redis.incr.assert_not_called()
    finally:
        _cleanup()
