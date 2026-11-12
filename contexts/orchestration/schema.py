"""
contexts/orchestration/schema.py

Transport shapes of the orchestration context (ADR-030, CHY-107).

Moved here from `domain/workflow.py`.

A workflow describes a directed graph of nodes and connections. Each node has a
type that determines what the runtime engine does with it:

  context_compose  -> assemble context from the scope tree
  llm_call         -> call an LLM via LiteLLM with the composed context
  tool_call        -> external tool execution (API, database query, etc.)
  fork_gateway     -> start parallel execution of multiple branches
  join_gateway     -> wait for parallel branches (JoinPolicy sets the condition)
  human_review     -> pause the workflow until a human approves the node

Why this rather than a LangGraph or Airflow configuration: workflows live in
the database and change at runtime without a deployment. Workflow-as-data is
the product feature; a config file would move the whole thing back into the
release cycle.

Two node types reach into another context — context_compose into `repository`
for the composition, human_review into `governance` for the review. Both go
through published interfaces once the runner exists (ADR-028, ADR-030 point 4).
Rebuilding either here would be a second truth about what is composed and what
is approved.

Published interface of a context is service, schema, errors — this is `schema`,
and for now the only one.

Dependencies:
  - pydantic (validation and serialization)
"""
from __future__ import annotations
from datetime import datetime, timedelta
from typing import Annotated, Any, Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, field_validator
from contexts.orchestration.output_schema import schema_problem
from kernel.redaction import identifiers_from
from kernel.schema import (
    PG_INT_MAX,
    REVIEW_AREA_NAME,
    SMALLEST_SHARE,
    RiskClass,
    share_as_stored,
)

NodeType = Literal[
    "context_compose", "llm_call", "tool_call",
    "fork_gateway", "join_gateway", "human_review",
    # New in arc42 v2.7 (CHY-82). A sub-workflow is an independent object with
    # its own endpoint, pinned version and audit trail — not a subgraph. That
    # distinction is reason 2 of ADR-028 for building an own runner.
    "sub_workflow",
    # New with ADR-048 (CHY-173): a pure function registered in steps/, named
    # by name and version. No side effects, which is what tool_call lacks.
    "compute",
    # New with ADR-049 (CHY-174): hands one document of the run to its
    # successors, fenced. Documents are not in the run input, so the edges of
    # this node decide who sees one.
    "document",
]
JoinPolicy = Literal["wait_all", "wait_any", "first_wins"]
ExecutionMode = Literal["sequential", "parallel"]

# `deprecated`, not `archived` (CHY-95 finding B3, unified here per migration
# 018). context_blocks called retirement `deprecated` and this table called it
# `archived` — two words for one thing, which cost a morning on 2026-08-13.
WorkflowStatus = Literal["draft", "active", "deprecated"]

# Who may release a human_review node, and what happens when nobody does.
#
# `approve` is deliberately not a member of ReviewTimeout, and that absence is
# the point: a gate that approves on silence is not a gate (ADR-004). The
# banking pilot fell back to `approved` twice — CHY-83 — and migration 018
# makes the value unstorable so the same mistake cannot be configured.
ReviewRole = Literal["sample", "approver", "four_eyes"]
#: The level that applied when a run reached a gate (CHY-223) — the same words.
ReviewLevel = Literal["sample", "approver", "four_eyes"]
ReviewTimeout = Literal["reject", "escalate"]

# What the runner writes (ADR-028, tables in migration 018). The same words as
# pilot_runs.status on purpose: one concept, one spelling — a second name for
# "waiting for a human" would be the duplication CHY-95 spent a ticket removing.
#
# `cancelled` (038, CHY-200) is the one word pilot runs do not have: a person
# stopped the run (Art. 14(4)(e)). A pilot run stopped the same way ends as
# `failed` with a message saying so; the pilots are being replaced by this
# runner (ADR-028), and their table is not widened for a state they will not
# keep.
#: `awaiting_documents` (041, ADR-049): the definition has document nodes and
#: not every document has arrived. No node runs; the last upload starts the run.
#:
#: `expired` (042, ADR-050): nobody decided at a gate before its time limit or
#: the content period ran out. Not `rejected` — nobody said no — and not
#: `cancelled` — nobody stopped it.
RunStatus = Literal[
    "pending", "running", "awaiting_oversight", "awaiting_documents", "complete",
    "failed", "rejected", "cancelled", "expired",
]
#: What a document may be (041). A PDF is read to text; nothing else is parsed.
DocumentMediaType = Literal["application/pdf", "text/plain"]
NodeExecutionStatus = Literal[
    "pending", "running", "complete", "failed", "skipped", "cancelled", "expired"
]


class ContextRequirements(BaseModel):
    """What a `context_compose` node asks the repository for (CHY-134).

    A typed column since migration 022, not a key in `config`. The field
    decides **which context reaches a model**, and a free-form JSONB that
    anyone may write anything into is the wrong place for that decision.

    `block_types` is required and non-empty, and both halves matter. The empty
    list would be read by the store as "no filter given", which means
    *everything* — a value that says the opposite of what it means. "No
    restriction" is spelled by having no requirements at all.

    The entries are `str` and not the repository's `BlockType`: the vocabulary
    belongs to that context and this one may not import it (ADR-030). The
    database carries the allow-list instead (migration 022), which is also the
    one enforcement point no write path goes around. Asking for a
    non-composable type would otherwise compose *nothing* and report success.
    """

    model_config = ConfigDict(extra="forbid")

    block_types: list[str] = Field(
        min_length=1,
        description="Restrict the composition to these block types. Composable "
                    "types only (ADR-037) — the database refuses the rest. "
                    "Omit the whole object to take everything the scope offers.",
    )
    tags: list[Annotated[str, Field(min_length=1)]] | None = Field(
        default=None, min_length=1,
        description="Only blocks that carry every one of these tags — a prompt "
                    'per step, e.g. ["banking:risiko"] (CHY-178). Checked on the '
                    "version that would be composed, the newest active one. Omit "
                    "for no tag filter; an empty list is refused.",
    )


class WorkflowNode(BaseModel):
    """A node in the workflow graph with a type and configuration.

    node_ref: human-readable identifier within the workflow (e.g. "summarize").
    config:   type-specific parameters — for llm_call e.g. model, temperature;
              for context_compose e.g. scope_id, block_type_filter.
    join_policy: only relevant for join_gateway nodes.
    """

    id: UUID
    workflow_id: UUID
    node_ref: str
    node_type: NodeType
    name: str | None = None
    description: str | None = None
    join_policy: JoinPolicy | None = None
    config: dict[str, Any] = {}
    # context_compose (migration 022). Refused by the database on any other
    # node type, and `config["block_types"]` is refused everywhere — the old
    # spelling would be accepted and ignored otherwise (CHY-134).
    context_requirements: ContextRequirements | None = None
    # llm_call (migration 023), and required there: the key its model is
    # routed by. Refused on every other node type (CHY-145).
    task_type: str | None = None
    # llm_call (migration 039): the JSON Schema its answer must match (CHY-175).
    output_schema: dict[str, Any] | None = None
    # compute (migration 040), and required there: the registered function and
    # its pinned version (ADR-048, CHY-173).
    function: str | None = None
    function_version: int | None = None
    # document (migration 041), and the name required there: what the document
    # is uploaded under, and limits below the installation's (ADR-049).
    document_name: str | None = None
    document_max_bytes: int | None = None
    document_max_pages: int | None = None
    # sub_workflow (migration 018). The version is pinned — a parent keeps
    # calling the child it was reviewed against.
    target_workflow_id: UUID | None = None
    target_workflow_version: int | None = None
    input_mapping: dict[str, Any] = {}
    output_mapping: dict[str, Any] = {}
    # human_review (migration 018)
    review_role: ReviewRole | None = None
    review_timeout: timedelta | None = None
    on_timeout: ReviewTimeout | None = None
    # human_review (migration 043): the areas that each cast one vote (CHY-180).
    review_areas: list[str] | None = None
    # human_review with review_role sample (migration 045): the share drawn.
    review_sample_rate: float | None = None


#: A guard is **structured**, not an expression, and it has been since CHY-130:
#: `{"op": "eq", "field": "risk", "value": "high"}`. The type here said `str`
#: until CHY-144, which meant the published contract described a value that
#: could be sent and never activated — there was no value that was valid by the
#: contract *and* valid in fact.
#:
#: The notes stay in a comment: pydantic publishes a class docstring as the
#: schema description in the OpenAPI contract (CHY-105).
class WorkflowConnection(BaseModel):
    id: UUID
    workflow_id: UUID
    source_node_id: UUID
    target_node_id: UUID
    condition: dict[str, Any] | None = Field(
        default=None,
        description='Structured guard, e.g. {"op": "eq", "field": '
                    '"risk", "value": "high"}. Checked on activation; '
                    "a malformed one refuses the release rather than being "
                    "read as false at run time (ADR-026, CHY-130).",
    )
    label: str | None = None
    execution_mode: ExecutionMode = "sequential"
    loop_limit: int | None = Field(
        default=None, gt=0,
        description="Iterations allowed on this edge per run. Mandatory on a "
                    "back edge, refused on activation if missing (ADR-026). "
                    "Per edge, not per run — two independent loops must not "
                    "limit one another.",
    )


# How much harm a wrong output of a workflow can do (CHY-223, ADR-050):
# `RiskClass`, imported from the kernel above. Since CHY-199 direct model calls
# are classified the same way, and both are read by one rule (votes.minimum).
# Two symbols could drift apart and break that rule without a test noticing.


class WorkflowDefinition(BaseModel):
    """Complete workflow definition including all nodes and edges.

    Versioned like ContextBlocks: changes produce new versions.
    Status lifecycle: draft → active → archived.
    scope_id: the scope determines which context is available for this workflow.
    """

    id: UUID
    scope_id: UUID
    name: str
    description: str | None = None
    version: int
    superseded_by_version: int | None = None
    status: WorkflowStatus
    risk_classification: dict[str, Any] = Field(
        default={}, json_schema_extra={"deprecated": True},
        description="Deprecated, read by nothing. Use risk_class (CHY-223).")
    risk_class: RiskClass | None = Field(
        default=None,
        description="How much harm a wrong output can do: minimal, limited, high. "
                    "None counts as high. With the data class of the scope it sets "
                    "the minimum review level of the gates (ADR-050).")
    nodes: list[WorkflowNode] = []
    connections: list[WorkflowConnection] = []
    created_by: UUID | None = None
    created_at: datetime


# ── what a caller sends (CHY-82) ──────────────────────────────────────────────

class WorkflowCreate(BaseModel):
    scope_id: UUID
    name: str = Field(min_length=1)
    description: str | None = None
    risk_classification: dict[str, Any] = Field(
        default={}, json_schema_extra={"deprecated": True},
        description="Deprecated, stored and read by nothing. Use risk_class (CHY-223).")
    risk_class: RiskClass | None = Field(
        default=None,
        description="How much harm a wrong output of this workflow can do. At least: "
                    "minimal → the gate as drawn, limited → one approver, high → four "
                    "eyes. The stricter of this and the data class of the scope is the "
                    "minimum of every gate; leave it out and it counts as high (ADR-050).")


#: Keys that used to be read from `config` and now live somewhere typed, with
#: where they went. Written here rather than at the `raise` because the sentence
#: is the contract of the refusal, and a reader of the model should meet it
#: next to the field (and ruff's EM101/TRY003 want it assigned anyway).
_MOVED_OUT_OF_CONFIG: dict[str, str] = {
    "block_types": (
        "config['block_types'] has moved to context_requirements (CHY-134). "
        "Send {'context_requirements': {'block_types': [...]}} instead."
    ),
    "model": (
        "config['model'] is no longer read (CHY-145). A node does not choose "
        "its model: it is resolved from the routing rules of the workflow's "
        "scope for the node's task_type, so it passes through approval, scope "
        "inheritance, the privacy ratchet and the fallback chain (ADR-038)."
    ),
    "task_type": (
        "config['task_type'] has moved to the task_type field (CHY-145). "
        "Send {'task_type': '...'} on the node instead."
    ),
    # Never read from config, refused all the same: written there it would be
    # stored and ignored, and the author would believe the answer is checked.
    "output_schema": (
        "config['output_schema'] is not read (CHY-175). Send "
        "{'output_schema': {...}} on the node instead."
    ),
    "function": (
        "config['function'] is not read (CHY-173). Send {'function': '...', "
        "'function_version': 1} on the node instead."
    ),
    "function_version": (
        "config['function_version'] is not read (CHY-173). Send it as the "
        "node's function_version field."
    ),
    "document_name": (
        "config['document_name'] is not read (CHY-174). Send it as the node's "
        "document_name field."
    ),
    "document_max_bytes": (
        "config['document_max_bytes'] is not read (CHY-174). Send it as the "
        "node's document_max_bytes field."
    ),
    "document_max_pages": (
        "config['document_max_pages'] is not read (CHY-174). Send it as the "
        "node's document_max_pages field."
    ),
}


class NodeCreate(BaseModel):
    node_ref: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9_]*$")
    node_type: NodeType
    name: str | None = None
    description: str | None = None
    join_policy: JoinPolicy | None = None
    config: dict[str, Any] = {}
    context_requirements: ContextRequirements | None = Field(
        default=None,
        description="Only for context_compose. Omit it to compose everything "
                    "the workflow's scope makes available.",
    )
    task_type: str | None = Field(
        default=None,
        description="Only for llm_call, and required there. The model is not "
                    "named on the node: it is resolved from the routing rules "
                    "of the workflow's scope for this task type (ADR-038).",
    )
    output_schema: dict[str, Any] | None = Field(
        default=None,
        description="Only for llm_call. A JSON Schema of type object: the "
                    "model's answer is read as one JSON object and checked "
                    "against it, and the checked object is in the node's "
                    "output as `data`. A mismatch fails the node (CHY-175).",
    )
    function: str | None = Field(
        default=None, pattern=r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$",
        description="Only for compute, and required there: a pure function "
                    "registered in the code, e.g. banking.ratios. Checked at "
                    "activation; its result is the node's output as `data` "
                    "(ADR-048, CHY-173).",
    )
    function_version: int | None = Field(
        default=None, ge=1, le=PG_INT_MAX,
        description="Only for compute, and required there: the pinned version "
                    "of the function. A changed calculation is a new version.",
    )
    document_name: str | None = Field(
        default=None, pattern=r"^[a-z][a-z0-9_]{0,62}$",
        description="Only for document, and required there: the name the run's "
                    "starter uploads the document under "
                    "(PUT /v1/workflows/runs/{run_id}/documents/{name}). One node "
                    "per name and workflow; the node's successors are the only "
                    "steps that see it (ADR-049, CHY-174).",
    )
    document_max_bytes: int | None = Field(
        default=None, ge=1, le=PG_INT_MAX,
        description="Only for document: the largest file accepted, in bytes. "
                    "Omit it for the installation's ceiling; above that ceiling "
                    "activation is refused.",
    )
    document_max_pages: int | None = Field(
        default=None, ge=1, le=PG_INT_MAX,
        description="Only for document: the most pages a PDF may have. Omit it "
                    "for the installation's ceiling.",
    )
    target_workflow_id: UUID | None = None
    target_workflow_version: int | None = Field(default=None, ge=1, le=PG_INT_MAX)
    input_mapping: dict[str, Any] = {}
    output_mapping: dict[str, Any] = {}
    review_role: ReviewRole | None = None
    review_timeout: timedelta | None = None
    on_timeout: ReviewTimeout | None = Field(
        default=None,
        description="reject or escalate. Never approve — a gate that approves "
                    "on silence is not a gate (ADR-004).",
    )
    review_areas: list[Annotated[str, Field(pattern=REVIEW_AREA_NAME)]] | None = Field(
        default=None, min_length=1, max_length=16,
        description="Only for human_review: the review areas that each cast one "
                    "vote, by name, as defined on the workflow's scope or above "
                    "(POST /v1/scopes/{id}/review-areas). All approve → released; "
                    "all reject → rejected; votes differ → escalated along the "
                    "escalation edge. Two or more count as four_eyes (ADR-050).",
    )

    review_sample_rate: float | None = Field(
        default=None, ge=SMALLEST_SHARE, le=1,
        description="Only for human_review with review_role sample, and required "
                    "there: the share of passes drawn into the review queue, from "
                    "0.001 to 1 with at most three decimal places. The run does not "
                    "wait at a sample gate (ADR-050).",
    )

    @field_validator("review_sample_rate")
    @classmethod
    def _share_as_stored(cls, rate: float | None) -> float | None:
        """Refused rather than rounded (CHY-276): the column keeps three places,
        and 0.0505 was stored as 0.051 without a word."""
        return share_as_stored(rate)

    @field_validator("review_areas")
    @classmethod
    def _each_area_once(cls, areas: list[str] | None) -> list[str] | None:
        """An area named twice would still cast one vote — so the second name
        promises something the gate does not do."""
        if areas is not None and len(set(areas)) != len(areas):
            msg = "review_areas names an area twice"
            raise ValueError(msg)
        return areas

    @field_validator("config")
    @classmethod
    def _moved_keys_are_refused(cls, config: dict[str, Any]) -> dict[str, Any]:
        """Keys that left `config` are refused, not ignored (CHY-134, CHY-145).

        Each was read until the migration that moved it. Accepting one now
        would answer 201 and do something else than the author asked for —
        compose other blocks, call another model — which is the same silence
        that let a deleted block reappear on 2026-08-13.

        Here *and* as CHECKs in migrations 022 and 023. Two enforcement points
        for the reason ratchet.py gives: rows reach this table by paths that do
        not pass through this model, and the one that answers with a readable
        message is not the one that cannot be bypassed.
        """
        for key, reason in _MOVED_OUT_OF_CONFIG.items():
            if key in config:
                raise ValueError(reason)
        return config

    @field_validator("output_schema")
    @classmethod
    def _output_schema_is_usable(cls, schema: dict[str, Any] | None) -> dict[str, Any] | None:
        """A schema the runner can check against, refused here rather than in the run.

        Whether it is a JSON Schema at all the database cannot tell; that it
        describes an object it checks as well (039), for rows that do not pass
        through this model.
        """
        if schema is not None:
            problem = schema_problem(schema)
            if problem:
                raise ValueError(problem)
        return schema


class ConnectionCreate(BaseModel):
    source_node_ref: str
    target_node_ref: str
    condition: dict[str, Any] | None = Field(
        default=None,
        description='Structured guard, e.g. {"op": "eq", "field": '
                    '"risk", "value": "high"}. A string is refused by '
                    "the model: the column has been JSONB since migration 020, "
                    "and a string reached activation only to be rejected there "
                    "(CHY-144).",
    )
    label: str | None = None
    execution_mode: ExecutionMode = "sequential"
    loop_limit: int | None = Field(default=None, gt=0, le=PG_INT_MAX)


# Only the status. Changing name or scope of a released workflow is a new
# version, not an edit — the same rule context_blocks follows.
class WorkflowStatusPut(BaseModel):
    status: WorkflowStatus


class WorkflowListOut(BaseModel):
    items: list[WorkflowDefinition]
    total: int


class ViolationOut(BaseModel):
    rule: str = Field(description="Stable identifier — branch on this.")
    message: str = Field(description="For a person to read. Do not parse.")
    node_ref: str | None = None
    connection_id: UUID | None = None


class ValidationOut(BaseModel):
    workflow_id: UUID
    valid: bool
    violations: list[ViolationOut] = []


# ── runs (CHY-127) ────────────────────────────────────────────────────────────

class WorkflowRunStart(BaseModel):
    input_data: dict[str, Any] = Field(
        default={},
        description=(
            "The run's input, reachable from every node as `run.<key>`. It is "
            "stored with placeholders: every text in it is replaced by the "
            "shipped patterns and the scope's redaction rules before the run "
            "begins, and every step reads and writes placeholders (CHY-234). "
            "The key `identifiers_to_redact` names further values that identify "
            'someone, by category, e.g. {"PERSON": ["Erika Mustermann"]}. It is '
            "not stored with the input: its values go into the run's encrypted "
            "mapping and are searched for in every step, found in the input or not. "
            "Documents are not sent here: a workflow with document nodes starts "
            "as `awaiting_documents`, and each document is uploaded on its own "
            "(ADR-049)."
        ),
    )

    @field_validator("input_data")
    @classmethod
    def _identifiers_are_usable(cls, value: dict[str, Any]) -> dict[str, Any]:
        # Checked when the run starts, not at its first model call (CHY-63): a
        # run that would fail at its third node over a typo in the list should
        # not be started at all.
        identifiers_from(value.get("identifiers_to_redact"))
        return value


class WorkflowRunOut(BaseModel):
    id: UUID
    workflow_id: UUID
    workflow_version: int = Field(
        description="Which version actually ran. Pinned like a sub-workflow "
                    "target — the question an audit asks."
    )
    status: RunStatus
    actor_id: UUID | None = None
    input_data: dict[str, Any] = {}
    result: dict[str, Any] | None = None
    error_message: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime
    parent_run_id: UUID | None = Field(
        default=None,
        description="Set when this run was started by a sub_workflow node. An "
                    "independent run with its own audit trail, not a subgraph "
                    "(ADR-028).",
    )
    parent_node_id: UUID | None = None
    content_purged_at: datetime | None = Field(
        default=None,
        description="When the retention purge cleared this run's input, result "
                    "and node content (ADR-046). Null while the content is there; "
                    "an empty input then means the input was empty.",
    )


class NodeExecutionOut(BaseModel):
    """One pass through one node of a run, as a reader sees it (CHY-229)."""

    node_ref: str
    node_type: NodeType
    name: str | None = None
    iteration: int
    status: NodeExecutionStatus
    model_used: str | None = None
    output: dict[str, Any] | None = Field(
        default=None,
        description="What the node produced: for an llm_call the model's answer "
                    "under `content`, for a context_compose the composed "
                    "`prompt`, for a human_review the decision. Null until the "
                    "node completes.",
    )
    error_message: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None


class ExhaustedLoopOut(BaseModel):
    """A loop of the run that used up its loop_limit (CHY-150)."""

    from_ref: str = Field(description="The node the loop goes back from.")
    to_ref: str = Field(description="The node it goes back to.")
    loop_limit: int


class RunNodesOut(BaseModel):
    """What each node of a run produced, in the order it ran (CHY-229).

    Until CHY-229 none of this left the database. A run waiting at a
    `human_review` gate showed its status and its input; whoever decided there
    decided without seeing the output the decision was about. Article 14 of the
    AI Act asks for the opposite: the person overseeing must be able to read
    what the system produced. `WorkflowRunOut.result` deliberately does not
    carry a copy — the outputs live in one place, and every deletion path
    (CHY-17) has to know only that one.
    """

    run_id: UUID
    status: RunStatus
    nodes: list[NodeExecutionOut]
    exhausted_loops: list[ExhaustedLoopOut] = Field(
        default_factory=list,
        description="Loops that ran until their loop_limit (CHY-150). Read this at "
                    "a gate: an output that arrives here after its loop ran out has "
                    "not met the loop's criterion — only its budget was spent.",
    )


class RunResolveRequest(BaseModel):
    """Which steps to have resolved; the run's input always comes with them."""

    model_config = ConfigDict(extra="forbid")

    nodes: list[str] | None = Field(
        default=None,
        description="node_refs whose output is wanted with the values; null for "
                    "every step. A ref the run has no step for is left out.",
    )


class RunResolvedOut(BaseModel):
    """A run's input and its steps' outputs with the values in place of the
    placeholders (CHY-234).

    A run is stored with placeholders, and reading it shows them. Whoever has
    to check that the right person is meant -- the starter, and from `reviewer`
    up whoever sees the run (decided 06.11.2026, E4) -- asks for this instead,
    and every request is audited as `workflow.run_content_resolved`: who, which
    run, which steps, never a value. A run started before CHY-234 is returned
    as it is stored; its values were never replaced.
    """

    run_id: UUID
    status: RunStatus
    input_data: dict[str, Any]
    nodes: list[NodeExecutionOut]


class DocumentFinding(BaseModel):
    """What the scan of a document found, by kind and rule (CHY-135).

    Counted, never quoted: an excerpt would be document text, in the audit log
    kept for good and in a response anyone may log.
    """

    kind: str = Field(description="fence_break (a fact) or instruction_shape (a suspicion).")
    rule: str
    count: int


class RunDocumentOut(BaseModel):
    """One document a run expects, and what arrived for it (ADR-049)."""

    name: str
    received: bool
    media_type: DocumentMediaType | None = None
    bytes: int | None = Field(default=None, description="Size of the file as it arrived.")
    pages: int | None = Field(default=None, description="PDF only.")
    characters: int | None = Field(default=None, description="Length of the text read from it.")
    findings: list[DocumentFinding] = Field(
        default=[],
        description="Empty for a document that was scanned and showed nothing. "
                    "A finding does not stop the run: the text reaches every step "
                    "fenced, as foreign data (CHY-135).",
    )
    received_at: datetime | None = None


class RunDocumentsOut(BaseModel):
    """The documents of a run: which it expects, which have arrived (ADR-049).

    A run whose definition has `document` nodes waits in `awaiting_documents`
    until each has arrived, and starts with the last one — the status here is
    the run's after that.
    """

    run_id: UUID
    status: RunStatus
    documents: list[RunDocumentOut]


# `approved` and `rejected`, and nothing else. No timeout value here on
# purpose: what happens when nobody decides is a property of the node
# (`on_timeout`, never `approve`), not of a decision somebody made (ADR-004).
OversightChoice = Literal["approved", "rejected"]


class RunCancelRequest(BaseModel):
    """Stopping a run (CHY-200). The reason is optional: who stopped it, and
    when, is recorded either way."""

    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(
        default=None,
        description="Why the run is stopped. Stored in the run's error_message "
                    "with the run's placeholders, like a reason at the gate.",
    )


SampleVerdict = Literal["confirmed", "objected"]


#: Where something in the review queue was drawn from (ADR-050).
SampleSource = Literal["workflow", "direct_call"]


class ReviewSampleOut(BaseModel):
    """Something drawn for a check afterwards, and the verdict on it (ADR-050).

    One queue, two sources (decided by Thomas, 10.11.2026): a pass of a sample
    gate in a workflow (CHY-223), and a direct call answered in a scope whose
    minimum review is `sample` (CHY-269). The verdict means the same for both:
    the output went on, and a person judges it afterwards.
    """

    sample_id: UUID = Field(
        description="A workflow pass: the gate's execution id. A direct call: its "
                    "id, as /v1/complete returned it in sample_id.")
    source: SampleSource
    scope_id: UUID = Field(
        description="Where it belongs: the workflow's scope, or the scope of the call.")
    drawn_at: datetime
    verdict: SampleVerdict | None = None
    reviewed_by: UUID | None = None
    reviewed_at: datetime | None = None
    run_id: UUID | None = Field(
        default=None, description="A workflow pass: its run. The output to judge is "
                                  "the run's: GET /v1/workflows/runs/{run_id}/nodes.")
    node_ref: str | None = Field(default=None, description="A workflow pass: its gate.")
    task_type: str | None = Field(default=None, description="A direct call: its task type.")
    model_used: str | None = Field(
        default=None, description="A direct call: the model that answered.")


class ReviewSampleDetail(ReviewSampleOut):
    """One entry of the review queue with what there is to judge.

    For a direct call the prompt and the answer as the model saw and gave them:
    placeholders where an identifier was replaced, never the value (CHY-63). The
    context is named by its block versions. Both are cleared when the scope's
    content period ends. For a workflow pass the output is the run's.
    """

    prompt: str | None = None
    answer: str | None = None
    block_versions: dict[str, int] = Field(default_factory=dict)
    content_purged_at: datetime | None = None


class ReviewSampleVerdict(BaseModel):
    verdict: SampleVerdict
    reason: str | None = Field(
        default=None,
        description="Why. Stored with placeholders — a workflow pass's by its run's "
                    "mapping, a direct call's by its scope's rules — and cleared with "
                    "the content. An objection recalls nothing — the output is out — "
                    "and is counted (ADR-050).",
    )


class OversightDecision(BaseModel):
    decision: OversightChoice
    area: str | None = Field(
        default=None, pattern=REVIEW_AREA_NAME,
        description="At a gate with review_areas: the area this vote is cast for. "
                    "Needed only by someone who belongs to more than one of the "
                    "gate's areas; otherwise it is their area (CHY-180).",
    )
    reason: str | None = Field(
        default=None,
        description="Why. Carried into the audit entry and, on a rejection, "
                    "into the run's error_message — it is the only artefact of "
                    "the decision anyone downstream will see.",
    )
