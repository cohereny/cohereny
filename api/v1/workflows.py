"""
api/v1/workflows.py

REST endpoints for workflow definitions and their graphs (CHY-82).

A workflow is a **directed graph**, not a DAG and not an ordered list: cycles
are allowed and every back edge carries an iteration limit (ADR-026). The
execution order follows from traversal, not from a position in a row.

Workflow-as-data is the product feature: workflows live in the database and
change at runtime without a deployment. A configuration file would move the
whole thing back into the release cycle.

Two endpoints stay 501 on purpose. `GET /{id}/versions` and
`PUT /{id}/nodes/{id}` need the version lineage of arc42 §5.6
(`superseded_by_version`), which migration 018 adds as a column and nothing
yet writes — a new version of a workflow is a copy of its whole graph, and
that belongs with the runner (CHY-127) that has to pin one.

What executes these graphs does not exist yet. ADR-028 decided an own runner
over LangGraph; CHY-127 builds it. Until then this is a place to *define*
workflows, and the validator is what keeps a definition honest.

Called by:
  - Clients building workflow structures (architects)

Dependencies:
  - api.deps · contexts.orchestration.service · core.db
"""
from uuid import UUID

from fastapi import APIRouter, Depends

from api.deps import CurrentUser, require_auth
from api.v1._planned import not_implemented, planned
from api.v1._responses import FORBIDDEN, NOT_FOUND
from api.config import settings
from contexts.orchestration import service
from contexts.orchestration.errors import NodeExecutionFailed, NodeTypeNotExecutable
from contexts.orchestration.service import (
    ExecutorRegistry,
    NodeContext,
    NodeResult,
)
from core import completion
from contexts.orchestration.schema import (
    ConnectionCreate,
    OversightDecision,
    NodeCreate,
    RunNodesOut,
    ValidationOut,
    WorkflowConnection,
    WorkflowCreate,
    WorkflowDefinition,
    WorkflowListOut,
    WorkflowNode,
    WorkflowRunOut,
    WorkflowRunStart,
    WorkflowStatusPut,
)
from contexts.repository import service as repository
from contexts.repository.schema import ComposeRequest
from core.db import get_pool
from core.redis import get_redis
from api.v1._routing import route_for, router_config
from kernel.audit import ACTION_ROUTING_FELL_BACK, target_ref, write_audit
from kernel.errors import ScopeNotFound
from router.config import RoutingConfigError
from router.errors import RoutingError

router = APIRouter()

# A graph may only be changed while its workflow is a draft, and activation is
# refused for a graph that does not validate. Both arrive as 409 — the caller
# is allowed to do this in principle, the state of the workflow is what
# refuses. The same reading as the block lifecycle (CHY-32).
_GRAPH_CONFLICT = {
    409: {"description": "The workflow is not a draft, or its graph does not "
                         "validate. See CHY-82."}
}


@router.get("", response_model=WorkflowListOut)
async def list_workflows(
    scope_id: UUID | None = None,
    actor: CurrentUser = Depends(require_auth),
    pool=Depends(get_pool),
) -> WorkflowListOut:
    return await service.list_workflows(pool, actor, scope_id)


@router.post("", response_model=WorkflowDefinition, status_code=201, responses=FORBIDDEN)
async def create_workflow(
    body: WorkflowCreate,
    actor: CurrentUser = Depends(require_auth),
    pool=Depends(get_pool),
) -> WorkflowDefinition:
    return await service.create_workflow(pool, actor, body)


@router.get("/{workflow_id}", response_model=WorkflowDefinition, responses=NOT_FOUND)
async def get_workflow(
    workflow_id: UUID,
    actor: CurrentUser = Depends(require_auth),
    pool=Depends(get_pool),
) -> WorkflowDefinition:
    """The definition without its graph — see `/graph` for nodes and edges.

    Kept apart so a listing of ten workflows does not load ten graphs.
    """
    return await service.get_workflow(pool, actor, workflow_id)


@router.get("/{workflow_id}/versions", **planned("CHY-127"))
async def list_versions(workflow_id: str, actor: CurrentUser = Depends(require_auth)) -> None:
    not_implemented("CHY-127")


@router.put(
    "/{workflow_id}",
    response_model=WorkflowDefinition,
    responses={**FORBIDDEN, **NOT_FOUND, **_GRAPH_CONFLICT},
)
async def set_workflow_status(
    workflow_id: UUID,
    body: WorkflowStatusPut,
    actor: CurrentUser = Depends(require_auth),
    pool=Depends(get_pool),
) -> WorkflowDefinition:
    """Release or retire a workflow.

    Only the status. Changing the name or the scope of a released workflow is a
    new version, not an edit — the same rule context blocks follow.

    Activating an invalid graph answers **409** with every violation, not just
    the first: fixing a graph one round trip at a time is how a validator
    becomes something people route around.
    """
    return await service.set_workflow_status(pool, actor, workflow_id, body)


@router.get("/{workflow_id}/graph", response_model=WorkflowDefinition, responses=NOT_FOUND)
async def get_graph(
    workflow_id: UUID,
    actor: CurrentUser = Depends(require_auth),
    pool=Depends(get_pool),
) -> WorkflowDefinition:
    return await service.get_graph(pool, actor, workflow_id)


@router.post(
    "/{workflow_id}/validate",
    response_model=ValidationOut,
    responses=NOT_FOUND,
)
async def validate_workflow(
    workflow_id: UUID,
    actor: CurrentUser = Depends(require_auth),
    pool=Depends(get_pool),
) -> ValidationOut:
    """A dry run: what activation would refuse, without activating anything.

    Answers 200 with `valid: false` rather than an error status. A validation
    that ran successfully and found problems *succeeded* — reporting it as 4xx
    would conflate "your request was wrong" with "your workflow is".
    """
    return await service.validate_workflow(pool, actor, workflow_id)


@router.post(
    "/{workflow_id}/nodes",
    response_model=WorkflowNode,
    status_code=201,
    responses={**FORBIDDEN, **NOT_FOUND, **_GRAPH_CONFLICT},
)
async def add_node(
    workflow_id: UUID,
    body: NodeCreate,
    actor: CurrentUser = Depends(require_auth),
    pool=Depends(get_pool),
) -> WorkflowNode:
    return await service.add_node(pool, actor, workflow_id, body)


@router.put("/{workflow_id}/nodes/{node_id}", **planned("CHY-127"))
async def update_node(workflow_id: str, node_id: str, actor: CurrentUser = Depends(require_auth)) -> None:
    not_implemented("CHY-127")


@router.post(
    "/{workflow_id}/connections",
    response_model=WorkflowConnection,
    status_code=201,
    responses={**FORBIDDEN, **NOT_FOUND, **_GRAPH_CONFLICT},
)
async def add_connection(
    workflow_id: UUID,
    body: ConnectionCreate,
    actor: CurrentUser = Depends(require_auth),
    pool=Depends(get_pool),
) -> WorkflowConnection:
    """Edges name their endpoints by `node_ref`, not by id.

    A client that has just posted two nodes knows the refs it chose; making it
    keep the returned ids to draw an edge between them is bookkeeping the
    server can do.
    """
    return await service.add_connection(pool, actor, workflow_id, body)


@router.delete(
    "/{workflow_id}/connections/{connection_id}",
    status_code=204,
    responses={**FORBIDDEN, **NOT_FOUND, **_GRAPH_CONFLICT},
)
async def delete_connection(
    workflow_id: UUID,
    connection_id: UUID,
    actor: CurrentUser = Depends(require_auth),
    pool=Depends(get_pool),
) -> None:
    await service.remove_connection(pool, actor, workflow_id, connection_id)


# ── running a workflow (CHY-127, CHY-129) ─────────────────────────────────────
#
# The registry is built here and not in the context. Contexts do not import one
# another (ADR-030), so whoever wires them together has to sit above all of
# them — and that is the composition root, which is this file.


def _compose_for(pool, redis):
    """Build the `context_compose` executor for this request.

    A closure rather than a module-level function, because composing needs a
    pool and a Redis client and `NodeContext` deliberately carries neither. A
    node context that held connections would make every executor a place where
    someone can open a transaction, and the runner would stop being testable
    without a database.
    """

    async def _compose(ctx: NodeContext) -> NodeResult:
        """Execute a `context_compose` node.

        `compose_context` has lived below the transport since CHY-105, so this
        does not reimplement composition — it translates a node into a request.
        It sits here and not in `contexts/orchestration` because one context may
        not import another (ADR-030); the composition root may know both.

        **The scope comes from the workflow definition**, and a node cannot
        override it from its own config. A node that could choose its own scope
        could reach context it was never approved for, and it would do so
        without leaving anything a reviewer would notice.

        **Which block types are asked for comes from `context_requirements`**,
        a typed column since CHY-134 — not from `config`, where it was one key
        among five that nothing validated. Absent means "everything this scope
        makes available", which is the ordinary case; the empty list is
        unstorable, because the store would read it as exactly that and the
        author meant the opposite.
        """
        if ctx.scope_id is None:
            reason = (
                f"Node {ctx.node_ref} cannot compose: its workflow has no "
                f"scope. Composition is defined as 'what this scope makes "
                f"available', so there is nothing to assemble — and an empty "
                f"context would reach a model looking exactly like a real one."
            )
            raise NodeTypeNotExecutable(ctx.node_type, ctx.node_ref, reason=reason)

        composed = await repository.compose_context(
            pool, redis,
            ComposeRequest(
                scope_id=ctx.scope_id,
                # Part of the cache key, and not optional in practice:
                # `node_ref` is unique only per workflow, so without this two
                # workflows that both name a node `compose` would share an
                # entry and each would get the other's context (CHY-133).
                workflow_id=ctx.workflow_id,
                node_ref=ctx.node_ref,
                # No model: `config["model"]` is gone (CHY-145), and a compose
                # node does not know which model its output will reach — that
                # is resolved later, by the llm_call downstream. Harmless while
                # composition does not shape content by model; the day it
                # does, compose has to learn the chain first (ADR-010).
                model_id=None,
                block_types=(ctx.context_requirements.block_types
                             if ctx.context_requirements else None),
            ),
        )
        return NodeResult(output={
            "prompt": composed.as_prompt(),
            # Not decoration: these two say *what* the composition contained,
            # which is what makes the run reproducible. A record naming only
            # the result is ADR-032 with the sign flipped — it looks complete
            # and cannot be checked.
            "block_versions": composed.block_versions,
            "scope_generations": composed.scope_generations,
            "cache_hit": composed.cache_hit,
        })

    return _compose


def _call_model_for(pool):
    """Build the `llm_call` executor for this request.

    A closure for the reason `_compose_for` is one: resolving a model reads the
    routing rules of the scope tree, which takes a pool, and `NodeContext`
    deliberately carries none.
    """

    async def _call_model(ctx: NodeContext) -> NodeResult:
        """Execute an `llm_call` node.

        **The model is resolved, not named** (CHY-145). By the node's
        `task_type`, from the routing rules of the workflow's scope, exactly
        as `/v1/complete` does it — one decision, one path. Until CHY-145 the
        node read `config["model"]`, which skipped approval, scope
        inheritance, the privacy ratchet and the fallback chain: a run could
        reach a model never released for its scope.

        **The call goes through the chain.** When the first candidate refuses
        with 429 or 5xx the next one answers (ADR-035), and the fallback is
        written to the audit with the run and the node that fell back.

        **Failures end the run, they do not escape it.** No rule for the task
        type, a scope that is gone, a model that refused: each becomes
        `NodeExecutionFailed`, which the runner records. Before CHY-145 a
        `ModelRefused` passed through the runner and left the run at `running`.

        **The prompt comes from the node's config or its input** (CHY-131), and
        an empty one is refused before the request is built.

        **So does the system message** (CHY-189). Until then it came from the
        config only, so a node could hand the model the composed context or the
        run's input, never both: `{"system": "compose.prompt", "prompt":
        "run.message"}` now gives the context as instructions and the input as
        the request — the split `/v1/complete` makes (system = composition,
        user = prompt). The config wins where both are set, as for the prompt.
        """
        if ctx.scope_id is None:
            reason = (
                f"Node {ctx.node_ref} cannot be routed: its workflow has no "
                f"scope, and which model serves a task type is decided per "
                f"scope (ADR-038). There is no deployment-wide default."
            )
            raise NodeTypeNotExecutable(ctx.node_type, ctx.node_ref, reason=reason)
        if not ctx.task_type:
            # Unreachable through the API — migration 023 refuses an llm_call
            # without one. Checked anyway: routing by an empty task type would
            # ask the rules for a decision nobody made.
            reason = f"Node {ctx.node_ref} has no task_type to be routed by."
            raise NodeTypeNotExecutable(ctx.node_type, ctx.node_ref, reason=reason)

        prompt = ctx.config.get("prompt") or ctx.input_data.get("prompt", "")
        if not prompt:
            reason = (
                f"Node {ctx.node_ref} has no prompt. Neither its config nor its "
                f"input carries one — an upstream context_compose reaches it "
                f"through an input_mapping such as "
                f'{{"prompt": "<compose-node>.prompt"}}, and without that mapping '
                f"nothing connects the two."
            )
            raise NodeTypeNotExecutable(ctx.node_type, ctx.node_ref, reason=reason)

        try:
            rule = await route_for(pool, router_config, ctx.scope_id, ctx.task_type)
        except (RoutingError, RoutingConfigError, ScopeNotFound) as exc:
            raise NodeExecutionFailed(
                ctx.node_ref,
                f"no model for task type {ctx.task_type!r} in scope "
                f"{ctx.scope_id}: {exc}",
            ) from exc
        candidates = rule.candidates

        try:
            result = await completion.complete_with_fallback(
                completion.CompletionRequest(
                    prompt=prompt,
                    model=candidates[0],
                    system=ctx.config.get("system") or ctx.input_data.get("system", ""),
                    task_type=ctx.task_type,
                    actor_id=ctx.actor_id,
                    scope_id=ctx.scope_id,
                    # A node has no field of its own for this: the rule's limit
                    # is the one a run gets (CHY-166).
                    max_tokens=rule.max_tokens,
                ),
                candidates,
                base_url=settings.litellm_base_url,
                api_key=settings.litellm_api_key,
            )
        except completion.CompletionError as exc:
            raise NodeExecutionFailed(
                ctx.node_ref,
                f"no candidate for task type {ctx.task_type!r} answered "
                f"({', '.join(candidates)}): {exc}",
            ) from exc

        if result.model_used != candidates[0]:
            # The same record /v1/complete writes, plus where it happened. A
            # fallback inside a run is otherwise visible only as a model name
            # in node_executions that differs from nothing — the chain that
            # explains it is not stored anywhere else.
            await write_audit(
                pool,
                action=ACTION_ROUTING_FELL_BACK,
                actor_id=ctx.actor_id,
                target=target_ref("workflow_run", ctx.run_id),
                payload={
                    "workflow_id": str(ctx.workflow_id),
                    "node_ref": ctx.node_ref,
                    "iteration": ctx.iteration,
                    "task_type": ctx.task_type,
                    "chosen": candidates[0],
                    "answered": result.model_used,
                    "attempts": [
                        {"model": a.model, "outcome": a.outcome, "detail": a.detail[:200]}
                        for a in result.attempts
                    ],
                },
            )

        return NodeResult(
            output={"content": result.content, "trace_id": result.trace_id},
            model_used=result.model_used,
        )

    return _call_model


def get_executors(
    pool=Depends(get_pool), redis=Depends(get_redis)
) -> ExecutorRegistry:
    """What this deployment can actually execute.

    A dependency rather than a module constant so a test can substitute one —
    and since CHY-133 it needs one, because composing takes a pool and a Redis
    client that only the request scope has.

    Two of the three mandatory node types are here. `human_review` needs no
    executor at all: it is a wait state of the state machine, not work (ADR-028).

    `tool_call` stays absent, and that refusal is honest rather than pending —
    nothing anywhere defines what a tool call is. A stub would let a run reach
    `complete` without doing the work, which is indistinguishable from success.
    """
    return ExecutorRegistry({
        "llm_call": _call_model_for(pool),
        "context_compose": _compose_for(pool, redis),
    })


@router.post(
    "/{workflow_id}/runs",
    response_model=WorkflowRunOut,
    status_code=201,
    responses={**FORBIDDEN, **NOT_FOUND, **_GRAPH_CONFLICT},
)
async def start_run(
    workflow_id: UUID,
    body: WorkflowRunStart,
    actor: CurrentUser = Depends(require_auth),
    pool=Depends(get_pool),
    executors: ExecutorRegistry = Depends(get_executors),
) -> WorkflowRunOut:
    """Start a run and push it as far as it goes in one call.

    Not "queue a run and answer 202". The run is advanced synchronously until
    it finishes, fails, or reaches a gate, and the response says which. A
    caller that has to poll to learn that its workflow was invalid learns it
    later and from a different endpoint.

    Only an **active** workflow can be run. A draft has not passed the
    validator, and the validator is what guarantees the mandatory nodes and a
    limit on every back edge (ADR-004, CHY-82).
    """
    advance = await service.start_workflow_run(
        pool, actor, workflow_id, body, registry=executors
    )
    return advance.run


@router.get("/runs/{run_id}", response_model=WorkflowRunOut, responses=NOT_FOUND)
async def get_run(
    run_id: UUID,
    actor: CurrentUser = Depends(require_auth),
    pool=Depends(get_pool),
) -> WorkflowRunOut:
    return await service.get_run(pool, actor, run_id)


@router.get("/runs/{run_id}/nodes", response_model=RunNodesOut, responses=NOT_FOUND)
async def get_run_nodes(
    run_id: UUID,
    actor: CurrentUser = Depends(require_auth),
    pool=Depends(get_pool),
) -> RunNodesOut:
    """What each node of the run produced, in the order it ran (CHY-229).

    Read this before deciding at a `human_review` gate: the decision is about
    the output of the nodes before it — for an `llm_call` the model's answer
    under `output.content`.
    """
    return await service.run_nodes(pool, actor, run_id)


@router.post(
    "/runs/{run_id}/oversight",
    response_model=WorkflowRunOut,
    responses={**FORBIDDEN, **NOT_FOUND, **_GRAPH_CONFLICT},
)
async def decide_oversight(
    run_id: UUID,
    body: OversightDecision,
    actor: CurrentUser = Depends(require_auth),
    pool=Depends(get_pool),
    executors: ExecutorRegistry = Depends(get_executors),
) -> WorkflowRunOut:
    """A human decides at the gate, and the run continues — or stops for good.

    Requires `reviewer`, and the decision is audited with the actor. An Art. 14
    record that cannot say who approved is not a record.

    A rejection ends the run as **`rejected`**, not `failed`. Nothing went
    wrong: a person looked and said no. Conflating the two would make a
    governance decision appear as an incident in every report that counts
    failures (CHY-83).

    There is no `approve` on timeout anywhere in this API, and that absence is
    a rule rather than an omission: a gate that approves on silence is not a
    gate (ADR-004). What happens when nobody decides is configured on the node
    as `reject` or `escalate`.
    """
    advance = await service.record_oversight(
        pool, actor, run_id, body, registry=executors
    )
    return advance.run
