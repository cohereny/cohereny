"""
tests/test_scope_data_class.py

The data class belongs to the scope (CHY-160, decided 2026-09-24).

Until CHY-157 every declared model was `critical`, and no routing rule could
send data anywhere a local model would not see it. Since then two models are
`public`, and three mechanisms — `check_chain`, the privacy ratchet, the human
approving a rule — all passed a rule [gemma4-cloud, ...] for a scope full of
critical data. None of them compared the class of the data with the ceiling of
the model.

What is tested here, and where
------------------------------
- `effective_privacy_level`: the strictest class on the chain, `critical` when
  nobody declared one
- a scope may not be created looser than its parent
- resolution refuses a rule whose candidates may not see the scope's data —
  through `/v1/route`, against the shipped `routing-config.yaml`
- approval refuses the same rule, inside the transition

What this file cannot see: the CHECK of migration 024 (the pool is a double).
It was probed against a real Postgres like 022 and 023.
"""
from __future__ import annotations

from uuid import uuid4

import pytest

from kernel.scopes import UNCLASSIFIED, effective_privacy_level
from router.config import RouterConfig
from router.errors import RouteExceedsScopeClass
from router.rules import check_ceiling
from tests.conftest import actor_row, client_for, make_pool, stub_user

_SCOPE = uuid4()
_CLOUD, _LOCAL = "gemma4-cloud", "gemma4-e4b"


def _chain(*levels):
    """Chain rows root first, each with its declared level (or None)."""
    return [
        {"id": uuid4() if i < len(levels) - 1 else _SCOPE, "scope_path": "x",
         "node_type": "org", "name": "x", "depth": i + 1, "privacy_level": level}
        for i, level in enumerate(levels)
    ]


# ── the effective class ───────────────────────────────────────────────────────

class TestEffectiveClass:
    def test_nobody_declared_means_critical(self):
        """The safe direction. Forgetting to classify must not open anything."""
        assert UNCLASSIFIED == "critical"
        assert effective_privacy_level(_chain(None, None)) == "critical"

    def test_a_declared_class_applies_to_the_scope_below(self):
        assert effective_privacy_level(_chain("public", None)) == "public"

    def test_the_strictest_on_the_chain_wins_not_the_deepest(self):
        """A project cannot relax what its organisation declared."""
        assert effective_privacy_level(_chain("confidential", "public")) == "confidential"
        assert effective_privacy_level(_chain("public", "confidential")) == "confidential"


# ── the ceiling check ─────────────────────────────────────────────────────────

class TestCeiling:
    """Against the SHIPPED declaration: gemma4-cloud is public since CHY-157."""

    def test_a_cloud_model_may_not_see_critical_data(self):
        with pytest.raises(RouteExceedsScopeClass) as refused:
            check_ceiling((_CLOUD, _LOCAL), "critical", RouterConfig(), _SCOPE)
        assert refused.value.too_open == [_CLOUD]

    def test_every_candidate_is_checked_not_only_the_first(self):
        """A fallback receives the same request. A local first candidate does
        not make a cloud second one safe."""
        with pytest.raises(RouteExceedsScopeClass) as refused:
            check_ceiling((_LOCAL, _CLOUD), "internal", RouterConfig(), _SCOPE)
        assert refused.value.too_open == [_CLOUD]

    def test_public_data_may_go_to_the_cloud(self):
        """Counter-probe: the check must not refuse everything hosted."""
        check_ceiling((_CLOUD, _LOCAL), "public", RouterConfig(), _SCOPE)

    def test_a_local_model_may_see_anything(self):
        check_ceiling((_LOCAL,), "critical", RouterConfig(), _SCOPE)


# ── resolution, through the API ───────────────────────────────────────────────

def _routing_pool(chain, candidates):
    rule = {"block_id": uuid4(), "version": 1, "scope_id": _SCOPE,
            "content": "so entschieden",
            "metadata": {"task_type": "summarize", "candidates": candidates,
                         "max_tokens": 256}}

    def _fetch(sql, *_args):
        if "scope_nodes" in sql:
            return chain
        return [rule]

    return make_pool(fetch=_fetch)


def _route(pool):
    with client_for(pool, user=stub_user(groups=["chy-consumers"])) as client:
        return client.post("/v1/route", json={"task_type": "summarize",
                                               "scope_id": str(_SCOPE)})


class TestResolution:
    def test_a_cloud_rule_in_an_unclassified_scope_is_refused(self):
        """The case CHY-160 was filed for. `check_chain` passes it — cloud
        first, local behind is not a descent — and until now nothing else
        looked."""
        response = _route(_routing_pool(_chain(None), [_CLOUD, _LOCAL]))
        assert response.status_code == 409, response.text
        assert "critical" in response.json()["detail"]
        assert _CLOUD in response.json()["detail"]

    def test_the_same_rule_in_a_public_scope_resolves(self):
        """Counter-probe: classification is what decides, not the model."""
        response = _route(_routing_pool(_chain("public"), [_CLOUD, _LOCAL]))
        assert response.status_code == 200, response.text
        assert response.json()["candidates"] == [_CLOUD, _LOCAL]

    def test_a_parent_declaring_confidential_closes_the_cloud_below_it(self):
        response = _route(_routing_pool(_chain("confidential", None), [_CLOUD, _LOCAL]))
        assert response.status_code == 409, response.text


# ── scope creation ────────────────────────────────────────────────────────────

class TestScopeCreation:
    def _create(self, parent_chain, level):
        parent = uuid4()
        pool = make_pool(actor=actor_row(role="architect"),
                         conn_fetch=lambda sql, *a: parent_chain)
        pool.conn.fetchval.return_value = "org_x"
        with client_for(pool) as client:
            return client.post("/v1/scopes", json={
                "name": "p", "node_type": "project",
                "parent_id": str(parent), "privacy_level": level,
            }), pool

    def test_a_child_looser_than_its_parent_is_refused(self):
        response, pool = self._create(_chain("confidential"), "public")
        assert response.status_code == 422, response.text
        assert "confidential" in response.json()["detail"]
        inserted = [c for c in pool.conn.fetchrow.call_args_list
                    if "INSERT INTO scope_nodes" in c.args[0]]
        assert inserted == [], "the looser scope was written anyway"

    def test_a_child_looser_than_an_unclassified_parent_is_refused_too(self):
        """Unclassified counts as critical — so a child cannot open the cloud
        underneath a parent nobody classified. Somebody has to decide at the
        top first."""
        response, _ = self._create(_chain(None), "public")
        assert response.status_code == 422, response.text

    def test_a_child_may_be_stricter(self):
        """Counter-probe."""
        row = {"id": uuid4(), "scope_path": "org_x.project_y", "node_type": "project",
               "name": "p", "description": None, "parent_id": uuid4(),
               "privacy_level": "critical", "created_by": uuid4(),
               "created_at": "2026-09-24T12:00:00+00:00"}
        parent = uuid4()
        pool = make_pool(actor=actor_row(role="architect"),
                         conn_fetch=lambda sql, *a: _chain("internal"),
                         conn_fetchrow=[row])
        pool.conn.fetchval.return_value = "org_x"
        with client_for(pool) as client:
            response = client.post("/v1/scopes", json={
                "name": "p", "node_type": "project",
                "parent_id": str(parent), "privacy_level": "critical",
            })
        assert response.status_code == 201, response.text
        assert response.json()["privacy_level"] == "critical"


# ── approval, inside the transition ───────────────────────────────────────────

class TestApproval:
    """The first enforcement point: a person is standing there (CHY-160).

    Run as `before_activation` inside the repository's transition, on the
    locked row — the second read an earlier draft of this check made would
    have judged a row that might not be the one approved.
    """

    def _approve(self, candidates, chain, max_tokens=256):
        block_id = uuid4()
        locked = {
            "id": uuid4(), "block_id": block_id, "version": 1,
            "status": "pending_review", "scope_id": _SCOPE, "author_id": uuid4(),
            "governance_key": None, "privacy_impact": None, "locked": False,
            "block_type": "routing_rule",
            "metadata": {"task_type": "summarize", "candidates": candidates,
                         "max_tokens": max_tokens},
        }
        if max_tokens is None:
            del locked["metadata"]["max_tokens"]
        updated = {**locked, "status": "active", "superseded_by_version": None,
                   "content": "so entschieden", "tags": [],
                   "created_at": "2026-09-24T12:00:00+00:00"}
        pool = make_pool(actor=actor_row(role="reviewer"),
                         fetchrow={"author_id": locked["author_id"]},
                         conn_fetchrow=[locked, updated],
                         conn_fetch=lambda sql, *a: chain if "scope_nodes" in sql else [])
        with client_for(pool) as client:
            response = client.post(f"/v1/blocks/{block_id}/approve", json={"version": 1})
        return response, pool

    def _status_written(self, pool) -> bool:
        return any("UPDATE context_blocks" in c.args[0] and "status" in c.args[0]
                   for c in pool.conn.fetchrow.call_args_list)

    def test_a_cloud_rule_in_an_unclassified_scope_is_not_approved(self):
        response, pool = self._approve([_CLOUD, _LOCAL], _chain(None))
        assert response.status_code == 409, response.text
        assert _CLOUD in response.json()["detail"]
        assert not self._status_written(pool), "the rule went active anyway"

    def test_a_local_rule_is_approved(self):
        """Counter-probe: the hook must not refuse every routing rule."""
        response, pool = self._approve([_LOCAL], _chain(None))
        assert response.status_code == 200, response.text
        assert self._status_written(pool)

    def test_a_cloud_rule_in_a_public_scope_is_approved(self):
        response, _ = self._approve([_CLOUD, _LOCAL], _chain("public"))
        assert response.status_code == 200, response.text

    def test_a_rule_without_a_length_limit_is_refused_as_the_approvers_to_fix(self):
        """422, not 500 (CHY-166): the rule is incomplete, nothing broke."""
        response, pool = self._approve([_LOCAL], _chain(None), max_tokens=None)
        assert response.status_code == 422, response.text
        assert "max_tokens" in response.json()["detail"]
        assert not self._status_written(pool), "the rule went active anyway"

    def test_a_descending_chain_is_refused_as_the_approvers_to_fix(self):
        """The same reading, the same answer. Before CHY-166 this was a 500 at
        approval — the reviewer was told the server had failed."""
        response, pool = self._approve([_LOCAL, _CLOUD], _chain("public"))
        assert response.status_code == 422, response.text
        assert "falls back" in response.json()["detail"]
        assert not self._status_written(pool)
