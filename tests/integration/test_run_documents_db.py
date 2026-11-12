"""
tests/integration/test_run_documents_db.py

Documents of a run on a real PostgreSQL (CHY-174, ADR-049).

What only the database shows:

  - migration 041 holds the node: a document node names its document, the
    fields belong to it alone, one node per name and workflow
  - activation refuses a node above the installation's ceiling
  - row-level security: a run's documents are its starter's to send and its
    readers' to see; somebody else does not even find the run
  - a whole run, the shape of CHY-177 way 1: the run waits, two documents
    arrive — one as PDF, one as text —, the last starts it, and the bank
    statement reaches the draft and not the extraction. The model is a mock
    that records what it was sent; everything around it is the product. No
    table and no request to the model carries the name listed at the start

The purge of a run that waited in vain is in test_retention_purge_db.py.
"""
from __future__ import annotations

import json
from functools import partial
from uuid import UUID, uuid4

import asyncpg
import httpx
import pytest

from api.v1 import workflows
from api.v1._redaction import mapping_keys, policy_or_shipped
from contexts.orchestration import service as orchestration
from contexts.orchestration.errors import (
    DocumentNodeExists,
    NodeNotStorable,
    RunNotFound,
)
from contexts.orchestration.execution import ExecutorRegistry
from contexts.orchestration.schema import NodeCreate, WorkflowRunStart
from core import completion, ingress
from router.rules import RoutingRule
from tests.fixtures.documents import POISONED_DOCUMENT
from tests.fixtures.pdfs import pdf
from tests.integration._callers import caller

pytestmark = pytest.mark.integration

LIMITS = orchestration.DocumentLimits(max_bytes=10 * 1024 * 1024, max_pages=200)
_ERIKA = "Erika Mustermann"
_STATEMENT = f"Kontoauszug März 2025\nGutschrift {_ERIKA} (Miete) 1.200,00 EUR\n"
_ANNUAL = "Jahresabschluss 2025\nEigenkapital 1.250.000 EUR"
FIGURES = {"eigenkapital_current": 1_250_000}
FIGURES_SCHEMA = {"type": "object", "required": ["eigenkapital_current"],
                  "properties": {"eigenkapital_current": {"type": "number"}}}


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


# ── the node (migration 041) ──────────────────────────────────────────────────

class TestTheNode:
    async def test_a_document_node_names_its_document(self, pool, scope):
        workflow = await _definition(pool, scope)
        with pytest.raises(NodeNotStorable, match="needs a document_name"):
            await orchestration.add_node(pool, caller("contributor"), workflow,
                                         NodeCreate(node_ref="doc", node_type="document"))

    async def test_its_fields_belong_to_it_alone(self, pool, scope):
        workflow = await _definition(pool, scope)
        with pytest.raises(NodeNotStorable, match="only a document node"):
            await orchestration.add_node(pool, caller("contributor"), workflow, NodeCreate(
                node_ref="call", node_type="llm_call", task_type="summarize",
                document_name="kontoauszug"))

    async def test_one_node_per_document(self, pool, scope):
        workflow = await _definition(pool, scope)
        author = caller("contributor")
        await orchestration.add_node(pool, author, workflow, NodeCreate(
            node_ref="first", node_type="document", document_name="kontoauszug"))
        with pytest.raises(DocumentNodeExists):
            await orchestration.add_node(pool, author, workflow, NodeCreate(
                node_ref="second", node_type="document", document_name="kontoauszug"))

    async def test_the_same_name_in_another_workflow_is_its_own(self, pool, scope):
        """Counter-probe: unique per workflow, not across them."""
        author = caller("contributor")
        for _ in range(2):
            workflow = await _definition(pool, scope)
            await orchestration.add_node(pool, author, workflow, NodeCreate(
                node_ref="doc", node_type="document", document_name="kontoauszug"))

    @pytest.mark.parametrize("column", ["document_max_bytes", "document_max_pages"])
    async def test_a_limit_of_zero_is_unstorable(self, pool, scope, column):
        workflow = await _definition(pool, scope)
        with pytest.raises(asyncpg.CheckViolationError):
            await _node(pool, workflow, "doc", "document", document_name="x", **{column: 0})

    async def test_a_name_that_is_no_path_segment_is_unstorable(self, pool, scope):
        """For rows that do not pass through NodeCreate."""
        workflow = await _definition(pool, scope)
        with pytest.raises(asyncpg.CheckViolationError):
            await _node(pool, workflow, "doc", "document", document_name="../kontoauszug")


class TestActivation:
    async def _rules(self, pool, workflow: UUID) -> dict[str, str | None]:
        out = await orchestration.validate_workflow(pool, caller("consumer"), workflow,
                                                    limits=LIMITS)
        return {v.rule: v.node_ref for v in out.violations}

    async def test_a_node_above_the_ceiling_cannot_be_released(self, pool, scope):
        workflow = await _definition(pool, scope)
        await _node(pool, workflow, "statement", "document", document_name="kontoauszug",
                    document_max_bytes=LIMITS.max_bytes + 1)
        assert (await self._rules(pool, workflow)).get(
            "document_limit_above_installation") == "statement"

    async def test_a_node_below_it_passes_that_rule(self, pool, scope):
        workflow = await _definition(pool, scope)
        await _node(pool, workflow, "statement", "document", document_name="kontoauszug",
                    document_max_bytes=1024, document_max_pages=5)
        assert "document_limit_above_installation" not in await self._rules(pool, workflow)


# ── a whole run ───────────────────────────────────────────────────────────────

@pytest.fixture
def model(monkeypatch) -> list[dict]:
    """A gateway that answers with the figures, and keeps every request it got."""
    real_client = httpx.AsyncClient
    requests: list[dict] = []

    def answer(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={
            "id": f"gw-{uuid4().hex}", "model": "m",
            "choices": [{"message": {"content": json.dumps(FIGURES)}}], "usage": {}})

    monkeypatch.setattr(completion.httpx, "AsyncClient",
                        lambda **kw: real_client(transport=httpx.MockTransport(answer), **kw))

    async def rule(*_a, **_k):
        return RoutingRule("extract_kpis", ("m",), uuid4(), uuid4(), 1, max_tokens=256)

    monkeypatch.setattr(workflows, "route_for", rule)
    return requests


async def _banking_shape(pool, scope) -> UUID:
    """CHY-177 way 1, reduced: the annual statement feeds the extraction, the bank
    statement has one edge, to the draft. Both end at the gate."""
    workflow = await _definition(pool, scope, status="active")
    annual = await _node(pool, workflow, "annual", "document", document_name="jahresabschluss")
    statement = await _node(pool, workflow, "statement", "document", document_name="kontoauszug")
    extract = await _node(pool, workflow, "extract", "llm_call", task_type="extract_kpis",
                          input_mapping=json.dumps({"prompt": "annual.text"}),
                          output_schema=json.dumps(FIGURES_SCHEMA))
    draft = await _node(pool, workflow, "draft", "llm_call", task_type="extract_kpis",
                        input_mapping=json.dumps({"prompt": "statement.text"}))
    review = await _node(pool, workflow, "review", "human_review", join_policy="wait_all")
    for source, target in ((annual, extract), (statement, draft),
                           (extract, review), (draft, review)):
        await _edge(pool, workflow, source, target)
    return workflow


def _engine(pool, system_pool) -> orchestration.Engine:
    return orchestration.Engine(
        registry=ExecutorRegistry({"llm_call": workflows._call_model_for(pool)}),
        pool=system_pool,
        placeholders=orchestration.Placeholders(
            policy_for=partial(policy_or_shipped, pool), keys=mapping_keys()),
    )


async def _upload(pool, engine, who, run_id: UUID, name: str, data: bytes, content_type: str):
    return await orchestration.receive_document(
        pool, who, orchestration.DocumentUpload(run_id, name, data, content_type),
        engine=engine, limits=LIMITS)


async def test_the_bank_statement_reaches_the_draft_and_nothing_else(
        pool, system_pool, scope, model):
    workflow = await _banking_shape(pool, scope)
    engine = _engine(pool, system_pool)
    starter = caller("contributor")

    advance = await orchestration.start_workflow_run(
        pool, starter, workflow,
        WorkflowRunStart(input_data={"case": "K-17",
                                     "identifiers_to_redact": {"PERSON": [_ERIKA]}}),
        engine=engine)
    run_id = advance.run.id
    assert advance.run.status == "awaiting_documents"
    assert advance.run.started_at is None

    # Somebody else does not even find the run (CHY-92).
    with pytest.raises(RunNotFound):
        await _upload(pool, engine, caller("contributor"), run_id, "kontoauszug",
                      _STATEMENT.encode(), "text/plain")

    waiting = await _upload(pool, engine, starter, run_id, "jahresabschluss",
                            pdf(_ANNUAL), "application/pdf")
    assert waiting.status == "awaiting_documents" and model == []

    done = await _upload(pool, engine, starter, run_id, "kontoauszug",
                         _STATEMENT.encode(), "text/plain; charset=utf-8")
    assert done.status == "awaiting_oversight"
    assert all(d.received for d in done.documents)

    # What each model call was sent: the extraction never the bank statement.
    assert len(model) == 2
    sent = [json.dumps(r, ensure_ascii=False) for r in model]
    [to_extract] = [s for s in sent if "Jahresabschluss 2025" in s]
    [to_draft] = [s for s in sent if "Gutschrift" in s]
    assert "Gutschrift" not in to_extract and "Kontoauszug" not in to_extract
    assert "Gutschrift {{PERSON_1}} (Miete)" in to_draft
    assert ingress.FENCE_OPEN in to_draft

    # And what each step received, which is more than its prompt: a document in
    # the run input would sit under `run` for every step, read or not.
    received = {r["node_ref"]: r["input"] for r in await system_pool.fetch(
        "SELECT n.node_ref, e.input_data::text AS input FROM node_executions e "
        "JOIN workflow_nodes n ON n.id = e.node_id WHERE e.run_id = $1", run_id)}
    assert "Gutschrift" not in received["extract"]
    assert "Gutschrift" in received["draft"]

    # The name listed at the start is nowhere: not in a request, not in a table.
    assert all(_ERIKA not in s for s in sent)
    for table, column in (("workflow_run_documents", "text"),
                          ("node_executions", "input_data::text"),
                          ("node_executions", "output_data::text"),
                          ("workflow_runs", "input_data::text")):
        key = "run_id" if table != "workflow_runs" else "id"
        rows = await system_pool.fetch(
            f"SELECT {column} AS v FROM {table} WHERE {key} = $1", run_id)
        assert rows and all(_ERIKA not in (r["v"] or "") for r in rows), f"{table}.{column}"

    started = await system_pool.fetchval(
        "SELECT started_at FROM workflow_runs WHERE id = $1", run_id)
    assert started is not None, "the run starts with its last document"


async def test_what_is_kept_and_audited(pool, system_pool, scope, model):
    workflow = await _banking_shape(pool, scope)
    engine = _engine(pool, system_pool)
    starter = caller("contributor")
    run_id = (await orchestration.start_workflow_run(
        pool, starter, workflow, WorkflowRunStart(input_data={}), engine=engine)).run.id

    await _upload(pool, engine, starter, run_id, "jahresabschluss",
                  pdf(_ANNUAL, POISONED_DOCUMENT), "application/pdf")

    row = await system_pool.fetchrow(
        "SELECT media_type, pages, bytes, characters, findings::text AS findings "
        "FROM workflow_run_documents WHERE run_id = $1", run_id)
    assert row["media_type"] == "application/pdf" and row["pages"] == 2
    assert row["bytes"] > 0 and row["characters"] > 0
    assert "instruction_shape" in row["findings"] and "Ignoriere" not in row["findings"]

    [entry] = await system_pool.fetch(
        "SELECT payload::text AS payload FROM audit_log "
        "WHERE action = 'workflow.run_document_received' AND target_ref = $1",
        f"workflow_run:{run_id}")
    payload = json.loads(entry["payload"])
    assert payload["name"] == "jahresabschluss" and payload["pages"] == 2
    assert "Ignoriere" not in entry["payload"] and "Eigenkapital" not in entry["payload"]

    listing = await orchestration.run_documents(pool, starter, run_id)
    assert [(d.name, d.received) for d in listing.documents] == [
        ("jahresabschluss", True), ("kontoauszug", False)]
    assert listing.documents[0].findings[0].kind == "instruction_shape"
