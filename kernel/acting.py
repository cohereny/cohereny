"""
kernel/acting.py

Who the database works for: the person behind a request, or the installation (CHY-92).

Row-level security on the run tables (migration 035) reads two session settings.
This module is the only place that sets them:

  acting_as(pool, actor_id)   a connection for one person's request; the run
                              tables show what that person may see
  as_system(conn)             the engine, the pilot threads and the purge, which
                              work for the installation and see every run

With neither set, those tables show nothing and refuse every write. That is the
point of putting the rule into the database: a read path that forgets to say
for whom stays closed, where a forgotten `WHERE` used to leave it open.

The system context is deliberately narrow. Who may use it is held by
tests/test_run_isolation.py — a request handler reaching for it would quietly
undo the isolation for its route. The decision whether a person may see or
decide a run is taken in their own context, before the engine takes over.

Not a defence against the application's own credentials: whoever holds them
can set either setting. See migration 035.

Called by:
  - contexts.orchestration.service, contexts.pilots.service   (acting_as)
  - core.db            (the system pool sets as_system on every acquire)
  - contexts.pilots.store, cli.retention                      (connect_as_system)
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

import asyncpg

#: The session settings migration 035 reads. Custom settings need a dot.
ACTOR_SETTING = "cohereny.actor_id"
SYSTEM_SETTING = "cohereny.system"


@asynccontextmanager
async def acting_as(pool, actor_id: UUID) -> AsyncIterator[asyncpg.Connection]:
    """A connection on which the run tables show what `actor_id` may see.

    Session-level, not `SET LOCAL`: most reads run outside a transaction, where
    a local setting would be gone before the query. Cleared again on the way
    out, so the connection returns to the pool knowing nobody — without relying
    on the pool's own reset.
    """
    async with pool.acquire() as conn:
        await conn.execute("SELECT set_config($1, $2, false)", ACTOR_SETTING, str(actor_id))
        try:
            yield conn
        finally:
            await conn.execute("SELECT set_config($1, '', false)", ACTOR_SETTING)


async def as_system(conn) -> None:
    """Let this connection see every run: for work done for the installation."""
    await conn.execute("SELECT set_config($1, 'on', false)", SYSTEM_SETTING)


async def connect_as_system(dsn: str) -> asyncpg.Connection:
    """A new connection in the system context, for code that opens its own."""
    conn = await asyncpg.connect(dsn)
    try:
        await as_system(conn)
    except BaseException:
        await conn.close()
        raise
    return conn
