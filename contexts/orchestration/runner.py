"""
contexts/orchestration/runner.py

The workflow state machine, as pure rules (CHY-127, ADR-028, ADR-026).

**A run is advanced, not executed.** The core operation is "move this run as
far as it can go, then stop" — because it finished, because it failed, or
because it reached a human review gate. That shape is not a style choice: it is
what makes ADR-028 point 2 true. A gate is a row in the database, so resuming
after a decision is *the same operation* as starting, and nothing about a run
lives in a thread that a restart can lose.

Nothing here opens a connection, evaluates an expression, or calls a model. It
answers one question — given this graph and what has happened so far, what
happens next — and `service.py` does what it says.

Why tokens and not a set of finished nodes
-------------------------------------------
The obvious model is "a node is ready when its predecessors are done". It falls
apart on the second lap of a loop: every predecessor is still done, so every
node is still ready, forever.

Completing a node therefore places a **token** on each outgoing edge that is
taken, and a node fires by **consuming** the tokens that armed it. A loop is
then a token travelling back around, `wait_all` is "a token on every incoming
edge", and none of the three needs a special case. It is the Petri-net reading
of a workflow, and it is the only one found that handles fork, join and cycle
with the same rule.

Why loop_limit is counted per edge
-----------------------------------
ADR-026, and reason 1 of ADR-028 for not using LangGraph: two independent loops
must not limit one another. A counter on the run cannot tell them apart. The
counter therefore lives on the traversal of one edge in one run, which is
exactly what the `loop_counters` table stores.

ADR-028 requires that join policies and resumption get tests before they get
callers. This module is written to that order.
"""
from __future__ import annotations

import enum
from collections.abc import Mapping
from dataclasses import dataclass, field
from uuid import UUID

from contexts.orchestration.graph import Edge, Graph

#: What a join gateway waits for. Mirrors workflow_nodes.join_policy.
WAIT_ALL = "wait_all"
WAIT_ANY = "wait_any"
FIRST_WINS = "first_wins"

#: The label that marks the fall-through of an XOR branch. A conditional
#: branch where nothing matches and no default exists fails the run — arc42
#: §5.6 is explicit that quietly going nowhere is not an option.
DEFAULT_LABEL = "default"


class Outcome(enum.Enum):
    """What advancing the run resolved to."""

    RUN = "run"
    """One or more nodes are ready. `Plan.steps` says which."""

    AWAIT_REVIEW = "await_review"
    """A human_review node is ready. The run stops here and becomes a row in
    `awaiting_oversight` — not a blocked thread (ADR-028 point 2)."""

    COMPLETE = "complete"
    """No tokens left and nothing ready. The run is over.

    There is no FAILED here. A run fails from `fire`, at the node where the
    rule broke — an XOR branch with nothing to take, a loop that used up its
    limit with nothing else under way — because that is where the reason is
    known. Until CHY-150 a FAILED member stood here, promised exactly the
    second case, and nothing ever returned it: the run ended as COMPLETE."""


@dataclass(frozen=True)
class Step:
    """One node, about to run for the nth time."""

    node_id: UUID
    iteration: int


@dataclass(frozen=True)
class Plan:
    """What to do next, and why.

    `reason` is filled for FAILED and AWAIT_REVIEW. It is the text a person
    reads when a run stops, and a run that stops without saying why is the
    thing this whole module exists to avoid.
    """

    outcome: Outcome
    steps: tuple[Step, ...] = ()
    reason: str | None = None


@dataclass(frozen=True)
class RunState:
    """What has happened in one run so far.

    Reconstructed from `node_executions` and `loop_counters` on every advance
    rather than held in memory. That is the point of ADR-028: a run that lives
    only in a process cannot be resumed by another instance, and cannot be
    resumed at all after a restart.
    """

    #: connection id → tokens waiting on that edge
    tokens: Mapping[UUID, int] = field(default_factory=dict)
    #: node id → how often it has completed
    completed: Mapping[UUID, int] = field(default_factory=dict)
    #: connection id → how often it has been traversed in this run
    traversals: Mapping[UUID, int] = field(default_factory=dict)
    #: nodes that have been started and not yet finished, so an advance does
    #: not hand out the same step twice
    in_flight: frozenset[UUID] = frozenset()

    def armed(self, edge: Edge) -> bool:
        return self.tokens.get(edge.id, 0) > 0


@dataclass(frozen=True)
class Firing:
    """What completing one node does to the run.

    Separate from `plan` because it answers a different question at a different
    moment: `plan` decides what to start, `fire` records what starting it led
    to. Merging them would mean the planner needed the node's output.
    """

    #: edges that receive a token
    taken: tuple[UUID, ...] = ()
    #: edges refused because their loop_limit is used up
    exhausted: tuple[UUID, ...] = ()
    #: set when the node's outgoing edges leave nowhere to go
    failure: str | None = None


def ready(graph: Graph, state: RunState) -> tuple[Step, ...]:
    """Every node that may start right now.

    A node with no incoming edges starts once, at the beginning — that is the
    entry point, and it needs no token because nothing gives it one.

    Everything else fires on tokens. `wait_all` needs one on every incoming
    edge; `wait_any` and `first_wins` need one anywhere. The difference between
    the latter two is not in *when* they fire but in what happens to the
    stragglers, which `consume` decides.

    A node already in flight is never handed out again. Without that, two
    advances in quick succession — an API call and a resumption, say — would
    both see the same armed edge and start the node twice.
    """
    steps = []
    for node in graph.nodes:
        if node.id in state.in_flight:
            continue
        incoming = graph.predecessors(node.id)
        if not incoming:
            if state.completed.get(node.id, 0) == 0:
                steps.append(Step(node.id, 1))
            continue
        armed = [e for e in incoming if state.armed(e)]
        if not armed:
            continue
        if node.join_policy == WAIT_ALL and len(armed) < len(incoming):
            continue
        steps.append(Step(node.id, state.completed.get(node.id, 0) + 1))
    return tuple(steps)


def consume(graph: Graph, state: RunState, node_id: UUID) -> tuple[UUID, ...]:
    """The edges whose tokens firing this node uses up.

    `first_wins` consumes **every** incoming edge, not only the armed ones —
    that is the whole difference between it and `wait_any`. The stragglers'
    tokens are discarded when they arrive, because the gateway has already
    decided. `wait_any` leaves them, so a second arrival fires the gateway
    again, which is what "any" means when a loop feeds it twice.
    """
    node = graph.node(node_id)
    incoming = graph.predecessors(node_id)
    if node.join_policy == FIRST_WINS:
        return tuple(e.id for e in incoming)
    return tuple(e.id for e in incoming if state.armed(e))


def fire(
    graph: Graph, state: RunState, node_id: UUID, *, conditions: Mapping[UUID, bool],
    excluding: frozenset[UUID] = frozenset(),
) -> Firing:
    """Where the run goes after this node completes.

    `conditions` maps each guarded outgoing edge to whether its guard held.
    Evaluating a guard needs the node's output and an expression language;
    deciding what a branch *means* does not. The split keeps this function pure
    and keeps the XOR rule out of whatever evaluates expressions later.

    Rules, in order:

    1. An edge whose loop_limit is used up is not taken, and reported as
       `exhausted`. A guarded one counts as a guard that did not hold, so a
       `default` edge takes over: the loop has used its budget, the run goes
       the way it goes when the criterion is not met (CHY-150). A loop that has
       run out while nothing else in the run is ready or running is a failure,
       decided here because only here is the edge known (rule 6).
    2. Unguarded edges without a `default` label are always taken. That is a
       fork.
    3. Guarded edges are taken when their guard held.
    4. A `default` edge is taken only when guards existed and none held.
    5. If nothing is left and the node had outgoing edges, the run **fails**.
       arc42 §5.6: an XOR branch where no condition matches and no default
       exists ends the run with an error, because quietly going nowhere is
       indistinguishable from having finished.
    6. The same for a loop that has run out with nothing else to go on
       (Thomas, 09.11.2026). Until CHY-150 it ended as `complete` — the budget
       used up looked like the work done.

    `excluding` names edges that do not belong to this firing at all: a gate's
    escalation edges when it releases (ADR-050). Left in, a gate whose only
    outgoing edge is its escalation would fail on every approval — no guard
    held, no default — when on approval it is simply the end.
    """
    outgoing = [e for e in graph.successors(node_id) if e.id not in excluding]
    if not outgoing:
        return Firing()

    exhausted, open_edges = [], []
    for edge in outgoing:
        limit = edge.loop_limit
        if limit is not None and state.traversals.get(edge.id, 0) >= limit:
            exhausted.append(edge)
        else:
            open_edges.append(edge)

    # Three kinds of outgoing edge, and they answer to different rules.
    #
    #   plain        no guard, no default label — a fork. Always taken.
    #   conditional  guarded — taken when the guard held.
    #   default      labelled `default` — taken only when no guard held.
    #
    # The default has to be last, or an XOR branch would fire both the matching
    # arm and the fall-through. The first draft of this function treated a
    # default like any unguarded edge and did exactly that: it defined the
    # constant and then never used it.
    plain = [e for e in open_edges if not e.has_condition and e.label != DEFAULT_LABEL]
    guarded = [e for e in open_edges if e.has_condition]
    defaults = [e for e in open_edges if not e.has_condition and e.label == DEFAULT_LABEL]

    matched = [e for e in guarded if conditions.get(e.id, False)]
    taken = [*plain, *matched]
    # An exhausted guard is a guard that did not hold (CHY-150). Without it in
    # this test the default stayed shut whenever the loop's own guard was the
    # only one, and a run that never met its criterion ended without a review.
    guards_existed = bool(guarded) or any(e.has_condition for e in exhausted)
    if guards_existed and not matched:
        taken.extend(defaults)

    spent = tuple(e.id for e in exhausted)
    if taken:
        return Firing(taken=tuple(e.id for e in taken), exhausted=spent)

    if exhausted:
        if _nothing_else_under_way(graph, state, node_id):
            return Firing(exhausted=spent, failure=_ran_out(graph, node_id, exhausted))
        # Something else is still under way: this branch has run its course,
        # the run has not.
        return Firing(exhausted=spent)

    return Firing(failure=(
        f"{graph.ref(node_id)} has outgoing connections but none of them can be "
        f"taken: no condition matched and there is no default edge. A branch "
        f"that leads nowhere ends the run rather than looking finished."
    ))


def _nothing_else_under_way(graph: Graph, state: RunState, node_id: UUID) -> bool:
    """Whether the run stops once `node_id` has finished taking nothing.

    The node itself is in flight while its completion is recorded, so it does
    not count; anything else running, or ready to run, does.
    """
    if state.in_flight - {node_id}:
        return False
    return not any(step.node_id != node_id for step in ready(graph, state))


def _ran_out(graph: Graph, node_id: UUID, exhausted: list[Edge]) -> str:
    """The reason a run ends on a loop that has used up its limit."""
    edges = ", ".join(f"{graph.ref(e.source)} → {graph.ref(e.target)} "
                      f"(loop_limit of {e.loop_limit})" for e in exhausted)
    return (f"{graph.ref(node_id)} has used up its loop: {edges}, and nothing else in "
            f"the run can go on. A loop that only ran out of passes has not met its "
            f"criterion; the run ends as failed rather than looking finished.")


def escalate(graph: Graph, state: RunState, node_id: UUID, *,
             escalation_edges: frozenset[UUID]) -> Firing:
    """Where a gate goes when it escalates instead of releasing (ADR-050).

    Only its escalation edges are taken, never the plain ones: those are the way
    on after a release. Which edges escalate is decided by the caller from their
    guards — this module reads no condition, as with `fire`. Loop limits apply
    as there.

    No escalation edge to take is a failure, not a quiet end: activation refuses
    a gate that can escalate without one (`escalation_without_edge`), so
    reaching it means the definition changed underneath a run.
    """
    edges = [e for e in graph.successors(node_id) if e.id in escalation_edges]
    open_edges = [e for e in edges
                  if e.loop_limit is None or state.traversals.get(e.id, 0) < e.loop_limit]
    if open_edges:
        return Firing(taken=tuple(e.id for e in open_edges),
                      exhausted=tuple(e.id for e in edges if e not in open_edges))
    return Firing(
        exhausted=tuple(e.id for e in edges),
        failure=(f"{graph.ref(node_id)} escalates, but it has no escalation edge it can "
                 f"take. Escalation goes along an edge to a further gate (ADR-050)."),
    )


def plan(graph: Graph, state: RunState) -> Plan:
    """What to do next with this run.

    A `human_review` among the ready nodes stops the **run**, not just its own
    branch, and the other ready nodes wait. Two reasons: the status lives on
    the run row and not per node, and a human deciding one branch may well
    change what the others should do. Letting parallel work race past a gate
    would make the gate advisory, which ADR-004 does not allow.

    Nothing ready and nothing in flight means the run is over. That is the only
    definition of "over" available — there is no end node in this model, and
    inventing one would be a second way to finish.
    """
    steps = ready(graph, state)
    if not steps:
        if state.in_flight:
            return Plan(Outcome.RUN)
        return Plan(Outcome.COMPLETE)

    gates = [s for s in steps if graph.node(s.node_id).node_type == "human_review"]
    if gates:
        node_ref = graph.ref(gates[0].node_id)
        return Plan(
            Outcome.AWAIT_REVIEW,
            steps=tuple(gates[:1]),
            reason=f"Waiting for a decision at {node_ref}.",
        )

    return Plan(Outcome.RUN, steps=steps)


# ── rebuilding the state ──────────────────────────────────────────────────────

def rebuild_state(executions, traversal_rows) -> RunState:
    """The run's position, reconstructed from what the database remembers.

    Rebuilt on every advance rather than carried in memory. That is not
    caution, it is the requirement: a run whose position lives in a process
    cannot be resumed by another instance and is lost entirely on a restart
    (ADR-028 point 2).

    Both arguments are sequences of mappings, not rows of a particular driver.
    Keeping the shape that loose is what lets this be tested against
    dictionaries — the reconstruction is the part most likely to be subtly
    wrong, and needing a database to find out would mean finding out late.

    **Tokens are a difference, not a stored value:**

        tokens on edge E  =  times E was traversed  -  times E was consumed

    Storing them directly would be a second record of where the run stands, and
    the position of a run is exactly the thing that must have one.

    `max(0, …)` is load bearing, and not defensive. `first_wins` consumes
    incoming edges that were never traversed — that is how it discards the
    stragglers — so consumption legitimately exceeds traversal. When the late
    branch finally arrives, its traversal is cancelled by the consumption
    already on record, and the gateway does not fire twice. Clamping is what
    makes that arithmetic instead of a special case.
    """
    completed: dict[UUID, int] = {}
    in_flight: set[UUID] = set()
    consumed: dict[UUID, int] = {}

    for row in executions:
        node_id = row["node_id"]
        if row["status"] == "running":
            in_flight.add(node_id)
        elif row["status"] == "complete":
            completed[node_id] = completed.get(node_id, 0) + 1
        # `failed` and `skipped` are neither: a failed node has not completed,
        # and counting it would let the run walk on as though it had.
        for edge_id in row["consumed_edges"] or ():
            consumed[edge_id] = consumed.get(edge_id, 0) + 1

    traversals = {r["connection_id"]: r["traversals"] for r in traversal_rows}
    tokens = {
        edge_id: max(0, count - consumed.get(edge_id, 0))
        for edge_id, count in traversals.items()
    }

    return RunState(
        tokens=tokens,
        completed=completed,
        traversals=traversals,
        in_flight=frozenset(in_flight),
    )
