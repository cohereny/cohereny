"""
api/main.py

FastAPI application entry point and startup/shutdown lifecycle.

On startup the PostgreSQL connection pool and Redis connection are initialized
(lifespan context manager). On shutdown both are closed cleanly to prevent
connection leaks in the asyncio environment.

OpenAPI docs (Swagger UI / ReDoc) are only active in dev mode — in prod an
unauthenticated endpoint exposing internal schema details would be a security risk.

Called by:
  - uvicorn (entrypoint: api.main:app)

Dependencies:
  - api.config      (Settings, api_version, dev_mode)
  - api.v1.router   (all /v1/* endpoints)
  - core.db         (asyncpg pool management)
  - core.redis      (aioredis connection management)
"""
import asyncpg
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from api.v1.router import router as v1_router
from api.config import settings
from core.db import create_pool, close_pool
from core.redis import create_redis, close_redis
from core.tracing import init_tracing, shutdown_tracing
from contexts.pilots.errors import (
    InvalidDecision,
    NoCoverageData,
    ConfigSectionNotEffective,
    NotAwaitingOversight,
    UnknownPilotConfig,
    OversightGateGone,
    PilotError,
    QuestionNotFound,
    RunHasTransparencyRecords,
    RunNotCancellable,
    RunNotFound,
    RunNotOwned,
    UnknownPilot,
)
from core.errors import CompletionError, ModelRefused, ModelUnreachable
from router.errors import (
    MalformedRoutingRule,
    NoRouteForTask,
    RouteExceedsScopeClass,
    RoutingError,
    RoutingRuleNotApprovable,
    TokenLimitAboveRule,
)
from contexts.orchestration.errors import (
    MalformedCondition,
    OversightGateMissing,
    ReviewerAlreadyDecided,
    RunDidNotSettle,
    RunNotAwaitingOversight,
    RunNotFound as WorkflowRunNotFound,
    WorkflowNotRunnable,
    ConnectionNotFound,
    NodeTypeNotExecutable,
    InvalidNodeRef,
    NodeExecutionFailed,
    NodeNotFound,
    NodeNotStorable,
    OrchestrationError,
    SubWorkflowCycle,
    TargetWorkflowNotFound,
    UnknownNodeInEdge,
    WorkflowInvalid,
    WorkflowNotEditable,
    WorkflowNotFound,
)
from contexts.learning.errors import (
    AnchorNotApprovable,
    BenchmarkRunNotFound,
    LearningError,
    MalformedAnchor,
)
from contexts.repository.errors import (
    BlockNotFound,
    BlockOutsideScope,
    BlockTypeImmutable,
    InvalidTransition,
    GovernanceKeyLocked,
    GovernanceNotEditable,
    NothingPublished,
    NothingToPromote,
    PrivacyRatchetViolation,
    PromotionTargetInvalid,
    UnknownGovernanceKey,
    BlockNotStorable,
    RepositoryError,
    UnknownStatus,
    VersionConflict,
)
from kernel.errors import (
    AccessDenied,
    ActorAnonymized,
    ActorGone,
    ActorNotFound,
    MembershipExists,
    ParentScopeNotFound,
    ScopeLoosensPrivacy,
    ActorStateInconsistent,
    KernelError,
    LastAdminProtected,
    ScopeNotFound,
    UnknownRole,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await create_pool()
    await create_redis()
    # After the pool: a failing auth_check must not hide a failing database.
    # Never raises — a broken trace store may not stop the API from serving.
    init_tracing()
    yield
    # Before the connections close: shutdown() flushes what the SDK still has
    # queued, and that batch is the one covering the seconds before a deploy.
    shutdown_tracing()
    await close_pool()
    await close_redis()


API_DESCRIPTION = """\
Self-hosted middleware for multi-LLM workflows with a versioned context
repository. Context blocks live in a scope hierarchy (org → team → project),
are composed along that hierarchy at request time, and only reach a live prompt
after review.

Two conventions worth knowing before writing a client:

* **409 is normal.** The block lifecycle refuses transitions that are not
  allowed from the current status, and refuses a request whose `version` is not
  the current one. That is the review gate doing its job, not an error to retry.
* **501 means planned.** Operations marked `x-cohereny-status: planned` exist in
  this contract and not yet in the code. Each names its ticket.

This specification is generated from the running application and checked into
the repository as `openapi.yaml`; CI fails when the two disagree (ADR-029).
"""

app = FastAPI(
    title="Cohereny API",
    version=settings.api_version,
    description=API_DESCRIPTION,
    # No `servers`: every deployment has its own address. The list used to name
    # the reference installation's three stages, and every generated contract
    # carried those internal hostnames with it (CHY-188).
    docs_url="/docs" if settings.dev_mode else None,
    redoc_url="/redoc" if settings.dev_mode else None,
    # Gated with the other two. The docstring above has always said the schema
    # is dev-only "because an unauthenticated endpoint exposing internal schema
    # details would be a security risk" — but /openapi.json kept its default and
    # served exactly that in production. Swagger UI was hidden; the data behind
    # it was not (CHY-28).
    openapi_url="/openapi.json" if settings.dev_mode else None,
    lifespan=lifespan,
)

# ── Map domain errors onto HTTP (ADR-030, CHY-101) ──────────────────────────
#
# Until CHY-101, kernel/access.py (then core/authz.py) raised HTTPException
# itself — three times. That put the choice of status code inside the rules,
# and made the rules unusable outside a request: a CLI wanting to check a role
# got back a 403 object it had no idea what to do with.
#
# The codes are unchanged. Only the place where they are chosen has moved to
# where HTTP actually exists.
_KERNEL_STATUS: dict[type[KernelError], int] = {
    AccessDenied: 403,
    ActorGone: 403,
    ActorNotFound: 404,
    ScopeNotFound: 404,
    ParentScopeNotFound: 404,
    ScopeLoosensPrivacy: 422,
    MembershipExists: 409,
    ActorStateInconsistent: 409,
    LastAdminProtected: 409,
    # Raised only on the CLI bootstrap path today. Mapped anyway: the kernel is
    # transport-agnostic by design, so any of its errors may surface through a
    # transport that does not exist yet, and the handler's default is 500.
    UnknownRole: 400,
    ActorAnonymized: 409,
}


@app.exception_handler(KernelError)
async def _kernel_error(_request: Request, exc: KernelError) -> JSONResponse:
    """An unmapped kernel error becomes 500, not a quiet 403.

    The default is deliberately the pessimistic one: a new error type nobody
    registered here is a programming mistake and must not pass for a plausible
    refusal.
    """
    status = _KERNEL_STATUS.get(type(exc), 500)
    return JSONResponse(status_code=status, content={"detail": str(exc)})


# Same arrangement one zone further out (CHY-104). api/v1/pilots.py raised
# HTTPException twelve times; every one of those put a status code inside a
# rule and made the rule unreachable without a request — including the EU AI
# Act Art. 14 oversight decision, which could only be taken over HTTP.
#
# The codes are unchanged from before the move.
_PILOT_STATUS: dict[type[PilotError], int] = {
    RunNotFound: 404,
    NoCoverageData: 404,
    QuestionNotFound: 404,
    UnknownPilot: 400,
    InvalidDecision: 400,
    RunNotOwned: 403,
    RunNotCancellable: 409,
    RunHasTransparencyRecords: 409,
    NotAwaitingOversight: 409,
    # A typo in a section name — malformed request, like UnknownStatus (CHY-42).
    UnknownPilotConfig: 422,
    # Stored, served, and not yet in effect. 422 rather than 501: the request
    # is well-formed but asks for something that would silently do nothing, and
    # the refusal is about this field, not about the endpoint.
    ConfigSectionNotEffective: 422,
    OversightGateGone: 409,
}


@app.exception_handler(PilotError)
async def _pilot_error(_request: Request, exc: PilotError) -> JSONResponse:
    """An unmapped context error becomes 500, for the reason given above."""
    status = _PILOT_STATUS.get(type(exc), 500)
    return JSONResponse(status_code=status, content={"detail": str(exc)})


# The repository context (CHY-105). api/v1/blocks.py raised HTTPException nine
# times, which is why the whole approval lifecycle could only be reached over
# HTTP — see contexts/repository/errors.py. Codes unchanged from before.
_REPOSITORY_STATUS: dict[type[RepositoryError], int] = {
    BlockNotFound: 404,
    VersionConflict: 409,
    InvalidTransition: 409,
    BlockTypeImmutable: 409,
    NothingPublished: 409,
    NothingToPromote: 409,
    PromotionTargetInvalid: 422,
    # The privacy ratchet (CHY-62, ADR-017). 409 rather than 403: the caller is
    # allowed to do this in principle, the state of the scope tree is what
    # refuses — the same reading as InvalidTransition above.
    PrivacyRatchetViolation: 409,
    GovernanceKeyLocked: 409,
    GovernanceNotEditable: 409,
    # 422 like UnknownStatus: a value outside a fixed vocabulary is a malformed
    # request, not a conflict with anything.
    UnknownGovernanceKey: 422,
    UnknownStatus: 422,
    # A CHECK across columns refused the block (CHY-165) — malformed, like the
    # two above, not in conflict with anything.
    BlockNotStorable: 422,
    # An explicit block list reaching outside the request's scope chain
    # (CHY-196). 422, not 403: the request contradicts itself — it names one
    # scope for the model and context from another.
    BlockOutsideScope: 422,
}


# The orchestration context (CHY-82). Same reading of 409 as the block
# lifecycle: the caller is allowed to do this, the state of the workflow is
# what refuses.
_ORCHESTRATION_STATUS: dict[type[OrchestrationError], int] = {
    WorkflowNotFound: 404,
    # The workflow a sub_workflow node names in its body (CHY-165).
    TargetWorkflowNotFound: 404,
    NodeNotFound: 404,
    ConnectionNotFound: 404,
    WorkflowNotEditable: 409,
    WorkflowInvalid: 409,
    SubWorkflowCycle: 409,
    InvalidNodeRef: 409,
    # 422 like a malformed guard: the request parsed, and a rule the model
    # cannot express refused it — either one spanning two columns or one whose
    # vocabulary belongs to another context (CHY-134).
    NodeNotStorable: 422,
    # Listed although it never reaches HTTP: the runner records it in the run
    # and does not raise it. 502 because what failed is a model or the route to
    # one — if it ever did escape, "the upstream did not deliver" is the honest
    # reading, not "the server broke" (CHY-145).
    NodeExecutionFailed: 502,
    # 500, and deliberately listed rather than left to the default: the stored
    # graph does not hang together, which is not something the caller did or
    # can fix. Naming it here keeps the completeness test honest — an entry
    # that says "500 on purpose" and a missing entry look the same from the
    # outside otherwise.
    # 422: the request is well-formed, the capability is absent (CHY-130).
    NodeTypeNotExecutable: 422,
    # A guard that is not a well-formed condition. 422 like an unknown
    # status: the request is malformed, not in conflict with anything.
    MalformedCondition: 422,
    WorkflowNotRunnable: 409,
    RunNotAwaitingOversight: 409,
    # 409: the same person decided at this gate before. Not malformed — a
    # four-eyes gate needs someone else, and the state says who (CHY-176).
    ReviewerAlreadyDecided: 409,
    WorkflowRunNotFound: 404,
    # 500: the run says it is waiting and no node is. Not something the caller
    # did or can fix — its position was never recorded.
    OversightGateMissing: 500,
    RunDidNotSettle: 500,
    UnknownNodeInEdge: 500,
}


# Calling a model (CHY-129). The two are kept apart on purpose: 502 is a
# gateway that answered and refused — a wrong model name, an expired key, a
# context window exceeded — and retrying it unchanged fails again. 503 is a
# gateway that did not answer, which is worth retrying and worth waking someone
# if it persists. One status code for both would make an on-call page and a
# client bug look identical.
#: A routing decision that cannot be made, mapped once like every other family.
#:
#: 409 and not 404: the scope exists and the task type is a perfectly good
#: name — what is missing is a *decision*, and the fix is to create and approve
#: a rule, not to correct the request. A 404 would send whoever reads it
#: looking for a typo.
#:
#: A stored rule that cannot be read is 500: the request was fine, the data is
#: not, and only an operator can repair it.
_ROUTING_STATUS: dict[type[RoutingError], int] = {
    NoRouteForTask: 409,
    MalformedRoutingRule: 500,
    # 409 like a missing rule: two governance decisions contradict each other —
    # the scope's class and the rule's candidates — and neither is the caller's
    # doing. Not 500: nothing broke, a decision is missing (CHY-160).
    RouteExceedsScopeClass: 409,
    # 422, both: the request is the thing to fix (CHY-166). An approver
    # submitted a rule that could never be read; a caller asked for longer
    # answers than the approved rule allows.
    RoutingRuleNotApprovable: 422,
    TokenLimitAboveRule: 422,
}


# The learning context (ADR-043). An anchor that could never be measured is
# the approver's to fix, like an unusable routing rule (422). One that reads as
# malformed at run time is governance that failed — the server's problem (500).
_LEARNING_STATUS: dict[type[LearningError], int] = {
    AnchorNotApprovable: 422,
    BenchmarkRunNotFound: 404,
    MalformedAnchor: 500,
}


@app.exception_handler(LearningError)
async def _learning_error(_request: Request, exc: LearningError) -> JSONResponse:
    """An unmapped context error becomes 500, like every other context's."""
    return JSONResponse(
        status_code=_LEARNING_STATUS.get(type(exc), 500),
        content={"detail": str(exc)},
    )


@app.exception_handler(RoutingError)
async def _routing_error(_request: Request, exc: RoutingError) -> JSONResponse:
    return JSONResponse(
        status_code=_ROUTING_STATUS.get(type(exc), 500),
        content={"detail": str(exc)},
    )


_COMPLETION_STATUS: dict[type[CompletionError], int] = {
    ModelRefused: 502,
    ModelUnreachable: 503,
}


@app.exception_handler(CompletionError)
async def _completion_error(_request: Request, exc: CompletionError) -> JSONResponse:
    return JSONResponse(
        status_code=_COMPLETION_STATUS.get(type(exc), 502),
        content={"detail": str(exc)},
    )


@app.exception_handler(OrchestrationError)
async def _orchestration_error(_request: Request, exc: OrchestrationError) -> JSONResponse:
    return JSONResponse(
        status_code=_ORCHESTRATION_STATUS.get(type(exc), 500),
        content={"detail": str(exc)},
    )


@app.exception_handler(RepositoryError)
async def _repository_error(_request: Request, exc: RepositoryError) -> JSONResponse:
    """An unmapped context error becomes 500, for the reason given above."""
    status = _REPOSITORY_STATUS.get(type(exc), 500)
    return JSONResponse(status_code=status, content={"detail": str(exc)})


# The two ways PostgreSQL refuses a NUL character (CHY-165): U+0000 in a text
# column, and the escape `\u0000` inside a JSONB value.
@app.exception_handler(asyncpg.exceptions.CharacterNotInRepertoireError)
@app.exception_handler(asyncpg.exceptions.UntranslatableCharacterError)
async def _nul_in_input(_request: Request, _exc: Exception) -> JSONResponse:
    """A NUL character in the input is a 422, not a 500.

    The one database error mapped here rather than in a store, and on purpose.
    A NUL can arrive in any text or JSONB field of any request, and it is
    refused by PostgreSQL itself, not by a constraint a store could name — so
    translating it in each store would be the same six lines in every write
    path, and the one that is forgotten answers 500. The contract cannot
    express the rule either: "no U+0000 in any string" is not a schema
    property a client can read. Schemathesis found both variants on its first
    run.
    """
    return JSONResponse(
        status_code=422,
        content={"detail": "The input contains a NUL character (U+0000), which "
                           "PostgreSQL cannot store in text or JSON. Remove it "
                           "and send the request again."},
    )


app.include_router(v1_router)
