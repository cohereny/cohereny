"""
tests/integration/test_run_isolation_db.py

Who sees a run, enforced by row-level security on a real PostgreSQL (CHY-92).

The rule, migration 035: a workflow run is visible to whoever started it, to
the members of its workflow's scope and of every scope above it, and to admin;
a pilot run to whoever started it and to admin. Everyone else gets "not found"
— the run might as well not exist.

Each refusal below has its counterpart that is let through, so a policy that
hid everything would fail half of this file. The rule against forgotten
filters is shown directly: a query that does not say for whom sees no rows and
writes none.
"""
from __future__ import annotations

from functools import partial
from uuid import UUID, uuid4

import asyncpg
import pytest

from api.v1._redaction import mapping_keys, policy_or_shipped
from contexts.orchestration import service as orchestration
from contexts.orchestration.errors import RunNotFound
from contexts.orchestration.schema import OversightDecision
from contexts.pilots import service as pilots
from contexts.pilots import store as pilot_store
from contexts.pilots.errors import NoCoverageData
from contexts.pilots.errors import RunNotFound as PilotRunNotFound
from kernel import acting
from kernel.identity import resolve_actor
from kernel.retention import INVENTORY
from tests.integration._callers import caller
from tests.integration.conftest import _database_url

pytestmark = pytest.mark.integration


async def _scope(pool, parent: UUID | None = None) -> UUID:
    scope_id = uuid4()
    path = f"s_{scope_id.hex}"
    if parent:
        parent_path = await pool.fetchval(
            "SELECT scope_path::text FROM scope_nodes WHERE id = $1", parent)
        path = f"{parent_path}.{path}"
    await pool.execute(
        "INSERT INTO scope_nodes (id, scope_path, node_type, name, parent_id) "
        "VALUES ($1, $2::ltree, $3, 'it', $4)",
        scope_id, path, "project" if parent else "org", parent,
    )
    return scope_id


async def _member(pool, scope_id: UUID, actor_id: UUID) -> None:
    await pool.execute(
        "INSERT INTO scope_memberships (id, scope_id, actor_id, role) VALUES ($1, $2, $3, 'reviewer')",
        uuid4(), scope_id, actor_id,
    )


class _World:
    """An organisation with a project, a neighbouring organisation, and the people."""

    def __init__(self, **kw):
        self.__dict__.update(kw)


@pytest.fixture
async def world(pool, system_pool) -> _World:
    org = await _scope(pool)
    project = await _scope(pool, parent=org)
    neighbour = await _scope(pool)

    people = {name: caller(role) for name, role in (
        ("starter", "contributor"), ("org_member", "reviewer"),
        ("neighbour", "reviewer"), ("stranger", "consumer"), ("admin", "admin"),
    )}
    ids = {name: (await resolve_actor(pool, c)).id for name, c in people.items()}
    await _member(pool, org, ids["org_member"])
    await _member(pool, neighbour, ids["neighbour"])

    workflow = await pool.fetchval(
        "INSERT INTO workflow_definitions (scope_id, name) VALUES ($1, 'it') RETURNING id", project)
    run_id = await system_pool.fetchval(
        "INSERT INTO workflow_runs (workflow_id, workflow_version, actor_id, input_data, status) "
        "VALUES ($1, 1, $2, '{\"patient\": \"Ms Mustermann\"}', 'awaiting_oversight') RETURNING id",
        workflow, ids["starter"],
    )
    return _World(people=people, ids=ids, run_id=run_id, workflow=workflow)


class TestWhoSeesAWorkflowRun:
    async def test_the_starter_sees_it(self, pool, world):
        run = await orchestration.get_run(pool, world.people["starter"], world.run_id)
        assert run.id == world.run_id

    async def test_a_member_of_a_scope_above_sees_it(self, pool, world):
        """Membership in the organisation covers its projects."""
        run = await orchestration.get_run(pool, world.people["org_member"], world.run_id)
        assert run.id == world.run_id

    async def test_admin_sees_it(self, pool, world):
        run = await orchestration.get_run(pool, world.people["admin"], world.run_id)
        assert run.id == world.run_id

    async def test_a_member_of_another_scope_does_not(self, pool, world):
        with pytest.raises(RunNotFound):
            await orchestration.get_run(pool, world.people["neighbour"], world.run_id)

    async def test_a_stranger_does_not(self, pool, world):
        with pytest.raises(RunNotFound):
            await orchestration.get_run(pool, world.people["stranger"], world.run_id)

    async def test_the_steps_follow_the_run(self, pool, system_pool, world):
        """node_executions inherit the run's visibility through their policy."""
        node = await system_pool.fetchval(
            "INSERT INTO workflow_nodes (workflow_id, node_ref, node_type, name, task_type) "
            "VALUES ($1, 'draft', 'llm_call', 'draft', 'summarize') RETURNING id", world.workflow)
        await system_pool.execute(
            "INSERT INTO node_executions (run_id, node_id, iteration, status, output_data) "
            "VALUES ($1, $2, 1, 'complete', '{\"content\": \"Dear Ms Mustermann\"}')",
            world.run_id, node)

        nodes = await orchestration.run_nodes(pool, world.people["org_member"], world.run_id)
        assert [n.node_ref for n in nodes.nodes] == ["draft"]
        async with acting.acting_as(pool, world.ids["neighbour"]) as conn:
            assert await conn.fetchval(
                "SELECT count(*) FROM node_executions WHERE run_id = $1", world.run_id) == 0

    async def test_the_mapping_follows_the_run(self, pool, system_pool, world):
        """Ciphertext only, but still the run's: whoever does not see the run
        does not learn that it holds identifiers, or how many (CHY-234)."""
        await system_pool.execute(
            "INSERT INTO pii_mappings (run_id, key_id, nonce, ciphertext) "
            "VALUES ($1, 'k1', '\\x000102030405060708090a0b', '\\xdeadbeef')", world.run_id)

        query = "SELECT count(*) FROM pii_mappings WHERE run_id = $1"
        async with acting.acting_as(pool, world.ids["starter"]) as conn:
            assert await conn.fetchval(query, world.run_id) == 1
        async with acting.acting_as(pool, world.ids["neighbour"]) as conn:
            assert await conn.fetchval(query, world.run_id) == 0
        assert await pool.fetchval(query, world.run_id) == 0, "nobody named, nothing seen"


class TestDecidingNeedsToSeeTheRun:
    async def test_a_reviewer_outside_the_scope_cannot_decide(self, pool, system_pool, world):
        """The role alone is not enough: the run is not found before anything is recorded."""
        with pytest.raises(RunNotFound):
            await orchestration.record_oversight(
                pool, world.people["neighbour"], world.run_id,
                OversightDecision(decision="approved"),
                engine=orchestration.Engine(
                    registry=None, pool=system_pool,
                    placeholders=orchestration.Placeholders(
                        policy_for=partial(policy_or_shipped, pool), keys=mapping_keys())),
            )
        assert await system_pool.fetchval(
            "SELECT count(*) FROM review_votes v JOIN node_executions e ON e.id = v.execution_id "
            "WHERE e.run_id = $1", world.run_id) == 0


class TestWithoutSayingForWhom:
    async def test_a_query_sees_no_rows(self, pool, system_pool, world):
        assert await pool.fetchval(
            "SELECT count(*) FROM workflow_runs WHERE id = $1", world.run_id) == 0
        assert await system_pool.fetchval(
            "SELECT count(*) FROM workflow_runs WHERE id = $1", world.run_id) == 1

    async def test_nothing_is_written(self, pool, world):
        with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError,
                           match="row-level security"):
            await pool.execute(
                "INSERT INTO workflow_runs (workflow_id, workflow_version, actor_id) "
                "VALUES ($1, 1, $2)", world.workflow, world.ids["starter"])

    async def test_the_setting_is_cleared_when_the_connection_goes_back(self, world):
        """A pooled connection must not carry one person's view into the next request.

        A pool of one, so the second acquire is certain to get the same
        connection the first one used.
        """
        single = await asyncpg.create_pool(_database_url(), min_size=1, max_size=1)
        try:
            async with acting.acting_as(single, world.ids["starter"]) as conn:
                assert await conn.fetchval(
                    "SELECT count(*) FROM workflow_runs WHERE id = $1", world.run_id) == 1
            async with single.acquire() as conn:
                assert await conn.fetchval(
                    "SELECT count(*) FROM workflow_runs WHERE id = $1", world.run_id) == 0
        finally:
            await single.close()


class TestWhoSeesAPilotRun:
    @pytest.fixture
    async def pilot_run(self, pool, system_pool, world):
        async with system_pool.acquire() as conn:
            row = await pilot_store.insert_run(
                conn, thread_id=f"it-{uuid4().hex}", pilot_name="study", input_file="it.pdf",
                actor_id=world.ids["starter"], run_config={}, effective_config={},
            )
        return row["id"]

    async def test_the_starter_and_admin_see_it(self, pool, world, pilot_run):
        for who in ("starter", "admin"):
            assert (await pilots.get_run(pool, world.people[who], pilot_run)).id == pilot_run

    async def test_a_member_of_any_scope_does_not(self, pool, world, pilot_run):
        """A pilot run has no scope, so no membership reaches it."""
        with pytest.raises(PilotRunNotFound):
            await pilots.get_run(pool, world.people["org_member"], pilot_run)

    async def test_its_coverage_follows_the_run(self, pool, world, pilot_run):
        """Visible to the starter — who learns there is nothing to report — and
        not found for anyone else. Written after the contract fuzzer found this
        route answering 500: the coverage query took a connection where it
        expected a pool, and no test went through it against a database."""
        with pytest.raises(NoCoverageData):
            await pilots.get_coverage(pool, world.people["starter"], pilot_run)
        with pytest.raises(PilotRunNotFound):
            await pilots.get_coverage(pool, world.people["stranger"], pilot_run)


async def test_every_table_with_content_has_row_security(pool):
    """The rule follows the data: a table that holds run content and has no
    policy would be readable by everyone signed in."""
    content_tables = sorted(t for t, e in INVENTORY.items() if e.content_columns)
    rows = await pool.fetch(
        "SELECT relname, relrowsecurity FROM pg_class "
        "WHERE relnamespace = 'public'::regnamespace AND relname = ANY($1)", content_tables)
    assert {r["relname"]: r["relrowsecurity"] for r in rows} == dict.fromkeys(content_tables, True)
