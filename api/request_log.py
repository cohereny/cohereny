"""
api/request_log.py

One line per request, and the request's id on every other line (CHY-97).

The request line holds exactly what was decided on 09.11.2026, and nothing
more:

  request_id    generated here, returned as X-Request-ID on every response
  method        GET, POST, ...
  route         the template, /v1/workflows/runs/{run_id}, never the path with
                its ids filled in, never the query string
  status        what was sent
  duration_ms   from the request reaching the application to the end of
                the response
  actor_id      the actor's UUID, once the request resolved one; pseudonymous,
                because only the actors table ties it to a person

No client address, no user agent, no body. uvicorn's own access line wrote the
address and the full path with its query string, so it is switched off; this
line replaces it.

The id is generated, never taken from the request. A caller that could choose
it could make its requests look like someone else's in the log.
"""
from __future__ import annotations

import logging
import time
from uuid import uuid4

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from core import app_log
from kernel import current_call

REQUEST_ID_HEADER = "X-Request-ID"

_log = logging.getLogger("cohereny.request")

#: Asked every few seconds by health checks and probes. At INFO they would be
#: most of the log and bury the requests a person made; at DEBUG they are there
#: for whoever needs them.
QUIET_ROUTES = frozenset({"/v1/health"})


def route_template(scope: Scope) -> str | None:
    """The matched route as a template, or None when nothing matched.

    Rebuilt from the path, segment by segment: each segment that holds a path
    parameter's value becomes the parameter's name. FastAPI 0.138 nests
    included routers, so the matched route knows only its own part of the path
    (/runs/{run_id}); the full template exists only in a private object of
    FastAPI's, and a log that broke on a framework update would break silently.

    A value with a slash cannot be found segment by segment. Then the route's
    own part is written instead: shorter, and never the value.
    """
    route = scope.get("route")
    if route is None:
        return None
    values = {str(value): name for name, value in scope.get("path_params", {}).items()}
    if any("/" in value for value in values):
        return getattr(route, "path", None)
    return "/".join(
        f"{{{values[segment]}}}" if segment in values else segment
        for segment in scope["path"].split("/")
    )


class CallContextFilter(logging.Filter):
    """Puts the open call's id and actor on every record that passes."""

    def filter(self, record: logging.LogRecord) -> bool:
        call = current_call.current()
        if call is not None:
            record.request_id = call.request_id
            if call.actor_id is not None:
                record.actor_id = str(call.actor_id)
        return True


class RequestLog:
    """ASGI middleware: opens the call, stamps the header, writes the line.

    Plain ASGI rather than BaseHTTPMiddleware, which runs the application in a
    task of its own: the call opened here would then not be the one the
    application sees.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        call = current_call.begin(uuid4().hex)
        started = time.perf_counter()
        # What goes out if the application raises before it answers: the
        # server's error handler then sends a 500 (see api/main.py).
        status = 500

        async def send_with_id(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                MutableHeaders(scope=message).append(REQUEST_ID_HEADER, call.request_id)
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        finally:
            route = route_template(scope)
            # The call's id and actor on the line itself, not only through the
            # filter: the line is the record of the request, and it must say
            # whose request it was wherever it is read.
            who = {"actor_id": str(call.actor_id)} if call.actor_id is not None else {}
            _log.log(
                logging.DEBUG if route in QUIET_ROUTES else logging.INFO,
                "request",
                extra={
                    "request_id": call.request_id,
                    **who,
                    "method": scope["method"],
                    # An address no route matched is not written: it is
                    # whatever a scanner tried, and the 404 says enough.
                    "route": route or "unmatched",
                    "status": status,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                },
            )


def configure_logging(level: str) -> None:
    """Route every log line, the server's included, through the app log."""
    app_log.install(level, CallContextFilter())
    # uvicorn gives its loggers handlers of their own that write plain text.
    # Without them the records reach the root logger and come out as JSON.
    for name in ("uvicorn", "uvicorn.error"):
        server_log = logging.getLogger(name)
        server_log.handlers.clear()
        server_log.propagate = True
    logging.getLogger("uvicorn.access").disabled = True
