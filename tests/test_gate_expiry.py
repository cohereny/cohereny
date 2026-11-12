"""
tests/test_gate_expiry.py

A gate nobody decides, without a database (CHY-254, ADR-050).

Three pieces decide what happens, and each is pure:

- **the escalation guard** — which edge is the way to the higher authority.
  Exactly one guard, so the validator and the runner agree on which edge it is
- **the runner** — on a release a gate takes its plain edges and never its
  escalation; on an escalation it takes only that one. Both directions are
  tested, because a runner that mixed them would pass either test alone
- **the activation rules** — a time limit that says what happens, an
  escalation that has somewhere to go, and a target that asks for at least as
  many people as the gate escalating to it

What only PostgreSQL shows — that the run ends `expired`, that the content
goes, that the escalated run waits at the next gate — is in
tests/integration/test_gate_expiry_db.py.
"""
from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest

from contexts.orchestration import conditions
from contexts.orchestration.graph import Edge, Graph, Node
from contexts.orchestration.runner import RunState, escalate, fire
from contexts.orchestration.service import _escalation_edges, _gate_violations


def _n(ref: str, node_type: str = "human_review") -> Node:
    return Node(id=uuid4(), node_ref=ref, node_type=node_type)


def _e(a: Node, b: Node, **kw) -> Edge:
    return Edge(id=uuid4(), source=a.id, target=b.id, **kw)


def _escalation(a: Node, b: Node, **kw) -> Edge:
    return _e(a, b, condition=dict(conditions.ESCALATION), **kw)


# ── the guard ─────────────────────────────────────────────────────────────────

class TestEscalationGuard:
    def test_the_guard_is_a_well_formed_condition(self):
        """It goes through the same validator as every guard: a gate's
        escalation is drawn in the graph, not configured beside it."""
        conditions.check(conditions.ESCALATION)

    def test_it_holds_on_an_escalated_output_only(self):
        assert conditions.evaluate(conditions.ESCALATION, {"decision": "escalated"})
        assert not conditions.evaluate(conditions.ESCALATION, {"decision": "approved"})

    def test_only_the_exact_guard_counts(self):
        """A broader guard would fire on more than an escalation, and the
        validator could no longer say which edge is the one to check."""
        assert conditions.is_escalation({"op": "eq", "field": "decision", "value": "escalated"})
        assert not conditions.is_escalation(None)
        assert not conditions.is_escalation(
            {"any": [dict(conditions.ESCALATION), {"field": "x", "op": "exists", "value": True}]})
        assert not conditions.is_escalation({"op": "ne", "field": "decision", "value": "approved"})

    def test_a_gates_escalation_edges_are_found_by_their_guard(self):
        gate, end, higher = _n("gate"), _n("end", "tool_call"), _n("higher")
        plain, up = _e(gate, end), _escalation(gate, higher)
        graph = Graph([gate, end, higher], [plain, up])
        assert _escalation_edges(graph, gate.id) == frozenset({up.id})


# ── the runner ────────────────────────────────────────────────────────────────

class TestReleaseLeavesTheEscalationOut:
    def test_a_gate_whose_only_edge_escalates_ends_the_run_on_approval(self):
        """The case `excluding` exists for. The gate is the last step; its one
        edge leads to the higher authority, for the timeout."""
        gate, higher = _n("gate"), _n("higher")
        up = _escalation(gate, higher)
        graph = Graph([gate, higher], [up])

        result = fire(graph, RunState(), gate.id, conditions={}, excluding=frozenset({up.id}))

        assert result.taken == () and result.failure is None

    def test_without_excluding_that_approval_would_fail(self):
        """Counter-probe: the guard alone does not keep the edge out. A guarded
        edge that does not hold, with no default beside it, is a branch that
        leads nowhere (arc42 §5.6) — so the approval failed the run."""
        gate, higher = _n("gate"), _n("higher")
        graph = Graph([gate, higher], [_escalation(gate, higher)])

        assert fire(graph, RunState(), gate.id, conditions={}).failure

    def test_with_a_plain_edge_beside_it_the_plain_one_is_taken(self):
        gate, next_step, higher = _n("gate"), _n("send", "tool_call"), _n("higher")
        plain, up = _e(gate, next_step), _escalation(gate, higher)
        graph = Graph([gate, next_step, higher], [plain, up])

        result = fire(graph, RunState(), gate.id, conditions={}, excluding=frozenset({up.id}))

        assert result.taken == (plain.id,)


class TestEscalate:
    def test_it_takes_the_escalation_edge_and_not_the_plain_one(self):
        """Counter-probe to the release: an escalated gate does not also send
        the run on as if it had been approved."""
        gate, next_step, higher = _n("gate"), _n("send", "tool_call"), _n("higher")
        plain, up = _e(gate, next_step), _escalation(gate, higher)
        graph = Graph([gate, next_step, higher], [plain, up])

        result = escalate(graph, RunState(), gate.id, escalation_edges=frozenset({up.id}))

        assert result.taken == (up.id,) and result.failure is None

    def test_without_an_escalation_edge_it_fails_saying_why(self):
        gate, next_step = _n("gate"), _n("send", "tool_call")
        graph = Graph([gate, next_step], [_e(gate, next_step)])

        result = escalate(graph, RunState(), gate.id, escalation_edges=frozenset())

        assert result.taken == ()
        assert "gate escalates" in result.failure and "ADR-050" in result.failure

    def test_an_escalation_loop_that_has_run_out_fails(self):
        """An escalation edge back to an earlier gate is a loop and carries a
        limit. Once it is used up there is nowhere left to escalate to — and a
        gate that timed out may not simply wait again."""
        first, second = _n("first"), _n("second")
        forward = _escalation(first, second)
        back = _escalation(second, first, loop_limit=1)
        graph = Graph([first, second], [forward, back])

        result = escalate(graph, RunState(traversals={back.id: 1}), second.id,
                          escalation_edges=frozenset({back.id}))

        assert result.exhausted == (back.id,) and result.failure

    def test_below_the_limit_it_is_open(self):
        first, second = _n("first"), _n("second")
        back = _escalation(second, first, loop_limit=2)
        graph = Graph([first, second], [_escalation(first, second), back])

        result = escalate(graph, RunState(traversals={back.id: 1}), second.id,
                          escalation_edges=frozenset({back.id}))

        assert result.taken == (back.id,)


# ── activation ────────────────────────────────────────────────────────────────

def _row(node: Node, *, role=None, timeout=None, action=None) -> dict:
    """A row as store.node_details gives it, reduced to what the gate rules read."""
    return {"id": node.id, "review_role": role, "review_timeout": timeout, "on_timeout": action}


def _rules(graph: Graph, rows) -> list[str]:
    return sorted(v.rule for v in _gate_violations(graph, rows))


DAY = timedelta(days=1)


class TestGateActivationRules:
    def test_a_gate_without_time_limit_or_action_is_fine(self):
        """The common case, unchanged: it waits until somebody decides or the
        content period of its scope runs out."""
        gate = _n("gate")
        assert _rules(Graph([gate], []), [_row(gate)]) == []

    def test_a_time_limit_must_say_what_happens(self):
        gate = _n("gate")
        assert _rules(Graph([gate], []), [_row(gate, timeout=DAY)]) == ["timeout_without_action"]

    def test_an_action_without_a_time_limit_never_acts(self):
        gate = _n("gate")
        assert _rules(Graph([gate], []), [_row(gate, action="reject")]) == [
            "action_without_timeout"]

    def test_reject_on_timeout_needs_no_edge(self):
        gate = _n("gate")
        assert _rules(Graph([gate], []), [_row(gate, timeout=DAY, action="reject")]) == []

    def test_escalate_needs_an_escalation_edge(self):
        gate, end = _n("gate"), _n("end", "tool_call")
        graph = Graph([gate, end], [_e(gate, end)])
        assert _rules(graph, [_row(gate, timeout=DAY, action="escalate"), _row(end)]) == [
            "escalation_without_edge"]

    def test_an_escalation_goes_to_a_gate(self):
        gate, step = _n("gate"), _n("send", "tool_call")
        graph = Graph([gate, step], [_escalation(gate, step)])
        assert _rules(graph, [_row(gate, timeout=DAY, action="escalate"), _row(step)]) == [
            "escalation_not_to_gate"]

    def test_the_higher_authority_may_not_ask_for_fewer_people(self):
        gate, higher = _n("gate"), _n("higher")
        graph = Graph([gate, higher], [_escalation(gate, higher)])
        rows = [_row(gate, role="four_eyes", timeout=DAY, action="escalate"),
                _row(higher, role="approver")]
        assert _rules(graph, rows) == ["escalation_to_weaker_gate"]

    @pytest.mark.parametrize(("source", "target"), [
        (None, None), ("approver", "approver"), ("approver", "four_eyes"),
        (None, "four_eyes"), ("four_eyes", "four_eyes"),
    ])
    def test_the_same_or_a_stricter_gate_is_accepted(self, source, target):
        """Counter-probe to the rule above, across the order: a gate without a
        role asks for an approver, so it ranks with `approver`."""
        gate, higher = _n("gate"), _n("higher")
        graph = Graph([gate, higher], [_escalation(gate, higher)])
        rows = [_row(gate, role=source, timeout=DAY, action="escalate"), _row(higher, role=target)]
        assert _rules(graph, rows) == []

    def test_an_escalation_edge_is_checked_without_a_timeout_as_well(self):
        """From CHY-180 on, differing votes escalate too, with no time limit
        involved. Where the edge leads is checked whenever there is one."""
        gate, step = _n("gate"), _n("send", "tool_call")
        graph = Graph([gate, step], [_escalation(gate, step)])
        assert _rules(graph, [_row(gate), _row(step)]) == ["escalation_not_to_gate"]
