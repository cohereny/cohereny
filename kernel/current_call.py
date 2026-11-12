"""
kernel/current_call.py

The call in progress: the id a transport gave it, and the actor it turned out
to be (CHY-97).

Two readers need these, and neither may ask the transport for them:

  the application log   puts both on every line, so a line written deep in a
                        context can be traced back to the request that caused it
  error texts           that leave the installation — a response, a failed run —
                        carry the id instead of the cause, so whoever reads one
                        has something to quote that the operator can look up

In the kernel because both ends are here or above it: identity resolves the
actor, and contexts write the failures. Nothing in it knows HTTP. A CLI command
or a scheduled job could open a call the same way; where none is open, nothing
is added and nothing fails.

A holder rather than two context variables. A value set inside a dependency
that FastAPI runs in its threadpool does not travel back out to the transport
that logs the request; an object both sides point at does (measured 09.11.2026,
FastAPI 0.138, Starlette 1.3.1).
"""
from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from uuid import UUID


@dataclass
class Call:
    """One call: the id that finds it in the log, and who made it once known."""

    request_id: str
    actor_id: UUID | None = None


_current: ContextVar[Call | None] = ContextVar("cohereny_current_call", default=None)


def begin(request_id: str) -> Call:
    """Open a call for everything that runs from here on in this context.

    Not reset when the call ends, deliberately. The transport's error handler
    and the server's own traceback line both run after the application has
    unwound, and both should still name the call. Nothing carries over to the
    next request: each runs in a task of its own, with its own copy of the
    context.
    """
    call = Call(request_id)
    _current.set(call)
    return call


def current() -> Call | None:
    """The open call, or None outside one: CLI, scheduled jobs, most tests."""
    return _current.get()


def note_actor(actor_id: UUID) -> None:
    """Record who the open call turned out to be. Outside a call, nothing."""
    call = _current.get()
    if call is not None:
        call.actor_id = actor_id


def with_reference(text: str) -> str:
    """`text`, followed by the call's id when a call is open.

    For error texts that leave the installation. The id is what the person
    reading one can quote, and what finds the details in the application log;
    the details themselves stay there.
    """
    call = _current.get()
    return f"{text} (request ID {call.request_id})" if call is not None else text
