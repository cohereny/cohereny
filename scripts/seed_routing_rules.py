#!/usr/bin/env python3
"""
scripts/seed_routing_rules.py

Write the routing table that used to live in YAML into a scope (CHY-87).

Until step 2b the routes were six lines in `router/routing-config.yaml`. They
are now `routing_rule` blocks: versioned, approval-bound, inherited along the
scope tree (ADR-037). A deployment with no such blocks cannot route at all —
deliberately, because routing is a decision and an installation that has not
made it should say so rather than pick a model that then looks chosen.

This script makes the *former* default explicit, once, in a named scope. It is
not a migration: a migration would write governance decisions on behalf of
whoever runs it, and these rows are supposed to have an author.

    python3 scripts/seed_routing_rules.py --scope <uuid> --author <uuid>
    python3 scripts/seed_routing_rules.py --scope <uuid> --author <uuid> --apply

Without --apply it prints what it would write and touches nothing.

The blocks are created as `draft`. Approving them is a separate act by a
separate person, which is the entire reason the rules moved out of a file.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from uuid import UUID, uuid4

import asyncpg

#: What `routing-config.yaml` carried until CHY-87 step 2b, verbatim.
#:
#: Kept here rather than read from the file: the file no longer has a `routes`
#: section, and re-adding one so a script could read it would put the second
#: source back that this change removed.
FORMER_ROUTES: dict[str, list[str]] = {
    "summarize": ["gemma3-4b", "phi3-mini"],
    "extract": ["gemma3-4b", "phi3-mini"],
    "mindmap": ["gemma3-4b", "phi3-mini"],
    "audio_script": ["gemma4-e4b", "gemma3-4b"],
    "quiz": ["gemma3-4b", "phi3-mini"],
    "default": ["gemma3-4b", "phi3-mini"],
}

#: The answer length each seeded rule proposes (CHY-166). The YAML never had
#: one — nothing bounded an answer — so these are not carried over but
#: proposed, and the reviewer approving the draft decides. Generous on purpose:
#: a limit that cuts a real answer short is the worse surprise. 2048 for the
#: audio script, the one task that writes long prose; the banking pilot's nodes
#: used the same figure (pilots/banking/banking/nodes.py).
PROPOSED_MAX_TOKENS: dict[str, int] = {
    "summarize": 1024,
    "extract": 1024,
    "mindmap": 1024,
    "audio_script": 2048,
    "quiz": 1024,
    "default": 1024,
}

_REASON = (
    "Übernommen aus router/routing-config.yaml beim Umzug der Routing-Regeln "
    "in das Kontext-Repository (CHY-87). Der zweite Kandidat läuft bewusst auf "
    "einem anderen Host: am 13.08.2026 zeigte jeder Task-Typ auf ein Modell, "
    "das nur ein einziger Inferenz-Host bedient; der war aus, und /v1/complete "
    "antwortete durchgehend 502. Die Längengrenze (max_tokens) ist ein "  # codespell:ignore
    "Vorschlag aus CHY-166 — die YAML-Datei kannte keine; bei der Freigabe prüfen."
)


async def seed(dsn: str, scope_id: UUID, author_id: UUID, apply: bool) -> int:
    conn = await asyncpg.connect(dsn)
    try:
        existing = await conn.fetch(
            "SELECT metadata->>'task_type' AS task_type FROM context_blocks "
            "WHERE scope_id = $1 AND block_type = 'routing_rule'",
            scope_id,
        )
        already_there = {r["task_type"] for r in existing}

        written = 0
        for task_type, candidates in sorted(FORMER_ROUTES.items()):
            if task_type in already_there:
                print(f"  · {task_type:12s} übersprungen — steht schon in diesem Scope")
                continue
            limit = PROPOSED_MAX_TOKENS[task_type]
            print(f"  {'+' if apply else '?'} {task_type:12s} {' → '.join(candidates)}"
                  f"  (max_tokens {limit})")
            if not apply:
                continue
            await conn.execute(
                "INSERT INTO context_blocks "
                "  (id, block_id, version, scope_id, block_type, content, "
                "   metadata, author_id, status) "
                "VALUES ($1, $2, 1, $3, 'routing_rule', $4, $5, $6, 'draft')",
                uuid4(), uuid4(), scope_id, _REASON,
                json.dumps({"task_type": task_type, "candidates": candidates,
                            "max_tokens": limit}),
                author_id,
            )
            written += 1
        return written
    finally:
        await conn.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--scope", required=True, type=UUID)
    ap.add_argument("--author", required=True, type=UUID)
    ap.add_argument("--apply", action="store_true",
                    help="write the rows; without it nothing is touched")
    args = ap.parse_args()

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("DATABASE_URL ist nicht gesetzt", file=sys.stderr)
        return 2

    print(f"Scope {args.scope}, Autor {args.author}, "
          f"{'schreibend' if args.apply else 'Probelauf'}:")
    n = asyncio.run(seed(dsn, args.scope, args.author, args.apply))

    if args.apply:
        print(f"\n{n} Regeln als `draft` angelegt.")
        print("Sie wirken erst nach der Freigabe — das ist der Grund, aus dem "
              "sie keine Datei mehr sind.")
    else:
        print("\nProbelauf. Mit --apply schreiben.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
