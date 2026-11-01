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
    """Base class, so the transport layer can catch the family in one handler."""


class ModelRefused(CompletionError):
    """The gateway answered, and the answer was an error.

    Distinct from `ModelUnreachable` because the two mean different things to
    whoever is on call: this one is a request the gateway understood and
    rejected — a wrong model name, an expired key, a context window exceeded.
    Retrying it unchanged will fail again.
    """

    def __init__(self, status_code: int, body: str) -> None:
        self.status_code = status_code
        super().__init__(f"LiteLLM error {status_code}: {body[:200]}")


class ModelUnreachable(CompletionError):
    """The gateway did not answer at all.

    Worth retrying, and worth waking someone if it persists — unlike
    `ModelRefused`, which is a defect in the request.
    """

    def __init__(self, cause: str) -> None:
        super().__init__(f"LiteLLM unreachable: {cause}")
