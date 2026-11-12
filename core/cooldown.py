"""
core/cooldown.py

A model that answers 429 is skipped for a while instead of being asked again
on every request (CHY-159).

The fallback chain already moves on after a 429 (ADR-035), so nothing fails.
What it did not do was remember: with a hosted model's quota used up, every
request asked it again, waited for the refusal and only then asked the next
candidate. A weekly limit meant days of one refused round trip per call, and
nobody saw it unless they read attempt records.

A 429 says nothing about how long. A short rate limit and an exhausted quota
look the same, and the gateway passes on no header that would tell them apart
(measured 24.09.2026). So the pause grows with each refusal in a row — one
minute, five, fifteen, an hour — which handles both without guessing. A
`Retry-After`, should one ever arrive, wins, up to the same hour. The first
answer from the model forgets the whole streak.

Decided by Thomas on 10.11.2026:
  * Only 429. A 5xx is about the backend behind this one request; a timeout
    is CHY-264's question.
  * The last candidate of a chain is always asked, cooling or not. A chain of
    one behaves as before, and no request fails that would have gone through.
  * Per stage: the state lives in the stage's own Redis database. Each stage
    pays one refused round trip per step and none cools another.

The state is an optimisation, not a dependency, like the composition cache:
a Redis that does not answer means nothing is skipped, and a warning in the
application log.

Called by:
  - core/completion.py  (complete_with_fallback)
  - api/v1/complete.py, api/v1/workflows.py  (build one per request)

Dependencies:
  - redis.asyncio
"""
from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import redis.asyncio as aioredis

_log = logging.getLogger(__name__)

#: The pause after the first, second, third and every further 429 in a row.
STEPS_SECONDS = (60, 300, 900, 3600)
#: No pause is longer, a `Retry-After` included.
MAX_SECONDS = STEPS_SECONDS[-1]
#: How long a streak is remembered after its last refusal. Longer than the
#: longest pause, or the next refusal after it would start again at a minute;
#: short enough that a quota renewed overnight starts from the bottom.
MEMORY_SECONDS = 24 * 3600

_PREFIX = "cc:model_cooldown"


def cooldown_key(model: str) -> str:
    return f"{_PREFIX}:{model}"


@dataclass(frozen=True)
class Cooling:
    """A model's streak: how many refusals in a row, and skipped until when."""

    step: int
    until: float  # seconds since the epoch

    def until_text(self) -> str:
        return datetime.fromtimestamp(self.until, UTC).isoformat(timespec="seconds")


def retry_after_seconds(value: str | None, now: float) -> float | None:
    """Seconds from a `Retry-After` header, either form; None if unusable.

    A date in the past or a value that is neither a number nor a date counts as
    no header at all: the steps decide then, which is what happens without one.
    """
    if not value:
        return None
    try:
        seconds = float(value)
    except ValueError:
        try:
            seconds = parsedate_to_datetime(value).timestamp() - now
        except (TypeError, ValueError):
            return None
    return seconds if seconds > 0 else None


class ModelCooldown:
    """Reads and writes the streaks of one stage, in its Redis database."""

    def __init__(self, redis: aioredis.Redis, *, clock: Callable[[], float] = time.time) -> None:
        self._redis = redis
        self._clock = clock

    async def state(self, model: str) -> Cooling | None:
        """The model's streak, or None. A Redis that fails reads as none."""
        try:
            raw = await self._redis.get(cooldown_key(model))
        except Exception:
            _log.warning("model cooldown not read", extra={"model": model}, exc_info=True)
            return None
        if not raw:
            return None
        try:
            data = json.loads(raw)
            return Cooling(step=int(data["step"]), until=float(data["until"]))
        except (ValueError, KeyError, TypeError):
            # Not ours or damaged: asked rather than skipped on a guess.
            return None

    def cooling(self, state: Cooling | None) -> bool:
        return state is not None and state.until > self._clock()

    async def refused(self, model: str, state: Cooling | None, retry_after: str | None) -> Cooling:
        """Note a 429: one step further, and skipped until the pause is over."""
        now = self._clock()
        step = 0 if state is None else min(state.step + 1, len(STEPS_SECONDS) - 1)
        asked = retry_after_seconds(retry_after, now)
        seconds = min(asked, MAX_SECONDS) if asked is not None else STEPS_SECONDS[step]
        cooling = Cooling(step=step, until=now + seconds)
        try:
            await self._redis.set(
                cooldown_key(model),
                json.dumps({"step": cooling.step, "until": cooling.until}),
                ex=MEMORY_SECONDS,
            )
        except Exception:
            _log.warning("model cooldown not stored", extra={"model": model}, exc_info=True)
        _log.warning("model cooling down", extra={
            "model": model, "step": step + 1, "seconds": round(seconds),
            "until": cooling.until_text(), "retry_after": asked is not None,
        })
        return cooling

    async def answered(self, model: str) -> None:
        """The model answered: the streak is over."""
        try:
            await self._redis.delete(cooldown_key(model))
        except Exception:
            _log.warning("model cooldown not cleared", extra={"model": model}, exc_info=True)
