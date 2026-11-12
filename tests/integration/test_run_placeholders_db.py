"""
tests/integration/test_run_placeholders_db.py

A workflow run stored with placeholders, from the start to the gate, on a real
PostgreSQL (CHY-234).

The central proof: a run with a known name and an e-mail address goes through
two model calls, and afterwards **every row of every table** of the database is
searched for them -- not only the content columns kernel/retention.py lists, so
a table or column nobody thought of is covered as well, the audit log included.
Expected: no hit anywhere but in the sealed mapping, which opens with the key
and holds all of them. The model is an echo that also writes a phone number
and the name of its own accord, as a model can; neither may be stored as written.

The values carry a suffix per test, because the integration database is shared
and other tests write "Mustermann" on purpose.

What the model saw is checked too: every request the echo received.

And who may have the values back (E4): the starter, and from `reviewer` up
whoever sees the run -- each time audited, never with a value.
"""
from __future__ import annotations

import json
from functools import partial
from uuid import UUID, uuid4

import httpx
import pytest

from api.v1 import workflows
from api.v1._redaction import mapping_keys, policy_or_shipped
from contexts.orchestration import service as orchestration
from contexts.orchestration.errors import RunContentPurged, RunNotFound
from contexts.orchestration.execution import ExecutorRegistry
from contexts.orchestration.schema import OversightDecision, RunResolveRequest, WorkflowRunStart
from core import completion
from kernel import mapping_cipher
from kernel.errors import AccessDenied
from kernel.identity import resolve_actor
from router.rules import RoutingRule
from tests.integration._callers import caller

pytestmark = pytest.mark.integration


class _Echo:
    """A model that repeats its prompt and adds what nobody gave it."""

    def __init__(self, name: str, phone: str) -> None:
        self.name, self.phone = name, phone
        self.received: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = request.content.decode("utf-8")
        self.received.append(body)
        prompt = json.loads(body)["messages"][-1]["content"]
        content = f"Entwurf: {prompt} Rückruf: {self.phone}. Gruß an {self.name}."
        return httpx.Response(200, json={
            "id": f"echo-{uuid4().hex}", "model": "echo",
            "choices": [{"message": {"content": content}}], "usage": {}})


@pytest.fixture
def echo(monkeypatch) -> _Echo:
    suffix = uuid4().hex[:8]
    model = _Echo(name=f"Erika Probe{suffix}", phone=f"0171 55{int(suffix, 16) % 10**6:06d}")
    real_client = httpx.AsyncClient

    def client(**kw):
        return real_client(transport=httpx.MockTransport(model), **kw)

    async def rule(*_a, **_k):
        return RoutingRule("draft_reply", ("echo",), uuid4(), uuid4(), 1, max_tokens=256)

    monkeypatch.setattr(completion.httpx, "AsyncClient", client)
    monkeypatch.setattr(workflows, "route_for", rule)
    return model


async def _workflow(pool, scope: UUID) -> UUID:
    """draft → revise → approve: two model calls, then a gate."""
    workflow = await pool.fetchval(
        "INSERT INTO workflow_definitions (scope_id, name, status) VALUES ($1, 'it', 'active') "
        "RETURNING id", scope)
    ids = {}
    for ref, node_type, mapping in (
        ("draft", "llm_call", {"prompt": "run.letter"}),
        ("revise", "llm_call", {"prompt": "draft.content"}),
        ("approve", "human_review", {}),
    ):
        ids[ref] = await pool.fetchval(
            "INSERT INTO workflow_nodes (workflow_id, node_ref, node_type, task_type, "
            "input_mapping) VALUES ($1, $2, $3, $4, $5::text::jsonb) RETURNING id",
            workflow, ref, node_type, "draft_reply" if node_type == "llm_call" else None,
            json.dumps(mapping))
    for source, target in (("draft", "revise"), ("revise", "approve")):
        await pool.execute(
            "INSERT INTO workflow_connections (workflow_id, source_node_id, target_node_id) "
            "VALUES ($1, $2, $3)", workflow, ids[source], ids[target])
    return workflow


def _engine(pool, system_pool) -> orchestration.Engine:
    return orchestration.Engine(
        registry=ExecutorRegistry({"llm_call": workflows._call_model_for(pool)}),
        pool=system_pool,
        placeholders=orchestration.Placeholders(
            policy_for=partial(policy_or_shipped, pool), keys=mapping_keys()),
    )


async def _start(pool, system_pool, scope: UUID, echo: _Echo, *, email: str, starter=None):
    workflow = await _workflow(pool, scope)
    engine = _engine(pool, system_pool)
    return await orchestration.start_workflow_run(
        pool, starter or caller("contributor"), workflow,
        WorkflowRunStart(input_data={
            "letter": f"Bitte an {echo.name} schreiben, Kopie an {email}.",
            "case": {"contact": [email]},
            "identifiers_to_redact": {"PERSON": [echo.name]},
        }),
        engine=engine,
    )


async def _tables_holding(owner_pool, needle: str) -> dict[str, int]:
    """Every table of the schema whose rows contain `needle`, as the owner --
    who is not subject to row-level security and sees every row."""
    tables = [r["tablename"] for r in await owner_pool.fetch(
        "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename")]
    hits = {}
    for table in tables:
        # The name comes from the catalogue just read, not from outside.
        count = await owner_pool.fetchval(
            f'SELECT count(*) FROM "{table}" t WHERE strpos(lower(t::text), lower($1)) > 0',  # noqa: S608
            needle)
        if count:
            hits[table] = count
    return hits


async def test_no_value_of_the_run_is_stored_as_written(pool, system_pool, owner_pool,
                                                        scope, echo):
    email = f"erika.{uuid4().hex[:8]}@example.de"
    advance = await _start(pool, system_pool, scope, echo, email=email)
    assert advance.run.status == "awaiting_oversight", advance.run

    for value in (echo.name, email, echo.phone):
        assert await _tables_holding(owner_pool, value) == {}, value


async def test_the_model_saw_placeholders_only(pool, system_pool, scope, echo):
    email = f"erika.{uuid4().hex[:8]}@example.de"
    await _start(pool, system_pool, scope, echo, email=email)

    assert len(echo.received) == 2, "two model calls"
    for body in echo.received:
        assert echo.name not in body and email not in body
    # The second call read the first answer: the phone number the model wrote
    # came back to it as a placeholder, not as written.
    assert echo.phone not in echo.received[1]


async def test_the_values_are_kept_in_the_sealed_mapping(pool, system_pool, scope, echo):
    """Not lost, only locked: the mapping opens with the key and holds every
    value, including the one the model added -- and the stored text resolves."""
    email = f"erika.{uuid4().hex[:8]}@example.de"
    advance = await _start(pool, system_pool, scope, echo, email=email)
    run_id = advance.run.id

    row = await system_pool.fetchrow(
        "SELECT key_id, nonce, ciphertext FROM pii_mappings WHERE run_id = $1", run_id)
    mapping = mapping_cipher.unseal(
        mapping_cipher.Sealed(row["key_id"], row["nonce"], row["ciphertext"]),
        run_id=run_id, keys=mapping_keys())
    assert set(mapping.values()) == {echo.name, email, echo.phone}

    revised = await system_pool.fetchval(
        "SELECT e.output_data->>'content' FROM node_executions e "
        "JOIN workflow_nodes n ON n.id = e.node_id WHERE e.run_id = $1 AND n.node_ref = 'revise'",
        run_id)
    names = {v: k for k, v in mapping.items()}
    assert "{{" + names[echo.phone] + "}}" in revised
    assert "{{" + names[echo.name] + "}}" in revised
    assert advance.run.input_data == {
        "letter": "Bitte an {{" + names[echo.name] + "}} schreiben, Kopie an {{"
                  + names[email] + "}}.",
        "case": {"contact": ["{{" + names[email] + "}}"]},
    }


async def test_two_steps_extending_at_once_take_turns_at_the_lock(pool, system_pool, scope,
                                                                   echo):
    """The row lock of E1, against PostgreSQL: a second transaction that wants
    the mapping waits until the first has written it."""
    import asyncio

    email = f"erika.{uuid4().hex[:8]}@example.de"
    run_id = (await _start(pool, system_pool, scope, echo, email=email)).run.id
    order: list[str] = []

    async with system_pool.acquire() as first, first.transaction():
        await orchestration.store.lock_pii_mapping(first, run_id)
        order.append("first holds")

        async def second():
            async with system_pool.acquire() as conn, conn.transaction():
                await orchestration.store.lock_pii_mapping(conn, run_id)
                order.append("second holds")

        waiting = asyncio.create_task(second())
        await asyncio.sleep(0.3)
        order.append("first releases")
    await waiting
    assert order == ["first holds", "first releases", "second holds"]


# ── resolving (E4) ────────────────────────────────────────────────────────────

class _People:
    """The starter, two members of the run's scope, and someone from outside."""

    def __init__(self, **kw):
        self.__dict__.update(kw)


@pytest.fixture
async def people(pool, scope) -> _People:
    found = _People(starter=caller("contributor"), reviewer=caller("reviewer"),
                    colleague=caller("contributor"), stranger=caller("reviewer"))
    for who, role in ((found.reviewer, "reviewer"), (found.colleague, "contributor")):
        actor = await resolve_actor(pool, who)
        await pool.execute(
            "INSERT INTO scope_memberships (id, scope_id, actor_id, role) VALUES ($1, $2, $3, $4)",
            uuid4(), scope, actor.id, role)
    return found


@pytest.fixture
async def resolvable(pool, system_pool, scope, echo, people):
    email = f"erika.{uuid4().hex[:8]}@example.de"
    advance = await _start(pool, system_pool, scope, echo, email=email, starter=people.starter)
    return advance.run.id, email


async def _resolve(pool, who, run_id, nodes=None):
    return await orchestration.resolve_run(
        pool, who, run_id, RunResolveRequest(nodes=nodes), keys=mapping_keys())


class TestResolving:
    async def test_the_starter_and_a_reviewer_who_sees_the_run_get_the_values(
            self, pool, people, resolvable, echo):
        run_id, email = resolvable
        for who in (people.starter, people.reviewer):
            out = await _resolve(pool, who, run_id)
            assert out.input_data["case"] == {"contact": [email]}
            assert echo.name in out.input_data["letter"]
            revise = next(n for n in out.nodes if n.node_ref == "revise")
            assert echo.phone in revise.output["content"]
            assert echo.name in revise.output["content"]

    async def test_a_colleague_without_the_role_reads_placeholders_only(
            self, pool, people, resolvable, echo):
        """Sees the run (member of its scope), may not have it resolved."""
        run_id, _ = resolvable
        assert echo.name not in json.dumps(
            (await orchestration.get_run(pool, people.colleague, run_id)).input_data)
        with pytest.raises(AccessDenied):
            await _resolve(pool, people.colleague, run_id)

    async def test_someone_who_does_not_see_the_run_learns_nothing(self, pool, people,
                                                                   resolvable):
        """Not found, as everywhere: a refusal would say the run exists."""
        run_id, _ = resolvable
        with pytest.raises(RunNotFound):
            await _resolve(pool, people.stranger, run_id)

    async def test_every_resolution_is_audited_without_a_value(self, pool, system_pool, people,
                                                               resolvable, echo):
        run_id, email = resolvable
        await _resolve(pool, people.reviewer, run_id, nodes=["draft"])
        rows = await system_pool.fetch(
            "SELECT actor_id, payload::text AS payload FROM audit_log "
            "WHERE action = 'workflow.run_content_resolved' AND target_ref = $1", f"workflow_run:{run_id}")
        assert len(rows) == 1
        reviewer = await resolve_actor(pool, people.reviewer)
        assert rows[0]["actor_id"] == reviewer.id
        assert json.loads(rows[0]["payload"])["nodes"] == ["draft"]
        for value in (echo.name, email, echo.phone):
            assert value not in rows[0]["payload"]

    async def test_only_the_named_steps_come_back(self, pool, people, resolvable):
        run_id, _ = resolvable
        out = await _resolve(pool, people.starter, run_id, nodes=["draft"])
        assert [n.node_ref for n in out.nodes] == ["draft"]

    async def test_a_purged_run_has_nothing_left_to_resolve(self, pool, system_pool, people,
                                                            resolvable):
        run_id, _ = resolvable
        await system_pool.execute(
            "UPDATE workflow_runs SET status = 'complete' WHERE id = $1", run_id)
        async with system_pool.acquire() as conn:
            await orchestration.store.clear_run_content(conn, [run_id])
        with pytest.raises(RunContentPurged):
            await _resolve(pool, people.starter, run_id)

    async def test_a_reason_at_the_gate_is_stored_with_placeholders(
            self, pool, system_pool, owner_pool, people, resolvable, echo):
        """E7: the reviewer names the person in the reason. Stored nowhere as
        written -- the whole database is searched again -- and readable for
        whoever may resolve."""
        run_id, _ = resolvable
        await orchestration.record_oversight(
            pool, people.reviewer, run_id,
            OversightDecision(decision="rejected", reason=f"Nicht die Akte von {echo.name}"),
            engine=_engine(pool, system_pool))

        assert await _tables_holding(owner_pool, echo.name) == {}
        gate = next(n for n in (await _resolve(pool, people.reviewer, run_id)).nodes
                    if n.node_ref == "approve")
        assert gate.error_message == f"Nicht die Akte von {echo.name}"
