"""
tests/test_run_documents.py

Documents of a run, without a database (CHY-174, ADR-049).

What is held here:

  - reading: a PDF becomes text, page by page; everything that cannot be
    taken as it is is refused with a reason that names the limit or the
    problem — and a PDF without a text layer is refused, not taken as empty
  - limits: the node narrows the installation's ceiling, never widens it, and
    activation refuses a node above it
  - binding: only the starter, only while the run waits, only an expected
    name, each once; the last document starts the run, and nothing runs before
  - placeholders: a document is stored with the run's mapping, so a name
    listed at the start is replaced in the document too
  - the audit entry counts findings and never quotes them, also when there
    are none
  - § 37a BDSG (CHY-177 way 1): a document reaches the successors of its node
    and no other step — the bank statement reaches the draft, not the score

Constraints, row-level security, the purge and a whole run against
PostgreSQL are in tests/integration/test_run_documents_db.py.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from api.v1 import workflows as transport
from contexts.orchestration import documents, service
from contexts.orchestration.errors import (
    DocumentAlreadyReceived,
    DocumentMediaTypeUnsupported,
    DocumentNotExpected,
    DocumentNotFromStarter,
    DocumentTooLarge,
    DocumentUnreadable,
    RunContentPurged,
    RunNotAwaitingDocuments,
)
from contexts.orchestration.execution import ExecutorRegistry, NodeContext, NodeResult
from contexts.orchestration.schema import NodeCreate, RunCancelRequest, WorkflowRunStart
from core import ingress
from tests.conftest import stub_user
from tests.fixtures.documents import CLEAN_DOCUMENT, POISONED_DOCUMENT
from tests.fixtures.pdfs import encrypted, pdf
from tests.test_workflow_advance import (
    _WF,
    FakeDb,
    _conn_row,
    _engine,
    _node,
    _NullPool,
    _run,
)

LIMITS = documents.DocumentLimits(max_bytes=10 * 1024 * 1024, max_pages=200)
_ERIKA = "Erika Mustermann"
_STATEMENT = f"Kontoauszug März 2025\nGutschrift {_ERIKA} (Miete) 1.200,00 EUR"


def _read(data: bytes, content_type: str = "application/pdf", *,
          max_bytes: int | None = None, max_pages: int | None = None):
    return documents.read(data, content_type, installation=LIMITS,
                          node_max_bytes=max_bytes, node_max_pages=max_pages)


# ── reading ───────────────────────────────────────────────────────────────────

class TestReading:
    def test_a_pdf_becomes_its_text_page_by_page(self):
        read = _read(pdf(_STATEMENT, "Seite 2"))
        assert read.pages == 2
        assert read.media_type == "application/pdf"
        assert "Gutschrift Erika Mustermann (Miete) 1.200,00 EUR" in read.text
        assert read.text.index("März") < read.text.index("Seite 2")

    def test_a_text_file_is_taken_as_utf8_without_pages(self):
        read = _read(_STATEMENT.encode(), "text/plain; charset=utf-8")
        assert read.text == _STATEMENT
        assert read.pages is None

    def test_a_pdf_without_a_text_layer_is_refused_not_taken_as_empty(self):
        """The case the pilot never had to face: a scanned page is an image.

        Taken as it comes, it would be an empty string, and an empty document
        reaching a model reads like a document that said nothing.
        """
        with pytest.raises(DocumentUnreadable, match="no text layer"):
            _read(pdf("", ""))

    @pytest.mark.parametrize("data, content_type, match", [
        (b"%PDF-1.4 truncated", "application/pdf", "not a PDF"),
        (_STATEMENT.encode("latin-1"), "text/plain", "not UTF-8"),
        (b"Konto\x00auszug", "text/plain", "NUL"),
        (b"   \n ", "text/plain", "empty"),
        (b"", "application/pdf", "empty"),
    ])
    def test_what_cannot_be_read_is_refused_with_its_reason(self, data, content_type, match):
        with pytest.raises(DocumentUnreadable, match=match):
            _read(data, content_type)

    def test_an_encrypted_pdf_is_refused(self):
        with pytest.raises(DocumentUnreadable, match="encrypted"):
            _read(encrypted(pdf(_STATEMENT)))

    @pytest.mark.parametrize("content_type", ["image/png", "application/msword", None, ""])
    def test_only_pdf_and_text_are_taken(self, content_type):
        with pytest.raises(DocumentMediaTypeUnsupported):
            _read(pdf(_STATEMENT), content_type)

    def test_the_declared_type_is_not_second_guessed(self):
        """A text declared as PDF is refused as unparsable, not quietly read as text."""
        with pytest.raises(DocumentUnreadable, match="not a PDF"):
            _read(_STATEMENT.encode(), "application/pdf")


class TestLimits:
    def test_the_node_narrows_the_ceiling(self):
        data = pdf(_STATEMENT)
        with pytest.raises(DocumentTooLarge) as refused:
            _read(data, max_bytes=len(data) - 1)
        assert f"has {len(data)} bytes" in str(refused.value)
        assert f"the node allows {len(data) - 1}" in str(refused.value)

    def test_the_node_never_widens_it(self):
        ceiling = documents.DocumentLimits(max_bytes=100, max_pages=1)
        assert ceiling.for_node(10_000, 50) == ceiling
        assert ceiling.for_node(None, None) == ceiling
        assert ceiling.for_node(40, None) == documents.DocumentLimits(40, 1)

    def test_pages_are_counted_before_any_is_read(self):
        with pytest.raises(DocumentUnreadable, match="3 pages, at most 2"):
            _read(pdf("a", "b", "c"), max_pages=2)

    def test_a_document_at_the_limit_is_taken(self):
        """Counter-probe: the limit is a maximum, not a bound to stay under."""
        data = pdf(_STATEMENT)
        assert _read(data, max_bytes=len(data)).bytes == len(data)


class TestActivationRule:
    """`document_limit_above_installation` — the rule that keeps a node from
    promising more than the installation takes. Against PostgreSQL, through
    validate_workflow, in the integration test."""

    @staticmethod
    def _row(max_bytes=None, max_pages=None):
        return {"document_max_bytes": max_bytes, "document_max_pages": max_pages}

    def test_a_node_above_the_ceiling_is_refused_per_limit(self):
        found = service._above_ceiling(self._row(LIMITS.max_bytes + 1, 201), "statement", LIMITS)
        assert [v.rule for v in found] == ["document_limit_above_installation"] * 2
        assert "DOCUMENT_MAX_BYTES" in found[0].message
        assert "DOCUMENT_MAX_PAGES" in found[1].message
        assert {v.node_ref for v in found} == {"statement"}

    @pytest.mark.parametrize("row", [
        {"document_max_bytes": None, "document_max_pages": None},
        {"document_max_bytes": LIMITS.max_bytes, "document_max_pages": 200},
        {"document_max_bytes": 1, "document_max_pages": 1},
    ])
    def test_at_or_below_it_nothing_is_said(self, row):
        assert service._above_ceiling(row, "statement", LIMITS) == []


class TestTheNodeDefinition:
    def test_a_document_name_is_a_path_segment(self):
        with pytest.raises(ValidationError):
            NodeCreate(node_ref="statement", node_type="document", document_name="Konto-Auszug")

    @pytest.mark.parametrize("key", ["document_name", "document_max_bytes", "document_max_pages"])
    def test_the_fields_are_refused_in_config(self, key):
        with pytest.raises(ValidationError, match="not read"):
            NodeCreate(node_ref="statement", node_type="document", config={key: 1})


class TestFindings:
    def test_they_are_counted_and_never_quoted(self):
        found = documents.findings(POISONED_DOCUMENT)
        assert found and all(set(f) == {"kind", "rule", "count"} for f in found)
        assert "Ignoriere" not in json.dumps(found)

    def test_a_clean_document_has_none(self):
        assert documents.findings(CLEAN_DOCUMENT) == []


# ── a run that waits for its documents ────────────────────────────────────────

def _document_node(ref: str, name: str, **limits) -> dict:
    return {**_node(ref, "document"), "document_name": name,
            "document_max_bytes": limits.get("max_bytes"),
            "document_max_pages": limits.get("max_pages")}


class _Seen:
    """An llm_call that keeps what it was handed, per node."""

    def __init__(self) -> None:
        self.inputs: dict[str, dict] = {}

    async def __call__(self, ctx: NodeContext) -> NodeResult:
        self.inputs[ctx.node_ref] = dict(ctx.input_data)
        return NodeResult(output={"content": f"answer of {ctx.node_ref}"})


def _banking_shape():
    """The shape of CHY-177 way 1, reduced to what decides it.

    The annual statement goes to the score; the bank statement has one edge,
    to the draft. The draft waits for both (wait_all), then the gate.
    """
    annual = _document_node("annual", "jahresabschluss")
    statement = _document_node("statement", "kontoauszug")
    score = _node("score", "llm_call")
    draft = _node("draft", "llm_call", join_policy="wait_all")
    review = _node("review", "human_review")
    nodes = [annual, statement, score, draft, review]
    edges = [_conn_row(annual, score), _conn_row(score, draft),
             _conn_row(statement, draft), _conn_row(draft, review)]
    return nodes, edges


class _World(SimpleNamespace):
    pass


async def _started(monkeypatch, nodes, edges, input_data=None) -> _World:
    """A run started through the service, with the store in dictionaries."""
    db = FakeDb(nodes=nodes, connections=edges)
    db.install(monkeypatch)
    audits: list[dict] = []
    starter = SimpleNamespace(id=uuid4(), role="contributor", is_service_account=False)
    seen = _Seen()

    async def insert_run(_conn, new):
        run_id = _run(db)
        db.runs[run_id].update(
            workflow_id=new.workflow_id, input_data=new.input_data, actor_id=new.actor_id,
            status="awaiting_documents" if new.awaits_documents else "running",
            content_purged_at=None)
        return db.runs[run_id]

    async def audit(_conn, **kw):
        audits.append(kw)

    monkeypatch.setattr(service.store, "insert_run", insert_run)
    monkeypatch.setattr(service, "write_audit", audit)
    monkeypatch.setattr(service, "resolve_actor", AsyncMock(return_value=starter))
    monkeypatch.setattr(service, "require", lambda *_a: None)

    engine = _engine(ExecutorRegistry({"llm_call": seen}))
    advance = await service.start_workflow_run(
        _NullPool(), stub_user(), _WF, WorkflowRunStart(input_data=input_data or {}),
        engine=engine)
    return _World(db=db, run_id=advance.run.id, status=advance.run.status, audits=audits,
                  engine=engine, starter=starter, seen=seen, monkeypatch=monkeypatch)


async def _upload(world: _World, name: str, data: bytes,
                  content_type: str = "application/pdf"):
    return await service.receive_document(
        _NullPool(), stub_user(),
        service.DocumentUpload(world.run_id, name, data, content_type),
        engine=world.engine, limits=LIMITS)


def _audited(world: _World, action: str) -> list[dict]:
    return [a["payload"] for a in world.audits if a["action"] == action]


class TestTheRunWaits:
    async def test_nothing_runs_until_the_last_document_has_arrived(self, monkeypatch):
        world = await _started(monkeypatch, *_banking_shape())
        assert world.status == "awaiting_documents"
        assert world.db.executions == []

        out = await _upload(world, "jahresabschluss", pdf("Bilanz 2025"))
        assert out.status == "awaiting_documents"
        assert world.db.executions == []
        assert {d.name: d.received for d in out.documents} == {
            "jahresabschluss": True, "kontoauszug": False}

        out = await _upload(world, "kontoauszug", _STATEMENT.encode(), "text/plain")
        assert out.status == "awaiting_oversight"
        assert set(world.seen.inputs) == {"score", "draft"}

    async def test_the_start_names_what_the_run_waits_for(self, monkeypatch):
        world = await _started(monkeypatch, *_banking_shape())
        [started] = _audited(world, "workflow.run_started")
        assert started["documents_expected"] == ["jahresabschluss", "kontoauszug"]

    async def test_a_workflow_without_document_nodes_starts_as_before(self, monkeypatch):
        """Counter-probe: the waiting is the document nodes', not every run's."""
        a, review = _node("a", "llm_call"), _node("review", "human_review")
        world = await _started(monkeypatch, [a, review], [_conn_row(a, review)])
        assert world.status == "awaiting_oversight"
        assert "documents_expected" not in _audited(world, "workflow.run_started")[0]

    async def test_an_advance_does_not_start_a_waiting_run(self, monkeypatch):
        """Only the last document starts it — a resumption or a stray advance must not
        run the steps that need no document while the others wait."""
        world = await _started(monkeypatch, *_banking_shape())
        result = await service.advance_run(_NullPool(), world.run_id,
                                           registry=world.engine.registry, keys=world.engine.placeholders.keys)
        assert result.run.status == "awaiting_documents"
        assert world.db.executions == []

    async def test_a_waiting_run_can_be_stopped(self, monkeypatch):
        world = await _started(monkeypatch, *_banking_shape())
        out = await service.cancel_run(_NullPool(), stub_user(), world.run_id,
                                       RunCancelRequest(), engine=world.engine)
        assert out.status == "cancelled"


class TestWhoMayUpload:
    async def test_only_the_starter(self, monkeypatch):
        world = await _started(monkeypatch, *_banking_shape())
        monkeypatch.setattr(service, "resolve_actor",
                            AsyncMock(return_value=SimpleNamespace(id=uuid4(), role="admin", is_service_account=False)))
        with pytest.raises(DocumentNotFromStarter):
            await _upload(world, "kontoauszug", pdf(_STATEMENT))
        assert world.db.documents == []

    async def test_only_a_name_the_definition_expects(self, monkeypatch):
        world = await _started(monkeypatch, *_banking_shape())
        with pytest.raises(DocumentNotExpected, match="jahresabschluss, kontoauszug"):
            await _upload(world, "lohnabrechnung", pdf(_STATEMENT))

    async def test_each_name_once(self, monkeypatch):
        world = await _started(monkeypatch, *_banking_shape())
        await _upload(world, "kontoauszug", pdf(_STATEMENT))
        with pytest.raises(DocumentAlreadyReceived):
            await _upload(world, "kontoauszug", pdf("ein anderer Auszug"))
        assert len(world.db.documents) == 1
        assert len(_audited(world, "workflow.run_document_received")) == 1

    async def test_only_while_the_run_waits(self, monkeypatch):
        world = await _started(monkeypatch, *_banking_shape())
        await _upload(world, "jahresabschluss", pdf("Bilanz"))
        await _upload(world, "kontoauszug", pdf(_STATEMENT))
        with pytest.raises(RunNotAwaitingDocuments, match="awaiting_oversight"):
            await _upload(world, "kontoauszug", pdf(_STATEMENT))

    async def test_not_once_its_content_was_cleared(self, monkeypatch):
        world = await _started(monkeypatch, *_banking_shape())
        world.db.runs[world.run_id]["content_purged_at"] = "2026-12-01"
        with pytest.raises(RunContentPurged):
            await _upload(world, "kontoauszug", pdf(_STATEMENT))

    async def test_a_refused_file_leaves_nothing_behind(self, monkeypatch):
        world = await _started(monkeypatch, *_banking_shape())
        with pytest.raises(DocumentUnreadable):
            await _upload(world, "kontoauszug", pdf(""))
        assert world.db.documents == []
        assert _audited(world, "workflow.run_document_received") == []
        assert world.db.runs[world.run_id]["status"] == "awaiting_documents"

    async def test_the_node_limit_applies(self, monkeypatch):
        nodes, edges = _banking_shape()
        nodes[1]["document_max_pages"] = 1
        world = await _started(monkeypatch, nodes, edges)
        with pytest.raises(DocumentUnreadable, match="2 pages, at most 1"):
            await _upload(world, "kontoauszug", pdf(_STATEMENT, "Seite 2"))
        await _upload(world, "jahresabschluss", pdf(_STATEMENT, "Seite 2"))


class TestWhatIsKept:
    INPUT = {"identifiers_to_redact": {"PERSON": [_ERIKA]}}

    async def test_the_text_is_kept_with_the_runs_placeholders(self, monkeypatch):
        """A name listed at the start is replaced in the document too: the run's
        mapping exists from the start, and the document is replaced by it."""
        world = await _started(monkeypatch, *_banking_shape(), input_data=self.INPUT)
        await _upload(world, "kontoauszug", pdf(_STATEMENT))
        [kept] = world.db.documents
        assert _ERIKA not in kept["text"]
        assert "{{PERSON_1}}" in kept["text"]
        assert kept["characters"] == len(_read(pdf(_STATEMENT)).text)

    async def test_the_audit_counts_and_never_quotes(self, monkeypatch):
        world = await _started(monkeypatch, *_banking_shape(), input_data=self.INPUT)
        await _upload(world, "jahresabschluss", POISONED_DOCUMENT.encode(), "text/plain")
        await _upload(world, "kontoauszug", pdf(_STATEMENT))
        poisoned, clean = _audited(world, "workflow.run_document_received")
        assert poisoned["findings"] and poisoned["findings"][0]["kind"] == "instruction_shape"
        assert clean["findings"] == []
        assert clean["redactions"] == {"PERSON": 1}
        assert clean["pages"] == 1 and clean["media_type"] == "application/pdf"
        written = json.dumps([poisoned, clean], ensure_ascii=False)
        for content in ("Ignoriere", _ERIKA, "Gutschrift"):
            assert content not in written


class TestSection37a:
    """CHY-177 way 1: the bank statement reaches the draft and nothing else.

    The documents are not in the run input, so `run` carries none; a step sees
    a document only through an edge from its document node. Before CHY-174 a
    document would have had to travel in the run input — and reached the score.
    """

    async def _ran(self, monkeypatch) -> _World:
        world = await _started(monkeypatch, *_banking_shape(),
                               input_data={"identifiers_to_redact": {"PERSON": [_ERIKA]},
                                           "case": "K-17"})
        await _upload(world, "jahresabschluss", pdf("Bilanz 2025, Eigenkapital 1,1 Mio."))
        await _upload(world, "kontoauszug", pdf(_STATEMENT))
        return world

    async def test_the_score_does_not_see_the_bank_statement(self, monkeypatch):
        world = await self._ran(monkeypatch)
        score = json.dumps(world.seen.inputs["score"], ensure_ascii=False)
        assert "Gutschrift" not in score and "kontoauszug" not in score
        assert "Bilanz 2025" in score
        assert world.seen.inputs["score"]["run"] == {"case": "K-17"}

    async def test_the_draft_sees_it_fenced_and_with_placeholders(self, monkeypatch):
        world = await self._ran(monkeypatch)
        handed = world.seen.inputs["draft"]["statement"]
        assert handed["text"].startswith(ingress.FENCE_OPEN)
        assert "Gutschrift {{PERSON_1}} (Miete)" in handed["text"]
        assert handed["name"] == "kontoauszug" and handed["findings"] == []

    async def test_counter_probe_an_edge_is_what_lets_it_through(self, monkeypatch):
        """With an edge from the bank statement to the score, the score sees it:
        the edges decide, not a filter somewhere else."""
        nodes, edges = _banking_shape()
        statement, score = nodes[1], nodes[2]
        score["join_policy"] = "wait_all"
        world = await _started(monkeypatch, nodes, [*edges, _conn_row(statement, score)])
        await _upload(world, "jahresabschluss", pdf("Bilanz"))
        await _upload(world, "kontoauszug", pdf(_STATEMENT))
        assert "Gutschrift" in json.dumps(world.seen.inputs["score"], ensure_ascii=False)


# ── the transport reads no further than the ceiling ───────────────────────────

class _Request:
    def __init__(self, chunks: list[bytes], content_length: str | None = None) -> None:
        self.headers = {"content-length": content_length} if content_length else {}
        self._chunks = chunks
        self.read = 0

    async def stream(self):
        for chunk in self._chunks:
            self.read += 1
            yield chunk


class TestTheBody:
    async def test_a_declared_length_above_the_ceiling_is_refused_unread(self):
        request = _Request([b"x" * 10], content_length="11")
        with pytest.raises(DocumentTooLarge):
            await transport._body_up_to(request, 10)
        assert request.read == 0

    async def test_a_body_that_keeps_coming_is_cut_off_at_the_ceiling(self):
        request = _Request([b"x" * 6, b"x" * 6, b"x" * 6])
        with pytest.raises(DocumentTooLarge, match="larger than 10 bytes"):
            await transport._body_up_to(request, 10)
        assert request.read == 2

    async def test_a_body_at_the_ceiling_is_read_whole(self):
        assert await transport._body_up_to(_Request([b"x" * 4, b"y" * 6]), 10) == b"xxxxyyyyyy"


def test_the_ceiling_comes_from_the_configuration(monkeypatch):
    monkeypatch.setattr(transport.settings, "document_max_bytes", 1234)
    monkeypatch.setattr(transport.settings, "document_max_pages", 7)
    assert transport.document_limits() == documents.DocumentLimits(1234, 7)


class TestTheBodyWaitsForTheSignIn:
    """Without a token there is a 401 and not a byte read — ten megabytes from
    anybody would otherwise be read before anybody asked who they were.

    The router's surface gate checks the token before any dependency of the
    route; that is what holds it. The order of the route's own parameters is a
    second line: swapping it leaves this test green, and that is correct."""

    @staticmethod
    def _counting(monkeypatch) -> list[int]:
        read: list[int] = []

        async def body(_request, limit):
            read.append(limit)
            return b"Kontoauszug"

        monkeypatch.setattr(transport, "_body_up_to", body)
        return read

    def test_without_a_token_nothing_is_read(self, monkeypatch):
        from fastapi.testclient import TestClient

        from api.main import app

        read = self._counting(monkeypatch)
        response = TestClient(app, raise_server_exceptions=False).put(
            f"/v1/workflows/runs/{uuid4()}/documents/kontoauszug",
            content=b"x" * 1000, headers={"Content-Type": "text/plain"})
        assert response.status_code == 401
        assert read == []

    def test_counter_probe_signed_in_it_is(self, monkeypatch):
        from tests.conftest import client_for, make_pool

        read = self._counting(monkeypatch)
        received = AsyncMock(return_value={"run_id": str(uuid4()), "status": "running",
                                           "documents": []})
        monkeypatch.setattr(service, "receive_document", received)
        with client_for(make_pool()) as client:
            response = client.put(f"/v1/workflows/runs/{uuid4()}/documents/kontoauszug",
                                  content=b"x", headers={"Content-Type": "text/plain"})
        assert response.status_code == 200, response.text
        assert read == [transport.settings.document_max_bytes]
        upload = received.await_args.args[2]
        assert (upload.name, upload.data, upload.content_type) == (
            "kontoauszug", b"Kontoauszug", "text/plain")
