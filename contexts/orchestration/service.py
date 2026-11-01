"""
contexts/orchestration/service.py

Use cases of the orchestration context (ADR-030, CHY-82).

The published interface, together with schema and errors. Each function owns a
transaction, asks the aggregate for the decision and the store for the
execution, and returns something that has nothing to do with HTTP.

Callable from outside the API on purpose. ADR-030 point 4 turns on it: when the
runner of CHY-127 arrives it uses this interface rather than building a second
way to read a graph — and two ways to read a graph become two opinions about
which edges exist.

Dependencies:
  - contexts.orchestration.{graph, store, schema, errors} · kernel.{access, audit, identity}
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from uuid import UUID

from contexts.orchestration import conditions
from contexts.orchestration import dataflow
from contexts.orchestration import graph as analysis
from contexts.orchestration import runner
from contexts.orchestration import store
# Re-exported, not merely imported. A caller of `advance_run` has to be able
# to build an ExecutorRegistry — it is a parameter of the published interface,
# so it is published here. `execution` itself stays internal, and the boundary
# guard is right to say so: it caught the transport importing it directly
# within a minute of the route being written.
from contexts.orchestration.execution import (
    ConditionEvaluator,
    ExecutorRegistry,
    NodeContext,
    NodeExecutor,
    NodeResult,
    evaluate_edge_condition,
)

__all__ = [
    "ConditionEvaluator",
    "ExecutorRegistry",
    "NodeExecutor",
    "NodeResult",
    "evaluate_edge_condition",
]
from contexts.orchestration.errors import (
    ConnectionNotFound,
    MalformedCondition,
    OrchestrationError,
    OversightGateMissing,
    RunDidNotSettle,
    RunNotAwaitingOversight,
    RunNotFound,
    WorkflowNotRunnable,
    InvalidNodeRef,
    NodeNotFound,
    SubWorkflowCycle,
    WorkflowInvalid,
    WorkflowNotEditable,
    WorkflowNotFound,
)
from contexts.orchestration.schema import (
    ContextRequirements,
    OversightDecision,
    WorkflowRunOut,
    WorkflowRunStart,
    ConnectionCreate,
    NodeCreate,
    ValidationOut,
    ViolationOut,
    WorkflowCreate,
    WorkflowDefinition,
    WorkflowListOut,
    WorkflowNode,
    WorkflowConnection,
    WorkflowStatusPut,
)
from kernel.access import require
from kernel.audit import (
    ACTION_WORKFLOW_ACTIVATED,
    ACTION_WORKFLOW_CREATED,
    ACTION_RUN_OVERSIGHT,
    ACTION_RUN_STARTED,
    ACTION_WORKFLOW_DEPRECATED,
    target_ref,
    write_audit,
)
from kernel.identity import Caller, resolve_actor

#: Graph changes are allowed only here. The same rule context_blocks follows:
#: what a reviewer released must not change underneath the release.
EDITABLE_STATUS = "draft"

#: A run in one of these is over; advancing it again is a no-op rather than an
#: error, so a duplicate call from a retrying client cannot restart it.
_TERMINAL_RUN_STATES: frozenset[str] = frozenset({"complete", "failed", "rejected"})

#: The one status from which a human decision is accepted.
AWAITING = "awaiting_oversight"


def _node_out(row) -> WorkflowNode:
    return WorkflowNode(**store.row_to_dict(row))


def _connection_out(row) -> WorkflowConnection:
    return WorkflowConnection(**dict(row))


def _definition_out(row, nodes=(), connections=()) -> WorkflowDefinition:
    return WorkflowDefinition(
        **store.row_to_dict(row),
        nodes=[_node_out(n) for n in nodes],
        connections=[_connection_out(c) for c in connections],
    )


def _as_mapping(raw) -> dict:
    """A stored JSONB column as a dict, whatever asyncpg handed back.

    asyncpg returns JSONB as str on some versions and as dict on others — the
    third place in this codebase to meet that, and the reason both store
    modules carry a row_to_dict.
    """
    if raw is None:
        return {}
    if isinstance(raw, str):
        return json.loads(raw)
    return dict(raw)


def _as_requirements(raw) -> ContextRequirements | None:
    """The stored `context_requirements` as the model, or None (CHY-134).

    Validated on the way out of the database rather than trusted. The CHECK in
    migration 022 makes a malformed value unstorable, so this cannot normally
    fail — and if it ever does, a run that stops with a validation message is
    the outcome to want. Composing everything instead, which is what a silently
    dropped requirement would do, is the failure that looks like success.
    """
    if raw is None:
        return None
    return ContextRequirements.model_validate(
        json.loads(raw) if isinstance(raw, str) else raw
    )


def _as_condition(raw) -> dict | None:
    """A stored condition as a mapping, or None for an unconditional edge.

    Absence answers differently here than in `_as_mapping`: a node without
    configuration has an empty one, but an edge without a condition does not
    have an empty condition — it is unguarded, and `Edge.has_condition`
    derives exactly that distinction from this return value.
    """
    if raw is None:
        return None
    return _as_mapping(raw)


async def _load_graph(conn_or_pool, workflow_id: UUID) -> analysis.Graph:
    """The stored graph, in the shape the analysis works on.

    Reduced to id, ref, type and loop_limit — the analysis needs nothing else,
    and handing it full rows would let a future rule quietly start depending on
    a column the runner does not load.
    """
    node_rows = await store.nodes(conn_or_pool, workflow_id)
    edge_rows = await store.connections(conn_or_pool, workflow_id)
    return analysis.Graph(
        nodes=[
            analysis.Node(id=r["id"], node_ref=r["node_ref"], node_type=r["node_type"])
            for r in node_rows
        ],
        edges=[
            analysis.Edge(
                id=r["id"], source=r["source_node_id"], target=r["target_node_id"],
                loop_limit=r["loop_limit"], condition=_as_condition(r["condition"]),
                label=r["label"],
            )
            for r in edge_rows
        ],
    )


# ── reading ───────────────────────────────────────────────────────────────────

async def list_workflows(pool, caller: Caller, scope_id: UUID | None = None) -> WorkflowListOut:
    require(await resolve_actor(pool, caller), "consumer")
    rows = (await store.workflows_in_scope(pool, scope_id) if scope_id is not None
            else await store.all_workflows(pool))
    items = [_definition_out(r) for r in rows]
    return WorkflowListOut(items=items, total=len(items))


async def get_workflow(pool, caller: Caller, workflow_id: UUID) -> WorkflowDefinition:
    """The definition without its graph.

    Nodes and edges come from `get_graph`. A listing entry and a full graph are
    different requests with different costs, and returning the graph here would
    make every list of ten workflows load ten graphs.
    """
    require(await resolve_actor(pool, caller), "consumer")
    row = await store.workflow(pool, workflow_id)
    if row is None:
        raise WorkflowNotFound
    return _definition_out(row)


async def get_graph(pool, caller: Caller, workflow_id: UUID) -> WorkflowDefinition:
    require(await resolve_actor(pool, caller), "consumer")
    row = await store.workflow(pool, workflow_id)
    if row is None:
        raise WorkflowNotFound
    return _definition_out(
        row,
        await store.nodes(pool, workflow_id),
        await store.connections(pool, workflow_id),
    )


# ── writing ───────────────────────────────────────────────────────────────────

async def create_workflow(pool, caller: Caller, body: WorkflowCreate) -> WorkflowDefinition:
    author = await resolve_actor(pool, caller)
    require(author, "contributor")
    async with pool.acquire() as conn, conn.transaction():
        row = await store.insert_workflow(conn, body, author.id)
        await write_audit(
            conn, action=ACTION_WORKFLOW_CREATED, actor_id=author.id,
            target=target_ref("workflow", row["id"]),
            payload={"name": body.name, "scope_id": str(body.scope_id)},
        )
    return _definition_out(row)


async def _require_draft(conn, workflow_id: UUID) -> None:
    row = await store.lock_workflow(conn, workflow_id)
    if row is None:
        raise WorkflowNotFound
    if row["status"] != EDITABLE_STATUS:
        raise WorkflowNotEditable(row["status"])


async def add_node(pool, caller: Caller, workflow_id: UUID, body: NodeCreate) -> WorkflowNode:
    author = await resolve_actor(pool, caller)
    require(author, "contributor")
    async with pool.acquire() as conn, conn.transaction():
        await _require_draft(conn, workflow_id)
        if await store.node_by_ref(conn, workflow_id, body.node_ref):
            raise InvalidNodeRef(body.node_ref)
        return _node_out(await store.insert_node(conn, workflow_id, body))


async def add_connection(
    pool, caller: Caller, workflow_id: UUID, body: ConnectionCreate
) -> WorkflowConnection:
    """Edges name their endpoints by `node_ref`, not by id.

    A client that has just posted two nodes knows the refs it chose; making it
    keep the returned ids to draw an edge between them is bookkeeping the
    server can do. The refs are unique per workflow, which is what makes it
    unambiguous.
    """
    author = await resolve_actor(pool, caller)
    require(author, "contributor")
    async with pool.acquire() as conn, conn.transaction():
        await _require_draft(conn, workflow_id)
        source = await store.node_by_ref(conn, workflow_id, body.source_node_ref)
        target = await store.node_by_ref(conn, workflow_id, body.target_node_ref)
        if source is None or target is None:
            raise NodeNotFound
        row = await store.insert_connection(
            conn, workflow_id, source_id=source["id"], target_id=target["id"], body=body
        )
    return _connection_out(row)


async def remove_connection(pool, caller: Caller, workflow_id: UUID, connection_id: UUID) -> None:
    author = await resolve_actor(pool, caller)
    require(author, "contributor")
    async with pool.acquire() as conn, conn.transaction():
        await _require_draft(conn, workflow_id)
        if (await store.delete_connection(conn, workflow_id, connection_id)) == "DELETE 0":
            raise ConnectionNotFound


# ── validation and activation ─────────────────────────────────────────────────

async def validate_workflow(pool, caller: Caller, workflow_id: UUID) -> ValidationOut:
    """A dry run. Says what activation would refuse, and changes nothing.

    Separate from activation because the two questions are asked at different
    moments: an editor wants to know while building, and a release wants to
    know at the last possible instant. Answering the first by attempting the
    second would activate workflows by accident.
    """
    require(await resolve_actor(pool, caller), "consumer")
    if await store.workflow(pool, workflow_id) is None:
        raise WorkflowNotFound

    violations = await _violations(pool, workflow_id)
    return ValidationOut(
        workflow_id=workflow_id,
        valid=not violations,
        violations=[ViolationOut(**v.__dict__) for v in violations],
    )


async def _violations(conn_or_pool, workflow_id: UUID) -> list[analysis.Violation]:
    """Everything wrong with this graph, in-workflow rules and the call graph.

    The cycle check is separate in `graph.py` because it needs a second query;
    it is joined back here, where reading is allowed, so that a caller asking
    "is this workflow releasable" gets one answer rather than two.
    """
    graph = await _load_graph(conn_or_pool, workflow_id)
    violations = analysis.validate(graph)

    # Guards are checked here and not in graph.py: `analysis.validate` works on
    # the reduced graph and knows nothing about what a condition is. Checking
    # at activation is the whole point of choosing data over an expression —
    # the answer arrives while somebody is looking at the workflow, instead of
    # failing a run half way through (CHY-130).
    for edge in graph.edges:
        if edge.condition is None:
            continue
        try:
            conditions.check(edge.condition)
        except MalformedCondition as exc:
            violations.append(analysis.Violation(
                rule="malformed_condition",
                message=str(exc),
                connection_id=edge.id,
            ))

    calls: dict[UUID, set[UUID]] = {}
    for row in await store.sub_workflow_calls(conn_or_pool):
        calls.setdefault(row["workflow_id"], set()).add(row["target_workflow_id"])
    for cycle in analysis.sub_workflow_cycles(calls, workflow_id):
        violations.append(analysis.Violation(
            rule="sub_workflow_cycle",
            message=SubWorkflowCycle(cycle).args[0],
        ))
    return violations


async def set_workflow_status(
    pool, caller: Caller, workflow_id: UUID, body: WorkflowStatusPut
) -> WorkflowDefinition:
    """Release or retire a workflow.

    Activation validates first, inside the same transaction that reads the
    status FOR UPDATE. Validating outside it would check a graph that another
    request could still change before the status is written — the check would
    pass and the released graph would be a different one.

    Retiring is not validated. A workflow already active is being taken out of
    service, and refusing that because its graph has meanwhile become invalid
    would trap the exact workflow somebody wants to stop.
    """
    performer = await resolve_actor(pool, caller)
    require(performer, "reviewer")

    async with pool.acquire() as conn, conn.transaction():
        current = await store.lock_workflow(conn, workflow_id)
        if current is None:
            raise WorkflowNotFound

        if body.status == "active":
            violations = await _violations(conn, workflow_id)
            if violations:
                raise WorkflowInvalid(violations)

        row = await store.set_status(conn, workflow_id, body.status)
        await write_audit(
            conn,
            action=(ACTION_WORKFLOW_ACTIVATED if body.status == "active"
                    else ACTION_WORKFLOW_DEPRECATED),
            actor_id=performer.id,
            target=target_ref("workflow", workflow_id),
            payload={"from_status": current["status"], "to_status": body.status,
                     "version": current["version"]},
        )
    return _definition_out(row)

# ── running a workflow (CHY-127) ──────────────────────────────────────────────

#: How many rounds one `advance` will take before giving up.
#:
#: loop_limit already bounds a well-formed graph, so reaching this means a bug
#: in the state machine rather than a runaway workflow — and a bug that spins
#: is worse than one that stops. Named rather than inlined so the number is
#: arguable instead of magic.
MAX_ROUNDS = 200


@dataclass(frozen=True)
class Advance:
    """What one call to `advance_run` achieved."""

    run: WorkflowRunOut
    rounds: int
    executed: tuple[str, ...] = ()


def _run_out(row) -> WorkflowRunOut:
    d = dict(row)
    for column in ("input_data", "result"):
        if isinstance(d.get(column), str):
            d[column] = json.loads(d[column])
    return WorkflowRunOut(**d)


async def _load_state(conn, run_id: UUID) -> runner.RunState:
    return runner.rebuild_state(
        await store.executions(conn, run_id),
        await store.traversals(conn, run_id),
    )


@dataclass(frozen=True)
class _NodeDetail:
    """A node's own configuration and how it addresses its inputs.

    Kept out of `graph.Node` on purpose. The graph is what the state machine
    reads, and the state machine must stay decidable without a database — a
    routing rule that could reach into a node's configuration would end that
    property quietly, on the day someone finds it convenient.
    """

    config: dict
    #: Only a context_compose node has one; every other type stores NULL,
    #: which the database enforces (022). None means "everything the scope
    #: offers", not "nothing" — the difference the empty list would blur.
    context_requirements: ContextRequirements | None
    #: Only an llm_call has one, and it must (023). The key its model is
    #: routed by — carried from the row, never defaulted to the node type.
    task_type: str | None
    input_mapping: dict


#: Stands in for a node the details query did not return. Every node has a row
#: with NOT NULL DEFAULT '{}' (018), so this is unreachable through the API —
#: but a KeyError here would surface as a failed run with a traceback instead
#: of an empty configuration, and an empty configuration is what the schema
#: promises.
_NO_DETAIL = _NodeDetail(
    config={}, context_requirements=None, task_type=None, input_mapping={}
)


async def _load_node_details(conn, workflow_id: UUID) -> dict[UUID, _NodeDetail]:
    """Configuration and input mapping for every node of a workflow."""
    return {
        row["id"]: _NodeDetail(
            config=_as_mapping(row["config"]),
            context_requirements=_as_requirements(row["context_requirements"]),
            task_type=row["task_type"],
            input_mapping=_as_mapping(row["input_mapping"]),
        )
        for row in await store.node_details(conn, workflow_id)
    }


async def _node_input(conn, graph, run_row, node_id: UUID,
                      details: dict[UUID, _NodeDetail]) -> dict:
    """What this pass of this node receives.

    Assembled at **claim** time and written to `node_executions.input_data`,
    which makes it the record of what the node actually saw rather than a
    reconstruction of what it probably saw. On a loop those differ: the third
    pass read what its predecessor produced on the third pass, and by the time
    anyone asks, a fourth pass has replaced the answer a reconstruction would
    find.
    """
    edges = graph.predecessors(node_id)
    upstream: dict[str, dict] = {}
    if edges:
        rows = await store.predecessor_outputs(
            conn, run_row["id"], [edge.source for edge in edges]
        )
        upstream = {
            graph.ref(row["node_id"]): _as_mapping(row["output_data"])
            for row in rows
        }
    return dataflow.build_input(
        run_input=dict(run_row["input_data"] or {}),
        upstream=upstream,
        input_mapping=details.get(node_id, _NO_DETAIL).input_mapping,
    )


@dataclass(frozen=True)
class _Claim:
    """A node written down as started, with everything needed to run it.

    Carried from `_claim` to the executor rather than re-read there. The two
    happen in different transactions, and a second read could see a different
    world — the point of claiming is that this pass is decided.
    """

    step: object
    execution_id: UUID
    input_data: dict
    config: dict
    context_requirements: ContextRequirements | None
    task_type: str | None
    scope_id: object


async def _claim(conn, graph, run_row, steps) -> list[_Claim]:
    """Write down that these nodes have started, and spend their tokens.

    In its own transaction, and **before** anything runs. Two reasons, and both
    are about what a crash leaves behind:

    A node that ran before leaving a trace could be handed out again by a
    concurrent advance — `in_flight` is read back from exactly these rows.

    And tokens are spent by *starting*, not by finishing. A node that died
    halfway would otherwise leave its token in place and arm itself again on
    the next advance, which is an infinite loop wearing the costume of a retry.
    """
    details = await _load_node_details(conn, run_row["workflow_id"])
    # The scope lives on the definition, not on the run — read once per round
    # rather than once per node, since every node of a run shares it.
    definition = await store.workflow(conn, run_row["workflow_id"])
    scope_id = definition["scope_id"] if definition else None
    claimed = []
    for step in steps:
        consumed = list(runner.consume(graph, await _load_state(conn, run_row["id"]), step.node_id))
        node_input = await _node_input(conn, graph, run_row, step.node_id, details)
        execution_id = await store.start_execution(conn, store.NewExecution(
            run_id=run_row["id"], node_id=step.node_id, iteration=step.iteration,
            consumed=consumed, input_data=node_input,
        ))
        detail = details.get(step.node_id, _NO_DETAIL)
        claimed.append(_Claim(
            step=step, execution_id=execution_id["id"], input_data=node_input,
            config=detail.config, context_requirements=detail.context_requirements,
            task_type=detail.task_type, scope_id=scope_id,
        ))
    return claimed


async def advance_run(pool, run_id: UUID, *, registry: ExecutorRegistry) -> Advance:
    """Move a run as far as it will go, then stop.

    The one operation this module offers, and the reason there is only one:
    **starting a run and resuming it after a review are the same thing.** A
    gate is a row, so continuing means rebuilding the state and asking the
    machine what is next — exactly what starting does. Two functions would be
    two paths through the state machine, and the second one would be the one
    nobody exercises.

    Each round is two transactions, not one:

      1. lock the run, rebuild its state, plan, and claim the nodes to run
      2. …run them, outside any transaction…
      3. lock again, record the results, place the tokens they produce

    An LLM call inside an open transaction would hold a row lock for the length
    of a model's answer. The lock is there to make "decide and claim" atomic,
    which takes milliseconds; doing the work is not part of that.

    A node that raises does not take the run down quietly: its execution is
    recorded as `failed`, the run becomes `failed`, and the message survives.
    The alternative — swallowing it and carrying on — would produce a run that
    completed without doing its work, which is indistinguishable from success.
    """
    executed: list[str] = []

    for round_number in range(1, MAX_ROUNDS + 1):
        async with pool.acquire() as conn, conn.transaction():
            run_row = await store.lock_run(conn, run_id)
            if run_row is None:
                raise RunNotFound
            if run_row["status"] in _TERMINAL_RUN_STATES:
                return Advance(_run_out(run_row), round_number - 1, tuple(executed))

            graph = await _load_graph(conn, run_row["workflow_id"])
            state = await _load_state(conn, run_id)
            decision = runner.plan(graph, state)

            if decision.outcome is runner.Outcome.COMPLETE:
                row = await store.set_run_status(conn, run_id, "complete", result={})
                return Advance(_run_out(row), round_number - 1, tuple(executed))

            if decision.outcome is runner.Outcome.AWAIT_REVIEW:
                # The gate is claimed like any other node, and that is not
                # bookkeeping. Three things depend on it: the audit trail says
                # the run *reached* the gate rather than merely stopping; its
                # tokens are spent, so a second advance does not plan it again;
                # and releasing it has a row to complete. Without the claim,
                # the decision would have nothing to attach to and the run
                # would show a gap exactly where a human intervened.
                await _claim(conn, graph, run_row, decision.steps)
                row = await store.set_run_status(conn, run_id, "awaiting_oversight")
                return Advance(_run_out(row), round_number - 1, tuple(executed))

            if not decision.steps:
                # RUN with nothing to start: work is in flight that this call
                # did not launch. Returning rather than spinning — whoever owns
                # that node will advance the run when it finishes.
                return Advance(_run_out(run_row), round_number - 1, tuple(executed))

            claimed = await _claim(conn, graph, run_row, decision.steps)

        for claim in claimed:
            node = graph.node(claim.step.node_id)
            executed.append(node.node_ref)
            await _execute_and_record(
                pool, run_id,
                _Job(graph=graph, node=node, claim=claim, run_row=run_row),
                registry=registry,
            )

    raise RunDidNotSettle(run_id, MAX_ROUNDS)


@dataclass(frozen=True)
class _Job:
    """One claimed node, ready to run.

    The values always travel together — they are what `_claim` handed back —
    and bundling them is what keeps the function below at a size where its
    argument list is readable rather than counted.
    """

    graph: object
    node: object
    claim: _Claim
    run_row: object


async def _execute_and_record(
    pool, run_id: UUID, job: _Job, *, registry: ExecutorRegistry
) -> None:
    """Run one node and write down where the run goes next.

    The execution happens outside a transaction; only recording it takes one.
    A failure is recorded the same way a success is — the run stops, and it
    stops saying why. That holds for **every** exception, not only for this
    context's own: an executor calls into routing, completion and the
    repository, and until CHY-145 their errors passed through and left the run
    at `running` forever. Expected failures arrive as `OrchestrationError` and
    end here; anything else is recorded and then raised again.
    """
    node, claim = job.node, job.claim
    step = claim.step
    ctx = NodeContext(
        run_id=run_id, workflow_id=job.run_row["workflow_id"],
        node_id=node.id, node_ref=node.node_ref,
        node_type=node.node_type, iteration=step.iteration,
        config=claim.config, context_requirements=claim.context_requirements,
        task_type=claim.task_type, input_data=claim.input_data,
        scope_id=claim.scope_id, actor_id=job.run_row["actor_id"],
    )
    try:
        result = await registry.for_type(node.node_type, node.node_ref)(ctx)
    except OrchestrationError as exc:
        await _record_failure(pool, run_id, claim.execution_id, str(exc))
        return
    except Exception as exc:
        # Not an expected failure, so it is raised again — a bug has to stay
        # loud. But first it is written down. The execution was claimed in its
        # own committed transaction, so an exception that simply passed through
        # here left the run and the execution at `running` for good: no
        # status, no message, and nothing that would ever advance it (CHY-145,
        # shown with a ModelRefused from an llm_call before the fix).
        await _record_failure(
            pool, run_id, claim.execution_id, f"{type(exc).__name__}: {exc}"
        )
        raise

    conditions = {}
    for edge in job.graph.successors(node.id):
        if edge.has_condition:
            conditions[edge.id] = await registry.evaluate_condition(
                edge.id, edge.condition, result
            )

    async with pool.acquire() as conn, conn.transaction():
        state = await _load_state(conn, run_id)
        firing = runner.fire(job.graph, state, node.id, conditions=conditions)
        await store.complete_execution(
            conn, claim.execution_id,
            output=dict(result.output), model_used=result.model_used,
        )
        if firing.failure:
            await store.set_run_status(conn, run_id, "failed", error=firing.failure)
            return
        await store.record_traversals(conn, run_id, list(firing.taken))


async def _record_failure(pool, run_id: UUID, execution_id: UUID, message: str) -> None:
    """End a claimed execution and its run as failed, saying why."""
    async with pool.acquire() as conn, conn.transaction():
        await store.fail_execution(conn, execution_id, message)
        await store.set_run_status(conn, run_id, "failed", error=message)


async def start_workflow_run(
    pool, caller: Caller, workflow_id: UUID, body: WorkflowRunStart, *, registry: ExecutorRegistry
) -> Advance:
    """Create a run of an active workflow and push it as far as it goes.

    Only `active`. A draft has not been through the validator, and the
    validator is what guarantees the three mandatory nodes and a limit on every
    back edge — running a draft would be running a graph nobody released
    (ADR-004, CHY-82).

    The version is pinned onto the run at creation. `workflow_definitions.id`
    already identifies one version row, but carrying the number as well means
    an audit can answer "which version ran" without resolving a foreign key
    into a table whose rows may since have been retired.
    """
    starter = await resolve_actor(pool, caller)
    require(starter, "contributor")

    async with pool.acquire() as conn, conn.transaction():
        definition = await store.workflow(conn, workflow_id)
        if definition is None:
            raise WorkflowNotFound
        if definition["status"] != "active":
            raise WorkflowNotRunnable(definition["status"])

        row = await store.insert_run(conn, store.NewRun(
            workflow_id=workflow_id, workflow_version=definition["version"],
            actor_id=starter.id, input_data=dict(body.input_data),
        ))
        await write_audit(
            conn, action=ACTION_RUN_STARTED, actor_id=starter.id,
            target=target_ref("workflow_run", row["id"]),
            payload={"workflow_id": str(workflow_id),
                     "workflow_version": definition["version"]},
        )
    return await advance_run(pool, row["id"], registry=registry)


async def get_run(pool, caller: Caller, run_id: UUID) -> WorkflowRunOut:
    require(await resolve_actor(pool, caller), "consumer")
    row = await store.run(pool, run_id)
    if row is None:
        raise RunNotFound
    return _run_out(row)


#: How many different people must approve before a gate completes, by
#: `review_role` (CHY-176). A role not listed — `approver`, or none set — needs
#: one. Written as data so the rule reads as a rule, not as a branch.
APPROVALS_REQUIRED: dict[str | None, int] = {"four_eyes": 2}


async def record_oversight(
    pool, caller: Caller, run_id: UUID, body: OversightDecision, *, registry: ExecutorRegistry
) -> Advance:
    """A human decides at the gate, and the run moves on — or stops for good.

    `reviewer`, and the decision is audited with the actor. An Art. 14 record
    that cannot say who approved is not a record.

    **Rejection ends the run as `rejected`, not `failed`.** Nothing went wrong:
    a person looked and said no. Conflating the two would make a governance
    decision show up as an incident in every report that counts failures — the
    same distinction the pilots draw, and the reason `rejected` is a status of
    its own (CHY-83).

    Approval completes the gate's execution and hands the run back to
    `advance_run`. Not a second path through the state machine: continuing
    after a decision is the same operation as starting, which is the property
    the whole design rests on (ADR-028 point 2).

    **Four eyes are two people (CHY-176).** Every decision is a row in
    `review_votes`, whose primary key refuses a second decision by the same
    person. A `four_eyes` gate completes only when two different people have
    approved; the first approval is recorded and audited, and the run stays at
    the gate. Until 2026-09-30 the value was stored and never read, and the
    first approval closed every gate.
    """
    reviewer = await resolve_actor(pool, caller)
    require(reviewer, "reviewer")

    async with pool.acquire() as conn, conn.transaction():
        run_row = await store.lock_run(conn, run_id)
        if run_row is None:
            raise RunNotFound
        if run_row["status"] != AWAITING:
            raise RunNotAwaitingOversight(run_row["status"])

        gate = await store.running_execution(conn, run_id)
        if gate is None:
            # The run says it is waiting and no node is. Refused rather than
            # guessed at: approving a gate that was never claimed would let the
            # run continue from a position nobody recorded.
            raise OversightGateMissing(run_id)

        # The vote first: a second decision by the same person is refused by
        # the table before anything is audited (CHY-176).
        await store.record_vote(conn, gate["id"], reviewer.id, body.decision, body.reason)
        required = APPROVALS_REQUIRED.get(await store.review_role(conn, gate["node_id"]), 1)
        approved = (await store.approvals(conn, gate["id"])
                    if body.decision == "approved" else 0)
        await write_audit(
            conn, action=ACTION_RUN_OVERSIGHT, actor_id=reviewer.id,
            target=target_ref("workflow_run", run_id),
            payload={"decision": body.decision, "node_id": str(gate["node_id"]),
                     "approvals": approved, "required": required},
        )

        if body.decision == "rejected":
            await store.fail_execution(conn, gate["id"], body.reason or "rejected")
            row = await store.set_run_status(
                conn, run_id, "rejected", error=body.reason
            )
            return Advance(_run_out(row), 0)

        if approved < required:
            # Four eyes, first pair: recorded, audited, and the run waits for
            # a second person. Nothing is completed and nothing is fired.
            return Advance(_run_out(run_row), 0)

        await store.complete_execution(
            conn, gate["id"], output={"decision": "approved"}, model_used=None
        )
        graph = await _load_graph(conn, run_row["workflow_id"])
        state = await _load_state(conn, run_id)
        firing = runner.fire(graph, state, gate["node_id"], conditions={})
        if firing.failure:
            row = await store.set_run_status(conn, run_id, "failed", error=firing.failure)
            return Advance(_run_out(row), 0)
        await store.record_traversals(conn, run_id, list(firing.taken))
        await store.set_run_status(conn, run_id, "running")

    return await advance_run(pool, run_id, registry=registry)
