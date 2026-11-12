"""
tests/test_workflow_graph.py

Graph analysis for workflow definitions (CHY-82, ADR-026, ADR-028).

Two things are worth saying about how this file is written.

**The counter-probes carry as much weight as the rules.** A validator that
refuses everything satisfies every "must be rejected" test and is useless — it
would make activation impossible and nobody would find out until the first
workflow. Every rule here therefore has a case that must *pass*.

**Cycles are allowed.** ADR-026 corrected the model from DAG to directed graph
precisely because the study pilot has two back loops. A test that asserted
"no cycles" would encode the mistake the ADR removed.
"""
from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from contexts.orchestration.graph import (
    Edge,
    Graph,
    Node,
    UnknownNodeInEdge,
    back_edges,
    sub_workflow_cycles,
    validate,
)


def _n(ref: str, node_type: str) -> Node:
    return Node(id=uuid4(), node_ref=ref, node_type=node_type)


def _e(source: Node, target: Node, loop_limit: int | None = None) -> Edge:
    return Edge(id=uuid4(), source=source.id, target=target.id, loop_limit=loop_limit)


def _minimal() -> tuple[Graph, dict[str, Node]]:
    """context_compose → llm_call → human_review. The smallest legal workflow."""
    ctx = _n("compose", "context_compose")
    call = _n("summarize", "llm_call")
    review = _n("approve", "human_review")
    graph = Graph([ctx, call, review], [_e(ctx, call), _e(call, review)])
    return graph, {"ctx": ctx, "call": call, "review": review}


def _rules(graph: Graph) -> set[str]:
    return {v.rule for v in validate(graph)}


# ── the shape a graph must have ───────────────────────────────────────────────

def test_the_smallest_legal_workflow_passes():
    """The counter-probe the whole file rests on.

    Without it every rule below could be satisfied by a validator that refuses
    everything — and refusing everything is indistinguishable from working,
    right up to the moment somebody tries to activate a workflow.
    """
    graph, _ = _minimal()
    assert validate(graph) == []


@pytest.mark.parametrize("missing", ["context_compose", "llm_call", "human_review"])
def test_each_mandatory_node_is_required(missing: str):
    graph, nodes = _minimal()
    kept = [n for n in graph.nodes if n.node_type != missing]
    edges = [e for e in graph.edges
             if e.source in {n.id for n in kept} and e.target in {n.id for n in kept}]
    assert "mandatory_node_missing" in _rules(Graph(kept, edges))


def test_an_edge_to_a_foreign_node_is_refused_at_construction():
    """A connection row pointing outside its workflow is a data error.

    Skipping it would let the traversal answer a question about a different
    graph than the one stored — and answer it confidently.
    """
    graph, nodes = _minimal()
    stray = _e(nodes["review"], _n("elsewhere", "llm_call"))
    with pytest.raises(UnknownNodeInEdge):
        Graph(graph.nodes, [*graph.edges, stray])


# ── cycles and their limits ───────────────────────────────────────────────────

class TestBackEdges:
    def test_a_straight_line_has_none(self):
        graph, _ = _minimal()
        assert back_edges(graph) == set()

    def test_the_edge_that_closes_a_loop_is_found(self):
        graph, n = _minimal()
        loop = _e(n["call"], n["ctx"], loop_limit=3)
        found = back_edges(Graph(graph.nodes, [*graph.edges, loop]))
        assert found == {loop.id}

    def test_a_forward_jump_is_not_a_back_edge(self):
        """The distinction the whole rule turns on.

        An edge that skips ahead looks like a shortcut and is not a cycle. A
        check that flagged it would demand a loop_limit on a straight path, and
        the first person to hit that would add one to make the error go away —
        which is worse than no check.
        """
        graph, n = _minimal()
        skip = _e(n["ctx"], n["review"])
        assert back_edges(Graph(graph.nodes, [*graph.edges, skip])) == set()

    def test_a_self_loop_counts(self):
        graph, n = _minimal()
        loop = _e(n["call"], n["call"], loop_limit=2)
        assert loop.id in back_edges(Graph(graph.nodes, [*graph.edges, loop]))

    def test_two_independent_loops_are_both_found(self):
        """The reason ADR-026 counts per edge and not per run.

        A global counter cannot tell these apart, which is reason 1 of ADR-028
        for not using LangGraph's recursion_limit.
        """
        ctx = _n("compose", "context_compose")
        a = _n("extract", "llm_call")
        b = _n("verify", "llm_call")
        review = _n("approve", "human_review")
        loop_a = _e(a, ctx, loop_limit=3)
        loop_b = _e(b, a, loop_limit=2)
        graph = Graph(
            [ctx, a, b, review],
            [_e(ctx, a), _e(a, b), _e(b, review), loop_a, loop_b],
        )
        assert back_edges(graph) == {loop_a.id, loop_b.id}

    def test_a_cycle_nothing_points_into_is_still_found(self):
        """An unreachable cycle is unreachable today.

        It is still a cycle in the stored graph, and the next edge somebody
        adds may point into it. Starting only from entry points would have
        walked straight past it.
        """
        ctx = _n("compose", "context_compose")
        call = _n("summarize", "llm_call")
        review = _n("approve", "human_review")
        x = _n("orphan_a", "tool_call")
        y = _n("orphan_b", "tool_call")
        loop = _e(y, x)
        graph = Graph(
            [ctx, call, review, x, y],
            [_e(ctx, call), _e(call, review), _e(x, y), loop],
        )
        assert loop.id in back_edges(graph)


class TestLoopLimit:
    def test_a_back_edge_without_a_limit_is_refused(self):
        graph, n = _minimal()
        loop = _e(n["call"], n["ctx"])
        assert "back_edge_without_loop_limit" in _rules(Graph(graph.nodes, [*graph.edges, loop]))

    def test_a_back_edge_with_a_limit_passes(self):
        """Counter-probe: cycles are allowed. ADR-026 corrected the model from
        DAG to directed graph exactly because the study pilot loops twice."""
        graph, n = _minimal()
        loop = _e(n["call"], n["ctx"], loop_limit=3)
        assert validate(Graph(graph.nodes, [*graph.edges, loop])) == []

    def test_a_forward_edge_needs_no_limit(self):
        graph, _ = _minimal()
        assert validate(graph) == []


# ── nothing leaves unreviewed ─────────────────────────────────────────────────

class TestReviewCoverage:
    def test_a_branch_that_bypasses_the_review_is_refused(self):
        """The case the weaker reading of the rule misses.

        "human_review comes after the last llm_call" is satisfied here: the
        review does follow the call. But a second branch leaves the same call
        and reaches the end without one, and that branch is the whole point.
        """
        graph, n = _minimal()
        shortcut = _n("export", "tool_call")
        graph = Graph([*graph.nodes, shortcut], [*graph.edges, _e(n["call"], shortcut)])
        assert "unreviewed_output" in _rules(graph)

    def test_a_call_that_ends_the_workflow_is_refused(self):
        ctx = _n("compose", "context_compose")
        call = _n("summarize", "llm_call")
        review = _n("approve", "human_review")
        # review is present, but nothing routes the call into it
        graph = Graph([ctx, call, review], [_e(ctx, call)])
        assert "unreviewed_output" in _rules(graph)

    def test_work_after_the_review_is_fine(self):
        """Counter-probe: the rule is about reaching the end unreviewed, not
        about the review being last. A workflow that formats and delivers what
        a human approved is the normal case."""
        graph, n = _minimal()
        deliver = _n("deliver", "tool_call")
        graph = Graph([*graph.nodes, deliver], [*graph.edges, _e(n["review"], deliver)])
        assert validate(graph) == []

    def test_every_branch_reaching_its_own_review_is_fine(self):
        ctx = _n("compose", "context_compose")
        call = _n("summarize", "llm_call")
        fork = _n("split", "fork_gateway")
        r1 = _n("approve_a", "human_review")
        r2 = _n("approve_b", "human_review")
        graph = Graph(
            [ctx, call, fork, r1, r2],
            [_e(ctx, call), _e(call, fork), _e(fork, r1), _e(fork, r2)],
        )
        assert validate(graph) == []

    def test_a_loop_before_the_review_does_not_confuse_the_walk(self):
        """A cycle on the way to the gate must not read as an unreviewed end.

        Without the visited set the walk would go round forever; with a wrong
        one it would stop early and miss a real bypass.
        """
        graph, n = _minimal()
        graph = Graph(graph.nodes, [*graph.edges, _e(n["call"], n["ctx"], loop_limit=2)])
        assert validate(graph) == []


# ── across workflows ──────────────────────────────────────────────────────────

class TestSubWorkflowCycles:
    A, B, C = UUID(int=1), UUID(int=2), UUID(int=3)

    def test_a_chain_is_fine(self):
        assert sub_workflow_cycles({self.A: {self.B}, self.B: {self.C}}, self.A) == []

    def test_a_workflow_calling_itself_is_a_cycle(self):
        assert sub_workflow_cycles({self.A: {self.A}}, self.A)

    def test_a_two_step_cycle_is_found(self):
        cycles = sub_workflow_cycles({self.A: {self.B}, self.B: {self.A}}, self.A)
        assert cycles and self.A in cycles[0] and self.B in cycles[0]

    def test_a_diamond_is_not_a_cycle(self):
        """Counter-probe. Two paths to the same workflow is reuse, not
        recursion — a checker that confused them would forbid the obvious way
        to share a sub-workflow."""
        assert sub_workflow_cycles(
            {self.A: {self.B, self.C}, self.B: {UUID(int=4)}, self.C: {UUID(int=4)}},
            self.A,
        ) == []

    def test_a_workflow_that_calls_nothing_is_fine(self):
        assert sub_workflow_cycles({}, self.A) == []
