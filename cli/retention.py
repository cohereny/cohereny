"""
cli/retention.py

The purge behind `cc retention purge` (CHY-17, ADR-046).

A command, not a scheduler in the API process: the operator's timer, cron job
or Kubernetes CronJob starts it, as with the benchmark (ADR-044, point 4). The
API serves requests; deciding when data expires on a host is operations.

**One transaction for everything.** The kernel computes which period applies
where; each context then clears and deletes its own tables — orchestration its
runs, pilots theirs, learning the feedback — and the kernel writes one
`retention.purged` entry with what happened. Either all of it commits or none
of it, so the audit entry never describes a purge that half failed.

**`--dry-run` takes the same path and rolls back.** The counts are what the
real run would do, including runs held by a sub-run, rather than an estimate
from a second set of queries that could disagree with the first.

Composition root for the purge: zone 1 may call every context (ADR-030), and
none of them calls another.
"""
from __future__ import annotations

import asyncpg

from contexts.learning import service as learning
from contexts.orchestration import service as orchestration
from contexts.pilots import service as pilots
from kernel import acting, least_privilege, retention


async def purge(dsn: str, *, dry_run: bool) -> dict[str, int]:
    """Apply every retention period once. Returns the counts per table.

    Runs as the application role like the API, and refuses to run as more
    (kernel/least_privilege.py, CHY-85). In the system context: the purge works
    for the installation and must reach every run, whoever started it (CHY-92).
    """
    conn = await asyncpg.connect(dsn)
    try:
        await least_privilege.check(conn)
        await acting.as_system(conn)
        transaction = conn.transaction()
        await transaction.start()
        try:
            periods = await retention.current_periods(conn)
            counts: dict[str, int] = {}
            counts.update(await orchestration.purge_runs(conn, periods))
            counts.update(await pilots.purge_runs(conn, periods))
            counts.update(await learning.purge_feedback(
                conn, periods, await orchestration.workflow_scopes(conn)))
            await retention.record_purge(conn, counts, periods)
        except BaseException:
            await transaction.rollback()
            raise
        if dry_run:
            await transaction.rollback()
        else:
            await transaction.commit()
        return counts
    finally:
        await conn.close()
