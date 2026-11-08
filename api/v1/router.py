"""
api/v1/router.py

Aggregates all v1 endpoints under the /v1 prefix, and assigns each one a
consumer surface (ADR-031, CHY-109).

The assignment below is a declaration, not a sequence of calls. Two things are
derived from it and cannot disagree: the runtime gate (`dependencies=`) and the
per-surface contracts written by scripts/export_openapi.py. Writing the second
by hand — say, by grouping on OpenAPI tags — was wrong from the start: the
`governance` tag covered merge requests (author) and the audit log (observe).
The merge requests are gone since CHY-65, the reason to derive stays: a tag
says what a route is about, the surface says who may call it.

Why the gate is attached per router and not per endpoint:
  A gate that has to be repeated on every route is a gate that will be missing
  from one of them, and the missing one will be the interesting one.

Endpoint overview:
  /v1/health                    → liveness check (no auth)
  /v1/route                     → resolve task_type to model + LiteLLM URL
  /v1/scopes                    → scope hierarchy management
  /v1/me                        → the caller's actor id, role and memberships
  /v1/blocks                    → context block CRUD
  /v1/workflows                 → workflow definitions
  /v1/compose                   → context composition for workflow nodes
  /v1/complete                  → LLM completion proxy
  /v1/executions/.../feedback   → user feedback
  /v1/learning-proposals        → learning loop
  /v1/benchmarks                → quality measurement
  /v1/admin                     → audit log, actor management
"""
from fastapi import APIRouter, Depends

from api.surfaces import Surface, require_surface
from api.v1 import (
    admin,
    audit,
    benchmarks,
    blocks,
    complete,
    compose,
    feedback,
    health,
    me,
    pilots,
    questions,
    route,
    scopes,
    workflows,
)

PREFIX = "/v1"

#: (router, prefix, tag, surface). `surface=None` means the endpoint takes no
#: token — today only /v1/health, and it must stay that way: a readiness probe
#: behind an audience gate reports the service as down whenever a token is wrong.
#:
#: ADR-031 named the four surfaces and listed the routers it had in view. Three
#: were not in that list and are placed here with a reason, because leaving them
#: out would have meant leaving them ungated:
#:
#:   pilots     → author.   The reference UI's only consumer today (7 call sites),
#:                and a pilot run is human-initiated work, not a machine call.
#:   questions  → observe.  The question bank exists to measure coverage of a
#:                study run; it is evidence about a run, not authoring.
#:   feedback   → author.   Submitting a correction is authoring, and the same
#:                humans do it. Reading aggregate feedback belongs to observe and
#:                does not exist yet (CHY-40).
_ASSIGNMENT: list[tuple[APIRouter, str, str, Surface | None]] = [
    (health.router, "", "health", None),
    (route.router, "", "routing", Surface.SERVE),
    (scopes.router, "/scopes", "scopes", Surface.AUTHOR),
    (me.router, "", "scopes", Surface.AUTHOR),
    (blocks.router, "/blocks", "blocks", Surface.AUTHOR),
    (workflows.router, "/workflows", "workflows", Surface.AUTHOR),
    (compose.router, "", "runtime", Surface.SERVE),
    (complete.router, "", "runtime", Surface.SERVE),
    (pilots.router, "/pilots", "pilots", Surface.AUTHOR),
    (questions.router, "/questions", "questions", Surface.OBSERVE),
    (feedback.router, "", "feedback", Surface.AUTHOR),
    (benchmarks.router, "/benchmarks", "benchmarks", Surface.OBSERVE),
    (admin.router, "/admin", "admin", Surface.ADMIN),
    # No prefix: the audit log is read by reviewers too, not only administrators
    # — the required role depends on the filter, not on the route (CHY-31).
    (audit.router, "", "governance", Surface.OBSERVE),
]

router = APIRouter(prefix=PREFIX)

#: surface value → the full paths published on it. Built from the same loop that
#: registers the routes, so a route cannot appear in a contract it is not gated
#: for, nor be gated without appearing.
SURFACE_PATHS: dict[str, set[str]] = {}

for _sub, _prefix, _tag, _surface in _ASSIGNMENT:
    router.include_router(
        _sub,
        prefix=_prefix,
        tags=[_tag],
        dependencies=[Depends(require_surface(_surface))] if _surface else [],
    )
    if _surface is not None:
        SURFACE_PATHS.setdefault(_surface.value, set()).update(
            f"{PREFIX}{_prefix}{_r.path}" for _r in _sub.routes
        )
