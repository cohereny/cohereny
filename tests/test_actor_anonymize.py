"""
tests/test_actor_anonymize.py

Unit tests for the actor domain model and POST /v1/admin/actors/{ref}/anonymize
(CHY-34, ADR-016, ADR-025).

The endpoint erases personal data irreversibly, so the tests are weighted towards
the ways it must refuse: an unmapped caller, a non-admin, a missing target, and
the last remaining admin. Mocks: asyncpg pool. No PostgreSQL needed.
"""
import re
from datetime import datetime, UTC
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from api.deps import CurrentUser, require_auth
from api.main import app
from core.db import get_pool
from kernel.schema import ANONYMIZED_DISPLAY_NAME, Actor, ActorOut

_stub_user = CurrentUser(sub="admin-sub", username="admin")
_NOW = datetime(2026, 8, 12, 9, 0, tzinfo=UTC)
_CALLER_ID = uuid4()
_TARGET_ID = uuid4()


# ── Test doubles ──────────────────────────────────────────────────────────────

class _AsyncCM:
    """Minimal async context manager for pool.acquire() and conn.transaction()."""

    def __init__(self, value=None):
        self._value = value

    async def __aenter__(self):
        return self._value

    async def __aexit__(self, *exc):
        return False


# Distinct from None: a test must be able to say "the caller has no actor row",
# which is itself None. A plain default would swallow that case.
_DEFAULT = object()


def _make_pool(caller_row=_DEFAULT, target_row=None, remaining_admins=1) -> MagicMock:
    """Pool whose first fetchrow answers the admin check, second the target."""
    if caller_row is _DEFAULT:
        caller_row = {"id": _CALLER_ID, "role": "admin"}

    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value=target_row)
    conn.fetchval = AsyncMock(return_value=remaining_admins)
    conn.execute = AsyncMock()
    conn.transaction = MagicMock(return_value=_AsyncCM())

    pool = MagicMock()
    pool.fetchrow = AsyncMock(return_value=caller_row)
    pool.acquire = MagicMock(return_value=_AsyncCM(conn))
    pool._conn = conn  # exposed so tests can assert on the UPDATE
    return pool


def _make_client(pool) -> TestClient:
    app.dependency_overrides[require_auth] = lambda: _stub_user
    app.dependency_overrides[get_pool] = lambda: pool
    return TestClient(app)


def _cleanup():
    app.dependency_overrides.pop(require_auth, None)
    app.dependency_overrides.pop(get_pool, None)


def _target(**overrides) -> dict:
    return {
        "id": _TARGET_ID,
        "role": "contributor",
        "is_anonymized": False,
        "anonymized_at": None,
        **overrides,
    }


# ── Domain model ──────────────────────────────────────────────────────────────

class TestActorModel:
    def _base(self, **overrides) -> dict:
        return {
            "id": _TARGET_ID,
            "idp_sub": "authentik-sub-1",
            "display_name": "Thomas",
            "role": "contributor",
            "created_at": _NOW,
            **overrides,
        }

    def test_normal_actor_is_valid(self):
        actor = Actor(**self._base())
        assert actor.is_anonymized is False
        assert actor.is_identifiable is True

    def test_anonymized_actor_is_valid(self):
        actor = Actor(**self._base(
            idp_sub=None,
            display_name=ANONYMIZED_DISPLAY_NAME,
            is_anonymized=True,
            anonymized_at=_NOW,
        ))
        assert actor.is_identifiable is False

    def test_local_actor_without_idp_sub_is_not_anonymized(self):
        """A service account has no idp_sub either — that must not read as erased."""
        actor = Actor(**self._base(idp_sub=None, display_name="ci-bot"))
        assert actor.is_anonymized is False
        assert actor.is_identifiable is False

    def test_anonymized_without_timestamp_rejected(self):
        with pytest.raises(ValidationError, match="anonymized_at"):
            Actor(**self._base(idp_sub=None, is_anonymized=True))

    def test_anonymized_keeping_idp_sub_rejected(self):
        """Severing the identity link is the point — the flag must not lie."""
        with pytest.raises(ValidationError, match="idp_sub"):
            Actor(**self._base(is_anonymized=True, anonymized_at=_NOW))

    def test_timestamp_without_flag_rejected(self):
        with pytest.raises(ValidationError, match="is_anonymized"):
            Actor(**self._base(anonymized_at=_NOW))

    def test_actor_out_omits_idp_sub(self):
        """The IdP subject identifies a person and has no use outside auth."""
        out = ActorOut.from_actor(Actor(**self._base()))
        assert "idp_sub" not in out.model_dump()
        assert out.display_name == "Thomas"


class TestModelMatchesMigration:
    """The Pydantic validator and the SQL CHECKs must agree.

    Same drift guard as tests/test_scope_path.py: two places encode one rule, so
    a test reads the migration rather than trusting that both were updated.
    """

    def _sql(self) -> str:
        path = Path(__file__).resolve().parents[1] / "migrations" / "010_actor_anonymization.sql"
        return path.read_text(encoding="utf-8")

    def test_migration_exists_and_adds_both_columns(self):
        sql = self._sql()
        assert "is_anonymized" in sql
        assert "anonymized_at" in sql

    def test_migration_enforces_flag_timestamp_consistency(self):
        assert "actors_anonymized_consistent" in self._sql()

    def test_migration_enforces_no_idp_sub_when_anonymized(self):
        assert "actors_anonymized_has_no_idp_sub" in self._sql()

    def test_migration_number_is_unique(self):
        migrations = (Path(__file__).resolve().parents[1] / "migrations").glob("*.sql")
        numbers = [int(re.match(r"(\d+)_", p.name).group(1)) for p in migrations]
        assert len(numbers) == len(set(numbers)), f"duplicate migration number in {sorted(numbers)}"


# ── Endpoint: refusals ────────────────────────────────────────────────────────

class TestRefusals:
    def test_requires_auth(self):
        resp = TestClient(app).post(f"/v1/admin/actors/{_TARGET_ID}/anonymize")
        assert resp.status_code in (401, 403)

    def test_unknown_caller_is_provisioned_then_refused_as_too_junior(self):
        """Was test_unmapped_caller_is_refused.

        CHY-33 provisions a caller the system has never seen rather than
        turning them away — an action has to be attributable to someone. The
        default role is `consumer`, so an unknown caller still cannot erase
        anybody; they are refused for being too junior, not for being unknown.
        """
        pool = _make_pool(caller_row=None)
        # The provisioning INSERT and the endpoint's own SELECT both run on the
        # transaction connection; the first answer is the freshly created actor.
        pool._conn.fetchrow = AsyncMock(
            side_effect=[{"id": _CALLER_ID, "role": "consumer"}, _target()]
        )
        client = _make_client(pool)
        try:
            resp = client.post(f"/v1/admin/actors/{_TARGET_ID}/anonymize")
            assert resp.status_code == 403
            assert "caller has 'consumer'" in resp.json()["detail"]
        finally:
            _cleanup()

    def test_non_admin_is_refused(self):
        pool = _make_pool(caller_row={"id": _CALLER_ID, "role": "reviewer"})
        client = _make_client(pool)
        try:
            resp = client.post(f"/v1/admin/actors/{_TARGET_ID}/anonymize")
            assert resp.status_code == 403
            assert "'admin' or higher required" in resp.json()["detail"]
        finally:
            _cleanup()

    def test_non_admin_never_reaches_the_update(self):
        pool = _make_pool(caller_row={"id": _CALLER_ID, "role": "consumer"})
        client = _make_client(pool)
        try:
            client.post(f"/v1/admin/actors/{_TARGET_ID}/anonymize")
            pool._conn.execute.assert_not_called()
        finally:
            _cleanup()

    def test_missing_target_is_404(self):
        pool = _make_pool(target_row=None)
        client = _make_client(pool)
        try:
            resp = client.post(f"/v1/admin/actors/{_TARGET_ID}/anonymize")
            assert resp.status_code == 404
        finally:
            _cleanup()

    def test_last_active_admin_is_refused(self):
        """Clearing idp_sub would lock every future request out of this endpoint."""
        pool = _make_pool(target_row=_target(role="admin"), remaining_admins=0)
        client = _make_client(pool)
        try:
            resp = client.post(f"/v1/admin/actors/{_TARGET_ID}/anonymize")
            assert resp.status_code == 409
            assert "last active admin" in resp.json()["detail"]
            pool._conn.execute.assert_not_called()
        finally:
            _cleanup()

    def test_admin_can_be_anonymized_when_another_remains(self):
        pool = _make_pool(target_row=_target(role="admin"), remaining_admins=1)
        client = _make_client(pool)
        try:
            resp = client.post(f"/v1/admin/actors/{_TARGET_ID}/anonymize")
            assert resp.status_code == 200
            pool._conn.execute.assert_called_once()
        finally:
            _cleanup()

    def test_malformed_actor_ref_is_422(self):
        pool = _make_pool()
        client = _make_client(pool)
        try:
            assert client.post("/v1/admin/actors/not-a-uuid/anonymize").status_code == 422
        finally:
            _cleanup()


# ── Endpoint: the erasure itself ──────────────────────────────────────────────

class TestAnonymize:
    def test_happy_path_returns_result(self):
        pool = _make_pool(target_row=_target())
        client = _make_client(pool)
        try:
            resp = client.post(f"/v1/admin/actors/{_TARGET_ID}/anonymize")
            assert resp.status_code == 200
            body = resp.json()
            assert body["actor_id"] == str(_TARGET_ID)
            assert body["is_anonymized"] is True
            assert body["already_anonymized"] is False
            assert body["anonymized_at"] is not None
        finally:
            _cleanup()

    def test_update_clears_identity_and_sets_flag(self):
        pool = _make_pool(target_row=_target())
        client = _make_client(pool)
        try:
            client.post(f"/v1/admin/actors/{_TARGET_ID}/anonymize")
            sql, *params = pool._conn.execute.call_args.args
            assert "idp_sub = NULL" in sql
            assert "is_anonymized = TRUE" in sql
            assert params[0] == ANONYMIZED_DISPLAY_NAME
            assert params[2] == _TARGET_ID
        finally:
            _cleanup()

    def test_placeholder_name_does_not_derive_from_the_old_one(self):
        """A derived placeholder would leak what it is meant to remove."""
        assert ANONYMIZED_DISPLAY_NAME == "[anonym]"

    def test_row_is_locked_for_update(self):
        """Two concurrent requests must not both write an erasure timestamp."""
        pool = _make_pool(target_row=_target())
        client = _make_client(pool)
        try:
            client.post(f"/v1/admin/actors/{_TARGET_ID}/anonymize")
            assert "FOR UPDATE" in pool._conn.fetchrow.call_args.args[0]
        finally:
            _cleanup()

    def test_repeating_the_request_succeeds(self):
        """Idempotent: asking twice is not an error for the requester."""
        earlier = datetime(2026, 8, 1, 10, 0, tzinfo=UTC)
        pool = _make_pool(target_row=_target(is_anonymized=True, anonymized_at=earlier))
        client = _make_client(pool)
        try:
            resp = client.post(f"/v1/admin/actors/{_TARGET_ID}/anonymize")
            assert resp.status_code == 200
            assert resp.json()["already_anonymized"] is True
        finally:
            _cleanup()

    def test_repeating_the_request_keeps_the_original_date(self):
        """The first erasure date is the evidence — a repeat must not overwrite it."""
        earlier = datetime(2026, 8, 1, 10, 0, tzinfo=UTC)
        pool = _make_pool(target_row=_target(is_anonymized=True, anonymized_at=earlier))
        client = _make_client(pool)
        try:
            resp = client.post(f"/v1/admin/actors/{_TARGET_ID}/anonymize")
            assert resp.json()["anonymized_at"].startswith("2026-08-01")
            pool._conn.execute.assert_not_called()
        finally:
            _cleanup()


# ── Scope guard ───────────────────────────────────────────────────────────────

def test_audit_log_moved_out_of_admin():
    """Was a CHY-34 scope guard on the /admin/audit-log placeholder.

    CHY-31 implemented the audit log at /v1/audit-log and removed the
    placeholder rather than keeping an alias — the required role there depends
    on the filter, not on the route. The guard now records the move so a later
    change cannot quietly reintroduce a second, weaker way in.
    """
    paths = app.openapi()["paths"]
    assert "/v1/admin/audit-log" not in paths
    assert "/v1/audit-log" in paths
