"""
tests/test_completion.py

Calling a model, from anywhere (CHY-129).

`api/v1/complete.py` held the whole path — the model call, the tracing and the
error handling, with `httpx` in the route handler. A model could only be called
over HTTP: not from the workflow runner, not from the CLI, not from a job.

`tests/test_complete.py` still drives the endpoint and is the right place for
what the endpoint promises. This file covers what the capability promises to
its *other* callers, which is the half that did not exist before.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import httpx
import pytest

from core.completion import (
    CompletionRequest,
    CompletionResult,
    build_body,
    build_messages,
    complete,
    complete_with_fallback,
)
from core.errors import ModelRefused, ModelUnreachable

pytestmark = pytest.mark.anyio


def _http(payload: dict) -> MagicMock:
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json = MagicMock(return_value=payload)
    client = MagicMock()
    client.post = AsyncMock(return_value=response)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client


_ANSWER = {
    "id": "trace-1",
    "model": "gemma4-e4b",
    "choices": [{"message": {"content": "Eine Antwort."}}],
    "usage": {"prompt_tokens": 11, "completion_tokens": 4},
}


# ── the message shape ─────────────────────────────────────────────────────────

class TestMessages:
    def test_a_system_message_comes_first(self):
        messages = build_messages(
            CompletionRequest(prompt="Frage", model="m", system="Kontext")
        )
        assert [m["role"] for m in messages] == ["system", "user"]
        assert messages[0]["content"] == "Kontext"

    def test_an_empty_system_message_is_omitted_not_sent_empty(self):
        """Some gateways read an empty system message as a deliberate
        instruction to say nothing. Omitting it and sending "" are different
        requests, and only one of them is what "no context" means."""
        messages = build_messages(CompletionRequest(prompt="Frage", model="m"))
        assert [m["role"] for m in messages] == ["user"]


class TestBody:
    """Whether the length limit goes out (CHY-166)."""

    def test_a_limit_is_sent_as_max_tokens(self):
        body = build_body(CompletionRequest(prompt="Frage", model="m", max_tokens=16))
        assert body["max_tokens"] == 16
        assert body["model"] == "m" and body["stream"] is False

    def test_no_limit_sends_no_key_rather_than_null(self):
        """`"max_tokens": null` is not "no limit" to every gateway."""
        assert "max_tokens" not in build_body(CompletionRequest(prompt="Frage", model="m"))


# ── what comes back ───────────────────────────────────────────────────────────

async def test_a_completion_returns_content_and_usage():
    with patch("core.completion.httpx.AsyncClient", return_value=_http(_ANSWER)):
        result = await complete(
            CompletionRequest(prompt="Frage", model="gemma4-e4b", actor_id=uuid4()),
            base_url="https://litellm.example.com/v1",
        )
    assert result.content == "Eine Antwort."
    assert result.model_used == "gemma4-e4b"
    assert result.input_tokens == 11
    assert result.trace_id == "trace-1"


async def test_the_model_the_gateway_actually_used_wins():
    """A gateway may answer with a different model than was asked for — a
    fallback, an alias. The record has to say which one answered, not which one
    was requested, or the transparency record is a wish."""
    answer = {**_ANSWER, "model": "phi3-mini"}
    with patch("core.completion.httpx.AsyncClient", return_value=_http(answer)):
        result = await complete(
            CompletionRequest(prompt="Frage", model="gemma4-e4b"), base_url="https://x/v1"
        )
    assert result.model_used == "phi3-mini"


async def test_no_api_key_means_no_authorization_header():
    """Local development runs LiteLLM without auth. An empty Bearer token is
    not the same as no token — some gateways reject it outright."""
    client = _http(_ANSWER)
    with patch("core.completion.httpx.AsyncClient", return_value=client):
        await complete(CompletionRequest(prompt="F", model="m"), base_url="https://x/v1")
    assert client.post.await_args.kwargs["headers"] == {}


async def test_an_api_key_becomes_a_bearer_header():
    client = _http(_ANSWER)
    with patch("core.completion.httpx.AsyncClient", return_value=client):
        await complete(
            CompletionRequest(prompt="F", model="m"), base_url="https://x/v1", api_key="k"
        )
    assert client.post.await_args.kwargs["headers"] == {"Authorization": "Bearer k"}


# ── the two failures, kept apart ──────────────────────────────────────────────

class TestFailures:
    async def test_a_refused_request_is_not_an_unreachable_gateway(self):
        """502 and 503 mean different things to whoever is on call.

        A gateway that answered and rejected is a defect in the request —
        retrying it unchanged fails again. A gateway that did not answer is
        worth retrying and worth waking someone if it persists. One error class
        for both would make a client bug and an outage look identical.
        """
        response = MagicMock(status_code=400, text="unknown model")
        client = _http(_ANSWER)
        client.post = AsyncMock(
            side_effect=httpx.HTTPStatusError("e", request=MagicMock(), response=response)
        )
        with (
            patch("core.completion.httpx.AsyncClient", return_value=client),
            pytest.raises(ModelRefused) as refused,
        ):
            await complete(CompletionRequest(prompt="F", model="nope"), base_url="https://x/v1")
        assert "unknown model" in str(refused.value)

    async def test_an_unreachable_gateway_raises_its_own_error(self):
        client = _http(_ANSWER)
        client.post = AsyncMock(side_effect=httpx.ConnectError("refused"))
        with (
            patch("core.completion.httpx.AsyncClient", return_value=client),
            pytest.raises(ModelUnreachable),
        ):
            await complete(CompletionRequest(prompt="F", model="m"), base_url="https://x/v1")

    @pytest.mark.parametrize(
        ("failure", "expected"),
        [
            (httpx.ConnectError("refused"), ModelUnreachable),
            (
                httpx.HTTPStatusError(
                    "e", request=MagicMock(),
                    response=MagicMock(status_code=400, text="unknown model"),
                ),
                ModelRefused,
            ),
        ],
        ids=["unreachable", "refused"],
    )
    async def test_a_failure_is_traced_before_it_is_raised(self, failure, expected):
        """A failed completion is the one worth finding later.

        An unrecorded failure looks like a request that never happened, which
        is the worst possible reading of an outage.

        Both branches, and not because symmetry is tidy: the first version of
        this test only triggered a connection error, and a probe that removed
        the recording from the *other* branch left it green. One of the two
        paths was carrying a claim nothing checked.
        """
        recorder = MagicMock()
        client = _http(_ANSWER)
        client.post = AsyncMock(side_effect=failure)
        with (
            patch("core.completion.httpx.AsyncClient", return_value=client),
            patch("core.completion.completion_trace") as trace,
        ):
            trace.return_value.__enter__ = MagicMock(return_value=recorder)
            trace.return_value.__exit__ = MagicMock(return_value=False)
            with pytest.raises(expected):
                await complete(CompletionRequest(prompt="F", model="m"), base_url="https://x/v1")
        recorder.record_error.assert_called_once()


# ── reachable without a request ───────────────────────────────────────────────

async def test_a_caller_with_no_actor_can_still_complete():
    """The point of the whole extraction.

    A scheduled job has no OIDC subject and no actor row, and before CHY-129
    that meant it could not call a model at all — the only path went through a
    route handler that resolves one. The trace records the absence rather than
    inventing an identity.
    """
    with patch("core.completion.httpx.AsyncClient", return_value=_http(_ANSWER)):
        result = await complete(
            CompletionRequest(prompt="Frage", model="m", actor_id=None),
            base_url="https://x/v1",
        )
    assert result.content == "Eine Antwort."


def test_the_capability_does_not_read_the_api_settings():
    """`base_url` and `api_key` are parameters, not settings lookups.

    `core/` already imports `api.config` three times, and the boundary guard
    counts all three. Adding a fourth would also tie the runner's model calls
    to the API's configuration object — the wrong thing for a scheduled job to
    depend on.
    """
    import ast
    from pathlib import Path

    src = Path("core/completion.py").read_text(encoding="utf-8")
    imported = {
        node.module for node in ast.walk(ast.parse(src))
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert not any(m.startswith("api") for m in imported), sorted(imported)


# ── the fallback chain (CHY-87) ───────────────────────────────────────────────

class TestTheFallbackChain:
    """Which candidate answered, and why not the first.

    The rule runs the other way round from the obvious reading, and the outage
    this exists for is the proof. Every candidate goes to the same gateway:

      * gateway silent  -- asking it again for another model asks the same
        silent gateway. The chain cannot help.
      * gateway answers 5xx -- it is up and reports that the backend for THIS
        model failed. That is 13.08.2026, and exactly what a chain is for.
    """

    @staticmethod
    def _stub(outcomes: dict):
        """Replaces `complete`; records the order and raises what is asked for."""
        called: list[str] = []

        async def fake(request, *, base_url, api_key=""):
            called.append(request.model)
            out = outcomes[request.model]
            if isinstance(out, Exception):
                raise out
            return out

        return fake, called

    @staticmethod
    def _answer(model: str) -> CompletionResult:
        return CompletionResult(content="…", model_used=model)

    async def _run(self, outcomes, candidates):
        fake, called = self._stub(outcomes)
        with patch("core.completion.complete", fake):
            result = await complete_with_fallback(
                CompletionRequest(prompt="p", model="ignoriert"),
                candidates, base_url="http://gw",
            )
        return result, called

    async def test_the_first_candidate_answering_asks_nobody_else(self):
        result, called = await self._run({"a": self._answer("a")}, ("a", "b"))
        assert called == ["a"]
        assert result.model_used == "a"
        assert [(x.model, x.outcome) for x in result.attempts] == [("a", "answered")]

    async def test_a_5xx_moves_on_and_the_record_says_why(self):
        """The 13.08. case: LiteLLM up, the model's host off, 502 for everything."""
        result, called = await self._run(
            {"a": ModelRefused(502, "backend down"), "b": self._answer("b")},
            ("a", "b"),
        )
        assert called == ["a", "b"]
        assert result.model_used == "b"
        assert [(x.model, x.outcome) for x in result.attempts] == [
            ("a", "refused"), ("b", "answered"),
        ]
        assert "502" in result.attempts[0].detail

    async def test_a_rate_limit_moves_on(self):
        result, called = await self._run(
            {"a": ModelRefused(429, "slow down"), "b": self._answer("b")}, ("a", "b")
        )
        assert called == ["a", "b"]

    async def test_a_4xx_stops_instead_of_hiding_a_typo(self):
        """An unknown model or a bad key is not fixed by another candidate, and
        moving on would let a wrong entry sit behind a working second one."""
        with pytest.raises(ModelRefused):
            await self._run(
                {"a": ModelRefused(404, "no such model"), "b": self._answer("b")},
                ("a", "b"),
            )

    async def test_a_4xx_really_does_not_ask_the_second(self):
        """Counter-probe to the test above: raising is not the same as stopping."""
        fake, called = self._stub(
            {"a": ModelRefused(401, "bad key"), "b": self._answer("b")}
        )
        with patch("core.completion.complete", fake), pytest.raises(ModelRefused):
            await complete_with_fallback(
                CompletionRequest(prompt="p", model="x"), ("a", "b"),
                base_url="http://gw",
            )
        assert called == ["a"], "the chain asked a second model about a bad key"

    async def test_a_silent_gateway_stops_because_all_candidates_share_it(self):
        fake, called = self._stub(
            {"a": ModelUnreachable("timeout"), "b": self._answer("b")}
        )
        with patch("core.completion.complete", fake), pytest.raises(ModelUnreachable):
            await complete_with_fallback(
                CompletionRequest(prompt="p", model="x"), ("a", "b"),
                base_url="http://gw",
            )
        assert called == ["a"], "the chain waited on a gateway that had gone quiet"

    async def test_when_nothing_answers_the_last_error_is_raised(self):
        """The first would name the model the author chose, which reads like the
        chain was never tried."""
        fake, _ = self._stub(
            {"a": ModelRefused(502, "erste"), "b": ModelRefused(503, "letzte")}
        )
        with patch("core.completion.complete", fake), pytest.raises(ModelRefused) as e:
            await complete_with_fallback(
                CompletionRequest(prompt="p", model="x"), ("a", "b"),
                base_url="http://gw",
            )
        assert e.value.status_code == 503

    async def test_an_empty_chain_is_a_programming_error_not_a_default(self):
        with pytest.raises(ValueError, match="at least one"):
            await complete_with_fallback(
                CompletionRequest(prompt="p", model="x"), (), base_url="http://gw"
            )
