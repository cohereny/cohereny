"""
tests/test_router.py

What this deployment can reach, and the /v1/route endpoint (CHY-87).

The task_type -> model table left this module in CHY-87 step 2b: which model
serves which task is a governance decision and lives in `context_blocks` as
`routing_rule` blocks (ADR-037). Reading those is covered in
`tests/test_routing_rules.py`.

What is covered here is the half that is not a decision — which models this
installation declares and what each may see — and the endpoint that puts the
two together.
"""
import tempfile
from pathlib import Path
from uuid import uuid4

import pytest
import yaml
from fastapi.testclient import TestClient

from api.main import app
from api.deps import require_auth, CurrentUser
from tests.conftest import client_for, make_pool
from router.config import RouterConfig, RoutingConfigError


def _declaration(models: dict) -> RouterConfig:
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        yaml.dump({"models": models}, f)
        return RouterConfig(config_path=Path(f.name))


_LOCAL_MODEL = {"runs_on": "local-raspberry-pi", "max_privacy_level": "critical"}


# --- the deployment declaration ---

def test_the_shipped_declaration_loads():
    config = RouterConfig()
    assert config.knows("gemma3-4b")
    assert config.privacy_rank("gemma3-4b") >= 0


def test_an_empty_declaration_does_not_load():
    """A deployment that reaches nothing cannot route, and a stored rule would
    have no model to name."""
    with pytest.raises(RoutingConfigError, match="no models"):
        _declaration({})


def test_an_unknown_privacy_level_raises_rather_than_sorting_last():
    """Treating it as least privileged would let a chain through a ratchet it
    was never measured against."""
    with pytest.raises(RoutingConfigError, match="max_privacy_level"):
        _declaration({"m": {"runs_on": "x", "max_privacy_level": "geheim"}})


def test_an_undeclared_model_has_no_rank(self=None):
    config = _declaration({"m": _LOCAL_MODEL})
    assert config.knows("m")
    assert not config.knows("gibtsnicht")
    with pytest.raises(RoutingConfigError, match="not declared"):
        config.privacy_rank("gibtsnicht")


# --- HTTP endpoint tests ---

# Stub auth: bypass OIDC validation in all HTTP tests below.
#
# Uses the shared client_for from conftest rather than setting the override and
# leaving it. The previous helper never removed it — four tests leaked
# require_auth into whatever ran next, which happened to be harmless only
# because the following modules do not use auth. The autouse guard in
# conftest.py now fails on that.
_stub_user = CurrentUser(sub="test-sub", username="testuser")


_SCOPE = uuid4()


def _routing_pool(rules=None):
    """A pool that answers the two queries a routing decision makes.

    Dispatched on the statement rather than returning one fixed list: the
    handler reads the ancestor chain and then the rules in it, and answering
    both the same way would be green for a reason that has nothing to do with
    routing.
    """
    def _fetch(sql, *args):
        if "scope_nodes" in sql:
            return [{"id": _SCOPE, "scope_path": "org", "node_type": "org",
                     "name": "org", "depth": 1, "privacy_level": None}]
        return rules if rules is not None else []

    return make_pool(fetch=_fetch)


def _rule(task_type, candidates, scope_id=None):
    return {
        "block_id": uuid4(), "version": 1, "scope_id": scope_id or _SCOPE,
        "content": "begründet", "metadata": {
            "task_type": task_type, "candidates": candidates, "max_tokens": 256,
        },
    }


def test_route_returns_the_stored_candidates():
    pool = _routing_pool([_rule("summarize", ["gemma3-4b", "phi3-mini"])])
    with client_for(pool, user=_stub_user) as client:
        resp = client.post("/v1/route", json={
            "task_type": "summarize", "scope_id": str(_SCOPE),
        })
    assert resp.status_code == 200
    data = resp.json()
    assert data["model"] == "gemma3-4b"
    assert data["candidates"] == ["gemma3-4b", "phi3-mini"]
    # A caller that asks the gateway itself needs the limit too (CHY-166).
    assert data["max_tokens"] == 256
    assert data["litellm_base_url"].startswith("http")


def test_route_without_a_scope_is_rejected_by_the_contract():
    """The scope became required in CHY-87 step 2b — a routing decision is
    always made *in* a scope, and the endpoint now says so."""
    with client_for(make_pool(), user=_stub_user) as client:
        resp = client.post("/v1/route", json={"task_type": "summarize"})
    assert resp.status_code == 422


def test_an_undecided_task_type_is_refused_not_defaulted():
    """The behaviour that replaced the YAML `default` route.

    An installation that has not decided which model serves a task should say
    so. Picking one would put a model into the trace that nobody chose, and it
    would look exactly like a decision.
    """
    pool = _routing_pool([_rule("summarize", ["gemma3-4b"])])
    with client_for(pool, user=_stub_user) as client:
        resp = client.post("/v1/route", json={
            "task_type": "gibtesnicht", "scope_id": str(_SCOPE),
        })
    assert resp.status_code == 409
    assert "gibtesnicht" in resp.json()["detail"]


def test_a_scope_that_does_not_exist_is_a_different_answer():
    """Counter-probe: 'no scope' and 'no rule' call for different fixes, so
    they must not collapse into one status code."""
    def _fetch(sql, *args):
        return []

    with client_for(make_pool(fetch=_fetch), user=_stub_user) as client:
        resp = client.post("/v1/route", json={
            "task_type": "summarize", "scope_id": str(uuid4()),
        })
    assert resp.status_code == 404


def test_route_requires_auth():
    """Without auth override, unauthenticated request must be rejected (403 or 401)."""
    app.dependency_overrides.pop(require_auth, None)
    # No Authorization header → require_auth raises HTTPException
    client = TestClient(app)
    resp = client.post("/v1/route", json={"task_type": "summarize"})
    assert resp.status_code in (401, 403)


# ── ROUTING_CONFIG (CHY-189) ──────────────────────────────────────────────────

class TestTheModelDeclarationIsConfigurable:
    """Which models a deployment can reach is its own file, named by a setting.

    Before CHY-189 the only declaration was the one bundled with the code, so
    running Cohereny against another gateway meant editing the source tree.
    """

    def test_empty_means_the_bundled_file(self):
        from api.v1._routing import load_router_config

        assert load_router_config("").privacy_rank("gemma3-4b") >= 0

    def test_a_path_replaces_the_bundled_declaration(self, tmp_path):
        from api.v1._routing import load_router_config
        from router.config import RoutingConfigError

        own = tmp_path / "models.yaml"
        own.write_text("models:\n  qwen2.5:1.5b:\n    runs_on: local\n"
                       "    max_privacy_level: critical\n", encoding="utf-8")
        config = load_router_config(str(own))
        assert config.privacy_rank("qwen2.5:1.5b") >= 0
        # Replaced, not merged: the reference models are unknown here.
        with pytest.raises(RoutingConfigError):
            config.privacy_rank("gemma3-4b")

    def test_a_missing_file_stops_rather_than_falling_back(self, tmp_path):
        """Falling back would route against models this deployment cannot reach."""
        from api.v1._routing import load_router_config

        with pytest.raises(FileNotFoundError):
            load_router_config(str(tmp_path / "absent.yaml"))


def test_every_caller_that_routes_reads_the_same_declaration():
    """/v1/route loaded its own declaration, always the bundled file (CHY-189).

    With ROUTING_CONFIG pointing elsewhere it then disagreed with /v1/complete
    and the workflow runner, and answered 500 for a model they served.
    """
    from api.v1 import _routing
    from api.v1.route import get_router_config

    assert get_router_config() is _routing.router_config
