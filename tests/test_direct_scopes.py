"""
tests/test_direct_scopes.py

The direct path of ADR-011, built on the block lifecycle (ADR-042, CHY-65).

In a project, sub_project or personal scope a write goes live at once; in an
org, team or workspace it waits for a reviewer. What must hold, each a test:

  - the table of review modes covers every scope type the database allows,
    and a type missing from it is strict, not direct
  - a direct create and a direct edit pass pending_review on the way to
    active, and each step leaves the audit entry a strict approval leaves,
    marked `mode: direct`
  - a strict create stays a draft and writes nothing else
  - the ratchet and `before_activation` still run on the direct path — the
    counter-probes of the acceptance criteria
  - a governance claim may ride along with an edit in a direct scope, and
    still may not in a strict one

The pool is a double. What only a database can show — that a refusal leaves no
draft behind, that migration 013 accepts the two steps — is in
tests/integration/test_direct_scopes_db.py.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import get_args
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from kernel.audit import ACTION_BLOCK_APPROVED, ACTION_BLOCK_CREATED, ACTION_BLOCK_SUBMITTED
from kernel.schema import ScopeNodeType
from kernel.scopes import REVIEW_MODES, ReviewMode, review_mode
from tests.conftest import DEFAULT_ACTOR_ID, actor_row, client_for, make_pool, make_redis

_ROOT = Path(__file__).resolve().parents[1]
_SCOPE = uuid4()
_ORG = uuid4()
_BLOCK = uuid4()
_AUTHOR = uuid4()
_KEY = "cloud_routing"


# ── The table ─────────────────────────────────────────────────────────────────

class TestTheTable:
    def test_it_is_adr_011(self):
        assert {t: m.value for t, m in REVIEW_MODES.items()} == {
            "org": "strict", "team": "strict", "workspace": "strict",
            "project": "direct", "sub_project": "direct", "personal": "direct",
        }

    def test_it_covers_every_type_the_schema_allows(self):
        assert set(REVIEW_MODES) == set(get_args(ScopeNodeType))

    def test_it_covers_every_type_the_database_allows(self):
        """The CHECK on scope_nodes is the vocabulary that rows actually carry."""
        sql = (_ROOT / "migrations" / "001_initial.sql").read_text(encoding="utf-8")
        check = re.search(r"node_type\s+TEXT NOT NULL CHECK \(node_type IN \(([^)]*)\)\)", sql)
        assert check, "the CHECK on scope_nodes.node_type moved"
        assert set(REVIEW_MODES) == set(re.findall(r"'(\w+)'", check.group(1)))

    @pytest.mark.parametrize("node_type", [None, "", "galaxy"])
    def test_a_type_it_does_not_know_is_strict(self, node_type):
        """The safe side is review, not a write that goes live unasked."""
        assert review_mode(node_type) is ReviewMode.STRICT


# ── Doubles ───────────────────────────────────────────────────────────────────

def _row(status: str, version: int = 1, **kw) -> dict:
    """A block row as every write returns it (RETURNING *)."""
    return {
        "id": uuid4(), "block_id": _BLOCK, "version": version,
        "superseded_by_version": None, "scope_id": _SCOPE,
        "block_type": kw.get("block_type", "system_prompt"),
        "content": "x", "metadata": kw.get("metadata", {}), "tags": [],
        "author_id": kw.get("author_id", _AUTHOR), "status": status,
        "governance_key": kw.get("governance_key"),
        "privacy_impact": kw.get("privacy_impact"),
        "locked": kw.get("locked", False),
        "created_at": "2026-09-29T09:00:00+00:00",
    }


def _pool(node_type: str, writes: list[dict], *, current: dict | None = None,
          role: str = "contributor", conn_fetch=None, keys=({"key": _KEY},)):
    """A pool whose scope is of `node_type`; `writes` answer the statements
    inside the transaction in order — the write, then each status change."""
    pool = make_pool(
        actor=actor_row(role=role),
        fetchrow=current,
        fetch=list(keys),
        conn_fetchrow=writes,
        conn_fetch=conn_fetch,
    )

    async def fetchval(sql, *args):
        if "FROM scope_nodes" in sql:
            return node_type
        return uuid4()  # an audit entry's id

    pool.conn.fetchval = AsyncMock(side_effect=fetchval)
    return pool


def _audit(pool) -> list[tuple[str, dict]]:
    """(action, payload) of every audit entry, in order."""
    return [
        (c.args[2], json.loads(c.args[4]))
        for c in pool.conn.fetchval.call_args_list if "audit_log" in c.args[0]
    ]


def _status_changes(pool) -> list[str]:
    return [c.args[1] for c in pool.conn.fetchrow.call_args_list
            if c.args[0].startswith("UPDATE context_blocks SET status")]


_CREATE = {"scope_id": str(_SCOPE), "block_type": "system_prompt", "content": "x"}


# ── Create ────────────────────────────────────────────────────────────────────

class TestCreate:
    @pytest.mark.parametrize("node_type", ["project", "sub_project", "personal"])
    def test_in_a_direct_scope_it_is_active_at_once(self, node_type):
        pool = _pool(node_type, [_row("draft"), _row("pending_review"), _row("active")])
        with client_for(pool) as client:
            response = client.post("/v1/blocks", json=_CREATE)

        assert response.status_code == 201, response.text
        assert response.json()["status"] == "active"
        assert _status_changes(pool) == ["pending_review", "active"]

    def test_the_audit_shows_both_steps_marked_direct(self):
        """Acceptance criterion 1 of CHY-65, as far as a double can show it."""
        me = {"author_id": DEFAULT_ACTOR_ID}
        pool = _pool("personal", [_row("draft", **me), _row("pending_review", **me),
                                  _row("active", **me)])
        with client_for(pool) as client:
            client.post("/v1/blocks", json=_CREATE)

        (created, _), (submitted, s_payload), (approved, a_payload) = _audit(pool)
        assert (created, submitted, approved) == (
            ACTION_BLOCK_CREATED, ACTION_BLOCK_SUBMITTED, ACTION_BLOCK_APPROVED)
        assert s_payload["mode"] == a_payload["mode"] == "direct"
        # The writer approves their own block — recorded as such, visibly.
        assert a_payload["approved_by"] == a_payload["author_id"] == str(DEFAULT_ACTOR_ID)
        assert (s_payload["from_status"], a_payload["to_status"]) == ("draft", "active")

    @pytest.mark.parametrize("node_type", ["org", "team", "workspace"])
    def test_in_a_strict_scope_it_stays_a_draft(self, node_type):
        """Acceptance criterion 2: a draft, and nothing written besides it —
        except the record of who wrote it (CHY-171)."""
        pool = _pool(node_type, [_row("draft")])
        with client_for(pool) as client:
            response = client.post("/v1/blocks", json=_CREATE)

        assert response.status_code == 201
        assert response.json()["status"] == "draft"
        assert _status_changes(pool) == []
        assert [action for action, _ in _audit(pool)] == [ACTION_BLOCK_CREATED]

    def test_a_direct_create_invalidates_the_composed_cache(self):
        """It changes what the scope composes; a draft does not."""
        direct, strict = make_redis(), make_redis()
        for node_type, redis, writes in (
            ("personal", direct, [_row("draft"), _row("pending_review"), _row("active")]),
            ("team", strict, [_row("draft")]),
        ):
            with client_for(_pool(node_type, writes), redis=redis) as client:
                client.post("/v1/blocks", json=_CREATE)
        assert direct.incr.await_count == 1
        assert strict.incr.await_count == 0


# ── The gates still hold ──────────────────────────────────────────────────────

def _chain_and_claims(claims: list[dict]):
    chain = [
        {"id": _ORG, "scope_path": "org", "node_type": "org", "name": "org",
         "depth": 1, "privacy_level": None},
        {"id": _SCOPE, "scope_path": "org.me", "node_type": "personal", "name": "me",
         "depth": 2, "privacy_level": None},
    ]

    def answer(sql, *args):
        if "scope_path @>" in sql:
            return chain
        if "governance_key = $2" in sql:
            return claims
        return []

    return answer


_RESTRICTIVE_ABOVE = [{"block_id": uuid4(), "scope_id": _ORG, "governance_key": _KEY,
                       "privacy_impact": "restrictive", "locked": False}]


class TestTheGatesStillHold:
    def test_the_ratchet_refuses_a_direct_create(self):
        """Acceptance criterion 4, the probe. A permissive claim under a
        restrictive one goes no further than pending_review — and the
        transaction around it takes that back too (integration test)."""
        claim = {"governance_key": _KEY, "privacy_impact": "permissive"}
        pool = _pool("personal", [_row("draft", **claim), _row("pending_review", **claim)],
                     conn_fetch=_chain_and_claims(_RESTRICTIVE_ABOVE))
        with client_for(pool) as client:
            response = client.post("/v1/blocks", json={**_CREATE, **claim})

        assert response.status_code == 409, response.text
        assert _KEY in response.json()["detail"]
        assert "active" not in _status_changes(pool)

    def test_the_ratchet_lets_a_tightening_direct_create_through(self):
        """The counter-probe: without it the test above would also pass
        against a direct path that refuses every claim."""
        claim = {"governance_key": _KEY, "privacy_impact": "restrictive"}
        pool = _pool("personal", [_row("draft", **claim), _row("pending_review", **claim),
                                  _row("active", **claim)],
                     conn_fetch=_chain_and_claims(_RESTRICTIVE_ABOVE))
        with client_for(pool) as client:
            response = client.post("/v1/blocks", json={**_CREATE, **claim})

        assert response.status_code == 201, response.text
        assert response.json()["status"] == "active"

    def test_before_activation_runs_on_the_direct_path(self):
        """A routing rule without max_tokens could never be used (CHY-166).
        Approving it is refused with 422 — and so is writing it where writing
        approves."""
        rule = {"block_type": "routing_rule",
                "metadata": {"task_type": "summarize", "candidates": ["gemma3-4b"]}}
        pool = _pool("personal", [_row("draft", **rule), _row("pending_review", **rule)])
        with client_for(pool) as client:
            response = client.post("/v1/blocks", json={**_CREATE, **rule})

        assert response.status_code == 422, response.text
        assert "active" not in _status_changes(pool)


# ── Edit ──────────────────────────────────────────────────────────────────────

class TestEdit:
    def test_editing_a_live_block_in_a_direct_scope_publishes_it_live(self):
        pool = _pool("project", [_row("draft", version=2), _row("pending_review", version=2),
                                 _row("active", version=2)],
                     current=_row("active"))
        with client_for(pool) as client:
            response = client.patch(f"/v1/blocks/{_BLOCK}", json={"content": "y"})

        assert response.status_code == 201, response.text
        assert response.json()["status"] == "active"
        assert response.json()["version"] == 2
        assert [a for a, _ in _audit(pool)] == [ACTION_BLOCK_SUBMITTED, ACTION_BLOCK_APPROVED]

    def test_editing_a_live_block_in_a_strict_scope_leaves_a_draft(self):
        pool = _pool("team", [_row("draft", version=2)], current=_row("active"))
        with client_for(pool) as client:
            response = client.patch(f"/v1/blocks/{_BLOCK}", json={"content": "y"})

        assert response.status_code == 201
        assert response.json()["status"] == "draft"
        assert _status_changes(pool) == []

    def test_an_empty_edit_publishes_nothing(self):
        """Nothing written, nothing to activate — even where writing activates."""
        pool = _pool("personal", [], current=_row("draft"))
        with client_for(pool) as client:
            response = client.patch(f"/v1/blocks/{_BLOCK}", json={})

        assert response.status_code == 200
        assert response.json()["status"] == "draft"
        assert pool.conn.fetchrow.await_count == 0

    def test_a_claim_may_ride_along_in_a_direct_scope(self):
        """The claim is frozen outside draft, and a direct scope never leaves
        a draft behind — refusing the ride-along would freeze it for good."""
        claim = {"governance_key": _KEY, "privacy_impact": "restrictive"}
        pool = _pool("personal", [_row("draft", version=2), _row("draft", version=2, **claim),
                                  _row("pending_review", version=2, **claim),
                                  _row("active", version=2, **claim)],
                     current=_row("active"), conn_fetch=_chain_and_claims([]))
        with client_for(pool) as client:
            response = client.patch(f"/v1/blocks/{_BLOCK}", json={"content": "y", **claim})

        assert response.status_code == 201, response.text
        assert response.json()["governance_key"] == _KEY
        sql = [c.args[0] for c in pool.conn.fetchrow.call_args_list]
        # The claim lands on the new draft, before the activation judges it.
        assert "cc_supersede_block" in sql[0]
        assert "governance_key" in sql[1] and sql[1].startswith("UPDATE context_blocks SET")

    def test_a_claim_still_may_not_ride_along_in_a_strict_scope(self):
        """Two decisions a reviewer should see as two (CHY-62)."""
        pool = _pool("team", [_row("draft", version=2)], current=_row("active"))
        with client_for(pool) as client:
            response = client.patch(f"/v1/blocks/{_BLOCK}", json={"content": "y", "locked": True})

        assert response.status_code == 409, response.text
        assert "locked" in response.json()["detail"]
        assert pool.conn.fetchrow.await_count == 0
