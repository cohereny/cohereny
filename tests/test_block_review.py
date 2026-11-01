"""
tests/test_block_review.py

Tests for the ContextBlock review workflow (CHY-32).

Until now nothing in the codebase could set `status = 'active'`, while three
read paths filtered on it — so `/v1/compose` and `/v1/complete` could only ever
return nothing. These four endpoints are what makes content reach a prompt, and
the tests are weighted accordingly: which transitions exist, who may trigger
them, and the two ways a reviewer could end up approving something they did not
read.

The transition table is enforced twice — in migration 013 and here — and the
verification against real PostgreSQL is recorded in the MR. The endpoint checks
exist so a wrong pre-state produces a 409 with an explanation rather than a 500
carrying a plpgsql exception.
"""
import json
from uuid import uuid4

import pytest

from kernel.audit import (
    ACTION_BLOCK_APPROVED,
    ACTION_BLOCK_DEPRECATED,
    ACTION_BLOCK_REJECTED,
    ACTION_BLOCK_SUBMITTED,
    ACTION_BLOCK_WITHDRAWN,
)
from tests.conftest import DEFAULT_ACTOR_ID, actor_row, client_for, make_pool

_BLOCK_ID = uuid4()
_ROW_ID = uuid4()
_SCOPE_ID = uuid4()
_AUTHOR_ID = uuid4()


def _current(status="draft", version=1, author_id=_AUTHOR_ID, **governance) -> dict:
    """A row as lock_current_block returns it.

    The three governance columns are part of that row since CHY-62. Defaulted
    to "makes no claim" so the existing transition tests keep testing
    transitions — the ratchet has its own file, and a claim smuggled into these
    fixtures would make every one of them a governance test by accident.
    """
    return {
        "id": _ROW_ID,
        "block_id": _BLOCK_ID,
        "version": version,
        "status": status,
        "scope_id": _SCOPE_ID,
        "author_id": author_id,
        "governance_key": governance.get("governance_key"),
        "privacy_impact": governance.get("privacy_impact"),
        "locked": governance.get("locked", False),
        # Since CHY-160 the locked row carries what an activation check needs.
        "block_type": "system_prompt",
        "metadata": {},
    }


def _updated(status) -> dict:
    return {
        "id": _ROW_ID,
        "block_id": _BLOCK_ID,
        "version": 1,
        "superseded_by_version": None,
        "scope_id": _SCOPE_ID,
        "block_type": "system_prompt",
        "content": "x",
        "metadata": {},
        "tags": [],
        "author_id": _AUTHOR_ID,
        "status": status,
        "governance_key": None,
        "privacy_impact": None,
        "locked": False,
        "created_at": "2026-08-13T09:00:00+00:00",
    }


def _pool(role, current, updated_status=None, **kw):
    """current answers the FOR UPDATE read, updated the RETURNING of the UPDATE."""
    rows = [current]
    if updated_status is not None:
        rows.append(_updated(updated_status))
    return make_pool(actor=actor_row(role=role), conn_fetchrow=rows, **kw)


def _audit_payload(pool):
    call = next(c for c in pool.conn.fetchval.call_args_list if "audit_log" in c.args[0])
    return call.args[2], json.loads(call.args[4])  # action, payload (JSON text, CHY-164)


# ── The happy path ────────────────────────────────────────────────────────────

class TestTransitions:
    def test_submit_moves_a_draft_to_review(self):
        pool = _pool("contributor", _current("draft"), "pending_review")
        with client_for(pool) as client:
            resp = client.post(f"/v1/blocks/{_BLOCK_ID}/submit", json={"version": 1})
            assert resp.status_code == 200
            assert resp.json()["status"] == "pending_review"
            assert _audit_payload(pool)[0] == ACTION_BLOCK_SUBMITTED

    def test_a_rejected_block_can_be_submitted_again(self):
        """Rejection is a round trip, not a dead end."""
        pool = _pool("contributor", _current("rejected"), "pending_review")
        with client_for(pool) as client:
            assert client.post(
                f"/v1/blocks/{_BLOCK_ID}/submit", json={"version": 1}
            ).status_code == 200

    def test_withdraw_takes_a_submission_back(self):
        """Without it the only way to cancel a review would be to retire the
        block, which is a different thing entirely."""
        pool = _pool("contributor", _current("pending_review"), "draft")
        with client_for(pool) as client:
            resp = client.post(f"/v1/blocks/{_BLOCK_ID}/withdraw", json={"version": 1})
            assert resp.json()["status"] == "draft"
            assert _audit_payload(pool)[0] == ACTION_BLOCK_WITHDRAWN

    def test_approve_activates(self):
        pool = _pool("reviewer", _current("pending_review"), "active",
                     fetchrow={"author_id": _AUTHOR_ID})
        with client_for(pool) as client:
            resp = client.post(f"/v1/blocks/{_BLOCK_ID}/approve", json={"version": 1})
            assert resp.json()["status"] == "active"
            assert _audit_payload(pool)[0] == ACTION_BLOCK_APPROVED

    def test_reject_records_the_reason(self):
        pool = _pool("reviewer", _current("pending_review"), "rejected")
        with client_for(pool) as client:
            resp = client.post(
                f"/v1/blocks/{_BLOCK_ID}/reject",
                json={"version": 1, "reason": "no source given"},
            )
            assert resp.json()["status"] == "rejected"
            action, payload = _audit_payload(pool)
            assert action == ACTION_BLOCK_REJECTED
            assert payload["reason"] == "no source given"


# ── Wrong pre-state ───────────────────────────────────────────────────────────

class TestPreStateRefusals:
    @pytest.mark.parametrize("state", ["active", "pending_review", "deprecated"])
    def test_submit_only_from_draft_or_rejected(self, state):
        pool = _pool("contributor", _current(state))
        with client_for(pool) as client:
            resp = client.post(f"/v1/blocks/{_BLOCK_ID}/submit", json={"version": 1})
            assert resp.status_code == 409
            assert state in resp.json()["detail"]

    @pytest.mark.parametrize("state", ["draft", "active", "rejected", "deprecated"])
    def test_approve_only_from_pending_review(self, state):
        """A block must not skip review — that is the whole point of CHY-32."""
        pool = _pool("reviewer", _current(state), fetchrow={"author_id": _AUTHOR_ID})
        with client_for(pool) as client:
            assert client.post(
                f"/v1/blocks/{_BLOCK_ID}/approve", json={"version": 1}
            ).status_code == 409

    def test_withdraw_only_from_pending_review(self):
        pool = _pool("contributor", _current("draft"))
        with client_for(pool) as client:
            assert client.post(
                f"/v1/blocks/{_BLOCK_ID}/withdraw", json={"version": 1}
            ).status_code == 409

    def test_missing_block_is_404(self):
        pool = _pool("reviewer", None)
        with client_for(pool) as client:
            assert client.post(
                f"/v1/blocks/{uuid4()}/submit", json={"version": 1}
            ).status_code == 404

    def test_nothing_is_written_when_the_pre_state_is_wrong(self):
        pool = _pool("reviewer", _current("draft"), fetchrow={"author_id": _AUTHOR_ID})
        with client_for(pool) as client:
            client.post(f"/v1/blocks/{_BLOCK_ID}/approve", json={"version": 1})
        pool.conn.execute.assert_not_called()


# ── Approving what you actually read ──────────────────────────────────────────

class TestVersionCheck:
    def test_a_stale_version_is_refused(self):
        """The author patched between the reviewer's GET and their POST — the
        row they read has been superseded and they must look again."""
        pool = _pool("reviewer", _current("pending_review", version=3),
                     fetchrow={"author_id": _AUTHOR_ID})
        with client_for(pool) as client:
            resp = client.post(f"/v1/blocks/{_BLOCK_ID}/approve", json={"version": 2})
            assert resp.status_code == 409
            assert "version 2" in resp.json()["detail"]
            assert "current one is 3" in resp.json()["detail"]

    def test_the_version_check_runs_before_the_state_check(self):
        """Otherwise a stale reviewer gets told about a state they were never
        looking at."""
        pool = _pool("reviewer", _current("draft", version=3),
                     fetchrow={"author_id": _AUTHOR_ID})
        with client_for(pool) as client:
            resp = client.post(f"/v1/blocks/{_BLOCK_ID}/approve", json={"version": 1})
            assert "has moved on" in resp.json()["detail"]

    def test_version_is_required(self):
        pool = _pool("reviewer", _current("pending_review"))
        with client_for(pool) as client:
            assert client.post(
                f"/v1/blocks/{_BLOCK_ID}/reject", json={"reason": "x"}
            ).status_code == 422

    def test_the_row_is_locked(self):
        pool = _pool("contributor", _current("draft"), "pending_review")
        with client_for(pool) as client:
            client.post(f"/v1/blocks/{_BLOCK_ID}/submit", json={"version": 1})
        assert "FOR UPDATE" in pool.conn.fetchrow.call_args_list[0].args[0]


# ── Roles ─────────────────────────────────────────────────────────────────────

class TestRoles:
    def test_a_contributor_cannot_approve(self):
        """Otherwise anyone who can write can also publish, and the gate is
        decoration."""
        pool = _pool("contributor", _current("pending_review"))
        with client_for(pool) as client:
            assert client.post(
                f"/v1/blocks/{_BLOCK_ID}/approve", json={"version": 1}
            ).status_code == 403

    def test_a_contributor_cannot_reject(self):
        pool = _pool("contributor", _current("pending_review"))
        with client_for(pool) as client:
            assert client.post(
                f"/v1/blocks/{_BLOCK_ID}/reject",
                json={"version": 1, "reason": "not convincing"},
            ).status_code == 403

    def test_a_consumer_cannot_submit(self):
        pool = _pool("consumer", _current("draft"))
        with client_for(pool) as client:
            assert client.post(
                f"/v1/blocks/{_BLOCK_ID}/submit", json={"version": 1}
            ).status_code == 403

    @pytest.mark.parametrize("route", ["submit", "withdraw", "approve", "reject"])
    def test_all_transitions_require_auth(self, route):
        from fastapi.testclient import TestClient

        from api.main import app

        resp = TestClient(app).post(
            f"/v1/blocks/{_BLOCK_ID}/{route}", json={"version": 1, "reason": "x"}
        )
        assert resp.status_code in (401, 403)


# ── The rejection reason ──────────────────────────────────────────────────────

class TestRejectionReason:
    @pytest.mark.parametrize("reason", ["", "  ", "ab"])
    def test_a_blank_or_trivial_reason_is_refused(self, reason):
        """It is the only artefact of the decision the author ever sees."""
        pool = _pool("reviewer", _current("pending_review"))
        with client_for(pool) as client:
            assert client.post(
                f"/v1/blocks/{_BLOCK_ID}/reject", json={"version": 1, "reason": reason}
            ).status_code == 422

    def test_a_reason_is_required_at_all(self):
        pool = _pool("reviewer", _current("pending_review"))
        with client_for(pool) as client:
            assert client.post(
                f"/v1/blocks/{_BLOCK_ID}/reject", json={"version": 1}
            ).status_code == 422


# ── Cache invalidation ────────────────────────────────────────────────────────

class TestCacheInvalidation:
    def test_approve_invalidates(self):
        """Approving is the moment a block starts being composed."""
        pool = _pool("reviewer", _current("pending_review"), "active",
                     fetchrow={"author_id": _AUTHOR_ID})
        from tests.conftest import make_redis

        redis = make_redis()
        with client_for(pool, redis=redis) as client:
            client.post(f"/v1/blocks/{_BLOCK_ID}/approve", json={"version": 1})
        redis.incr.assert_awaited_once_with(f"cc:scope_gen:{_SCOPE_ID}")

    def test_submit_does_not_invalidate(self):
        """Nothing about what the scope composes changed."""
        from tests.conftest import make_redis

        pool = _pool("contributor", _current("draft"), "pending_review")
        redis = make_redis()
        with client_for(pool, redis=redis) as client:
            client.post(f"/v1/blocks/{_BLOCK_ID}/submit", json={"version": 1})
        redis.incr.assert_not_called()

    def test_a_failing_invalidation_does_not_fail_the_approval(self):
        """The status change is committed. A 500 here would tell the caller
        their approval did not happen when it did."""
        from unittest.mock import AsyncMock

        from tests.conftest import make_redis

        pool = _pool("reviewer", _current("pending_review"), "active",
                     fetchrow={"author_id": _AUTHOR_ID})
        redis = make_redis()
        redis.incr = AsyncMock(side_effect=RuntimeError("redis down"))
        with client_for(pool, redis=redis) as client:
            assert client.post(
                f"/v1/blocks/{_BLOCK_ID}/approve", json={"version": 1}
            ).status_code == 200


# ── Self-approval stays visible ───────────────────────────────────────────────

def test_approve_records_author_and_approver():
    """Four eyes is not enforced — that belongs with ADR-011, and a hard rule
    would make approval impossible in a single-operator deployment. Recording
    both at least makes a self-approval detectable afterwards."""
    pool = _pool("reviewer", _current("pending_review"), "active",
                 fetchrow={"author_id": _AUTHOR_ID})
    with client_for(pool) as client:
        client.post(f"/v1/blocks/{_BLOCK_ID}/approve", json={"version": 1})
    _, payload = _audit_payload(pool)
    assert payload["author_id"] == str(_AUTHOR_ID)
    assert payload["approved_by"] == str(DEFAULT_ACTOR_ID)


def test_deprecate_is_audited_as_its_own_action():
    pool = make_pool(
        actor=actor_row(role="reviewer"),
        conn_fetch=[_current(status="active")],
    )
    with client_for(pool) as client:
        assert client.delete(f"/v1/blocks/{_BLOCK_ID}").status_code == 204
    action, _ = _audit_payload(pool)
    assert action == ACTION_BLOCK_DEPRECATED
