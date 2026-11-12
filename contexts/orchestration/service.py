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
  - contexts.orchestration.{graph, store, schema, errors}
  - kernel.{access, audit, identity, mapping_cipher, redaction}
"""
from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, replace
from decimal import Decimal
from uuid import UUID, uuid4

from contexts.orchestration import conditions
from contexts.orchestration import dataflow
from contexts.orchestration import graph as analysis
from contexts.orchestration import output_schema
from contexts.orchestration import runner
from contexts.orchestration import store
from contexts.orchestration import votes
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
# Re-exported for the same reason: an executor reads the run's own input under
# this key -- the model call takes `identifiers_to_redact` from there (CHY-63)
# -- and `dataflow` stays internal.
from contexts.orchestration.dataflow import RUN_INPUT
# Re-exported: the transport builds the installation's ceiling from its
# configuration and hands it in (ADR-049); `documents` stays internal.
from contexts.orchestration import documents
from contexts.orchestration.documents import DocumentLimits, DocumentUpload

__all__ = [
    "RUN_INPUT",
    "ConditionEvaluator",
    "DocumentLimits",
    "DocumentUpload",
    "ExecutorRegistry",
    "NodeExecutor",
    "NodeResult",
    "evaluate_edge_condition",
]
from contexts.orchestration.errors import (
    ConnectionNotFound,
    DocumentAlreadyReceived,
    DocumentNotExpected,
    DocumentNotFromStarter,
    MalformedCondition,
    OrchestrationError,
    GateBelowMinimum,
    NotInReviewArea,
    OversightGateMissing,
    SampleAlreadyJudged,
    ServiceAccountMayNotDecide,
    SampleContentPurged,
    SampleNotFound,
    ReviewAreaAmbiguous,
    ReviewAreaNotAtGate,
    ReviewAreaUnresolved,
    RunAlreadyFinished,
    RunContentPurged,
    RunDidNotSettle,
    RunNotAwaitingDocuments,
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
    DocumentFinding,
    NodeExecutionOut,
    OversightDecision,
    ReviewSampleDetail,
    ReviewSampleOut,
    ReviewSampleVerdict,
    RunCancelRequest,
    RunDocumentOut,
    RunDocumentsOut,
    ExhaustedLoopOut,
    RunNodesOut,
    RunResolvedOut,
    RunResolveRequest,
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
from kernel import acting, current_call, mapping_cipher, review_areas
from kernel.access import require
from kernel.audit import (
    ACTION_DIRECT_CALL_SAMPLE_REVIEWED,
    ACTION_WORKFLOW_ACTIVATED,
    ACTION_WORKFLOW_CREATED,
    ACTION_RUN_CANCELLED,
    ACTION_RUN_CONTENT_RESOLVED,
    ACTION_RUN_DOCUMENT_RECEIVED,
    ACTION_RUN_ESCALATED,
    ACTION_RUN_EXPIRED,
    ACTION_RUN_GATE_REACHED,
    ACTION_RUN_LOOP_EXHAUSTED,
    ACTION_SAMPLE_REVIEWED,
    ACTION_RUN_OVERSIGHT,
    ACTION_RUN_STARTED,
    ACTION_WORKFLOW_DEPRECATED,
    target_ref,
    write_audit,
)
from kernel.identity import Caller, resolve_actor
from kernel.redaction import (
    RedactionPolicy,
    identifiers_from,
    identifiers_in,
    merged,
    placeholders_in,
    rebased,
    redact,
    redact_structure,
    renamed,
    reserve,
    resolved,
)
from kernel import retention
from kernel.retention import Periods, RetentionClass
import steps

#: Graph changes are allowed only here. The same rule context_blocks follows:
#: what a reviewer released must not change underneath the release.
EDITABLE_STATUS = "draft"

#: A person stopped the run (CHY-200, AI Act Art. 14(4)(e)).
CANCELLED = "cancelled"

#: Nobody decided at a gate before its time limit or the content period ran out
#: (CHY-254, ADR-050).
EXPIRED = "expired"

#: A run in one of these is over; advancing it again is a no-op rather than an
#: error, so a duplicate call from a retrying client cannot restart it.
_TERMINAL_RUN_STATES: frozenset[str] = frozenset(
    {"complete", "failed", "rejected", CANCELLED, EXPIRED})

#: The one status from which a human decision is accepted.
AWAITING = "awaiting_oversight"

#: The one status in which documents are accepted, and from which no node runs
#: (ADR-049).
AWAITING_DOCUMENTS = "awaiting_documents"


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
    """The stored graph, in the shape the analysis and the runner work on.

    Reduced to what those two read — id, ref, type and join policy of a node;
    the ends, guard, label and loop_limit of an edge. Handing them full rows
    would let a future rule quietly start depending on a column nobody loads.

    `join_policy` was missing here until CHY-260 (09.11.2026). The runner's
    own tests build their graphs by hand, with the policy, so they passed;
    every stored join meanwhile behaved as `wait_any`, and a `wait_all` step
    started on its first input and read the others as missing.
    """
    node_rows = await store.nodes(conn_or_pool, workflow_id)
    edge_rows = await store.connections(conn_or_pool, workflow_id)
    return analysis.Graph(
        nodes=[
            analysis.Node(id=r["id"], node_ref=r["node_ref"], node_type=r["node_type"],
                          join_policy=r["join_policy"])
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

async def validate_workflow(pool, caller: Caller, workflow_id: UUID, *,
                            limits: DocumentLimits) -> ValidationOut:
    """A dry run. Says what activation would refuse, and changes nothing.

    Separate from activation because the two questions are asked at different
    moments: an editor wants to know while building, and a release wants to
    know at the last possible instant. Answering the first by attempting the
    second would activate workflows by accident.

    `limits` is the installation's ceiling for documents, which a document
    node may not exceed (ADR-049).
    """
    require(await resolve_actor(pool, caller), "consumer")
    if await store.workflow(pool, workflow_id) is None:
        raise WorkflowNotFound

    violations = await _violations(pool, workflow_id, limits)
    return ValidationOut(
        workflow_id=workflow_id,
        valid=not violations,
        violations=[ViolationOut(**v.__dict__) for v in violations],
    )


async def _violations(conn_or_pool, workflow_id: UUID,
                      limits: DocumentLimits) -> list[analysis.Violation]:
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

    # A compute node names code, and only the code knows whether it exists
    # (ADR-048). Refused here, at activation, not by the first run that reaches
    # the node — the acceptance of CHY-173.
    compute = {node.id: node.node_ref for node in graph.of_type("compute")}
    document = {node.id: node.node_ref for node in graph.of_type("document")}
    if compute or document or graph.of_type("human_review"):
        rows = await store.node_details(conn_or_pool, workflow_id)
        violations += _gate_violations(
            graph, rows, await _gate_areas_on_chain(conn_or_pool, workflow_id, rows),
            await _floor(conn_or_pool, workflow_id))
        for row in rows:
            if row["id"] in compute and steps.lookup(row["function"], row["function_version"]) is None:
                violations.append(analysis.Violation(
                    rule="unknown_function",
                    message=(f"{row['function']}@{row['function_version']} is not a registered "
                             f"step. Registered: {', '.join(steps.known())}."),
                    node_ref=compute[row["id"]],
                ))
            if row["id"] in document:
                violations += _above_ceiling(row, document[row["id"]], limits)

    calls: dict[UUID, set[UUID]] = {}
    for row in await store.sub_workflow_calls(conn_or_pool):
        calls.setdefault(row["workflow_id"], set()).add(row["target_workflow_id"])
    for cycle in analysis.sub_workflow_cycles(calls, workflow_id):
        violations.append(analysis.Violation(
            rule="sub_workflow_cycle",
            message=SubWorkflowCycle(cycle).args[0],
        ))
    return violations


def _escalation_edges(graph, node_id: UUID) -> frozenset[UUID]:
    """A node's outgoing edges guarded by the escalation guard (ADR-050)."""
    return frozenset(e.id for e in graph.successors(node_id)
                     if conditions.is_escalation(e.condition))


async def _floor(conn_or_pool, workflow_id: UUID) -> votes.Minimum:
    """The minimum of this workflow's gates: its scope's data class, its risk class."""
    definition = await store.workflow(conn_or_pool, workflow_id)
    return votes.minimum(await store.data_class(conn_or_pool, definition["scope_id"]),
                         definition["risk_class"])


async def _gate_areas_on_chain(conn_or_pool, workflow_id: UUID,
                               rows) -> dict[str, review_areas.Resolved]:
    """Every area a gate of this workflow names, as its scope chain defines them."""
    names = sorted({name for row in rows for name in (row.get("review_areas") or [])})
    if not names:
        return {}
    definition = await store.workflow(conn_or_pool, workflow_id)
    return await review_areas.resolve(conn_or_pool, definition["scope_id"], names)


def _gate_violations(graph, rows,
                     areas: Mapping[str, review_areas.Resolved] | None = None,
                     floor: votes.Minimum | None = None,
                     ) -> list[analysis.Violation]:
    """What activation refuses about a gate (ADR-050): its time limit, its
    areas, where it escalates to, and whether it asks for enough people.

    `areas` are the review areas the gates name, resolved on the workflow's
    scope chain; a name missing from it is defined nowhere above. `floor` is
    the minimum of the scope's data class and the workflow's risk class.
    """
    by_id = {row["id"]: row for row in rows}
    found: list[analysis.Violation] = []
    for gate in graph.of_type("human_review"):
        row = by_id.get(gate.id)
        if row is None:
            continue
        found += _timeout_violations(gate, row)
        found += _area_violations(gate, row, areas or {})
        found += _escalation_violations(graph, gate, by_id)
        if floor is not None:
            found += _level_violations(graph, gate, by_id, floor)
    return found


def _level_violations(graph, gate, by_id, floor: votes.Minimum) -> list[analysis.Violation]:
    """A gate below the minimum of its data and its risk is refused (CHY-223).

    Except one reached only after the areas of the gate before disagreed: they
    have looked, and the higher authority decides as drawn — the credit
    committee with one vote (Thomas, 09.11.2026). A gate that a timeout can
    reach is not excepted: nobody looked before it.
    """
    row = by_id[gate.id]
    rank = votes.strictness(row["review_role"], row.get("review_areas"))
    if rank >= floor.rank or _only_after_differing_votes(graph, gate, by_id):
        return []
    because = " and ".join(
        f"the data class {floor.data_class}" if source == "data_class" else
        f"the risk class {floor.risk_class or 'none (counts as high)'}"
        for source in floor.because)
    return [analysis.Violation(
        rule="review_below_minimum", node_ref=gate.node_ref,
        message=(f"{gate.node_ref} asks for {votes.LEVELS[rank]}, and {because} "
                 f"{'require' if len(floor.because) > 1 else 'requires'} at least "
                 f"{floor.level} (ADR-050). Set review_role, or name more review areas."))]


def _only_after_differing_votes(graph, gate, by_id) -> bool:
    """Whether a gate can be reached only because the areas before disagreed:
    every connection into it is an escalation, from a gate whose areas can
    differ and which does not escalate on a timeout."""
    incoming = graph.predecessors(gate.id)
    if not incoming:
        return False
    for edge in incoming:
        source = by_id.get(edge.source)
        if (source is None or not conditions.is_escalation(edge.condition)
                or source["on_timeout"] == "escalate"
                or len(source.get("review_areas") or []) < votes.AREAS_THAT_CAN_DIFFER):
            return False
    return True


def _timeout_violations(gate, row) -> list[analysis.Violation]:
    """A time limit without `on_timeout` would wait without saying what happens;
    `on_timeout` without a time limit never acts."""
    timeout, action = row["review_timeout"], row["on_timeout"]
    if timeout is not None and action is None:
        return [analysis.Violation(
            rule="timeout_without_action", node_ref=gate.node_ref,
            message=(f"{gate.node_ref} has a review_timeout but no on_timeout. Say what "
                     f"happens when nobody decides: reject (the run expires) or "
                     f"escalate (ADR-050)."))]
    if action is not None and timeout is None:
        return [analysis.Violation(
            rule="action_without_timeout", node_ref=gate.node_ref,
            message=(f"{gate.node_ref} has on_timeout but no review_timeout, so it never "
                     f"acts. Without a time limit a gate expires with the content "
                     f"period of its scope (ADR-050)."))]
    return []


def _area_violations(gate, row, areas: Mapping[str, review_areas.Resolved],
                     ) -> list[analysis.Violation]:
    """Each named area applies here and has somebody to vote for it (CHY-180).

    One vote per area, so the role may not say otherwise: `approver` with two
    or more areas promises one person where there are several, `four_eyes`
    with exactly one promises two where there is one vote.
    """
    names = row.get("review_areas") or []
    found = []
    for name in names:
        if name not in areas:
            found.append(analysis.Violation(
                rule="unknown_review_area", node_ref=gate.node_ref,
                message=(f"{gate.node_ref} asks for the review area {name!r}, which is "
                         f"defined neither on the workflow's scope nor above it "
                         f"(POST /v1/scopes/{{id}}/review-areas).")))
        elif areas[name].members == 0:
            found.append(analysis.Violation(
                rule="review_area_without_members", node_ref=gate.node_ref,
                message=(f"Nobody belongs to the review area {name!r} that {gate.node_ref} "
                         f"asks for, so the gate could never be decided.")))
    role = row["review_role"]
    if ((len(names) >= votes.AREAS_THAT_CAN_DIFFER and role == "approver")
            or (len(names) == 1 and role == "four_eyes")):
        found.append(analysis.Violation(
            rule="review_role_contradicts_areas", node_ref=gate.node_ref,
            message=(f"{gate.node_ref} names {len(names)} review area(s), one vote each, "
                     f"and review_role {role}, which says otherwise. Leave the role out: "
                     f"with areas, the areas say who decides (ADR-050).")))
    return found


def _escalation_violations(graph, gate, by_id) -> list[analysis.Violation]:
    """A gate that can escalate has an edge to go along, and it leads to a higher
    authority: a gate with an area of its own that the escalating gate does not
    name — the credit committee, with one vote (Thomas, 09.11.2026) — or else
    one that asks for at least as many people."""
    row = by_id[gate.id]
    escalations = _escalation_edges(graph, gate.id)
    found = []
    if not escalations:
        why = ("escalates on timeout" if row["on_timeout"] == "escalate" else
               "has review areas whose votes can differ"
               if len(row.get("review_areas") or []) >= votes.AREAS_THAT_CAN_DIFFER else None)
        if why:
            found.append(analysis.Violation(
                rule="escalation_without_edge", node_ref=gate.node_ref,
                message=(f"{gate.node_ref} {why} but has no escalation edge. Add an edge "
                         f"guarded by {dict(conditions.ESCALATION)} to the gate of the "
                         f"higher authority (ADR-050).")))
    source = votes.strictness(row["review_role"], row.get("review_areas"))
    for edge in graph.successors(gate.id):
        if edge.id not in escalations:
            continue
        target = graph.node(edge.target)
        target_row = by_id.get(target.id, {})
        if target.node_type != "human_review":
            found.append(analysis.Violation(
                rule="escalation_not_to_gate", node_ref=gate.node_ref,
                connection_id=edge.id,
                message=(f"The escalation edge of {gate.node_ref} leads to "
                         f"{target.node_ref}, a {target.node_type}. Escalating hands the "
                         f"decision to a higher authority: another human_review.")))
        elif (not set(target_row.get("review_areas") or []) - set(row.get("review_areas") or [])
              and votes.strictness(target_row.get("review_role"),
                                   target_row.get("review_areas")) < source):
            found.append(analysis.Violation(
                rule="escalation_to_weaker_gate", node_ref=gate.node_ref,
                connection_id=edge.id,
                message=(f"{gate.node_ref} escalates to {target.node_ref}, which asks for "
                         f"fewer people. The higher authority may not decide with less.")))
    return found


def _above_ceiling(row, node_ref: str, limits: DocumentLimits) -> list[analysis.Violation]:
    """A document node whose limits exceed the installation's ceiling (ADR-049).

    Refused at activation rather than clipped at the upload: a definition that
    promises 50 MB on an installation that takes 10 would be released saying
    something that is not true, and the first large file would find out.
    """
    found = []
    for column, ceiling, setting in (
        ("document_max_bytes", limits.max_bytes, "DOCUMENT_MAX_BYTES"),
        ("document_max_pages", limits.max_pages, "DOCUMENT_MAX_PAGES"),
    ):
        if row[column] is not None and row[column] > ceiling:
            found.append(analysis.Violation(
                rule="document_limit_above_installation",
                message=(f"{column} is {row[column]}; this installation accepts at most "
                         f"{ceiling} ({setting}). A node can narrow the installation's "
                         f"limit, not widen it."),
                node_ref=node_ref,
            ))
    return found


async def set_workflow_status(
    pool, caller: Caller, workflow_id: UUID, body: WorkflowStatusPut, *,
    limits: DocumentLimits,
) -> WorkflowDefinition:
    """Release or retire a workflow.

    Activation validates first, inside the same transaction that reads the
    status FOR UPDATE. Validating outside it would check a graph that another
    request could still change before the status is written — the check would
    pass and the released graph would be a different one.

    Retiring is not validated. A workflow already active is being taken out of
    service, and refusing that because its graph has meanwhile become invalid
    would trap the exact workflow somebody wants to stop.

    **A workflow that left the draft does not return to it (CHY-248).** Back in
    `draft` its graph could be edited again — underneath the runs that used it,
    which is the change `WorkflowNotEditable` exists to refuse. Until
    2026-11-07 the transition was accepted; the contract fuzzer reopened an
    active workflow and then failed to delete an edge its runs had counted.
    A released workflow changes by a new version (CHY-127).
    """
    performer = await resolve_actor(pool, caller)
    require(performer, "reviewer")

    async with pool.acquire() as conn, conn.transaction():
        current = await store.lock_workflow(conn, workflow_id)
        if current is None:
            raise WorkflowNotFound
        if body.status == EDITABLE_STATUS and current["status"] != EDITABLE_STATUS:
            raise WorkflowNotEditable(current["status"])

        if body.status == "active":
            violations = await _violations(conn, workflow_id, limits)
            if violations:
                raise WorkflowInvalid(violations)
            # Art. 26(6) AI Act (CHY-266): a high-risk workflow — or one nobody
            # classified, which counts as high (ADR-050) — goes live only where
            # its trace and content are kept six months.
            if current["risk_class"] in (None, "high"):
                await retention.require_high_risk_periods(conn, current["scope_id"])

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


@dataclass(frozen=True)
class Placeholders:
    """What a run's identifiers are replaced by, and the keys its mapping is
    sealed with (CHY-234).

    Handed in by the transport like the executors: the redaction rules of a
    scope come from the repository, which this context does not import
    (ADR-030), and the keys from the configuration.
    """

    policy_for: Callable[[UUID | None], Awaitable[RedactionPolicy]]
    keys: mapping_cipher.KeyRing


@dataclass(frozen=True)
class Engine:
    """What advances a run: the executors, and the pool they work in (CHY-92).

    The pool is the system pool. The engine works for the installation and sees
    every run; whether the person behind a request may start or decide one is
    checked before the engine is handed the run. The two travel together from
    the transport to `advance_run` and nowhere else, so they are one argument —
    and since CHY-234 so do the placeholders a run is stored with.
    """

    registry: ExecutorRegistry
    pool: object
    placeholders: Placeholders


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
    #: Only an llm_call may have one (039): the shape its answer is checked
    #: against before anything reads it (CHY-175).
    output_schema: dict | None
    #: Only a compute node has them, and must (040): the registered function
    #: and its pinned version (ADR-048).
    function: str | None
    function_version: int | None
    input_mapping: dict
    #: Only a document node has one, and must (041): the run's document it
    #: hands on (ADR-049).
    document_name: str | None = None


#: Stands in for a node the details query did not return. Every node has a row
#: with NOT NULL DEFAULT '{}' (018), so this is unreachable through the API —
#: but a KeyError here would surface as a failed run with a traceback instead
#: of an empty configuration, and an empty configuration is what the schema
#: promises.
_NO_DETAIL = _NodeDetail(
    config={}, context_requirements=None, task_type=None, output_schema=None,
    function=None, function_version=None, input_mapping={},
)


async def _load_node_details(conn, workflow_id: UUID) -> dict[UUID, _NodeDetail]:
    """Configuration and input mapping for every node of a workflow."""
    return {
        row["id"]: _NodeDetail(
            config=_as_mapping(row["config"]),
            context_requirements=_as_requirements(row["context_requirements"]),
            task_type=row["task_type"],
            output_schema=(_as_mapping(row["output_schema"])
                           if row["output_schema"] is not None else None),
            function=row["function"],
            function_version=row["function_version"],
            input_mapping=_as_mapping(row["input_mapping"]),
            document_name=row["document_name"],
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
    #: Checked by the service after the executor, not by the executor: an
    #: executor is injected and replaceable, the check is not (CHY-175).
    output_schema: dict | None = None
    function: str | None = None
    function_version: int | None = None
    #: The run's mapping as it stood at the claim (CHY-234); None before 036.
    run_mapping: Mapping[str, str] | None = None
    #: For a document node, its document as the run keeps it (ADR-049): read
    #: in the claim's transaction, like the node's input.
    document: Mapping | None = None
    #: The connections whose tokens this pass used up. A gate reads from them
    #: whether the run came by an escalation, and why (CHY-223).
    consumed: tuple[UUID, ...] = ()


async def _claim(conn, graph, run_row, steps, *,
                 run_mapping: Mapping[str, str] | None = None) -> list[_Claim]:
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
            task_type=detail.task_type, scope_id=scope_id, run_mapping=run_mapping,
            output_schema=detail.output_schema,
            function=detail.function, function_version=detail.function_version,
            document=await _document_of(conn, run_row["id"], detail.document_name),
            consumed=tuple(consumed),
        ))
    return claimed


async def _document_of(conn, run_id: UUID, name: str | None) -> dict | None:
    """The run's document of this name, for the document node that hands it on."""
    if name is None:
        return None
    row = await store.run_document(conn, run_id, name)
    if row is None:
        return None
    return {**dict(row), "findings": _as_list(row["findings"])}


def _as_list(raw) -> list:
    """A stored JSONB array as a list, whatever asyncpg handed back (see `_as_mapping`)."""
    if isinstance(raw, str):
        return json.loads(raw)
    return list(raw or [])


# ── the run's placeholders (CHY-234) ──────────────────────────────────────────

def _opened(row, run_id: UUID, keys: mapping_cipher.KeyRing | None) -> dict[str, str] | None:
    """The run's mapping in the clear, or None for a run started before CHY-234.

    Whether a run keeps placeholders is decided by its row, never by whether a
    key is at hand: a missing key stops the run, it never lets a value through.
    Such a run, started before migration 036, keeps its values until its
    content period ends (E6) and is replaced only at each model call.
    """
    if row is None:
        return None
    if keys is None:
        msg = f"run {run_id} keeps its placeholders, and no key was handed in to open them"
        raise mapping_cipher.MappingKeyError(msg)
    if row["ciphertext"] is None:
        msg = f"the mapping of run {run_id} was cleared with its content"
        raise mapping_cipher.MappingUnreadableError(msg)
    sealed = mapping_cipher.Sealed(row["key_id"], bytes(row["nonce"]), bytes(row["ciphertext"]))
    return mapping_cipher.unseal(sealed, run_id=run_id, keys=keys)


def _sealed(mapping: Mapping[str, str], run_id: UUID, keys: mapping_cipher.KeyRing) -> dict:
    sealed = mapping_cipher.seal(mapping, run_id=run_id, keys=keys)
    return {"key_id": sealed.key_id, "nonce": sealed.nonce, "ciphertext": sealed.ciphertext}


def _with_placeholders(run_input: Mapping, policy: RedactionPolicy) -> tuple[dict, dict, dict]:
    """The input as it is stored, the run's mapping, and the counts per category.

    Every text of the input is replaced (E2), by the scope's rules and every
    name the caller listed. The list itself is not stored -- it is what it
    protects. It goes into the mapping, so later steps still search for each
    name (kernel.redaction.reserve).
    """
    listed = identifiers_from(run_input.get("identifiers_to_redact"))
    rest = {key: value for key, value in run_input.items() if key != "identifiers_to_redact"}
    stored, redaction = redact_structure(
        rest, identifiers=merged(policy.identifiers, listed), rules=policy.rules)
    mapping = reserve(listed, known=redaction.replacements, taken=redaction.preexisting)
    return stored, mapping, redaction.counts


async def _reason_with_placeholders(conn, run_row, reason: str | None,
                                    placeholders: Placeholders) -> str | None:
    """A reason as it is stored -- given at the gate or when stopping the run
    (CHY-200) -- with the run's placeholders (E7).

    Whoever rejects easily writes the name into the reason -- "not the file of
    Ms Mustermann". Replaced like the input, by the run's mapping, the scope's
    rules and the shipped patterns, under the row lock; a value the run did not
    know yet extends its mapping. A run started before CHY-234 keeps its values
    here as everywhere (E6).
    """
    if not reason:
        return reason
    stored, _counts = await _with_run_placeholders(conn, run_row, reason, placeholders)
    return stored


async def _with_run_placeholders(conn, run_row, text: str,
                                 placeholders: Placeholders) -> tuple[str, dict[str, int]]:
    """A text as the run stores it, and what was replaced per category.

    By the run's mapping, the scope's rules, the shipped patterns and every
    value the run knows -- the names listed at its start among them --, under
    the mapping's row lock; a value the run did not know yet extends the
    mapping (E7). The run's own row is locked by the caller, first, as
    everywhere. A run without a mapping keeps its values (E6).
    """
    run_id = run_row["id"]
    current = _opened(await store.lock_pii_mapping(conn, run_id), run_id, placeholders.keys)
    if current is None:
        return text, {}
    definition = await store.workflow(conn, run_row["workflow_id"])
    policy = await placeholders.policy_for(definition["scope_id"] if definition else None)
    redaction = redact({"text": text}, rules=policy.rules, known=current,
                       identifiers=merged(policy.identifiers, identifiers_in(current)))
    if redaction.replacements != current:
        await store.update_pii_mapping(
            conn, run_id, **_sealed(redaction.replacements, run_id, placeholders.keys))
    return redaction.texts["text"], dict(redaction.counts)


async def _merged_output(conn, run_id: UUID, claim: _Claim, result: NodeResult,
                         keys: mapping_cipher.KeyRing | None) -> dict:
    """The node's output with its new names merged into the run's mapping.

    Under the row lock (E1). Another step of the run may have recorded since
    this one was claimed and handed out the same name for somebody else; then
    this step's names move, in its output as in the mapping
    (kernel.redaction.rebased).
    """
    output = dict(result.output)
    if claim.run_mapping is None or result.run_mapping is None:
        return output
    current = _opened(await store.lock_pii_mapping(conn, run_id), run_id, keys)
    if current is None:
        msg = f"the mapping of run {run_id} disappeared while a step was running"
        raise mapping_cipher.MappingUnreadableError(msg)
    mapping, renames = rebased(
        claim.run_mapping, result.run_mapping, current,
        taken=placeholders_in(output) - frozenset(result.run_mapping))
    if mapping != current:
        await store.update_pii_mapping(conn, run_id, **_sealed(mapping, run_id, keys))
    return renamed(output, renames)


async def advance_run(pool, run_id: UUID, *, registry: ExecutorRegistry,
                      keys: mapping_cipher.KeyRing | None = None) -> Advance:
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

    `keys` open the run's placeholder mapping (CHY-234). Only a run without one
    — started before migration 036 — advances without them.
    """
    executed: list[str] = []

    for round_number in range(1, MAX_ROUNDS + 1):
        async with pool.acquire() as conn, conn.transaction():
            run_row = await store.lock_run(conn, run_id)
            if run_row is None:
                raise RunNotFound
            if run_row["status"] in _TERMINAL_RUN_STATES:
                return Advance(_run_out(run_row), round_number - 1, tuple(executed))
            if run_row["status"] == AWAITING_DOCUMENTS:
                # Not started yet, and not by this: a run starts with its last
                # document (`receive_document`). Planning here would run the
                # steps that need no document while the others wait.
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
                if await _at_gates(conn, graph, run_row, decision.steps):
                    row = await store.set_run_status(conn, run_id, "awaiting_oversight")
                    return Advance(_run_out(row), round_number - 1, tuple(executed))
                # Every gate reached was `sample`: passed, and the run goes on.
                continue

            if not decision.steps:
                # RUN with nothing to start: work is in flight that this call
                # did not launch. Returning rather than spinning — whoever owns
                # that node will advance the run when it finishes.
                return Advance(_run_out(run_row), round_number - 1, tuple(executed))

            run_mapping = _opened(await store.pii_mapping(conn, run_id), run_id, keys)
            claimed = await _claim(conn, graph, run_row, decision.steps, run_mapping=run_mapping)

        for claim in claimed:
            node = graph.node(claim.step.node_id)
            executed.append(node.node_ref)
            await _execute_and_record(
                pool, run_id,
                _Job(graph=graph, node=node, claim=claim, run_row=run_row),
                registry=registry, keys=keys,
            )

    raise RunDidNotSettle(run_id, MAX_ROUNDS)


async def _at_gates(conn, graph, run_row, steps) -> bool:
    """Claim the gates the run has reached; True if it waits at one of them.

    A gate at `sample` does not hold the run (CHY-223): it passes, and a drawn
    pass goes into the review queue. Every other level waits for a person.
    """
    waiting = False
    for claim in await _claim(conn, graph, run_row, steps):
        reached = await _gate_reached(conn, graph, run_row, claim)
        if reached.level == votes.SAMPLE:
            await _pass_sample(conn, run_row, claim, drawn=reached.drawn)
        else:
            waiting = True
    return waiting


@dataclass(frozen=True)
class _Reached:
    """The level that applies at a gate, and — at `sample` — whether this pass
    was drawn for review."""

    level: str
    drawn: bool = False


async def _gate_reached(conn, graph, run_row, claim: _Claim) -> _Reached:
    """Fix the review level of a gate the run has just reached, and say why (CHY-223).

    The stricter of the gate as drawn and the minimum of its scope's data class
    and the workflow's risk class — as they are now, not as they were at the
    release, so a scope classified stricter makes its gates stricter at once.
    Except after the areas of the gate before disagreed: they have looked, and
    the higher authority decides as drawn (Thomas, 09.11.2026).

    Written to the execution, which the decision reads, and to the audit log.
    """
    node_id = claim.step.node_id
    floor = await _floor(conn, run_row["workflow_id"])
    role = await store.review_role(conn, node_id)
    areas = await store.review_areas(conn, node_id)
    gate_rank = votes.strictness(role, areas)
    after_differing = await _after_differing_votes(conn, graph, run_row["id"], claim.consumed)
    level = votes.applies(gate_rank, floor, after_differing_votes=after_differing)
    await store.set_review_level(conn, claim.execution_id, level)
    payload = {"node_ref": graph.ref(node_id), "level": level,
               "gate": votes.LEVELS[gate_rank], "minimum": floor.level,
               "because": list(floor.because), "data_class": floor.data_class,
               "risk_class": floor.risk_class, "areas": list(areas or []),
               "after_differing_votes": after_differing}
    reached = _Reached(level)
    if level == votes.SAMPLE:
        # Whether this pass was drawn stands in the entry, with the share it was
        # drawn from (ADR-050 point 6) — checkable, since the draw is a hash.
        rate = await store.review_sample_rate(conn, node_id)
        reached = _Reached(level, drawn=votes.drawn(claim.execution_id, rate))
        payload |= {"sample_rate": float(rate), "drawn": reached.drawn}
    await write_audit(
        conn, action=ACTION_RUN_GATE_REACHED, actor_id=None,
        target=target_ref("workflow_run", run_row["id"]), payload=payload,
    )
    return reached


async def _pass_sample(conn, run_row, claim: _Claim, *, drawn: bool) -> None:
    """A sample gate the run does not wait at: passed, and queued if drawn."""
    if drawn:
        await store.insert_sample(conn, claim.execution_id, run_row["id"])
    await _release(conn, run_row, {"id": claim.execution_id, "node_id": claim.step.node_id},
                   output={"decision": "sampled", "drawn": drawn})


async def _after_differing_votes(conn, graph, run_id: UUID, consumed) -> bool:
    """Whether the run reached this gate only because the areas before disagreed.

    Every connection it came by is an escalation, and every gate it came from
    escalated for `votes_differ` — not for a timeout, after which nobody had
    looked (CHY-223).
    """
    edges = [e for e in graph.edges if e.id in set(consumed)]
    if not edges or not all(conditions.is_escalation(e.condition) for e in edges):
        return False
    outputs = await store.predecessor_outputs(conn, run_id, [e.source for e in edges])
    reasons = [_as_mapping(row["output_data"]).get("reason") for row in outputs]
    return len(reasons) == len({e.source for e in edges}) and all(
        reason == "votes_differ" for reason in reasons)


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
    pool, run_id: UUID, job: _Job, *, registry: ExecutorRegistry,
    keys: mapping_cipher.KeyRing | None = None,
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
        run_mapping=claim.run_mapping,
        output_schema=claim.output_schema,
        function=claim.function, function_version=claim.function_version,
        document=claim.document,
    )
    try:
        result = await registry.for_type(node.node_type, node.node_ref)(ctx)
        # Here and not in the executor: executors are injected (ADR-028 point 3),
        # and one that forgot the check must not be able to skip it. Before the
        # edges are judged, so a guard can read what was checked (CHY-175).
        if claim.output_schema is not None:
            result = output_schema.checked(result, claim.output_schema, node.node_ref)
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
        #
        # The run keeps the type and the request's id, not the message: a
        # library's message can name an address or a value, and the run is
        # read by everyone who may read the run. The traceback is written to
        # the application log by whoever catches the raise below (CHY-97).
        await _record_failure(
            pool, run_id, claim.execution_id,
            current_call.with_reference(
                f"unexpected error ({type(exc).__name__}), details in the application log"
            ),
        )
        raise

    conditions = {}
    for edge in job.graph.successors(node.id):
        if edge.has_condition:
            conditions[edge.id] = await registry.evaluate_condition(
                edge.id, edge.condition, result
            )

    try:
        await _record(pool, job, result, conditions, keys=keys)
    except (mapping_cipher.MappingKeyError, mapping_cipher.MappingUnreadableError) as exc:
        # Written down before it is raised, as for an executor's exception: a
        # mapping that does not open must not leave the run at `running`.
        await _record_failure(
            pool, run_id, claim.execution_id, f"{type(exc).__name__}: {exc}"
        )
        raise


async def _record(pool, job: _Job, result: NodeResult, conditions: dict, *,
                  keys: mapping_cipher.KeyRing | None) -> None:
    """Write down what one node produced and where the run goes next.

    Under the run's lock, and that is not caution for its own sake: a person
    may have stopped the run while this node was working (CHY-200). Its result
    is then dropped — the execution stays `cancelled`, the run stays stopped,
    and nothing it produced moves the run on.
    """
    node, claim, run_id = job.node, job.claim, job.run_row["id"]
    async with pool.acquire() as conn, conn.transaction():
        if await _stopped_meanwhile(conn, run_id):
            return
        output = await _merged_output(conn, run_id, claim, result, keys)
        state = await _load_state(conn, run_id)
        firing = runner.fire(job.graph, state, node.id, conditions=conditions)
        await store.complete_execution(
            conn, claim.execution_id, output=output, model_used=result.model_used,
        )
        await _record_exhausted(conn, job.graph, run_id, node.id, firing)
        if firing.failure:
            await store.set_run_status(conn, run_id, "failed", error=firing.failure)
            return
        await store.record_traversals(conn, run_id, list(firing.taken))


async def _record_exhausted(conn, graph: analysis.Graph, run_id: UUID, node_id: UUID,
                            firing: runner.Firing) -> None:
    """One audit entry per loop that used up its limit at this node (CHY-150).

    Written whatever happens next — the run going on along a default edge, on
    along another branch, or failing — because whoever decides at the gate
    asks whether the criterion was met or only the budget spent, and the
    counter in `loop_counters` answers that only to somebody who knows to
    compare it with the limit. In the caller's transaction, with the state
    change it explains.
    """
    if not firing.exhausted:
        return
    edges = {e.id: e for e in graph.successors(node_id)}
    went_on = sorted(graph.ref(edges[t].target) for t in firing.taken if t in edges)
    for edge_id in firing.exhausted:
        edge = edges[edge_id]
        await write_audit(
            conn, action=ACTION_RUN_LOOP_EXHAUSTED, actor_id=None,
            target=target_ref("workflow_run", run_id),
            payload={"node_ref": graph.ref(node_id), "to": graph.ref(edge.target),
                     "loop_limit": edge.loop_limit, "went_on_to": went_on,
                     "run_failed": firing.failure is not None},
        )


async def _record_failure(pool, run_id: UUID, execution_id: UUID, message: str) -> None:
    """End a claimed execution and its run as failed, saying why — unless a
    person stopped the run meanwhile (CHY-200): then both stay `cancelled`."""
    async with pool.acquire() as conn, conn.transaction():
        if await _stopped_meanwhile(conn, run_id):
            return
        await store.fail_execution(conn, execution_id, message)
        await store.set_run_status(conn, run_id, "failed", error=message)


async def _stopped_meanwhile(conn, run_id: UUID) -> bool:
    """Lock the run; True if a person stopped it while a node was working.

    The lock comes before the mapping's, as everywhere: run first, then
    mapping, so two writers never wait on each other in opposite order.
    """
    run_row = await store.lock_run(conn, run_id)
    return run_row is None or run_row["status"] == CANCELLED


async def start_workflow_run(
    pool, caller: Caller, workflow_id: UUID, body: WorkflowRunStart, *, engine: Engine,
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

    The run is created as the starter — row-level security admits it because
    they are its starter (CHY-92) — and then advanced by the `engine` in the
    system context: the engine works for the installation, and the starter's
    right to run the workflow is what was checked here.

    **The run is stored with placeholders** (CHY-234). Its input is replaced
    before the first row is written, the mapping back to the values is sealed
    into `pii_mappings` in the same transaction, and from here on every step
    reads and writes placeholders. The audit entry counts what was replaced,
    per category — counts, never values.

    **A workflow with document nodes does not start here** (ADR-049). The run
    is created as `awaiting_documents`, the audit entry names the documents it
    waits for, and nothing runs until the last of them has arrived
    (`receive_document`). Its mapping exists from now on, so each document is
    stored with the same placeholders as the input.
    """
    starter = await resolve_actor(pool, caller)
    require(starter, "contributor")

    async with acting.acting_as(pool, starter.id) as conn, conn.transaction():
        definition = await store.workflow(conn, workflow_id)
        if definition is None:
            raise WorkflowNotFound
        if definition["status"] != "active":
            raise WorkflowNotRunnable(definition["status"])

        expected = [r["document_name"] for r in await store.document_nodes(conn, workflow_id)]
        policy = await engine.placeholders.policy_for(definition["scope_id"])
        stored_input, mapping, counts = _with_placeholders(body.input_data, policy)
        row = await store.insert_run(conn, store.NewRun(
            workflow_id=workflow_id, workflow_version=definition["version"],
            actor_id=starter.id, input_data=stored_input,
            awaits_documents=bool(expected),
        ))
        await store.insert_pii_mapping(
            conn, row["id"], **_sealed(mapping, row["id"], engine.placeholders.keys))
        await write_audit(
            conn, action=ACTION_RUN_STARTED, actor_id=starter.id,
            target=target_ref("workflow_run", row["id"]),
            payload={"workflow_id": str(workflow_id),
                     "workflow_version": definition["version"],
                     "redactions": counts,
                     **({"documents_expected": expected} if expected else {})},
        )
    if expected:
        return Advance(_run_out(row), 0)
    return await advance_run(engine.pool, row["id"], registry=engine.registry,
                             keys=engine.placeholders.keys)


# ── documents of a run (CHY-174, ADR-049) ─────────────────────────────────────

async def run_documents(pool, caller: Caller, run_id: UUID) -> RunDocumentsOut:
    """Which documents a run expects, and what arrived for each (ADR-049).

    Visible to whoever sees the run (CHY-92). Without the text: what a document
    says is read by the steps it reaches, and with the values by whoever may
    resolve the run's content (CHY-234) — not by a listing.
    """
    reader = await resolve_actor(pool, caller)
    require(reader, "consumer")
    async with acting.acting_as(pool, reader.id) as conn:
        row = await store.run(conn, run_id)
        if row is None:
            raise RunNotFound
        expected = [r["document_name"] for r in await store.document_nodes(conn, row["workflow_id"])]
        received = {r["name"]: r for r in await store.run_documents(conn, run_id)}
    return RunDocumentsOut(
        run_id=run_id, status=row["status"],
        documents=[_document_out(name, received.get(name)) for name in expected],
    )


def _document_out(name: str, row) -> RunDocumentOut:
    if row is None:
        return RunDocumentOut(name=name, received=False)
    return RunDocumentOut(
        name=name, received=True, media_type=row["media_type"], bytes=row["bytes"],
        pages=row["pages"], characters=row["characters"],
        findings=[DocumentFinding(**f) for f in _as_list(row["findings"])],
        received_at=row["received_at"],
    )


@dataclass(frozen=True)
class _Expected:
    """A document node of the run's definition: what it accepts."""

    max_bytes: int | None
    max_pages: int | None
    all_names: tuple[str, ...]


async def _check_upload(conn, uploader_id: UUID, run_row, name: str) -> _Expected:
    """Everything that refuses a document before its bytes are read.

    Asked twice: before the file is parsed, so a document that will be refused
    costs no parsing, and again under the run's lock, because between the two
    the run may have been stopped, cleared or completed by another upload.
    """
    if run_row is None:
        raise RunNotFound
    if run_row["actor_id"] != uploader_id:
        raise DocumentNotFromStarter
    if run_row["content_purged_at"] is not None:
        raise RunContentPurged
    if run_row["status"] != AWAITING_DOCUMENTS:
        raise RunNotAwaitingDocuments(run_row["status"])
    nodes = {r["document_name"]: r for r in await store.document_nodes(conn, run_row["workflow_id"])}
    node = nodes.get(name)
    if node is None:
        raise DocumentNotExpected(name, sorted(nodes))
    if any(r["name"] == name for r in await store.run_documents(conn, run_row["id"])):
        raise DocumentAlreadyReceived(name)
    return _Expected(node["document_max_bytes"], node["document_max_pages"], tuple(sorted(nodes)))


async def receive_document(pool, caller: Caller, upload: DocumentUpload, *,
                           engine: Engine, limits: DocumentLimits) -> RunDocumentsOut:
    """Take one document of a run, and start the run with the last one (ADR-049).

    **Only the starter, only while the run waits, only a document its
    definition expects, each once.** There is no upload token: the binding of a
    document is its run, and the run is its starter's (decided 08.11.2026).

    **Read once, scanned once, here.** The file is turned into text
    (`documents.read`: media type, the node's limits under the installation's,
    no text layer refused) outside any transaction, then scanned
    (`core.ingress`), then stored with the run's placeholders — the run's
    mapping exists since its start, so the same person keeps the same
    placeholder in the input and in every document, and the names listed at
    the start are replaced here as well. The file itself is not kept.

    **Audited also when the scan found nothing**: name, size, pages, what was
    replaced per category, the findings by kind and rule — never an excerpt. A
    finding does not stop the run; the text reaches every step fenced, as in
    the pilot (CHY-135).

    **The last document starts the run**, in this call, as `start_workflow_run`
    would have: the run's lock makes sure exactly one upload does, however
    many arrive at once.
    """
    run_id, name = upload.run_id, upload.name
    uploader = await resolve_actor(pool, caller)
    require(uploader, "consumer")
    async with acting.acting_as(pool, uploader.id) as conn:
        expected = await _check_upload(conn, uploader.id, await store.run(conn, run_id), name)

    document = documents.read(upload.data, upload.content_type, installation=limits,
                              node_max_bytes=expected.max_bytes,
                              node_max_pages=expected.max_pages)
    found = documents.findings(document.text)

    async with acting.acting_as(pool, uploader.id) as conn, conn.transaction():
        run_row = await store.lock_run(conn, run_id)
        await _check_upload(conn, uploader.id, run_row, name)
        text, counts = await _with_run_placeholders(conn, run_row, document.text,
                                                    engine.placeholders)
        await store.insert_document(conn, store.NewDocument(
            run_id=run_id, name=name, media_type=document.media_type,
            bytes=document.bytes, pages=document.pages,
            characters=len(document.text), text=text, findings=found,
        ))
        await write_audit(
            conn, action=ACTION_RUN_DOCUMENT_RECEIVED, actor_id=uploader.id,
            target=target_ref("workflow_run", run_id),
            payload={"name": name, "media_type": document.media_type,
                     "bytes": document.bytes, "pages": document.pages,
                     "characters": len(document.text), "redactions": counts,
                     "findings": found},
        )
        arrived = {r["name"] for r in await store.run_documents(conn, run_id)}
        complete = arrived >= set(expected.all_names)
        if complete:
            await store.start_waiting_run(conn, run_id)

    if complete:
        await advance_run(engine.pool, run_id, registry=engine.registry,
                          keys=engine.placeholders.keys)
    return await run_documents(pool, caller, run_id)


async def get_run(pool, caller: Caller, run_id: UUID) -> WorkflowRunOut:
    """A run, if the caller may see it (CHY-92); otherwise as if it did not exist."""
    reader = await resolve_actor(pool, caller)
    require(reader, "consumer")
    async with acting.acting_as(pool, reader.id) as conn:
        row = await store.run(conn, run_id)
    if row is None:
        raise RunNotFound
    return _run_out(row)


async def run_visible(pool, actor_id: UUID, run_id: UUID) -> bool:
    """Whether this actor sees the run — the same row security as reading it (CHY-92).

    For callers outside this context that need the answer, not the run: the
    audit log hands a run's history to exactly the people who see the run
    (CHY-303).
    """
    async with acting.acting_as(pool, actor_id) as conn:
        return await store.run_visible(conn, run_id)


async def direct_sample_visible(pool, actor_id: UUID, sample_id: UUID) -> bool:
    """Whether this actor sees the drawn direct call (CHY-269); see run_visible."""
    async with acting.acting_as(pool, actor_id) as conn:
        return await store.direct_sample_visible(conn, sample_id)


async def run_nodes(pool, caller: Caller, run_id: UUID) -> RunNodesOut:
    """What each node of a run produced, in the order it ran (CHY-229).

    The same role as reading the run itself: whoever may see its input may see
    what was made of it. Read this before deciding at a `human_review` gate —
    the decision is about this output. Visible to the same people as the run
    itself (CHY-92): starter, members of the workflow's scope chain, admin.
    """
    reader = await resolve_actor(pool, caller)
    require(reader, "consumer")
    async with acting.acting_as(pool, reader.id) as conn:
        row = await store.run(conn, run_id)
        if row is None:
            raise RunNotFound
        history = await store.execution_history(conn, run_id)
        graph = await _load_graph(conn, row["workflow_id"])
        counted = {r["connection_id"]: r["traversals"]
                   for r in await store.traversals(conn, run_id)}
    return RunNodesOut(run_id=run_id, status=row["status"],
                       nodes=[_execution_out(r) for r in history],
                       exhausted_loops=_exhausted_loops(graph, counted))


def _exhausted_loops(graph: analysis.Graph, counted: dict[UUID, int]) -> list[ExhaustedLoopOut]:
    """The loops whose counter reached their limit (CHY-150).

    Derived, not stored: `loop_counters` is the truth, and the audit entry
    `workflow.loop_exhausted` the event. Shown with the outputs because that is
    what a person reads before deciding at a gate.
    """
    return [ExhaustedLoopOut(from_ref=graph.ref(e.source), to_ref=graph.ref(e.target),
                             loop_limit=e.loop_limit)
            for e in graph.edges
            if e.loop_limit is not None and counted.get(e.id, 0) >= e.loop_limit]


def _execution_out(row) -> NodeExecutionOut:
    d = dict(row)
    output = d.pop("output_data")
    d["output"] = json.loads(output) if isinstance(output, str) else output
    return NodeExecutionOut(**d)


async def resolve_run(pool, caller: Caller, run_id: UUID, body: RunResolveRequest, *,
                      keys: mapping_cipher.KeyRing) -> RunResolvedOut:
    """A run's input and its steps' outputs with the values put back (CHY-234).

    For whoever started the run, and from `reviewer` up for whoever sees it
    (CHY-92): at the gate one has to be able to check that the right person is
    meant (E4, decided 06.11.2026). A run the caller does not see is not found,
    as everywhere; one they see without the role is refused. Everyone else
    reads placeholders.

    Every resolution is audited in the same transaction as the reading -- who,
    which run, which steps; never a value.
    """
    reader = await resolve_actor(pool, caller)
    require(reader, "consumer")
    async with acting.acting_as(pool, reader.id) as conn, conn.transaction():
        row = await store.run(conn, run_id)
        if row is None:
            raise RunNotFound
        if row["actor_id"] != reader.id:
            require(reader, "reviewer")
        if row["content_purged_at"] is not None:
            raise RunContentPurged
        # None for a run started before CHY-234: stored with its values, it is
        # returned as it is.
        mapping = _opened(await store.pii_mapping(conn, run_id), run_id, keys) or {}
        nodes = [
            out.model_copy(update={"output": resolved(out.output, mapping),
                                   "error_message": resolved(out.error_message, mapping)})
            for out in map(_execution_out, await store.execution_history(conn, run_id))
            if body.nodes is None or out.node_ref in body.nodes
        ]
        await write_audit(
            conn, action=ACTION_RUN_CONTENT_RESOLVED, actor_id=reader.id,
            target=target_ref("workflow_run", run_id),
            payload={"nodes": sorted({n.node_ref for n in nodes}),
                     "started_by_reader": row["actor_id"] == reader.id},
        )
    run = _run_out(row)
    return RunResolvedOut(run_id=run_id, status=run.status,
                          input_data=resolved(run.input_data, mapping), nodes=nodes)


async def cancel_run(pool, caller: Caller, run_id: UUID, body: RunCancelRequest, *,
                     engine: Engine) -> WorkflowRunOut:
    """Stop a run that has not finished (CHY-200, AI Act Art. 14(4)(e)).

    Allowed for whoever started the run, and from `reviewer` up for whoever
    sees it (CHY-92, decided 07.11.2026) — the same people who may have its
    values put back. A run the caller does not see is not found.

    **`cancelled` is neither a failure nor a rejection.** The run and every
    node that had not finished — the gate it waits at, or the node in flight —
    end as `cancelled`. Nothing is recorded as a decision at the gate: stopping
    a run is not judging its output, and an Art. 14 record of a judgement
    nobody made would be worse than none. A node still working when the stop
    arrives finishes, and its result is dropped (`_record`).

    **The reason is optional** and stored with the run's placeholders, like a
    reason at the gate (E7); who stopped the run, and when, is audited either
    way. The audit entry names the state and the stopped nodes, never the
    reason: the log is kept for good, the run's content only for its period.

    A run that is already over is refused rather than stopped a second time —
    see RunAlreadyFinished.
    """
    canceller = await resolve_actor(pool, caller)
    require(canceller, "consumer")
    async with acting.acting_as(pool, canceller.id) as conn, conn.transaction():
        run_row = await store.lock_run(conn, run_id)
        if run_row is None:
            raise RunNotFound
        if run_row["actor_id"] != canceller.id:
            require(canceller, "reviewer")
        if run_row["status"] in _TERMINAL_RUN_STATES:
            raise RunAlreadyFinished(run_row["status"])
        reason = await _reason_with_placeholders(conn, run_row, body.reason, engine.placeholders)
        stopped = await store.cancel_open_executions(conn, run_id, reason)
        row = await store.set_run_status(conn, run_id, CANCELLED, error=reason)
        await write_audit(
            conn, action=ACTION_RUN_CANCELLED, actor_id=canceller.id,
            target=target_ref("workflow_run", run_id),
            payload={"from_status": run_row["status"], "nodes": stopped,
                     "started_by_canceller": run_row["actor_id"] == canceller.id},
        )
    return _run_out(row)


def _a_person(actor) -> None:
    """Refuse a technical account where a person has to answer (CHY-275).

    At a gate and for a sample's verdict. The mark comes from the identity
    provider's group chy-service-accounts and is refreshed on every request
    (CHY-163), so an account moved into the group is refused from its next call.
    """
    if actor.is_service_account:
        raise ServiceAccountMayNotDecide


async def record_oversight(
    pool, caller: Caller, run_id: UUID, body: OversightDecision, *, engine: Engine,
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

    **Only someone who may see the run decides it (CHY-92).** The decision is
    taken in the reviewer's own context, so a run outside their scopes is not
    found — the role alone is not enough. The engine continues afterwards in
    the system context.

    **The reason is stored with placeholders** (CHY-234, E7), like everything
    else the run keeps.

    **At a gate with areas, each area casts one vote (CHY-180, ADR-050).** The
    reviewer votes for their area — or names it, if they belong to several of
    the gate's areas. The gate waits until every area has voted, a rejection
    included. All approve → released; all reject → `rejected`; the votes
    differ → escalated along the gate's escalation edge to a higher authority,
    where the run waits again (BTO 1.1 Tz. 6). Who may vote for an area the
    database decides as well: a current membership (migration 043).

    **A technical account never decides (CHY-275).** Its role may suffice, but
    it is not a person; refused before the run is looked up, so the refusal
    says nothing about whether the run exists.
    """
    reviewer = await resolve_actor(pool, caller)
    require(reviewer, "reviewer")
    _a_person(reviewer)

    async with acting.acting_as(pool, reviewer.id) as conn, conn.transaction():
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

        # Before it is written anywhere: the vote, the gate and the run each
        # keep it (CHY-234, E7).
        reason = await _reason_with_placeholders(conn, run_row, body.reason, engine.placeholders)
        tally = await _tally(conn, run_row, gate,
                             store.Vote(reviewer.id, body.decision, reason), body.area)
        await write_audit(
            conn, action=ACTION_RUN_OVERSIGHT, actor_id=reviewer.id,
            target=target_ref("workflow_run", run_id),
            payload={"decision": body.decision, "node_id": str(gate["node_id"]),
                     **tally.payload},
        )

        if tally.outcome == votes.PENDING:
            # Four eyes, first pair, or an area still to vote: recorded,
            # audited, and the run waits. Nothing is completed or fired.
            return Advance(_run_out(run_row), 0)

        if tally.outcome == votes.REJECTED:
            await store.fail_execution(conn, gate["id"], reason or "rejected")
            row = await store.set_run_status(conn, run_id, "rejected", error=reason)
            return Advance(_run_out(row), 0)

        if tally.outcome == votes.ESCALATED:
            # The areas disagree: neither may overrule the other, so the
            # decision goes up (ADR-050). The votes are in the entry, by area.
            # A run with nowhere to escalate fails there, and advancing it
            # below finds it over.
            await _escalate(conn, run_row, _Gate(gate["id"], gate["node_id"]),
                            reason="votes_differ", detail={"votes": tally.payload["votes"]})
        else:
            await _release(conn, run_row, gate)

    return await advance_run(engine.pool, run_id, registry=engine.registry,
                             keys=engine.placeholders.keys)


@dataclass(frozen=True)
class _Tally:
    """What a vote added up to, and what the audit entry says about it."""

    outcome: str
    payload: dict


async def _tally(conn, run_row, gate, vote: store.Vote, area: str | None) -> _Tally:
    """Write the vote and count the gate (CHY-176, CHY-180).

    The vote first: a second decision by the same person, a second vote for an
    area, a vote without membership — the tables refuse each before anything
    is audited.
    """
    names = await store.review_areas(conn, gate["node_id"])
    role = await store.review_role(conn, gate["node_id"])
    # The level fixed when the run reached the gate (CHY-223) — the node's role
    # for a gate reached before migration 044.
    level = await store.review_level(conn, gate["id"]) or role
    if not names:
        await store.record_vote(conn, gate["id"], vote)
        approved = (await store.approvals(conn, gate["id"])
                    if vote.decision == votes.APPROVED else 0)
        return _Tally(votes.without_areas(level, vote.decision, approved),
                      {"approvals": approved, "level": level,
                       "required": votes.APPROVALS_REQUIRED.get(level, 1)})

    if votes.strictness(level, None) > votes.strictness(role, names):
        raise GateBelowMinimum(level)
    areas = await _gate_areas(conn, run_row, names)
    chosen = await _voting_area(conn, vote.actor_id, areas, area)
    await store.record_vote(conn, gate["id"],
                            replace(vote, area=(areas[chosen].id, chosen)))
    cast = await store.area_votes(conn, gate["id"])
    by_name = {name: cast[found.id] for name, found in areas.items() if found.id in cast}
    return _Tally(votes.by_areas([found.id for found in areas.values()], cast),
                  {"area": chosen, "votes": by_name, "required": sorted(areas)})


async def _gate_areas(conn, run_row, names: list[str]) -> dict[str, review_areas.Resolved]:
    """The areas a gate names, as the workflow's scope chain defines them now."""
    definition = await store.workflow(conn, run_row["workflow_id"])
    found = await review_areas.resolve(conn, definition["scope_id"], names)
    missing = sorted(set(names) - set(found))
    if missing:
        raise ReviewAreaUnresolved(missing)
    return found


async def _voting_area(conn, reviewer_id: UUID, areas: dict[str, review_areas.Resolved],
                       requested: str | None) -> str:
    """The area this reviewer votes for at this gate.

    Named when given; otherwise the one area of the gate they belong to. Checked
    here for an answer a person can read — the database checks again on write.
    """
    if requested is not None and requested not in areas:
        raise ReviewAreaNotAtGate(requested, sorted(areas))
    mine = await review_areas.current_areas(conn, reviewer_id,
                                            [found.id for found in areas.values()])
    if requested is not None:
        if areas[requested].id not in mine:
            raise NotInReviewArea(requested)
        return requested
    candidates = sorted(name for name, found in areas.items() if found.id in mine)
    if not candidates:
        raise NotInReviewArea
    if len(candidates) > 1:
        raise ReviewAreaAmbiguous(candidates)
    return candidates[0]


async def _release(conn, run_row, gate, output: Mapping | None = None) -> None:
    """The gate is approved — or passed, at `sample` — complete it and send the
    run along its plain edges.

    A run that cannot go on fails here, saying why; advancing it afterwards
    finds it over and leaves it.
    """
    run_id = run_row["id"]
    await store.complete_execution(
        conn, gate["id"], output=dict(output or {"decision": votes.APPROVED}), model_used=None
    )
    graph = await _load_graph(conn, run_row["workflow_id"])
    state = await _load_state(conn, run_id)
    # A release takes the plain edges; the escalation edges are not part of
    # it, or a gate that ends the workflow on approval would fail (ADR-050).
    firing = runner.fire(graph, state, gate["node_id"], conditions={},
                         excluding=_escalation_edges(graph, gate["node_id"]))
    await _record_exhausted(conn, graph, run_id, gate["node_id"], firing)
    if firing.failure:
        await store.set_run_status(conn, run_id, "failed", error=firing.failure)
        return
    await store.record_traversals(conn, run_id, list(firing.taken))
    await store.set_run_status(conn, run_id, "running")


# ── the review queue (CHY-223, CHY-269, ADR-050) ──────────────────────────────
#
# Two sources, one queue: the passes of sample gates, and the direct calls drawn
# where the minimum review is sample. Whoever may see one may see it in the
# queue — RLS decides for both (CHY-92, migration 048) — and a verdict means the
# same for both: the output went on, a person judges it afterwards.

async def list_samples(pool, caller: Caller, *, open_only: bool = True) -> list[ReviewSampleOut]:
    """Everything drawn that the caller may see, oldest first."""
    reader = await resolve_actor(pool, caller)
    require(reader, "consumer")
    async with acting.acting_as(pool, reader.id) as conn:
        rows = await store.samples(conn, open_only=open_only)
    return [ReviewSampleOut(**dict(row)) for row in rows]


async def sample_detail(pool, caller: Caller, sample_id: UUID) -> ReviewSampleDetail:
    """One entry, with what there is to judge where the queue keeps it.

    A direct call keeps its prompt and answer with placeholders until its
    scope's content period ends. A workflow pass keeps nothing of its own: the
    output is the run's, read where the run is (`/v1/workflows/runs/{id}/nodes`).
    """
    reader = await resolve_actor(pool, caller)
    require(reader, "consumer")
    async with acting.acting_as(pool, reader.id) as conn:
        row = await store.workflow_sample(conn, sample_id) or await store.direct_sample(
            conn, sample_id)
    if row is None:
        raise SampleNotFound(sample_id)
    return ReviewSampleDetail(**dict(row))


async def judge_sample(pool, caller: Caller, sample_id: UUID, body: ReviewSampleVerdict,
                       *, placeholders: Placeholders) -> ReviewSampleOut:
    """A person judges a drawn sample afterwards: confirmed or objected (ADR-050 point 6).

    From `reviewer` up, and only who may see it. Once per sample. The reason is
    stored with placeholders and cleared with the content; a sample whose
    content is cleared has nothing left to judge. An objection recalls nothing —
    the output went on — it is counted, a signal from operation (ADR-044, way 3).
    A technical account never judges (CHY-275).
    """
    reviewer = await resolve_actor(pool, caller)
    require(reviewer, "reviewer")
    _a_person(reviewer)
    async with acting.acting_as(pool, reviewer.id) as conn, conn.transaction():
        sample = await store.lock_workflow_sample(conn, sample_id)
        if sample is not None:
            return await _judge_workflow_pass(conn, reviewer.id, sample, body, placeholders)
        sample = await store.lock_direct_sample(conn, sample_id)
        if sample is not None:
            return await _judge_direct_call(conn, reviewer.id, sample, body, placeholders)
    raise SampleNotFound(sample_id)


async def _judge_workflow_pass(conn, reviewer_id: UUID, sample, body: ReviewSampleVerdict,
                               placeholders: Placeholders) -> ReviewSampleOut:
    """The verdict on a pass of a sample gate. The reason takes the run's
    placeholders (CHY-234, E7)."""
    if sample["verdict"] is not None:
        raise SampleAlreadyJudged(sample["sample_id"])
    run_row = await store.lock_run(conn, sample["run_id"])
    if run_row["content_purged_at"] is not None:
        raise RunContentPurged
    reason = await _reason_with_placeholders(conn, run_row, body.reason, placeholders)
    row = await store.record_verdict(conn, sample["sample_id"], body.verdict, reason,
                                     reviewer_id)
    await write_audit(
        conn, action=ACTION_SAMPLE_REVIEWED, actor_id=reviewer_id,
        target=target_ref("workflow_run", sample["run_id"]),
        payload={"node_ref": sample["node_ref"], "verdict": body.verdict},
    )
    return ReviewSampleOut(**{**dict(sample), **dict(row)})


async def _judge_direct_call(conn, reviewer_id: UUID, sample, body: ReviewSampleVerdict,
                             placeholders: Placeholders) -> ReviewSampleOut:
    """The verdict on a drawn direct call (CHY-269).

    The reason is replaced by the scope's rules and the shipped patterns before
    it is kept. There is no mapping to number on from — the call kept none — so
    a value the rules catch becomes a placeholder that resolves to nothing,
    which is the point: nothing here may turn back into a name.
    """
    if sample["verdict"] is not None:
        raise SampleAlreadyJudged(sample["sample_id"])
    if sample["content_purged_at"] is not None:
        raise SampleContentPurged(sample["sample_id"])
    reason = body.reason
    if reason:
        policy = await placeholders.policy_for(sample["scope_id"])
        reason = redact({"text": reason}, rules=policy.rules,
                        identifiers=policy.identifiers).texts["text"]
    row = await store.record_direct_verdict(conn, sample["sample_id"], body.verdict, reason,
                                            reviewer_id)
    await write_audit(
        conn, action=ACTION_DIRECT_CALL_SAMPLE_REVIEWED, actor_id=reviewer_id,
        target=target_ref("direct_call_sample", sample["sample_id"]),
        payload={"scope_id": str(sample["scope_id"]), "task_type": sample["task_type"],
                 "verdict": body.verdict},
    )
    return ReviewSampleOut(**{**dict(sample), **dict(row)})


@dataclass(frozen=True)
class DirectCall:
    """What a direct call keeps if it is drawn: placeholders only (CHY-269)."""

    scope_id: UUID
    called_by: UUID
    task_type: str
    model_used: str
    block_versions: Mapping[str, int]
    prompt: str
    answer: str


async def draw_direct_call(pool, call: DirectCall, sample_rate: Decimal) -> UUID | None:
    """Draw a direct call for review at `sample_rate`; its id if drawn, else None.

    The call gets an id of its own, and whether it is drawn is a hash of that
    id, as for a pass of a sample gate (votes.drawn): the same id is drawn or
    not every time it is asked. Kept for the caller, in their name — RLS lets a
    caller write their own call (migration 048) — with what the model saw and
    answered, never the values.
    """
    sample_id = uuid4()
    if not votes.drawn(sample_id, sample_rate):
        return None
    async with acting.acting_as(pool, call.called_by) as conn:
        await store.insert_direct_sample(conn, sample_id, call, sample_rate)
    return sample_id


# ── gates nobody decides (CHY-254, ADR-050) ───────────────────────────────────

async def expire_gates(pool, periods: Periods, *, dry_run: bool = False,
                       keys: mapping_cipher.KeyRing | None = None) -> dict[str, int]:
    """End or escalate the gates nobody decided in time. Returns the counts.

    Started by the operator's timer (`cc oversight expire`), never by the API
    process — as the purge (ADR-046 point 5). In the system context: the
    command works for the installation and must reach every run (CHY-92).

    A waiting gate is due when the content period of its scope has passed since
    the run reached it, or its own `review_timeout` has. Then:

    - **content period passed** → the run ends as `expired`, whatever the gate
      says about timeouts: its content may not be kept longer, and an escalation
      would only keep it waiting. The content is cleared with it, at once
    - **time limit passed, `on_timeout = escalate`** → the gate ends with
      `decision = escalated` and the run goes along its escalation edge to the
      next gate, where it waits again
    - **time limit passed, `on_timeout = reject`** → the run ends as `expired`

    Each run in its own transaction, under its lock, and checked again there: a
    person may have decided since it was found. A decision that came first
    wins. An escalation with no edge left to take fails the run and counts as
    `gates_failed`. `dry_run` counts what would happen and changes nothing.
    """
    scope_ids, keep_days = periods.finite(RetentionClass.RUN_CONTENT)
    async with pool.acquire() as conn:
        due = await store.waiting_gates(conn, scope_ids, keep_days)
    counts = {"gates_expired": 0, "gates_escalated": 0, "gates_failed": 0}
    escalated: list[UUID] = []
    for gate in due:
        escalate = not gate["content_due"] and gate["on_timeout"] == "escalate"
        if dry_run:
            counts["gates_escalated" if escalate else "gates_expired"] += 1
            continue
        async with pool.acquire() as conn, conn.transaction():
            run_row = await store.lock_run(conn, gate["run_id"])
            current = await store.running_execution(conn, gate["run_id"])
            if (run_row is None or run_row["status"] != AWAITING or current is None
                    or current["id"] != gate["execution_id"]):
                continue
            if not escalate:
                await _expire_gate(conn, gate)
                counts["gates_expired"] += 1
            elif await _escalate(conn, run_row, _Gate(gate["execution_id"], gate["node_id"]),
                                 reason="timeout"):
                escalated.append(gate["run_id"])
                counts["gates_escalated"] += 1
            else:
                counts["gates_failed"] += 1
    for run_id in escalated:
        # The escalation edge leads to a gate (activation checks it), so this
        # claims that gate and stops: no step runs, and no executor is needed.
        await advance_run(pool, run_id, registry=ExecutorRegistry(), keys=keys)
    return counts


async def _expire_gate(conn, gate) -> None:
    """The run and its gate end as `expired`; the audit entry names no person.

    Past the content period, the content goes in the same transaction. Left to
    the purge, it would stay a whole period more: the purge counts a finished
    run from `completed_at`, and that is now. The message goes with it — the
    inventory counts `error_message` as content (kernel/retention.py) —, so a
    cleared run looks like every other; why it ended stays in the audit entry.
    """
    reason = "content_period" if gate["content_due"] else "timeout"
    message = (
        f"Nobody decided at {gate['node_ref']} before the content period of its scope ran "
        f"out (ADR-050)."
        if reason == "content_period" else
        f"Nobody decided at {gate['node_ref']} within its time limit "
        f"({gate['review_timeout']}); on_timeout is reject (ADR-050)."
    )
    await store.expire_execution(conn, gate["execution_id"], message)
    await store.set_run_status(conn, gate["run_id"], EXPIRED, error=message)
    if reason == "content_period":
        await store.clear_run_content(conn, [gate["run_id"]])
    await write_audit(
        conn, action=ACTION_RUN_EXPIRED, actor_id=None,
        target=target_ref("workflow_run", gate["run_id"]),
        payload={"node_ref": gate["node_ref"], "reason": reason,
                 "waiting_since": gate["started_at"].isoformat()},
    )


@dataclass(frozen=True)
class _Gate:
    """A gate execution: which row, and which node of the graph."""

    execution_id: UUID
    node_id: UUID


async def _escalate(conn, run_row, gate: _Gate, *, reason: str,
                    detail: dict | None = None) -> bool:
    """The gate ends as escalated and the run moves along its escalation edge.

    Two reasons lead here (ADR-050): `timeout`, from `cc oversight expire`, and
    `votes_differ`, when the areas at a gate disagree. The audit entry names no
    person either way — the escalation is the gate's result, and the votes that
    led to it are entries of their own.

    False when there is no edge to take — then the run fails, saying why,
    rather than waiting at a gate that cannot decide.
    """
    await store.complete_execution(
        conn, gate.execution_id,
        output={"decision": conditions.ESCALATED, "reason": reason}, model_used=None)
    graph = await _load_graph(conn, run_row["workflow_id"])
    state = await _load_state(conn, run_row["id"])
    firing = runner.escalate(graph, state, gate.node_id,
                             escalation_edges=_escalation_edges(graph, gate.node_id))
    await _record_exhausted(conn, graph, run_row["id"], gate.node_id, firing)
    if firing.failure:
        await store.set_run_status(conn, run_row["id"], "failed", error=firing.failure)
        return False
    await store.record_traversals(conn, run_row["id"], list(firing.taken))
    await store.set_run_status(conn, run_row["id"], "running")
    await write_audit(
        conn, action=ACTION_RUN_ESCALATED, actor_id=None,
        target=target_ref("workflow_run", run_row["id"]),
        payload={"node_ref": graph.ref(gate.node_id), "reason": reason,
                 "to": sorted(graph.ref(e.target) for e in graph.successors(gate.node_id)
                              if e.id in firing.taken),
                 **(detail or {})},
    )
    return True


# ── retention (CHY-17, ADR-046) ───────────────────────────────────────────────

async def purge_runs(conn, periods: Periods) -> dict[str, int]:
    """Apply the retention periods to workflow runs, in the caller's transaction.

    Content first: clear what went in and came out of every run past its
    `run_content` period — its documents among it —, mark it, keep the row.
    Then the trace: delete every run past its `run_trace` period, leaf first.
    The period of a run is the one that applies in its workflow's scope
    (kernel.retention). Finished runs count, and so do runs waiting for
    documents, which do no work (ADR-049); a running run, or one at a gate, is
    never touched (CHY-254).
    """
    scope_ids, keep_days = periods.finite(RetentionClass.RUN_CONTENT)
    cleared = await store.runs_past_period(conn, scope_ids, keep_days, only_uncleared=True)
    if cleared:
        await store.clear_run_content(conn, cleared)

    scope_ids, keep_days = periods.finite(RetentionClass.RUN_TRACE)
    expired = await store.runs_past_period(conn, scope_ids, keep_days, only_uncleared=False)
    deleted, held = await store.delete_runs(conn, expired) if expired else (0, 0)
    return {"workflow_runs_cleared": len(cleared), "workflow_runs_deleted": deleted,
            "workflow_runs_held": held}


async def purge_direct_samples(conn, periods: Periods) -> dict[str, int]:
    """Apply the retention periods to drawn direct calls, in the caller's transaction.

    As for runs (CHY-269): content first — prompt, answer and reason cleared
    after the scope's `run_content` period, the verdict kept —, then the row,
    deleted after its `run_trace` period. Counted from when it was drawn.
    """
    scope_ids, keep_days = periods.finite(RetentionClass.RUN_CONTENT)
    cleared = await store.direct_samples_past_period(conn, scope_ids, keep_days,
                                                     only_uncleared=True)
    if cleared:
        await store.clear_direct_samples(conn, cleared)

    scope_ids, keep_days = periods.finite(RetentionClass.RUN_TRACE)
    expired = await store.direct_samples_past_period(conn, scope_ids, keep_days,
                                                     only_uncleared=False)
    if expired:
        await store.delete_direct_samples(conn, expired)
    return {"direct_call_samples_cleared": len(cleared),
            "direct_call_samples_deleted": len(expired)}


async def high_risk_scopes(conn) -> set[UUID]:
    """Scopes holding an active workflow that counts as high-risk (CHY-266).

    Handed to the kernel by the transport when a retention period changes:
    the kernel decides whether the change shortens what such a scope keeps,
    and imports no context to find out which scopes those are.
    """
    return await store.high_risk_scopes(conn)


async def workflow_scopes(conn) -> dict[UUID, UUID]:
    """Each workflow's scope, for a context that keeps rows per workflow.

    Published so that context does not join workflow_definitions itself — the
    table is this context's (CHY-231).
    """
    return await store.workflow_scopes(conn)
