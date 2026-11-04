"""
tests/test_pilots.py

Unit tests for GET|POST /v1/pilots/runs, GET /v1/pilots/runs/{id}, and
GET /v1/pilots/runs/{id}/stream (Issues #14, streaming extension).

Mocks: asyncpg pool (DB), pathlib.Path.exists (input file), _bg_run (prevents
actual workflow execution). No real PostgreSQL, LiteLLM, or pilot code needed.
"""
import json
from datetime import datetime, UTC
import pathlib
import tempfile
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from fastapi.testclient import TestClient

from api.main import app
from api.deps import require_auth, CurrentUser
from api.config import settings
from contexts.pilots.registry import registry
from core.db import get_pool
from core.redis import get_redis
from tests.conftest import actor_row, as_caller, make_pool, make_redis

_stub_user = CurrentUser(sub="test-sub", username="testuser")
_RUN_ID = uuid4()
_CREATED = datetime(2026, 7, 4, 12, 0, tzinfo=UTC)


#: The configuration row the seeded database has for `study` (migration 017).
#: Present in the double because starting a run composes from it — a pool that
#: answers None here would send every run down the "seed is missing" branch and
#: the tests would be exercising an error path while looking like they cover
#: the happy one (CHY-42).
_STUDY_CONFIG = {
    "pilot_name": "study",
    "version": 1,
    "model_map": {"small": "phi3-mini", "medium": "gemma4-e4b"},
    "params": {},
    "updated_at": _CREATED,
}


def _make_run_record(**overrides) -> dict:
    return {
        "id": _RUN_ID,
        "thread_id": "study-test123",
        "pilot_name": "study",
        "input_file": "study-example.pdf",
        "status": "pending",
        "started_at": None,
        "completed_at": None,
        "result_json": None,
        "error_message": None,
        "created_at": _CREATED,
        "run_config": {},
        "effective_config": None,
        **overrides,
    }


def _make_pool(**kw) -> MagicMock:
    """Shared double from conftest since CHY-88.

    Starting a run now resolves the caller to an actor and writes the row inside
    a transaction, so the pool has to answer an actor lookup and hand out a
    connection. The local three-line mock could do neither — it returned the
    same row for every fetchrow, including the actor query, and resolve_actor
    then failed on a missing 'role'.
    """
    return make_pool(**kw)


def _make_client(pool=None, redis=None) -> TestClient:
    app.dependency_overrides[require_auth] = as_caller(_stub_user)
    app.dependency_overrides[get_pool] = lambda: (pool if pool is not None else _make_pool())
    # Starting a run reads the workflow configuration, which prefers Redis
    # (CHY-42). make_redis() misses on every key, so the tests take the
    # database path — which is the one worth exercising here.
    app.dependency_overrides[get_redis] = lambda: redis if redis is not None else make_redis()
    return TestClient(app)


def _cleanup():
    app.dependency_overrides.pop(require_auth, None)
    app.dependency_overrides.pop(get_pool, None)
    app.dependency_overrides.pop(get_redis, None)


# ── GET /v1/pilots/runs ───────────────────────────────────────────────────────


@contextmanager
def _real_input_file(name: str = "study-example.pdf"):
    """A real file in a real directory, instead of patching Path.exists away.

    The previous tests did `patch("pathlib.Path.exists", return_value=True)`,
    which is why the path traversal in CHY-90 survived: a test that replaces the
    filesystem cannot notice that the path left it. `../../etc/passwd` "existed"
    too.
    """
    with tempfile.TemporaryDirectory() as d:
        base = pathlib.Path(d)
        (base / name).write_bytes(b"%PDF-1.4 test")
        old = settings.pilot_input_dir
        settings.pilot_input_dir = str(base)
        try:
            yield base
        finally:
            settings.pilot_input_dir = old


def test_list_runs_empty():
    pool = _make_pool()
    pool.fetch = AsyncMock(return_value=[])
    client = _make_client(pool)
    try:
        resp = client.get("/v1/pilots/runs")
        assert resp.status_code == 200
        assert resp.json() == []
    finally:
        _cleanup()


def test_list_runs_returns_records():
    pool = _make_pool()
    pool.fetch = AsyncMock(return_value=[_make_run_record()])
    client = _make_client(pool)
    try:
        resp = client.get("/v1/pilots/runs")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["status"] == "pending"
        assert data[0]["pilot_name"] == "study"
        assert data[0]["input_file"] == "study-example.pdf"
    finally:
        _cleanup()


def test_list_runs_requires_auth():
    resp = TestClient(app).get("/v1/pilots/runs")
    assert resp.status_code in (401, 403)


# ── GET /v1/pilots/runs/{id} ──────────────────────────────────────────────────

def test_get_run_found():
    pool = _make_pool()
    pool.fetchrow = AsyncMock(return_value=_make_run_record(status="running"))
    client = _make_client(pool)
    try:
        resp = client.get(f"/v1/pilots/runs/{_RUN_ID}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == str(_RUN_ID)
        assert data["status"] == "running"
    finally:
        _cleanup()


def test_get_run_complete_with_result():
    result_data = {"fakten": ["Fakt 1", "Fakt 2"], "lernziel_erreicht": True}
    pool = _make_pool()
    pool.fetchrow = AsyncMock(return_value=_make_run_record(
        status="complete", result_json=result_data
    ))
    client = _make_client(pool)
    try:
        resp = client.get(f"/v1/pilots/runs/{_RUN_ID}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "complete"
        assert data["result_json"]["lernziel_erreicht"] is True
        assert len(data["result_json"]["fakten"]) == 2
    finally:
        _cleanup()


def test_get_run_not_found():
    pool = _make_pool()
    pool.fetchrow = AsyncMock(return_value=None)
    client = _make_client(pool)
    try:
        resp = client.get(f"/v1/pilots/runs/{uuid4()}")
        assert resp.status_code == 404
    finally:
        _cleanup()


def test_get_run_requires_auth():
    resp = TestClient(app).get(f"/v1/pilots/runs/{_RUN_ID}")
    assert resp.status_code in (401, 403)


# ── POST /v1/pilots/runs ──────────────────────────────────────────────────────

def test_start_run_unknown_pilot():
    client = _make_client()
    try:
        resp = client.post("/v1/pilots/runs", json={
            "pilot_name": "does_not_exist",
            "input_file": "test.pdf",
        })
        assert resp.status_code == 400
        assert "Unknown pilot" in resp.json()["detail"]
    finally:
        _cleanup()


def test_start_run_file_not_found():
    pool = _make_pool()
    client = _make_client(pool)
    with patch("pathlib.Path.exists", return_value=False):
        try:
            resp = client.post("/v1/pilots/runs", json={"input_file": "missing.pdf"})
            assert resp.status_code == 404
            assert "not found" in resp.json()["detail"].lower()
        finally:
            _cleanup()


def test_start_run_success():
    pool = _make_pool(conn_fetchrow=_make_run_record())
    client = _make_client(pool)
    with _real_input_file(), patch("contexts.pilots.service.run_in_thread"):
        try:
            resp = client.post("/v1/pilots/runs", json={"input_file": "study-example.pdf"})
            assert resp.status_code == 201
            data = resp.json()
            assert data["status"] == "pending"
            assert data["pilot_name"] == "study"
            assert data["input_file"] == "study-example.pdf"
            # notify event was registered for SSE streaming
            assert registry.is_live(_RUN_ID)
        finally:
            registry.forget(_RUN_ID)
            _cleanup()


def test_start_run_custom_thread_id():
    pool = _make_pool(conn_fetchrow=_make_run_record(thread_id="my-custom-run-42"))
    client = _make_client(pool)
    with _real_input_file(), patch("contexts.pilots.service.run_in_thread"):
        try:
            resp = client.post("/v1/pilots/runs", json={
                "input_file": "study-example.pdf",
                "thread_id": "my-custom-run-42",
            })
            assert resp.status_code == 201
            assert resp.json()["thread_id"] == "my-custom-run-42"
            call_args = pool.conn.fetchrow.call_args[0]
            assert "my-custom-run-42" in call_args
        finally:
            registry.forget(_RUN_ID)
            _cleanup()


def _start_with(role: str, run_config: dict):
    """POST a run as an actor with this role; returns (response, run_in_thread mock)."""
    pool = _make_pool(actor=actor_row(role=role), conn_fetchrow=_make_run_record(run_config=run_config))
    client = _make_client(pool)
    with _real_input_file(), patch("contexts.pilots.service.run_in_thread") as started:
        try:
            body = {"input_file": "study-example.pdf"}
            if run_config:
                body["run_config"] = run_config
            return client.post("/v1/pilots/runs", json=body), started
        finally:
            registry.forget(_RUN_ID)
            _cleanup()


def test_overriding_the_model_map_for_one_run_requires_architect():
    """The run override may not do what storing the map is not allowed to (CHY-196).

    Storing a model map needs `architect`, because the map is the local/cloud
    boundary. Before, any author could pass the same map as `run_config` and
    send the banking pilot's documents to a hosted model.
    """
    resp, started = _start_with("contributor", {"model_map": {"medium": "cloud-model"}})
    assert resp.status_code == 403, resp.text
    started.assert_not_called()


def test_an_architect_may_override_the_model_map_for_one_run():
    resp, started = _start_with("architect", {"model_map": {"medium": "gemma4-e4b"}})
    assert resp.status_code == 201, resp.text
    started.assert_called_once()


def test_a_run_without_an_override_needs_no_architect():
    """The gate is on the override, not on starting a run at all."""
    resp, started = _start_with("contributor", {})
    assert resp.status_code == 201, resp.text
    started.assert_called_once()


def test_start_run_requires_auth():
    resp = TestClient(app).post("/v1/pilots/runs", json={"input_file": "test.pdf"})
    assert resp.status_code in (401, 403)


# ── GET /v1/pilots/runs/{id}/stream ──────────────────────────────────────────

def test_stream_run_not_found():
    pool = _make_pool()
    pool.fetchrow = AsyncMock(return_value=None)
    client = _make_client(pool)
    try:
        resp = client.get(f"/v1/pilots/runs/{uuid4()}/stream")
        assert resp.status_code == 404
    finally:
        _cleanup()


def test_stream_run_requires_auth():
    resp = TestClient(app).get(f"/v1/pilots/runs/{_RUN_ID}/stream")
    assert resp.status_code in (401, 403)


def test_stream_run_already_complete():
    """For a finished run, endpoint synthesizes a done event immediately and closes."""
    result_data = {"fakten": ["F1", "F2", "F3"], "lernziel_erreicht": True, "iteration_count": 1}
    pool = _make_pool()
    pool.fetchrow = AsyncMock(return_value={
        "status": "complete",
        "result_json": result_data,
        "events_json": None,
    })
    client = _make_client(pool)
    try:
        resp = client.get(f"/v1/pilots/runs/{_RUN_ID}/stream")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        lines = [ln for ln in resp.text.splitlines() if ln.startswith("data: ")]
        assert len(lines) == 2  # done + eof
        done_event = json.loads(lines[0][6:])
        assert done_event["type"] == "done"
        assert done_event["facts_count"] == 3
        assert done_event["lernziel_erreicht"] is True
        eof_event = json.loads(lines[1][6:])
        assert eof_event["type"] == "eof"
    finally:
        _cleanup()


def test_stream_run_live_events():
    """Live run: SSE endpoint yields all queued events and closes on sentinel."""
    pool = _make_pool()
    pool.fetchrow = AsyncMock(return_value={"status": "running", "result_json": None})
    client = _make_client(pool)

    # Simulate the background thread emitting before the client connects —
    # through the registry's own interface, so the test exercises the path the
    # executor actually takes rather than the dicts underneath it.
    registry.open(_RUN_ID)
    registry.push(_RUN_ID, {"type": "node_complete", "node": "ingest", "elapsed_ms": 180,
                            "details": "847 Chunks geladen", "iteration": 0, "preview": None})
    registry.push(_RUN_ID, {"type": "done", "lernziel_erreicht": True,
                            "iteration_count": 1, "facts_count": 42})
    registry.push(_RUN_ID, None)  # sentinel

    try:
        resp = client.get(f"/v1/pilots/runs/{_RUN_ID}/stream")
        assert resp.status_code == 200
        lines = [ln for ln in resp.text.splitlines() if ln.startswith("data: ")]
        assert len(lines) == 3  # node_complete + done + eof

        node_evt = json.loads(lines[0][6:])
        assert node_evt["type"] == "node_complete"
        assert node_evt["node"] == "ingest"
        assert node_evt["details"] == "847 Chunks geladen"

        done_evt = json.loads(lines[1][6:])
        assert done_evt["type"] == "done"
        assert done_evt["facts_count"] == 42

        eof_evt = json.loads(lines[2][6:])
        assert eof_evt["type"] == "eof"
    finally:
        registry.forget(_RUN_ID)
        _cleanup()


def test_stream_run_live_queue_cleaned_up_after_eof():
    """The registry forgets a run once its stream has sent eof."""
    pool = _make_pool()
    pool.fetchrow = AsyncMock(return_value={"status": "running", "result_json": None})
    client = _make_client(pool)

    registry.open(_RUN_ID)
    registry.push(_RUN_ID, None)  # no events, straight to the sentinel

    try:
        client.get(f"/v1/pilots/runs/{_RUN_ID}/stream")
        assert not registry.is_live(_RUN_ID)
        assert not registry.is_done(_RUN_ID)
    finally:
        registry.forget(_RUN_ID)
        _cleanup()


def test_stream_replay_from_events_json():
    """Completed run with populated events_json: all events replayed in order before eof."""
    events = [
        {"type": "node_complete", "node": "ingest", "elapsed_ms": 180, "details": "847 Chunks geladen", "iteration": 0, "preview": None},
        {"type": "node_complete", "node": "extract", "elapsed_ms": 320, "details": "12 Fakten extrahiert", "iteration": 0, "preview": None},
        {"type": "done", "lernziel_erreicht": True, "iteration_count": 2, "facts_count": 12},
    ]
    pool = _make_pool()
    pool.fetchrow = AsyncMock(return_value={
        "status": "complete",
        "result_json": {"lernziel_erreicht": True, "fakten": ["f"] * 12, "iteration_count": 2},
        "events_json": events,
    })
    client = _make_client(pool)
    try:
        resp = client.get(f"/v1/pilots/runs/{_RUN_ID}/stream")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        lines = [ln for ln in resp.text.splitlines() if ln.startswith("data: ")]
        assert len(lines) == 4  # 3 stored events + eof

        evt0 = json.loads(lines[0][6:])
        assert evt0["type"] == "node_complete"
        assert evt0["node"] == "ingest"
        assert evt0["details"] == "847 Chunks geladen"

        evt1 = json.loads(lines[1][6:])
        assert evt1["type"] == "node_complete"
        assert evt1["node"] == "extract"

        evt2 = json.loads(lines[2][6:])
        assert evt2["type"] == "done"
        assert evt2["facts_count"] == 12
        assert evt2["lernziel_erreicht"] is True

        assert json.loads(lines[3][6:])["type"] == "eof"
    finally:
        _cleanup()


def test_stream_oversight_historical_rewrite():
    """oversight_required is rewritten to oversight_historical when run is no longer awaiting_oversight."""
    events = [
        {"type": "node_complete", "node": "compliance_check", "elapsed_ms": 90, "iteration": 0, "details": "3 Risiken gefunden", "preview": None},
        {"type": "oversight_required", "node": "compliance_check", "question": "Ist Risiko A kritisch?"},
        {"type": "node_complete", "node": "summarize", "elapsed_ms": 200, "iteration": 0, "details": "Bericht erstellt", "preview": None},
        {"type": "done", "lernziel_erreicht": True, "iteration_count": 1, "facts_count": 5},
    ]
    pool = _make_pool()
    pool.fetchrow = AsyncMock(return_value={
        "status": "complete",
        "result_json": None,
        "events_json": events,
    })
    client = _make_client(pool)
    try:
        resp = client.get(f"/v1/pilots/runs/{_RUN_ID}/stream")
        assert resp.status_code == 200
        lines = [ln for ln in resp.text.splitlines() if ln.startswith("data: ")]
        assert len(lines) == 5  # 4 stored events + eof

        # oversight_required → oversight_historical because status != "awaiting_oversight"
        oversight_evt = json.loads(lines[1][6:])
        assert oversight_evt["type"] == "oversight_historical"
        assert oversight_evt["node"] == "compliance_check"
        assert oversight_evt["question"] == "Ist Risiko A kritisch?"

        # surrounding events are passed through unchanged
        assert json.loads(lines[0][6:])["type"] == "node_complete"
        assert json.loads(lines[2][6:])["type"] == "node_complete"
        assert json.loads(lines[3][6:])["type"] == "done"
        assert json.loads(lines[4][6:])["type"] == "eof"
    finally:
        _cleanup()


def test_stream_oversight_required_preserved_when_awaiting():
    """oversight_required is NOT rewritten when run is still in awaiting_oversight state."""
    events = [
        {"type": "oversight_required", "node": "compliance_check", "question": "Genehmigen?"},
    ]
    pool = _make_pool()
    pool.fetchrow = AsyncMock(return_value={
        "status": "awaiting_oversight",
        "result_json": None,
        "events_json": events,
    })
    client = _make_client(pool)
    try:
        resp = client.get(f"/v1/pilots/runs/{_RUN_ID}/stream")
        assert resp.status_code == 200
        lines = [ln for ln in resp.text.splitlines() if ln.startswith("data: ")]
        assert len(lines) == 2  # 1 stored event + eof

        evt = json.loads(lines[0][6:])
        assert evt["type"] == "oversight_required"  # NOT rewritten
        assert evt["question"] == "Genehmigen?"
    finally:
        _cleanup()


# ── CHY-88: Urheberschaft von Läufen und Art.-14-Freigaben ────────────────────
#
# Migration 006 heißt im Kopf "Human Oversight support (EU AI Act Art. 14)" und
# speicherte, *was* entschieden wurde — nicht wer und nicht wann. submit_oversight
# bekam den Actor injiziert und las ihn nie. Diese Tests halten fest, dass beides
# jetzt geschrieben wird, und zwar in derselben Transaktion wie die Entscheidung.

def test_start_run_records_who_started_it():
    pool = _make_pool(conn_fetchrow=_make_run_record())
    client = _make_client(pool)
    with _real_input_file(), patch("contexts.pilots.service.run_in_thread"):
        try:
            resp = client.post("/v1/pilots/runs", json={"input_file": "study-example.pdf"})
            assert resp.status_code == 201

            insert_sql = pool.conn.fetchrow.call_args[0][0]
            assert "actor_id" in insert_sql

            # write_audit passes its values positionally:
            #   (sql, actor_id, action, target_ref, payload)
            actions = [c[0][2] for c in pool.conn.fetchval.call_args_list if len(c[0]) > 2]
            assert "pilot.run_started" in actions
        finally:
            registry.forget(_RUN_ID)
            _cleanup()


def _awaiting_run(actor_id=None):
    return {"status": "awaiting_oversight", "actor_id": actor_id or uuid4()}


def test_oversight_records_approver_and_time():
    """The column trio moves together — the trigger in migration 014 refuses the
    UPDATE otherwise, so a statement missing one of them would fail in
    production rather than here. Asserting it in the SQL keeps the two in step."""

    run_id = uuid4()
    pool = _make_pool(fetchrow=_awaiting_run())
    client = _make_client(pool)
    registry.open_oversight_gate(run_id)
    try:
        resp = client.post(f"/v1/pilots/runs/{run_id}/oversight",
                           json={"decision": "approved"})
        assert resp.status_code == 200

        update_sql = pool.conn.execute.call_args[0][0]
        assert "oversight_by" in update_sql
        assert "oversight_at" in update_sql
    finally:
        registry.forget(run_id)
        _cleanup()


def test_an_author_cannot_release_the_oversight_gate():
    """The Art. 14 decision needs `reviewer`, like approving a block (CHY-196).

    Before, any author could approve a gated banking run, which made the human
    in the loop anyone at all. Refused before anything is written, and the gate
    stays where it was.
    """
    run_id = uuid4()
    pool = _make_pool(actor=actor_row(role="contributor"), fetchrow=_awaiting_run())
    client = _make_client(pool)
    registry.open_oversight_gate(run_id)
    try:
        resp = client.post(f"/v1/pilots/runs/{run_id}/oversight",
                           json={"decision": "approved"})
        assert resp.status_code == 403, resp.text
        pool.conn.execute.assert_not_called()
        assert registry.has_oversight_gate(run_id)
    finally:
        registry.forget(run_id)
        _cleanup()


def test_a_reviewer_can_release_the_oversight_gate():
    run_id = uuid4()
    pool = _make_pool(actor=actor_row(role="reviewer"), fetchrow=_awaiting_run())
    client = _make_client(pool)
    registry.open_oversight_gate(run_id)
    try:
        resp = client.post(f"/v1/pilots/runs/{run_id}/oversight",
                           json={"decision": "approved"})
        assert resp.status_code == 200, resp.text
    finally:
        registry.forget(run_id)
        _cleanup()


def test_oversight_audit_names_both_sides():
    """Four eyes is not enforced — one-person operation, same call as for the
    blocks in CHY-32. But a self-approval must be recognisable afterwards, so
    the entry carries starter and decider rather than only the decider."""

    run_id, starter = uuid4(), uuid4()
    pool = _make_pool(fetchrow=_awaiting_run(actor_id=starter))
    client = _make_client(pool)
    registry.open_oversight_gate(run_id)
    try:
        client.post(f"/v1/pilots/runs/{run_id}/oversight", json={"decision": "rejected"})

        # positional: (sql, actor_id, action, target_ref, payload)
        args = next(c[0] for c in pool.conn.fetchval.call_args_list
                    if len(c[0]) > 2 and c[0][2] == "pilot.oversight_decided")
        actor_id, payload = args[1], json.loads(args[4])
        assert payload["decision"] == "rejected"
        assert payload["started_by"] == str(starter)
        assert payload["decided_by"] == str(actor_id)
    finally:
        registry.forget(run_id)
        _cleanup()
