"""
cli/oversight.py

The command behind `cc oversight expire` (CHY-254, ADR-050).

A gate nobody decides used to wait for good, and its run kept its content past
every period. This ends it: past the content period of its scope, or past its
own `review_timeout`, a waiting gate either escalates along its escalation edge
or ends the run as `expired` (orchestration.service.expire_gates).

A command, not a scheduler in the API process, for the reason the purge is one
(ADR-046 point 5): when something times out on a host is operations. Run it
from the same timer as `cc retention purge`: a gate past the content period
is cleared by this command itself, so the order of the two does not matter.

Composition root: zone 1 may call every context (ADR-030).
"""
from __future__ import annotations

import asyncpg

from contexts.orchestration import service as orchestration
from core.db import _register_codecs
from kernel import acting, least_privilege, retention


async def expire(dsn: str, *, dry_run: bool) -> dict[str, int]:
    """Expire or escalate the gates that are due. Returns the counts.

    Runs as the application role, like the API, and refuses to run as more
    (kernel/least_privilege.py, CHY-85). In the system context, through a pool
    whose connections set it on every acquire: an escalation advances the run,
    and advancing takes connections of its own. With the JSON codecs of the
    application's pools (core/db.py) — advancing reads the run's input as a
    mapping, and without them it arrives as a string.
    """
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2,
                                     init=_register_codecs, setup=acting.as_system)
    try:
        async with pool.acquire() as conn:
            await least_privilege.check(conn)
            periods = await retention.current_periods(conn)
        return await orchestration.expire_gates(pool, periods, dry_run=dry_run)
    finally:
        await pool.close()
