"""
tests/test_e2e_surface_tokens.py

The E2E carries one token per surface and picks it by path (CHY-124, ADR-031).

What must hold, and why each is a test rather than a hope:

  - every operation of openapi.yaml resolves to exactly one surface — a path the
    per-surface contracts miss would go out without a token and fail as a 401
    that looks like an Authentik problem
  - /v1/health gets no token, because it must not need one
  - the token comes from the surface's own machine client, `cohereny.<s>.ci`
  - a refused token request stops the run with a hint, and without the password
  - a path in two contracts stops the run instead of guessing

No network: the token endpoint and the clock are doubles.
"""
from __future__ import annotations

import re
from pathlib import Path

import httpx
import pytest
import yaml

from kernel.surfaces import CI_AUDIENCES
from scripts.e2e_context_lifecycle import COMPLETE_TIMEOUT_S, SurfaceTokens

_ROOT = Path(__file__).resolve().parents[1]


class _Endpoint:
    """Stands in for Authentik's token endpoint. Issues `tok-<client_id>-<n>`."""

    def __init__(self, status: int = 200, expires_in: int = 600) -> None:
        self.calls: list[dict] = []
        self._status = status
        self._expires_in = expires_in

    def __call__(self, url: str, data: dict, timeout: float) -> httpx.Response:
        self.calls.append(data)
        if self._status != 200:
            return httpx.Response(self._status, json={"error": "invalid_grant"})
        return httpx.Response(200, json={
            "access_token": f"tok-{data['client_id']}-{len(self.calls)}",
            "expires_in": self._expires_in,
        })


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _tokens(endpoint=None, clock=None, contracts=None) -> SurfaceTokens:
    return SurfaceTokens("https://auth.example/token/", ("cohereny-ci", "s3cret-app-password"),
                         contracts=contracts, post=endpoint or _Endpoint(), clock=clock or _Clock())


def _concrete(template: str) -> str:
    return re.sub(r"\{[^/]+\}", "00000000-0000-0000-0000-000000000001", template)


class TestTheContractsDecide:
    def test_every_operation_has_exactly_one_surface(self):
        """Measured on 24.09.: 61 of 62 operations in one surface, /v1/health in none."""
        spec = yaml.safe_load((_ROOT / "openapi.yaml").read_text(encoding="utf-8"))
        tokens = _tokens()
        unowned = [
            (method.upper(), path)
            for path, ops in spec["paths"].items()
            for method in ops
            if path != "/v1/health" and tokens.surface_for(method, _concrete(path)) is None
        ]
        assert unowned == []

    def test_health_goes_out_without_a_token(self):
        endpoint = _Endpoint()
        request = httpx.Request("GET", "https://api.example/v1/health")
        _tokens(endpoint).authorize(request)
        assert "Authorization" not in request.headers
        assert endpoint.calls == []

    @pytest.mark.parametrize(("method", "path", "surface"), [
        ("POST", "/v1/compose", "serve"),
        ("POST", "/v1/blocks", "author"),
        ("POST", "/v1/blocks/00000000-0000-0000-0000-000000000001/approve", "author"),
        ("POST", "/v1/scopes", "author"),
        ("GET", "/v1/audit-log", "observe"),
    ])
    def test_the_calls_of_the_run_land_where_adr_031_puts_them(self, method, path, surface):
        assert _tokens().surface_for(method, path) == surface

    def test_a_path_in_two_contracts_stops_the_run(self, tmp_path):
        for surface in ("author", "observe"):
            (tmp_path / f"openapi-{surface}.yaml").write_text(
                yaml.safe_dump({"paths": {"/v1/blocks": {"get": {}}}}), encoding="utf-8")
        with pytest.raises(SystemExit, match="several surface contracts"):
            _tokens(contracts=tmp_path).surface_for("GET", "/v1/blocks")


class TestTheTokenIsTheSurfaces:
    def test_it_comes_from_the_surfaces_machine_client(self):
        endpoint = _Endpoint()
        request = httpx.Request("GET", "https://api.example/v1/audit-log")
        _tokens(endpoint).authorize(request)
        assert endpoint.calls[0]["client_id"] == CI_AUDIENCES["observe"] == "cohereny.observe.ci"
        assert endpoint.calls[0]["grant_type"] == "client_credentials"
        assert endpoint.calls[0]["username"] == "cohereny-ci"
        assert request.headers["Authorization"] == "Bearer tok-cohereny.observe.ci-1"

    def test_two_surfaces_get_two_tokens(self):
        tokens = _tokens()
        a = httpx.Request("POST", "https://api.example/v1/blocks")
        b = httpx.Request("POST", "https://api.example/v1/compose")
        tokens.authorize(a)
        tokens.authorize(b)
        assert a.headers["Authorization"] != b.headers["Authorization"]
        assert tokens.used == {"author", "serve"}

    def test_a_token_is_reused_while_it_is_fresh(self):
        endpoint, clock = _Endpoint(), _Clock()
        tokens = _tokens(endpoint, clock)
        tokens.token_for("author")
        clock.now += 500
        tokens.token_for("author")
        assert len(endpoint.calls) == 1

    def test_a_token_is_fetched_again_before_it_expires(self):
        """Ten minutes from Authentik, a minute of margin: at 541 s it is renewed."""
        endpoint, clock = _Endpoint(), _Clock()
        tokens = _tokens(endpoint, clock)
        first = tokens.token_for("author")
        clock.now += 541
        assert tokens.token_for("author") != first
        assert len(endpoint.calls) == 2


class TestARefusal:
    def test_stops_the_run_and_names_the_usual_causes(self):
        with pytest.raises(SystemExit) as exc:
            _tokens(_Endpoint(status=400)).token_for("serve")
        message = str(exc.value)
        assert "cohereny.serve.ci" in message
        assert "INFRA-122" in message
        assert "docker logs authentik-server" in message

    def test_does_not_print_the_password(self):
        with pytest.raises(SystemExit) as exc:
            _tokens(_Endpoint(status=400)).token_for("serve")
        assert "s3cret-app-password" not in str(exc.value)


def test_the_run_waits_longer_for_a_model_than_the_api_does():
    """The client gave up at 30 s while the API allowed a model 300 s (24.09.)."""
    from core.completion import REQUEST_TIMEOUT_SECONDS
    assert COMPLETE_TIMEOUT_S > REQUEST_TIMEOUT_SECONDS
