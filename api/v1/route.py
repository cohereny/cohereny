"""
api/v1/route.py

POST /v1/route — resolve task_type to a model name via the static routing table.

This endpoint is the entry point for clients that need to know which model
to use for a given task before making a LiteLLM call. The response includes
litellm_base_url so clients have everything they need in one round-trip.

Typical client flow:
  1. POST /v1/route  { "task_type": "summarize" }
  2. Response:       { "task_type": "summarize", "model": "gemma3-4b", "litellm_base_url": "..." }
  3. Client calls LiteLLM directly at litellm_base_url with model name

Why expose the base URL in the response:
  Clients should not hardcode infrastructure URLs. Serving it here means
  the API is the single config source — if LiteLLM moves, only the server
  env var changes, not every client.

Phase 2: /complete will combine route + LiteLLM call into one endpoint.
Until then, /route + direct LiteLLM call is the intended pattern.

Called by:
  - Agentic clients selecting a model before a LiteLLM call
  - Study pilot (future migration from hardcoded MODEL_SMALL/MODEL_MEDIUM)

Dependencies:
  - router.config     (RouterConfig — task_type → model resolution)
  - api.config        (settings.litellm_base_url)
  - api.deps          (require_auth, CurrentUser)
"""
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api.config import settings
from api.deps import CurrentUser, require_auth
from api.v1._routing import route_for, router_config
from api.v1._responses import NOT_FOUND, ROUTING_CONFLICT
from core.db import get_pool
from router.config import RouterConfig

router = APIRouter()


def get_router_config() -> RouterConfig:
    """The deployment's model declaration — the instance `_routing` loads.

    This used to load its own, always from the bundled file. So /v1/route
    answered from a different declaration than /v1/complete and the workflow
    runner the moment a deployment named its own with ROUTING_CONFIG: in the
    quickstart, every route request failed with a 500 for a model the rest of
    the API served (CHY-189). One declaration, read once, for every caller that
    routes. A dependency still, so tests can inject one through
    app.dependency_overrides[get_router_config].
    """
    return router_config


class RouteRequest(BaseModel):
    task_type: str
    # Required since CHY-87 step 2b. A routing decision is always made *in* a
    # scope: an organisation sets a default and a project sharpens it. The
    # endpoint had no consumers outside the tests, so making the contract
    # honest cost nothing.
    scope_id: UUID


class RouteResponse(BaseModel):
    task_type: str
    # The model a caller should try first. Kept as a scalar so existing callers
    # keep working; `candidates` carries the rest.
    model: str
    # The whole chain, best first, since CHY-87. A caller that walks it itself
    # gets the same order the server would use -- two orders for one decision
    # would be two answers to one question.
    candidates: list[str]
    # The longest answer the rule allows, in tokens (CHY-166). This endpoint
    # hands out the gateway's address so a caller can ask the model itself;
    # without the limit that caller would be the one path around it.
    max_tokens: int
    litellm_base_url: str


@router.post("/route", response_model=RouteResponse,
             responses={**NOT_FOUND, **ROUTING_CONFLICT})
async def route(
    request: RouteRequest,
    actor: CurrentUser = Depends(require_auth),
    pool=Depends(get_pool),
    config: RouterConfig = Depends(get_router_config),
) -> RouteResponse:
    """Resolve task_type to its model candidates and the LiteLLM base URL."""
    rule = await route_for(pool, config, request.scope_id, request.task_type)
    return RouteResponse(
        task_type=request.task_type,
        model=rule.candidates[0],
        candidates=list(rule.candidates),
        max_tokens=rule.max_tokens,
        litellm_base_url=settings.litellm_base_url,
    )
