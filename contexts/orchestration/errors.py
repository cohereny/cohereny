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
            "and report success. `tags`, if given, is a non-empty list of "
            "non-empty strings (CHY-178): an empty list would read as no "
            "filter at all.",
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
        "workflow_nodes_output_schema_only_on_llm_call":
            "only an llm_call node has an output_schema. On any other type it "
            "would be stored and never read (CHY-175).",
        "workflow_nodes_output_schema_is_object":
            'output_schema describes an object ("type": "object"): the answer '
            "is read as one JSON object, and its fields are what edges and "
            "successors address (CHY-175).",
        "workflow_nodes_config_without_output_schema":
            "config['output_schema'] is not read (CHY-175). Send it as the "
            "node's output_schema field.",
        "workflow_nodes_function_only_on_compute":
            "only a compute node has a function and function_version. On any "
            "other type they would be stored and never run (CHY-173).",
        "workflow_nodes_compute_names_function":
            "a compute node needs a function and a function_version from 1: "
            "without them there is nothing to run, and the version keeps a "
            "definition computing what it was reviewed against (ADR-048).",
        "workflow_nodes_config_without_function":
            "config['function'] and config['function_version'] are not read "
            "(CHY-173). Send them as the node's fields.",
        "workflow_nodes_document_only_on_document":
            "only a document node has document_name, document_max_bytes and "
            "document_max_pages. On any other type they would be stored and "
            "never read (CHY-174).",
        "workflow_nodes_document_names_document":
            "a document node needs a document_name — lower case letters, digits "
            "and underscores, starting with a letter. It is what the document is "
            "uploaded under (ADR-049).",
        "workflow_nodes_document_limits_positive":
            "document_max_bytes and document_max_pages are at least 1. Omit "
            "them for the installation's limits.",
        "workflow_nodes_config_without_document":
            "config['document_name'], config['document_max_bytes'] and "
            "config['document_max_pages'] are not read (CHY-174). Send them as "
            "the node's fields.",
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
        "workflow_nodes_sample_gate":
            "review_sample_rate belongs to a gate with review_role sample, and that "
            "gate needs one, from 0.001 to 1. A sample gate has no review "
            "areas and no time limit: it decides nothing and waits for nothing "
            "(CHY-223).",
        "workflow_nodes_review_areas_gate":
            "review_areas belong to a human_review node: one to sixteen names "
            "of the form lower_case (CHY-180).",
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


class NotInReviewArea(OrchestrationError):
    """The reviewer belongs to none of the areas this gate asks (CHY-180).

    Checked by the service for a readable answer, and by the database as well:
    a vote for an area needs a current membership there (migration 043), so a
    path around this check is refused too.
    """

    def __init__(self, area: str | None = None) -> None:
        self.area = area
        super().__init__(
            f"You are not a current member of the review area {area!r}."
            if area else
            "You belong to none of the review areas this gate asks for."
        )


class ReviewAreaNotAtGate(OrchestrationError):
    """The vote names an area this gate does not ask for."""

    def __init__(self, area: str, areas: list[str]) -> None:
        self.area = area
        super().__init__(f"This gate asks for {areas}, not for {area!r}.")


class ReviewAreaAmbiguous(OrchestrationError):
    """The reviewer belongs to several of the gate's areas and named none.

    Not guessed: which area a vote counts for is the decision, and one person
    still casts one vote at one gate (027).
    """

    def __init__(self, areas: list[str]) -> None:
        self.areas = areas
        super().__init__(f"You belong to {areas} at this gate. Say which area you vote for (area).")


class AreaAlreadyVoted(OrchestrationError):
    """Another member of this area has voted at this gate (CHY-180).

    One vote per area, refused by UNIQUE (execution_id, area_id) and translated
    here — also when two members of one area send theirs at the same moment.
    """

    def __init__(self, area: str) -> None:
        self.area = area
        super().__init__(f"The review area {area!r} has already voted at this gate.")


class ReviewAreaUnresolved(OrchestrationError):
    """A review area the gate names applies nowhere on the workflow's scope chain.

    Activation refuses this (`unknown_review_area`); reaching it means the
    definition and the competence order no longer agree — not something the
    reviewer did or can fix.
    """

    def __init__(self, areas: list[str]) -> None:
        self.areas = areas
        super().__init__(f"The review areas {areas} are defined nowhere above this workflow.")


class SampleNotFound(OrchestrationError):
    """Nothing drawn with this id that the caller may see (CHY-223, CHY-269)."""

    def __init__(self, sample_id: UUID) -> None:
        self.sample_id = sample_id
        super().__init__(f"No sample {sample_id} in the review queue.")


class ServiceAccountMayNotDecide(OrchestrationError):
    """A technical account tried to decide at a gate or judge a sample (CHY-275).

    A CI account holds the role its jobs need, and it is still not a person. A
    decision at a gate, or the verdict on a drawn sample, is human oversight
    (Art. 14 AI Act) — someone has to answer for it. The same rule the
    repository context applies to data-flow blocks (CHY-163, ADR-045), here for
    every gate and every sample.
    """

    def __init__(self) -> None:
        super().__init__(
            "A technical account (group chy-service-accounts) cannot decide at a gate "
            "or judge a sample. A person has to."
        )


class SampleAlreadyJudged(OrchestrationError):
    """The sample has its verdict. One verdict per sample, like one decision per gate."""

    def __init__(self, sample_id: UUID) -> None:
        self.sample_id = sample_id
        super().__init__(f"Sample {sample_id} has been judged already.")


class SampleContentPurged(OrchestrationError):
    """A drawn direct call whose prompt and answer were cleared when the scope's
    content period ended (CHY-269): there is nothing left to judge."""

    def __init__(self, sample_id: UUID) -> None:
        self.sample_id = sample_id
        super().__init__(
            f"The content of sample {sample_id} was cleared at the end of its period")


class GateBelowMinimum(OrchestrationError):
    """A gate with areas asks for fewer people than its scope now requires (CHY-223).

    Activation refuses such a gate; this is the case after it — the scope was
    classified stricter while the workflow stayed released. A gate without
    areas then simply asks for more people. A gate with areas cannot: each area
    casts one vote, and adding a vote nobody drew would be a decision about who
    decides that nobody made. It is not decided until a stricter definition is
    released; the run can be stopped meanwhile, or expires.
    """

    def __init__(self, level: str) -> None:
        self.level = level
        super().__init__(
            f"This gate's areas ask for fewer people than its scope requires now "
            f"({level}). Release the workflow again with a stricter gate."
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


class DocumentNodeExists(OrchestrationError):
    """A second document node for the same name (CHY-174).

    The node *is* the document, with one limit. Two nodes for one name would
    expect it twice under different limits; two steps that need it get two
    edges from the one node. Refused by a unique index, translated here.
    """

    def __init__(self, document_name: str) -> None:
        self.document_name = document_name
        super().__init__(
            f"This workflow already has a document node for {document_name!r}. "
            f"Connect further steps to that node instead."
        )


# ── documents of a run (CHY-174, ADR-049) ─────────────────────────────────────

class RunNotAwaitingDocuments(OrchestrationError):
    """A document arrived for a run that is not waiting for one.

    A run takes its documents before its first step and not after: a document
    that arrived once the run was under way would reach some steps and not
    others, and nobody could say afterwards which steps saw what.
    """

    def __init__(self, status: str) -> None:
        self.status = status
        super().__init__(
            f"The run is {status}, not awaiting documents. Documents are taken "
            f"before the first step only."
        )


class DocumentNotFromStarter(OrchestrationError):
    """Someone other than the run's starter tried to upload a document.

    The binding of a document is its run, and the run is the starter's: they
    sent its input, they send its documents. Whoever else sees the run reads
    it; supplying what it works on is not theirs to do (ADR-049, decided
    08.11.2026 — no upload token of its own).
    """

    def __init__(self) -> None:
        super().__init__(
            "Only the person who started this run can upload its documents."
        )


class DocumentNotExpected(OrchestrationError):
    """The run's definition has no document node for this name."""

    def __init__(self, name: str, expected: list[str]) -> None:
        self.name = name
        self.expected = expected
        listed = ", ".join(expected) if expected else "none"
        super().__init__(
            f"This run expects no document named {name!r}. Expected: {listed}."
        )


class DocumentAlreadyReceived(OrchestrationError):
    """The document arrived before. What arrived stays as it arrived: the
    audit entry and the scan describe that file, not a later one."""

    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(
            f"Document {name!r} has already been received for this run. Cancel "
            f"the run and start a new one to send a different file."
        )


class DocumentRefused(OrchestrationError):
    """A document that cannot be taken as it is. The subclasses say why; the
    message names the limit or the problem, never the content."""


class DocumentTooLarge(DocumentRefused):
    """Larger than the node's limit or the installation's ceiling, whichever is
    smaller. `size` is None when reading stopped at the ceiling: the body is
    never read further than that."""

    def __init__(self, limit: int, *, installation_limit: int,
                 node_limit: int | None = None, size: int | None = None) -> None:
        self.size = size
        self.limit = limit
        has = f"has {size} bytes" if size is not None else f"is larger than {limit} bytes"
        node = f"the node allows {node_limit}, " if node_limit is not None else ""
        super().__init__(
            f"The document {has}; at most {limit} are accepted "
            f"({node}the installation {installation_limit})."
        )


class DocumentMediaTypeUnsupported(DocumentRefused):
    def __init__(self, media_type: str, supported: tuple[str, ...]) -> None:
        self.media_type = media_type
        super().__init__(
            f"Content-Type {media_type!r} is not accepted. Send one of: "
            f"{', '.join(supported)}."
        )


class DocumentUnreadable(DocumentRefused):
    """The file is of an accepted type and cannot be taken: too many pages,
    no text to read, encrypted, broken. One class with named constructors,
    as `MalformedCondition` has — the reasons are the rule."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"The document cannot be read: {reason}.")

    @classmethod
    def too_many_pages(cls, pages: int, limit: int) -> DocumentUnreadable:
        return cls(f"it has {pages} pages, at most {limit} are accepted")

    @classmethod
    def no_text(cls) -> DocumentUnreadable:
        return cls("it has no text layer — a scanned page is an image, and there is "
                   "no text recognition here. Recognise the text first and send that")

    @classmethod
    def empty(cls) -> DocumentUnreadable:
        return cls("it is empty")

    @classmethod
    def encrypted(cls) -> DocumentUnreadable:
        return cls("the PDF is encrypted")

    @classmethod
    def not_a_pdf(cls) -> DocumentUnreadable:
        return cls("it is not a PDF that can be parsed")

    @classmethod
    def not_utf8_text(cls) -> DocumentUnreadable:
        return cls("it is not UTF-8 text")

    @classmethod
    def contains_nul(cls) -> DocumentUnreadable:
        return cls("it contains NUL characters, which text does not")


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
