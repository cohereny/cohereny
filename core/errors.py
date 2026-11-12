"""
core/errors.py

What can go wrong in the outbound adapters, expressed without HTTP (CHY-129).

An errors module rather than an `Error` suffix on each class. That is the shape
`kernel/errors.py` and every `contexts/*/errors.py` already have, and N818 is
waived per file for exactly that reason — a convention that holds everywhere
except one package is not a convention.

The status codes are chosen once, in api/main.py. Nothing here knows them.
"""
from __future__ import annotations


class CompletionError(Exception):
    """Base class, so the transport layer can catch the family in one handler.

    Two texts, for two audiences (CHY-97). `str()` is what may leave the
    installation: what went wrong, and nothing about where. `detail` is the
    gateway's own account — its status, its body, the address it could not
    reach — and belongs in the application log and the trace, never in a
    response, a stored run or the audit log.

    Until CHY-97 there was only one, and it was the second: a 502 of 14.08.2026
    told an arbitrary caller the private address and port of an inference host.
    """

    detail: str


class ModelRefused(CompletionError):
    """The gateway answered, and the answer was an error.

    Distinct from `ModelUnreachable` because the two mean different things to
    whoever is on call: this one is a request the gateway understood and
    rejected — a wrong model name, an expired key, a context window exceeded.
    Retrying it unchanged will fail again.
    """

    def __init__(self, status_code: int, body: str, *, retry_after: str | None = None) -> None:
        self.status_code = status_code
        self.detail = f"LiteLLM error {status_code}: {body[:200]}"
        # The header as sent, for the cooldown after a 429 (CHY-159). Kept raw:
        # it is a number of seconds or a date, and reading it needs a clock.
        self.retry_after = retry_after
        # The status stays: it tells the caller whether to wait (429) or to
        # change the request (400), and it says nothing about the inside.
        super().__init__(f"the model gateway refused the request (status {status_code})")


class ModelUnreachable(CompletionError):
    """The gateway did not answer at all.

    Worth retrying, and worth waking someone if it persists — unlike
    `ModelRefused`, which is a defect in the request.
    """

    def __init__(self, cause: str) -> None:
        self.detail = f"LiteLLM unreachable: {cause}"
        super().__init__("the model gateway is unreachable")
