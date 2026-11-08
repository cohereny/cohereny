"""
contexts/pilots/service.py

Use cases of the pilots context (ADR-030, CHY-104).

The published interface, together with schema and errors. Each function here
owns a transaction, calls the aggregate for the decision and the store for the
execution, and returns something that has nothing to do with HTTP.

The test that this split is real is not the file count. It is that
`submit_oversight` — the EU AI Act Art. 14 decision — can now be triggered
without a request. Before CHY-104 the only way to approve a banking run was to
speak HTTP to it, which made the control untestable and unautomatable at once.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import UUID, uuid4

import asyncpg

from contexts.pilots import events, run as rules, store
from contexts.pilots import workflow_config as cfg_rules
from contexts.pilots.errors import (
    NoCoverageData,
    UnknownPilotConfig,
    QuestionNotFound,
    OversightGateGone,
    RunHasTransparencyRecords,
    RunNotFound,
)
from contexts.pilots.execution import RunSpec, executor, run_in_thread
# The registry is this context's process state, not something a caller
# supplies: the transport layer has no business knowing it exists, and the
# guard rule context_encapsulation says so out loud — only service, schema and
# errors are published. It stays an argument so a test can pass its own
# instead of clearing a global between cases.
from contexts.pilots.registry import RunRegistry, registry as _process_registry

from contexts.pilots.run import Disposition
from contexts.pilots.schema import (
    CoverageOut,
    PilotRunOut,
    PilotRunRequest,
    QuestionOut,
    RunEnvironment,
    RunStatus,
    TERMINAL_STATES,
    WorkflowConfigListOut,
    WorkflowConfigOut,
    WorkflowConfigPut,
)
from kernel import acting
from kernel.audit import (
    ACTION_PILOT_OVERSIGHT_DECIDED,
    ACTION_PILOT_RUN_CANCELLED,
    ACTION_PILOT_RUN_DELETED,
    ACTION_PILOT_RUN_STARTED,
    ACTION_WORKFLOW_CONFIG_CHANGED,
    write_audit,
)
from kernel.access import require
from kernel.identity import Caller, resolve_actor
from kernel.retention import Periods, RetentionClass


# ── reading ───────────────────────────────────────────────────────────────────

def _run_out(row) -> PilotRunOut:
    """A run row as a model, with its JSONB columns parsed.

    asyncpg hands JSONB back as str on some versions and as dict on others.
    Three call sites had `PilotRunOut(**dict(row))` and would each have needed
    the same two-line workaround once run_config and effective_config arrived
    (CHY-42) — the repository context learned this and has store.row_to_dict.
    """
    d = dict(row)
    for column in ("run_config", "effective_config", "result_json"):
        if isinstance(d.get(column), str):
            d[column] = json.loads(d[column])
    return PilotRunOut(**d)


async def list_runs(pool, caller: Caller) -> list[PilotRunOut]:
    """The recent runs the caller may see: their own, or all of them for admin (CHY-92)."""
    reader = await resolve_actor(pool, caller)
    async with acting.acting_as(pool, reader.id) as conn:
        return [_run_out(r) for r in await store.recent_runs(conn)]


async def get_run(pool, caller: Caller, run_id: UUID) -> PilotRunOut:
    """One run, if the caller may see it; otherwise as if it did not exist (CHY-92)."""
    reader = await resolve_actor(pool, caller)
    async with acting.acting_as(pool, reader.id) as conn:
        row = await store.full_run(conn, run_id)
    if row is None:
        raise RunNotFound
    return _run_out(row)


async def get_coverage(pool, caller: Caller, run_id: UUID) -> CoverageOut:
    """Coverage of a completed study run against the question bank.

    Two distinct absences, kept distinct: no such run, and a run with nothing
    to report (a banking run, or no questions ingested yet). Both ended as the
    same 404 before; they still do, but the reason now survives to the caller.
    A run the caller may not see counts as no such run (CHY-92).
    """
    reader = await resolve_actor(pool, caller)
    async with acting.acting_as(pool, reader.id) as conn:
        visible = await store.run_exists(conn, run_id)
    if not visible:
        raise RunNotFound
    # run_coverage holds no content and has no row security; it is read only
    # once the run itself was found in the caller's context.
    rows = await store.coverage_rows(pool, run_id)
    if not rows:
        raise NoCoverageData(run_id)
    return rules.coverage_from_rows(run_id, rows)


async def list_questions(pool, *, topic: str | None = None,
                         limit: int = 50, offset: int = 0) -> list[QuestionOut]:
    """A page of the question bank."""
    return [QuestionOut(**dict(r))
            for r in await store.questions(pool, topic=topic, limit=limit, offset=offset)]


async def get_question(pool, question_id: UUID) -> QuestionOut:
    row = await store.question(pool, question_id)
    if row is None:
        raise QuestionNotFound
    return QuestionOut(**dict(row))


def list_input_files(input_dir: str | Path, pilot: str | None = None) -> list[str]:
    """PDF filenames available to start a run with, optionally filtered by pilot."""
    files = sorted(p.name for p in Path(input_dir).glob("*.pdf") if p.is_file())
    if pilot:
        files = [f for f in files if f.startswith(pilot)]
    return files


# ── starting ──────────────────────────────────────────────────────────────────

# ── workflow configuration (CHY-42) ───────────────────────────────────────────

_CACHE_PREFIX = "cc:workflow"


def _config_key(pilot_name: str) -> str:
    return f"{_CACHE_PREFIX}:{pilot_name}:config"


def _config_out(row) -> WorkflowConfigOut:
    d = dict(row)
    for section in ("model_map", "params"):
        if isinstance(d.get(section), str):
            d[section] = json.loads(d[section])
    return WorkflowConfigOut(**d)


async def list_workflow_configs(pool, caller: Caller) -> WorkflowConfigListOut:
    """Every pilot's stored configuration.

    `contributor`, not merely authenticated. The quality gate counted these two
    reads as endpoints that authenticate and then discard who asked, and it was
    right to: the model map is a governance fact — in the banking pilot it is
    the local/cloud boundary — and a consumer who only starts runs has no need
    to read which model sees which data.
    """
    require(await resolve_actor(pool, caller), "contributor")
    rows = await store.all_workflow_configs(pool)
    items = [_config_out(r) for r in rows]
    return WorkflowConfigListOut(items=items, total=len(items))


async def get_workflow_config(pool, redis, pilot_name: str,
                              caller: Caller | None = None) -> WorkflowConfigOut:
    """Read the configuration, preferring the cache.

    `caller` is optional because this is also the internal read on the way to
    starting a run, where the caller has already been resolved and requiring a
    second lookup would mean two actor queries per run. Passed from the
    endpoint, omitted internally — and the endpoint is the only place where
    "who is asking" is still an open question.

    No TTL on the cached entry, unlike the compiled contexts of ADR-010. There
    the key carries a generation counter, so a stale entry is unreachable
    rather than wrong and the TTL only reclaims memory. Here the key is stable,
    so correctness rests entirely on the DEL in put_workflow_config — and a TTL
    would turn a missed invalidation from a permanent bug into an intermittent
    one, which is strictly harder to find.

    A cache read or write that fails costs a database round trip and nothing
    else. Refusing to answer because Redis is unavailable would turn an
    optimisation into a dependency — the same reasoning as compose.py.
    """
    rules.check_pilot_known(pilot_name)
    if caller is not None:
        require(await resolve_actor(pool, caller), "contributor")

    with contextlib.suppress(Exception):
        cached = await redis.get(_config_key(pilot_name))
        if cached:
            return WorkflowConfigOut(**json.loads(cached))

    row = await store.workflow_config(pool, pilot_name)
    if row is None:
        # A known pilot with no row means the seed of migration 017 never ran.
        # Not "unknown pilot" — it exists — and not something this request can
        # repair, so it must not read as a client error the caller could fix.
        raise UnknownPilotConfig(pilot_name, ())
    out = _config_out(row)

    with contextlib.suppress(Exception):
        await redis.set(_config_key(pilot_name), out.model_dump_json())
    return out


async def put_workflow_config(
    pool, redis, caller: Caller, pilot_name: str, body: WorkflowConfigPut
) -> WorkflowConfigOut:
    """Replace the named sections, audited, and drop the cached copy.

    `architect`, not `contributor`: the model map decides which model sees
    which data, and in the banking pilot that map *is* the local/cloud
    boundary. Someone who may write context blocks should not be able to move a
    prompt onto a cloud model by editing a configuration row.

    The DEL comes after the commit. Before it, a concurrent read could refill
    the cache from the old row and the new value would stay invisible until the
    next write — the classic invalidation race, and the reason this order is
    not incidental.

    A failed DEL is deliberately *not* suppressed, unlike the reads above. A
    cache still serving the previous configuration is not a slow answer, it is
    a wrong one: runs would keep composing from a value nobody can see any more.
    """
    performer = await resolve_actor(pool, caller)
    require(performer, "architect")
    rules.check_pilot_known(pilot_name)

    async with pool.acquire() as conn, conn.transaction():
        row = await store.upsert_workflow_config(
            conn, pilot_name, model_map=body.model_map, params=body.params
        )
        await write_audit(
            conn,
            action=ACTION_WORKFLOW_CONFIG_CHANGED,
            actor_id=performer.id,
            target=f"workflow_config:{pilot_name}",
            payload={
                "version": row["version"],
                "sections": sorted(
                    name for name, value in
                    (("model_map", body.model_map), ("params", body.params))
                    if value is not None
                ),
            },
        )

    await redis.delete(_config_key(pilot_name))
    return _config_out(row)


async def _compose_run_config(pool, redis, pilot_name: str, run_config: dict) -> dict:
    """What this run will use, ready to be recorded.

    The override is checked before anything is read: a request naming a section
    that cannot take effect is refused outright, not honoured in part.
    """
    cfg_rules.check_override(run_config)
    stored = await get_workflow_config(pool, redis, pilot_name)
    return cfg_rules.compose_effective(
        pilot_name=pilot_name,
        version=stored.version,
        stored={"model_map": stored.model_map, "params": stored.params},
        run_config=run_config,
    )


async def start_run(
    pool,
    redis,
    caller: Caller,
    body: PilotRunRequest,
    env: RunEnvironment,
    *,
    registry: RunRegistry = _process_registry,
) -> PilotRunOut:
    """Create the run, audit it, and hand it to a background thread.

    Resolved, not merely authenticated (CHY-88). A run without an owner cannot
    answer "who started this", and the Art. 14 record needs the same internal
    id so a DSGVO erasure (CHY-34) reaches both.

    `model_used` is no longer a parameter (CHY-42). The transport used to pass
    `f"{settings.model_small}, {settings.model_medium}"` — the *deployment's*
    models, which is not the same claim as the models this run used, and would
    have gone on disagreeing silently the moment a run overrode one. It is now
    derived from the effective configuration, which is also what is recorded.
    """
    rules.check_pilot_known(body.pilot_name)
    pdf_path = rules.contain_input_file(env.input_dir, body.input_file)
    thread_id = body.thread_id or f"{body.pilot_name}-{uuid4().hex[:8]}"

    starter = await resolve_actor(pool, caller)
    # The same gate as storing the map (put_workflow_config): the model map is
    # the local/cloud boundary, and overriding it for one run moves a prompt as
    # far as rewriting the row does. Without this, any author could send the
    # banking pilot's documents to a hosted model through `run_config` that
    # they were not allowed to configure (CHY-196). Present-but-empty counts:
    # the check is on the attempt, not on whether it changes anything.
    if "model_map" in body.run_config:
        require(starter, "architect")
    effective = await _compose_run_config(pool, redis, body.pilot_name, body.run_config)
    model_map = effective["model_map"]
    # Sorted and deduplicated: two tiers often name the same model, and the
    # transparency record should read as a set of models, not as a tier list.
    model_used = ", ".join(sorted(set(model_map.values())))

    # As the starter: row-level security admits the row because it is theirs
    # (CHY-92). The background thread then works in the system context.
    async with acting.acting_as(pool, starter.id) as conn, conn.transaction():
        row = await store.insert_run(
            conn, thread_id=thread_id, pilot_name=body.pilot_name,
            input_file=body.input_file, actor_id=starter.id,
            run_config=body.run_config, effective_config=effective,
        )
        await write_audit(
            conn,
            action=ACTION_PILOT_RUN_STARTED,
            actor_id=starter.id,
            target=f"pilot_run:{row['id']}",
            payload={"pilot_name": body.pilot_name, "input_file": body.input_file,
                     "config_version": effective["config_version"],
                     "overrides": sorted(body.run_config)},
        )
    started = _run_out(row)

    cancel_event = registry.open(started.id)
    gate = (
        registry.open_oversight_gate(started.id)
        if body.pilot_name == "banking"
        else None
    )

    spec = RunSpec(
        run_id=started.id, pdf_path=pdf_path, thread_id=thread_id,
        pilot_name=body.pilot_name, db_dsn=env.db_dsn,
        model_used=model_used, model_map=model_map, node_metrics=env.node_metrics,
    )
    main_loop = asyncio.get_running_loop()
    main_loop.run_in_executor(
        executor, run_in_thread, spec, main_loop, cancel_event, registry, gate,
    )
    return started


# ── cancelling and deleting ───────────────────────────────────────────────────

async def cancel_or_delete(
    pool, caller: Caller, run_id: UUID, *, registry: RunRegistry = _process_registry
) -> str:
    """Cancel a running run or delete a terminal one — audited, and only your own.

    Returns the outcome as a word: cancelling, cancelled, or deleted.

    What this used to be (CHY-90): the endpoint took the actor and never read
    it, so any authenticated caller could delete any run. Nothing was audited,
    and transparency_records referenced pilot_runs with ON DELETE CASCADE, so
    the delete silently took the Article 50 records with it. Demonstrated on
    cohereny_dev before the fix: one DELETE, nine records became eight.

    No role gate, and the reason is the same as at submit_oversight: ownership
    is enforceable today and is enforced; the role gate follows once the chy-*
    groups carry real members.
    """
    deleter = await resolve_actor(pool, caller)

    # In the deleter's own context throughout: a run they may not see is not
    # found, before ownership is even asked (CHY-92).
    async with acting.acting_as(pool, deleter.id) as conn:
        row = await store.run_status_and_owner(conn, run_id)
    if row is None:
        raise RunNotFound

    status = row["status"]
    owner = row["actor_id"]
    rules.check_ownership(owner_id=owner, actor_id=deleter.id)

    disposition = rules.plan_disposition(
        status=status, in_flight=registry.in_flight(run_id)
    )

    if disposition is Disposition.DELETE:
        try:
            async with acting.acting_as(pool, deleter.id) as conn, conn.transaction():
                # Audit before the delete, in the same transaction: afterwards
                # there is no row left to describe, and the two must stand or
                # fall together.
                await write_audit(
                    conn,
                    action=ACTION_PILOT_RUN_DELETED,
                    actor_id=deleter.id,
                    target=f"pilot_run:{run_id}",
                    payload={"status": status, "owner_id": str(owner) if owner else None},
                )
                await store.delete_run(conn, run_id)
        except asyncpg.ForeignKeyViolationError:
            raise RunHasTransparencyRecords from None
        registry.forget(run_id)
        return "deleted"

    if disposition is Disposition.RELEASE_GATE:
        # Released with "cancelled", the gate's third value, never with a
        # decision (CHY-153). Until 07.11.2026 this sent "rejected": the thread
        # then booked the run as an Art. 14 rejection, the trigger from 014
        # refused a decision without a decider, and the run stayed `running`
        # with no result. Writing a cancellation into oversight_decision would
        # manufacture a record for a decision nobody made — the opposite of
        # what CHY-88 built. The run ends through the cancel path, as `failed`
        # with a message saying it was cancelled.
        released = registry.decide_oversight(run_id, "cancelled")
        async with acting.acting_as(pool, deleter.id) as conn, conn.transaction():
            await store.clear_oversight_gate(conn, run_id)
            await write_audit(
                conn,
                action=ACTION_PILOT_RUN_CANCELLED,
                actor_id=deleter.id,
                target=f"pilot_run:{run_id}",
                payload={"from_status": status, "released_oversight_gate": released},
            )
        return "cancelling"

    if disposition is Disposition.CANCEL_IN_FLIGHT:
        registry.cancel_signal(run_id).set()
        async with acting.acting_as(pool, deleter.id) as conn:
            await write_audit(
                conn,
                action=ACTION_PILOT_RUN_CANCELLED,
                actor_id=deleter.id,
                target=f"pilot_run:{run_id}",
                payload={"from_status": status, "in_flight": True},
            )
        return "cancelling"

    # Disposition.MARK_FAILED — the database says running, this process has
    # never heard of the run. It restarted; nothing will ever finish it.
    async with acting.acting_as(pool, deleter.id) as conn, conn.transaction():
        await store.mark_failed(conn, run_id, "Cancelled by user (run not in flight)")
        await write_audit(
            conn,
            action=ACTION_PILOT_RUN_CANCELLED,
            actor_id=deleter.id,
            target=f"pilot_run:{run_id}",
            payload={"from_status": status, "in_flight": False},
        )
    registry.push(run_id, {"type": events.ERROR, "message": "Cancelled by user"})
    registry.push(run_id, None)
    return "cancelled"


# ── human oversight (EU AI Act Art. 14) ───────────────────────────────────────

async def submit_oversight(
    pool, caller: Caller, run_id: UUID, decision: str, *, registry: RunRegistry = _process_registry
) -> str:
    """Record a human decision on a gated run and let the thread continue.

    `reviewer`, like approving a block. Until CHY-196 there was no role gate
    here, on the reasoning that the chy-* groups had no members yet and a gate
    nobody can pass is worse than one that only records who passed it. That
    stopped holding once the README told operators to create the groups: any
    author could release the Art. 14 decision, which makes the human in the
    loop anyone at all. Four eyes is still not enforced — the audit entry names
    starter and decider so a self-approval stays recognisable.
    """
    rules.check_decision(decision)
    decider = await resolve_actor(pool, caller)
    require(decider, "reviewer")

    # Only someone who may see the run decides it — for a pilot run, which has
    # no scope, that is its starter or an admin (CHY-92).
    async with acting.acting_as(pool, decider.id) as conn:
        row = await store.run_status_and_owner(conn, run_id)
    if row is None:
        raise RunNotFound
    rules.check_awaiting_oversight(row["status"])

    # The gate first: if it is gone the run cannot continue no matter what is
    # written, and recording a decision that will never take effect is worse
    # than refusing. The registry write happens after the audited UPDATE
    # commits, so a crash in between leaves a recorded decision and a still-
    # waiting thread — recoverable — rather than a released thread and no record.
    if not registry.has_oversight_gate(run_id):
        raise OversightGateGone

    async with acting.acting_as(pool, decider.id) as conn, conn.transaction():
        await store.record_oversight(
            conn, run_id, decision=decision, decided_by=decider.id
        )
        await write_audit(
            conn,
            action=ACTION_PILOT_OVERSIGHT_DECIDED,
            actor_id=decider.id,
            target=f"pilot_run:{run_id}",
            payload={
                "decision": decision,
                # Both sides recorded so a self-approval is visible afterwards.
                # Four eyes is not enforced (one-person operation, as with the
                # blocks in CHY-32) — but it must not be invisible.
                "started_by": str(row["actor_id"]) if row["actor_id"] else None,
                "decided_by": str(decider.id),
            },
        )

    registry.decide_oversight(run_id, decision)
    registry.push(run_id, {"type": events.OVERSIGHT_SUBMITTED, "decision": decision})
    return decision


# ── streaming ─────────────────────────────────────────────────────────────────

async def stream_events(
    pool, caller: Caller, run_id: UUID, *,
    registry: RunRegistry = _process_registry,
    idle_timeout: float = 30.0,
) -> AsyncIterator[dict | None]:
    """Yield this run's events; None means "nothing happened, still connected".

    The None ticks are what lets the transport send an SSE heartbeat without
    knowing anything about the registry: keeping a socket open is an HTTP
    concern, noticing that nothing is happening is not.

    Two sources, and which one applies is not a detail: a run this process
    knows is streamed live from memory, a run it does not is replayed from
    events_json. The second case is a restart, or simply a client arriving after
    the run finished — the common case, not the exception.
    """
    reader = await resolve_actor(pool, caller)
    async with acting.acting_as(pool, reader.id) as conn:
        row = await store.run_stream_state(conn, run_id)
    if row is None:
        raise RunNotFound

    if not registry.is_live(run_id):
        for event in _replay(row):
            yield event
        yield {"type": events.EOF}
        return

    buffer, notify = registry.buffers(run_id)
    cursor = 0
    while True:
        while cursor < len(buffer):
            yield buffer[cursor]
            cursor += 1

        if registry.is_done(run_id):
            yield {"type": events.EOF}
            registry.forget(run_id)
            return

        notify.clear()
        # TOCTOU guard: re-check after clear() in case something arrived between
        # draining the buffer above and clearing the flag.
        if cursor < len(buffer) or registry.is_done(run_id):
            continue

        try:
            await asyncio.wait_for(notify.wait(), timeout=idle_timeout)
        except asyncio.TimeoutError:
            yield None
        # On client disconnect CancelledError propagates out of here and the
        # generator exits. The buffers stay intact for the next connection.


def _replay(row) -> list[dict]:
    """Reconstruct a finished run's event stream from the database row."""
    persisted = row["events_json"] or []
    if isinstance(persisted, str):  # asyncpg returns jsonb as text on some paths
        persisted = json.loads(persisted)
    status = row["status"]

    if persisted:
        # A gate that has already been decided must not come back as actionable.
        return [
            {**e, "type": events.OVERSIGHT_HISTORICAL}
            if e.get("type") == events.OVERSIGHT_REQUIRED
            and status != RunStatus.AWAITING_OVERSIGHT
            else e
            for e in persisted
        ]

    # Runs completed before migration 004 added events_json have no log.
    result = row["result_json"]
    if isinstance(result, str):
        result = json.loads(result)
    if status == RunStatus.COMPLETE and result:
        return [{
            "type": events.DONE,
            "lernziel_erreicht": result.get("lernziel_erreicht"),
            "iteration_count": result.get("iteration_count"),
            "facts_count": len(result.get("fakten", [])),
        }]
    if status == RunStatus.FAILED:
        return [{"type": events.ERROR, "message": "Run failed — reload for details"}]
    return [{"type": events.STATUS, "status": status}]


# ── retention (CHY-17, ADR-046) ───────────────────────────────────────────────

async def purge_runs(conn, periods: Periods) -> dict[str, int]:
    """Apply the retention periods to finished pilot runs, in the caller's transaction.

    Pilot runs belong to no scope, so the installation-wide periods apply. The
    LangGraph checkpoints of a run stay: they are tables of the library, not of
    this context, and they go with the pilots (CHY-179).
    """
    finished = sorted(state.value for state in TERMINAL_STATES)
    counts = {"pilot_runs_cleared": 0, "pilot_runs_deleted": 0}
    content_days = periods.installation_days(RetentionClass.RUN_CONTENT)
    if content_days is not None:
        counts["pilot_runs_cleared"] = await store.clear_run_content(conn, content_days, finished)
    trace_days = periods.installation_days(RetentionClass.RUN_TRACE)
    if trace_days is not None:
        counts["pilot_runs_deleted"] = await store.delete_runs(conn, trace_days, finished)
    return counts
