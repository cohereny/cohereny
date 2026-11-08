"""
contexts/orchestration/errors.py

What can go wrong in the orchestration context, expressed without HTTP
(ADR-030, CHY-82).

The status codes are chosen once, in api/main.py. Nothing here knows them.
"""
from __future__ import annotations

from typing import ClassVar
from uuid import UUID


class OrchestrationError(Exception):
    """Base class, so the transport layer can catch the family in one handler."""


class WorkflowNotFound(OrchestrationError):
    def __init__(self) -> None:
        super().__init__("Workflow not found")


class TargetWorkflowNotFound(OrchestrationError):
    """A sub_workflow node points at a workflow that does not exist (CHY-165).

    Its own error rather than `WorkflowNotFound`: the workflow in the path
    exists — the one named in the body does not, and "Workflow not found" would
    send the caller looking at the wrong one.
    """

    def __init__(self, target_workflow_id) -> None:
        self.target_workflow_id = target_workflow_id
        super().__init__(
            f"sub_workflow target {target_workflow_id} does not exist — a "
            f"sub_workflow node must point at an existing workflow"
        )


class NodeNotFound(OrchestrationError):
    def __init__(self) -> None:
        super().__init__("Node not found")


class ConnectionNotFound(OrchestrationError):
    def __init__(self) -> None:
        super().__init__("Connection not found")


class WorkflowNotEditable(OrchestrationError):
    """Nodes and edges may only change while the workflow is a draft.

    The same rule context_blocks follows: what a reviewer approved must not
    change underneath the approval. An active workflow that grew a node would
    be running a graph nobody released.
    """

    def __init__(self, status: str) -> None:
        self.status = status
        super().__init__(
            f"The workflow is {status}; its graph can only be changed while it "
            f"is a draft. Create a new version instead."
        )


class WorkflowInvalid(OrchestrationError):
    """Activation was refused because the graph does not hold up.

    Carries every violation rather than the first: fixing a graph one round
    trip at a time is how a validator becomes something people route around.
    """

    #: How many violations the message spells out before summarising. Three
    #: because a refusal has to fit in a toast; the full list is in the
    #: `violations` attribute and in the /validate response.
    NAMED_IN_MESSAGE = 3

    def __init__(self, violations: list) -> None:
        self.violations = violations
        heads = "; ".join(v.message for v in violations[: self.NAMED_IN_MESSAGE])
        rest = len(violations) - self.NAMED_IN_MESSAGE
        more = f" (and {rest} more)" if rest > 0 else ""
        super().__init__(f"Workflow cannot be activated: {heads}{more}")


class SubWorkflowCycle(OrchestrationError):
    """The sub-workflow call graph closes a loop.

    Cycles inside a workflow are allowed and bounded by loop_limit (ADR-026).
    A cycle between workflows is bounded by nothing: each call starts a fresh
    run with fresh counters, so it recurses until the process dies.
    """

    def __init__(self, cycle: list[UUID]) -> None:
        self.cycle = cycle
        super().__init__(
            "Sub-workflow calls form a cycle: "
            + " → ".join(str(w) for w in cycle)
            + ". A loop between workflows has no iteration limit — each call "
              "starts a new run with fresh counters."
        )


class InvalidNodeRef(OrchestrationError):
    """Two nodes in one workflow claim the same node_ref.

    The database enforces it too (UNIQUE in 001_initial). This exists so the
    refusal is a sentence rather than a constraint violation arriving as a 500.
    """

    def __init__(self, node_ref: str) -> None:
        self.node_ref = node_ref
        super().__init__(
            f"A node with ref {node_ref!r} already exists in this workflow. "
            f"Refs are how edges and traces name nodes, so they have to be unique."
        )


class NodeNotStorable(OrchestrationError):
    """The table refused this node, and the refusal is the whole design.

    `workflow_nodes` carries CHECK constraints that say which fields belong
    to which node type: a sub_workflow needs a pinned target (018), only a
    context_compose node has `context_requirements` (022), `on_timeout` is
    never `approve` (018, ADR-004). Each of them is a rule that could not be
    expressed in the request model — either because it spans two columns, or
    because the vocabulary belongs to another context this one may not import
    (ADR-030, the case for block types).

    Without this the same rules would arrive as an asyncpg `CheckViolationError`
    and leave as a 500: a caller told that the server broke, when in fact the
    server refused something specific and can say what.

    `InvalidNodeRef` exists for the same reason one constraint further along.
    """

    #: Constraint name → what it means, in the words of the rule rather than of
    #: the DDL. An unlisted constraint still produces a usable message; the
    #: entry is what turns "violates check constraint" into a next step.
    #:
    #: Deliberately without a count in the prose: "six" stood here while the
    #: table had eight, and a number written next to a list rots with every
    #: migration. tests/test_context_requirements.py holds the set against the
    #: migrations in both directions instead.
    REASONS: ClassVar[dict[str, str]] = {
        "workflow_nodes_requirements_shape":
            "context_requirements may only name block types that can actually "
            "be composed (ADR-037), and must name at least one. A "
            "non-composable type such as routing_rule would compose nothing "
            "and report success.",
        "workflow_nodes_requirements_only_on_compose":
            "only a context_compose node has context_requirements. On any "
            "other type the field would be stored and never read.",
        "workflow_nodes_config_without_block_types":
            "config['block_types'] has moved to context_requirements "
            "(CHY-134). In config it would be stored and ignored.",
        "workflow_nodes_task_type_only_on_llm_call":
            "only an llm_call node has a task_type. On any other type it would "
            "be stored and never read.",
        "workflow_nodes_llm_call_has_task_type":
            "an llm_call node needs a task_type. Its model is resolved from the "
            "routing rules of the workflow's scope for that task type, and "
            "without one there is nothing to resolve by (CHY-145).",
        "workflow_nodes_config_without_model":
            "config['model'] is no longer read (CHY-145). The model comes from "
            "the routing rules of the workflow's scope, so it passes through "
            "approval and the privacy ratchet.",
        "workflow_nodes_config_without_task_type":
            "config['task_type'] has moved to the task_type field (CHY-145).",
        "workflow_nodes_sub_workflow_complete":
            "a sub_workflow node needs target_workflow_id and a pinned "
            "target_workflow_version — 'always the newest' would change "
            "behaviour without review.",
        "workflow_nodes_target_only_on_sub":
            "target_workflow_id and target_workflow_version belong to a "
            "sub_workflow node and to no other type.",
        "workflow_nodes_on_timeout_check":
            "on_timeout is reject or escalate, never approve: a gate that "
            "approves on silence is not a gate (ADR-004).",
        "workflow_nodes_review_role_check":
            "review_role is approver or four_eyes.",
    }

    def __init__(self, constraint: str) -> None:
        self.constraint = constraint
        reason = self.REASONS.get(
            constraint, f"the row violates {constraint}, which this node does not satisfy"
        )
        super().__init__(f"This node cannot be stored: {reason}")


class UnknownNodeInEdge(OrchestrationError):
    """An edge names a node the workflow does not contain.

    Not a client error: the caller asked to read or validate a workflow, and
    what came back from the database does not hang together. Skipping the edge
    would let the traversal answer a question about a different graph than the
    one stored — confidently, and about the wrong graph.

    In errors.py rather than beside the analysis it is raised from. It is an
    error of this context like any other, N818 wants the suffix or the
    exemption, and the exemption is granted per file — which is a small nudge
    in the right direction: errors belong together.
    """

    def __init__(self, edge_id: UUID, node_id: UUID) -> None:
        self.edge_id = edge_id
        self.node_id = node_id
        super().__init__(
            f"Connection {edge_id} references node {node_id}, which is not part "
            f"of this workflow. The stored graph is inconsistent."
        )


class NodeTypeNotExecutable(OrchestrationError):
    """No executor for this node type, or none that could exist yet.

    Two of the seven node types have no default executor, and both absences are
    deliberate (CHY-127):

    `llm_call` — the completion path lives entirely in `api/v1/complete.py`,
    with httpx in the route handler. There is no way to call a model that does
    not go over HTTP (CHY-129), and writing one in this context would be a
    second truth about how a model is called. The composition root injects one
    instead.

    `tool_call` — nothing defines what a tool call is. An executor would be an
    invention.

    Raised rather than skipped. A node type that silently produced nothing
    would let a run reach `complete` having done none of its work, and a
    workflow that reports success without running is the failure this project
    keeps meeting under other names.
    """

    def __init__(self, node_type: str, node_ref: str, reason: str | None = None) -> None:
        self.node_type = node_type
        self.node_ref = node_ref
        super().__init__(reason or (
            f"No executor for node type {node_type!r} ({node_ref}). "
            f"llm_call needs one injected until the completion path moves below "
            f"the transport (CHY-129); tool_call has no defined semantics yet."
        ))


class NodeExecutionFailed(OrchestrationError):
    """A node ran, and its work failed for a reason outside the graph.

    The model refused, no routing rule covers the task type, the scope is gone.
    None of these is a modelling mistake in the workflow, and none is a bug in
    the runner — they are the node's work not succeeding, which is a normal
    outcome a run has to be able to end with.

    Raised by executors in the composition root, which is the only place that
    knows both error families: routing and completion live below the transport,
    orchestration may not import them (ADR-030). The original error stays
    attached as `__cause__`.

    Until CHY-145 there was no such class, and a model failure inside an
    `llm_call` did not end the run at all — the exception passed through the
    runner, which only caught its own family, and left both the run and the
    execution at `running` for good.
    """

    def __init__(self, node_ref: str, reason: str) -> None:
        self.node_ref = node_ref
        super().__init__(f"Node {node_ref} failed: {reason}")


class RunNotFound(OrchestrationError):
    def __init__(self) -> None:
        super().__init__("Workflow run not found")


class RunContentPurged(OrchestrationError):
    """The run's content and its mapping were cleared when its period ended
    (CHY-17, CHY-234): there is nothing left to put back."""

    def __init__(self) -> None:
        super().__init__("The content of this run was cleared at the end of its period")


class RunDidNotSettle(OrchestrationError):
    """An advance kept finding work after an implausible number of rounds.

    `loop_limit` already bounds a well-formed graph, so this is a bug in the
    state machine rather than a runaway workflow — and a bug that spins is
    worse than one that stops. Raised rather than logged: a run that never
    settles would otherwise hold a connection and look busy forever.
    """

    def __init__(self, run_id: UUID, rounds: int) -> None:
        self.run_id = run_id
        self.rounds = rounds
        super().__init__(
            f"Run {run_id} still had work after {rounds} rounds. loop_limit "
            f"bounds a correct graph, so this points at the state machine and "
            f"not at the workflow."
        )


class WorkflowNotRunnable(OrchestrationError):
    """Only an active workflow may be run.

    A draft has not been through the validator, and the validator is what
    guarantees the three mandatory nodes and a limit on every back edge. Running
    one would be running a graph nobody released (ADR-004, CHY-82).
    """

    def __init__(self, status: str) -> None:
        self.status = status
        super().__init__(
            f"The workflow is {status}. Only an active workflow can be run — a "
            f"draft has not passed the validator, and a deprecated one was "
            f"taken out of service on purpose."
        )


class RunNotAwaitingOversight(OrchestrationError):
    """A decision arrived for a run that is not at a gate."""

    def __init__(self, status: str) -> None:
        self.status = status
        super().__init__(
            f"The run is {status}, not awaiting a decision. A decision for a "
            f"run that already moved on would be recorded against nothing."
        )


class RunAlreadyFinished(OrchestrationError):
    """A run that is over cannot be stopped (CHY-200).

    Refused rather than accepted as a no-op: a cancellation recorded for a run
    that had already completed would say a person stopped something that
    finished on its own, and the audit trail would contradict the run.
    """

    def __init__(self, status: str) -> None:
        self.status = status
        super().__init__(
            f"The run is already {status}; there is nothing left to stop."
        )


class OversightGateMissing(OrchestrationError):
    """The run says it is waiting and no node is.

    Refused rather than guessed at. Approving a gate that was never claimed
    would let the run continue from a position nobody recorded — and the record
    of where a run stood is the whole reason the gate is a row (ADR-028).
    """

    def __init__(self, run_id: UUID) -> None:
        self.run_id = run_id
        super().__init__(
            f"Run {run_id} is marked as awaiting oversight but no node is "
            f"claimed. Its position was not recorded, so the decision has "
            f"nothing to attach to."
        )


class ReviewerAlreadyDecided(OrchestrationError):
    """The same person decided at this gate before (CHY-176).

    A four-eyes gate counts people, not clicks. Refused by the primary key of
    `review_votes` and translated here, so a second approval by the first
    reviewer cannot pass the gate — not even when two of that person's
    requests arrive at once.
    """

    def __init__(self, actor_id: UUID) -> None:
        self.actor_id = actor_id
        super().__init__(
            "This person has already decided at this gate. A four-eyes gate "
            "needs a second, different person."
        )


class MalformedCondition(OrchestrationError):
    """A guard on an edge is not a well-formed condition (CHY-130).

    Raised by the validator at activation, which is the point of choosing data
    over an expression: the answer arrives while somebody is looking at the
    workflow, not while a run is half finished.

    The messages live here rather than at the six call sites, which is the
    shape every other error in this package has — `VersionConflict` takes the
    two versions, not a sentence. A constructor per case also means the
    validator reads as a list of rules instead of a list of strings.

    Each message names what was wrong **and** what was expected. "Invalid
    condition" makes the author guess, and guessing at a branch condition is
    how a workflow ends up taking the wrong arm.
    """

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"Malformed edge condition: {reason}.")

    @classmethod
    def not_an_object(cls) -> MalformedCondition:
        return cls("a condition is a non-empty object")

    @classmethod
    def too_deep(cls, limit: int) -> MalformedCondition:
        return cls(f"nested deeper than {limit} levels")

    @classmethod
    def empty_combinator(cls, combinator: str) -> MalformedCondition:
        return cls(f"{combinator!r} takes a non-empty list")

    @classmethod
    def missing_keys(cls, missing: set[str], present: list[str]) -> MalformedCondition:
        return cls(
            f"a comparison needs {', '.join(sorted(missing))}; "
            f"got keys {sorted(present)}"
        )

    @classmethod
    def unknown_operator(cls, op: object, known: tuple[str, ...]) -> MalformedCondition:
        return cls(f"unknown operator {op!r}. Expected one of: {', '.join(known)}")

    @classmethod
    def bad_field(cls) -> MalformedCondition:
        return cls("field is a non-empty string")
