"""
tests/integration/test_loop_limit_db.py

A loop that runs out, on a real PostgreSQL (CHY-150, Thomas 09.11.2026).

`draft → check`, back to `draft` while the check says the draft is not good
enough, at most twice; the default leads on. The model is a mock that never
lets the check pass. What only the database shows:

- the run goes on along the default edge to the review once the loop ran out,
  and the entry `workflow.loop_exhausted` stands in the real `audit_log`
- whoever reads the run before deciding sees the loop that ran out, derived
  from `loop_counters`
- without a default the run ends as failed, saying which edge and which limit

Until CHY-150 the run ended as `complete` in both cases: no review, nothing in
the log, only a counter at its limit.
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
from contexts.orchestration.execution import ExecutorRegistry
from contexts.orchestration.schema import WorkflowRunStart
from core import completion
from router.rules import RoutingRule
from tests.integration._callers import caller

pytestmark = pytest.mark.integration

VERDICT = {"type": "object", "required": ["ok"], "properties": {"ok": {"type": "boolean"}}}
NOT_YET = {"op": "eq", "field": "data.ok", "value": False}


@pytest.fixture
def never_good_enough(monkeypatch):
    """A gateway whose check never passes."""
    real_client = httpx.AsyncClient

    def answer(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "id": f"gw-{uuid4().hex}", "model": "m",
            "choices": [{"message": {"content": json.dumps({"ok": False})}}], "usage": {}})

    monkeypatch.setattr(completion.httpx, "AsyncClient",
                        lambda **kw: real_client(transport=httpx.MockTransport(answer), **kw))

    async def rule(*_a, **_k):
        return RoutingRule("draft", ("m",), uuid4(), uuid4(), 1, max_tokens=256)

    monkeypatch.setattr(workflows, "route_for", rule)


async def _node(pool, workflow: UUID, ref: str, node_type: str, **columns) -> UUID:
    names = ["workflow_id", "node_ref", "node_type", *columns]
    casts = ["", "", "", *("::text::jsonb" if k in {"input_mapping", "output_schema"} else ""
                           for k in columns)]
    placeholders = ", ".join(f"${i}{cast}" for i, cast in enumerate(casts, start=1))
    return await pool.fetchval(
        f"INSERT INTO workflow_nodes ({', '.join(names)}) VALUES ({placeholders}) RETURNING id",
        workflow, ref, node_type, *columns.values())


async def _edge(pool, workflow: UUID, source: UUID, target: UUID, *,
                condition: dict | None = None, label: str | None = None,
                loop_limit: int | None = None) -> None:
    await pool.execute(
        "INSERT INTO workflow_connections (workflow_id, source_node_id, target_node_id, "
        "condition, label, loop_limit) VALUES ($1, $2, $3, $4::text::jsonb, $5, $6)",
        workflow, source, target, json.dumps(condition) if condition else None, label, loop_limit)


async def _loop(pool, scope: UUID, *, with_review: bool) -> UUID:
    workflow = await pool.fetchval(
        "INSERT INTO workflow_definitions (scope_id, name, status) VALUES ($1, 'it', 'active') "
        "RETURNING id", scope)
    start = await _node(pool, workflow, "start", "llm_call", task_type="draft",
                        input_mapping=json.dumps({"prompt": "run.brief"}))
    draft = await _node(pool, workflow, "draft", "llm_call", task_type="draft",
                        input_mapping=json.dumps({"prompt": "run.brief"}))
    check = await _node(pool, workflow, "check", "llm_call", task_type="draft",
                        input_mapping=json.dumps({"prompt": "draft.content"}),
                        output_schema=json.dumps(VERDICT))
    await _edge(pool, workflow, start, draft)
    await _edge(pool, workflow, draft, check)
    await _edge(pool, workflow, check, draft, condition=NOT_YET, loop_limit=2)
    if with_review:
        review = await _node(pool, workflow, "review", "human_review")
        await _edge(pool, workflow, check, review, label="default")
    return workflow


async def _run(pool, system_pool, workflow: UUID, starter):
    engine = orchestration.Engine(
        registry=ExecutorRegistry({"llm_call": workflows._call_model_for(pool)}),
        pool=system_pool,
        placeholders=orchestration.Placeholders(
            policy_for=partial(policy_or_shipped, pool), keys=mapping_keys()),
    )
    return (await orchestration.start_workflow_run(
        pool, starter, workflow, WorkflowRunStart(input_data={"brief": "Antwort an Kundin"}),
        engine=engine)).run


async def _exhausted(system_pool, run_id: UUID) -> list[dict]:
    rows = await system_pool.fetch(
        "SELECT payload::text AS payload FROM audit_log "
        "WHERE action = 'workflow.loop_exhausted' AND target_ref = $1", f"workflow_run:{run_id}")
    return [json.loads(r["payload"]) for r in rows]


async def test_a_loop_that_runs_out_goes_on_to_the_review_and_says_so(
        pool, system_pool, scope, never_good_enough):
    starter = caller("contributor")
    run = await _run(pool, system_pool, await _loop(pool, scope, with_review=True), starter)

    assert run.status == "awaiting_oversight", run.error_message
    checks = await system_pool.fetchval(
        "SELECT count(*) FROM node_executions e JOIN workflow_nodes n ON n.id = e.node_id "
        "WHERE e.run_id = $1 AND n.node_ref = 'check'", run.id)
    assert checks == 3
    assert await _exhausted(system_pool, run.id) == [{
        "node_ref": "check", "to": "draft", "loop_limit": 2,
        "went_on_to": ["review"], "run_failed": False}]

    seen = await orchestration.run_nodes(pool, starter, run.id)
    assert [(e.from_ref, e.to_ref, e.loop_limit) for e in seen.exhausted_loops] == [
        ("check", "draft", 2)]


async def test_with_no_way_on_the_run_fails_and_names_the_limit(
        pool, system_pool, scope, never_good_enough):
    run = await _run(pool, system_pool, await _loop(pool, scope, with_review=False),
                     caller("contributor"))

    assert run.status == "failed"
    assert "check → draft (loop_limit of 2)" in run.error_message
    assert [e["run_failed"] for e in await _exhausted(system_pool, run.id)] == [True]
