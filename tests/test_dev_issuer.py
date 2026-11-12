"""scripts/dev_issuer.py stands in for Authentik, and only where it should (CHY-192).

It replaced DEV_MODE: instead of switching the token check off, development
gets real tokens from a local issuer. That is only worth something if those
tokens go through exactly the path Authentik's take — require_auth fetching
the JWKS over HTTP, the issuer and audience checks, the surface gate, the role
mapping. So the tests here drive the application with them rather than
inspecting the tokens.
"""
from __future__ import annotations

import threading

import httpx
import jwt
import pytest
from fastapi.testclient import TestClient

from api import deps
from api.config import settings
from api.main import app
from core.db import get_pool
from core.redis import get_redis
from kernel.identity import role_from_groups
from kernel.roles import GROUP_ROLE_MAP
from kernel.surfaces import ALL_AUDIENCES
from scripts import dev_issuer
from scripts.e2e_context_lifecycle import SurfaceTokens
from tests.conftest import actor_row, make_pool, make_redis


@pytest.fixture
def issuer(monkeypatch):
    """A running issuer on a free port, and the API pointed at it."""
    server = dev_issuer.serve("127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(settings, "oidc_base_url", server.issuer.base_url)
    deps._jwks_cache.clear()
    yield server.issuer
    server.shutdown()
    server.server_close()
    deps._jwks_cache.clear()


def _token(issuer, client_id: str, username: str = "admin") -> httpx.Response:
    return httpx.post(f"{issuer.base_url}/token/", data={
        "grant_type": "client_credentials", "client_id": client_id,
        "username": username, "password": "anything",
    })


def _bearer(issuer, client_id: str, username: str = "admin") -> dict:
    return {"Authorization": f"Bearer {_token(issuer, client_id, username).json()['access_token']}"}


@pytest.fixture
def api():
    """The application with only the database doubled — authentication is real."""
    app.dependency_overrides[get_pool] = lambda: make_pool(actor=actor_row(role="admin"), fetch=[])
    app.dependency_overrides[get_redis] = lambda: make_redis()
    yield TestClient(app)
    app.dependency_overrides.pop(get_pool, None)
    app.dependency_overrides.pop(get_redis, None)


class TestItsTokensTakeTheRealPath:
    def test_a_token_opens_the_surface_it_was_issued_for(self, issuer, api):
        resp = api.get("/v1/scopes", headers=_bearer(issuer, "cohereny.author.ci"))
        assert resp.status_code == 200, resp.text

    def test_the_gate_turns_away_a_token_for_another_surface(self, issuer, api):
        resp = api.get("/v1/scopes", headers=_bearer(issuer, "cohereny.serve.ci"))
        assert resp.status_code == 403
        assert "issued for" in resp.json()["detail"]

    def test_without_a_token_it_is_still_401(self, issuer, api):
        assert api.get("/v1/scopes").status_code == 401

    @pytest.mark.asyncio
    async def test_require_auth_reads_the_claims(self, issuer):
        token = _token(issuer, "cohereny.observe", "reviewer").json()["access_token"]
        user = await deps.require_auth(
            deps.HTTPAuthorizationCredentials(scheme="Bearer", credentials=token))
        assert user.sub == "dev-issuer:reviewer"
        assert user.audience == "cohereny.observe"
        assert role_from_groups(user.groups) == "reviewer"

    def test_a_key_from_another_start_is_refused(self, issuer):
        """A fresh key per start: no token outlives the process that signed it."""
        other = dev_issuer.Issuer(issuer.base_url)
        token = other.token("cohereny.author.ci", "admin")["access_token"]
        with pytest.raises(deps.HTTPException) as exc:
            deps._validate_token(token, issuer.jwks, issuer.issuer_for("cohereny-author-ci"))
        assert exc.value.status_code == 401

    def test_the_e2e_script_gets_its_tokens_unchanged(self, issuer):
        """Same request shape as Authentik's client_credentials (INFRA-122)."""
        tokens = SurfaceTokens(f"{issuer.base_url}/token/", ("cohereny-ci", "x"))
        assert tokens.token_for("author").count(".") == 2


class TestWhatItIssues:
    def test_every_client_of_adr_031_is_known(self):
        assert set(dev_issuer.CLIENTS) == set(ALL_AUDIENCES)

    @pytest.mark.parametrize("role", sorted(set(GROUP_ROLE_MAP.values())))
    def test_every_role_has_a_username(self, role):
        assert role_from_groups([dev_issuer.GROUPS[role]]) == role

    def test_the_ci_account_is_an_architect_as_in_the_reference_installation(self):
        assert role_from_groups([dev_issuer.GROUPS["cohereny-ci"]]) == "architect"

    def test_jwks_are_served_for_each_application_and_nothing_else(self, issuer):
        for slug in dev_issuer.CLIENTS.values():
            assert httpx.get(f"{issuer.base_url}/{slug}/jwks/").json() == issuer.jwks
        assert httpx.get(f"{issuer.base_url}/cohereny/jwks/").status_code == 404


class TestWhatItRefuses:
    def test_an_unknown_username(self, issuer):
        resp = _token(issuer, "cohereny.author.ci", "mallory")
        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_grant"

    def test_an_unknown_client(self, issuer):
        resp = _token(issuer, "cohereny.everything")
        assert resp.status_code == 400
        assert "unknown client_id" in resp.json()["error_description"]

    def test_any_grant_but_client_credentials(self, issuer):
        resp = httpx.post(f"{issuer.base_url}/token/", data={"grant_type": "password"})
        assert resp.json()["error"] == "unsupported_grant_type"


class TestThePublicUrl:
    """`iss` names where the API reaches the issuer, not where it listens (CHY-189)."""

    def test_without_it_the_listening_address_is_the_issuer(self):
        server = dev_issuer.serve("127.0.0.1", 0)
        try:
            port = server.server_address[1]
            assert server.issuer.base_url == f"http://127.0.0.1:{port}/application/o"
        finally:
            server.server_close()

    def test_with_it_tokens_name_the_public_address(self):
        """Listening on 0.0.0.0 in a container, reached as http://issuer:9010."""
        server = dev_issuer.serve("127.0.0.1", 0, public_url="http://issuer:9010/")
        try:
            token = server.issuer.token("cohereny.author.ci", "admin")["access_token"]
            iss = jwt.decode(token, options={"verify_signature": False})["iss"]
            assert iss == "http://issuer:9010/application/o/cohereny-author-ci/"
        finally:
            server.server_close()
