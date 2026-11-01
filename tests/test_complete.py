"""
tests/test_complete.py

Unit tests for POST /v1/complete (Issue #10, #22).

LiteLLM is mocked via httpx mock — no real LiteLLM needed.

The patch target is `core.completion.httpx` since CHY-129: the model call moved
out of the route handler so that the workflow runner and a scheduled job can
reach it too. These tests still drive it through the endpoint, which is the
right level for them — what changed is where the client lives, not what the
endpoint promises.
DB pool is mocked via dependency override — no real PostgreSQL needed.
Tests cover: routing, block injection, auth, error handling.
"""
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, UTC
from uuid import uuid4

import httpx
from fastapi.testclient import TestClient

from api.main import app
from api.deps import require_auth, CurrentUser
from core.db import get_pool
from core.redis import get_redis
from tests.conftest import make_pool, make_redis

_stub_user = CurrentUser(sub="test-sub", username="testuser")
_SCOPE_ID = uuid4()
_NOW = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)
_BLOCK_ID = uuid4()


_CHAIN = [{"id": _SCOPE_ID, "scope_path": "org_x", "node_type": "org", "name": "x", "depth": 1, "privacy_level": None}]


def _block(content: str, **kw) -> dict:
    """A composed block as core.compose reads it out of the database."""
    return {
        "block_id": kw.get("block_id", uuid4()),
        "version": kw.get("version", 1),
        "block_type": kw.get("block_type", "system_prompt"),
        "content": content,
        "scope_id": kw.get("scope_id", _SCOPE_ID),
        "tags": [],
        "scope_path": "org_x",
        "depth": 1, "privacy_level": None,
        # Selected by the query since CHY-62. Present here even though this
        # test never sets them: a fixture that omits a column the query returns
        # would read as "makes no governance claim" for the wrong reason, and
        # the ratchet would be silently untested rather than satisfied.
        "governance_key": kw.get("governance_key"),
        "privacy_impact": kw.get("privacy_impact"),
        "locked": kw.get("locked", False),
        "created_at": _NOW,
    }


#: One rule for every task type these tests ask for. The chain is two local
#: models on different hosts, which is what the fallback tests need.
_ROUTING_RULES = [
    {
        "block_id": uuid4(), "version": 1, "scope_id": _SCOPE_ID,
        "content": "Standard für die Tests",
        "metadata": {"task_type": t, "candidates": ["gemma3-4b", "phi3-mini"],
                     "max_tokens": 256},
    }
    for t in ("summarize", "extract", "mindmap", "quiz", "audio_script")
]


def _make_pool(fetch_return=None, scope_exists=True) -> MagicMock:
    """Pool for /v1/complete.

    Since CHY-24 the scope path goes through core.compose, which reads the
    ancestor chain first and the blocks second. Dispatching on the statement
    rather than counting calls keeps this working for the explicit-ids path
    too, which issues only one query.
    """
    pool = make_pool()
    rows = fetch_return or []

    async def _fetch(sql, *args):
        if "scope_nodes" in sql and "context_blocks" not in sql:
            return _CHAIN if scope_exists else []
        # Since CHY-87 step 2b the model is a stored decision, read from the
        # same table by its own query. Dispatched on the statement rather than
        # answered with `rows`, which would hand the composition path a routing
        # rule and the routing path a system prompt.
        if "block_type = 'routing_rule'" in sql:
            return _ROUTING_RULES
        return rows

    pool.fetch = AsyncMock(side_effect=_fetch)
    return pool


def _make_client(pool=None, redis=None) -> TestClient:
    app.dependency_overrides[require_auth] = lambda: _stub_user
    app.dependency_overrides[get_pool] = lambda: (pool if pool is not None else _make_pool())
    app.dependency_overrides[get_redis] = lambda: redis if redis is not None else make_redis()
    return TestClient(app)


def _cleanup():
    app.dependency_overrides.pop(require_auth, None)
    app.dependency_overrides.pop(get_pool, None)
    app.dependency_overrides.pop(get_redis, None)


def _litellm_response(content: str, model: str = "gemma3-4b") -> dict:
    return {
        "id": "chatcmpl-abc123",
        "model": model,
        "choices": [{"message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 50, "completion_tokens": 100},
    }


def _mock_http(content: str = "Antwort."):
    """Returns a pre-wired AsyncClient mock that responds with a LiteLLM response."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = _litellm_response(content)
    mock_resp.raise_for_status = MagicMock()

    mock_http = AsyncMock()
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=False)
    mock_http.post = AsyncMock(return_value=mock_resp)
    return mock_http


# --- Passthrough tests (no blocks) ---

def test_complete_basic():
    client = _make_client()
    with patch("core.completion.httpx.AsyncClient") as mock_cls:
        mock_cls.return_value = _mock_http("Eine Zusammenfassung.")
        try:
            resp = client.post("/v1/complete", json={"scope_id": str(_SCOPE_ID), 
                "task_type": "summarize",
                "prompt": "Fasse diesen Text zusammen: ...",
            })
            assert resp.status_code == 200
            data = resp.json()
            assert data["content"] == "Eine Zusammenfassung."
            assert data["model_used"] == "gemma3-4b"
            assert data["input_tokens"] == 50
            assert data["output_tokens"] == 100
            assert data["latency_ms"] >= 0
            assert data["blocks_used"] == 0
        finally:
            _cleanup()


def test_complete_requires_auth():
    client = TestClient(app)
    resp = client.post("/v1/complete", json={"scope_id": str(_SCOPE_ID), "task_type": "summarize", "prompt": "test"})
    assert resp.status_code in (401, 403)


def test_complete_missing_prompt():
    client = _make_client()
    try:
        resp = client.post("/v1/complete", json={"scope_id": str(_SCOPE_ID), "task_type": "summarize"})
        assert resp.status_code == 422
    finally:
        _cleanup()


def test_complete_missing_task_type():
    client = _make_client()
    try:
        resp = client.post("/v1/complete", json={"scope_id": str(_SCOPE_ID), "prompt": "test"})
        assert resp.status_code == 422
    finally:
        _cleanup()


def test_complete_litellm_error_502():
    client = _make_client()
    mock_http = AsyncMock()
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=False)
    error_resp = MagicMock()
    error_resp.status_code = 500
    error_resp.text = "Internal Server Error"
    mock_http.post = AsyncMock(
        side_effect=httpx.HTTPStatusError("error", request=MagicMock(), response=error_resp)
    )
    with patch("core.completion.httpx.AsyncClient") as mock_cls:
        mock_cls.return_value = mock_http
        try:
            resp = client.post("/v1/complete", json={"scope_id": str(_SCOPE_ID), "task_type": "summarize", "prompt": "test"})
            assert resp.status_code == 502
        finally:
            _cleanup()


def test_complete_litellm_unreachable_503():
    client = _make_client()
    mock_http = AsyncMock()
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=False)
    mock_http.post = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))
    with patch("core.completion.httpx.AsyncClient") as mock_cls:
        mock_cls.return_value = mock_http
        try:
            resp = client.post("/v1/complete", json={"scope_id": str(_SCOPE_ID), "task_type": "summarize", "prompt": "test"})
            assert resp.status_code == 503
        finally:
            _cleanup()


def test_complete_refuses_a_task_type_nobody_decided():
    """The behaviour that replaced the YAML `default` route (CHY-87 step 2b).

    Until the rules moved into the scope tree, an unknown task_type quietly got
    the default model and the call succeeded. That was a reasonable default for
    a file and a bad one for a governance decision: the trace then names a
    model, and nothing distinguishes "somebody chose this" from "nobody did".

    409 and not 404 — the scope exists and the name is fine; what is missing is
    a decision, and the fix is to approve a rule.
    """
    client = _make_client()
    with patch("core.completion.httpx.AsyncClient") as mock_cls:
        mock_cls.return_value = _mock_http("Antwort.")
        try:
            resp = client.post("/v1/complete", json={
                "scope_id": str(_SCOPE_ID),
                "task_type": "completely_unknown_task",
                "prompt": "test",
            })
            assert resp.status_code == 409
            assert "completely_unknown_task" in resp.json()["detail"]
            assert mock_cls.call_count == 0, "a model was called anyway"
        finally:
            _cleanup()


def test_complete_without_a_scope_is_refused():
    """Routing is decided per scope, so a completion without one has nowhere to
    read its decision from — including the explicit-block-ids path, where the
    context needs no scope but the model choice still does."""
    client = _make_client()
    try:
        resp = client.post("/v1/complete", json={
            "task_type": "summarize", "prompt": "test",
        })
        assert resp.status_code == 422
        assert "scope_id" in resp.json()["detail"]
    finally:
        _cleanup()


# --- Block injection tests (#22) ---

def test_complete_injects_active_block_as_system_message():
    """Active block content is sent as a system message before the user prompt."""
    pool = _make_pool(fetch_return=[_block("Du bist ein Experte.")])
    client = _make_client(pool)

    with patch("core.completion.httpx.AsyncClient") as mock_cls:
        http = _mock_http()
        mock_cls.return_value = http
        try:
            resp = client.post("/v1/complete", json={
                "task_type": "summarize",
                "prompt": "Test",
                "scope_id": str(_SCOPE_ID),
            })
            assert resp.status_code == 200
            assert resp.json()["blocks_used"] == 1

            messages = http.post.call_args.kwargs["json"]["messages"]
            assert messages[0]["role"] == "system"
            assert "Du bist ein Experte." in messages[0]["content"]
            assert messages[1]["role"] == "user"
            assert messages[1]["content"] == "Test"
        finally:
            _cleanup()


def test_complete_no_scope_no_blocks_injected():
    """Without scope_id or context_block_ids, only the user message is sent."""
    client = _make_client()

    with patch("core.completion.httpx.AsyncClient") as mock_cls:
        http = _mock_http()
        mock_cls.return_value = http
        try:
            resp = client.post("/v1/complete", json={"scope_id": str(_SCOPE_ID), "task_type": "summarize", "prompt": "Test"})
            assert resp.status_code == 200
            assert resp.json()["blocks_used"] == 0

            messages = http.post.call_args.kwargs["json"]["messages"]
            assert len(messages) == 1
            assert messages[0]["role"] == "user"
        finally:
            _cleanup()


def test_complete_draft_block_not_injected():
    """compose filters status='active'; an empty block list means the scope has
    nothing approved yet — a 200 with no system message, not an error."""
    pool = _make_pool(fetch_return=[])
    client = _make_client(pool)

    with patch("core.completion.httpx.AsyncClient") as mock_cls:
        mock_cls.return_value = _mock_http()
        try:
            resp = client.post("/v1/complete", json={
                "task_type": "summarize",
                "prompt": "Test",
                "scope_id": str(_SCOPE_ID),
            })
            assert resp.status_code == 200
            assert resp.json()["blocks_used"] == 0
        finally:
            _cleanup()


def test_complete_explicit_block_ids():
    """context_block_ids selects specific blocks from the scope's own chain."""
    # Rows, not bare contents: since CHY-29 the query also selects block_id and
    # version so the trace can record *which version* answered.
    pool = _make_pool(fetch_return=[_block("Block A"), _block("Block B")])
    client = _make_client(pool)

    with patch("core.completion.httpx.AsyncClient") as mock_cls:
        http = _mock_http()
        mock_cls.return_value = http
        try:
            resp = client.post("/v1/complete", json={"scope_id": str(_SCOPE_ID), 
                "task_type": "summarize",
                "prompt": "Test",
                "context_block_ids": [str(_BLOCK_ID)],
            })
            assert resp.status_code == 200
            assert resp.json()["blocks_used"] == 2

            messages = http.post.call_args.kwargs["json"]["messages"]
            assert messages[0]["role"] == "system"
            assert "Block A" in messages[0]["content"]
            assert "Block B" in messages[0]["content"]
        finally:
            _cleanup()


def test_an_explicit_block_from_another_scope_is_refused_before_any_model_is_asked():
    """The model is chosen and checked for scope_id; context from elsewhere may not reach it.

    The case CHY-196 closed: a scope whose rule allows a hosted model, and a
    block from a `critical` scope somewhere else in the tree. Before, its
    content went to the hosted model with only the first scope's class checked.
    """
    foreign = _block("Vertraulich aus einem anderen Scope", scope_id=uuid4())
    pool = _make_pool(fetch_return=[_block("Block A"), foreign])
    client = _make_client(pool)

    with patch("core.completion.httpx.AsyncClient") as mock_cls:
        http = _mock_http()
        mock_cls.return_value = http
        try:
            resp = client.post("/v1/complete", json={
                "scope_id": str(_SCOPE_ID),
                "task_type": "summarize",
                "prompt": "Test",
                "context_block_ids": [str(uuid4()), str(foreign["block_id"])],
            })
            assert resp.status_code == 422
            assert "scope chain" in resp.json()["detail"]
            http.post.assert_not_called()
        finally:
            _cleanup()


def test_an_explicit_block_of_a_type_composition_never_serves_is_refused():
    """A routing rule is configuration, not prompt material (ADR-037).

    /v1/compose never serves it; the explicit list may not either, or it would
    be the one path on which a non-composable block reaches a model.
    """
    rule = _block("candidates: [cloud-model]", block_type="routing_rule")
    pool = _make_pool(fetch_return=[rule])
    client = _make_client(pool)

    with patch("core.completion.httpx.AsyncClient") as mock_cls:
        http = _mock_http()
        mock_cls.return_value = http
        try:
            resp = client.post("/v1/complete", json={
                "scope_id": str(_SCOPE_ID),
                "task_type": "summarize",
                "prompt": "Test",
                "context_block_ids": [str(rule["block_id"])],
            })
            assert resp.status_code == 422
            http.post.assert_not_called()
        finally:
            _cleanup()


def test_complete_multiple_blocks_joined_with_separator():
    """Multiple blocks are joined with '---' separator in system message."""
    pool = _make_pool(fetch_return=[
        _block("Kontext 1"), _block("Kontext 2"), _block("Kontext 3"),
    ])
    client = _make_client(pool)

    with patch("core.completion.httpx.AsyncClient") as mock_cls:
        http = _mock_http()
        mock_cls.return_value = http
        try:
            resp = client.post("/v1/complete", json={
                "task_type": "summarize",
                "prompt": "Test",
                "scope_id": str(_SCOPE_ID),
            })
            assert resp.status_code == 200
            assert resp.json()["blocks_used"] == 3

            system_content = http.post.call_args.kwargs["json"]["messages"][0]["content"]
            assert "---" in system_content
            assert "Kontext 1" in system_content
            assert "Kontext 3" in system_content
        finally:
            _cleanup()


# --- the fallback chain (CHY-87) ---

def _mock_http_falling_back(first_status: int = 502):
    """First candidate gets an error from the gateway, second one answers.

    The gateway *answers* — that is the 13.08.2026 shape, and the reason the
    chain fires here at all: a silent gateway would stop the chain instead.
    """
    bad = MagicMock()
    bad.status_code = first_status
    bad.text = "backend unavailable"
    bad.raise_for_status = MagicMock(
        side_effect=httpx.HTTPStatusError("boom", request=MagicMock(), response=bad)
    )

    good = MagicMock()
    good.status_code = 200
    good.json.return_value = _litellm_response("Antwort.", model="phi3-mini")
    good.raise_for_status = MagicMock()

    http = AsyncMock()
    http.__aenter__ = AsyncMock(return_value=http)
    http.__aexit__ = AsyncMock(return_value=False)
    http.post = AsyncMock(side_effect=[bad, good])
    return http


def test_complete_falls_back_to_the_second_candidate():
    """The outage this ticket exists for, driven through the endpoint."""
    client = _make_client()
    with (
        patch("core.completion.httpx.AsyncClient") as mock_cls,
        patch("api.v1.complete.write_audit", new=AsyncMock()),
    ):
        mock_cls.return_value = _mock_http_falling_back()
        resp = client.post("/v1/complete", json={"scope_id": str(_SCOPE_ID), "prompt": "p", "task_type": "summarize"})
    _cleanup()

    assert resp.status_code == 200
    assert resp.json()["model_used"] == "phi3-mini"


def test_a_fallback_is_written_to_the_audit_log():
    """Otherwise the fallback is invisible.

    The answer names the model that replied, and nothing anywhere says it was
    not the one the routing table chose. The routing table records what was
    intended; only this row records what happened.
    """
    client = _make_client()
    audit = AsyncMock()
    with (
        patch("core.completion.httpx.AsyncClient") as mock_cls,
        patch("api.v1.complete.write_audit", new=audit),
    ):
        mock_cls.return_value = _mock_http_falling_back()
        client.post("/v1/complete", json={"scope_id": str(_SCOPE_ID), "prompt": "p", "task_type": "summarize"})
    _cleanup()

    assert audit.await_count == 1, "the fallback left no trace"
    payload = audit.await_args.kwargs["payload"]
    assert payload["chosen"] == "gemma3-4b"
    assert payload["answered"] == "phi3-mini"
    assert [a["outcome"] for a in payload["attempts"]] == ["refused", "answered"]
    assert "502" in payload["attempts"][0]["detail"]


def test_no_audit_entry_when_the_first_candidate_answers():
    """Counter-probe: an entry per completion would drown the log and hide the
    exception it exists to show."""
    client = _make_client()
    audit = AsyncMock()
    with (
        patch("core.completion.httpx.AsyncClient") as mock_cls,
        patch("api.v1.complete.write_audit", new=audit),
    ):
        mock_cls.return_value = _mock_http()
        client.post("/v1/complete", json={"scope_id": str(_SCOPE_ID), "prompt": "p", "task_type": "summarize"})
    _cleanup()

    assert audit.await_count == 0


# --- The answer length (CHY-166) ---

def _complete(body: dict):
    """POST /v1/complete with a recording gateway; returns (response, gateway)."""
    client = _make_client()
    with patch("core.completion.httpx.AsyncClient") as mock_cls:
        gateway = _mock_http()
        mock_cls.return_value = gateway
        try:
            resp = client.post("/v1/complete", json={
                "scope_id": str(_SCOPE_ID), "task_type": "summarize",
                "prompt": "ping", **body,
            })
        finally:
            _cleanup()
    return resp, gateway


def test_without_max_tokens_the_rules_limit_goes_to_the_gateway():
    resp, gateway = _complete({})
    assert resp.status_code == 200, resp.text
    assert gateway.post.call_args.kwargs["json"]["max_tokens"] == 256


def test_a_smaller_max_tokens_goes_to_the_gateway():
    resp, gateway = _complete({"max_tokens": 16})
    assert resp.status_code == 200, resp.text
    assert gateway.post.call_args.kwargs["json"]["max_tokens"] == 16


def test_a_max_tokens_above_the_rule_is_refused_before_any_model_is_asked():
    """422 with a sentence that names the limit, and no gateway call — a
    refused request must not cost a model call first."""
    resp, gateway = _complete({"max_tokens": 1000})
    assert resp.status_code == 422, resp.text
    assert "256" in resp.json()["detail"]
    gateway.post.assert_not_called()


def test_max_tokens_zero_fails_validation():
    resp, gateway = _complete({"max_tokens": 0})
    assert resp.status_code == 422
    assert isinstance(resp.json()["detail"], list)
    gateway.post.assert_not_called()
