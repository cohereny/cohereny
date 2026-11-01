"""
tests/test_workflow_runner.py

The workflow state machine (CHY-127, ADR-028, ADR-026).

ADR-028 makes one requirement of this file explicitly: **join policies and
resumption get tests before they get callers.** That order is kept literally —
nothing in `service.py` calls this module yet.

The reason is in the ADR too: the risk of an own runner is not in the first
draft but in the edge cases. Here those are the second lap of a loop, a join
that fires twice, and a branch where nothing matches. All three look fine in a
straight-line workflow.

The counter-probes matter as much as the rules, and one of them is load
bearing: **cycles are allowed.** ADR-026 corrected the model from DAG to
directed graph precisely because the study pilot loops twice. A test suite that
only proved things get refused would pass against a runner that never advances.
"""
from __future__ import annotations

from uuid import uuid4

from contexts.orchestration.graph import Edge, Graph, Node
from contexts.orchestration.runner import (
    FIRST_WINS,
    WAIT_ALL,
    WAIT_ANY,
    Outcome,
    RunState,
    consume,
    fire,
    plan,
    ready,
)


def _n(ref: str, node_type: str = "tool_call", join_policy: str | None = None) -> Node:
    return Node(id=uuid4(), node_ref=ref, node_type=node_type, join_policy=join_policy)


def _e(a: Node, b: Node, **kw) -> Edge:
    return Edge(id=uuid4(), source=a.id, target=b.id, **kw)


def _refs(graph: Graph, steps) -> set[str]:
    return {graph.ref(s.node_id) for s in steps}


# ── starting ──────────────────────────────────────────────────────────────────

class TestEntry:
    def test_a_node_with_no_incoming_edges_starts(self):
        a, b = _n("a"), _n("b")
        graph = Graph([a, b], [_e(a, b)])
        assert _refs(graph, ready(graph, RunState())) == {"a"}

    def test_it_does_not_start_twice(self):
        """Without this an entry node would be handed out on every advance, and
        a run that is advanced twice would run its first node twice."""
        a, b = _n("a"), _n("b")
        graph = Graph([a, b], [_e(a, b)])
        state = RunState(completed={a.id: 1})
        assert _refs(graph, ready(graph, state)) == set()

    def test_a_node_in_flight_is_not_handed_out_again(self):
        """Two advances in quick succession — an API call and a resumption —
        would otherwise both see the same armed edge and start the node twice.
        """
        a, b = _n("a"), _n("b")
        edge = _e(a, b)
        graph = Graph([a, b], [edge])
        state = RunState(tokens={edge.id: 1}, completed={a.id: 1}, in_flight=frozenset({b.id}))
        assert ready(graph, state) == ()


# ── tokens carry the run ──────────────────────────────────────────────────────

class TestTokens:
    def test_a_token_makes_the_next_node_ready(self):
        a, b = _n("a"), _n("b")
        edge = _e(a, b)
        graph = Graph([a, b], [edge])
        state = RunState(tokens={edge.id: 1}, completed={a.id: 1})
        assert _refs(graph, ready(graph, state)) == {"b"}

    def test_without_a_token_nothing_is_ready(self):
        """The counter-probe to the whole model: readiness comes from tokens,
        not from predecessors being done. A completed predecessor with no token
        is a branch that was not taken."""
        a, b = _n("a"), _n("b")
        graph = Graph([a, b], [_e(a, b)])
        state = RunState(completed={a.id: 1})
        assert ready(graph, state) == ()

    def test_the_second_lap_of_a_loop_is_a_new_iteration(self):
        """Where "a node is ready when its predecessors are done" falls apart.

        On the second lap every predecessor is still done, so that model makes
        every node ready forever. With tokens the lap number falls out of how
        often the node has completed.
        """
        a, b = _n("a"), _n("b")
        forward, back = _e(a, b), _e(b, a, loop_limit=3)
        graph = Graph([a, b], [forward, back])
        state = RunState(tokens={back.id: 1}, completed={a.id: 1, b.id: 1})
        steps = ready(graph, state)
        assert _refs(graph, steps) == {"a"}
        assert steps[0].iteration == 2


# ── join policies ─────────────────────────────────────────────────────────────

class TestJoinPolicies:
    def _fork_join(self, policy: str):
        start = _n("start")
        left, right = _n("left"), _n("right")
        join = _n("join", "join_gateway", join_policy=policy)
        e_l, e_r = _e(start, left), _e(start, right)
        j_l, j_r = _e(left, join), _e(right, join)
        return Graph([start, left, right, join], [e_l, e_r, j_l, j_r]), j_l, j_r, join

    def test_wait_all_waits(self):
        graph, j_l, _, _ = self._fork_join(WAIT_ALL)
        state = RunState(tokens={j_l.id: 1})
        assert "join" not in _refs(graph, ready(graph, state))

    def test_wait_all_fires_when_both_arrive(self):
        """Counter-probe: a gateway that never fires satisfies the test above
        and deadlocks every parallel workflow."""
        graph, j_l, j_r, _ = self._fork_join(WAIT_ALL)
        state = RunState(tokens={j_l.id: 1, j_r.id: 1})
        assert "join" in _refs(graph, ready(graph, state))

    def test_wait_any_fires_on_the_first(self):
        graph, j_l, _, _ = self._fork_join(WAIT_ANY)
        state = RunState(tokens={j_l.id: 1})
        assert "join" in _refs(graph, ready(graph, state))

    def test_wait_any_leaves_the_straggler_armed(self):
        """"Any" means each arrival counts. A loop that feeds the gateway twice
        must fire it twice, so the second token has to survive the first firing.
        """
        graph, j_l, j_r, join = self._fork_join(WAIT_ANY)
        state = RunState(tokens={j_l.id: 1, j_r.id: 1})
        assert set(consume(graph, state, join.id)) == {j_l.id, j_r.id}

        state = RunState(tokens={j_l.id: 1})
        assert set(consume(graph, state, join.id)) == {j_l.id}

    def test_first_wins_discards_the_straggler(self):
        """The entire difference between first_wins and wait_any.

        Both fire on the first arrival. first_wins additionally consumes the
        edges that have *not* arrived, so the late branch finds its token
        already spent and the gateway does not fire a second time.
        """
        graph, j_l, j_r, join = self._fork_join(FIRST_WINS)
        state = RunState(tokens={j_l.id: 1})
        assert set(consume(graph, state, join.id)) == {j_l.id, j_r.id}

    def test_a_plain_node_consumes_only_what_armed_it(self):
        a, b, c = _n("a"), _n("b"), _n("c")
        e_a, e_b = _e(a, c), _e(b, c)
        graph = Graph([a, b, c], [e_a, e_b])
        state = RunState(tokens={e_a.id: 1})
        assert consume(graph, state, c.id) == (e_a.id,)


# ── where the run goes next ───────────────────────────────────────────────────

class TestFire:
    def test_an_unguarded_edge_is_always_taken(self):
        a, b = _n("a"), _n("b")
        edge = _e(a, b)
        graph = Graph([a, b], [edge])
        assert fire(graph, RunState(), a.id, conditions={}).taken == (edge.id,)

    def test_a_fork_takes_every_unguarded_edge(self):
        a, b, c = _n("a"), _n("b"), _n("c")
        e_b, e_c = _e(a, b), _e(a, c)
        graph = Graph([a, b, c], [e_b, e_c])
        assert set(fire(graph, RunState(), a.id, conditions={}).taken) == {e_b.id, e_c.id}

    def test_an_xor_takes_only_the_arm_that_matched(self):
        a, b, c = _n("a"), _n("b"), _n("c")
        e_b = _e(a, b, condition={"field": "risk", "op": "eq", "value": "high"})
        e_c = _e(a, c, condition={"field": "risk", "op": "eq", "value": "high"})
        graph = Graph([a, b, c], [e_b, e_c])
        result = fire(graph, RunState(), a.id, conditions={e_b.id: True, e_c.id: False})
        assert result.taken == (e_b.id,)

    def test_the_default_arm_runs_when_nothing_matched(self):
        a, b, c = _n("a"), _n("b"), _n("c")
        guarded = _e(a, b, condition={"field": "risk", "op": "eq", "value": "high"})
        fallback = _e(a, c, label="default")
        graph = Graph([a, b, c], [guarded, fallback])
        result = fire(graph, RunState(), a.id, conditions={guarded.id: False})
        assert result.taken == (fallback.id,)

    def test_the_default_arm_stays_out_when_something_matched(self):
        """The bug the first draft of `fire` had: it declared a DEFAULT_LABEL
        constant and never used it, so a default edge behaved like any other
        unguarded edge and an XOR fired both arms."""
        a, b, c = _n("a"), _n("b"), _n("c")
        guarded = _e(a, b, condition={"field": "risk", "op": "eq", "value": "high"})
        fallback = _e(a, c, label="default")
        graph = Graph([a, b, c], [guarded, fallback])
        result = fire(graph, RunState(), a.id, conditions={guarded.id: True})
        assert result.taken == (guarded.id,)

    def test_a_branch_with_nowhere_to_go_fails_the_run(self):
        """arc42 §5.6: quietly going nowhere is not an option — it is
        indistinguishable from having finished."""
        a, b = _n("a"), _n("b")
        guarded = _e(a, b, condition={"field": "risk", "op": "eq", "value": "high"})
        graph = Graph([a, b], [guarded])
        result = fire(graph, RunState(), a.id, conditions={guarded.id: False})
        assert result.failure and "default" in result.failure

    def test_a_terminal_node_is_not_a_failure(self):
        """Counter-probe to the rule above: a node with no outgoing edges has
        nowhere to go by design, and that is how a workflow ends."""
        a = _n("a")
        graph = Graph([a], [])
        assert fire(graph, RunState(), a.id, conditions={}) .failure is None


# ── loop limits ───────────────────────────────────────────────────────────────

class TestLoopLimit:
    def test_a_loop_edge_is_refused_once_its_limit_is_used_up(self):
        a, b = _n("a"), _n("b")
        back = _e(b, a, loop_limit=2)
        graph = Graph([a, b], [_e(a, b), back])
        result = fire(graph, RunState(traversals={back.id: 2}), b.id, conditions={})
        assert result.taken == ()
        assert result.exhausted == (back.id,)

    def test_it_is_still_open_below_the_limit(self):
        """Counter-probe: cycles are allowed. A check that refused the loop
        outright would pass the test above and make ADR-026 a dead letter."""
        a, b = _n("a"), _n("b")
        back = _e(b, a, loop_limit=2)
        graph = Graph([a, b], [_e(a, b), back])
        assert fire(graph, RunState(traversals={back.id: 1}), b.id, conditions={}).taken == (back.id,)

    def test_two_loops_do_not_limit_one_another(self):
        """Reason 1 of ADR-028 for not using LangGraph's global recursion_limit.

        The first loop has used its budget; the second has not, and must still
        be open. A counter on the run cannot express this.
        """
        a, b, c = _n("a"), _n("b"), _n("c")
        back_1 = _e(b, a, loop_limit=1)
        back_2 = _e(c, b, loop_limit=5)
        graph = Graph([a, b, c], [_e(a, b), _e(b, c), back_1, back_2])
        state = RunState(traversals={back_1.id: 1, back_2.id: 1})

        assert fire(graph, state, b.id, conditions={}).exhausted == (back_1.id,)
        assert back_2.id in fire(graph, state, c.id, conditions={}).taken

    def test_an_exhausted_loop_is_not_a_failure(self):
        """A bounded loop ending is how a bounded loop is supposed to end.

        Whether the *run* is over depends on tokens elsewhere, which `plan`
        sees and `fire` does not — so reporting this as a failure here would
        turn every completed loop into an error.
        """
        a, b = _n("a"), _n("b")
        back = _e(b, a, loop_limit=1)
        graph = Graph([a, b], [_e(a, b), back])
        assert fire(graph, RunState(traversals={back.id: 1}), b.id, conditions={}).failure is None


# ── the decision ──────────────────────────────────────────────────────────────

class TestPlan:
    def test_a_fresh_run_starts_at_the_entry(self):
        a, b = _n("a"), _n("b")
        graph = Graph([a, b], [_e(a, b)])
        result = plan(graph, RunState())
        assert result.outcome is Outcome.RUN
        assert _refs(graph, result.steps) == {"a"}

    def test_nothing_ready_and_nothing_running_means_complete(self):
        a = _n("a")
        graph = Graph([a], [])
        assert plan(graph, RunState(completed={a.id: 1})).outcome is Outcome.COMPLETE

    def test_nothing_ready_but_something_in_flight_keeps_running(self):
        """The difference between "finished" and "waiting for a node".

        Without it a parallel branch still executing would read as a completed
        run, and the run would be closed while work was outstanding.
        """
        a, b = _n("a"), _n("b")
        graph = Graph([a, b], [_e(a, b)])
        state = RunState(completed={a.id: 1}, in_flight=frozenset({b.id}))
        assert plan(graph, state).outcome is Outcome.RUN

    def test_a_review_gate_stops_the_whole_run(self):
        """Not just its own branch.

        The status lives on the run row, and a human deciding one branch may
        change what the others should do. Letting parallel work race past the
        gate would make it advisory, which ADR-004 does not allow.
        """
        start = _n("start")
        gate = _n("approve", "human_review")
        other = _n("other")
        e_gate, e_other = _e(start, gate), _e(start, other)
        graph = Graph([start, gate, other], [e_gate, e_other])
        state = RunState(tokens={e_gate.id: 1, e_other.id: 1}, completed={start.id: 1})

        result = plan(graph, state)
        assert result.outcome is Outcome.AWAIT_REVIEW
        assert _refs(graph, result.steps) == {"approve"}
        assert "approve" in result.reason

    def test_work_resumes_after_the_gate_is_released(self):
        """Resumption is not a special case — it is the same advance.

        That is the whole point of ADR-028 point 2: the gate is a row, so
        continuing after a decision is the same operation as starting, and
        nothing has to survive in a thread.
        """
        start = _n("start")
        gate = _n("approve", "human_review")
        after = _n("after")
        e_gate, e_after = _e(start, gate), _e(gate, after)
        graph = Graph([start, gate, after], [e_gate, e_after])

        released = RunState(
            tokens={e_after.id: 1}, completed={start.id: 1, gate.id: 1}
        )
        result = plan(graph, released)
        assert result.outcome is Outcome.RUN
        assert _refs(graph, result.steps) == {"after"}
