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

Called by:
  - api.main      (create_pool / close_pool in lifespan)
  - api/v1/*.py   (get_pool in route handlers, once implemented)

Dependencies:
  - asyncpg           (native async PostgreSQL driver)
  - api.config        (settings.database_url)
"""
import json as _json
import asyncpg
from api.config import settings

_pool: asyncpg.Pool | None = None


async def _register_codecs(conn: asyncpg.Connection) -> None:
    """Register JSON/JSONB type codecs so asyncpg returns dicts, not raw strings."""
    await conn.set_type_codec(
        "jsonb", encoder=_json.dumps, decoder=_json.loads, schema="pg_catalog"
    )
    await conn.set_type_codec(
        "json", encoder=_json.dumps, decoder=_json.loads, schema="pg_catalog"
    )


async def create_pool() -> None:
    """Create the global connection pool. Called once on app startup."""
    global _pool
    _pool = await asyncpg.create_pool(
        settings.database_url, min_size=2, max_size=10, init=_register_codecs
    )


async def close_pool() -> None:
    """Close the pool cleanly on shutdown (releases all connections)."""
    if _pool:
        await _pool.close()


async def get_pool() -> asyncpg.Pool:
    """Provide the global pool to route handlers. Raises RuntimeError if not initialized."""
    if _pool is None:
        raise RuntimeError("DB pool not initialized")
    return _pool
