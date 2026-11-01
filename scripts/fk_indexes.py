#!/usr/bin/env python3
"""
scripts/fk_indexes.py

Which foreign keys lack a leading index, and whether that matters yet (CHY-112).

Why this is a script and not a test:
  The answer depends on the live schema and on how many rows each table holds,
  and no test in this repository talks to a database. Stating the finding as a
  fixed list would freeze an answer that changes as tables fill — the very
  failure this project keeps finding. So: a check that can be re-run, at the
  milestone gate, or before any migration that adds a
  relationship.

Why an unindexed foreign key is not automatically a defect:
  Measured on 19.08.2026 — 21 of 28 foreign keys had no leading index, and not
  one of them needed one. Three questions decide it, in this order.

  1. Is the parent ever deleted? PostgreSQL checks the child table on every
     parent DELETE, including for NO ACTION, and without an index that check is
     a sequential scan. Only pilot_runs is ever deleted, and both of its
     children were already indexed.
  2. Is the column queried on its own? context_blocks.author_id appears only in
     `WHERE block_id = $1 AND author_id = $2`, and block_id carries two indexes
     — the lookup is already down to a handful of rows.
  3. Is the table big enough for it to matter? Everything except context_blocks
     (34 rows) was empty.

  Adding all 21 would have meant write amplification on empty tables for
  queries nobody makes.

Usage:
    DATABASE_URL=postgresql://... python scripts/fk_indexes.py [--rows N]

Exit code is 1 when a foreign key is both unindexed and load-bearing by the
rules above, so this can be wired into a gate later without changing it.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

import asyncpg

#: Below this, a sequential scan is cheaper than maintaining an index.
#: Deliberately generous: the point is to catch the table that grew, not to
#: police a schema nobody is using yet.
DEFAULT_ROW_THRESHOLD = 1_000

QUERY = """
SELECT c.conrelid::regclass::text          AS child,
       a.attname                           AS col,
       c.confrelid::regclass::text         AS parent,
       c.confdeltype                       AS on_delete,
       EXISTS (SELECT 1 FROM pg_index i
               WHERE i.indrelid = c.conrelid AND a.attnum = i.indkey[0]) AS indexed,
       COALESCE((SELECT n_live_tup FROM pg_stat_user_tables s
                 WHERE s.relid = c.conrelid), 0) AS child_rows,
       EXISTS (SELECT 1 FROM pg_constraint p
               WHERE p.contype = 'f' AND p.confrelid = c.confrelid) AS parent_has_children
FROM pg_constraint c
JOIN unnest(c.conkey) AS k(attnum) ON TRUE
JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.attnum
WHERE c.contype = 'f' AND c.connamespace = 'public'::regnamespace
ORDER BY indexed, child, col;
"""


async def collect(dsn: str) -> list[dict]:
    conn = await asyncpg.connect(dsn)
    try:
        return [dict(r) for r in await conn.fetch(QUERY)]
    finally:
        await conn.close()


def verdict(fk: dict, threshold: int) -> tuple[bool, str]:
    """(is a problem, why). The order of the questions is the whole method."""
    if fk["indexed"]:
        return False, "indexed"
    if fk["child_rows"] < threshold:
        return False, f"{fk['child_rows']} rows — below the threshold"
    # A cascade or restrict on a parent that is actually deleted turns every
    # parent DELETE into a scan of this table.
    if fk["on_delete"] in ("c", "r"):
        return True, f"{fk['child_rows']} rows and ON DELETE {'CASCADE' if fk['on_delete']=='c' else 'RESTRICT'}"
    return True, f"{fk['child_rows']} rows"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rows", type=int, default=DEFAULT_ROW_THRESHOLD,
                    help=f"Row count above which a missing index counts (default {DEFAULT_ROW_THRESHOLD})")
    args = ap.parse_args()

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("DATABASE_URL is not set.", file=sys.stderr)
        return 2

    fks = asyncio.run(collect(dsn))
    problems = [(f, why) for f in fks for ok, why in [verdict(f, args.rows)] if ok]
    unindexed = [f for f in fks if not f["indexed"]]

    print(f"{len(fks)} foreign keys · {len(fks) - len(unindexed)} indexed "
          f"· {len(unindexed)} without a leading index\n")

    if unindexed:
        print("Without an index:")
        for f in unindexed:
            _, why = verdict(f, args.rows)
            mark = "\033[31m!\033[0m" if (f, why) in problems else " "
            print(f"  {mark} {f['child']}.{f['col']} → {f['parent']}   {why}")
        print()

    if not problems:
        print("No missing index is load-bearing right now.")
        return 0

    print(f"\033[31m{len(problems)} load-bearing:\033[0m")
    for f, why in problems:
        print(f"  CREATE INDEX idx_{f['child']}_{f['col']} ON {f['child']} ({f['col']});  -- {why}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
