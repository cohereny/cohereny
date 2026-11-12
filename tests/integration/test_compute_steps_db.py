"""
tests/integration/test_compute_steps_db.py

Compute steps on a real PostgreSQL (CHY-173, ADR-048).

Two things only the database path shows. Activation refuses a step the code
does not have — the acceptance of CHY-173, checked where a release is decided.
And a chain the banking workflow will use runs end to end with the real
executors: a model reads figures into `data` (CHY-175), two steps compute on
them, the run stops at the gate. The gateway is a mock that answers with the
figures; everything after it is the product.
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

FIGURES = {
    "bilanzsumme_current": 5_000_000, "eigenkapital_current": 1_250_000,
    "umlaufvermoegen_current": 1_500_000, "kurzfristige_verbindlichkeiten_current": 2_200_000,
    "kurzfristige_verbindlichkeiten_prior": 1_400_000, "umsatz_current": 6_000_000,
    "ebit_current": 150_000, "abschreibungen_current": 200_000,
}
FIGURES_SCHEMA = {
    "type": "object", "required": sorted(FIGURES),
    "properties": {key: {"type": "number"} for key in FIGURES},
}


async def _node(pool, workflow: UUID, ref: str, node_type: str, **columns) -> UUID:
    names = ["workflow_id", "node_ref", "node_type", *columns]
    values = [workflow, ref, node_type, *columns.values()]
    casts = ["", "", "", *("::text::jsonb" if k in {"input_mapping", "output_schema"} else ""
                           for k in columns)]
    placeholders = ", ".join(f"${i}{cast}" for i, cast in enumerate(casts, start=1))
    return await pool.fetchval(
        f"INSERT INTO workflow_nodes ({', '.join(names)}) VALUES ({placeholders}) RETURNING id",
        *values)


async def _edge(pool, workflow: UUID, source: UUID, target: UUID) -> None:
    await pool.execute(
        "INSERT INTO workflow_connections (workflow_id, source_node_id, target_node_id) "
        "VALUES ($1, $2, $3)", workflow, source, target)


async def _definition(pool, scope: UUID, *, status: str = "draft") -> UUID:
    return await pool.fetchval(
        "INSERT INTO workflow_definitions (scope_id, name, status) VALUES ($1, 'it', $2) "
        "RETURNING id", scope, status)


# ── activation ────────────────────────────────────────────────────────────────

async def _rules(pool, workflow: UUID) -> dict[str, str | None]:
    out = await orchestration.validate_workflow(
        pool, caller("consumer"), workflow,
        limits=orchestration.DocumentLimits(max_bytes=10 * 1024 * 1024, max_pages=200))
    return {v.rule: v.node_ref for v in out.violations}


async def test_a_step_the_code_does_not_have_cannot_be_released(pool, scope):
    workflow = await _definition(pool, scope)
    await _node(pool, workflow, "calc", "compute", function="banking.ratios", function_version=2)

    rules = await _rules(pool, workflow)
    assert rules.get("unknown_function") == "calc"


async def test_a_registered_step_passes_that_rule(pool, scope):
    """The counter-probe: the rule fires on the unknown step, not on every step."""
    workflow = await _definition(pool, scope)
    await _node(pool, workflow, "calc", "compute", function="banking.ratios", function_version=1)

    assert "unknown_function" not in await _rules(pool, workflow)


# ── a run ─────────────────────────────────────────────────────────────────────

@pytest.fixture
def figures_from_the_model(monkeypatch):
    """A gateway that answers with the figures, as JSON, every time."""
    real_client = httpx.AsyncClient

    def answer(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "id": f"gw-{uuid4().hex}", "model": "m",
            "choices": [{"message": {"content": json.dumps(FIGURES)}}], "usage": {}})

    monkeypatch.setattr(completion.httpx, "AsyncClient",
                        lambda **kw: real_client(transport=httpx.MockTransport(answer), **kw))

    async def rule(*_a, **_k):
        return RoutingRule("extract_kpis", ("m",), uuid4(), uuid4(), 1, max_tokens=256)

    monkeypatch.setattr(workflows, "route_for", rule)


async def test_figures_read_by_a_model_are_computed_on_and_the_run_waits_at_the_gate(
        pool, system_pool, scope, figures_from_the_model):
    workflow = await _definition(pool, scope, status="active")
    extract = await _node(pool, workflow, "extract", "llm_call", task_type="extract_kpis",
                          input_mapping=json.dumps({"prompt": "run.statement"}),
                          output_schema=json.dumps(FIGURES_SCHEMA))
    ratios = await _node(pool, workflow, "ratios", "compute",
                         function="banking.ratios", function_version=1,
                         input_mapping=json.dumps({"kpis": "extract.data"}))
    anomalies = await _node(pool, workflow, "anomalies", "compute",
                            function="banking.anomalies", function_version=1,
                            input_mapping=json.dumps({"ratios": "ratios.data"}))
    review = await _node(pool, workflow, "review", "human_review")
    for source, target in ((extract, ratios), (ratios, anomalies), (anomalies, review)):
        await _edge(pool, workflow, source, target)

    engine = orchestration.Engine(
        registry=ExecutorRegistry({"llm_call": workflows._call_model_for(pool)}),
        pool=system_pool,
        placeholders=orchestration.Placeholders(
            policy_for=partial(policy_or_shipped, pool), keys=mapping_keys()),
    )
    advance = await orchestration.start_workflow_run(
        pool, caller("contributor"), workflow,
        WorkflowRunStart(input_data={"statement": "Jahresabschluss 2025, Maschinenbau GmbH"}),
        engine=engine)

    assert advance.run.status == "awaiting_oversight"
    rows = {row["node_ref"]: row for row in await system_pool.fetch(
        "SELECT n.node_ref, e.status, e.output_data FROM node_executions e "
        "JOIN workflow_nodes n ON n.id = e.node_id WHERE e.run_id = $1", advance.run.id)}

    def output(ref: str) -> dict:
        raw = rows[ref]["output_data"]
        return json.loads(raw) if isinstance(raw, str) else dict(raw)

    assert output("ratios")["function"] == "banking.ratios@1"
    assert output("ratios")["data"]["current_ratio"] == pytest.approx(1_500_000 / 2_200_000)
    assert output("anomalies")["data"]["count"] == 2
