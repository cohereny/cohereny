"""
tests/test_promote.py

Promote: a block's live version, copied as a draft into a strict scope above
it (ADR-042 point 3, CHY-65).

What must hold:

  - the target is above the block and strict — not its own scope, not a
    sibling, not a direct scope, not a scope that does not exist
  - what is copied is the version in use, including its governance claim,
    and the copy is a draft in the target scope
  - the copy's first audit entry names where it came from
  - a block that never went live has nothing to promote

The pool is a double. That the original stays untouched on a real database —
the acceptance criterion — is in tests/integration/test_direct_scopes_db.py.
"""
from __future__ import annotations

import json
from uuid import uuid4

import pytest

from contexts.repository import block as rules
from contexts.repository.errors import PromotionTargetInvalid
from kernel.audit import ACTION_BLOCK_PROMOTED
from tests.conftest import actor_row, client_for, make_pool

_ORG, _TEAM, _PROJECT, _OTHER = uuid4(), uuid4(), uuid4(), uuid4()
_BLOCK = uuid4()
_KEY = "cloud_routing"

#: org → team → project, as kernel.scopes.ancestor_chain returns it.
_CHAIN = [
    {"id": _ORG, "node_type": "org", "depth": 1, "scope_path": "o", "name": "o",
     "privacy_level": None},
    {"id": _TEAM, "node_type": "team", "depth": 2, "scope_path": "o.t", "name": "t",
     "privacy_level": None},
    {"id": _PROJECT, "node_type": "project", "depth": 3, "scope_path": "o.t.p", "name": "p",
     "privacy_level": None},
]


# ── The rule ──────────────────────────────────────────────────────────────────

class TestWhereAPromotionMayGo:
    @pytest.mark.parametrize("target", [_TEAM, _ORG])
    def test_up_into_a_strict_scope(self, target):
        rules.check_promotion(_CHAIN, source_scope_id=_PROJECT, target_scope_id=target)

    @pytest.mark.parametrize(("target", "reason"), [
        (_PROJECT, "own scope"),
        (_OTHER, "not a scope above"),
    ])
    def test_not_sideways_and_not_onto_itself(self, target, reason):
        with pytest.raises(PromotionTargetInvalid, match=reason):
            rules.check_promotion(_CHAIN, source_scope_id=_PROJECT, target_scope_id=target)

    def test_not_down(self):
        """Seen from the team, the project is not in the chain at all."""
        with pytest.raises(PromotionTargetInvalid, match="not a scope above"):
            rules.check_promotion(_CHAIN[:2], source_scope_id=_TEAM, target_scope_id=_PROJECT)

    def test_not_into_a_direct_scope(self):
        """A draft would sit in a scope that never leaves one standing."""
        chain = [*_CHAIN, {"id": _OTHER, "node_type": "sub_project", "depth": 4,
                           "scope_path": "o.t.p.s", "name": "s", "privacy_level": None}]
        with pytest.raises(PromotionTargetInvalid, match="project scope activates on write"):
            rules.check_promotion(chain, source_scope_id=_OTHER, target_scope_id=_PROJECT)


# ── Through the endpoint ──────────────────────────────────────────────────────

def _row(scope_id, status: str, version: int = 1, block_id=_BLOCK, **kw) -> dict:
    return {
        "id": uuid4(), "block_id": block_id, "version": version,
        "superseded_by_version": None, "scope_id": scope_id,
        "block_type": "system_prompt", "content": kw.get("content", "proven"),
        "metadata": {"lang": "de"}, "tags": ["style"], "author_id": uuid4(),
        "status": status, "governance_key": kw.get("governance_key"),
        "privacy_impact": kw.get("privacy_impact"), "locked": kw.get("locked", False),
        "created_at": "2026-09-29T09:00:00+00:00",
    }


def _pool(*, current, live, inserted=None):
    """Answers, in order: the current version, the newest active one, the insert."""
    rows = [current, live] + ([inserted] if inserted is not None else [])
    return make_pool(actor=actor_row(role="contributor"), conn_fetchrow=rows,
                     conn_fetch=lambda sql, *a: _CHAIN if "scope_path @>" in sql else [])


def _post(pool, target=_TEAM):
    with client_for(pool) as client:
        return client.post(f"/v1/blocks/{_BLOCK}/promote",
                           json={"target_scope_id": str(target)})


def _insert_args(pool):
    return next(c.args for c in pool.conn.fetchrow.call_args_list
                if c.args[0].startswith("INSERT INTO context_blocks"))


class TestPromoting:
    def test_the_copy_is_a_draft_in_the_target_scope(self):
        new = uuid4()
        pool = _pool(current=_row(_PROJECT, "active"), live=_row(_PROJECT, "active"),
                     inserted=_row(_TEAM, "draft", block_id=new))
        response = _post(pool)

        assert response.status_code == 201, response.text
        body = response.json()
        assert (body["scope_id"], body["status"], body["block_id"]) == (str(_TEAM), "draft", str(new))

    def test_it_copies_the_version_in_use_with_its_claim(self):
        """Not the current version: that may be a draft in review, while the
        one before it is what the project actually composes."""
        claim = {"governance_key": _KEY, "privacy_impact": "restrictive", "locked": True}
        pool = _pool(current=_row(_PROJECT, "draft", version=3, content="unreviewed"),
                     live=_row(_PROJECT, "active", version=2, content="proven", **claim),
                     inserted=_row(_TEAM, "draft", block_id=uuid4(), **claim))
        _post(pool)

        args = _insert_args(pool)
        assert args[3] == _TEAM                      # scope_id
        assert args[5] == "proven"                   # content of the live version
        assert json.loads(args[6]) == {"lang": "de"}  # metadata, carried over
        assert args[7] == ["style"]
        assert args[9:12] == (_KEY, "restrictive", True)
        assert args[2] != _BLOCK, "the copy must be a new line, not a version of the old one"

    def test_its_first_audit_entry_names_the_origin(self):
        new = uuid4()
        pool = _pool(current=_row(_PROJECT, "active", version=2),
                     live=_row(_PROJECT, "active", version=2),
                     inserted=_row(_TEAM, "draft", block_id=new))
        _post(pool)

        (call,) = [c for c in pool.conn.fetchval.call_args_list if "audit_log" in c.args[0]]
        assert call.args[2] == ACTION_BLOCK_PROMOTED
        assert call.args[3] == f"block:{new}"
        assert json.loads(call.args[4]) == {
            "from_block_id": str(_BLOCK), "from_version": 2,
            "from_scope_id": str(_PROJECT), "to_scope_id": str(_TEAM),
        }

    def test_a_block_that_never_went_live_has_nothing_to_promote(self):
        pool = _pool(current=_row(_PROJECT, "draft"), live=None)
        response = _post(pool)

        assert response.status_code == 409, response.text
        assert "draft" in response.json()["detail"]
        assert not any(c.args[0].startswith("INSERT") for c in pool.conn.fetchrow.call_args_list)

    def test_a_wrong_target_is_refused_before_anything_is_written(self):
        pool = _pool(current=_row(_PROJECT, "active"), live=_row(_PROJECT, "active"))
        response = _post(pool, target=_OTHER)

        assert response.status_code == 422, response.text
        assert "not a scope above" in response.json()["detail"]
        assert not any(c.args[0].startswith("INSERT") for c in pool.conn.fetchrow.call_args_list)

    def test_an_unknown_block_is_404(self):
        response = _post(_pool(current=None, live=None))
        assert response.status_code == 404

    def test_a_consumer_may_not(self):
        pool = make_pool(actor=actor_row(role="consumer"))
        response = _post(pool)
        assert response.status_code == 403
        assert pool.conn.fetchrow.await_count == 0
