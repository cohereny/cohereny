"""
contexts/orchestration/graph.py

Graph analysis for workflow definitions (CHY-82, ADR-026, ADR-028).

Two callers, one implementation. The validator answers "may this workflow be
activated"; the runner of CHY-127 walks the same edges to execute it. ADR-028
says so explicitly — *"teilt sich die Graphanalyse mit dem Validator"* — and
two traversals that disagree about what a back edge is would disagree about
which loop limit applies.

The aggregate of ADR-030: it decides, it does not execute. Nothing here opens a
connection, and nothing knows that HTTP exists.

Why these four rules and not a general graph checker
-----------------------------------------------------
Each one prevents a failure that has a name.

1. **The three mandatory nodes.** `context_compose`, `llm_call`, `human_review`
   are the core of ADR-004 on graph level: a workflow that calls a model
   without composing context has no scope, and one without a review has no
   human in the loop. Not a style rule — the property the product claims.

2. **Every back edge carries a limit.** ADR-026. Without it a cycle runs until
   something else stops it, and "something else" in an LLM pipeline means a
   bill.

3. **No model output reaches the end without a review.** Stated in the ticket
   as "human_review liegt nach dem letzten llm_call". Checked in the stronger
   form it is meant to protect: from every `llm_call`, every path to a node
   with no successor passes a `human_review`. The weaker reading — a review
   exists somewhere after the last call — is satisfied by a graph that branches
   past the gate, and that branch is exactly the one that would matter.

4. **The sub-workflow call graph is acyclic.** Cycles *inside* a workflow are
   allowed and bounded by loop_limit. A cycle between workflows is not bounded
   by anything: each call starts a fresh run with fresh counters.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from contexts.orchestration.errors import UnknownNodeInEdge
from uuid import UUID

MANDATORY_NODE_TYPES: tuple[str, ...] = ("context_compose", "llm_call", "human_review")


@dataclass(frozen=True)
class Node:
    """A node, reduced to what graph analysis needs.

    `join_policy` is only meaningful on a join_gateway and defaults to None, so
    the validator's tests — written before the runner existed — keep building
    nodes without it. The runner needs it here rather than in a second
    structure of its own: ADR-028 has both walking the same graph, and two
    representations of one node is where they start to disagree.
    """

    id: UUID
    node_ref: str
    node_type: str
    join_policy: str | None = None


@dataclass(frozen=True)
class Edge:
    """An edge, reduced to what graph analysis and execution need.

    `has_condition` was a bool here until CHY-130, on the grounds that the
    state machine must not know how a guard is written. It still must not —
    and it still does not: `runner.py` reads only the derived flag. But the
    condition has to reach the evaluator somehow, and threading it in a second
    structure alongside the graph would mean two things that must agree about
    which edge is which.

    One field, and the flag derived from it. A bool that can disagree with the
    condition it describes is a bug waiting for a refactor.
    """

    id: UUID
    source: UUID
    target: UUID
    loop_limit: int | None = None
    condition: Mapping[str, object] | None = None
    label: str | None = None

    @property
    def has_condition(self) -> bool:
        return self.condition is not None


@dataclass(frozen=True)
class Violation:
    """One reason a workflow may not be activated.

    `rule` is stable and machine-readable; `message` is for a person. Both,
    because a client needs to branch on the first and show the second, and a
    message parsed for its wording is a contract nobody declared.
    """

    rule: str
    message: str
    node_ref: str | None = None
    connection_id: UUID | None = None


@dataclass
class Graph:
    """Nodes and edges, indexed for traversal."""

    nodes: list[Node]
    edges: list[Edge]
    _by_id: dict[UUID, Node] = field(init=False, repr=False)
    _out: dict[UUID, list[Edge]] = field(init=False, repr=False)
    _in: dict[UUID, list[Edge]] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._by_id = {n.id: n for n in self.nodes}
        self._out = {n.id: [] for n in self.nodes}
        self._in = {n.id: [] for n in self.nodes}
        for e in self.edges:
            # An edge to a node of another workflow is a data error, not a
            # graph shape. Skipping it silently would make the traversal answer
            # a question about a different graph than the one stored.
            if e.source not in self._out:
                raise UnknownNodeInEdge(e.id, e.source)
            if e.target not in self._by_id:
                raise UnknownNodeInEdge(e.id, e.target)
            self._out[e.source].append(e)
            self._in[e.target].append(e)

    def successors(self, node_id: UUID) -> list[Edge]:
        return self._out[node_id]

    def predecessors(self, node_id: UUID) -> list[Edge]:
        """Edges pointing at this node — what a join gateway waits on."""
        return self._in[node_id]

    def node(self, node_id: UUID) -> Node:
        return self._by_id[node_id]

    def ref(self, node_id: UUID) -> str:
        return self._by_id[node_id].node_ref

    def type_of(self, node_id: UUID) -> str:
        return self._by_id[node_id].node_type

    def of_type(self, node_type: str) -> list[Node]:
        return [n for n in self.nodes if n.node_type == node_type]

    def entry_points(self) -> list[Node]:
        """Nodes nothing points at.

        A graph where everything has an incoming edge has no entry — which is
        possible, and is itself a finding rather than a crash.
        """
        targeted = {e.target for e in self.edges}
        return [n for n in self.nodes if n.id not in targeted]


# ── back edges ────────────────────────────────────────────────────────────────

def back_edges(graph: Graph) -> set[UUID]:
    """Edges that close a cycle — the ones ADR-026 requires a limit on.

    Depth-first with an explicit stack of the nodes currently on the path. An
    edge whose target is on that stack points backwards; anything else, however
    far it jumps, does not.

    Iterative and not recursive: a workflow is user-supplied data, and a deep
    chain would otherwise turn a validation request into a RecursionError —
    a 500 for a graph the caller could have been told about.

    Starting from the entry points and then from anything still unvisited: a
    cycle with no way in is unreachable, but it is still a cycle in the stored
    graph, and refusing to look at it would let it through.
    """
    found: set[UUID] = set()
    finished: set[UUID] = set()

    starts = [n.id for n in graph.entry_points()] + [n.id for n in graph.nodes]
    for start in starts:
        if start in finished:
            continue
        # (node, iterator position) — the path is the stack itself.
        stack: list[tuple[UUID, int]] = [(start, 0)]
        on_path: set[UUID] = {start}
        while stack:
            node_id, index = stack[-1]
            out = graph.successors(node_id)
            if index >= len(out):
                stack.pop()
                on_path.discard(node_id)
                finished.add(node_id)
                continue
            stack[-1] = (node_id, index + 1)
            edge = out[index]
            if edge.target in on_path:
                found.add(edge.id)
            elif edge.target not in finished:
                stack.append((edge.target, 0))
                on_path.add(edge.target)
    return found


# ── the four rules ────────────────────────────────────────────────────────────

def _check_mandatory_nodes(graph: Graph) -> list[Violation]:
    return [
        Violation(
            rule="mandatory_node_missing",
            message=(
                f"A workflow needs a {node_type} node. The three mandatory types "
                f"are what makes human oversight a property of the graph rather "
                f"than of the caller (ADR-004)."
            ),
        )
        for node_type in MANDATORY_NODE_TYPES
        if not graph.of_type(node_type)
    ]


def _check_loop_limits(graph: Graph) -> list[Violation]:
    limits = {e.id: e.loop_limit for e in graph.edges}
    return [
        Violation(
            rule="back_edge_without_loop_limit",
            message=(
                "This edge closes a cycle and carries no loop_limit. An "
                "unbounded loop in an LLM pipeline stops when something else "
                "stops it (ADR-026)."
            ),
            connection_id=edge_id,
        )
        for edge_id in sorted(back_edges(graph), key=str)
        if limits[edge_id] is None
    ]


def _check_review_after_calls(graph: Graph) -> list[Violation]:
    """No path from an llm_call reaches an end without passing a human_review.

    Walks forward from each `llm_call` and stops descending at a
    `human_review` — that path is covered. A node with no successors reached
    before then is model output leaving unreviewed.

    Cycles are handled by the visited set: a path that loops back has already
    been walked and cannot reach anything new.
    """
    violations = []
    for call in graph.of_type("llm_call"):
        seen: set[UUID] = set()
        stack = [call.id]
        while stack:
            node_id = stack.pop()
            if node_id in seen:
                continue
            seen.add(node_id)
            out = graph.successors(node_id)
            if not out and node_id != call.id:
                violations.append(Violation(
                    rule="unreviewed_output",
                    message=(
                        f"Output of {call.node_ref} can reach {graph.ref(node_id)}, "
                        f"which ends the workflow, without passing a human_review "
                        f"(ADR-004)."
                    ),
                    node_ref=call.node_ref,
                ))
                continue
            for edge in out:
                if graph.type_of(edge.target) != "human_review":
                    stack.append(edge.target)
        # An llm_call with no successors at all: it *is* the end.
        if not graph.successors(call.id):
            violations.append(Violation(
                rule="unreviewed_output",
                message=(
                    f"{call.node_ref} ends the workflow. Model output must pass a "
                    f"human_review before a workflow finishes (ADR-004)."
                ),
                node_ref=call.node_ref,
            ))
    return violations


def validate(graph: Graph) -> list[Violation]:
    """Every reason this workflow may not be activated, not just the first.

    All rules run. Returning on the first violation would make fixing a graph a
    sequence of round trips, and the three rules are independent — nothing
    found by one changes what another sees.
    """
    return [
        *_check_mandatory_nodes(graph),
        *_check_loop_limits(graph),
        *_check_review_after_calls(graph),
    ]


# ── rule 4: across workflows ──────────────────────────────────────────────────

def sub_workflow_cycles(calls: dict[UUID, set[UUID]], root: UUID) -> list[list[UUID]]:
    """Cycles in the call graph, reachable from `root`.

    `calls` maps a workflow to the workflows its sub_workflow nodes target. It
    comes from the store, because it spans workflows and this module reads
    nothing.

    Separate from `validate` for that reason: the other three rules need only
    the graph in hand, and mixing a rule that needs a second query into a pure
    function is how a pure function stops being one.

    Cycles *inside* a workflow are allowed and bounded by loop_limit. A cycle
    *between* workflows is bounded by nothing — each call starts a fresh run
    with fresh counters, so A→B→A recurses until the process dies.
    """
    cycles: list[list[UUID]] = []
    path: list[UUID] = []
    on_path: set[UUID] = set()
    visited: set[UUID] = set()

    def walk(node: UUID) -> None:
        if node in on_path:
            cycles.append([*path[path.index(node):], node])
            return
        if node in visited:
            return
        visited.add(node)
        path.append(node)
        on_path.add(node)
        for target in sorted(calls.get(node, ()), key=str):
            walk(target)
        path.pop()
        on_path.discard(node)

    walk(root)
    return cycles
