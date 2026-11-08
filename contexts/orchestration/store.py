"""
contexts/orchestration/store.py

Data access for the orchestration context — the only place with SQL
(ADR-030, CHY-82).

`workflow_definitions`, `workflow_nodes` and `workflow_connections` have
existed since 001_initial, and until the runner of ADR-028 nothing wrote them:
all ten endpoints answered 501. Checked at the host rather than assumed — zero rows in dev, test
and prod on 2026-09-04. That same check turned up the migration lag of test
and prod (CHY-161; first noted here under the wrong number, CHY-128).

Column lists are written out in each query rather than kept in a constant and
interpolated. Interpolating even a constant makes it a built query — to any
reader and to S608 — and that finding is worth reserving for the two places
where something really varies. The repository store made the same call for the
same reason. Nine near-identical SELECT lists is the price, and it is paid in
reading rather than in risk.

Dependencies:
  - asyncpg
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from uuid import UUID

from asyncpg.exceptions import (
    CheckViolationError,
    ForeignKeyViolationError,
    UniqueViolationError,
)

from contexts.orchestration.errors import (
    NodeNotStorable,
    ReviewerAlreadyDecided,
    TargetWorkflowNotFound,
)
from kernel.errors import ScopeNotFound


# ── reading ───────────────────────────────────────────────────────────────────

async def workflow(conn_or_pool, workflow_id: UUID):
    return await conn_or_pool.fetchrow(
        "SELECT id, scope_id, name, description, version, superseded_by_version, "
        "       status, risk_classification, created_by, created_at "
        "FROM workflow_definitions WHERE id = $1",
        workflow_id,
    )


async def lock_workflow(conn, workflow_id: UUID):
    """The status, held for the length of the transaction.

    Two concurrent activations would otherwise both read `draft`, both validate
    the graph they saw, and both write — and the second would have validated a
    graph the first had already released.
    """
    return await conn.fetchrow(
        "SELECT id, status, version FROM workflow_definitions WHERE id = $1 FOR UPDATE",
        workflow_id,
    )


async def all_workflows(pool) -> list:
    return await pool.fetch(
        "SELECT id, scope_id, name, description, version, superseded_by_version, "
        "       status, risk_classification, created_by, created_at "
        "FROM workflow_definitions ORDER BY name, version"
    )


async def workflows_in_scope(pool, scope_id: UUID) -> list:
    return await pool.fetch(
        "SELECT id, scope_id, name, description, version, superseded_by_version, "
        "       status, risk_classification, created_by, created_at "
        "FROM workflow_definitions WHERE scope_id = $1 ORDER BY name, version",
        scope_id,
    )


async def nodes(conn_or_pool, workflow_id: UUID) -> list:
    return await conn_or_pool.fetch(
        "SELECT id, workflow_id, node_ref, node_type, name, description, "
        "       join_policy, config, context_requirements, task_type, "
        "       target_workflow_id, target_workflow_version, "
        "       input_mapping, output_mapping, review_role, review_timeout, on_timeout "
        "FROM workflow_nodes WHERE workflow_id = $1 ORDER BY node_ref",
        workflow_id,
    )


async def connections(conn_or_pool, workflow_id: UUID) -> list:
    return await conn_or_pool.fetch(
        "SELECT id, workflow_id, source_node_id, target_node_id, condition, label, "
        "       execution_mode, loop_limit "
        "FROM workflow_connections WHERE workflow_id = $1 ORDER BY id",
        workflow_id,
    )


async def node_by_ref(conn, workflow_id: UUID, node_ref: str):
    return await conn.fetchrow(
        "SELECT id, node_ref FROM workflow_nodes "
        "WHERE workflow_id = $1 AND node_ref = $2",
        workflow_id, node_ref,
    )


async def sub_workflow_calls(conn_or_pool) -> list:
    """Every sub_workflow edge in the estate: caller → callee.

    The whole set rather than a walk from one workflow. The call graph is small
    — one row per sub_workflow node — and fetching it once lets the cycle check
    run as pure code instead of as a recursive query that would have to be
    reasoned about separately from the traversal it duplicates.
    """
    return await conn_or_pool.fetch(
        "SELECT workflow_id, target_workflow_id FROM workflow_nodes "
        "WHERE node_type = 'sub_workflow' AND target_workflow_id IS NOT NULL"
    )


# ── writing ───────────────────────────────────────────────────────────────────

async def insert_workflow(conn, body, created_by: UUID):
    """Create version 1 of a workflow in a scope.

    An unknown scope is translated to `ScopeNotFound` — a 404 — rather than
    leaving as a foreign-key violation and a 500 (CHY-165, found by
    Schemathesis on its first run).
    """
    try:
        return await _insert_workflow(conn, body, created_by)
    except ForeignKeyViolationError as exc:
        if exc.constraint_name == "workflow_definitions_scope_id_fkey":
            raise ScopeNotFound(body.scope_id) from exc
        raise


async def _insert_workflow(conn, body, created_by: UUID):
    return await conn.fetchrow(
        "INSERT INTO workflow_definitions "
        "(scope_id, name, description, risk_classification, created_by) "
        "VALUES ($1, $2, $3, $4::text::jsonb, $5) "
        "RETURNING id, scope_id, name, description, version, superseded_by_version, "
        "          status, risk_classification, created_by, created_at",
        body.scope_id, body.name, body.description,
        json.dumps(body.risk_classification), created_by,
    )


async def insert_node(conn, workflow_id: UUID, body):
    """Write one node, and turn the table's refusals into sentences.

    The CHECK constraints of `workflow_nodes` carry rules the request model
    cannot: they span two columns, or they use a vocabulary that belongs to
    another context (ADR-030). Letting them arrive as a 500 would tell a caller
    the server broke, when it refused something it can name.
    """
    try:
        return await _insert_node(conn, workflow_id, body)
    except CheckViolationError as exc:
        raise NodeNotStorable(exc.constraint_name or "an unnamed constraint") from exc
    except ForeignKeyViolationError as exc:
        # Only the target reference is the caller's: the path workflow was
        # looked up and locked before this insert (service.add_node).
        if exc.constraint_name == "workflow_nodes_target_workflow_id_fkey":
            raise TargetWorkflowNotFound(body.target_workflow_id) from exc
        raise


async def _insert_node(conn, workflow_id: UUID, body):
    return await conn.fetchrow(
        "INSERT INTO workflow_nodes "
        "(workflow_id, node_ref, node_type, name, description, join_policy, config, "
        " context_requirements, task_type, "
        " target_workflow_id, target_workflow_version, input_mapping, output_mapping, "
        " review_role, review_timeout, on_timeout) "
        "VALUES ($1, $2, $3, $4, $5, $6, $7::text::jsonb, $8::text::jsonb, $9, $10, $11, "
        "        $12::text::jsonb, $13::text::jsonb, $14, $15, $16) "
        "RETURNING id, workflow_id, node_ref, node_type, name, description, "
        "          join_policy, config, context_requirements, task_type, "
        "          target_workflow_id, target_workflow_version, "
        "          input_mapping, output_mapping, review_role, review_timeout, on_timeout",
        workflow_id, body.node_ref, body.node_type, body.name, body.description,
        body.join_policy, json.dumps(body.config),
        # NULL and not '{}': the column is absent for every node type but one,
        # and an empty object would be a second spelling of "no requirements"
        # that the CHECK in 022 refuses anyway.
        None if body.context_requirements is None
        else body.context_requirements.model_dump_json(),
        body.task_type,
        body.target_workflow_id, body.target_workflow_version,
        json.dumps(body.input_mapping), json.dumps(body.output_mapping),
        body.review_role, body.review_timeout, body.on_timeout,
    )


async def insert_connection(conn, workflow_id: UUID, *, source_id: UUID,
                            target_id: UUID, body):
    return await conn.fetchrow(
        "INSERT INTO workflow_connections "
        "(workflow_id, source_node_id, target_node_id, condition, label, "
        " execution_mode, loop_limit) "
        "VALUES ($1, $2, $3, $4::text::jsonb, $5, $6, $7) "
        "RETURNING id, workflow_id, source_node_id, target_node_id, condition, "
        "          label, execution_mode, loop_limit",
        workflow_id, source_id, target_id,
        None if body.condition is None else json.dumps(body.condition), body.label,
        body.execution_mode, body.loop_limit,
    )


async def delete_connection(conn, workflow_id: UUID, connection_id: UUID) -> str:
    return await conn.execute(
        "DELETE FROM workflow_connections WHERE workflow_id = $1 AND id = $2",
        workflow_id, connection_id,
    )


async def set_status(conn, workflow_id: UUID, status: str):
    return await conn.fetchrow(
        "UPDATE workflow_definitions SET status = $1 WHERE id = $2 "
        "RETURNING id, scope_id, name, description, version, superseded_by_version, "
        "          status, risk_classification, created_by, created_at",
        status, workflow_id,
    )


def row_to_dict(row) -> dict:
    """A row with its JSONB columns parsed.

    asyncpg returns JSONB as str on some versions and as dict on others. The
    repository context learned this the same way and has the same helper.
    """
    d = dict(row)
    for column in ("config", "input_mapping", "output_mapping",
                   "risk_classification", "context_requirements"):
        if isinstance(d.get(column), str):
            d[column] = json.loads(d[column])
    return d


# ── runs (CHY-127) ────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class NewRun:
    """The row a starting run is made of.

    An object rather than seven parameters, and the two parent fields are the
    reason: `insert_run` and `insert_child_run` differed only in whether they
    were set, and repeated the same twelve-column RETURNING list to say so. Two
    functions that differ by two optional values are one function with two
    optional values.
    """

    workflow_id: UUID
    workflow_version: int
    actor_id: UUID
    input_data: dict
    #: Set when a sub_workflow node started this run. Its own row, its own
    #: audit trail, its own loop counters — not a subgraph (ADR-028 point 2).
    parent_run_id: UUID | None = None
    parent_node_id: UUID | None = None


async def insert_run(conn, new: NewRun):
    return await conn.fetchrow(
        "INSERT INTO workflow_runs "
        "(workflow_id, workflow_version, actor_id, input_data, status, started_at, "
        " parent_run_id, parent_node_id) "
        "VALUES ($1, $2, $3, $4::text::jsonb, 'running', now(), $5, $6) "
        "RETURNING id, workflow_id, workflow_version, status, actor_id, input_data, "
        "          result, error_message, started_at, completed_at, created_at, "
        "          parent_run_id, parent_node_id",
        new.workflow_id, new.workflow_version, new.actor_id,
        json.dumps(new.input_data), new.parent_run_id, new.parent_node_id,
    )


async def lock_run(conn, run_id: UUID):
    """The run, held for the length of the advance.

    Two advances at once — an API call and a resumption, say — would otherwise
    both read the same state, both plan the same step, and both start it. The
    lock is what makes "advance" a single-threaded operation per run without
    any of it living in a process.
    """
    return await conn.fetchrow(
        "SELECT id, workflow_id, workflow_version, status, actor_id, input_data, "
        "       result, error_message, started_at, completed_at, created_at, "
        "       parent_run_id, parent_node_id, content_purged_at "
        "FROM workflow_runs WHERE id = $1 FOR UPDATE",
        run_id,
    )


async def run(conn_or_pool, run_id: UUID):
    return await conn_or_pool.fetchrow(
        "SELECT id, workflow_id, workflow_version, status, actor_id, input_data, "
        "       result, error_message, started_at, completed_at, created_at, "
        "       parent_run_id, parent_node_id, content_purged_at "
        "FROM workflow_runs WHERE id = $1",
        run_id,
    )


# ── the run's mapping (CHY-234) ───────────────────────────────────────────────
#
# Sealed and opened by the service with kernel.mapping_cipher; here it is bytes.

async def insert_pii_mapping(conn, run_id: UUID, *, key_id: str, nonce: bytes,
                             ciphertext: bytes) -> None:
    await conn.execute(
        "INSERT INTO pii_mappings (run_id, key_id, nonce, ciphertext) VALUES ($1, $2, $3, $4)",
        run_id, key_id, nonce, ciphertext,
    )


async def pii_mapping(conn, run_id: UUID):
    """The run's sealed mapping, or None for a run started before CHY-234."""
    return await conn.fetchrow(
        "SELECT key_id, nonce, ciphertext FROM pii_mappings WHERE run_id = $1", run_id)


async def lock_pii_mapping(conn, run_id: UUID):
    """As `pii_mapping`, held until the transaction ends.

    Two steps of a run that both found something new would otherwise both
    extend the mapping they read, and the second write would drop the first
    one's names (E1, decided 06.11.2026).
    """
    return await conn.fetchrow(
        "SELECT key_id, nonce, ciphertext FROM pii_mappings WHERE run_id = $1 FOR UPDATE",
        run_id)


async def update_pii_mapping(conn, run_id: UUID, *, key_id: str, nonce: bytes,
                             ciphertext: bytes) -> None:
    await conn.execute(
        "UPDATE pii_mappings SET key_id = $2, nonce = $3, ciphertext = $4, "
        "sealed_at = clock_timestamp() WHERE run_id = $1",
        run_id, key_id, nonce, ciphertext,
    )


async def executions(conn_or_pool, run_id: UUID) -> list:
    """Every pass through every node of this run, oldest first.

    The whole history and not only the latest per node: a loop produces several
    passes, and which pass a node is on is what the state machine hands back as
    `iteration`.
    """
    return await conn_or_pool.fetch(
        "SELECT node_id, iteration, status, consumed_edges, output_data, model_used "
        "FROM node_executions WHERE run_id = $1 ORDER BY started_at, iteration",
        run_id,
    )


async def execution_history(conn_or_pool, run_id: UUID) -> list:
    """Every pass through every node, as somebody reading the run needs it (CHY-229).

    `executions` above is the runner's view: node ids, consumed edges, output.
    A reader — above all whoever decides at a `human_review` gate — needs to
    know which node each pass belongs to and why one failed, so the node's
    ref, type and name come along and the error does too.
    """
    return await conn_or_pool.fetch(
        "SELECT n.node_ref, n.node_type, n.name, e.iteration, e.status, "
        "       e.output_data, e.model_used, e.error_message, "
        "       e.started_at, e.completed_at "
        "FROM node_executions e JOIN workflow_nodes n ON n.id = e.node_id "
        "WHERE e.run_id = $1 ORDER BY e.started_at, e.iteration",
        run_id,
    )


async def traversals(conn_or_pool, run_id: UUID) -> list:
    return await conn_or_pool.fetch(
        "SELECT connection_id, traversals FROM loop_counters WHERE run_id = $1",
        run_id,
    )


@dataclass(frozen=True)
class NewExecution:
    """One pass through one node, about to begin."""

    run_id: UUID
    node_id: UUID
    iteration: int
    #: The connections whose tokens this pass uses up. Recorded on *start*, not
    #: on finish: a node that died halfway would otherwise leave its token in
    #: place and arm itself again — an infinite loop in the costume of a retry.
    consumed: list[UUID]
    input_data: dict


async def start_execution(conn, new: NewExecution):
    """Record that a node has begun, before it does anything.

    Written first and not after: `in_flight` is read back from exactly these
    rows, and a node that ran without leaving a trace first could be handed out
    a second time by a concurrent advance. The consumed edges go in here too —
    they are consumed by *starting*, not by finishing, or a crash mid-node
    would leave the token to arm the same node again.
    """
    return await conn.fetchrow(
        "INSERT INTO node_executions "
        "(run_id, node_id, iteration, status, consumed_edges, input_data, started_at) "
        "VALUES ($1, $2, $3, 'running', $4::uuid[], $5::text::jsonb, now()) "
        "RETURNING id",
        new.run_id, new.node_id, new.iteration, new.consumed,
        json.dumps(new.input_data),
    )


async def complete_execution(conn, execution_id: UUID, *,
                             output: dict, model_used: str | None) -> None:
    """A pass that produced something.

    Two functions rather than one with a `status` parameter. Completing and
    failing take different data — an output and a model on one side, a message
    on the other — and a single function has to accept all of it and trust the
    caller to pass the right half. The status then becomes a flag that can be
    wrong, and a failed execution carrying an output looks like a successful one.
    """
    await conn.execute(
        "UPDATE node_executions SET status = 'complete', output_data = $2::text::jsonb, "
        "       model_used = $3, completed_at = now() "
        "WHERE id = $1",
        execution_id, json.dumps(output), model_used,
    )


async def fail_execution(conn, execution_id: UUID, error: str) -> None:
    await conn.execute(
        "UPDATE node_executions SET status = 'failed', error_message = $2, "
        "       completed_at = now() WHERE id = $1",
        execution_id, error,
    )


async def cancel_open_executions(conn, run_id: UUID, reason: str | None) -> list[str]:
    """Stop every node of the run that has not finished; returns their node_refs.

    Usually one — the gate the run waits at, or the node in flight — but a fork
    can have several branches running, and all of them stop with the run
    (CHY-200).
    """
    rows = await conn.fetch(
        "WITH stopped AS ("
        "    UPDATE node_executions SET status = 'cancelled', error_message = $2, "
        "           completed_at = now() "
        "    WHERE run_id = $1 AND status IN ('pending', 'running') "
        "    RETURNING node_id) "
        "SELECT n.node_ref FROM stopped s JOIN workflow_nodes n ON n.id = s.node_id "
        "ORDER BY n.node_ref",
        run_id, reason,
    )
    return [row["node_ref"] for row in rows]


async def record_traversals(conn, run_id: UUID, edge_ids: list[UUID]) -> None:
    """Count each edge one more time.

    ON CONFLICT rather than read-modify-write: two branches of a fork can reach
    the same edge in the same advance, and a read-then-write would lose one of
    them. The counter is what loop_limit is compared against, so losing one is
    an extra lap.
    """
    if not edge_ids:
        return
    await conn.executemany(
        "INSERT INTO loop_counters (run_id, connection_id, traversals) "
        "VALUES ($1, $2, 1) "
        "ON CONFLICT (run_id, connection_id) DO UPDATE "
        "SET traversals = loop_counters.traversals + 1",
        [(run_id, edge_id) for edge_id in edge_ids],
    )


async def set_run_status(conn, run_id: UUID, status: str, *,
                         result: dict | None = None, error: str | None = None):
    """Move the run, and stamp completed_at only when it is actually over.

    `awaiting_oversight` is not an end: a run that came back from a gate and
    finished later would otherwise carry the timestamp of the moment it
    stopped, which is the one thing a duration must not be measured from.
    """
    return await conn.fetchrow(
        "UPDATE workflow_runs SET status = $2, result = $3::text::jsonb, "
        "       error_message = $4, "
        "       completed_at = CASE WHEN $2 IN ('complete', 'failed', 'rejected', 'cancelled') "
        "                           THEN now() ELSE completed_at END "
        "WHERE id = $1 "
        "RETURNING id, workflow_id, workflow_version, status, actor_id, input_data, "
        "          result, error_message, started_at, completed_at, created_at, "
        "          parent_run_id, parent_node_id",
        run_id, status, json.dumps(result) if result is not None else None, error,
    )


async def running_execution(conn, run_id: UUID):
    """The node this run is currently sitting on.

    At a gate there is exactly one — `runner.plan` stops the whole run at a
    review, so nothing else is in flight. Ordered and limited anyway: a query
    that assumes uniqueness and is wrong returns an arbitrary row, and an
    arbitrary row is the worst possible answer to "which decision is this".
    """
    return await conn.fetchrow(
        "SELECT id, node_id, iteration FROM node_executions "
        "WHERE run_id = $1 AND status = 'running' "
        "ORDER BY started_at DESC LIMIT 1",
        run_id,
    )


async def review_role(conn, node_id: UUID) -> str | None:
    """Who the gate asks for: `approver`, `four_eyes`, or nothing set.

    Read at the decision, not carried in the graph. `graph.Node` holds what
    traversal needs, and who may release a gate is not a traversal question.
    """
    return await conn.fetchval(
        "SELECT review_role FROM workflow_nodes WHERE id = $1", node_id
    )


async def record_vote(conn, execution_id: UUID, actor_id: UUID,
                      decision: str, reason: str | None) -> None:
    """One person's decision at one gate execution (CHY-176, migration 027).

    The primary key refuses a second decision by the same person. Checking
    first would be a second round trip and a race; the constraint is the rule
    and this only translates it — the same call kernel/scopes.py makes for
    memberships.
    """
    try:
        await conn.execute(
            "INSERT INTO review_votes (execution_id, actor_id, decision, reason) "
            "VALUES ($1, $2, $3, $4)",
            execution_id, actor_id, decision, reason,
        )
    except UniqueViolationError:
        raise ReviewerAlreadyDecided(actor_id) from None


async def approvals(conn, execution_id: UUID) -> int:
    """How many different people have approved this gate execution."""
    return await conn.fetchval(
        "SELECT count(*) FROM review_votes "
        "WHERE execution_id = $1 AND decision = 'approved'",
        execution_id,
    )


async def node_details(conn, workflow_id: UUID) -> list:
    """Per-node configuration, context requirements, task type and input mapping.

    Loaded separately from the graph on purpose. `graph.Node` carries what
    traversal needs — id, ref, type, join policy — and nothing else. Putting a
    node's configuration there would let a future routing rule quietly start
    depending on it, and routing belongs to the state machine, which must stay
    testable without any of this.
    """
    return await conn.fetch(
        "SELECT id, config, context_requirements, task_type, input_mapping "
        "FROM workflow_nodes WHERE workflow_id = $1",
        workflow_id,
    )


async def predecessor_outputs(conn, run_id: UUID, node_ids: list[UUID]) -> list:
    """The latest completed output of each named node in this run.

    `DISTINCT ON (node_id)` with the ordering below picks the **most recent
    pass**, which is what a node inside a loop needs: on iteration three it
    reads what its predecessor produced on iteration three, not on the first.
    Reading "the" output of a node is only unambiguous outside a loop, and the
    runner exists to support loops.

    Only `complete`. A failed pass has no output worth passing on, and a
    running one has none yet — either would hand a node the leftovers of the
    previous iteration while looking exactly like a fresh value.
    """
    return await conn.fetch(
        "SELECT DISTINCT ON (node_id) node_id, output_data "
        "FROM node_executions "
        "WHERE run_id = $1 AND node_id = ANY($2::uuid[]) AND status = 'complete' "
        "ORDER BY node_id, iteration DESC, completed_at DESC",
        run_id, node_ids,
    )


# ── retention (CHY-17, ADR-046) ───────────────────────────────────────────────

_CLEAR_NODE_EXECUTIONS = (
    "UPDATE node_executions SET input_data = NULL, output_data = NULL, error_message = NULL "
    "WHERE run_id = ANY($1::uuid[])"
)
_CLEAR_REVIEW_VOTES = (
    "UPDATE review_votes SET reason = NULL "
    "WHERE execution_id IN (SELECT id FROM node_executions WHERE run_id = ANY($1::uuid[]))"
)
_CLEAR_PII_MAPPINGS = (
    "UPDATE pii_mappings SET nonce = NULL, ciphertext = NULL "
    "WHERE run_id = ANY($1::uuid[])"
)
_CLEAR_WORKFLOW_RUNS = (
    "UPDATE workflow_runs SET input_data = '{}'::jsonb, result = NULL, error_message = NULL, "
    "content_purged_at = now() WHERE id = ANY($1::uuid[])"
)

#: Runs nothing happens to any more. A pending, running or waiting run is never
#: touched, however old: its content is still the work in progress.
_FINISHED_RUN_STATES: list[str] = ["complete", "failed", "rejected", "cancelled"]


async def runs_past_period(conn, scope_ids: list[UUID], keep_days: list[int], *,
                           only_uncleared: bool) -> list[UUID]:
    """Finished runs whose period, in their workflow's scope, has passed.

    `scope_ids` and `keep_days` are parallel arrays — one period per scope, as
    kernel.retention.Periods.finite gives them. A run that never completed
    counts from its creation, so a run that failed before starting expires too.
    """
    rows = await conn.fetch(
        "SELECT r.id FROM workflow_runs r "
        "JOIN workflow_definitions d ON d.id = r.workflow_id "
        "JOIN unnest($1::uuid[], $2::int[]) AS p(scope_id, keep_days) "
        "  ON p.scope_id = d.scope_id "
        "WHERE r.status = ANY($3::text[]) "
        "  AND COALESCE(r.completed_at, r.created_at) "
        "      < now() - make_interval(days => p.keep_days) "
        "  AND (NOT $4::bool OR r.content_purged_at IS NULL)",
        scope_ids, keep_days, _FINISHED_RUN_STATES, only_uncleared,
    )
    return [row["id"] for row in rows]


async def clear_run_content(conn, run_ids: list[UUID]) -> None:
    """Clear what went into these runs and came out; their trace stays.

    The statements are below, static. What they clear is decided by the
    inventory (kernel/retention.py): tests/test_retention_purge.py holds each
    SET clause to kernel.retention.clear_assignments, so a column added there
    fails a test until it is cleared here too. Assembling the clause at run
    time would say the same, and S608 would count it as an assembled query.
    """
    await conn.execute(_CLEAR_NODE_EXECUTIONS, run_ids)
    await conn.execute(_CLEAR_REVIEW_VOTES, run_ids)
    await conn.execute(_CLEAR_PII_MAPPINGS, run_ids)
    await conn.execute(_CLEAR_WORKFLOW_RUNS, run_ids)


async def delete_runs(conn, run_ids: list[UUID]) -> tuple[int, int]:
    """Delete runs leaf first. Returns how many went and how many are held.

    A run that started a sub-workflow is the parent of that run's row, and the
    sub-run may live in a scope that keeps longer. It is not deleted out from
    under it: the parent waits, counted as held, until its last sub-run has
    gone. Node executions and loop counters go with their run (ON DELETE
    CASCADE); votes refer to executions without a cascade, deliberately
    (migration 027), so they are deleted here first, by name.
    """
    remaining = set(run_ids)
    deleted = 0
    while remaining:
        leaves = [row["id"] for row in await conn.fetch(
            "SELECT r.id FROM workflow_runs r WHERE r.id = ANY($1::uuid[]) "
            "AND NOT EXISTS (SELECT 1 FROM workflow_runs c WHERE c.parent_run_id = r.id)",
            list(remaining))]
        if not leaves:
            break
        await conn.execute(
            "DELETE FROM review_votes WHERE execution_id IN "
            "(SELECT id FROM node_executions WHERE run_id = ANY($1::uuid[]))", leaves)
        await conn.execute("DELETE FROM workflow_runs WHERE id = ANY($1::uuid[])", leaves)
        deleted += len(leaves)
        remaining -= set(leaves)
    return deleted, len(remaining)


async def workflow_scopes(conn) -> dict[UUID, UUID]:
    """Each workflow's scope — for contexts that hold rows per workflow."""
    return {row["id"]: row["scope_id"]
            for row in await conn.fetch("SELECT id, scope_id FROM workflow_definitions")}
