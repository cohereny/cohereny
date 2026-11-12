"""
core/db.py

asyncpg connection pool — singleton for the entire application lifetime.

The pool is created once on app startup (api/main.py lifespan) and cleanly
closed on shutdown. Route handlers acquire connections via get_pool() and
return them automatically (pool semantics).

min_size=2: keeps two connections open at all times — reduces latency for
the first requests after a restart.
max_size=10: limits concurrent DB connections; prevents exhausting the
PostgreSQL connection limit of a small database host.

Two pools since CHY-92, on the same role:

  get_pool()          for request handlers. Knows nobody until a service says
                      for whom it reads (kernel.acting.acting_as) — the run
                      tables show nothing to a connection that does not say.
  get_system_pool()   for the workflow engine, which works for the installation.
                      Every connection it hands out is in the system context.
                      Small, and used by name only where the engine takes over
                      after a person's right to act was checked; who may use it
                      is held by tests/test_run_isolation.py.

Called by:
  - api.main      (create_pool / close_pool in lifespan)
  - api/v1/*.py   (get_pool; get_system_pool where a run is started or decided)

Dependencies:
  - asyncpg                 (native async PostgreSQL driver)
  - api.config              (settings.database_url)
  - kernel.least_privilege  (the check at start, CHY-85)
  - kernel.acting           (the system context, CHY-92)
"""
import json as _json
from types import SimpleNamespace

import asyncpg
from api.config import settings
from kernel import acting, least_privilege

_pool: asyncpg.Pool | None = None
#: The engine's pool. In a holder rather than behind a second `global`: one
#: module-level name rebound from a function is plenty.
_system = SimpleNamespace(pool=None)


async def _register_codecs(conn: asyncpg.Connection) -> None:
    """Register JSON/JSONB type codecs so asyncpg returns dicts, not raw strings."""
    await conn.set_type_codec(
        "jsonb", encoder=_json.dumps, decoder=_json.loads, schema="pg_catalog"
    )
    await conn.set_type_codec(
        "json", encoder=_json.dumps, decoder=_json.loads, schema="pg_catalog"
    )


async def create_pool() -> None:
    """Create the global connection pool. Called once on app startup.

    Refuses to start when the role may do more than the application needs —
    superuser, owner, BYPASSRLS, CREATE (kernel/least_privilege.py, CHY-85).
    """
    global _pool
    pool = await asyncpg.create_pool(
        settings.database_url, min_size=2, max_size=10, init=_register_codecs
    )
    try:
        async with pool.acquire() as conn:
            await least_privilege.check(conn)
    except least_privilege.ExcessPrivilegeError:
        await pool.close()
        raise
    # `setup` runs on every acquire, `init` only once per connection: the
    # pool's reset on release would clear a setting made in `init`.
    _system.pool = await asyncpg.create_pool(
        settings.database_url, min_size=1, max_size=4,
        init=_register_codecs, setup=acting.as_system,
    )
    _pool = pool


async def close_pool() -> None:
    """Close both pools cleanly on shutdown (releases all connections)."""
    if _pool:
        await _pool.close()
    if _system.pool:
        await _system.pool.close()


async def get_pool() -> asyncpg.Pool:
    """Provide the global pool to route handlers. Raises RuntimeError if not initialized."""
    if _pool is None:
        raise RuntimeError("DB pool not initialized")
    return _pool


async def get_system_pool() -> asyncpg.Pool:
    """The engine's pool, in the system context. Not for reading on a person's behalf."""
    if _system.pool is None:
        message = "DB pool not initialized"
        raise RuntimeError(message)
    return _system.pool
