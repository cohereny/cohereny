"""
tests/test_run_placeholders.py

A workflow run stored with placeholders, against the store that remembers (CHY-234).

What is held here, without a database:

  - the start replaces every text of the input, keeps the caller's list out of
    it, and seals the mapping -- with a name for every listed value, found or not
  - a step is handed the mapping and its additions are sealed again; a step
    that recorded in between moves this step's names, so one name never means
    two people
  - a run with a mapping does not advance without keys, and a mapping that does
    not open ends the run with a message instead of leaving it `running`
  - the API does not start without a key (E5)

The row lock and the whole chain against PostgreSQL, every content column
searched for the name, are in tests/integration/test_run_placeholders_db.py.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from api.config import settings
from contexts.orchestration import service
from contexts.orchestration.execution import ExecutorRegistry, NodeContext, NodeResult
from contexts.orchestration.schema import WorkflowRunStart
from kernel import mapping_cipher
from kernel.redaction import RedactionPolicy
from tests.conftest import stub_user
from tests.test_workflow_advance import (
    KEYS,
    FakeDb,
    _at_the_gate,
    _engine,
    _node,
    _NullPool,
    _run,
)

_ERIKA = "Erika Mustermann"


def _opened(db: FakeDb, run_id: UUID, keys=KEYS) -> dict[str, str]:
    row = db.mappings[run_id]
    return mapping_cipher.unseal(
        mapping_cipher.Sealed(row["key_id"], row["nonce"], row["ciphertext"]),
        run_id=run_id, keys=keys)


def _seal_into(db: FakeDb, run_id: UUID, mapping: dict[str, str], keys=KEYS) -> None:
    sealed = mapping_cipher.seal(mapping, run_id=run_id, keys=keys)
    db.mappings[run_id] = {"key_id": sealed.key_id, "nonce": sealed.nonce,
                           "ciphertext": sealed.ciphertext}


def _texts(value) -> str:
    return json.dumps(value, ensure_ascii=False)


# ── the start ─────────────────────────────────────────────────────────────────

class _Started(SimpleNamespace):
    pass


async def _start(monkeypatch, input_data: dict, *,
                 policy: RedactionPolicy | None = None) -> _Started:
    """Start a one-node run through the service, with the store in dictionaries."""
    a = _node("a")
    db = FakeDb(nodes=[a])
    db.install(monkeypatch)
    audits: list[dict] = []
    asked: list[UUID | None] = []

    async def insert_run(_conn, new):
        run_id = _run(db)
        db.runs[run_id].update(workflow_id=new.workflow_id, input_data=new.input_data,
                               actor_id=new.actor_id)
        return db.runs[run_id]

    async def audit(_conn, **kw):
        audits.append(kw)

    async def policy_for(scope_id):
        asked.append(scope_id)
        return policy or RedactionPolicy()

    monkeypatch.setattr(service.store, "insert_run", insert_run)
    monkeypatch.setattr(service, "write_audit", audit)
    monkeypatch.setattr(service, "resolve_actor",
                        AsyncMock(return_value=SimpleNamespace(id=uuid4(), role="contributor", is_service_account=False)))
    monkeypatch.setattr(service, "require", lambda *_a: None)

    async def ok(_ctx):
        return NodeResult(output={})

    engine = service.Engine(ExecutorRegistry({"tool_call": ok}), _NullPool(),
                            service.Placeholders(policy_for=policy_for, keys=KEYS))
    advance = await service.start_workflow_run(
        _NullPool(), stub_user(), a["workflow_id"], WorkflowRunStart(input_data=input_data),
        engine=engine)
    run_id = advance.run.id
    return _Started(db=db, run_id=run_id, stored=db.runs[run_id]["input_data"],
                    audits=audits, asked=asked, scope_id=db.scope_id)


class TestTheStart:
    INPUT = {
        "letter": f"Liebe {_ERIKA}, Kopie an erika@example.de",
        "meta": {"cc": ["erika@example.de"], "pages": 3, "urgent": True},
        "identifiers_to_redact": {"PERSON": [_ERIKA, "Max Mustermann"]},
    }

    async def test_every_text_of_the_input_is_stored_with_placeholders(self, monkeypatch):
        """Nested fields too (E2): a field nobody thought of would keep its values."""
        started = await _start(monkeypatch, self.INPUT)
        assert started.stored == {
            "letter": "Liebe {{PERSON_1}}, Kopie an {{EMAIL_1}}",
            "meta": {"cc": ["{{EMAIL_1}}"], "pages": 3, "urgent": True},
        }

    async def test_the_list_goes_into_the_mapping_not_into_the_input(self, monkeypatch):
        """Max is not in the input and gets a name all the same, so a later step
        that meets him searches for him -- the list is not stored anywhere else."""
        started = await _start(monkeypatch, self.INPUT)
        assert "identifiers_to_redact" not in started.stored
        assert _opened(started.db, started.run_id) == {
            "PERSON_1": _ERIKA, "EMAIL_1": "erika@example.de", "PERSON_2": "Max Mustermann"}

    async def test_the_audit_counts_and_names_no_value(self, monkeypatch):
        started = await _start(monkeypatch, self.INPUT)
        [entry] = [a for a in started.audits if a["action"] == "workflow.run_started"]
        assert entry["payload"]["redactions"] == {"PERSON": 1, "EMAIL": 2}
        assert "Mustermann" not in _texts(entry["payload"])
        assert "erika@" not in _texts(entry["payload"])

    async def test_the_scopes_rules_apply_from_the_start(self, monkeypatch):
        started = await _start(
            monkeypatch, {"note": "Rückruf Dr. Schmidt"},
            policy=RedactionPolicy(identifiers={"STAFF": ("Dr. Schmidt",)}))
        assert started.stored == {"note": "Rückruf {{STAFF_1}}"}
        assert started.asked == [started.scope_id], "the rules of the workflow's scope"

    async def test_a_run_without_identifiers_still_gets_a_mapping(self, monkeypatch):
        """Every run has its row, so a step that finds something has one to lock."""
        started = await _start(monkeypatch, {"message": "Where is my order?"})
        assert started.stored == {"message": "Where is my order?"}
        assert _opened(started.db, started.run_id) == {}


# ── the steps ─────────────────────────────────────────────────────────────────

def _one_node_run(monkeypatch) -> tuple[FakeDb, UUID]:
    a = _node("a")
    db = FakeDb(nodes=[a])
    db.install(monkeypatch)
    run_id = _run(db)
    _seal_into(db, run_id, {"PERSON_1": _ERIKA})
    return db, run_id


class TestTheSteps:
    async def test_a_step_gets_the_mapping_and_what_it_adds_is_sealed(self, monkeypatch):
        seen: list = []

        async def answer(ctx: NodeContext) -> NodeResult:
            seen.append(dict(ctx.run_mapping))
            return NodeResult(output={"content": "{{PERSON_1}}, Kopie an {{EMAIL_1}}"},
                              run_mapping={**ctx.run_mapping, "EMAIL_1": "erika@example.de"})

        db, run_id = _one_node_run(monkeypatch)
        await service.advance_run(_NullPool(), run_id,
                                  registry=ExecutorRegistry({"tool_call": answer}), keys=KEYS)

        assert seen == [{"PERSON_1": _ERIKA}]
        assert _opened(db, run_id) == {"PERSON_1": _ERIKA, "EMAIL_1": "erika@example.de"}
        assert db.executions[0]["output_data"]["content"] == "{{PERSON_1}}, Kopie an {{EMAIL_1}}"

    async def test_a_step_that_recorded_in_between_moves_this_steps_names(self, monkeypatch):
        """Another step of the run called Max PERSON_2 while this one called
        Anna PERSON_2. Without the merge PERSON_2 would mean both, and
        resolving would show a reviewer the wrong person."""
        db_box: dict = {}

        async def answer(ctx: NodeContext) -> NodeResult:
            _seal_into(db_box["db"], ctx.run_id, {**ctx.run_mapping, "PERSON_2": "Max"})
            return NodeResult(output={"content": "Termin mit {{PERSON_2}}"},
                              run_mapping={**ctx.run_mapping, "PERSON_2": "Anna"})

        db, run_id = _one_node_run(monkeypatch)
        db_box["db"] = db
        await service.advance_run(_NullPool(), run_id,
                                  registry=ExecutorRegistry({"tool_call": answer}), keys=KEYS)

        assert db.executions[0]["output_data"]["content"] == "Termin mit {{PERSON_3}}"
        assert _opened(db, run_id) == {"PERSON_1": _ERIKA, "PERSON_2": "Max",
                                       "PERSON_3": "Anna"}

    async def test_a_run_with_a_mapping_does_not_advance_without_keys(self, monkeypatch):
        """Whether a run keeps placeholders is its row's to say, never the
        key's: a missing key stops it rather than letting a value through."""
        db, run_id = _one_node_run(monkeypatch)
        with pytest.raises(mapping_cipher.MappingKeyError, match="no key"):
            await service.advance_run(_NullPool(), run_id,
                                      registry=ExecutorRegistry({"tool_call": AsyncMock()}))
        assert db.executions == [], "nothing was claimed"

    async def test_a_mapping_that_does_not_open_ends_the_run_with_its_reason(self, monkeypatch):
        """Changed while the step ran -- here: sealed with a key the run does
        not hold. The run fails saying why instead of staying `running`."""
        other = mapping_cipher.keyring_from(mapping_cipher.generate_key("other"))
        db_box: dict = {}

        async def answer(ctx: NodeContext) -> NodeResult:
            _seal_into(db_box["db"], ctx.run_id, dict(ctx.run_mapping), keys=other)
            return NodeResult(output={"content": "{{PERSON_1}}"},
                              run_mapping={**ctx.run_mapping, "EMAIL_1": "e@example.de"})

        db, run_id = _one_node_run(monkeypatch)
        db_box["db"] = db
        with pytest.raises(mapping_cipher.MappingUnreadableError):
            await service.advance_run(_NullPool(), run_id,
                                      registry=ExecutorRegistry({"tool_call": answer}), keys=KEYS)
        assert db.runs[run_id]["status"] == "failed"
        assert "'other'" in db.runs[run_id]["error_message"]
        assert db.executions[0]["status"] == "failed"

    async def test_a_run_started_before_chy_234_advances_as_it_did(self, monkeypatch):
        """No mapping row: the step is handed none and replaces only for the
        length of its model call (E6)."""
        seen: list = []

        async def answer(ctx: NodeContext) -> NodeResult:
            seen.append(ctx.run_mapping)
            return NodeResult(output={})

        a = _node("a")
        db = FakeDb(nodes=[a])
        db.install(monkeypatch)
        run_id = _run(db)
        await service.advance_run(_NullPool(), run_id,
                                  registry=ExecutorRegistry({"tool_call": answer}))
        assert seen == [None]
        assert run_id not in db.mappings


# ── the reason at the gate (E7) ───────────────────────────────────────────────

class TestTheReasonAtTheGate:
    REASON = "Nicht die Akte von Erika Mustermann, Rückruf: 0171 5512345"

    async def test_it_is_stored_with_the_runs_placeholders(self, monkeypatch):
        """Whoever rejects writes the name easily: it reaches the vote, the
        gate and the run -- each with placeholders, and the phone number the
        run did not know yet extends its mapping."""
        from contexts.orchestration.schema import OversightDecision

        db, run_id, _ = await _at_the_gate(monkeypatch)
        _seal_into(db, run_id, {"PERSON_1": _ERIKA})
        await service.record_oversight(
            _NullPool(), stub_user(), run_id,
            OversightDecision(decision="rejected", reason=self.REASON), engine=_engine())

        stored = "Nicht die Akte von {{PERSON_1}}, Rückruf: {{PHONE_1}}"
        assert db.votes[0]["reason"] == stored
        assert db.runs[run_id]["error_message"] == stored
        assert next(e for e in db.executions if e["status"] == "failed")["error_message"] == stored
        assert _opened(db, run_id) == {"PERSON_1": _ERIKA, "PHONE_1": "0171 5512345"}

    async def test_a_run_started_before_chy_234_keeps_it_as_written(self, monkeypatch):
        from contexts.orchestration.schema import OversightDecision

        db, run_id, _ = await _at_the_gate(monkeypatch)
        await service.record_oversight(
            _NullPool(), stub_user(), run_id,
            OversightDecision(decision="rejected", reason=self.REASON), engine=_engine())
        assert db.votes[0]["reason"] == self.REASON


# ── the key ───────────────────────────────────────────────────────────────────

class TestTheKey:
    async def test_the_api_does_not_start_without_one(self, monkeypatch):
        """E5: refused at start, before the pool, instead of at every run."""
        from api import main
        from api.v1 import _redaction

        create_pool = AsyncMock()
        monkeypatch.setattr(main, "create_pool", create_pool)
        monkeypatch.setattr(settings, "pii_mapping_keys", "")
        _redaction.mapping_keys.cache_clear()
        try:
            with pytest.raises(mapping_cipher.MappingKeyError, match="PII_MAPPING_KEYS"):
                async with main.lifespan(main.app):
                    pass
        finally:
            _redaction.mapping_keys.cache_clear()
        create_pool.assert_not_awaited()


# ── the resolve route ─────────────────────────────────────────────────────────

class TestTheResolveRoute:
    """The wiring. Who may resolve, and the audit, against PostgreSQL:
    tests/integration/test_run_placeholders_db.py."""

    def test_a_post_without_a_body_resolves_every_step(self, monkeypatch):
        from api.v1 import workflows
        from contexts.orchestration.schema import RunResolvedOut
        from tests.conftest import client_for, make_pool

        asked = []

        async def resolve(_pool, _actor, run_id, body, *, keys):
            asked.append((body.nodes, keys.current))
            return RunResolvedOut(run_id=run_id, status="awaiting_oversight",
                                  input_data={"letter": "Liebe Erika"}, nodes=[])

        monkeypatch.setattr(workflows.service, "resolve_run", resolve)
        with client_for(make_pool()) as client:
            resp = client.post(f"/v1/workflows/runs/{uuid4()}/resolve")
        assert resp.status_code == 200, resp.text
        assert resp.json()["input_data"] == {"letter": "Liebe Erika"}
        assert asked == [(None, "test")]

    def test_a_purged_run_is_gone(self, monkeypatch):
        from api.v1 import workflows
        from contexts.orchestration.errors import RunContentPurged
        from tests.conftest import client_for, make_pool

        async def resolve(*_a, **_k):
            raise RunContentPurged

        monkeypatch.setattr(workflows.service, "resolve_run", resolve)
        with client_for(make_pool()) as client:
            resp = client.post(f"/v1/workflows/runs/{uuid4()}/resolve", json={"nodes": ["draft"]})
        assert resp.status_code == 410, resp.text
