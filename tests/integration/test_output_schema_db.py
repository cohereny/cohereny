"""
tests/integration/test_output_schema_db.py

An llm_call with an output schema, from the stored node to the checked answer, on
a real PostgreSQL (CHY-175).

The unit tests drive the check over a fake database. This goes the whole way the
schema travels: written into `workflow_nodes.output_schema` (migration 039), read
back when the node is claimed, the real executor asks a mock gateway for JSON,
and the service checks the answer before anything is recorded. The gateway keeps
every request body, so what was asked for is checked too, not only what came back.
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

RISK = {
    "type": "object",
    "required": ["risk_level"],
    "properties": {"risk_level": {"enum": ["low", "high"]}},
}


class _Gateway:
    """A model that always gives the same answer and keeps what it was asked."""

    def __init__(self, answer: str) -> None:
        self.answer = answer
        self.bodies: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.bodies.append(json.loads(request.content))
        return httpx.Response(200, json={
            "id": f"gw-{uuid4().hex}", "model": "m",
            "choices": [{"message": {"content": self.answer}}], "usage": {}})


@pytest.fixture
def gateway(monkeypatch):
    def answering(answer: str) -> _Gateway:
        model = _Gateway(answer)
        real_client = httpx.AsyncClient
        monkeypatch.setattr(completion.httpx, "AsyncClient",
                            lambda **kw: real_client(transport=httpx.MockTransport(model), **kw))
        return model

    async def rule(*_a, **_k):
        return RoutingRule("classify", ("m",), uuid4(), uuid4(), 1, max_tokens=64)

    monkeypatch.setattr(workflows, "route_for", rule)
    return answering


async def _workflow(pool, scope: UUID, schema: dict | None) -> UUID:
    """classify → review: one model call with or without a schema, then a gate."""
    workflow = await pool.fetchval(
        "INSERT INTO workflow_definitions (scope_id, name, status) VALUES ($1, 'it', 'active') "
        "RETURNING id", scope)
    classify = await pool.fetchval(
        "INSERT INTO workflow_nodes (workflow_id, node_ref, node_type, task_type, "
        "input_mapping, output_schema) "
        "VALUES ($1, 'classify', 'llm_call', 'classify', $2::text::jsonb, $3::text::jsonb) "
        "RETURNING id",
        workflow, json.dumps({"prompt": "run.text"}),
        None if schema is None else json.dumps(schema))
    review = await pool.fetchval(
        "INSERT INTO workflow_nodes (workflow_id, node_ref, node_type) "
        "VALUES ($1, 'review', 'human_review') RETURNING id", workflow)
    await pool.execute(
        "INSERT INTO workflow_connections (workflow_id, source_node_id, target_node_id) "
        "VALUES ($1, $2, $3)", workflow, classify, review)
    return workflow


async def _run(pool, system_pool, scope: UUID, schema: dict | None):
    engine = orchestration.Engine(
        registry=ExecutorRegistry({"llm_call": workflows._call_model_for(pool)}),
        pool=system_pool,
        placeholders=orchestration.Placeholders(
            policy_for=partial(policy_or_shipped, pool), keys=mapping_keys()),
    )
    return await orchestration.start_workflow_run(
        pool, caller("contributor"), await _workflow(pool, scope, schema),
        WorkflowRunStart(input_data={"text": "Bitte das Risiko einstufen."}),
        engine=engine,
    )


async def _classify(system_pool, run_id: UUID):
    return await system_pool.fetchrow(
        "SELECT e.status, e.output_data, e.error_message FROM node_executions e "
        "JOIN workflow_nodes n ON n.id = e.node_id "
        "WHERE e.run_id = $1 AND n.node_ref = 'classify'", run_id)


def _data(row) -> dict:
    output = row["output_data"]
    return json.loads(output) if isinstance(output, str) else dict(output)


async def test_a_matching_answer_reaches_the_gate_with_data(pool, system_pool, scope, gateway):
    model = gateway('{"risk_level": "high"}')
    advance = await _run(pool, system_pool, scope, RISK)

    assert advance.run.status == "awaiting_oversight"
    row = await _classify(system_pool, advance.run.id)
    assert row["status"] == "complete"
    assert _data(row)["data"] == {"risk_level": "high"}
    assert model.bodies[0]["response_format"] == {"type": "json_object"}


async def test_a_mismatch_fails_the_run_and_says_where_not_what(pool, system_pool, scope, gateway):
    gateway('{"risk_level": "severe"}')
    advance = await _run(pool, system_pool, scope, RISK)

    assert advance.run.status == "failed"
    row = await _classify(system_pool, advance.run.id)
    assert row["status"] == "failed"
    assert "at $.risk_level: enum" in row["error_message"]
    assert "severe" not in row["error_message"]
    stored = await system_pool.fetchval(
        "SELECT error_message FROM workflow_runs WHERE id = $1", advance.run.id)
    assert "severe" not in stored


async def test_without_a_schema_nothing_is_asked_for_or_checked(pool, system_pool, scope,
                                                                gateway):
    """The counter-probe of the acceptance: the same answer, no schema, no refusal."""
    model = gateway('{"risk_level": "severe"}')
    advance = await _run(pool, system_pool, scope, None)

    assert advance.run.status == "awaiting_oversight"
    assert "data" not in _data(await _classify(system_pool, advance.run.id))
    assert "response_format" not in model.bodies[0]
