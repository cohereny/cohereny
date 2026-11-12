"""
tests/integration/test_banking_example_db.py

The credit workflow of deploy/examples/banking, run on a real PostgreSQL and a
real Redis (CHY-178).

tests/test_banking_example.py holds the definition to its promises as data.
What only a run shows:

- the three files load through the service as the seed writes them, and the
  release accepts the definition in a confidential scope with risk class high
- every model step gets its own prompt: the blocks of blocks.yaml, tagged per
  step and approved like any block. The risk rating sees its instruction and
  not the vote's legal frame; the draft of the vote sees both of its own
- each step is served by the model its routing rule names (routing.yaml)
- the bank statement reaches the draft of the vote and no request before it
  (§ 37a BDSG, CHY-177 way 1); the name of the tenant in it reaches none
- the figures a model read are computed on by the registered steps
- the run stops at the vote of front and back office, at four eyes; differing
  votes go to the credit committee, which decides
- a prompt changed and approved reaches the next run

The gateway is a mock that answers per step, recognised by a phrase of its
instruction. Everything around it is the product: composition, routing,
redaction, the runner, the gate.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import partial
from uuid import UUID, uuid4

import httpx
import pytest

from api.v1 import workflows
from api.v1._activation import HOOKS
from api.v1._redaction import mapping_keys, policy_or_shipped
from contexts.orchestration import service as orchestration
from contexts.orchestration.execution import ExecutorRegistry
from contexts.orchestration.schema import (
    ConnectionCreate,
    NodeCreate,
    WorkflowCreate,
    WorkflowRunStart,
    WorkflowStatusPut,
)
from contexts.repository import service as repository
from contexts.repository.schema import BlockCreate, BlockPatch
from core import completion, ingress
from scripts import seed_banking_workflow as seed
from kernel import retention
from kernel.schema import RetentionPolicySet
from tests.integration._callers import caller
from tests.integration.test_review_areas_db import _Order, _vote, order  # noqa: F401 -- fixture

pytestmark = pytest.mark.integration

EXAMPLE = seed.load()
WORKFLOW = EXAMPLE.workflow
NODES = {n["node_ref"]: n for n in WORKFLOW["nodes"]}
LIMITS = orchestration.DocumentLimits(max_bytes=10 * 1024 * 1024, max_pages=200)

FIGURES = {
    "bilanzsumme_current": 5_000_000, "bilanzsumme_prior": 4_600_000,
    "eigenkapital_current": 1_250_000, "umlaufvermoegen_current": 1_500_000,
    "kurzfristige_verbindlichkeiten_current": 2_200_000,
    "kurzfristige_verbindlichkeiten_prior": 1_400_000, "umsatz_current": 6_000_000,
    "ebit_current": 150_000, "abschreibungen_current": 200_000,
    "jahresueberschuss_current": 90_000,
}
ANNUAL = (
    "Jahresabschluss zum 31.12.2024\nMuster Metallbau GmbH\n"
    "Bilanzsumme 5.000.000 EUR (Vorjahr 4.600.000 EUR)\nEigenkapital 1.250.000 EUR\n"
    "Umlaufvermögen 1.500.000 EUR\n"
    "Kurzfristige Verbindlichkeiten 2.200.000 EUR (Vorjahr 1.400.000 EUR)\n"
    "Umsatzerlöse 6.000.000 EUR\nEBIT 150.000 EUR\nAbschreibungen 200.000 EUR\n"
    "Jahresüberschuss 90.000 EUR\n"
)
_ERIKA = "Erika Mustermann"
STATEMENT = (
    f"Kontoauszug Dezember 2024\nGutschrift {_ERIKA} (Miete) 1.200,00 EUR\n"  # codespell:ignore
    "Lastschrift Finanzamt 8.400,00 EUR\n"
)

#: How the mock knows which step is asking: a phrase only that step's
#: instruction has (blocks.yaml). A system message with none or two of them
#: is answered with an error, and the run fails visibly.
RECOGNISED_BY = {
    "einordnung": "Du ordnest Bankdokumente ein.",
    "kennzahlen_lesen": "Du liest Bilanzkennzahlen aus einem Jahresabschluss.",
    "bewertung": "Du bist Kreditanalyst.",
    "risiko": "Du bist Senior-Kreditanalyst",
    "votum": "Du entwirfst das Votum",
}
ANSWERS = {
    "einordnung": json.dumps({"dokumenttyp": "Jahresabschluss",
                              "unternehmen": "Muster Metallbau GmbH",
                              "jahr": "2024"}),
    "kennzahlen_lesen": json.dumps(FIGURES),
    "bewertung": "Die Liquidität ist angespannt, die kurzfristigen Verbindlichkeiten "  # codespell:ignore
                 "sind deutlich gestiegen.",
    "risiko": json.dumps({"risikoklasse": "GELB",
                          "grund": "Liquidität angespannt, Eigenkapital ausreichend."},
                         ensure_ascii=False),
    "votum": "**Kernergebnis:** GELB. Dies ist ein Entwurf; entschieden wird durch die "  # codespell:ignore
             "Voten von Markt und Marktfolge.",
}


# ── the gateway ───────────────────────────────────────────────────────────────

@dataclass
class _Call:
    step: str | None
    model: str
    system: str
    user: str

    @property
    def text(self) -> str:
        return f"{self.system}\n{self.user}"


@pytest.fixture
def gateway(monkeypatch) -> list[_Call]:
    """Answers each step as blocks.yaml asks it to, and keeps every request."""
    real_client = httpx.AsyncClient
    calls: list[_Call] = []

    def answer(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        messages = {m["role"]: m["content"] for m in body["messages"]}
        system = messages.get("system", "")
        steps = [step for step, phrase in RECOGNISED_BY.items() if phrase in system]
        call = _Call(steps[0] if len(steps) == 1 else None, body["model"], system,
                     messages.get("user", ""))
        calls.append(call)
        if call.step is None:
            return httpx.Response(400, json={"error": {"message": f"steps {steps}"}})
        return httpx.Response(200, json={
            "id": f"gw-{uuid4().hex}", "model": body["model"],
            "choices": [{"message": {"content": ANSWERS[call.step]}}], "usage": {}})

    monkeypatch.setattr(completion.httpx, "AsyncClient",
                        lambda **kw: real_client(transport=httpx.MockTransport(answer), **kw))
    return calls


def _by_step(calls: list[_Call]) -> dict[str, _Call]:
    assert all(c.step for c in calls), [c.system[:80] for c in calls if not c.step]
    by_step = {c.step: c for c in calls}
    assert len(by_step) == len(calls), "a step asked twice"
    return by_step


# ── the example, set up as the seed sets it up ────────────────────────────────

@dataclass
class _Credit:
    workflow: UUID
    blocks: dict[str, UUID]
    order: _Order


async def _approved(pool, cache, body: BlockCreate) -> UUID:
    author = caller("contributor")
    created = await repository.create_block(pool, cache, author, body, hooks=HOOKS)
    await repository.transition(pool, cache, author, created.block_id, "submit",
                                expected_version=1)
    approval = await repository.approve(pool, cache, caller("reviewer"), created.block_id,
                                        expected_version=1, hooks=HOOKS)
    assert approval.block.status == "active"
    return created.block_id


async def _released(pool, scope: UUID) -> UUID:
    author = caller("contributor")
    created = await orchestration.create_workflow(
        pool, author, WorkflowCreate(**seed.workflow_body(str(scope), WORKFLOW)))
    for node in WORKFLOW["nodes"]:
        await orchestration.add_node(pool, author, created.id, NodeCreate(**node))
    for edge in WORKFLOW["connections"]:
        await orchestration.add_connection(pool, author, created.id, ConnectionCreate(**edge))
    # High-risk: released only where its trace and content are kept six months
    # (AI Act Art. 26(6), CHY-266) — the step the README asks of the operator.
    for retention_class in ("run_trace", "run_content"):
        await retention.set_policy(
            pool, caller("admin"), retention_class,
            RetentionPolicySet(scope_id=scope, keep_days=retention.HIGH_RISK_MIN_KEEP_DAYS),
            high_risk_scopes=orchestration.high_risk_scopes)
    released = await orchestration.set_workflow_status(
        pool, caller("reviewer"), created.id, WorkflowStatusPut(status="active"),
        limits=LIMITS)
    assert released.status == "active"
    return created.id


@pytest.fixture
async def credit(pool, owner_pool, cache, order) -> _Credit:  # noqa: F811 -- fixture
    """The scope the seed writes — confidential, with the competence order of
    BTO 1.1 —, its prompts and routing rules approved, the workflow released."""
    scope = order.scope
    await owner_pool.execute("UPDATE scope_nodes SET privacy_level = $2 WHERE id = $1",
                             scope, seed.SCOPE["privacy_level"])
    blocks = {b["name"]: await _approved(pool, cache, BlockCreate(**seed.block_body(str(scope), b)))
              for b in EXAMPLE.blocks}
    for rule in EXAMPLE.rules:
        await _approved(pool, cache, BlockCreate(
            **seed.rule_body(str(scope), EXAMPLE.routing_reason, rule)))
    return _Credit(await _released(pool, scope), blocks, order)


# ── a run ─────────────────────────────────────────────────────────────────────

def _engine(pool, system_pool, cache) -> orchestration.Engine:
    """The executors of api/v1/workflows.get_executors, with a real Redis."""
    return orchestration.Engine(
        registry=ExecutorRegistry({"llm_call": workflows._call_model_for(pool),
                                   "context_compose": workflows._compose_for(pool, cache)}),
        pool=system_pool,
        placeholders=orchestration.Placeholders(
            policy_for=partial(policy_or_shipped, pool), keys=mapping_keys()),
    )


async def _run(pool, system_pool, cache, workflow: UUID) -> tuple[UUID, str]:
    """Start, then hand in both documents; the last one starts the run."""
    starter, engine = caller("contributor"), _engine(pool, system_pool, cache)
    started = await orchestration.start_workflow_run(
        pool, starter, workflow,
        WorkflowRunStart(input_data={"antrag": f"K-{uuid4().hex[:6]}",
                                     "identifiers_to_redact": {"PERSON": [_ERIKA]}}),
        engine=engine)
    assert started.run.status == "awaiting_documents", started.run
    for name, text in (("jahresabschluss", ANNUAL), ("kontoauszug", STATEMENT)):
        done = await orchestration.receive_document(
            pool, starter,
            orchestration.DocumentUpload(started.run.id, name, text.encode(),
                                         "text/plain; charset=utf-8"),
            engine=engine, limits=LIMITS)
    return started.run.id, done.status


def _json(value) -> dict:
    return json.loads(value) if isinstance(value, str) else dict(value or {})


async def _executions(system_pool, run_id: UUID) -> dict[str, dict]:
    rows = await system_pool.fetch(
        "SELECT n.node_ref, e.status, e.review_level, e.error_message, e.input_data, "
        "       e.output_data "
        "FROM node_executions e JOIN workflow_nodes n ON n.id = e.node_id "
        "WHERE e.run_id = $1", run_id)
    return {r["node_ref"]: {**dict(r), "input_data": _json(r["input_data"]),
                            "output_data": _json(r["output_data"])} for r in rows}


def _failed(executions: dict[str, dict]) -> dict[str, str]:
    return {ref: e["error_message"] for ref, e in executions.items() if e["status"] == "failed"}


async def test_every_step_gets_its_own_prompt_and_the_run_stops_at_four_eyes(
        pool, system_pool, cache, credit, gateway):
    run_id, status = await _run(pool, system_pool, cache, credit.workflow)
    assert status == "awaiting_oversight", _failed(await _executions(system_pool, run_id))

    calls = _by_step(gateway)
    assert sorted(calls) == sorted(RECOGNISED_BY)

    # Each step by the model its rule names.
    first_choice = {r["task_type"]: r["candidates"][0] for r in EXAMPLE.rules}
    assert {step: c.model for step, c in calls.items()} == {
        step: first_choice[NODES[step]["task_type"]] for step in calls}

    # Its own instruction, nothing of another's. The legal frame is tagged for
    # the vote only — the risk rating does not see it.
    risk, vote = calls["risiko"], calls["votum"]
    assert "§ 37a BDSG" in vote.system and "§ 37a BDSG" not in risk.system
    assert "Du bist Kreditanalyst." not in risk.system

    # The bank statement: in the draft of the vote, in no request before it.
    assert "Gutschrift" in vote.user and ingress.FENCE_OPEN in vote.user
    assert [s for s, c in calls.items() if "Gutschrift" in c.text] == ["votum"]
    assert all(_ERIKA not in c.text for c in calls.values())

    # A prompt from several inputs, in sections.
    for section in ("unternehmen", "kennzahlen", "auffaelligkeiten", "bewertung"):
        assert f"## {section}\n" in risk.user, section
    assert ANSWERS["bewertung"] in risk.user
    assert '"risikoklasse": "GELB"' in vote.user

    executions = await _executions(system_pool, run_id)
    # What the steps received, which is more than their prompt.
    assert "Gutschrift" not in json.dumps(executions["risiko"]["input_data"])
    assert "Gutschrift" in json.dumps(executions["votum"]["input_data"])
    # The figures the model read, computed on by the registered steps.
    ratios = executions["kennzahlen"]["output_data"]
    assert ratios["function"] == "banking.ratios@1"
    assert ratios["data"]["current_ratio"] == pytest.approx(1_500_000 / 2_200_000)
    assert executions["auffaelligkeiten"]["output_data"]["data"]["count"] == 2
    # The composition says what it contained.
    blocks = credit.blocks
    defined = str(blocks["kennzahlen_definitionen"])
    assert executions["prompt_risiko"]["output_data"]["block_versions"] == {
        str(blocks["risiko"]): 1, defined: 1}
    assert executions["prompt_votum"]["output_data"]["block_versions"] == {
        str(blocks["votum"]): 1, str(blocks["rechtsrahmen"]): 1, defined: 1}
    # The gate: front and back office, four eyes; the committee not yet.
    assert executions["votum_freigabe"]["review_level"] == "four_eyes"
    assert "kreditkommission" not in executions


async def test_differing_votes_go_to_the_committee_which_decides(
        pool, system_pool, cache, credit, gateway):
    run_id, _ = await _run(pool, system_pool, cache, credit.workflow)

    await _vote(pool, system_pool, credit.order.markt, run_id, "approved")
    escalated = await _vote(pool, system_pool, credit.order.marktfolge, run_id, "rejected")
    assert escalated.run.status == "awaiting_oversight"
    assert "kreditkommission" in await _executions(system_pool, run_id)

    decided = await _vote(pool, system_pool, credit.order.kommission, run_id, "approved")
    assert decided.run.status == "complete"


async def test_a_prompt_changed_and_approved_reaches_the_next_run(
        pool, system_pool, cache, credit, gateway):
    await _run(pool, system_pool, cache, credit.workflow)
    before = _by_step(gateway)
    gateway.clear()

    block = credit.blocks["risiko"]
    added = "Eine Branche nennst du nur, wenn das Dokument sie nennt."  # codespell:ignore
    original = next(b["content"] for b in EXAMPLE.blocks if b["name"] == "risiko").strip()
    author = caller("contributor")
    await repository.patch_block(
        pool, cache, author, block,
        BlockPatch(content=f"{original}\n{added}",
                   change_reason="Branche nur aus dem Dokument"),  # codespell:ignore
        hooks=HOOKS)
    await repository.transition(pool, cache, author, block, "submit", expected_version=2)
    await repository.approve(pool, cache, caller("reviewer"), block, expected_version=2,
                             hooks=HOOKS)

    run_id, status = await _run(pool, system_pool, cache, credit.workflow)
    assert status == "awaiting_oversight"
    after = _by_step(gateway)

    assert added not in before["risiko"].system and added in after["risiko"].system
    assert after["votum"].system == before["votum"].system, "only the changed step moves"
    assert (await _executions(system_pool, run_id))["prompt_risiko"]["output_data"][
        "block_versions"] == {str(block): 2, str(credit.blocks["kennzahlen_definitionen"]): 1}
