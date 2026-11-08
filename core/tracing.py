"""
core/tracing.py

Langfuse tracing for the completion path (CHY-29).

Every /v1/complete call becomes one trace carrying the governance metadata that
makes an answer explainable afterwards: which context blocks went in, at which
version, which model answered, and who asked. Without it the repository can say
what a block *said* but not what it *did* — and "which block caused this answer"
is the question an audit actually asks.

Three decisions worth stating, because each departs from the ticket or from the
obvious route:

**The actor is the internal UUID, not the OIDC subject.** CHY-29 asked for
`current_user.sub` on the grounds that a subject identifier is not a clear name.
True, but it is still an identifier for a person, and it would live in Langfuse
where the erasure endpoint from CHY-34 cannot reach it — a DSGVO Art. 17 request
would clear the database and leave the traces intact. The internal `actors.id`
is the same identifier the audit log already uses; after anonymisation it
resolves to a row that no longer names anybody, so the link breaks at the
source instead of having to be chased across systems.

**Failures are loud at startup and silent afterwards.** The Langfuse SDK
swallows transport errors by design. That is right for a request path — an
unreachable trace store must never cost a completion — and it is exactly how
CHY-78 happened: a single wrong character in the host, and nothing arrived for
three weeks with no error anywhere. So `auth_check()` runs once at startup and
logs the outcome plainly. A misconfiguration is then visible in the first
seconds of the container log rather than in a dashboard nobody thinks to open.

**Prompt and response are captured, and that is a choice.** Traces without
input and output would show that something happened, not what. Langfuse is
self-hosted on the internal network, so the content does not leave the estate —
but `LANGFUSE_CAPTURE_IO=false` turns it off for deployments where even that is
too much. Since CHY-63 what arrives here is already redacted: `core.completion`
replaces identifiers before the trace starts and records the answer with its
placeholders, so the trace holds `{{EMAIL_1}}`, never the address. The metadata
counts the replacements per category, never the values.

Called by:
  - api/main.py       (lifespan: init and shutdown)
  - core/completion.py (every model call, since CHY-129)

Dependencies:
  - langfuse (SDK v4 — the v2 `langfuse.trace()` API in the ticket no longer exists)
"""
from __future__ import annotations

import logging
from collections.abc import Mapping
from contextlib import ExitStack, contextmanager
from typing import Any, Iterator
from uuid import UUID

from api.config import settings

logger = logging.getLogger(__name__)

_client: Any | None = None
_reason_disabled: str = "not initialised"


def init_tracing() -> None:
    """Build the client and prove it can talk to Langfuse. Never raises.

    Tracing is an observability feature; a broken trace store may not stop the
    API from serving. Every failure path here ends in `_client = None`, which
    makes `completion_trace()` a no-op.
    """
    global _client, _reason_disabled

    if not settings.langfuse_enabled:
        _reason_disabled = "LANGFUSE_ENABLED=false"
        logger.info("Langfuse tracing disabled (%s)", _reason_disabled)
        return

    if not (settings.langfuse_public_key and settings.langfuse_secret_key):
        # Deliberately info, not warning: running without keys is the normal
        # state for a local checkout and should not look like a fault.
        _reason_disabled = "no LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY"
        logger.info("Langfuse tracing disabled (%s)", _reason_disabled)
        return

    try:
        from langfuse import Langfuse

        client = Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            host=settings.langfuse_host,
            environment=settings.langfuse_environment,
            release=settings.api_version,
        )
        # The one call that actually reaches the server. Without it a wrong host
        # or a rotated key stays invisible until someone misses the traces.
        if not client.auth_check():
            _reason_disabled = f"auth_check failed against {settings.langfuse_host}"
            logger.error(
                "Langfuse tracing disabled: %s — check LANGFUSE_HOST and the key pair "
                "(CHY-78: a plain-http host answers 404 and the SDK stays quiet)",
                _reason_disabled,
            )
            _client = None
            return
    except Exception as exc:  # noqa: BLE001 — see the docstring
        _reason_disabled = f"{type(exc).__name__}: {exc}"
        logger.error("Langfuse tracing disabled: %s", _reason_disabled)
        _client = None
        return

    _client = client
    _reason_disabled = ""
    logger.info(
        "Langfuse tracing active — host=%s environment=%s capture_io=%s",
        settings.langfuse_host,
        settings.langfuse_environment,
        settings.langfuse_capture_io,
    )


def shutdown_tracing() -> None:
    """Flush what is still queued. The SDK batches, so without this the traces
    of the last few seconds before a deploy are lost — which is when they tend
    to be interesting."""
    global _client
    if _client is not None:
        try:
            _client.shutdown()
        except Exception:  # noqa: BLE001
            logger.warning("Langfuse shutdown failed", exc_info=True)
        _client = None


def tracing_status() -> tuple[bool, str]:
    """(active, reason). Used by tests and worth exposing if a status endpoint
    ever wants it — the CHY-78 failure mode was precisely that nobody could see
    this from outside."""
    return _client is not None, _reason_disabled


class _Recorder:
    """Handle passed to the caller. Does nothing when tracing is off, so the
    endpoint has one code path instead of an `if` around its whole body."""

    __slots__ = ("_generation",)

    def __init__(self, generation: Any | None) -> None:
        self._generation = generation

    def record_output(
        self,
        *,
        content: str,
        model_used: str,
        input_tokens: int | None,
        output_tokens: int | None,
    ) -> None:
        if self._generation is None:
            return
        usage = {}
        if input_tokens is not None:
            usage["input"] = input_tokens
        if output_tokens is not None:
            usage["output"] = output_tokens
        try:
            self._generation.update(
                output=content if settings.langfuse_capture_io else None,
                model=model_used,
                usage_details=usage or None,
            )
        except Exception:  # noqa: BLE001
            logger.debug("Langfuse update failed", exc_info=True)

    def record_error(self, message: str) -> None:
        if self._generation is None:
            return
        try:
            self._generation.update(level="ERROR", status_message=message[:500])
        except Exception:  # noqa: BLE001
            logger.debug("Langfuse error update failed", exc_info=True)


@contextmanager
def completion_trace(
    *,
    actor_id: UUID | None,
    task_type: str,
    prompt: str,
    model: str,
    scope_id: UUID | None,
    block_versions: dict[str, int],
    redactions: Mapping[str, int] | None = None,
) -> Iterator[_Recorder]:
    """One trace per completion.

    `block_versions` rather than a bare list of ids: knowing that block X was
    used says little when X has six versions and only one of them contained the
    sentence in question. It is the same map ComposedContext already carries, so
    the trace and the cache agree on what was composed.

    `redactions` counts what was replaced before the call (CHY-63). Without it a
    trace full of placeholders cannot be told apart from a prompt that happened
    to contain braces.
    """
    if _client is None:
        yield _Recorder(None)
        return

    metadata = {
        "context_block_versions": block_versions,
        "context_blocks_used": len(block_versions),
        "scope_id": str(scope_id) if scope_id else None,
        "task_type": task_type,
        "routed_model": model,
        "redactions": dict(redactions or {}),
    }

    # ExitStack rather than nested `with`: the try/except must cover only the
    # *setup*. Wrapping the caller's body would swallow its exceptions — an
    # HTTPException from the LiteLLM call would vanish and the generator would
    # try to yield a second time. Tracing may cost nothing, and it may not
    # change behaviour either.
    stack = ExitStack()
    generation: Any | None = None
    try:
        from langfuse import propagate_attributes

        stack.enter_context(
            propagate_attributes(
                # None where no actor could be resolved — a scheduled run,
                # for one (CHY-129). "unknown" and an omitted field read the
                # same in Langfuse; writing the word says the absence was
                # noticed rather than lost.
                user_id=str(actor_id) if actor_id else "unknown",
                metadata={"scope_id": metadata["scope_id"], "task_type": task_type},
                tags=["complete"],
            )
        )
        generation = stack.enter_context(
            _client.start_as_current_observation(
                name="complete",
                as_type="generation",
                model=model,
                input={"task_type": task_type, "prompt": prompt}
                if settings.langfuse_capture_io
                else {"task_type": task_type},
                metadata=metadata,
            )
        )
    except Exception:  # noqa: BLE001 — tracing must never cost a completion
        logger.warning("Langfuse span could not be started", exc_info=True)
        stack.close()
        generation = None

    try:
        yield _Recorder(generation)
    finally:
        stack.close()
