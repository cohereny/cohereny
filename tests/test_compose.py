"""
tests/test_compose.py

Unit tests for the composition read path (CHY-61, ADR-010).

Two things carry the weight here and both are easy to get subtly wrong: the
cache key, because a key that varies when it should not turns the cache off
without anyone noticing, and the composition order, because an unstable order
makes two identical requests produce different prompts.

Mocks: asyncpg pool and Redis. The SQL itself is exercised against real
PostgreSQL in the verification run recorded in the MR.
"""
import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from api.deps import CurrentUser, require_auth
from api.main import app
from contexts.repository.compose import (
    CACHE_TTL_SECONDS,
    bump_scope_generation,
    compiled_key,
    compose,
    compose_from_db,
    read_scope_generations,
    scope_generation_key,
)
from core.db import get_pool
from core.redis import get_redis
from kernel.errors import ScopeNotFound
from tests.conftest import as_caller
from contexts.repository.schema import ComposedBlock, ComposedContext, ComposeRequest

_stub_user = CurrentUser(sub="test-sub", username="tester")
_ORG = UUID("00000000-0000-0000-0000-0000000000a1")
_TEAM = UUID("00000000-0000-0000-0000-0000000000a2")
_PROJ = UUID("00000000-0000-0000-0000-0000000000a3")
_NOW = datetime(2026, 8, 12, 11, 0, tzinfo=UTC)


def _chain():
    return [
        {"id": _ORG, "scope_path": "org_a", "node_type": "org", "name": "A", "depth": 1, "privacy_level": None},
        {"id": _TEAM, "scope_path": "org_a.team_b", "node_type": "team", "name": "B", "depth": 2, "privacy_level": None},
        {"id": _PROJ, "scope_path": "org_a.team_b.project_c", "node_type": "project", "name": "C", "depth": 3, "privacy_level": None},
    ]


def _block_row(scope_id, depth, content, path, **kw):
    """A row as the query returns it since CHY-121.

    `scope_path` and `depth` are no longer columns — the join against
    scope_nodes is gone, and compose_from_db reads both out of the ancestor
    chain it already holds. The two parameters stay in the signature so the
    call sites keep reading as "a block at this depth", but they are ignored:
    the depth that counts comes from _chain().
    """
    del depth, path
    return {
        "block_id": kw.get("block_id", uuid4()),
        "version": kw.get("version", 1),
        "block_type": kw.get("block_type", "system_prompt"),
        "content": content,
        "scope_id": scope_id,
        "tags": kw.get("tags", []),
        "created_at": kw.get("created_at", datetime(2026, 1, 1, tzinfo=UTC)),
        # Selected by the query since CHY-62. Present here even though most
        # tests never set them: a fixture that omits a column the query returns
        # would read as "makes no governance claim" for the wrong reason, and
        # the ratchet would be silently untested rather than satisfied.
        "governance_key": kw.get("governance_key"),
        "privacy_impact": kw.get("privacy_impact"),
        "locked": kw.get("locked", False),
    }


def _redis(generations=None, cached=None) -> MagicMock:
    r = MagicMock()
    r.mget = AsyncMock(return_value=generations if generations is not None else [None, None, None])
    r.get = AsyncMock(return_value=cached)
    r.set = AsyncMock()
    r.incr = AsyncMock(return_value=1)
    return r


def _pool(chain=None, blocks=None) -> MagicMock:
    p = MagicMock()
    p.fetch = AsyncMock(side_effect=[chain if chain is not None else _chain(), blocks or []])
    return p


# ── The cache key ─────────────────────────────────────────────────────────────

class TestCacheKey:
    def _req(self, **kw):
        return ComposeRequest(scope_id=_PROJ, **kw)

    def test_key_is_stable_regardless_of_generation_dict_order(self):
        """Otherwise the key depends on row order and the cache misses for no
        reason."""
        a = compiled_key(self._req(), {"s1": 1, "s2": 2})
        b = compiled_key(self._req(), {"s2": 2, "s1": 1})
        assert a == b

    def test_generation_change_changes_the_key(self):
        """This is the whole invalidation mechanism (ADR-010 v2.0)."""
        before = compiled_key(self._req(), {"s1": 1})
        after = compiled_key(self._req(), {"s1": 2})
        assert before != after

    def test_ancestor_generation_changes_the_key(self):
        """A block added at org level must invalidate every project below it."""
        before = compiled_key(self._req(), {str(_ORG): 1, str(_PROJ): 5})
        after = compiled_key(self._req(), {str(_ORG): 2, str(_PROJ): 5})
        assert before != after

    def test_model_id_is_part_of_the_key(self):
        """auto_route can pick different models for one node; each needs its
        own compiled form."""
        assert compiled_key(self._req(model_id="gemma3"), {}) != compiled_key(
            self._req(model_id="phi3"), {}
        )

    def test_block_type_filter_is_part_of_the_key(self):
        assert compiled_key(self._req(block_types=["system_prompt"]), {}) != compiled_key(
            self._req(), {}
        )

    def test_block_type_order_does_not_change_the_key(self):
        a = compiled_key(self._req(block_types=["a", "b"]), {})
        b = compiled_key(self._req(block_types=["b", "a"]), {})
        assert a == b

    def test_refresh_is_not_part_of_the_key(self):
        """refresh says skip the read, not compile something different — if it
        changed the key it would also poison the entry for everyone else."""
        assert compiled_key(self._req(refresh=True), {}) == compiled_key(
            self._req(refresh=False), {}
        )

    def test_node_and_workflow_separate_keys(self):
        w = uuid4()
        assert compiled_key(self._req(workflow_id=w, node_ref="a"), {}) != compiled_key(
            self._req(workflow_id=w, node_ref="b"), {}
        )

    def test_key_is_prefixed_for_inspection(self):
        assert compiled_key(self._req(), {}).startswith("cc:compiled:")

    def test_generation_key_shape(self):
        assert scope_generation_key(_ORG) == f"cc:scope_gen:{_ORG}"


# ── Generation counters ───────────────────────────────────────────────────────

class TestGenerations:
    @pytest.mark.asyncio
    async def test_missing_counter_reads_as_zero(self):
        """A scope whose blocks were never activated has no counter. Treating
        that as an error would make the first composition uncacheable."""
        gens = await read_scope_generations(_redis([None, "3", None]), [_ORG, _TEAM, _PROJ])
        assert gens == {str(_ORG): 0, str(_TEAM): 3, str(_PROJ): 0}

    @pytest.mark.asyncio
    async def test_no_scopes_means_no_redis_call(self):
        r = _redis()
        assert await read_scope_generations(r, []) == {}
        r.mget.assert_not_called()

    @pytest.mark.asyncio
    async def test_bump_increments(self):
        r = _redis()
        r.incr = AsyncMock(return_value=7)
        assert await bump_scope_generation(r, _ORG) == 7
        r.incr.assert_awaited_once_with(f"cc:scope_gen:{_ORG}")


# ── Composition order and filtering ───────────────────────────────────────────

class TestCompositionFromDb:
    @pytest.mark.asyncio
    async def test_only_active_blocks_are_loaded(self):
        """A draft is work in progress, a pending_review block is waiting and a
        deprecated one was retired; none belongs in a live prompt."""
        pool = MagicMock()
        pool.fetch = AsyncMock(return_value=[])
        await compose_from_db(pool, ComposeRequest(scope_id=_PROJ), _chain())
        assert "status = 'active'" in pool.fetch.call_args.args[0]

    @pytest.mark.asyncio
    async def test_the_newest_active_version_wins(self):
        """Not "the current version, if active" — that dropped a block out of
        every composition for the whole review, because editing it moves
        superseded_by_version to a fresh draft (CHY-32)."""
        pool = MagicMock()
        pool.fetch = AsyncMock(return_value=[])
        await compose_from_db(pool, ComposeRequest(scope_id=_PROJ), _chain())
        sql = pool.fetch.call_args.args[0]
        assert "DISTINCT ON (block_id)" in sql
        assert "ORDER BY block_id, version DESC" in sql

    @pytest.mark.asyncio
    async def test_superseded_versions_are_no_longer_excluded(self):
        """The filter that caused the outage must be gone, not merely relaxed."""
        pool = MagicMock()
        pool.fetch = AsyncMock(return_value=[])
        await compose_from_db(pool, ComposeRequest(scope_id=_PROJ), _chain())
        assert "superseded_by_version IS NULL" not in pool.fetch.call_args.args[0]

    @pytest.mark.asyncio
    async def test_ordered_shallow_to_deep_then_stable(self):
        """Checked as behaviour, not as a substring of the SQL.

        Until CHY-121 this asserted `"ORDER BY x.depth, x.created_at,
        x.block_id" in sql` — which passes for a query that is never run and
        says nothing about what a caller receives. The ordering is Python now,
        so it can be driven with rows in the wrong order and read off the
        result.
        """
        early = datetime(2026, 1, 1, tzinfo=UTC)
        late = datetime(2026, 6, 1, tzinfo=UTC)
        deep_first = [
            _block_row(_PROJ, 3, "project", "p", created_at=early),
            _block_row(_ORG, 1, "org late", "o", created_at=late),
            _block_row(_ORG, 1, "org early", "o", created_at=early),
        ]
        pool = MagicMock()
        pool.fetch = AsyncMock(return_value=deep_first)

        blocks = await compose_from_db(pool, ComposeRequest(scope_id=_PROJ), _chain())

        assert [b.content for b in blocks] == ["org early", "org late", "project"], (
            "shallow before deep, and within one level the older block first — "
            "an unstable order changes the prompt between two identical requests"
        )
        assert [b.depth for b in blocks] == [1, 1, 3]
        assert blocks[0].scope_path == "org_a", "path comes from the chain, not a join"

    @pytest.mark.asyncio
    async def test_the_query_no_longer_joins_the_kernels_table(self):
        """`scope_nodes` belongs to the kernel (CHY-121)."""
        pool = MagicMock()
        pool.fetch = AsyncMock(return_value=[])
        await compose_from_db(pool, ComposeRequest(scope_id=_PROJ), _chain())
        assert "scope_nodes" not in pool.fetch.call_args.args[0]

    @pytest.mark.asyncio
    async def test_block_type_filter_is_applied(self):
        pool = MagicMock()
        pool.fetch = AsyncMock(return_value=[])
        await compose_from_db(
            pool, ComposeRequest(scope_id=_PROJ, block_types=["tool_schema"]), _chain()
        )
        assert "block_type = ANY" in pool.fetch.call_args.args[0]

    @pytest.mark.asyncio
    async def test_empty_chain_short_circuits(self):
        pool = MagicMock()
        pool.fetch = AsyncMock()
        assert await compose_from_db(pool, ComposeRequest(scope_id=_PROJ), []) == []
        pool.fetch.assert_not_called()


# ── The compose() flow ────────────────────────────────────────────────────────

class TestCompose:
    @pytest.mark.asyncio
    async def test_unknown_scope_raises_its_own_exception(self):
        """Not LookupError, which was the first attempt: KeyError inherits from
        it, so `except LookupError` in the endpoints turned any missing
        dictionary key inside the composition into a 404 claiming the scope did
        not exist. Found when a test fixture was missing a field and the
        endpoint answered 404 instead of failing (CHY-24)."""
        with pytest.raises(ScopeNotFound):
            await compose(_pool(chain=[]), _redis(), ComposeRequest(scope_id=_PROJ))

    @pytest.mark.asyncio
    async def test_a_missing_field_is_not_reported_as_a_missing_scope(self):
        pool = _pool(blocks=[{"block_id": uuid4()}])  # every other field absent
        with pytest.raises(Exception) as exc:
            await compose(pool, _redis(), ComposeRequest(scope_id=_PROJ))
        assert not isinstance(exc.value, ScopeNotFound)

    @pytest.mark.asyncio
    async def test_cache_miss_reads_db_and_writes_back(self):
        pool = _pool(blocks=[_block_row(_ORG, 1, "org rule", "org_a")])
        r = _redis()
        ctx = await compose(pool, r, ComposeRequest(scope_id=_PROJ))
        assert ctx.cache_hit is False
        assert len(ctx.blocks) == 1
        r.set.assert_awaited_once()
        assert r.set.await_args.kwargs["ex"] == CACHE_TTL_SECONDS

    @pytest.mark.asyncio
    async def test_cache_hit_skips_the_database(self):
        cached = ComposedContext(
            scope_id=_PROJ, blocks=[], compiled_at=_NOW
        ).to_cache()
        pool = MagicMock()
        pool.fetch = AsyncMock(side_effect=[_chain()])
        ctx = await compose(pool, _redis(cached=json.dumps(cached)), ComposeRequest(scope_id=_PROJ))
        assert ctx.cache_hit is True
        # One fetch for the scope chain, none for blocks.
        assert pool.fetch.await_count == 1

    @pytest.mark.asyncio
    async def test_refresh_bypasses_the_cache_read(self):
        cached = ComposedContext(scope_id=_PROJ, blocks=[], compiled_at=_NOW).to_cache()
        r = _redis(cached=json.dumps(cached))
        ctx = await compose(_pool(), r, ComposeRequest(scope_id=_PROJ, refresh=True))
        assert ctx.cache_hit is False
        r.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_block_versions_are_recorded(self):
        """Without them a run is not reproducible — 'the active blocks' means
        something different six months later."""
        bid = uuid4()
        pool = _pool(blocks=[_block_row(_ORG, 1, "x", "org_a", block_id=bid, version=4)])
        ctx = await compose(pool, _redis(), ComposeRequest(scope_id=_PROJ))
        assert ctx.block_versions == {str(bid): 4}

    @pytest.mark.asyncio
    async def test_generations_are_recorded(self):
        pool = _pool(blocks=[])
        ctx = await compose(pool, _redis(["1", "2", "3"]), ComposeRequest(scope_id=_PROJ))
        assert ctx.scope_generations == {str(_ORG): 1, str(_TEAM): 2, str(_PROJ): 3}

    @pytest.mark.asyncio
    async def test_a_failing_cache_write_does_not_fail_the_request(self):
        """The cache is an optimisation. Refusing to answer because it could
        not be updated would turn it into a dependency."""
        r = _redis()
        r.set = AsyncMock(side_effect=RuntimeError("redis down"))
        ctx = await compose(_pool(), r, ComposeRequest(scope_id=_PROJ))
        assert ctx.cache_hit is False

    @pytest.mark.asyncio
    async def test_cached_payload_never_claims_to_be_a_hit(self):
        """cache_hit describes this response, not the stored one."""
        ctx = ComposedContext(scope_id=_PROJ, blocks=[], compiled_at=_NOW, cache_hit=True)
        assert "cache_hit" not in ctx.to_cache()


# ── Rendering ─────────────────────────────────────────────────────────────────

def test_as_prompt_joins_in_composition_order():
    ctx = ComposedContext(
        scope_id=_PROJ,
        compiled_at=_NOW,
        blocks=[
            ComposedBlock(block_id=uuid4(), version=1, block_type="system_prompt",
                          content="org", scope_id=_ORG, scope_path="org_a", depth=1),
            ComposedBlock(block_id=uuid4(), version=1, block_type="system_prompt",
                          content="project", scope_id=_PROJ,
                          scope_path="org_a.team_b.project_c", depth=3),
        ],
    )
    assert ctx.as_prompt() == "org\n\n---\n\nproject"


def test_as_prompt_on_empty_context_is_empty():
    assert ComposedContext(scope_id=_PROJ, blocks=[], compiled_at=_NOW).as_prompt() == ""


# ── Endpoint ──────────────────────────────────────────────────────────────────

class TestEndpoint:
    def _client(self, pool, redis) -> TestClient:
        app.dependency_overrides[require_auth] = as_caller(_stub_user)
        app.dependency_overrides[get_pool] = lambda: pool
        app.dependency_overrides[get_redis] = lambda: redis
        return TestClient(app)

    def _cleanup(self):
        for dep in (require_auth, get_pool, get_redis):
            app.dependency_overrides.pop(dep, None)

    def test_requires_auth(self):
        resp = TestClient(app).post("/v1/compose", json={"scope_id": str(_PROJ)})
        assert resp.status_code in (401, 403)

    def test_unknown_scope_is_404(self):
        client = self._client(_pool(chain=[]), _redis())
        try:
            resp = client.post("/v1/compose", json={"scope_id": str(_PROJ)})
            assert resp.status_code == 404
        finally:
            self._cleanup()

    def test_scope_without_blocks_is_200_not_404(self):
        """"Nothing configured yet" is a normal state during setup."""
        client = self._client(_pool(blocks=[]), _redis())
        try:
            resp = client.post("/v1/compose", json={"scope_id": str(_PROJ)})
            assert resp.status_code == 200
            assert resp.json()["blocks"] == []
        finally:
            self._cleanup()

    def test_returns_blocks_and_reports_the_cache(self):
        client = self._client(_pool(blocks=[_block_row(_ORG, 1, "org rule", "org_a")]), _redis())
        try:
            body = client.post("/v1/compose", json={"scope_id": str(_PROJ)}).json()
            assert body["cache_hit"] is False
            assert body["blocks"][0]["content"] == "org rule"
        finally:
            self._cleanup()

    def test_scope_id_is_required(self):
        client = self._client(_pool(), _redis())
        try:
            assert client.post("/v1/compose", json={}).status_code == 422
        finally:
            self._cleanup()
