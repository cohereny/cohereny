"""
tests/test_tracing.py

Langfuse tracing on the completion path (CHY-29).

Two things are being guarded here, and only one of them is "does the trace
carry the right fields".

The other is that tracing **cannot change behaviour**. An observability feature
that turns a 502 into a 500, or swallows an exception, or fails a request
because a trace store is down, is worse than no observability at all — it adds
a failure mode to the path it was supposed to explain. Several tests below do
nothing but assert that the endpoint behaves identically with tracing broken,
absent, and working.

The DSGVO assertion is deliberate too: CHY-29 asked for the OIDC subject, and
this suite pins the internal actor id instead. A subject identifier written into
Langfuse would survive the erasure endpoint from CHY-34 — the database would be
clean and the trace store would still name the person.
"""
from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from api.config import settings
from core import tracing


@contextmanager
def _fake_client():
    """A Langfuse client that records what it was asked to do."""
    generation = MagicMock()
    span_cm = MagicMock()
    span_cm.__enter__ = MagicMock(return_value=generation)
    span_cm.__exit__ = MagicMock(return_value=False)

    client = MagicMock()
    client.start_as_current_observation = MagicMock(return_value=span_cm)

    attrs_cm = MagicMock()
    attrs_cm.__enter__ = MagicMock(return_value=None)
    attrs_cm.__exit__ = MagicMock(return_value=False)
    propagate = MagicMock(return_value=attrs_cm)

    previous = tracing._client
    tracing._client = client
    try:
        with patch("langfuse.propagate_attributes", propagate):
            yield client, generation, propagate
    finally:
        tracing._client = previous


@contextmanager
def _tracing_off():
    previous = tracing._client
    tracing._client = None
    try:
        yield
    finally:
        tracing._client = previous


# ── the span itself ───────────────────────────────────────────────────────────

def test_span_carries_the_internal_actor_id_not_the_oidc_subject():
    """The point of the whole DSGVO argument in core/tracing.py.

    If this ever flips back to `sub`, an Art. 17 erasure stops being complete:
    core/authz.py anonymises the row, and Langfuse keeps the identifier.
    """
    actor_id = uuid4()
    with _fake_client() as (_client, _gen, propagate), tracing.completion_trace(
        actor_id=actor_id,
        task_type="summarize",
        prompt="p",
        model="gemma3-4b",
        scope_id=None,
        block_versions={},
    ):
        pass

    user_id = propagate.call_args.kwargs["user_id"]
    assert user_id == str(actor_id)
    assert "test-sub" not in user_id


def test_span_records_which_block_version_answered():
    """A block id alone is not provenance. Version six of a block can say the
    opposite of version one, and the audit question is which one was used."""
    versions = {str(uuid4()): 3, str(uuid4()): 1}
    with _fake_client() as (client, _gen, _p), tracing.completion_trace(
        actor_id=uuid4(),
        task_type="summarize",
        prompt="p",
        model="gemma3-4b",
        scope_id=None,
        block_versions=versions,
    ):
        pass

    metadata = client.start_as_current_observation.call_args.kwargs["metadata"]
    assert metadata["context_block_versions"] == versions
    assert metadata["context_blocks_used"] == 2


def test_capture_io_false_keeps_the_prompt_out_of_the_trace():
    with _fake_client() as (client, generation, _p):
        with patch.object(settings, "langfuse_capture_io", False):
            with tracing.completion_trace(
                actor_id=uuid4(),
                task_type="summarize",
                prompt="etwas Vertrauliches",
                model="gemma3-4b",
                scope_id=None,
                block_versions={},
            ) as rec:
                rec.record_output(
                    content="Antwort", model_used="gemma3-4b",
                    input_tokens=1, output_tokens=2,
                )

    sent_input = client.start_as_current_observation.call_args.kwargs["input"]
    assert "prompt" not in sent_input
    assert sent_input["task_type"] == "summarize"
    assert generation.update.call_args.kwargs["output"] is None


def test_usage_is_omitted_rather_than_sent_as_null():
    """LiteLLM does not always return a usage block. Sending {"input": None}
    would show up in Langfuse as a token count of zero, which is a measurement
    rather than a gap."""
    with _fake_client() as (_client, generation, _p), tracing.completion_trace(
        actor_id=uuid4(), task_type="t", prompt="p", model="m",
        scope_id=None, block_versions={},
    ) as rec:
        rec.record_output(
            content="x", model_used="m", input_tokens=None, output_tokens=None
        )
    assert generation.update.call_args.kwargs["usage_details"] is None


# ── tracing may not change behaviour ──────────────────────────────────────────

def test_an_exception_from_the_body_propagates():
    """The bug this catches was real: with the try/except around the yield, an
    HTTPException from the LiteLLM call was swallowed and the generator tried to
    yield twice."""
    with _fake_client(), pytest.raises(ValueError, match="durchreichen"):
        with tracing.completion_trace(
            actor_id=uuid4(), task_type="t", prompt="p", model="m",
            scope_id=None, block_versions={},
        ):
            raise ValueError("durchreichen")


def test_a_broken_span_setup_does_not_break_the_caller():
    previous = tracing._client
    client = MagicMock()
    client.start_as_current_observation = MagicMock(side_effect=RuntimeError("boom"))
    tracing._client = client
    try:
        with patch("langfuse.propagate_attributes", MagicMock()):
            with tracing.completion_trace(
                actor_id=uuid4(), task_type="t", prompt="p", model="m",
                scope_id=None, block_versions={},
            ) as rec:
                rec.record_output(
                    content="x", model_used="m", input_tokens=1, output_tokens=1
                )
                rec.record_error("egal")
    finally:
        tracing._client = previous


def test_recorder_is_inert_when_tracing_is_off():
    with _tracing_off(), tracing.completion_trace(
        actor_id=uuid4(), task_type="t", prompt="p", model="m",
        scope_id=None, block_versions={},
    ) as rec:
        rec.record_output(
            content="x", model_used="m", input_tokens=1, output_tokens=1
        )
        rec.record_error("egal")


# ── startup: the CHY-78 lesson ────────────────────────────────────────────────

def test_a_failing_auth_check_disables_tracing_and_says_why():
    """CHY-78: LANGFUSE_HOST was http instead of https, Traefik answered 404,
    and the SDK stayed quiet — nothing arrived for three weeks. auth_check turns
    that into one line in the startup log."""
    client = MagicMock()
    client.auth_check = MagicMock(return_value=False)
    with patch.object(settings, "langfuse_public_key", "pk"), \
         patch.object(settings, "langfuse_secret_key", "sk"), \
         patch("langfuse.Langfuse", MagicMock(return_value=client)):
        tracing._client = None
        tracing.init_tracing()

    active, reason = tracing.tracing_status()
    assert active is False
    assert "auth_check failed" in reason


def test_a_constructor_that_raises_disables_tracing_instead_of_the_api():
    with patch.object(settings, "langfuse_public_key", "pk"), \
         patch.object(settings, "langfuse_secret_key", "sk"), \
         patch("langfuse.Langfuse", MagicMock(side_effect=RuntimeError("no route"))):
        tracing._client = None
        tracing.init_tracing()

    active, reason = tracing.tracing_status()
    assert active is False
    assert "RuntimeError" in reason


def test_missing_keys_are_not_reported_as_a_fault():
    with patch.object(settings, "langfuse_public_key", ""), \
         patch.object(settings, "langfuse_secret_key", ""):
        tracing._client = None
        tracing.init_tracing()
    active, reason = tracing.tracing_status()
    assert active is False
    assert "LANGFUSE_PUBLIC_KEY" in reason


def test_shutdown_flushes_and_survives_a_failing_client():
    client = MagicMock()
    client.shutdown = MagicMock(side_effect=RuntimeError("gone"))
    tracing._client = client
    tracing.shutdown_tracing()
    assert tracing._client is None
    client.shutdown.assert_called_once()


# ── the endpoint end of it ────────────────────────────────────────────────────

def test_complete_records_an_error_on_a_gateway_failure():
    """A failed completion is the one worth finding later. Without this the
    trace of a 502 looks like a request that never happened."""
    import httpx
    from fastapi.testclient import TestClient

    from api.deps import CurrentUser, require_auth
    from api.main import app
    from core.db import get_pool
    from core.redis import get_redis
    from tests.conftest import as_caller, make_pool, make_redis

    # Two queries now stand between the request and the model: the scope chain
    # and the routing rule (CHY-87 step 2b). Answered by statement, because one
    # fixed list would hand the routing path a scope row.
    scope = uuid4()
    chain = [{"id": scope, "scope_path": "org", "node_type": "org",
              "name": "org", "depth": 1, "privacy_level": None}]
    rule_rows = [{"block_id": uuid4(), "version": 1, "scope_id": scope,
              "content": "für den Test",
              "metadata": {"task_type": "summarize",
                           "candidates": ["gemma3-4b"], "max_tokens": 256}}]

    async def _fetch(sql, *args):
        if "scope_nodes" in sql:
            return chain
        if "block_type = 'routing_rule'" in sql:
            return rule_rows
        return []

    pool = make_pool()
    pool.fetch = AsyncMock(side_effect=_fetch)
    app.dependency_overrides[require_auth] = as_caller(CurrentUser(
        sub="test-sub", username="testuser"
    ))
    app.dependency_overrides[get_pool] = lambda: pool
    app.dependency_overrides[get_redis] = lambda: make_redis()

    try:
        with _fake_client() as (_client, generation, _p):
            http = MagicMock()
            http.__aenter__ = AsyncMock(return_value=http)
            http.__aexit__ = AsyncMock(return_value=False)
            response = MagicMock(status_code=500, text="upstream down")
            http.post = AsyncMock(
                side_effect=httpx.HTTPStatusError(
                    "boom", request=MagicMock(), response=response
                )
            )
            with patch("core.completion.httpx.AsyncClient", return_value=http):
                resp = TestClient(app).post(
                    "/v1/complete",
                    json={"task_type": "summarize", "prompt": "p",
                          "scope_id": str(scope)},
                )

        assert resp.status_code == 502
        assert generation.update.call_args.kwargs["level"] == "ERROR"
    finally:
        app.dependency_overrides.clear()
