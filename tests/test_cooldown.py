"""
tests/test_cooldown.py

A model that answers 429 is skipped for a while (CHY-159).

Four layers: the steps and the header on their own, a Redis that fails, the
chain that uses them, and the two callers that hand the chain a cooldown. The
last layer is driven through the real route and the real executor: a cooldown
the callers never pass would leave every test above green and change nothing.
"""
from __future__ import annotations

import json
import logging
from email.utils import format_datetime
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from core.completion import CompletionRequest, CompletionResult, complete_with_fallback
from core.cooldown import (
    MAX_SECONDS,
    MEMORY_SECONDS,
    STEPS_SECONDS,
    Cooling,
    ModelCooldown,
    cooldown_key,
    retry_after_seconds,
)
from core.errors import ModelRefused

NOW = 1_800_000_000.0


class _Redis:
    """The few commands the cooldown and composition use, kept in a dict."""

    def __init__(self) -> None:
        self.data: dict[str, str] = {}
        self.ttl: dict[str, int] = {}

    async def get(self, key):
        return self.data.get(key)

    async def set(self, key, value, ex=None):
        self.data[key] = value
        if ex is not None:
            self.ttl[key] = ex

    async def delete(self, key):
        return 1 if self.data.pop(key, None) is not None else 0

    async def mget(self, keys):
        return [self.data.get(k) for k in keys]

    async def incr(self, key):
        self.data[key] = str(int(self.data.get(key, "0")) + 1)
        return int(self.data[key])


class _Clock:
    def __init__(self, now: float = NOW) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def _cooldown(redis=None, clock=None):
    redis = redis if redis is not None else _Redis()
    clock = clock if clock is not None else _Clock()
    return ModelCooldown(redis, clock=clock), redis, clock


def _cooling(redis: _Redis, model: str, until: float, step: int = 0) -> None:
    redis.data[cooldown_key(model)] = json.dumps({"step": step, "until": until})


# ── the steps ─────────────────────────────────────────────────────────────────

class TestTheSteps:
    async def test_a_first_refusal_pauses_the_model_for_a_minute(self):
        cooldown, redis, _ = _cooldown()
        cooling = await cooldown.refused("m", None, None)
        assert cooling == Cooling(step=0, until=NOW + 60)
        assert json.loads(redis.data[cooldown_key("m")]) == {"step": 0, "until": NOW + 60}

    async def test_each_refusal_in_a_row_pauses_longer_up_to_an_hour(self):
        cooldown, _, _ = _cooldown()
        pauses, state = [], None
        for _ in range(6):
            state = await cooldown.refused("m", state, None)
            pauses.append(state.until - NOW)
        assert pauses == [60, 300, 900, 3600, 3600, 3600]

    async def test_the_streak_outlives_the_longest_pause(self):
        """Otherwise the refusal after an hour's pause would start at a minute
        again, and a used-up weekly quota would be asked every few minutes."""
        cooldown, redis, _ = _cooldown()
        await cooldown.refused("m", None, None)
        assert redis.ttl[cooldown_key("m")] == MEMORY_SECONDS > MAX_SECONDS

    async def test_an_answer_ends_the_streak(self):
        cooldown, redis, _ = _cooldown()
        state = await cooldown.refused("m", await cooldown.refused("m", None, None), None)
        assert state.step == 1
        await cooldown.answered("m")
        assert await cooldown.state("m") is None
        assert (await cooldown.refused("m", None, None)).until == NOW + STEPS_SECONDS[0]

    async def test_a_model_cools_until_its_pause_is_over_and_not_after(self):
        cooldown, _, clock = _cooldown()
        state = await cooldown.refused("m", None, None)
        assert cooldown.cooling(await cooldown.state("m"))
        clock.now = state.until
        assert not cooldown.cooling(await cooldown.state("m"))

    async def test_a_model_without_a_streak_does_not_cool(self):
        cooldown, _, _ = _cooldown()
        assert await cooldown.state("m") is None
        assert not cooldown.cooling(None)


class TestRetryAfter:
    async def test_seconds_from_the_gateway_win_over_the_step(self):
        cooldown, _, _ = _cooldown()
        assert (await cooldown.refused("m", None, "120")).until == NOW + 120

    async def test_no_pause_is_longer_than_an_hour(self):
        cooldown, _, _ = _cooldown()
        assert (await cooldown.refused("m", None, "86400")).until == NOW + MAX_SECONDS

    async def test_the_header_does_not_stop_the_streak_from_growing(self):
        """If the header comes once and then no more, the steps go on from
        where the streak is, not from the bottom."""
        cooldown, _, _ = _cooldown()
        state = await cooldown.refused("m", None, "30")
        assert state.step == 0
        assert (await cooldown.refused("m", state, None)).until == NOW + STEPS_SECONDS[1]

    def test_a_date_counts_from_now(self):
        date = format_datetime(datetime.fromtimestamp(NOW + 600, UTC), usegmt=True)
        assert retry_after_seconds(date, NOW) == 600

    @pytest.mark.parametrize("value", [None, "", "soon", "-5", "0",
                                       "Wed, 21 Oct 2015 07:28:00 GMT"])
    def test_what_cannot_be_used_leaves_it_to_the_steps(self, value):
        assert retry_after_seconds(value, NOW) is None


# ── a Redis that fails ────────────────────────────────────────────────────────

class TestARedisThatFails:
    """The state is an optimisation: without it nothing is skipped."""

    @staticmethod
    def _broken():
        redis = MagicMock()
        redis.get = AsyncMock(side_effect=ConnectionError("redis down"))
        redis.set = AsyncMock(side_effect=ConnectionError("redis down"))
        redis.delete = AsyncMock(side_effect=ConnectionError("redis down"))
        return redis

    async def test_an_unreadable_state_reads_as_none_and_says_so(self, caplog):
        cooldown, _, _ = _cooldown(self._broken())
        with caplog.at_level(logging.WARNING, logger="core.cooldown"):
            assert await cooldown.state("m") is None
        assert [r.message for r in caplog.records] == ["model cooldown not read"]

    async def test_an_unstorable_refusal_is_still_reported(self, caplog):
        cooldown, _, _ = _cooldown(self._broken())
        with caplog.at_level(logging.WARNING, logger="core.cooldown"):
            state = await cooldown.refused("m", None, None)
        assert state.until == NOW + 60
        assert [r.message for r in caplog.records] == [
            "model cooldown not stored", "model cooling down"]

    async def test_an_answer_that_cannot_be_noted_raises_nothing(self, caplog):
        cooldown, _, _ = _cooldown(self._broken())
        with caplog.at_level(logging.WARNING, logger="core.cooldown"):
            await cooldown.answered("m")
        assert [r.message for r in caplog.records] == ["model cooldown not cleared"]

    @pytest.mark.parametrize("raw", ["not json", json.dumps({"step": 1}), json.dumps([1, 2])])
    async def test_a_damaged_state_is_asked_rather_than_skipped(self, raw):
        cooldown, redis, _ = _cooldown()
        redis.data[cooldown_key("m")] = raw
        assert await cooldown.state("m") is None


# ── the chain ─────────────────────────────────────────────────────────────────

class TestTheChain:
    @staticmethod
    def _stub(outcomes: dict):
        called: list[str] = []

        async def fake(request, *, base_url, api_key=""):
            called.append(request.model)
            out = outcomes[request.model]
            if isinstance(out, Exception):
                raise out
            return CompletionResult(content="…", model_used=request.model)

        return fake, called

    async def _run(self, outcomes, candidates, cooldown):
        fake, called = self._stub(outcomes)
        with patch("core.completion.complete", fake):
            result = await complete_with_fallback(
                CompletionRequest(prompt="p", model="x"), candidates,
                base_url="http://gw", cooldown=cooldown,
            )
        return result, called

    async def test_a_cooling_model_is_not_asked_and_the_record_says_so(self):
        cooldown, redis, _ = _cooldown()
        _cooling(redis, "cloud", NOW + 300)
        result, called = await self._run({"cloud": None, "local": "ok"}, ("cloud", "local"), cooldown)
        assert called == ["local"]
        assert [(a.model, a.outcome) for a in result.attempts] == [
            ("cloud", "skipped"), ("local", "answered")]
        assert result.attempts[0].detail == "cooling down until 2027-01-15T08:05:00+00:00"

    async def test_without_a_cooldown_the_cooling_model_is_asked(self):
        """Counter-probe to the test above: the skip comes from the cooldown,
        not from something else in the chain."""
        _, redis, _ = _cooldown()
        _cooling(redis, "cloud", NOW + 300)
        _, called = await self._run({"cloud": "ok", "local": "ok"}, ("cloud", "local"), None)
        assert called == ["cloud"]

    async def test_the_last_candidate_is_asked_even_while_it_cools(self):
        cooldown, redis, _ = _cooldown()
        _cooling(redis, "cloud", NOW + 300)
        _cooling(redis, "local", NOW + 300)
        result, called = await self._run({"cloud": None, "local": "ok"}, ("cloud", "local"), cooldown)
        assert called == ["local"]
        assert result.model_used == "local"

    async def test_a_chain_of_one_behaves_as_before(self):
        cooldown, redis, _ = _cooldown()
        _cooling(redis, "cloud", NOW + 300)
        _, called = await self._run({"cloud": "ok"}, ("cloud",), cooldown)
        assert called == ["cloud"]

    async def test_a_429_cools_the_model_and_the_next_request_skips_it(self):
        """The case the ticket is about, over two requests."""
        cooldown, _, _ = _cooldown()
        outcomes = {"cloud": ModelRefused(429, "quota", retry_after=None), "local": "ok"}
        first, called_first = await self._run(outcomes, ("cloud", "local"), cooldown)
        second, called_second = await self._run(outcomes, ("cloud", "local"), cooldown)
        assert called_first == ["cloud", "local"]
        assert called_second == ["local"]
        assert [a.outcome for a in second.attempts] == ["skipped", "answered"]

    async def test_the_gateways_retry_after_reaches_the_cooldown(self):
        cooldown, _, _ = _cooldown()
        await self._run({"cloud": ModelRefused(429, "quota", retry_after="120"), "local": "ok"},
                        ("cloud", "local"), cooldown)
        assert (await cooldown.state("cloud")).until == NOW + 120

    @pytest.mark.parametrize("status", [500, 502, 503])
    async def test_a_5xx_does_not_cool_the_model(self, status):
        """Only 429 (decided 10.11.2026): a 5xx is about the backend behind one
        request, and the 13.08. outage needs the model asked again when its
        host is back."""
        cooldown, _, _ = _cooldown()
        await self._run({"cloud": ModelRefused(status, "down"), "local": "ok"}, ("cloud", "local"), cooldown)
        assert await cooldown.state("cloud") is None

    async def test_an_answer_after_the_pause_ends_the_streak(self):
        cooldown, redis, clock = _cooldown()
        _cooling(redis, "cloud", NOW - 1, step=3)
        _, called = await self._run({"cloud": "ok", "local": "ok"}, ("cloud", "local"), cooldown)
        assert called == ["cloud"]
        assert await cooldown.state("cloud") is None

    async def test_an_answer_without_a_streak_writes_nothing(self):
        """No delete per completion: the answer clears only what is there."""
        redis = _Redis()
        redis.delete = AsyncMock()
        cooldown, _, _ = _cooldown(redis)
        await self._run({"cloud": "ok"}, ("cloud",), cooldown)
        redis.delete.assert_not_awaited()


# ── the callers ───────────────────────────────────────────────────────────────

class TestTheCallers:
    @pytest.fixture(autouse=True)
    def _nothing_drawn(self, monkeypatch):
        """These tests are about the cooldown; a draw for review (CHY-269) is not."""
        monkeypatch.setattr("api.v1.complete.orchestration.draw_direct_call",
                            AsyncMock(return_value=None))

    @staticmethod
    def _answering(model: str):
        """A gateway that answers, naming the model it was asked for — as
        LiteLLM does: every routing.fell_back on dev names the alias."""
        from tests.test_complete import _litellm_response

        response = MagicMock()
        response.status_code = 200
        response.raise_for_status = MagicMock()
        response.json.return_value = _litellm_response("Antwort.", model=model)
        http = AsyncMock()
        http.__aenter__ = AsyncMock(return_value=http)
        http.__aexit__ = AsyncMock(return_value=False)
        http.post = AsyncMock(return_value=response)
        return http

    def test_complete_skips_a_cooling_model_and_the_audit_names_it(self):
        from tests.test_complete import _SCOPE_ID, _cleanup, _make_client

        redis = _Redis()
        _cooling(redis, "gemma3-4b", 4_000_000_000)
        audit = AsyncMock()
        client = _make_client(redis=redis)
        with (
            patch("core.completion.httpx.AsyncClient") as mock_cls,
            patch("api.v1.complete.write_audit", new=audit),
        ):
            http = self._answering("phi3-mini")
            mock_cls.return_value = http
            resp = client.post("/v1/complete", json={
                "scope_id": str(_SCOPE_ID), "prompt": "p", "task_type": "summarize"})
        _cleanup()

        assert resp.status_code == 200, resp.text
        assert [c.kwargs["json"]["model"] for c in http.post.call_args_list] == ["phi3-mini"]
        payload = audit.await_args.kwargs["payload"]
        assert [(a["model"], a["outcome"]) for a in payload["attempts"]] == [
            ("gemma3-4b", "skipped"), ("phi3-mini", "answered")]

    def test_complete_cools_a_model_that_answers_429(self):
        from tests.test_complete import _SCOPE_ID, _cleanup, _make_client, _mock_http_falling_back

        redis = _Redis()
        client = _make_client(redis=redis)
        with (
            patch("core.completion.httpx.AsyncClient") as mock_cls,
            patch("api.v1.complete.write_audit", new=AsyncMock()),
        ):
            mock_cls.return_value = _mock_http_falling_back(429)
            resp = client.post("/v1/complete", json={
                "scope_id": str(_SCOPE_ID), "prompt": "p", "task_type": "summarize"})
        _cleanup()

        assert resp.status_code == 200, resp.text
        assert json.loads(redis.data[cooldown_key("gemma3-4b")])["step"] == 0

    async def test_the_workflow_executor_skips_a_cooling_model(self, monkeypatch):
        from api.v1 import workflows
        from kernel.redaction import RedactionPolicy
        from router.rules import RoutingRule
        from tests.test_workflow_executors import _ctx

        async def shipped_only(_pool, _scope_id):
            # No database here (pool=None): the shipped rules alone, as in
            # tests/test_workflow_executors.py.
            return RedactionPolicy()

        async def chain(*_a, **_k):
            return RoutingRule("summarize", ("cloud", "local"), uuid4(), uuid4(), 1, max_tokens=256)

        called: list[str] = []

        async def fake(request, *, base_url, api_key=""):
            called.append(request.model)
            return CompletionResult(content="…", model_used=request.model)

        redis = _Redis()
        _cooling(redis, "cloud", 4_000_000_000)
        monkeypatch.setattr(workflows, "route_for", chain)
        monkeypatch.setattr(workflows, "redaction_policy_for", shipped_only)
        audit = AsyncMock()
        monkeypatch.setattr(workflows, "write_audit", audit)
        executor = workflows.get_executors(pool=None, redis=redis).for_type("llm_call", "n")
        with patch("core.completion.complete", fake):
            await executor(_ctx("llm_call", "n", task_type="summarize", scope_id=uuid4(),
                                config={"prompt": "hi"}))
        assert called == ["local"]
        payload = audit.await_args.kwargs["payload"]
        assert [(a["model"], a["outcome"]) for a in payload["attempts"]] == [
            ("cloud", "skipped"), ("local", "answered")]
