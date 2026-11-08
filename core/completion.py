"""
core/completion.py

Calling a model, reachable from anywhere (CHY-129).

Until now the whole completion path lived in `api/v1/complete.py` — the model
call, the tracing and the error handling, with `httpx` inside the route
handler. **A model could only be called over HTTP.** Not from the workflow
runner, not from the CLI, not from a scheduled job.

That is the same defect ADR-030 removed from the repository context, where the
approval lifecycle was reachable only over HTTP. Here it was the central
capability of the product.

Why `core/` and not a context
------------------------------
Contexts do not import one another (ADR-030): `orchestration` could not call an
`inference` context any more than it can call `repository`. A capability that
several contexts need and none owns belongs on the same shelf as the database
pool and the Redis client — outbound adapters, used by whoever needs them,
owning no tables.

What this deliberately does **not** do
---------------------------------------
It does not compose context, resolve a task type to a model, or decide which
blocks belong in a prompt. Those are decisions with owners: composition belongs
to the repository context, model choice to the router or to a workflow's
`model_map` (CHY-42). Pulling them in here would make this the second place
that knows how a prompt is built, and the first thing that goes wrong with two
places is that they stop agreeing.

It takes a finished system message and a model id, and calls the model.

What it does on the way: identifiers out, values back (CHY-63)
---------------------------------------------------------------
This is the one place that calls a model, which makes it the one place that can
replace identifiers before they leave -- and the place nobody can forget to
call. Every request is redacted with `kernel.redaction` before the body is
built and before the trace starts; the answer is checked the same way and
recorded with placeholders (CHY-244), and only then are the values put back for
the caller. For `/v1/complete` the mapping exists for the length of the call and
is never stored (ADR-018, decided 05.11.2026). A workflow run hands in its own
mapping and gets the answer back with placeholders and the mapping extended; the
run seals it (CHY-234).

Dependencies:
  - httpx · core.tracing · kernel.redaction
"""
from __future__ import annotations

import time
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from uuid import UUID

import httpx

from core.errors import CompletionError, ModelRefused, ModelUnreachable
from core.tracing import completion_trace
from kernel.redaction import SHIPPED_RULES, Rule, redact, unresolved_in

#: Long enough for a slow local model on a Raspberry Pi, which is the actual
#: constraint here — the Pi answers a 12 000-character prompt in minutes, not
#: seconds. Shorter would turn a working setup into an intermittent one.
REQUEST_TIMEOUT_SECONDS = 300.0


@dataclass(frozen=True)
class CompletionRequest:
    """What to ask, of which model, and what to record about it.

    `system` arrives finished. Building it is composition, and composition
    belongs to the repository context — see the module docstring.

    The four trace fields are not decoration. `block_versions` is what makes an
    answer reproducible six months later (EU AI Act Art. 12): "the run used
    block X" says little when X has six versions and only one of them contained
    the sentence that mattered.
    """

    prompt: str
    model: str
    system: str = ""
    task_type: str = ""
    actor_id: UUID | None = None
    scope_id: UUID | None = None
    block_versions: dict[str, int] = field(default_factory=dict)
    #: The longest answer, in tokens (CHY-166). None sends no limit, which
    #: leaves it to the model — the state before CHY-166 and the reason a
    #: one-word prompt could take five minutes. The API always sets it from
    #: the routing rule; None remains for a direct call that has no rule.
    max_tokens: int | None = None
    #: Values the caller knows identify someone, by category (CHY-63):
    #: `{"PERSON": ["Erika Mustermann"]}`. Checked by the caller with
    #: `kernel.redaction.identifiers_from`; replaced before the call, put back
    #: in the answer.
    identifiers_to_redact: Mapping[str, Sequence[str]] = field(default_factory=dict)
    #: How identifiers are found. The shipped rules unless a caller passes
    #: others. Never empty by default: a caller that forgets this field gets
    #: more protection, not less.
    redaction_rules: tuple[Rule, ...] = SHIPPED_RULES
    #: The mapping of the run this call works for (CHY-234), or None outside a
    #: run. With it, a value the run already knows keeps its placeholder, a new
    #: one is numbered after it, and the answer comes back *with* placeholders
    #: -- checked by the same rules first -- because the run stores it. None is
    #: the `/v1/complete` path: the values are put back, as since CHY-63.
    run_mapping: Mapping[str, str] | None = None


@dataclass(frozen=True)
class CompletionResult:
    content: str
    model_used: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    latency_ms: float = 0.0
    trace_id: str | None = None
    #: Every candidate that was asked, in order, including the one that
    #: answered. Empty for a direct `complete()` — there was no chain.
    attempts: tuple[Attempt, ...] = ()
    #: Identifiers replaced before the call, per category (CHY-63). Counts,
    #: never values.
    redactions: Mapping[str, int] = field(default_factory=dict)
    #: Placeholders in the answer that could not be put back, left as they
    #: are: a name the call never handed out, or one the model changed.
    unresolved_placeholders: int = 0
    #: The run's mapping after this call, old and new: what the runner seals
    #: back into `pii_mappings` (CHY-234). None outside a run.
    run_mapping: Mapping[str, str] | None = None


def build_messages(request: CompletionRequest) -> list[dict[str, str]]:
    """System message first, then the user prompt.

    A separate function because it is the one part of this module worth
    asserting on without a network: whether an empty system message is omitted
    rather than sent as an empty string, which some gateways treat as a
    deliberate instruction to say nothing.
    """
    messages: list[dict[str, str]] = []
    if request.system:
        messages.append({"role": "system", "content": request.system})
    messages.append({"role": "user", "content": request.prompt})
    return messages


def build_body(request: CompletionRequest) -> dict:
    """The request body for the gateway's /chat/completions.

    Split out for the same reason as `build_messages`: whether `max_tokens`
    goes out is the part worth asserting without a network (CHY-166). LiteLLM
    passes it on — to Ollama as `num_predict`.
    """
    body: dict = {
        "model": request.model,
        "messages": build_messages(request),
        "stream": False,
    }
    if request.max_tokens is not None:
        body["max_tokens"] = request.max_tokens
    return body


async def complete(
    request: CompletionRequest, *, base_url: str, api_key: str = ""
) -> CompletionResult:
    """Call the model, trace it, and return what came back.

    `base_url` and `api_key` are parameters and not read from settings. A module
    below the transport that reached into `api.config` would be the boundary
    violation the guard already counts three times in this package — and it
    would tie the runner's model calls to the API's configuration object, which
    is the wrong thing for a scheduled job to depend on.

    The trace spans the model call and nothing else. A cache hit and a cache
    miss have to produce the same trace shape, or a latency comparison across
    runs measures the cache instead of the model (CHY-29).

    The request is redacted first, so the body, the trace and any error text
    the gateway echoes back carry placeholders only (CHY-63). The answer is
    redacted too, with the same numbering, before the trace records it: a
    model can write an address or an account number that no request contained
    (CHY-244).

    Outside a run the caller then gets the answer as the model gave it, with
    the values put back. In a run (`request.run_mapping` set, CHY-234) it is
    not restored: the run stores the redacted answer, numbered on from the
    run's mapping (decided 06.11.2026, E3).
    """
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    redaction = redact(
        {"system": request.system, "prompt": request.prompt},
        identifiers=request.identifiers_to_redact,
        rules=request.redaction_rules,
        known=request.run_mapping,
    )
    outgoing = replace(
        request, system=redaction.texts["system"], prompt=redaction.texts["prompt"]
    )
    body = build_body(outgoing)

    with completion_trace(
        actor_id=request.actor_id,
        task_type=request.task_type,
        prompt=outgoing.prompt,
        model=request.model,
        scope_id=request.scope_id,
        block_versions=request.block_versions,
        redactions=redaction.counts,
    ) as trace:
        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as http:
                response = await http.post(
                    f"{base_url}/chat/completions",
                    headers=headers,
                    json=body,
                )
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            error = ModelRefused(exc.response.status_code, exc.response.text)
            # Recorded before raising: a failed completion is the one worth
            # finding later, and an unrecorded failure looks like a request that
            # never happened.
            trace.record_error(str(error))
            raise error from exc
        except httpx.RequestError as exc:
            error = ModelUnreachable(str(exc))
            trace.record_error(str(error))
            raise error from exc

        latency_ms = round((time.perf_counter() - started) * 1000, 2)
        payload = response.json()
        choice = (payload.get("choices") or [{}])[0]
        content = (choice.get("message") or {}).get("content", "")
        usage = payload.get("usage") or {}
        model_used = payload.get("model", request.model)

        # The answer is checked before anyone records it, in and outside a run:
        # what the model wrote on its own would otherwise reach the trace as
        # written (CHY-244).
        answer = redact(
            {"content": content},
            identifiers=request.identifiers_to_redact,
            rules=request.redaction_rules,
            known=redaction.replacements,
        )

        trace.record_output(
            content=answer.texts["content"],
            model_used=model_used,
            input_tokens=usage.get("prompt_tokens"),
            output_tokens=usage.get("completion_tokens"),
        )

    if request.run_mapping is not None:
        content = answer.texts["content"]
        return CompletionResult(
            content=content,
            model_used=model_used,
            input_tokens=usage.get("prompt_tokens"),
            output_tokens=usage.get("completion_tokens"),
            latency_ms=latency_ms,
            trace_id=payload.get("id"),
            redactions=dict(Counter(redaction.counts) + Counter(answer.counts)),
            unresolved_placeholders=unresolved_in(content, answer.replacements),
            run_mapping=answer.replacements,
        )

    restored = redaction.restore(content)
    return CompletionResult(
        content=restored.text,
        model_used=model_used,
        input_tokens=usage.get("prompt_tokens"),
        output_tokens=usage.get("completion_tokens"),
        latency_ms=latency_ms,
        trace_id=payload.get("id"),
        redactions=redaction.counts,
        unresolved_placeholders=restored.unresolved,
    )


#: Gateway answers that mean "not this model, maybe another one".
#:
#: The distinction runs the other way round from the obvious reading, and the
#: outage this exists for is the proof. Every candidate goes to the SAME
#: gateway, so:
#:
#:   ModelUnreachable -- the gateway said nothing. Asking it for a second model
#:       asks the same silent gateway. The chain cannot help, and pretending
#:       otherwise costs one more timeout per candidate.
#:   ModelRefused 5xx -- the gateway answered and reported that the backend for
#:       this model failed. That is 13.08.2026: LiteLLM was up, the local inference
#:       host was off, every task_type got 502. Exactly what a chain is for.
#:   ModelRefused 429 -- rate limited. About this model's availability, not
#:       about the request.
#:   ModelRefused other 4xx -- the request is wrong: unknown model, bad key,
#:       context window exceeded. No candidate fixes that, and moving on would
#:       hide a typo in the routing table behind a working second entry.
RETRY_ON_STATUS = frozenset({429}) | frozenset(range(500, 600))


@dataclass(frozen=True)
class Attempt:
    """One candidate that was asked, and how it went.

    The record the audit needs is not "which model was configured" but which
    one answered and why not the first — that sentence is what a DORA or MaRisk
    reviewer asks for, and it cannot be reconstructed afterwards from the
    routing table, because the table says what was intended, not what happened.
    """

    model: str
    outcome: str  # "answered" | "refused" | "unreachable"
    detail: str = ""


async def complete_with_fallback(
    request: CompletionRequest,
    candidates: tuple[str, ...],
    *,
    base_url: str,
    api_key: str = "",
) -> CompletionResult:
    """Ask each candidate in turn until one answers.

    `request.model` is ignored — the chain decides. Passing both a model and a
    chain would be two answers to one question, and the loser would depend on
    which line of this function ran last.

    The result carries every attempt, including the one that succeeded. A
    caller that wants to record the fallback has the facts; this module writes
    nothing to a database, because it sits below the transport and a scheduled
    job must be able to call it (CHY-129).

    Raises the LAST error when no candidate answered. The first would name the
    model the author chose, which reads like the chain was never tried.
    """
    attempts: list[Attempt] = []
    last: CompletionError | None = None

    for model in candidates:
        try:
            result = await complete(
                replace(request, model=model), base_url=base_url, api_key=api_key
            )
        except ModelRefused as exc:
            attempts.append(Attempt(model, "refused", str(exc)))
            last = exc
            if exc.status_code not in RETRY_ON_STATUS:
                break
        except ModelUnreachable as exc:
            attempts.append(Attempt(model, "unreachable", str(exc)))
            last = exc
            break
        else:
            attempts.append(Attempt(model, "answered"))
            return replace(result, attempts=tuple(attempts))

    if last is None:
        # Reached only for an empty chain: the loop body either returns or sets
        # `last`. Checked here rather than up front so the branch is reachable
        # and a test can drive it — a guard nothing can exercise is a guard
        # nobody can trust.
        raise ValueError("complete_with_fallback needs at least one candidate")
    raise last
