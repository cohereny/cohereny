"""
tests/conftest.py

Shared test doubles for the FastAPI endpoints.

Every test module used to carry its own copy of the same three helpers — build a
MagicMock pool, override `require_auth` and `get_pool`, pop them again. That was
fine while a request issued exactly one query. It stops working the moment
`core.authz.resolve_actor` runs first: the endpoint's own `fetchrow` then
receives the actor row, or a `side_effect` list runs out and raises
`StopIteration` in the middle of a handler.

The pool double here dispatches instead of counting. A query against `actors`
is answered from `actor`; everything else comes from the queue the test set up.
Tests keep working when a handler gains or loses an actor lookup, which is
exactly what CHY-33 does across the whole API.

`fetchrow` stays an AsyncMock, so `call_args_list` and the usual assertions
still work. Use `domain_calls()` / `actor_calls()` to inspect one kind without
having to know how many of the other kind happened.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

from datetime import UTC, datetime

import pytest

from api.deps import CurrentUser, require_auth
from api.main import app
from core.db import get_pool
from core.redis import get_redis

# The one query core.authz.resolve_actor issues. Matching on a fragment rather
# than the whole string keeps the double working when the SELECT list changes,
# but still fails loudly if the lookup moves to a different table.
_ACTOR_LOOKUP = "FROM actors WHERE idp_sub"

DEFAULT_ACTOR_ID = UUID("00000000-0000-0000-0000-00000000ac70")

# Distinct from None: a test must be able to say "this caller has no actor row",
# which is itself None.
_UNSET = object()


def stub_user(sub: str = "test-sub", username: str = "tester", groups=None,
              audience: str | None = None) -> CurrentUser:
    """An authenticated caller.

    `groups=None` means the token carried no groups claim — the case that must
    stay distinguishable from an empty claim (see api/deps.py).

    `audience=None` means no token was presented, as in dev mode, and the
    surface gate stands aside (ADR-031). Tests that exercise the gate pass one
    explicitly; every other test keeps the old behaviour.
    """
    return CurrentUser(sub=sub, username=username, groups=groups, audience=audience)


def actor_row(actor_id: UUID | None = None, role: str = "admin") -> dict:
    return {"id": actor_id or DEFAULT_ACTOR_ID, "role": role}


class _Rows:
    """Queue that yields each configured row once, then keeps returning None.

    Deliberately not StopIteration: a handler that issues one more query than
    the test anticipated should see "no such row" and produce its own 404,
    rather than a traceback that says nothing about the endpoint.
    """

    def __init__(self, rows):
        self._rows = list(rows or [])
        self._i = 0

    def next(self):
        if self._i < len(self._rows):
            row = self._rows[self._i]
            self._i += 1
            return row
        return self._rows[-1] if self._rows and len(self._rows) == 1 else None


#: Substring identifying the pilot configuration read. Matched the same way as
#: the actor lookup: these are queries every handler issues on the way to the
#: thing under test, and a double that answers them from the ordered row list
#: makes every test depend on how many queries the handler happens to make.
_WORKFLOW_CONFIG_LOOKUP = "FROM workflow_configs"


def workflow_config_row(pilot_name: str = "study", **overrides) -> dict:
    """The configuration migration 017 seeds for a pilot."""
    return {
        "pilot_name": pilot_name,
        "version": 1,
        "model_map": {"small": "phi3-mini", "medium": "gemma4-e4b"},
        "params": {},
        "updated_at": datetime(2026, 7, 4, 12, 0, tzinfo=UTC),
        **overrides,
    }


def make_pool(
    *,
    actor: Any = _UNSET,
    fetchrow=None,
    fetch=None,
    fetchval: Any = 0,
    execute: str = "UPDATE 1",
    conn_fetchrow=None,
    conn_fetch=None,
    conn_fetchval: Any = 1,
    workflow_config: Any = _UNSET,
) -> MagicMock:
    """A pool double that answers actor lookups separately from domain queries.

    actor        row returned for the resolve_actor lookup. Pass None for
                 "caller has no actor row"; omit for a default admin.
    workflow_config
                 row returned for the pilot configuration lookup. Same shape of
                 default as `actor`: omit for the seeded study config, pass None
                 for "no row" — which is the missing-seed branch, not something
                 a test should reach by accident (CHY-42).
    fetchrow     one row, or a list consumed in order, for the handler's own
                 queries. A single row is returned for every call.
    fetch        rows for a set-returning query on the pool, or a callable
                 (sql, *args) -> rows when one handler issues several and they
                 must answer differently.
    conn_fetchrow rows for queries issued through `pool.acquire()`, i.e. inside
                 a transaction.
    conn_fetch   rows for a set-returning query inside a transaction. A
                 callable (sql, *args) -> rows when several such queries in one
                 handler need different answers.
    """
    actor_value = actor_row() if actor is _UNSET else actor
    config_value = workflow_config_row() if workflow_config is _UNSET else workflow_config
    rows = _Rows(fetchrow if isinstance(fetchrow, list) else [fetchrow] if fetchrow is not None else [])

    async def _fetchrow(sql, *args):
        if _ACTOR_LOOKUP in sql:
            return actor_value
        if _WORKFLOW_CONFIG_LOOKUP in sql:
            return config_value
        return rows.next()

    conn_rows = _Rows(
        conn_fetchrow if isinstance(conn_fetchrow, list)
        else [conn_fetchrow] if conn_fetchrow is not None else []
    )

    async def _conn_fetchrow(sql, *args):
        if _ACTOR_LOOKUP in sql:
            return actor_value
        return conn_rows.next()

    conn = MagicMock()
    conn.fetchrow = AsyncMock(side_effect=_conn_fetchrow)

    # Handlers that read a whole set inside the transaction — retiring a block
    # locks every version of the line, for instance.
    #
    # A callable may be passed instead of a list when one handler issues more
    # than one set-returning query and they must answer differently. The
    # privacy ratchet does: it reads the ancestor chain and then the governance
    # claims in it, and one fixed list would answer both with the same rows —
    # green for a reason that has nothing to do with the rule (CHY-62).
    async def _conn_fetch(sql, *args):
        if callable(conn_fetch):
            return list(conn_fetch(sql, *args))
        return list(conn_fetch or [])

    conn.fetch = AsyncMock(side_effect=_conn_fetch)
    conn.fetchval = AsyncMock(return_value=conn_fetchval)
    conn.execute = AsyncMock()
    conn.transaction = MagicMock(return_value=_AsyncCM())

    pool = MagicMock()
    pool.fetchrow = AsyncMock(side_effect=_fetchrow)
    async def _fetch(sql, *args):
        # A callable may be passed when one handler issues more than one
        # set-returning query and they must answer differently — the same
        # reason `conn_fetch` takes one. Routing does: it reads the ancestor
        # chain and then the rules in it, and one fixed list would answer both
        # with the same rows, green for a reason unrelated to the rule.
        if callable(fetch):
            return list(fetch(sql, *args))
        return list(fetch) if fetch is not None else []

    pool.fetch = AsyncMock(side_effect=_fetch)
    pool.fetchval = AsyncMock(return_value=fetchval)
    pool.execute = AsyncMock(return_value=execute)
    pool.acquire = MagicMock(return_value=_AsyncCM(conn))
    pool.conn = conn  # so tests can assert on what happened inside the transaction
    return pool


def make_redis() -> MagicMock:
    r = MagicMock()
    # delete() is awaited on the write path (CHY-42) and, unlike the reads, its
    # failure is deliberately not suppressed — a plain MagicMock here would
    # raise "can't be awaited" inside a suppress() somewhere and turn a real
    # assertion into a silently skipped one.
    r.delete = AsyncMock(return_value=1)
    r.get = AsyncMock(return_value=None)
    # One entry per key, as real MGET always answers — not a fixed [] (CHY-105).
    # The old double returned an empty list for any number of keys, so
    # read_scope_generations only worked because zip() truncated silently. That
    # made the double the reason a `strict=True` was impossible, and a test
    # helper that quietly rules out a correctness check is worse than no helper.
    r.mget = AsyncMock(side_effect=lambda keys: [None] * len(keys))
    r.set = AsyncMock()
    r.incr = AsyncMock(return_value=1)
    return r


class _AsyncCM:
    """Async context manager for pool.acquire() and conn.transaction()."""

    def __init__(self, value=None):
        self._value = value

    async def __aenter__(self):
        return self._value

    async def __aexit__(self, *exc):
        return False


def actor_calls(pool: MagicMock) -> list:
    """Every resolve_actor lookup, in order."""
    return [c for c in pool.fetchrow.call_args_list if _ACTOR_LOOKUP in c.args[0]]


def domain_calls(pool: MagicMock) -> list:
    """Every query that was not an actor lookup, in order.

    Use this instead of `call_args_list[-1]` — an assertion pinned to the last
    call silently starts inspecting an actor lookup as soon as a handler gains
    one.
    """
    return [c for c in pool.fetchrow.call_args_list if _ACTOR_LOOKUP not in c.args[0]]


@contextmanager
def client_for(pool, *, redis=None, user: CurrentUser | None = None):
    """TestClient with the dependencies overridden, cleaned up on exit.

    A context manager rather than a pair of functions: the previous
    try/finally-with-_cleanup convention had to be repeated in every test, and
    a forgotten `finally` leaks an override into whichever test runs next.
    """
    from fastapi.testclient import TestClient

    app.dependency_overrides[require_auth] = lambda: user or stub_user()
    app.dependency_overrides[get_pool] = lambda: pool
    app.dependency_overrides[get_redis] = lambda: redis if redis is not None else make_redis()
    try:
        yield TestClient(app)
    finally:
        for dep in (require_auth, get_pool, get_redis):
            app.dependency_overrides.pop(dep, None)


@pytest.fixture
def pool():
    return make_pool()


@pytest.fixture
def redis():
    return make_redis()


@pytest.fixture(autouse=True)
def _no_leaked_overrides():
    """Fail loudly if a test leaves a dependency override behind.

    Leaked overrides make the next test pass or fail for reasons that have
    nothing to do with it — the kind of failure that costs an afternoon.
    """
    yield
    leaked = [d.__name__ for d in app.dependency_overrides]
    app.dependency_overrides.clear()
    assert not leaked, f"test left dependency overrides behind: {leaked}"


__all__ = [
    "DEFAULT_ACTOR_ID",
    "actor_calls",
    "actor_row",
    "client_for",
    "domain_calls",
    "make_pool",
    "make_redis",
    "stub_user",
    "uuid4",
]
