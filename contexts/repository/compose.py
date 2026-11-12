"""
contexts/repository/compose.py

The composition read path and its cache (CHY-61, ADR-010).

Two things live here: assembling a composed context from PostgreSQL, and the
Redis layer in front of it. They are separate functions so the assembly can be
tested without Redis and the cache key can be reasoned about on its own.

Why the cache key carries generation counters (ADR-010, Fortschreibung v2.0):
  The alternative was a secondary index of every compiled key per scope, and a
  targeted DEL fan-out on each block activation. A generation counter makes
  invalidation implicit instead: activating a block increments the counter of
  its scope, every key built afterwards differs, and the stale ones are never
  requested again. A compile task that fails then costs a cache miss rather
  than serving wrong data — the failure mode is the one you want.

Why the whole ancestor chain feeds the key:
  A block added at the org level changes what every project below it composes.
  If only the leaf's generation were in the key, that change would stay
  invisible until the leaf happened to change too.

Composition lives in the repository context rather than in one of its own
because it has no table: it reads blocks and nothing else. Separating the read
side from the write side across a context boundary would cost a translation
layer and buy nothing (ADR-030 point 3).

Reached through service.py; the SQL it needs is in store.py.

Dependencies:
  - redis.asyncio · contexts.repository.store
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, UTC
from uuid import UUID

import asyncpg
import redis.asyncio as aioredis

from contexts.repository import ratchet
from contexts.repository import store
from kernel import scopes
from kernel.errors import ScopeNotFound
from contexts.repository.schema import ComposedBlock, ComposedContext, ComposeRequest


# How long a compiled context may sit in Redis. Generation counters make stale
# entries unreachable rather than wrong, so the TTL is only there to reclaim
# memory from keys nobody will ask for again.
CACHE_TTL_SECONDS = 3600

_log = logging.getLogger(__name__)

_KEY_PREFIX = "cc:compiled"
_GEN_PREFIX = "cc:scope_gen"


def scope_generation_key(scope_id: UUID | str) -> str:
    return f"{_GEN_PREFIX}:{scope_id}"


def compiled_key(req: ComposeRequest, generations: dict[str, int]) -> str:
    """Build the cache key from the request and the scope generations.

    The generations are folded in sorted by scope id, so the same set always
    produces the same key regardless of the order the ancestors came back in.
    Without that the key would depend on a query's row order and the cache
    would miss for no reason.
    """
    gen_part = ".".join(f"{sid}={gen}" for sid, gen in sorted(generations.items()))
    return ":".join(
        [
            _KEY_PREFIX,
            str(req.workflow_id or "-"),
            req.node_ref or "-",
            str(req.scope_id),
            req.model_id or "-",
            ",".join(sorted(req.block_types)) if req.block_types else "-",
            # Two nodes of one workflow asking for different tags compose
            # different context; one key would hand each the other's (CHY-178).
            ",".join(sorted(req.tags)) if req.tags else "-",
            gen_part or "0",
        ]
    )


async def read_scope_generations(
    redis: aioredis.Redis, scope_ids: list[UUID]
) -> dict[str, int]:
    """Current generation of each scope. Missing counters read as 0.

    A missing counter is not an error: a scope whose blocks have never been
    activated has simply never been incremented. Treating it as 0 keeps the
    first composition cacheable instead of forcing a miss until someone writes.
    """
    if not scope_ids:
        return {}
    # strict=True because MGET answers with exactly one entry per key. Before
    # CHY-105 this was a bare zip(), which turned a short answer into silently
    # fewer generations in the cache key — a wrong key rather than an error.
    values = await redis.mget([scope_generation_key(s) for s in scope_ids])
    return {
        str(sid): int(v) if v is not None else 0
        for sid, v in zip(scope_ids, values, strict=True)
    }


async def bump_scope_generation(redis: aioredis.Redis, scope_id: UUID | str) -> int:
    """Invalidate every compiled context that involves this scope.

    Called when a block in the scope is activated or superseded. Nothing is
    deleted — the counter moves and the old keys become unreachable.
    """
    return int(await redis.incr(scope_generation_key(scope_id)))


async def load_scope_chain(pool: asyncpg.Pool, scope_id: UUID) -> list[asyncpg.Record]:
    """The scope and all its ancestors, root first.

    Through the kernel since CHY-121: `scope_nodes` belongs there, and a
    context that knows its ltree schema has a dependency nobody declared.
    """
    return await scopes.ancestor_chain(pool, scope_id)


async def compose_from_db(
    pool: asyncpg.Pool, req: ComposeRequest, chain: list[asyncpg.Record]
) -> list[ComposedBlock]:
    """The newest approved version of each block in the scope chain, in order.

    The reasoning behind "newest active" — and what it cost to get there — is
    in store.active_blocks_in_scopes, next to the query it explains.

    Depth and path come from the chain, not from a join (CHY-121). The chain is
    a handful of rows the caller has already loaded, so joining `scope_nodes`
    back in was a second table in the query for two values that were already in
    memory — and it put a kernel-owned table inside a context's SQL.

    Ordered shallow to deep so a later block can narrow an earlier one. Within
    one level, oldest first: the order two blocks were written in is the only
    stable tie-breaker available, and stable matters more than clever here,
    because an unstable order would change the prompt between two identical
    requests. block_id breaks a tie between two blocks created in the same
    microsecond — rare, but "rare" is not "never" for a value that decides what
    an LLM reads.
    """
    if not chain:
        return []

    by_scope = {r["id"]: (str(r["scope_path"]), r["depth"]) for r in chain}
    rows = await store.active_blocks_in_scopes(pool, list(by_scope), req.block_types,
                                               tags=req.tags)

    # Sorted here, on the rows, because created_at is a tie-breaker and not part
    # of a ComposedBlock — the model carries what a consumer needs, not what
    # the ordering needed on the way.
    rows = sorted(
        rows,
        key=lambda r: (by_scope[r["scope_id"]][1], r["created_at"], r["block_id"]),
    )
    # The backstop of CHY-62. `approve` refuses a conflicting claim before it
    # can become active, so this should never fire — but several versions of a
    # block can be active at once by design (see store.active_blocks_in_scopes),
    # and rows reach `active` by paths that do not pass through the endpoint. On
    # 2026-08-13 exactly that gap let a retired block reappear with its old
    # content, found by an end-to-end run and not by any unit test.
    #
    # Refusing here rather than dropping the offending block: silently composing
    # a subset would hand the caller a prompt that looks complete and is not,
    # which is the failure mode this whole ticket exists to prevent.
    claims = [
        ratchet.claim_from_row(r, by_scope[r["scope_id"]][1]) for r in rows
    ]
    ratchet.check_composition([c for c in claims if c is not None])

    return [
        ComposedBlock(
            block_id=r["block_id"],
            version=r["version"],
            block_type=r["block_type"],
            content=r["content"],
            scope_id=r["scope_id"],
            scope_path=by_scope[r["scope_id"]][0],
            depth=by_scope[r["scope_id"]][1],
            tags=list(r["tags"]),
            governance_key=r["governance_key"],
            privacy_impact=r["privacy_impact"],
            locked=r["locked"],
        )
        for r in rows
    ]


async def compose(
    pool: asyncpg.Pool, redis: aioredis.Redis, req: ComposeRequest
) -> ComposedContext:
    """Compose, preferring the cache. The cache-miss path is the slow one.

    On a miss the result is written back before returning, so the next caller
    for the same node hits. A failed write is not fatal — it costs another
    miss, and refusing to answer because a cache could not be updated would
    turn an optimisation into a dependency.
    """
    chain = await load_scope_chain(pool, req.scope_id)
    if not chain:
        raise ScopeNotFound(req.scope_id)

    scope_ids = [r["id"] for r in chain]
    generations = await read_scope_generations(redis, scope_ids)
    key = compiled_key(req, generations)

    if not req.refresh:
        cached = await redis.get(key)
        if cached:
            ctx = ComposedContext(**json.loads(cached))
            ctx.cache_hit = True
            return ctx

    blocks = await compose_from_db(pool, req, chain)
    ctx = ComposedContext(
        scope_id=req.scope_id,
        workflow_id=req.workflow_id,
        node_ref=req.node_ref,
        model_id=req.model_id,
        blocks=blocks,
        block_versions={str(b.block_id): b.version for b in blocks},
        scope_generations=generations,
        compiled_at=datetime.now(UTC),
        cache_hit=False,
    )

    # The cache is optional — see the docstring. A failed write costs another
    # miss; refusing to answer because a cache could not be updated would turn
    # an optimisation into a dependency.
    # Logged all the same: a Redis that stopped answering otherwise shows only
    # as everything getting slower (CHY-97).
    try:
        await redis.set(key, json.dumps(ctx.to_cache()), ex=CACHE_TTL_SECONDS)
    except Exception:
        _log.warning("composed context not cached", exc_info=True)

    return ctx
