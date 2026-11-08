"""
contexts/repository/service.py

Use cases of the repository context (ADR-030, CHY-105).

The published interface, together with schema and errors. Each function owns a
transaction, asks the aggregate for the decision and the store for the
execution, and returns something that has nothing to do with HTTP.

**What this buys, concretely:** the approval lifecycle — submit, withdraw,
approve, reject — was reachable only over HTTP. "What is approved" is the
central fact of this product, and neither the CLI, nor a background job, nor
the runner of ADR-028 could establish it. `transition()` below is callable from
any of them, and ADR-030 point 4 depends on exactly that: orchestration uses
this interface instead of building a second approval mechanism, because two
mechanisms would be two truths about what is approved.
"""
from __future__ import annotations

import contextlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from contexts.repository import block as rules
from contexts.repository import four_eyes
from contexts.repository import ratchet
from contexts.repository import store
from contexts.repository.block import DeletePlan, PatchPlan
from contexts.repository.compose import bump_scope_generation, compose
from contexts.repository.errors import BlockNotFound, BlockOutsideScope, NothingToPromote
from contexts.repository.schema import (
    COMPOSABLE_BLOCK_TYPES,
    BlockCreate,
    BlockStatus,
    BlockQuery,
    BlockHistoryOut,
    BlockListOut,
    BlockOut,
    BlockPatch,
    BlockPromote,
    BlockVersionOut,
    ComposedContext,
    ComposeRequest,
)
from kernel import scopes
from kernel.scopes import ReviewMode
from kernel.access import ResolvedActor, require
from kernel.errors import ScopeNotFound
from kernel.audit import (
    ACTION_BLOCK_CREATED,
    ACTION_BLOCK_DEPRECATED,
    ACTION_BLOCK_EDITED,
    ACTION_BLOCK_PROMOTED,
    entries_for,
    target_ref,
    write_audit,
)
from kernel.identity import Caller, count_people_who_may_approve, resolve_actor

logger = logging.getLogger(__name__)

#: Published so the composition root can type what it hands in (CHY-163).
RoutesBelowCritical = four_eyes.RoutesBelowCritical
Verdict = four_eyes.Verdict


@dataclass(frozen=True)
class Approval:
    """An approved block, and how four eyes was satisfied (ADR-045).

    `four_eyes` is None for a block that does not decide where data may go —
    the overwhelming majority. The transport layer turns a self-approval into
    a notice the client can show; the context only says that it happened.
    """

    block: BlockOut
    four_eyes: Verdict | None


def _out(row) -> BlockOut:
    d = store.row_to_dict(row)
    return BlockOut(
        id=d["id"], block_id=d["block_id"], version=d["version"],
        scope_id=d["scope_id"], block_type=d["block_type"], content=d["content"],
        metadata=d["metadata"], tags=list(d["tags"]),
        author_id=d.get("author_id"), status=d["status"], created_at=d["created_at"],
        # Read strictly, not with .get (CHY-62). Every query that feeds _out
        # selects these; a tolerant read would answer "makes no claim" for a
        # row that simply came from a query somebody forgot to extend, and the
        # API would drop a governance claim without anything going wrong.
        governance_key=d["governance_key"], privacy_impact=d["privacy_impact"],
        locked=d["locked"],
    )


# ── composition ───────────────────────────────────────────────────────────────

async def compose_context(pool, redis, req: ComposeRequest) -> ComposedContext:
    """Assemble the context a scope composes, preferring the cache.

    Published here rather than letting callers import compose.py, because
    compose.py is internal: the guard rule context_encapsulation says only
    service, schema and errors leave the context, and it caught two endpoints
    reaching past this line.

    An empty result is a valid answer — a scope that exists but has no active
    blocks yet is a normal state during setup. Only a scope that does not exist
    raises (ScopeNotFound).
    """
    return await compose(pool, redis, req)


# ── reading ───────────────────────────────────────────────────────────────────

async def get_block(pool, block_id: UUID) -> BlockOut:
    row = await store.current_block(pool, block_id)
    if row is None:
        raise BlockNotFound
    return _out(row)


async def list_blocks(pool, query: BlockQuery) -> BlockListOut:
    """Current versions, filtered and paged.

    An absent status filter means the default set, not "everything": a caller
    who asks for nothing in particular is not asking for retired blocks.
    """
    statuses = (
        (rules.check_status_filter(query.status),) if query.status is not None
        else rules.LISTED_BY_DEFAULT
    )
    rows, total = await store.count_and_page(pool, query, statuses)
    return BlockListOut(
        items=[_out(r) for r in rows], total=total,
        page=query.page, page_size=query.page_size,
    )


async def get_history(pool, block_id: UUID) -> BlockHistoryOut:
    rows = await store.all_versions(pool, block_id)
    if not rows:
        raise BlockNotFound
    return BlockHistoryOut(
        block_id=block_id,
        versions=[
            BlockVersionOut(
                id=r["id"], version=r["version"],
                superseded_by_version=r["superseded_by_version"],
                status=r["status"], author_id=r["author_id"],
                created_at=r["created_at"],
                # Derived rather than stored: a second place to say the same
                # thing is a second place for it to be wrong. The database
                # already enforces one row per line with a NULL here
                # (idx_blocks_one_active).
                is_current=r["superseded_by_version"] is None,
            )
            for r in rows
        ],
        total=len(rows),
    )


async def is_author(pool, block_id: UUID, actor_id: UUID) -> bool:
    """Whether this actor wrote any version of the line.

    Published for the audit endpoint, which decides from it who may read a
    block's entries. It asked `context_blocks` directly before — a module that
    owns `audit_log` reaching into a table it does not.
    """
    return await store.is_author(pool, block_id, actor_id)


async def active_blocks(pool, block_ids: list[UUID], scope_id: UUID) -> list:
    """The newest active version of each named block, in composition order.

    For an explicit block list, as opposed to composing a whole scope chain.
    Only blocks the scope could compose itself are served: from its own chain
    (the scope and its ancestors) and of a composable type. The caller chose
    and checked the model for `scope_id`; a block from elsewhere in the tree
    would reach that model without its own data class ever being asked
    (CHY-196). Raises BlockOutsideScope rather than dropping such a block.

    A named block with no active version is still left out without an error,
    as before: that is a block in review, not one the caller may not see.
    """
    chain = await scopes.ancestor_chain(pool, scope_id)
    if not chain:
        raise ScopeNotFound(scope_id)
    allowed = {row["id"] for row in chain}
    rows = await store.active_blocks_by_id(pool, block_ids)
    outside = [
        r for r in rows
        if r["scope_id"] not in allowed or r["block_type"] not in COMPOSABLE_BLOCK_TYPES
    ]
    if outside:
        raise BlockOutsideScope(len(outside))
    return rows


# ── writing ───────────────────────────────────────────────────────────────────

async def _check_ratchet(conn, row) -> None:
    """Refuse a block whose governance claim loosens or restates one above it.

    Runs on the transaction's connection, not the pool: the block was read
    FOR UPDATE a few lines earlier, and asking a second connection would judge
    the claim against a chain that another approval may have changed in
    between.

    A block that makes no claim — the overwhelming majority — costs nothing:
    the first line returns before any query runs.
    """
    if row["governance_key"] is None:
        return

    chain = await scopes.ancestor_chain(conn, row["scope_id"])
    depth_of = {r["id"]: r["depth"] for r in chain}

    rows = await store.active_governance_claims(
        conn, list(depth_of), row["governance_key"], exclude_block_id=row["block_id"]
    )
    existing = [ratchet.claim_from_row(r, depth_of[r["scope_id"]]) for r in rows]

    claim = ratchet.claim_from_row(row, depth_of[row["scope_id"]])
    ratchet.check_claim(claim, [c for c in existing if c is not None])


#: A check the caller wants run on the locked row before it becomes `active`.
#: Raises to refuse. Receives the connection so it reads inside the same
#: transaction — on exactly the row that is about to be approved.
BeforeActivation = Callable[[Any, Any], Awaitable[None]]


@dataclass(frozen=True)
class ActivationHooks:
    """What the composition root knows about activating a block (ADR-030).

    `before_activation` raises to refuse a row about to become `active`: a
    routing rule held against the models' ceilings (CHY-160), an anchor
    against its validators (ADR-043). `routes_below_critical` says whether a
    block sends data to a model below `critical`, which makes it a data-flow
    block that needs a second person (ADR-045). Both answers belong to the
    router or the learning context, which this context may not import.

    One object, because every path that activates needs both and the
    composition root always hands in both — two loose parameters on every
    function in between were two chances to pass one and forget the other.
    """

    before_activation: BeforeActivation | None = None
    routes_below_critical: RoutesBelowCritical | None = None


#: For callers without a composition root — tests, scripts. Only a loosening
#: privacy claim then counts as a data-flow block.
NO_HOOKS = ActivationHooks()


async def create_block(pool, redis, caller: Caller, body: BlockCreate, *,
                       hooks: ActivationHooks = NO_HOOKS) -> BlockOut:
    """Create version 1 of a new block.

    A draft in a strict scope; active at once in a direct one (ADR-011,
    ADR-042) — in the same transaction as the insert, so a block the ratchet
    or `before_activation` refuses is not left behind as a draft nobody asked
    for. The caller asked for a live block and gets the refusal instead.

    A data-flow block in a direct scope is the exception (ADR-045): checked as
    for activation, then handed to review, where a second person approves it.

    Audited as `block.created` since CHY-171. The action was defined from the
    start and written by nothing, so a block had no record of who wrote it
    until somebody approved it — and four eyes asks exactly that.
    """
    author = await resolve_actor(pool, caller)
    require(author, "contributor")
    # Before the insert, so a typo costs a sentence rather than a foreign-key
    # violation arriving as a 500 (CHY-62).
    ratchet.check_key_known(body.governance_key, await store.known_governance_keys(pool))

    async with pool.acquire() as conn, conn.transaction():
        row = await store.insert_block(conn, body, author.id)
        await write_audit(
            conn, action=ACTION_BLOCK_CREATED, actor_id=author.id,
            target=target_ref("block", row["block_id"]),
            payload={"version": row["version"], "block_type": row["block_type"],
                     "scope_id": str(row["scope_id"])},
        )
        # After the insert: it has just proven the scope exists, so a missing
        # scope stays the 404 it was (CHY-165) rather than a strict default.
        if await scopes.review_mode_of(conn, row["scope_id"]) is ReviewMode.DIRECT:
            row = await _publish_directly(conn, row, author, hooks)

    await _invalidate_if_composed(redis, BlockStatus.DRAFT, row["status"], row["scope_id"])
    return _out(row)


async def patch_block(pool, redis, caller: Caller, block_id: UUID, body: BlockPatch, *,
                      hooks: ActivationHooks = NO_HOOKS) -> tuple[BlockOut, bool]:
    """Change a block. Returns the result and whether a new version was created.

    Two things depending on the state of the current version (CHY-30): a draft
    is edited in place, anything else is superseded. The caller sees the
    difference as 200 versus 201, and that matters — silently doing one or the
    other would leave a client unable to tell whether its change is live or
    waiting for review.

    Why not a separate endpoint for "publish new version": the intent is the
    same either way — change this block. Which mechanism that requires is a
    property of the block's state, not of the request, and asking clients to
    track it would push the rule into every client instead of keeping it here.

    In a direct scope the result is then activated, as on create (ADR-042).
    That is also why a governance claim may ride along with a supersede there:
    the claim is frozen outside `draft`, a direct scope never leaves the new
    version in `draft`, and refusing the ride-along would make a claim in a
    direct scope unchangeable once it went live. The ratchet judges the claim
    on activation either way.
    """
    editor = await resolve_actor(pool, caller)
    require(editor, "contributor")

    current = await store.current_block(pool, block_id)
    if current is None:
        raise BlockNotFound
    current = store.row_to_dict(current)

    rules.check_block_type(current=current["block_type"], requested=body.block_type)

    governance = {
        k: v for k, v in (
            ("governance_key", body.governance_key),
            ("privacy_impact", body.privacy_impact),
            ("locked", body.locked),
        ) if v is not None
    }
    supersedes = rules.plan_patch(current["status"]) is PatchPlan.SUPERSEDE
    ratchet.check_key_known(body.governance_key, await store.known_governance_keys(pool))

    async with pool.acquire() as conn, conn.transaction():
        direct = await scopes.review_mode_of(conn, current["scope_id"]) is ReviewMode.DIRECT
        ratchet.check_governance_editable(
            plan_supersedes=supersedes and not direct, requested=governance,
        )

        if supersedes:
            merged = rules.merge_patch(
                current, content=body.content, metadata=body.metadata, tags=body.tags
            )
            row = await store.supersede(
                conn, block_id, merged,
                author_id=current["author_id"], editor_id=editor.id,
                change_reason=body.change_reason,
            )
            if governance:
                # The new version is a draft until the activation below, and
                # a draft is where the claim may change (migration 016).
                row = await store.edit_draft(conn, block_id, governance)
        else:
            updates = {
                k: v for k, v in (
                    ("block_type", body.block_type), ("content", body.content),
                    ("metadata", body.metadata), ("tags", body.tags),
                ) if v is not None
            } | governance
            if not updates:
                # Nothing written, so nothing to activate either: an empty
                # PATCH is not a request to publish.
                return _out(current), False
            row = await store.edit_draft(conn, block_id, updates)
            # A draft edit left no trace until CHY-163. Who wrote a version is
            # what four eyes asks; the field names say what changed without
            # copying content into a table that keeps everything (ADR-045).
            await write_audit(
                conn, action=ACTION_BLOCK_EDITED, actor_id=editor.id,
                target=target_ref("block", block_id),
                payload={"version": row["version"], "fields": sorted(updates)},
            )

        if direct:
            row = await _publish_directly(conn, row, editor, hooks)

    await _invalidate_if_composed(redis, BlockStatus.DRAFT, row["status"], row["scope_id"])
    return _out(row), supersedes


async def transition(pool, redis, caller: Caller, block_id: UUID, name: str, *,
                     expected_version: int, reason: str | None = None,
                     hooks: ActivationHooks = NO_HOOKS) -> BlockOut:
    """Move a block through the review workflow, audited, in one transaction.

    `name` is a key of block.TRANSITIONS: submit, withdraw, approve, reject.
    One function for all four, because they differ only in the table entry —
    the four endpoints that used to pass `to_status`, `allowed_from`, `action`
    and a role each are the reason the transition table existed nowhere.

    Authorize before the transaction, re-read FOR UPDATE inside it, refuse a
    wrong pre-state, and write the audit entry on the same connection so the
    change and the record of it stand or fall together.

    `hooks.before_activation` is the seam for checks this context may not make
    itself. A routing rule has to be held against the models' ceilings, and
    those belong to the router, which a context may not import (ADR-030) — so
    the composition root passes the check in, the way it builds the executor
    registry for the runner (CHY-160). Run inside the transaction, after the
    ratchet: a check made before it would judge a row that may no longer be
    the one being approved.

    `hooks.routes_below_critical` is the same kind of seam for four eyes
    (ADR-045): whether a routing rule names a model below `critical` is the
    router's to say. Without it only a loosening privacy claim counts as a
    data-flow block.
    """
    approval = await _transition(
        pool, redis, caller, block_id, name, expected_version=expected_version,
        reason=reason, hooks=hooks,
    )
    return approval.block


async def approve(pool, redis, caller: Caller, block_id: UUID, *, expected_version: int,
                  hooks: ActivationHooks = NO_HOOKS) -> Approval:
    """Approve a block, and say how four eyes was satisfied (ADR-045).

    The approve transition with its verdict kept: a single operator who
    approves their own data-flow block is allowed to, and must be told what
    that means. The transport layer decides how to tell them; this says that
    it happened.
    """
    return await _transition(
        pool, redis, caller, block_id, "approve", expected_version=expected_version,
        reason=None, hooks=hooks,
    )


async def _transition(pool, redis, caller: Caller, block_id: UUID, name: str, *,
                      expected_version: int, reason: str | None,
                      hooks: ActivationHooks) -> Approval:
    move = rules.TRANSITIONS[name]
    performer = await resolve_actor(pool, caller)
    require(performer, move.role)

    extra = {"reason": reason} if reason is not None else {}

    async with pool.acquire() as conn, conn.transaction():
        row = await store.lock_current_block(conn, block_id)
        if row is None:
            raise BlockNotFound
        rules.check_version(current=row["version"], expected=expected_version)
        updated, verdict = await _move(
            conn, row, move, performer=performer, extra=extra, hooks=hooks,
        )

    await _invalidate_if_composed(redis, row["status"], move.to, row["scope_id"])
    return Approval(block=_out(updated), four_eyes=verdict)


async def _check_activation(conn, row, hooks: ActivationHooks) -> None:
    """What must hold before a row becomes `active`, on the locked row.

    The real gate. Checked at activation rather than only at compose time
    because this is where a human is standing and the fix is cheap — a scope
    that only discovers the conflict when a workflow tries to compose learns
    about it at runtime, far from the cause (CHY-62). Also run before a
    data-flow block in a direct scope is handed to review, so a rule that could
    never be approved is refused to its writer, not to the second person.
    """
    await _check_ratchet(conn, row)
    if hooks.before_activation is not None:
        await hooks.before_activation(conn, row)


async def _judge_four_eyes(conn, row, performer: ResolvedActor) -> Verdict:
    """Four eyes on a data-flow block, from the facts of this transaction (ADR-045).

    Who wrote the version comes from the audit log, the only place that has it
    — the table keeps the line's author, not the editors of each round. A
    self-approval by a single operator is allowed, logged as a warning and
    marked in the approval's audit entry: nobody second checked whether the
    data may leave the installation.
    """
    entries = await entries_for(conn, target_ref("block", row["block_id"]),
                                four_eyes.ACTIONS_READ_FOR_FOUR_EYES)
    verdict = four_eyes.judge_approval(
        approver_id=performer.id,
        approver_is_service_account=performer.is_service_account,
        writer_ids=four_eyes.writers_since_last_approval(
            entries, line_author_id=row["author_id"]),
        people_who_may_approve=await count_people_who_may_approve(conn),
    )
    if verdict is Verdict.SELF_APPROVED:
        logger.warning(
            "Block %s version %s was approved by the person who wrote it. It decides "
            "where data may go, and this installation has a single person who may "
            "approve, so no second person checked whether the data may leave it "
            "(ADR-045). Add a second reviewer to end this.",
            row["block_id"], row["version"],
        )
    return verdict


async def _move(conn, row, move: rules.Transition, *, performer: ResolvedActor, extra: dict,
                hooks: ActivationHooks = NO_HOOKS):
    """One step of the lifecycle on a row the caller holds, audited.

    The single place a block changes status (ADR-042): the explicit transition
    above and the direct activation below both come through here, so there is
    no second path on which the ratchet, four eyes or the audit entry could be
    missing. Returns the updated row and, for an approved data-flow block, how
    four eyes was satisfied.
    """
    rules.check_transition(current_status=row["status"], transition=move)
    verdict = None
    if move.to == BlockStatus.ACTIVE:
        await _check_activation(conn, row, hooks)
        if four_eyes.is_data_flow(row, hooks.routes_below_critical):
            verdict = await _judge_four_eyes(conn, row, performer)
            extra = {**extra, "four_eyes": verdict.value}
        # Author and approver both recorded, from the row being approved.
        author = row["author_id"]
        extra = {**extra, "author_id": str(author) if author else None,
                 "approved_by": str(performer.id)}

    updated = await store.set_status(conn, row["id"], move.to)
    await write_audit(
        conn, action=move.action, actor_id=performer.id,
        target=target_ref("block", row["block_id"]),
        payload={"from_status": row["status"], "to_status": move.to,
                 "version": row["version"], **extra},
    )
    return updated, verdict


async def _publish_directly(conn, row, performer: ResolvedActor, hooks: ActivationHooks):
    """A write in a direct scope: active at once — unless it is a data-flow block.

    ADR-042 point 2 gives direct scopes their freedom for prompt material. A
    block that decides where data may go is not prompt material (ADR-045): it
    is checked as for activation, then submitted, and waits for a second person
    like anything written in a strict scope. Submitted rather than left a
    draft, because the writer asked to publish — a draft in a direct scope is
    something nobody expects to have to submit, and it would sit in no queue.
    """
    direct = {"mode": ReviewMode.DIRECT.value}
    if four_eyes.is_data_flow(row, hooks.routes_below_critical):
        await _check_activation(conn, row, hooks)
        submitted, _ = await _move(conn, row, rules.TRANSITIONS["submit"], performer=performer,
                                   extra={**direct, "four_eyes": "required"})
        return submitted
    return await _activate_directly(conn, row, performer, hooks)


async def _activate_directly(conn, row, performer: ResolvedActor, hooks: ActivationHooks):
    """Submit and approve in the caller's transaction — a direct scope's write.

    Two steps rather than one, because migration 013 refuses draft → active
    and ADR-042 leaves it that way. The audit shows both, each with
    `mode: direct`, and the approval names the writer as approver next to the
    line's author: the same record a strict approval leaves, so a direct
    activation is told apart by its mode, not by a missing entry.

    Never reached by a data-flow block (`_publish_directly` hands those to
    review). The hooks are passed on whole anyway, so if one ever did, four
    eyes would refuse it here rather than let it through.
    """
    direct = {"mode": ReviewMode.DIRECT.value}
    submitted, _ = await _move(conn, row, rules.TRANSITIONS["submit"],
                               performer=performer, extra=direct)
    activated, _ = await _move(conn, submitted, rules.TRANSITIONS["approve"],
                               performer=performer, extra=direct, hooks=hooks)
    return activated


async def promote_block(pool, caller: Caller, block_id: UUID, body: BlockPromote) -> BlockOut:
    """Copy a block's live version into a strict scope above it, as a draft.

    ADR-042 point 3, the way ADR-011 meant it: a domain expert tries a prompt
    out where writing is direct, and once it has proved itself it is offered to
    the team or the organisation — where it waits for a reviewer like anything
    else written there. Promote decides nothing; it only starts that review.

    A new line, not a version of the old one. The original stays where it is,
    unchanged and still active: the project that proved the prompt keeps using
    it whether or not the team accepts the copy. The copy carries the
    governance claim along, so the reviewer sees it and the ratchet judges it
    at approval, against the chain of the scope it would govern.

    The link back lives in the audit entry (`block.promoted`), the copy's first
    one — the table has no column for where a line came from, and one entry
    that names line and version is enough to follow it.
    """
    performer = await resolve_actor(pool, caller)
    require(performer, "contributor")

    async with pool.acquire() as conn, conn.transaction():
        current = await store.current_block(conn, block_id)
        if current is None:
            raise BlockNotFound
        live = await store.newest_active_version(conn, block_id)
        if live is None:
            raise NothingToPromote(current["status"])
        source = store.row_to_dict(live)

        chain = await scopes.ancestor_chain(conn, source["scope_id"])
        rules.check_promotion(chain, source_scope_id=source["scope_id"],
                              target_scope_id=body.target_scope_id)

        row = await store.insert_block(conn, BlockCreate(
            scope_id=body.target_scope_id, block_type=source["block_type"],
            content=source["content"], metadata=source["metadata"],
            tags=list(source["tags"]), governance_key=source["governance_key"],
            privacy_impact=source["privacy_impact"], locked=source["locked"],
        ), performer.id)
        await write_audit(
            conn, action=ACTION_BLOCK_PROMOTED, actor_id=performer.id,
            target=target_ref("block", row["block_id"]),
            payload={"from_block_id": str(block_id), "from_version": source["version"],
                     "from_scope_id": str(source["scope_id"]),
                     "to_scope_id": str(body.target_scope_id)},
        )

    # A draft composes nothing, so no generation is bumped.
    return _out(row)


async def retire_block(pool, redis, caller: Caller, block_id: UUID) -> None:
    """Take a published block out of service — the whole version line."""
    performer = await resolve_actor(pool, caller)
    require(performer, "reviewer")

    async with pool.acquire() as conn, conn.transaction():
        versions = await store.lock_all_versions(conn, block_id)
        if not versions:
            raise BlockNotFound
        plan, retired = rules.plan_deletion(versions)
        if plan is DeletePlan.ALREADY_RETIRED:
            return

        await store.deprecate_active(conn, block_id)
        await write_audit(
            conn, action=ACTION_BLOCK_DEPRECATED, actor_id=performer.id,
            target=target_ref("block", block_id),
            # Every version that was still composable, so the entry says what
            # was actually taken out of service.
            payload={"to_status": BlockStatus.DEPRECATED, "retired_versions": retired},
        )

    await _invalidate_if_composed(
        redis, BlockStatus.ACTIVE, BlockStatus.DEPRECATED, versions[0]["scope_id"]
    )


async def _invalidate_if_composed(redis, from_status: str, to_status: str,
                                  scope_id: UUID) -> None:
    """Bump the scope generation when the change alters what is composed.

    Outside the transaction and tolerant of failure, both deliberately: the
    status change is committed and correct even if Redis is unreachable, and
    failing the request afterwards would tell the caller their approval did not
    happen when it did. A missed bump costs a stale cache for CACHE_TTL_SECONDS
    (ADR-010).
    """
    if not rules.affects_composition(from_status, to_status):
        return
    with contextlib.suppress(Exception):
        await bump_scope_generation(redis, scope_id)


async def routing_rules_for(pool, scope_ids: list[UUID]) -> list:
    """Approved routing rules visible from a scope chain.

    Published here rather than letting callers import store.py, because store
    is internal: the guard rule `context_encapsulation` says only service,
    schema and errors leave a context, and it caught two endpoints reaching
    past that line before.

    Returns rows and not models. What a routing rule *means* is the router's
    business (`router/rules.py`); this context owns the table and hands over
    what is in it — the same division CHY-121 drew when the scope chain moved
    to the kernel.
    """
    return await store.active_routing_rules(pool, scope_ids)


async def redaction_rules_for(pool, scope_ids: list[UUID]) -> list:
    """Approved redaction rules visible from a scope chain (CHY-63).

    Rows, like `routing_rules_for`: what a redaction rule means is read in
    `kernel.redaction`; this context owns the table and hands over its rows.
    """
    return await store.active_redaction_rules(pool, scope_ids)
