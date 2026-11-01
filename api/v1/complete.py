"""
api/v1/complete.py

LLM completion proxy — routes the prompt via LiteLLM with the composed context.

Flow:
  1. Resolve task_type → model via RouterConfig
  2. Compose the context for the scope (contexts.repository.compose), or load an explicit
     selection of blocks — from the same scope's chain only (CHY-196)
  3. Build messages: system (the composed context) + user (prompt)
  4. POST to LiteLLM /chat/completions
  5. Return content + model_used + token counts + latency + blocks_used

Why this calls contexts.repository.compose instead of querying blocks itself (CHY-24):
  It used to run its own SELECT, and that SELECT had drifted into something
  else entirely. Four differences, all of them wrong:

    - `WHERE scope_id = $1`, flat. An org-level block never reached a
      project-scoped completion, which is the entire point of the scope tree.
    - `superseded_by_version IS NULL AND status = 'active'`: the filter CHY-32
      removed from composition because editing a block took it out of every
      prompt for the length of its review.
    - `ORDER BY block_type, created_at` rather than by scope depth, so the same
      context produced a different prompt here than in /v1/compose.
    - No cache. ADR-010's whole claim is that this path is PostgreSQL-free on a
      hit; it was a query per call.

  One composition function, one rendering (ComposedContext.as_prompt), one
  cache. Two endpoints that disagree about what a scope composes are worse than
  one that is slow.

When neither scope_id nor context_block_ids is provided the endpoint is a pure
passthrough with a single user message.

context_id and stream are accepted but ignored:
  context_id → superseded by scope_id; kept so old callers do not break
  stream      → not yet implemented
"""
from uuid import UUID

import asyncpg
import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from api.config import settings
from api.deps import CurrentUser, require_auth
from api.v1._responses import FORBIDDEN, GATEWAY, NOT_FOUND, ROUTING_CONFLICT, UNPROCESSABLE
from kernel.audit import ACTION_ROUTING_FELL_BACK, target_ref, write_audit
from kernel.identity import resolve_actor
from contexts.repository import service as repository
from kernel.errors import ScopeNotFound
from core.db import get_pool
from core.redis import get_redis
from core import completion as core_completion
from contexts.repository.schema import ComposeRequest
from api.v1._routing import route_for, router_config
from router import rules as routing_rules

router = APIRouter()


class CompleteRequest(BaseModel):
    task_type: str
    prompt: str
    scope_id: UUID | None = None        # Phase 2: load active blocks from this scope
    # Explicit selection instead of the whole composition. Only composable
    # blocks from scope_id's chain: the model is chosen and checked for that
    # scope, so its context may not come from anywhere else (CHY-196).
    context_block_ids: list[UUID] = []
    context_id: UUID | None = None      # Phase 3 — ignored
    stream: bool = False                # Phase 2 streaming — always False for now
    # The longest answer wanted, in tokens (CHY-166). Optional: without it the
    # routing rule's limit applies. May only go below that limit, never above.
    # No `le` here: the rule is the upper bound, and the value never reaches a
    # database column that could overflow.
    max_tokens: int | None = Field(default=None, ge=1)


class CompleteResponse(BaseModel):
    content: str
    model_used: str
    input_tokens: int | None
    output_tokens: int | None
    latency_ms: float
    blocks_used: int = 0
    trace_id: str | None = None


async def _load_explicit_blocks(
    pool: asyncpg.Pool, block_ids: list[UUID], scope_id: UUID
) -> list[asyncpg.Record]:
    """Specifically requested blocks, newest approved version each.

    The escape hatch for a caller that knows exactly which blocks it wants and
    does not want the scope tree. It still resolves to the newest *approved*
    version per line rather than the current row — otherwise editing a block
    would silently drop it here while /v1/compose kept serving it.

    Returns rows rather than plain strings since CHY-29: the trace records which
    *version* of each block went into an answer, and a list of contents cannot
    say that.

    Restricted to scope_id's chain since CHY-196. Before, the list was loaded
    without any scope filter: a caller could name a scope whose rule allows a
    hosted model and attach blocks from a `critical` scope, and their content
    went to the hosted model with only the first scope's data class checked.
    """
    if not block_ids:
        return []
    # Through the repository context since CHY-105: context_blocks belongs to
    # it, and a module that composes an answer has no business knowing the
    # table's version-selection rules.
    return await repository.active_blocks(pool, block_ids, scope_id)


# 403 is new since CHY-29: the endpoint resolves the caller to an actor row so
# the trace can name one, and resolve_actor refuses an anonymised actor.
@router.post(
    "/complete", response_model=CompleteResponse,
    responses={**FORBIDDEN, **NOT_FOUND, **ROUTING_CONFLICT, **GATEWAY, **UNPROCESSABLE},
)
async def complete(
    body: CompleteRequest,
    actor: CurrentUser = Depends(require_auth),
    pool: asyncpg.Pool = Depends(get_pool),
    redis: aioredis.Redis = Depends(get_redis),
) -> CompleteResponse:
    # Resolved, not just authenticated: the trace records the internal actor id
    # rather than the OIDC subject, so a DSGVO erasure (CHY-34) reaches it. A
    # subject identifier written into Langfuse would outlive the deletion.
    performer = await resolve_actor(pool, actor)
    if body.scope_id is None:
        # Since CHY-87 step 2b the routing rules live in the scope tree, so a
        # completion without a scope has nowhere to read its decision from.
        # Refused rather than defaulted: an installation that has not decided
        # should say so, not pick a model and let the choice look deliberate.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                "scope_id is required: which model serves a task type is "
                "decided per scope (ADR-037), and there is no deployment-wide "
                "default to fall back to"
            ),
        )
    rule = await route_for(pool, router_config, body.scope_id, body.task_type)
    candidates = rule.candidates
    # Before composing: a request the rule refuses should not cost a
    # composition first.
    max_tokens = routing_rules.token_limit(rule, body.max_tokens)
    # The context is compiled for the FIRST candidate. Today that is only a
    # cache-key dimension -- compose does not shape content by model -- so a
    # fallback answering does not change the prompt. The day adaptation becomes
    # model-specific, this line becomes a defect: the answering model would get
    # a context compiled for another one (ADR-010, CHY-87).
    model = candidates[0]

    block_versions: dict[str, int] = {}
    system_content = ""
    if body.context_block_ids:
        rows = await _load_explicit_blocks(pool, body.context_block_ids, body.scope_id)
        block_versions = {str(r["block_id"]): r["version"] for r in rows}
        system_content = "\n\n---\n\n".join(r["content"] for r in rows)
    elif body.scope_id is not None:
        # model_id in the cache key: auto_route and fallback chains can pick a
        # different model for the same scope, and each needs its own compiled
        # form (ADR-010).
        try:
            ctx = await repository.compose_context(
                pool, redis, ComposeRequest(scope_id=body.scope_id, model_id=model)
            )
        except ScopeNotFound as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
            ) from exc
        block_versions = ctx.block_versions
        # as_prompt, not a local join: /v1/compose, this endpoint and any future
        # SDK must render the same context identically, or two callers with the
        # same blocks get different prompts.
        system_content = ctx.as_prompt()

    # The model call itself lives in core.completion since CHY-129. What is
    # left here is what exists because of HTTP: reading a request, resolving
    # the caller, choosing what to compose, and shaping a response. The
    # capability is reachable from the workflow runner and from a job now, and
    # there is one place that knows how a model is called.
    result = await core_completion.complete_with_fallback(
        core_completion.CompletionRequest(
            prompt=body.prompt,
            model=model,
            system=system_content,
            task_type=body.task_type,
            actor_id=performer.id,
            scope_id=body.scope_id,
            block_versions=block_versions,
            max_tokens=max_tokens,
        ),
        candidates,
        base_url=settings.litellm_base_url,
        api_key=settings.litellm_api_key,
    )

    if result.model_used != candidates[0]:
        # Recorded where a pool exists. core.completion returns the facts and
        # writes nothing -- it sits below the transport (CHY-129). Without this
        # line the fallback is invisible: the answer names the model that
        # replied, and nothing says it was not the one that was chosen.
        await write_audit(
            pool,
            action=ACTION_ROUTING_FELL_BACK,
            actor_id=performer.id,
            target=target_ref("task_type", body.task_type),
            payload={
                "chosen": candidates[0],
                "answered": result.model_used,
                "attempts": [
                    {"model": a.model, "outcome": a.outcome, "detail": a.detail[:200]}
                    for a in result.attempts
                ],
            },
        )

    return CompleteResponse(
        content=result.content,
        model_used=result.model_used,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        latency_ms=result.latency_ms,
        blocks_used=len(block_versions),
        trace_id=result.trace_id,
    )
