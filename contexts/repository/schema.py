"""
contexts/repository/schema.py

Transport shapes of the repository context (ADR-030, CHY-105).

Two files merged here: `domain/blocks.py` (the block itself, its versions and
its lifecycle payloads) and `domain/compose.py` (the composition read path).
They describe the same context from two sides — what a block *is*, and what
comes out when the blocks of a scope chain are assembled — and the layout in
contexts/__init__.py calls for one `schema.py`, not one per subject.

A ContextBlock is a versioned, typed piece of context that lives in a scope.

Block types and their meaning:
  system_prompt       → instructions for the model (role, behavior, language)
  user_context        → factual knowledge, domain knowledge, project context
  tool_schema         → JSON schema for function calling / tool use
  example_io          → few-shot examples (input → expected output)
  calibration_anchor  → a benchmark test case; never composed (ADR-043)
  routing_rule        → which model handles a task; never composed (ADR-037)
  redaction_rule      → what is replaced before a model sees it; never composed
                        (ADR-018, CHY-63)

Versioning: each block has a block_id (logical id) and a version number. An
update writes a new row with an incremented version; exactly one row per line
has superseded_by_version IS NULL and is therefore current.

Why ComposedContext carries block_versions:
  A composed context is only reproducible if it says which versions went into
  it. A run recorded as "used the active blocks" is not reproducible six months
  later, because "active" has moved on. EU AI Act Art. 12 asks for records that
  can be re-examined, and this is the field that makes that possible.

Published interface of a context is service, schema, errors — this is `schema`.
"""
from __future__ import annotations
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID
from pydantic import BaseModel, Field
from kernel.schema import PG_INT_MAX

BlockType = Literal[
    "system_prompt", "user_context", "tool_schema", "example_io",
    "calibration_anchor",
    # Not prompt material. See COMPOSABLE_BLOCK_TYPES below (CHY-87, ADR-037).
    "routing_rule",
    # Not prompt material either (CHY-63): composed into a prompt, it would hand
    # the model the values it exists to keep away from it.
    "redaction_rule",
]

#: The types that may be concatenated into a prompt.
#:
#: Until CHY-87 the distinction did not exist, because every block type WAS
#: prompt material — `compose` without a type filter took everything active in
#: the scope, and that was correct. `routing_rule` is the first entry that must
#: never reach a model: it is configuration, and pasting it into a system
#: prompt would tell the model which models exist and how to pick one.
#:
#: The set is the allow-list, not a deny-list of the one exception. A sixth
#: type added tomorrow is non-composable until somebody says otherwise, which
#: is the safe direction to be wrong in.
#:
#: `calibration_anchor` left the set with CHY-67 (ADR-043). An anchor carries
#: the expected answer of a benchmark case; composed into a prompt it would
#: hand the model the answer it is measured on. It stayed harmless only because
#: no anchor existed on any stage when the defect was found.
COMPOSABLE_BLOCK_TYPES: frozenset[str] = frozenset({
    "system_prompt", "user_context", "tool_schema", "example_io",
})
# Mirrors the CHECK on context_blocks.status (migration 013);
# tests/test_status_vocabularies.py holds the two together, because SQL cannot
# import Python. The transition *graph* lives in cc_context_blocks_append_only,
# not here — a vocabulary says which states exist, not which moves are legal.
#
# A StrEnum rather than a Literal (CHY-95): these values are read in code, not
# only sent over the wire. `status == BlockStatus.DRAFT` is a typo the
# interpreter catches; `status == "draf"` is one it does not, and this
# vocabulary was compared against a bare string in eleven places.
#
# The notes stay in a comment — pydantic publishes a docstring as the schema
# description in the OpenAPI contract (CHY-105).
class BlockStatus(StrEnum):
    """The state of a context block in the review workflow."""

    DRAFT = "draft"
    PENDING_REVIEW = "pending_review"
    ACTIVE = "active"
    REJECTED = "rejected"
    DEPRECATED = "deprecated"


#: The one state in which content may still be edited in place. Named because
#: the same rule is encoded in the trigger (migration 013) and in the PATCH
#: branch; a constant makes the third copy a reference.
EDITABLE_STATUS = BlockStatus.DRAFT

# The direction a governance claim points (CHY-62, ADR-017). Mirrors the CHECK
# on context_blocks.privacy_impact; contexts/repository/ratchet.py holds the
# same pair and a test keeps all three honest.
PrivacyImpact = Literal["restrictive", "permissive"]


class ContextBlock(BaseModel):
    """A versioned, typed context block in a scope.

    id:                    Database row ID (unique per version)
    block_id:              Logical ID — stays the same across all versions
    version:               Monotonically increasing version number (starts at 1)
    superseded_by_version: Version of the successor; None = this is the current version
    metadata:              Free dict for model hints, language tags, weight, etc.
    tags:                  Categorization tags for selective composition
    """

    id: UUID
    block_id: UUID
    version: int
    superseded_by_version: int | None = None
    scope_id: UUID
    block_type: BlockType
    content: str
    metadata: dict[str, Any] = {}
    tags: list[str] = []
    author_id: UUID | None = None
    status: BlockStatus = "draft"
    governance_key: str | None = None
    privacy_impact: PrivacyImpact | None = None
    locked: bool = False
    created_at: datetime

    @property
    def is_current(self) -> bool:
        """True if this block is the current (non-superseded) version."""
        return self.superseded_by_version is None


# --- Phase 1 API Schemas ---

# The three governance fields are set at creation and frozen once the block
# leaves draft (migration 016). Declaring the claim up front is what makes the
# review a review of the claim as well as of the text.
class BlockCreate(BaseModel):
    scope_id: UUID
    block_type: BlockType
    content: str
    metadata: dict[str, Any] = {}
    tags: list[str] = []
    governance_key: str | None = Field(
        default=None,
        description="Which governance rule this block speaks to. Two blocks "
                    "are compared by the privacy ratchet only when this "
                    "matches. Must be a key from the vocabulary (ADR-017).",
    )
    privacy_impact: PrivacyImpact | None = Field(
        default=None,
        description="restrictive tightens the rule, permissive loosens it. "
                    "Required together with governance_key.",
    )
    locked: bool = Field(
        default=False,
        description="No scope below may state this governance_key at all — "
                    "not even more strictly.",
    )


class BlockPromote(BaseModel):
    """Where to copy a block to (ADR-042 point 3)."""

    target_scope_id: UUID = Field(
        description="A strict scope (org, team, workspace) above the block's own "
                    "scope. The copy lands there as a draft and goes through "
                    "review like any other.",
    )


class BlockPatch(BaseModel):
    block_type: BlockType | None = None
    content: str | None = None
    metadata: dict[str, Any] | None = None
    tags: list[str] | None = None
    # Only used when the patch supersedes a published block (CHY-30): it lands
    # in the audit entry that cc_supersede_block writes. Editing a draft in
    # place is not a change to the record, so it has nothing to explain.
    change_reason: str | None = None
    # Governance may only be changed while the block is a draft (CHY-62). In a
    # strict scope a patch that supersedes a published block carries the claim
    # over unchanged and refuses these three, so changing a claim is two
    # deliberate steps a reviewer sees: publish a new draft, then restate the
    # claim on it. In a direct scope they ride along, because no draft is left
    # standing to restate it on (ADR-042).
    governance_key: str | None = None
    privacy_impact: PrivacyImpact | None = None
    locked: bool | None = None


class BlockVersionOut(BaseModel):
    """One entry in a block's version history (CHY-30).

    Deliberately without `content`: a history listing is a timeline, and
    shipping the full text of every version turns a ten-version block into a
    response nobody wanted. The content of a specific version comes from
    GET /v1/blocks/{block_id}?version=n.
    """

    id: UUID
    version: int
    superseded_by_version: int | None
    status: BlockStatus
    author_id: UUID | None
    created_at: datetime
    is_current: bool

    @property
    def is_published(self) -> bool:
        """Has this version ever been approved?

        `status != "draft"` would have said yes for pending_review and
        rejected, which is exactly backwards: neither was ever published. Only
        a version that reached `active` was, and `deprecated` means it was and
        then retired.
        """
        return self.status in (BlockStatus.ACTIVE, BlockStatus.DEPRECATED)


class BlockHistoryOut(BaseModel):
    block_id: UUID
    versions: list[BlockVersionOut]
    total: int


class BlockOut(BaseModel):
    id: UUID
    block_id: UUID
    version: int
    scope_id: UUID
    block_type: str
    content: str
    metadata: dict[str, Any]
    tags: list[str]
    author_id: UUID | None
    status: BlockStatus
    governance_key: str | None = None
    privacy_impact: str | None = None
    locked: bool = False
    created_at: datetime


class BlockListOut(BaseModel):
    items: list[BlockOut]
    total: int
    page: int
    page_size: int


class BlockTransition(BaseModel):
    """Body for submit, withdraw and approve.

    `version` is not optional. A reviewer approves a specific text, and between
    their GET and their POST the author may have patched the block into a new
    version — the endpoint compares and refuses with 409 rather than letting
    somebody approve content they never read.
    """

    version: int = Field(ge=1, le=PG_INT_MAX,
                         description="The version the caller believes is current.")


class BlockRejection(BlockTransition):
    """Body for reject.

    The reason is required and must not be blank: it is the only artefact of
    the decision the author will ever see, and "rejected" with no explanation
    turns a review into an obstacle.
    """

    reason: str = Field(min_length=3, description="Why the block was rejected.")


class BlockQuery(BaseModel):
    """What a listing asks for.

    An object rather than five parameters threaded through transport, service
    and store: the set grows with every facet, and a call chain carrying five
    of them is where the scope and the type get swapped without anything
    noticing.

    `status` is what the caller asked for — optional, and validated by the
    service. What the store receives is the resolved set, because "no filter"
    means the default statuses and not "everything", and nothing below the
    service should have to know that.
    """

    scope_id: UUID | None = None
    block_type: str | None = None
    # Deliberately `str`, not BlockStatus (CHY-95). Typing it would let pydantic
    # reject a wrong value with a generic 422 before the service is reached, and
    # the refusal that names the five alternatives — the reason
    # check_status_filter exists at all — would never run. A filter value is the
    # one place where a helpful error beats an early one.
    status: str | None = None
    page: int = Field(1, ge=1, le=PG_INT_MAX)
    page_size: int = Field(20, ge=1, le=100)


# ── Composition read path (CHY-61, ADR-010) ─────────────────────────────────

class ComposeRequest(BaseModel):
    """What to compose for.

    scope_id identifies the leaf; the ancestors are derived from its ltree path
    rather than sent by the caller. A caller that names its own ancestry could
    name a different one than the tree says, and the composed result would
    depend on who asked rather than on where the node lives.
    """

    scope_id: UUID
    workflow_id: UUID | None = None
    node_ref: str | None = None
    model_id: str | None = Field(
        default=None,
        description="Part of the cache key: auto_route and fallback chains can "
                    "pick different models for the same node, and each needs its "
                    "own compiled form (ADR-010).",
    )
    block_types: list[str] | None = Field(
        default=None, description="Restrict to these block types. None = all."
    )
    refresh: bool = Field(
        default=False, description="Skip the cache read and recompile."
    )


class ComposedBlock(BaseModel):
    """One block as it entered the composition."""

    block_id: UUID
    version: int
    block_type: str
    content: str
    scope_id: UUID
    scope_path: str
    depth: int = Field(description="Levels below the root; deeper blocks come "
                                   "later in the composed prompt.")
    tags: list[str] = []
    governance_key: str | None = Field(
        default=None,
        description="Set when this block makes a governance claim (ADR-017). "
                    "A composed context that carries one is proof the ratchet "
                    "was satisfied at compile time.",
    )
    privacy_impact: str | None = None
    locked: bool = False


class ComposedContext(BaseModel):
    scope_id: UUID
    workflow_id: UUID | None = None
    node_ref: str | None = None
    model_id: str | None = None
    blocks: list[ComposedBlock]
    block_versions: dict[str, int] = Field(
        default_factory=dict,
        description="block_id → version. The record of what this composition "
                    "actually contained, for reproducing a run later.",
    )
    scope_generations: dict[str, int] = Field(
        default_factory=dict,
        description="scope_id → generation counter at compile time (ADR-010).",
    )
    compiled_at: datetime
    cache_hit: bool = False

    def as_prompt(self, separator: str = "\n\n---\n\n") -> str:
        """Flatten to a single string in composition order.

        Kept here rather than in the endpoint: whoever renders a composed
        context — /v1/compose, /v1/complete, a future SDK — must render it the
        same way, or two callers with identical context get different prompts.
        """
        return separator.join(b.content for b in self.blocks)

    def to_cache(self) -> dict[str, Any]:
        payload = self.model_dump(mode="json")
        payload.pop("cache_hit", None)
        return payload
