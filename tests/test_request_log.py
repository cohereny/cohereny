"""
The application log, the request line and what an error tells the caller
(CHY-97).

Driven through the real application where the claim is about the application:
a test that rebuilt the middleware stack itself would prove the stack it built.
"""
from __future__ import annotations

import contextvars
import json
import logging
import sys
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from api import deps
from api.deps import CurrentUser, require_auth
from api.main import app
from api.request_log import REQUEST_ID_HEADER, configure_logging
from core import app_log
from core.db import get_pool, get_system_pool
from core.redis import get_redis
from kernel import current_call
from kernel.identity import resolve_actor
from tests.conftest import actor_row, as_caller, client_for, make_pool, make_redis, stub_user

#: Where the inside of the installation shows in an error. A host name, not an
#: address: the leak scan of the public snapshot would rightly stop a private
#: address in a public file.
INSIDE = "gpu-box.lan:11434"


def _fresh(fn, *args):
    """Run `fn` in a copy of the context, so a call it opens ends with it.

    Opening a call in the test's own context would leave it open for every
    test after this one in the same thread.
    """
    return contextvars.copy_context().run(fn, *args)


def _request_lines(caplog) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == "cohereny.request"]


# ── the call ──────────────────────────────────────────────────────────────────

class TestTheCall:
    def test_outside_a_call_nothing_is_added_and_nothing_fails(self):
        def outside():
            current_call.note_actor(uuid4())
            return current_call.current(), current_call.with_reference("internal error")

        assert _fresh(outside) == (None, "internal error")

    def test_inside_a_call_a_text_carries_its_id(self):
        def inside():
            current_call.begin("abc123")
            return current_call.with_reference("internal error")

        assert _fresh(inside) == "internal error (request ID abc123)"

    def test_the_actor_is_noted_on_the_open_call(self):
        actor = uuid4()

        def inside():
            call = current_call.begin("abc123")
            current_call.note_actor(actor)
            return call.actor_id

        assert _fresh(inside) == actor

    async def test_resolving_an_actor_notes_it(self):
        """The one place a request learns who it is. Async, so the test runs in
        a task with its own copy of the context."""
        row = actor_row(role="contributor")
        pool = MagicMock()
        pool.fetchrow = AsyncMock(return_value=row)
        call = current_call.begin("abc123")

        await resolve_actor(pool, CurrentUser(sub="someone@example.com", username="someone"))

        assert call.actor_id == row["id"]


# ── the line ──────────────────────────────────────────────────────────────────

class TestTheFormat:
    @staticmethod
    def _line(**kwargs) -> dict:
        record = logging.getLogger("t").makeRecord(
            "t", kwargs.pop("level", logging.WARNING), "f.py", 1,
            kwargs.pop("msg", "model call refused"), (), kwargs.pop("exc_info", None),
            extra=kwargs,
        )
        return json.loads(app_log.JsonFormatter().format(record))

    def test_a_line_has_four_fields_and_every_extra_under_its_own_name(self):
        scope = uuid4()
        line = self._line(model="m", scope_id=scope)
        assert line["message"] == "model call refused"
        assert (line["level"], line["logger"]) == ("WARNING", "t")
        assert line["ts"].endswith("+00:00")
        assert line["model"] == "m"
        # default=str: a UUID is written, not refused
        assert line["scope_id"] == str(scope)

    def test_none_of_the_records_own_attributes_becomes_a_field(self):
        line = self._line()
        assert set(line) == {"ts", "level", "logger", "message"}

    def test_the_servers_coloured_copy_of_its_message_is_dropped(self):
        line = self._line(color_message="Uvicorn running on \x1b[1m%s\x1b[0m")
        assert "color_message" not in line

    def test_an_exception_comes_with_its_traceback(self):
        try:
            raise ValueError("broken")
        except ValueError:
            info = sys.exc_info()
        line = self._line(exc_info=info)
        assert "ValueError: broken" in line["exception"]
        assert "Traceback" in line["exception"]


class TestInstall:
    @pytest.fixture
    def root(self):
        """The root logger, put back as it was: handlers, level, and uvicorn's."""
        root = logging.getLogger()
        server = {n: logging.getLogger(n) for n in ("uvicorn", "uvicorn.error", "uvicorn.access")}
        saved = (list(root.handlers), root.level,
                 {n: (list(lg.handlers), lg.propagate, lg.disabled) for n, lg in server.items()})
        yield root
        root.handlers[:] = saved[0]
        root.setLevel(saved[1])
        for n, (handlers, propagate, disabled) in saved[2].items():
            server[n].handlers[:] = handlers
            server[n].propagate, server[n].disabled = propagate, disabled

    def test_installing_twice_writes_each_line_once(self, root):
        foreign = list(root.handlers)
        app_log.install("INFO")
        app_log.install("WARNING")
        ours = [h for h in root.handlers if h.get_name() == app_log.HANDLER_NAME]
        assert len(ours) == 1
        assert root.level == logging.WARNING
        # pytest's own capture is one of these; it must survive
        assert all(h in root.handlers for h in foreign)

    def test_the_server_writes_through_the_app_log_and_its_access_line_is_off(self, root):
        logging.getLogger("uvicorn").addHandler(logging.StreamHandler())
        configure_logging("INFO")
        assert logging.getLogger("uvicorn").handlers == []
        assert logging.getLogger("uvicorn").propagate is True
        assert logging.getLogger("uvicorn.access").disabled is True
        [ours] = [h for h in root.handlers if h.get_name() == app_log.HANDLER_NAME]
        assert any(type(f).__name__ == "CallContextFilter" for f in ours.filters)


# ── the request line, through the real application ───────────────────────────

class TestTheRequestLine:
    def test_without_a_token_the_line_has_route_status_and_no_query(self, caplog):
        run_id = uuid4()
        with caplog.at_level(logging.DEBUG, logger="cohereny.request"):
            response = TestClient(app).get(f"/v1/workflows/runs/{run_id}?note=secret")
        assert response.status_code == 401
        [line] = _request_lines(caplog)
        assert line.route == "/v1/workflows/runs/{run_id}"
        assert (line.method, line.status) == ("GET", 401)
        assert line.duration_ms >= 0
        assert line.request_id == response.headers[REQUEST_ID_HEADER]
        written = json.dumps({k: str(v) for k, v in vars(line).items()})
        assert str(run_id) not in written and "secret" not in written
        assert not hasattr(line, "actor_id")

    def test_each_request_gets_its_own_id(self):
        client = TestClient(app)
        first = client.get("/v1/health").headers[REQUEST_ID_HEADER]
        second = client.get("/v1/health").headers[REQUEST_ID_HEADER]
        assert first != second

    def test_a_signed_in_request_names_its_actor_by_uuid(self, caplog):
        """Counter-probe to the line above: with an actor, it is there."""
        with client_for(make_pool()) as client, caplog.at_level(logging.INFO, logger="cohereny.request"):
            response = client.get("/v1/me")
        assert response.status_code == 200, response.text
        [line] = _request_lines(caplog)
        assert line.actor_id == response.json()["actor_id"]
        assert line.route == "/v1/me"

    def test_health_checks_are_written_at_debug(self, caplog):
        with caplog.at_level(logging.DEBUG, logger="cohereny.request"):
            TestClient(app).get("/v1/health")
            TestClient(app).get("/v1/me")
        levels = {line.route: line.levelno for line in _request_lines(caplog)}
        assert levels == {"/v1/health": logging.DEBUG, "/v1/me": logging.INFO}

    def test_an_address_no_route_matched_is_not_written(self, caplog):
        with caplog.at_level(logging.INFO, logger="cohereny.request"):
            response = TestClient(app).get("/wp-admin/setup-config.php")
        assert response.status_code == 404
        [line] = _request_lines(caplog)
        assert line.route == "unmatched"
        assert "wp-admin" not in json.dumps({k: str(v) for k, v in vars(line).items()})


# ── what an error tells the caller ────────────────────────────────────────────

class TestAnUnexpectedError:
    @pytest.fixture
    def failing_client(self):
        """The real application, with a database that fails in a way nothing maps."""
        pool = MagicMock()
        pool.fetchrow = AsyncMock(side_effect=RuntimeError(f"could not reach {INSIDE}"))
        app.dependency_overrides[require_auth] = as_caller(stub_user())
        app.dependency_overrides[get_pool] = lambda: pool
        app.dependency_overrides[get_system_pool] = lambda: pool
        app.dependency_overrides[get_redis] = make_redis
        try:
            yield TestClient(app, raise_server_exceptions=False)
        finally:
            for dep in (require_auth, get_pool, get_system_pool, get_redis):
                app.dependency_overrides.pop(dep, None)

    def test_the_answer_is_a_500_with_its_id_and_nothing_else(self, failing_client, caplog):
        with caplog.at_level(logging.INFO, logger="cohereny.request"):
            response = failing_client.get("/v1/me")
        assert response.status_code == 500
        request_id = response.headers[REQUEST_ID_HEADER]
        assert response.json() == {"detail": f"internal error (request ID {request_id})"}
        assert "gpu-box" not in response.text
        [line] = _request_lines(caplog)
        assert (line.status, line.request_id) == (500, request_id)


class TestTheIdentityProviderUnreachable:
    async def test_the_caller_gets_the_id_and_the_log_the_address(self, caplog):
        issuer = f"https://{uuid4().hex}.example/application/o/cohereny-author/"
        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        client.get = AsyncMock(side_effect=httpx.ConnectError(f"cannot connect to {INSIDE}"))
        current_call.begin("abc123")

        with (
            patch.object(deps.httpx, "AsyncClient", return_value=client),
            caplog.at_level(logging.WARNING, logger="api.deps"),
            pytest.raises(HTTPException) as refused,
        ):
            await deps._fetch_jwks(issuer)

        assert refused.value.status_code == 503
        assert refused.value.detail == (
            "the identity provider's signing keys could not be fetched (request ID abc123)"
        )
        [line] = [r for r in caplog.records if r.name == "api.deps"]
        assert INSIDE in line.detail
        assert line.jwks_url.startswith(issuer)


class TestTheTemplate:
    """The route without its values, from public data only."""

    @staticmethod
    def _scope(path: str, params: dict) -> dict:
        return {"route": object(), "path": path, "path_params": params}

    def test_each_value_becomes_its_name(self):
        from api.request_log import route_template

        run, doc = uuid4(), "kontoauszug"
        scope = self._scope(f"/v1/workflows/runs/{run}/documents/{doc}",
                            {"run_id": run, "name": doc})
        assert route_template(scope) == "/v1/workflows/runs/{run_id}/documents/{name}"

    def test_a_value_with_a_slash_falls_back_to_the_routes_own_part(self):
        """No route takes one today. If one does, the value still stays out."""
        from types import SimpleNamespace

        from api.request_log import route_template

        scope = {"route": SimpleNamespace(path="/files/{rest}"),
                 "path": "/v1/files/a/b", "path_params": {"rest": "a/b"}}
        assert route_template(scope) == "/files/{rest}"

    def test_nothing_matched_is_none(self):
        from api.request_log import route_template

        assert route_template({"path": "/x", "path_params": {}}) is None
