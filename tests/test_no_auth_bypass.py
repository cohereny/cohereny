"""No path past the token check (CHY-192).

Until CHY-192 a switch, DEV_MODE, made require_auth answer every request as a
static admin. CHY-91 fenced it in — only with ENVIRONMENT=dev, never against a
_prod or _test database — and the fence held. The switch was still a path past
authentication that existed in every build, one misplaced .env file from
production, and on dev it hid three broken issuer configurations for weeks.

The acceptance criterion of CHY-192 is that no such path is left: a request
without a token is refused with 401, on every route but the ones that are
public on purpose. This file holds that over the whole application, so a route
added later is covered without anyone remembering to add it.

It also holds the replacements: the API docs on their own switch, the pilots'
metrics on theirs, and the guard that keeps scripts/dev_issuer.py — which hands
a token to anyone — on dev, as CHY-91 kept DEV_MODE there.
"""
from __future__ import annotations

import pathlib
import re
import sys

import pytest
from fastapi import HTTPException
from fastapi.routing import iter_route_contexts
from fastapi.testclient import TestClient
from pydantic import ValidationError

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from api.config import Settings  # noqa: E402
from api.deps import require_auth  # noqa: E402
from api.main import app  # noqa: E402
from tests.conftest import client_for, make_pool, stub_user  # noqa: E402

#: Operations that answer without a token, on purpose. A new one makes
#: test_the_public_operations_are_exactly_these fail until it is listed here:
#: answering anonymously has to be a decision, not an omission.
PUBLIC = {("GET", "/v1/health")}

_PARAM = re.compile(r"\{[^}]+\}")
_PLACEHOLDER = "00000000-0000-0000-0000-000000000001"


def _operations() -> list[tuple[str, str]]:
    """Every (method, path template) the application serves."""
    ops = {(m, c.path) for c in iter_route_contexts(app.routes)
           for m in (c.methods or ()) if m not in ("HEAD", "OPTIONS")}
    return sorted(ops)


def _call(client: TestClient, method: str, path: str):
    """One request to `path` with a placeholder for every path parameter."""
    return client.request(method, _PARAM.sub(_PLACEHOLDER, path))


OPERATIONS = _operations()
GUARDED = [op for op in OPERATIONS if op not in PUBLIC]


@pytest.fixture(autouse=True)
def _clean_environment(monkeypatch):
    """pydantic-settings reads os.environ even with _env_file=None."""
    for var in ("DEV_MODE", "ENVIRONMENT", "OIDC_BASE_URL", "API_DOCS", "PILOT_NODE_METRICS"):
        monkeypatch.delenv(var, raising=False)


def _settings(**kw) -> Settings:
    """Constructed without reading any .env — the test states the whole world."""
    return Settings(_env_file=None, **kw)


class TestNoRouteAnswersWithoutAToken:
    def test_the_walk_sees_the_application(self):
        """Fifty-six guarded operations on 04.11.2026. A walk that found none
        would pass every check below for the wrong reason."""
        assert len(GUARDED) >= 50, GUARDED

    def test_the_public_operations_are_exactly_these(self):
        """The other direction: every operation that answers without a token
        is listed in PUBLIC, and everything listed exists."""
        # Not as a context manager: that would run the lifespan and connect to
        # a database. Without a token nothing should get that far anyway.
        client = TestClient(app, raise_server_exceptions=False)
        anonymous = {op for op in OPERATIONS if _call(client, *op).status_code != 401}
        assert anonymous == PUBLIC

    @pytest.mark.parametrize("method,path", GUARDED, ids=[f"{m} {p}" for m, p in GUARDED])
    def test_without_a_token_the_answer_is_401(self, method, path):
        """The acceptance criterion of CHY-192, route by route."""
        client = TestClient(app, raise_server_exceptions=False)
        resp = _call(client, method, path)
        assert resp.status_code == 401, f"{method} {path}: {resp.status_code} {resp.text[:120]}"
        assert resp.headers.get("www-authenticate") == "Bearer"

    def test_the_check_would_notice_a_way_past_it(self):
        """Counter-probe: with a caller injected past the token check — what
        DEV_MODE did — the same walk finds no 401 anywhere.

        Without this, the route-by-route test could be passing because every
        request failed before authentication (a 404 for a wrong placeholder, a
        405 for a wrong method) and happened to be turned into 401 somewhere.
        """
        admin = stub_user(sub="bypass", groups=["chy-admins"])
        with client_for(make_pool(), user=admin) as client:
            still_401 = [op for op in GUARDED if _call(client, *op).status_code == 401]
        assert still_401 == []


class TestDevModeIsGone:
    def test_there_is_no_such_setting(self):
        assert "dev_mode" not in Settings.model_fields

    @pytest.mark.asyncio
    async def test_dev_mode_in_the_environment_changes_nothing(self, monkeypatch):
        """A stage whose environment still says DEV_MODE=true authenticates.

        Environment variables that name no setting are ignored, so a leftover
        does not stop the start; it just has no effect. Failing safe: the
        surprise is a 401, not an open API.
        """
        monkeypatch.setenv("DEV_MODE", "true")
        _settings()
        with pytest.raises(HTTPException) as exc:
            await require_auth(None)
        assert exc.value.status_code == 401

    def test_dev_mode_in_an_env_file_stops_the_start(self, tmp_path):
        """In a .env file an unknown key is refused (tests/test_env_example.py),
        so a template still carrying DEV_MODE is noticed at once."""
        env = tmp_path / ".env"
        env.write_text("DEV_MODE=true\n", encoding="utf-8")
        with pytest.raises(ValidationError, match="dev_mode"):
            Settings(_env_file=env)


class TestAPlainHttpIssuerIsForDevOnly:
    """The guard that keeps scripts/dev_issuer.py on dev (CHY-192).

    The issuer speaks plain http and does not check passwords: a deployment
    that trusts it has no authentication. The same exposure DEV_MODE had, so
    the same fence — ENVIRONMENT=dev — by the property it cannot hide.
    """

    _HTTP = "http://127.0.0.1:9010/application/o"

    def test_on_dev_it_is_accepted(self):
        assert _settings(environment="dev", oidc_base_url=self._HTTP).oidc_base_url == self._HTTP

    @pytest.mark.parametrize("stage", ["test", "prod"])
    def test_elsewhere_it_stops_the_start(self, stage):
        with pytest.raises(ValidationError, match="dev_issuer"):
            _settings(environment=stage, oidc_base_url=self._HTTP)

    def test_a_deployment_that_forgot_its_stage_is_treated_as_prod(self):
        """ENVIRONMENT defaults to prod; silence gets the strict reading."""
        with pytest.raises(ValidationError, match="ENVIRONMENT='prod'"):
            _settings(oidc_base_url=self._HTTP)

    def test_the_scheme_is_compared_without_case(self):
        with pytest.raises(ValidationError):
            _settings(environment="prod", oidc_base_url="HTTP://auth.example.com/application/o")

    @pytest.mark.parametrize("stage", ["dev", "test", "prod"])
    def test_https_is_accepted_everywhere(self, stage):
        _settings(environment=stage, oidc_base_url="https://auth.example.com/application/o")


class TestTheSwitchesThatReplacedIt:
    def test_api_docs_are_off_by_default(self):
        assert _settings().api_docs is False

    def test_pilot_node_metrics_are_off_by_default(self):
        assert _settings().pilot_node_metrics is False

    @pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
    def test_without_api_docs_nothing_is_served(self, path):
        """The test settings leave API_DOCS unset, so this is the default."""
        assert TestClient(app).get(path).status_code == 404
