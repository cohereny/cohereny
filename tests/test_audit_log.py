"""
tests/test_audit_log.py

Unit tests for the audit log write path, role gate and read endpoint (CHY-31).

The migration is tested separately against real PostgreSQL — a trigger that
raises cannot be verified against a mock. What is covered here: that the SQL
carrying the guarantee is present and says the right thing, that the write path
shares the caller's transaction, and that the read endpoint applies the right
bar to the right question.

Mocks: asyncpg pool. No PostgreSQL needed.
"""
import json
from datetime import datetime, UTC
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from api.deps import CurrentUser, require_auth
from api.main import app
from kernel.audit import ACTION_ACTOR_ANONYMIZED, target_ref, write_audit
from kernel.errors import AccessDenied
from kernel.access import ResolvedActor, require
from kernel.identity import resolve_actor
from core.db import get_pool
from tests.conftest import actor_row, make_pool

_stub_user = CurrentUser(sub="caller-sub", username="caller")
_NOW = datetime(2026, 8, 12, 10, 0, tzinfo=UTC)
_ACTOR_ID = uuid4()
_ENTRY_ID = uuid4()
_MIGRATION = Path(__file__).resolve().parents[1] / "migrations" / "011_audit_log_immutable.sql"


def _entry(**overrides) -> dict:
    return {
        "id": _ENTRY_ID,
        "actor_id": _ACTOR_ID,
        "action": "block.created",
        "target_ref": "block:abc",
        "payload": {"v": 1},
        "created_at": _NOW,
        **overrides,
    }


def _make_pool(role="admin", rows=None) -> MagicMock:
    """Thin wrapper over the shared double so the tests below stay readable."""
    return make_pool(
        actor=None if role is None else actor_row(_ACTOR_ID, role),
        fetch=rows if rows is not None else [],
    )


def _client(pool) -> TestClient:
    app.dependency_overrides[require_auth] = lambda: _stub_user
    app.dependency_overrides[get_pool] = lambda: pool
    return TestClient(app)


def _cleanup():
    app.dependency_overrides.pop(require_auth, None)
    app.dependency_overrides.pop(get_pool, None)


# ── The immutability guarantee lives in SQL ───────────────────────────────────

class TestMigration:
    """001_initial used rules that failed silently; 011 replaces them.

    Probed against cohereny_dev on 2026-08-12: UPDATE and DELETE were discarded
    without an error, and TRUNCATE emptied the table outright. These assertions
    keep the replacement from being quietly undone.
    """

    def _sql(self) -> str:
        return _MIGRATION.read_text(encoding="utf-8")

    def test_drops_the_silent_rules(self):
        sql = self._sql()
        assert "DROP RULE IF EXISTS no_update_audit_log" in sql
        assert "DROP RULE IF EXISTS no_delete_audit_log" in sql

    def test_raises_instead_of_ignoring(self):
        assert "RAISE EXCEPTION" in self._sql()

    @pytest.mark.parametrize("op", ["UPDATE", "DELETE", "TRUNCATE"])
    def test_all_three_operations_are_covered(self, op):
        """TRUNCATE is the one the rules never covered at all."""
        assert f"BEFORE {op} ON audit_log" in self._sql()

    def test_read_paths_are_indexed(self):
        sql = self._sql()
        assert "idx_audit_log_target" in sql
        assert "idx_audit_log_actor" in sql


# ── Write path ────────────────────────────────────────────────────────────────

class TestWriteAudit:
    @pytest.mark.asyncio
    async def test_writes_and_returns_id(self):
        conn = MagicMock()
        conn.fetchval = AsyncMock(return_value=_ENTRY_ID)
        result = await write_audit(
            conn, action="block.created", actor_id=_ACTOR_ID, target="block:abc"
        )
        assert result == _ENTRY_ID

    @pytest.mark.asyncio
    async def test_uses_the_caller_connection(self):
        """The entry must share the transaction with the change it records."""
        conn = MagicMock()
        conn.fetchval = AsyncMock(return_value=_ENTRY_ID)
        await write_audit(conn, action="block.created")
        conn.fetchval.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_missing_payload_becomes_empty_object_not_null(self):
        conn = MagicMock()
        conn.fetchval = AsyncMock(return_value=_ENTRY_ID)
        await write_audit(conn, action="block.created")
        # JSON text since CHY-164: sent as $4::text::jsonb, parsed by Postgres.
        assert json.loads(conn.fetchval.call_args.args[-1]) == {}

    @pytest.mark.asyncio
    async def test_write_failure_is_not_swallowed(self):
        """A change that could not be recorded must not silently happen."""
        conn = MagicMock()
        conn.fetchval = AsyncMock(side_effect=RuntimeError("db down"))
        with pytest.raises(RuntimeError):
            await write_audit(conn, action="block.created")

    def test_target_ref_format(self):
        uid = uuid4()
        assert target_ref("block", uid) == f"block:{uid}"


# ── Role gate ─────────────────────────────────────────────────────────────────

class TestRequireRole:
    @pytest.mark.asyncio
    async def test_unknown_caller_is_provisioned_not_refused(self):
        """Was test_unmapped_caller_is_refused, asserting 403.

        CHY-33 turned the refusal into provisioning: a caller the system has
        never seen gets an actor row so their actions can be attributed. What
        they may then *do* is still decided by the role check.
        """
        pool = _make_pool(role=None)
        pool.conn.fetchrow = AsyncMock(return_value={"id": _ACTOR_ID, "role": "consumer"})

        with pytest.raises(AccessDenied) as exc:
            require(await resolve_actor(pool, _stub_user), "reviewer")
        # Refused for the right reason now: too junior, not unknown.
        assert exc.value.actual == "consumer"
        assert "INSERT INTO actors" in pool.conn.fetchrow.call_args.args[0]

    @pytest.mark.asyncio
    async def test_anonymized_actors_are_excluded_by_the_query(self):
        pool = _make_pool(role="admin")
        require(await resolve_actor(pool, _stub_user), "admin")
        assert "is_anonymized = FALSE" in pool.fetchrow.call_args.args[0]

    @pytest.mark.asyncio
    async def test_higher_role_satisfies_lower_requirement(self):
        actor = require(await resolve_actor(_make_pool(role="admin"), _stub_user), "reviewer")
        assert actor.role == "admin"

    @pytest.mark.asyncio
    async def test_lower_role_is_refused(self):
        with pytest.raises(AccessDenied):
            require(await resolve_actor(_make_pool(role="contributor"), _stub_user), "reviewer")

    @pytest.mark.asyncio
    async def test_unknown_minimum_is_a_programming_error(self):
        """A typo must fail loudly, not rank everyone above an unknown bar."""
        with pytest.raises(ValueError):
            require(await resolve_actor(_make_pool(), _stub_user), "supervisor")

    @pytest.mark.parametrize(
        "role,minimum,expected",
        [
            ("consumer", "reviewer", False),
            ("contributor", "reviewer", False),
            ("reviewer", "reviewer", True),
            ("architect", "reviewer", True),
            ("admin", "reviewer", True),
            ("reviewer", "admin", False),
            ("architect", "admin", False),
            ("admin", "admin", True),
        ],
    )
    def test_role_ordering(self, role, minimum, expected):
        assert ResolvedActor(_ACTOR_ID, role).has_at_least(minimum) is expected

    def test_unknown_role_ranks_below_everything(self):
        """A role the code does not know must not accidentally outrank admin."""
        assert ResolvedActor(_ACTOR_ID, "wizard").has_at_least("consumer") is False


# ── Read endpoint ─────────────────────────────────────────────────────────────

class TestReadEndpoint:
    def test_requires_auth(self):
        assert TestClient(app).get("/v1/audit-log").status_code in (401, 403)

    def test_entity_history_needs_reviewer(self):
        pool = _make_pool(role="contributor")
        client = _client(pool)
        try:
            resp = client.get("/v1/audit-log", params={"target": "block:abc"})
            assert resp.status_code == 403
        finally:
            _cleanup()

    def test_reviewer_may_read_entity_history(self):
        pool = _make_pool(role="reviewer", rows=[_entry()])
        client = _client(pool)
        try:
            resp = client.get("/v1/audit-log", params={"target": "block:abc"})
            assert resp.status_code == 200
            assert resp.json()[0]["action"] == "block.created"
        finally:
            _cleanup()

    def test_reviewer_may_not_read_one_actors_history(self):
        """A record of what one named person did is not review material."""
        pool = _make_pool(role="reviewer")
        client = _client(pool)
        try:
            resp = client.get("/v1/audit-log", params={"actor_id": str(_ACTOR_ID)})
            assert resp.status_code == 403
        finally:
            _cleanup()

    def test_reviewer_may_not_read_the_whole_log(self):
        pool = _make_pool(role="reviewer")
        client = _client(pool)
        try:
            assert client.get("/v1/audit-log").status_code == 403
        finally:
            _cleanup()

    def test_admin_may_read_one_actors_history(self):
        pool = _make_pool(role="admin", rows=[_entry()])
        client = _client(pool)
        try:
            assert client.get(
                "/v1/audit-log", params={"actor_id": str(_ACTOR_ID)}
            ).status_code == 200
        finally:
            _cleanup()

    def test_both_filters_at_once_is_refused(self):
        pool = _make_pool(role="admin")
        client = _client(pool)
        try:
            resp = client.get(
                "/v1/audit-log",
                params={"target": "block:abc", "actor_id": str(_ACTOR_ID)},
            )
            assert resp.status_code == 400
        finally:
            _cleanup()

    def test_role_is_checked_before_the_log_is_touched(self):
        """Otherwise timing would reveal whether an entity has entries at all."""
        pool = _make_pool(role="contributor")
        client = _client(pool)
        try:
            client.get("/v1/audit-log", params={"target": "block:abc"})
            pool.fetch.assert_not_called()
        finally:
            _cleanup()

    def test_sorted_newest_first_with_a_tie_breaker(self):
        pool = _make_pool(role="admin", rows=[])
        client = _client(pool)
        try:
            client.get("/v1/audit-log")
            sql = pool.fetch.call_args.args[0]
            assert "ORDER BY created_at DESC, id DESC" in sql
        finally:
            _cleanup()

    def test_limit_is_bounded(self):
        pool = _make_pool(role="admin")
        client = _client(pool)
        try:
            assert client.get("/v1/audit-log", params={"limit": 5000}).status_code == 422
            assert client.get("/v1/audit-log", params={"limit": 0}).status_code == 422
        finally:
            _cleanup()

    def test_offset_may_not_be_negative(self):
        pool = _make_pool(role="admin")
        client = _client(pool)
        try:
            assert client.get("/v1/audit-log", params={"offset": -1}).status_code == 422
        finally:
            _cleanup()

    def test_no_write_verbs_on_the_route(self):
        """The log is append-only; writes go through core.audit, never HTTP."""
        paths = app.openapi()["paths"]["/v1/audit-log"]
        assert set(paths) == {"get"}


# ── Wiring: the erasure records itself ────────────────────────────────────────

class _AsyncCM:
    def __init__(self, value=None):
        self._value = value

    async def __aenter__(self):
        return self._value

    async def __aexit__(self, *exc):
        return False


def test_anonymize_writes_an_audit_entry_in_the_same_transaction():
    """CHY-34 left this as TODO(CHY-31). It must not stay a TODO."""
    target_id = uuid4()
    conn = MagicMock()
    conn.fetchrow = AsyncMock(
        return_value={
            "id": target_id,
            "role": "contributor",
            "is_anonymized": False,
            "anonymized_at": None,
        }
    )
    conn.fetchval = AsyncMock(return_value=_ENTRY_ID)
    conn.execute = AsyncMock()
    conn.transaction = MagicMock(return_value=_AsyncCM())

    pool = _make_pool(role="admin")
    pool.acquire = MagicMock(return_value=_AsyncCM(conn))

    client = _client(pool)
    try:
        resp = client.post(f"/v1/admin/actors/{target_id}/anonymize")
        assert resp.status_code == 200

        # Written through the same connection as the UPDATE, so a rollback takes
        # both or neither.
        conn.fetchval.assert_awaited_once()
        sql, *params = conn.fetchval.call_args.args
        assert "INSERT INTO audit_log" in sql
        assert params[1] == ACTION_ACTOR_ANONYMIZED
        assert params[2] == f"actor:{target_id}"
    finally:
        _cleanup()


def test_anonymize_audit_payload_carries_no_personal_data():
    """Recording who the person was would undo the erasure being recorded."""
    target_id = uuid4()
    conn = MagicMock()
    conn.fetchrow = AsyncMock(
        return_value={
            "id": target_id,
            "role": "contributor",
            "is_anonymized": False,
            "anonymized_at": None,
        }
    )
    conn.fetchval = AsyncMock(return_value=_ENTRY_ID)
    conn.execute = AsyncMock()
    conn.transaction = MagicMock(return_value=_AsyncCM())

    pool = _make_pool(role="admin")
    pool.acquire = MagicMock(return_value=_AsyncCM(conn))

    client = _client(pool)
    try:
        client.post(f"/v1/admin/actors/{target_id}/anonymize")
        payload = json.loads(conn.fetchval.call_args.args[-1])
        assert set(payload) == {"legal_basis", "previous_role"}
        assert "display_name" not in payload
        assert "idp_sub" not in payload
    finally:
        _cleanup()


# ── The author may read their own block's entries (CHY-32) ────────────────────

class TestAuthorAccess:
    """A contributor whose block was rejected must be able to find out why.

    The reason lives only in the audit payload, so without this exception the
    review would be unanswerable: reject with an explanation the author cannot
    read is a rejection with no explanation.
    """

    def _pool(self, role, is_author):
        pool = make_pool(actor=actor_row(_ACTOR_ID, role), fetch=[_entry()])
        pool.fetchval = AsyncMock(return_value=is_author)
        return pool

    def test_the_author_may_read_their_own_block(self):
        client = _client(self._pool("contributor", True))
        try:
            resp = client.get("/v1/audit-log", params={"target": f"block:{uuid4()}"})
            assert resp.status_code == 200
        finally:
            _cleanup()

    def test_a_stranger_still_cannot(self):
        client = _client(self._pool("contributor", False))
        try:
            resp = client.get("/v1/audit-log", params={"target": f"block:{uuid4()}"})
            assert resp.status_code == 403
            assert "authorship" in resp.json()["detail"]
        finally:
            _cleanup()

    def test_a_reviewer_needs_no_authorship(self):
        client = _client(self._pool("reviewer", False))
        try:
            assert client.get(
                "/v1/audit-log", params={"target": f"block:{uuid4()}"}
            ).status_code == 200
        finally:
            _cleanup()

    def test_the_exception_is_limited_to_blocks(self):
        """Authorship of a block says nothing about an actor's own history."""
        client = _client(self._pool("contributor", True))
        try:
            assert client.get(
                "/v1/audit-log", params={"target": f"actor:{uuid4()}"}
            ).status_code == 403
        finally:
            _cleanup()

    def test_a_malformed_target_is_not_treated_as_authorship(self):
        client = _client(self._pool("contributor", True))
        try:
            assert client.get(
                "/v1/audit-log", params={"target": "block:not-a-uuid"}
            ).status_code == 403
        finally:
            _cleanup()
