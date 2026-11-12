"""
kernel/retention.py

What data Cohereny keeps, in which retention class, and for how long (CHY-17, ADR-046).

Two halves that change at different speeds:

  the inventory    which table belongs to which retention class, and which of
                   its columns are content. Code: it changes only with the
                   schema, and a new table comes with a migration anyway.
                   tests/test_retention.py fails when a table the migrations
                   create is missing here — whoever adds one has to say how
                   long it stays, or it would silently escape every period.
  the periods      how long a class is kept. Configuration: rows in
                   retention_policies, changed through the admin API and
                   audited. They differ by scenario — a credit process keeps
                   longer than a study aid — so they are set per scope, and the
                   nearest declaration on the scope chain applies.

**The content of a run expires before its trace.** `run_content` is what went
in and came out — inputs, outputs, free text — and it is cleared. `run_trace`
is the row itself: who started what, which model answered, who decided at the
gate. It is deleted later; Art. 26(6) of the AI Act asks deployers of
high-risk systems to keep the logs for at least six months.

**Only the two run classes take a period.** The audit log, context blocks and
benchmark measurements refuse UPDATE and DELETE by trigger — since CHY-85 also
against the application, which neither owns them nor holds DELETE on them.
Whether they need a period at all, and how one would delete past the trigger,
is CHY-242. Accounts leave through anonymization (ADR-016). A period nothing
could act on would be a promise the system does not keep, so those classes say
why instead.

"Retention class", not "data class": that name belongs to the privacy level of
a scope (ADR-041). One thing, one name.

**The inventory also says what the application may do to each table** (CHY-85,
ADR-009). Migration 033 grants exactly that to the role `cohereny_app`, and
nothing more: the application owns no table, because an owner may switch off
the triggers that keep the audit log and the blocks append-only.
tests/test_app_privileges.py holds migration 033 to this list, and the
integration suite holds the database to it — a table nobody decided about fails
both.

Owned by the kernel like the audit log and the scopes: every context has rows
in it, and the kernel names their tables as strings without importing any of
them (ADR-030). Purging them is the business of the context that owns each
table.
"""
from __future__ import annotations

import enum
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from typing import Literal
from uuid import UUID

import asyncpg

from kernel import scopes
from kernel.access import require
from kernel.audit import (
    ACTION_RETENTION_POLICY_CHANGED,
    ACTION_RETENTION_PURGED,
    read_entries,
    target_ref,
    write_audit,
)
from kernel.errors import HighRiskPeriodTooShort, RetentionOverrideNotFound, ScopeNotFound
from kernel.identity import Caller, resolve_actor
from kernel.schema import (
    ExternalStoreOut,
    RetentionClassOut,
    RetentionOverview,
    RetentionPolicy,
    RetentionPolicySet,
    RetentionTable,
)


class RetentionClass(enum.StrEnum):
    """How long something stays, by what it is."""

    RUN_CONTENT = "run_content"
    RUN_TRACE = "run_trace"
    AUDIT = "audit"
    ACCOUNTS = "accounts"
    CONTEXT = "context"
    STRUCTURE = "structure"
    MEASUREMENTS = "measurements"


@dataclass(frozen=True)
class ClassRule:
    """What a retention class is and what a purge may do to it."""

    description: str
    action: str
    #: Why there is no period, for the classes that take none.
    reason: str | None = None

    @property
    def configurable(self) -> bool:
        return self.reason is None


RULES: dict[RetentionClass, ClassRule] = {
    RetentionClass.RUN_CONTENT: ClassRule(
        "What went into a run and came out: inputs, outputs, free-text reasons "
        "and comments. Cleared when the period ends; the run's trace stays.",
        "clear",
    ),
    RetentionClass.RUN_TRACE: ClassRule(
        "The run itself: who started it, which model answered, who decided at "
        "the gate and when. Deleted when the period ends.",
        "delete",
    ),
    RetentionClass.AUDIT: ClassRule(
        "The audit log.",
        "none",
        "Refuses UPDATE and DELETE by trigger, and the application holds neither "
        "right (CHY-85). Whether it needs a period, and a deletion path for one, "
        "is CHY-242.",
    ),
    RetentionClass.ACCOUNTS: ClassRule(
        "Actors and their scope memberships.",
        "none",
        "An account leaves through anonymization (POST /v1/admin/actors/{ref}/"
        "anonymize, ADR-016), not after a period.",
    ),
    RetentionClass.CONTEXT: ClassRule(
        "Curated material: context blocks, governance keys, the question bank, "
        "learning proposals.",
        "none",
        "Context blocks refuse UPDATE and DELETE by trigger: their history is the "
        "record of what applied when. The author's identity leaves through "
        "anonymization.",
    ),
    RetentionClass.STRUCTURE: ClassRule(
        "How the installation is set up: scopes, workflow definitions, "
        "configuration, these periods.",
        "none",
        "Configuration, not personal data; it is changed, not expired.",
    ),
    RetentionClass.MEASUREMENTS: ClassRule(
        "Benchmark runs, measurements and reports (ADR-043).",
        "none",
        "Measurements refuse UPDATE and DELETE by trigger; drift detection needs "
        "their history (ADR-044), and anchors are curated test cases, not user "
        "data.",
    ),
}


#: A table privilege the application can be granted. Nothing else is ever
#: granted to it: no TRUNCATE, REFERENCES or TRIGGER, and no ownership.
Privilege = Literal["SELECT", "INSERT", "UPDATE", "DELETE"]

#: The two privileges most tables share; the rest are spelled out per table.
READ: tuple[Privilege, ...] = ("SELECT",)
APPEND: tuple[Privilege, ...] = ("SELECT", "INSERT")


@dataclass(frozen=True)
class TableEntry:
    """One table: whose it is, what class its rows are, which columns are content,
    and what the application may do to it."""

    owner: str
    row_class: RetentionClass
    #: Columns cleared as `run_content` while the row stays as `run_trace`.
    content_columns: tuple[str, ...] = ()
    #: Granted to `cohereny_app` by migration 033 (CHY-85). Required: a table
    #: nobody decided about would otherwise get nothing and fail at run time,
    #: or everything and hold no line.
    access: tuple[Privilege, ...] = field(kw_only=True)


#: Every table the migrations leave behind, and the migration runner's own.
#:
#: `access` is what the code needs, found by reading every statement it sends —
#: an upsert (ON CONFLICT DO UPDATE) needs UPDATE as well. UPDATE and DELETE on
#: the run tables are mostly the purge (ADR-046); the rows of a run's children go
#: with it by ON DELETE CASCADE, which runs as the owner and needs no grant.
INVENTORY: dict[str, TableEntry] = {
    # orchestration — workflow runs (ADR-028)
    "workflow_runs": TableEntry("orchestration", RetentionClass.RUN_TRACE,
                                ("input_data", "result", "error_message"),
                                access=("SELECT", "INSERT", "UPDATE", "DELETE")),
    "node_executions": TableEntry("orchestration", RetentionClass.RUN_TRACE,
                                  ("input_data", "output_data", "error_message"),
                                  access=("SELECT", "INSERT", "UPDATE")),
    "loop_counters": TableEntry("orchestration", RetentionClass.RUN_TRACE,
                                access=("SELECT", "INSERT", "UPDATE")),
    # Drawn passes of sample gates and their verdict (CHY-223). DELETE: they go
    # with their run, leaf first, as the votes do.
    "review_samples": TableEntry("orchestration", RetentionClass.RUN_TRACE, ("reason",),
                                 access=("SELECT", "INSERT", "UPDATE", "DELETE")),
    # Direct calls drawn for review (CHY-269): prompt, answer and reason are
    # content, cleared after the scope's run_content period; the row, with its
    # verdict, goes after the run_trace period. Placeholders only — no mapping.
    "direct_call_samples": TableEntry("orchestration", RetentionClass.RUN_TRACE,
                                      ("prompt", "answer", "reason"),
                                      access=("SELECT", "INSERT", "UPDATE", "DELETE")),
    "review_votes": TableEntry("orchestration", RetentionClass.RUN_TRACE, ("reason",),
                               access=("SELECT", "INSERT", "UPDATE", "DELETE")),
    # What each placeholder of a run stands for, sealed (CHY-234). UPDATE: a step
    # extends it under a row lock, the purge clears it; the row goes with its run.
    "pii_mappings": TableEntry("orchestration", RetentionClass.RUN_TRACE,
                               ("nonce", "ciphertext"),
                               access=("SELECT", "INSERT", "UPDATE")),
    # A run's documents as text (CHY-174, ADR-049). The text is content; name,
    # size, pages and the counted findings are the trace. UPDATE: the purge.
    "workflow_run_documents": TableEntry("orchestration", RetentionClass.RUN_TRACE, ("text",),
                                         access=("SELECT", "INSERT", "UPDATE")),
    "workflow_definitions": TableEntry("orchestration", RetentionClass.STRUCTURE,
                                       access=("SELECT", "INSERT", "UPDATE")),
    "workflow_nodes": TableEntry("orchestration", RetentionClass.STRUCTURE, access=APPEND),
    # Replaced as a whole when a draft's edges change.
    "workflow_connections": TableEntry("orchestration", RetentionClass.STRUCTURE,
                                       access=("SELECT", "INSERT", "DELETE")),
    # pilots — the LangGraph pilots, replaced by the runner (ADR-028)
    # input_file is content: a file name can carry a person's name.
    "pilot_runs": TableEntry("pilots", RetentionClass.RUN_TRACE,
                             ("input_file", "result_json", "events_json", "error_message"),
                             access=("SELECT", "INSERT", "UPDATE", "DELETE")),
    # Written once, deleted only by the purge.
    "transparency_records": TableEntry("pilots", RetentionClass.RUN_TRACE,
                                       access=("SELECT", "INSERT", "DELETE")),
    "run_coverage": TableEntry("pilots", RetentionClass.RUN_TRACE,
                               access=("SELECT", "INSERT", "UPDATE")),
    "question_bank": TableEntry("pilots", RetentionClass.CONTEXT, access=APPEND),
    "workflow_configs": TableEntry("pilots", RetentionClass.STRUCTURE,
                                   access=("SELECT", "INSERT", "UPDATE")),
    # learning
    # Nothing writes feedback yet (CHY-40); the purge clears and deletes it.
    "feedback_events": TableEntry("learning", RetentionClass.RUN_TRACE, ("comment",),
                                  access=("SELECT", "UPDATE", "DELETE")),
    # Read-only until something writes them (CHY-40, CHY-68): the grant comes
    # with the code that needs it, in the same change.
    "learning_proposals": TableEntry("learning", RetentionClass.CONTEXT, access=READ),
    "benchmark_runs": TableEntry("learning", RetentionClass.MEASUREMENTS, access=READ),
    "benchmark_measurements": TableEntry("learning", RetentionClass.MEASUREMENTS,
                                         access=READ),
    "benchmark_reports": TableEntry("learning", RetentionClass.MEASUREMENTS, access=READ),
    # repository
    # UPDATE for status transitions only; the trigger refuses everything else,
    # and a new version goes through cc_supersede_block (migration 034).
    "context_blocks": TableEntry("repository", RetentionClass.CONTEXT,
                                 access=("SELECT", "INSERT", "UPDATE")),
    # Filled by migrations.
    "governance_keys": TableEntry("repository", RetentionClass.CONTEXT, access=READ),
    # kernel
    "audit_log": TableEntry("kernel", RetentionClass.AUDIT, access=APPEND),
    # UPDATE: the upsert on sign-in and anonymization (ADR-016).
    "actors": TableEntry("kernel", RetentionClass.ACCOUNTS,
                         access=("SELECT", "INSERT", "UPDATE")),
    "scope_memberships": TableEntry("kernel", RetentionClass.ACCOUNTS, access=APPEND),
    # The competence order of a scope (CHY-180): which areas vote at a gate, and
    # who belongs to them. UPDATE on the members revokes and grants again; a
    # revoked row stays, so a vote cast under it stays provable.
    "review_areas": TableEntry("kernel", RetentionClass.STRUCTURE, access=APPEND),
    "review_area_members": TableEntry("kernel", RetentionClass.ACCOUNTS,
                                      access=("SELECT", "INSERT", "UPDATE")),
    "scope_nodes": TableEntry("kernel", RetentionClass.STRUCTURE, access=APPEND),
    # Each classification of direct model calls is a row; the newest applies
    # (CHY-199). Append-only like the scopes it classifies.
    "direct_call_risk_classes": TableEntry("kernel", RetentionClass.STRUCTURE, access=APPEND),
    "retention_policies": TableEntry("kernel", RetentionClass.STRUCTURE,
                                     access=("SELECT", "INSERT", "UPDATE", "DELETE")),
    # created by migrations/runner.py, not by a migration; the application
    # never reads it
    "schema_migrations": TableEntry("migrations", RetentionClass.STRUCTURE, access=()),
}


@dataclass(frozen=True)
class ExternalStore:
    """Data written outside the tables above."""

    name: str
    retention_class: RetentionClass
    handled_by: str


#: Not created by any migration, and part of the inventory all the same.
EXTERNAL_STORES: tuple[ExternalStore, ...] = (
    ExternalStore(
        "LangGraph checkpoints (tables checkpoints, checkpoint_blobs, "
        "checkpoint_writes, checkpoint_migrations, same database)",
        RetentionClass.RUN_CONTENT,
        "Created by the LangGraph pilots and holding their full state; they go "
        "with the pilots (CHY-179) and are not purged by Cohereny until then.",
    ),
    ExternalStore(
        "Langfuse traces (optional)",
        RetentionClass.RUN_CONTENT,
        "The operator's Langfuse instance; its retention is set there.",
    ),
    ExternalStore(
        "Redis cache of composed contexts",
        RetentionClass.CONTEXT,
        "Expires by TTL (ADR-010).",
    ),
    ExternalStore(
        "Pilot input files (PILOT_INPUT_DIR)",
        RetentionClass.RUN_CONTENT,
        "The operator's file system; Cohereny only reads them.",
    ),
)

#: Tables that exist in the database without any migration creating them.
EXTERNAL_TABLES: frozenset[str] = frozenset(
    {"checkpoints", "checkpoint_blobs", "checkpoint_writes", "checkpoint_migrations"}
)

#: What the application may do to them. LangGraph upserts and deletes its own
#: state; the tables are created by migrations/runner.py as the migration role,
#: because the application role may not run DDL (CHY-85).
EXTERNAL_TABLE_ACCESS: tuple[Privilege, ...] = ("SELECT", "INSERT", "UPDATE", "DELETE")

CONFIGURABLE: tuple[RetentionClass, ...] = tuple(c for c, r in RULES.items() if r.configurable)

#: What a content column becomes when it is cleared, where NULL is not allowed.
#: Everything else becomes NULL.
CLEARED_VALUES: dict[tuple[str, str], str] = {
    ("workflow_runs", "input_data"): "'{}'::jsonb",
    ("pilot_runs", "input_file"): "'[purged]'",
    # Read back by the event stream of a finished run, which expects a list.
    ("pilot_runs", "events_json"): "'[]'::jsonb",
}


def clear_assignments(table: str) -> str:
    """The SET clause that clears a table's content columns, from the inventory.

    The stores carry their UPDATE as static SQL — an assembled query is what
    S608 counts, and the repository keeps that finding counted rather than
    silenced. tests/test_retention_purge.py holds every one of those
    statements to this clause, so a column added to the inventory fails a
    test until the store clears it too.
    """
    columns = INVENTORY[table].content_columns
    if not columns:
        message = f"{table} has no content columns"
        raise ValueError(message)
    return ", ".join(f"{c} = {CLEARED_VALUES.get((table, c), 'NULL')}" for c in columns)


@dataclass(frozen=True)
class Periods:
    """The period of each configurable class, installation-wide and in every scope.

    Computed once per purge, from the stored policies and the scope tree, so
    every context applies the same answer inside the same transaction.
    """

    installation: dict[str, int | None]
    by_scope: dict[UUID, dict[str, int | None]]

    def finite(self, retention_class: RetentionClass) -> tuple[list[UUID], list[int]]:
        """Scopes with a period for this class, as two arrays for `unnest`.

        A scope that keeps without limit is left out: nothing of it expires.
        """
        pairs = [(scope_id, days[retention_class.value])
                 for scope_id, days in self.by_scope.items()
                 if days.get(retention_class.value) is not None]
        return [p[0] for p in pairs], [p[1] for p in pairs]

    def installation_days(self, retention_class: RetentionClass) -> int | None:
        """For what belongs to no scope: pilot runs, feedback without a workflow."""
        return self.installation.get(retention_class.value)


def periods_from(policies: Iterable, scope_rows: Iterable) -> Periods:
    """The period of every scope, from its path — no query per scope.

    `scope_rows` carry `id` and the ltree `scope_path` as text; a scope's
    ancestors are the scopes whose path is a prefix of its own.
    """
    policies = list(policies)
    scope_rows = list(scope_rows)
    id_by_path = {row["scope_path"]: row["id"] for row in scope_rows}
    by_scope: dict[UUID, dict[str, int | None]] = {}
    for row in scope_rows:
        labels = row["scope_path"].split(".")
        prefixes = (".".join(labels[:depth]) for depth in range(1, len(labels) + 1))
        chain = [id_by_path[prefix] for prefix in prefixes if prefix in id_by_path]
        by_scope[row["id"]] = {
            c.value: effective_keep_days(policies, chain, c.value)[0] for c in CONFIGURABLE
        }
    installation = {c.value: effective_keep_days(policies, [], c.value)[0] for c in CONFIGURABLE}
    return Periods(installation=installation, by_scope=by_scope)


async def current_periods(conn) -> Periods:
    """Every period as it applies now, read on the purge's own connection."""
    return periods_from(await _policies(conn), await scopes.all_paths(conn))


async def record_purge(conn, counts: dict[str, int], periods: Periods) -> None:
    """The audit entry of one purge run, in the purge's transaction.

    Written even when nothing expired: a run that found nothing and a run that
    never happened must look different, and the newest entry is what GET
    /v1/admin/retention reports as the last purge.
    """
    await write_audit(
        conn, action=ACTION_RETENTION_PURGED, actor_id=None,
        target=target_ref("retention", "purge"),
        payload={"counts": counts,
                 "installation": periods.installation,
                 "scopes_with_own_period": sum(
                     1 for days in periods.by_scope.values() if days != periods.installation)},
    )


def effective_keep_days(policies: Iterable, chain_root_first: list[UUID],
                        retention_class: str) -> tuple[int | None, UUID | None]:
    """The period that applies in a scope, and the scope that declared it.

    The nearest declaration wins — the scope itself, then its parent, up to
    the installation-wide row (declared at None). Not the strictest, as for
    the privacy level (ADR-041): for retention, longer is not stricter, and
    neither is shorter. `keep_days` None keeps without limit.
    """
    declared = {p["scope_id"]: p["keep_days"] for p in policies
                if p["retention_class"] == retention_class}
    for scope_id in reversed(chain_root_first):
        if scope_id in declared:
            return declared[scope_id], scope_id
    return declared.get(None), None


# ── high-risk workflows keep their logs six months (CHY-266, Art. 26(6) AI Act) ──

#: Six months. Art. 26(6) asks a deployer of a high-risk AI system to keep the
#: logs it generates "for a period appropriate to the intended purpose ... of
#: at least six months"; the run trace's default (183 days) was chosen for it.
HIGH_RISK_MIN_KEEP_DAYS = 183

#: Both, decided by Thomas, 10.11.2026: the trace is the log, and the content
#: is what a reviewer of a high-risk run has to be able to read back.
HIGH_RISK_CLASSES: tuple[RetentionClass, ...] = (RetentionClass.RUN_TRACE,
                                                 RetentionClass.RUN_CONTENT)

#: The scopes that hold an active workflow counting as high-risk. Read by the
#: orchestration context, which owns workflows; the kernel imports no context,
#: so the transport hands the reader in (as the purge gets workflow_scopes).
HighRiskScopes = Callable[[asyncpg.Connection], Awaitable[set[UUID]]]


def _too_short(days: int | None) -> bool:
    """None keeps without limit, which is long enough."""
    return days is not None and days < HIGH_RISK_MIN_KEEP_DAYS


async def lock_high_risk_rule(conn) -> None:
    """One lock for both sides of the rule, held to the end of the transaction.

    A period being shortened and a high-risk workflow being activated each
    check the other's state; run at the same moment, each would pass against
    the state before the other. The lock makes them take turns.
    """
    await conn.execute("SELECT pg_advisory_xact_lock(hashtext('cohereny:high-risk-retention'))")


async def require_high_risk_periods(conn, scope_id: UUID) -> None:
    """Before a high-risk workflow becomes active in `scope_id`: both periods
    there are at least six months, or HighRiskPeriodTooShort. Takes the lock."""
    await lock_high_risk_rule(conn)
    policies = await _policies(conn)
    chain_ids = [row["id"] for row in await scopes.ancestor_chain(conn, scope_id)]
    short = {c.value: days for c in HIGH_RISK_CLASSES
             if _too_short(days := effective_keep_days(policies, chain_ids, c.value)[0])}
    if short:
        raise HighRiskPeriodTooShort({scope_id: short}, HIGH_RISK_MIN_KEEP_DAYS,
                                     activating=True)


def shortened_below_minimum(retention_class: str, before: Periods, after: Periods,
                            high_risk: set[UUID]) -> dict[UUID, dict[str, int]]:
    """High-risk scopes where a change makes the period shorter and under six months.

    Only shorter: a scope already under the minimum — activated before this
    rule, or configured by hand — must not block an unrelated change, nor the
    one that lengthens its own period towards the minimum.
    """
    if retention_class not in {c.value for c in HIGH_RISK_CLASSES}:
        return {}
    found: dict[UUID, dict[str, int]] = {}
    for scope_id in high_risk:
        old = before.by_scope.get(scope_id, before.installation).get(retention_class)
        new = after.by_scope.get(scope_id, after.installation).get(retention_class)
        if _too_short(new) and (old is None or new < old):
            found[scope_id] = {retention_class: new}
    return found


async def _refuse_shortening(conn, retention_class: str, before: Periods,
                             high_risk_scopes: HighRiskScopes) -> None:
    """After a change, inside its transaction: refuse it if it shortened a
    high-risk scope below six months. Raising rolls the change back."""
    after = periods_from(await _policies(conn), await scopes.all_paths(conn))
    found = shortened_below_minimum(retention_class, before, after,
                                    await high_risk_scopes(conn))
    if found:
        raise HighRiskPeriodTooShort(found, HIGH_RISK_MIN_KEEP_DAYS, activating=False)


async def _policies(conn) -> list:
    return await conn.fetch(
        "SELECT retention_class, scope_id, keep_days, set_by, set_at "
        "FROM retention_policies ORDER BY retention_class, scope_id NULLS FIRST"
    )


async def overview(pool, caller: Caller, scope_id: UUID | None = None) -> RetentionOverview:
    """Every retention class with its tables and periods; for one scope, what applies there.

    `reviewer` and above: whoever supervises the installation must be able to
    read how long it keeps what, not only whoever changes it (CHY-17).
    """
    require(await resolve_actor(pool, caller), "reviewer")
    rows = await _policies(pool)
    chain_ids: list[UUID] = []
    if scope_id is not None:
        chain = await scopes.ancestor_chain(pool, scope_id)
        if not chain:
            raise ScopeNotFound(scope_id)
        chain_ids = [row["id"] for row in chain]

    classes = []
    for retention_class, rule in RULES.items():
        tables = [
            RetentionTable(table=name, owner=entry.owner,
                           content_columns=list(entry.content_columns))
            for name, entry in INVENTORY.items()
            if entry.row_class is retention_class
            or (retention_class is RetentionClass.RUN_CONTENT and entry.content_columns)
        ]
        stored = [RetentionPolicy(**dict(r)) for r in rows
                  if r["retention_class"] == retention_class.value]
        out = RetentionClassOut(
            retention_class=retention_class.value, description=rule.description,
            action=rule.action, configurable=rule.configurable, reason=rule.reason,
            tables=tables, policies=stored,
        )
        if scope_id is not None and rule.configurable:
            out.effective_keep_days, out.effective_from_scope = effective_keep_days(
                rows, chain_ids, retention_class.value)
        classes.append(out)

    # The newest purge, so a supervisor sees whether the periods are applied
    # at all, and monitoring can alarm on its age rather than on a job status.
    last = await read_entries(pool, target=target_ref("retention", "purge"), limit=1)
    return RetentionOverview(
        scope_id=scope_id, classes=classes,
        last_purge_at=last[0].created_at if last else None,
        last_purge_counts=last[0].payload.get("counts") if last else None,
        external_stores=[ExternalStoreOut(name=s.name, retention_class=s.retention_class.value,
                                          handled_by=s.handled_by)
                         for s in EXTERNAL_STORES],
    )


async def set_policy(pool, caller: Caller, retention_class: str,
                     body: RetentionPolicySet, *,
                     high_risk_scopes: HighRiskScopes) -> RetentionPolicy:
    """Set a period, installation-wide or for one scope, audited. `admin`.

    The old value is read under lock and written into the entry next to the
    new one: a period that changes without saying from what is a change nobody
    can judge afterwards — the same reasoning as for a role change.

    Refused if it shortens the run trace or content below six months in a
    scope with an active high-risk workflow, also from further up the chain
    (CHY-266).
    """
    performer = await resolve_actor(pool, caller)
    require(performer, "admin")

    async with pool.acquire() as conn, conn.transaction():
        await lock_high_risk_rule(conn)
        before = periods_from(await _policies(conn), await scopes.all_paths(conn))
        previous = await conn.fetchrow(
            "SELECT keep_days FROM retention_policies "
            "WHERE retention_class = $1 AND scope_id IS NOT DISTINCT FROM $2 FOR UPDATE",
            retention_class, body.scope_id,
        )
        try:
            row = await conn.fetchrow(
                "INSERT INTO retention_policies (retention_class, scope_id, keep_days, set_by) "
                "VALUES ($1, $2, $3, $4) "
                "ON CONFLICT ON CONSTRAINT retention_policies_one_per_scope DO UPDATE "
                "SET keep_days = EXCLUDED.keep_days, set_by = EXCLUDED.set_by, "
                "set_at = clock_timestamp() "
                "RETURNING retention_class, scope_id, keep_days, set_by, set_at",
                retention_class, body.scope_id, body.keep_days, performer.id,
            )
        except asyncpg.exceptions.ForeignKeyViolationError as exc:
            raise ScopeNotFound(body.scope_id) from exc
        await write_audit(
            conn, action=ACTION_RETENTION_POLICY_CHANGED, actor_id=performer.id,
            target=target_ref("retention", retention_class),
            payload={"retention_class": retention_class,
                     "scope_id": str(body.scope_id) if body.scope_id else None,
                     "from_keep_days": previous["keep_days"] if previous else None,
                     "had_policy": previous is not None,
                     "to_keep_days": body.keep_days},
        )
        await _refuse_shortening(conn, retention_class, before, high_risk_scopes)
    return RetentionPolicy(**dict(row))


async def remove_override(pool, caller: Caller, retention_class: str, scope_id: UUID, *,
                          high_risk_scopes: HighRiskScopes) -> None:
    """Take a scope's own period away, so the inherited one applies again. `admin`.

    Only for a scope. The installation-wide period can be changed, not
    removed: without it, a scope nobody configured would keep without limit
    because a row was missing, not because anyone decided so.

    Refused like set_policy if the inherited period is shorter and under six
    months in a scope with an active high-risk workflow (CHY-266).
    """
    performer = await resolve_actor(pool, caller)
    require(performer, "admin")

    async with pool.acquire() as conn, conn.transaction():
        await lock_high_risk_rule(conn)
        before = periods_from(await _policies(conn), await scopes.all_paths(conn))
        removed = await conn.fetchrow(
            "DELETE FROM retention_policies WHERE retention_class = $1 AND scope_id = $2 "
            "RETURNING keep_days",
            retention_class, scope_id,
        )
        if removed is None:
            raise RetentionOverrideNotFound(retention_class, scope_id)
        await write_audit(
            conn, action=ACTION_RETENTION_POLICY_CHANGED, actor_id=performer.id,
            target=target_ref("retention", retention_class),
            payload={"retention_class": retention_class, "scope_id": str(scope_id),
                     "from_keep_days": removed["keep_days"], "had_policy": True,
                     "removed": True},
        )
        await _refuse_shortening(conn, retention_class, before, high_risk_scopes)
