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
from typing import Any, Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, field_validator
from kernel.schema import PG_INT_MAX

NodeType = Literal[
    "context_compose", "llm_call", "tool_call",
    "fork_gateway", "join_gateway", "human_review",
    # New in arc42 v2.7 (CHY-82). A sub-workflow is an independent object with
    # its own endpoint, pinned version and audit trail — not a subgraph. That
    # distinction is reason 2 of ADR-028 for building an own runner.
    "sub_workflow",
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
ReviewRole = Literal["approver", "four_eyes"]
ReviewTimeout = Literal["reject", "escalate"]

# What the runner writes (ADR-028, tables in migration 018). The same words as
# pilot_runs.status on purpose: one concept, one spelling — a second name for
# "waiting for a human" would be the duplication CHY-95 spent a ticket removing.
RunStatus = Literal[
    "pending", "running", "awaiting_oversight", "complete", "failed", "rejected"
]
NodeExecutionStatus = Literal["pending", "running", "complete", "failed", "skipped"]


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
    risk_classification: dict[str, Any] = {}
    nodes: list[WorkflowNode] = []
    connections: list[WorkflowConnection] = []
    created_by: UUID | None = None
    created_at: datetime


# ── what a caller sends (CHY-82) ──────────────────────────────────────────────

class WorkflowCreate(BaseModel):
    scope_id: UUID
    name: str = Field(min_length=1)
    description: str | None = None
    risk_classification: dict[str, Any] = {}


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
    input_data: dict[str, Any] = {}


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


# `approved` and `rejected`, and nothing else. No timeout value here on
# purpose: what happens when nobody decides is a property of the node
# (`on_timeout`, never `approve`), not of a decision somebody made (ADR-004).
OversightChoice = Literal["approved", "rejected"]


class OversightDecision(BaseModel):
    decision: OversightChoice
    reason: str | None = Field(
        default=None,
        description="Why. Carried into the audit entry and, on a rejection, "
                    "into the run's error_message — it is the only artefact of "
                    "the decision anyone downstream will see.",
    )
