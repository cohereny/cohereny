"""
tests/test_review_areas.py

Areas at the gate, without a database (CHY-180, ADR-050).

The tally first, case by case as Listing 10.2-1 of the book ran them against
PostgreSQL: one vote waits, two approvals release, two rejections reject, two
different votes escalate. Then what the requests may carry, and what activation
refuses about a gate with areas — among it the decision of 09.11.2026: a gate
with an area of its own counts as the higher authority, even with one vote.

The three refusals the database carries — a second vote for an area, a vote
without membership, a vote after the membership was revoked — and every path
through record_oversight are in tests/integration/test_review_areas_db.py.
"""
from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from contexts.orchestration import conditions, votes
from contexts.orchestration.graph import Edge, Graph, Node
from contexts.orchestration.schema import NodeCreate, OversightDecision
from contexts.orchestration.service import _gate_violations
from kernel.review_areas import Resolved

MARKT, MARKTFOLGE, KOMMISSION = uuid4(), uuid4(), uuid4()
BOTH = (MARKT, MARKTFOLGE)


# ── the tally ─────────────────────────────────────────────────────────────────

class TestByAreas:
    def test_one_vote_waits(self):
        assert votes.by_areas(BOTH, {MARKT: "approved"}) == votes.PENDING

    def test_both_approve_release(self):
        assert votes.by_areas(BOTH, {MARKT: "approved", MARKTFOLGE: "approved"}) == votes.APPROVED

    def test_both_reject_reject(self):
        assert votes.by_areas(BOTH, {MARKT: "rejected", MARKTFOLGE: "rejected"}) == votes.REJECTED

    @pytest.mark.parametrize(("markt", "marktfolge"), [("approved", "rejected"),
                                                       ("rejected", "approved")])
    def test_differing_votes_escalate_either_way(self, markt, marktfolge):
        """Neither area overrules the other: the back office may not be
        overruled (BTO 1.1 Tz. 2), and the front office may not either."""
        assert votes.by_areas(BOTH, {MARKT: markt, MARKTFOLGE: marktfolge}) == votes.ESCALATED

    def test_a_rejection_alone_does_not_close_the_gate(self):
        """The acceptance of CHY-180: until every area has voted, the gate
        waits — a rejection included. Without areas it would end the run."""
        assert votes.by_areas(BOTH, {MARKTFOLGE: "rejected"}) == votes.PENDING

    def test_a_vote_for_an_area_the_gate_does_not_name_is_not_counted(self):
        assert votes.by_areas(BOTH, {MARKT: "approved", KOMMISSION: "approved"}) == votes.PENDING

    def test_one_area_decides_with_one_vote(self):
        """The credit committee as the higher authority."""
        assert votes.by_areas((KOMMISSION,), {KOMMISSION: "approved"}) == votes.APPROVED


class TestWithoutAreas:
    """Unchanged since CHY-176, moved into this module."""

    def test_a_rejection_ends_the_gate_at_once(self):
        assert votes.without_areas("four_eyes", "rejected", 0) == votes.REJECTED

    @pytest.mark.parametrize(("role", "approvals", "outcome"), [
        (None, 1, votes.APPROVED), ("approver", 1, votes.APPROVED),
        ("four_eyes", 1, votes.PENDING), ("four_eyes", 2, votes.APPROVED),
    ])
    def test_approvals_against_the_role(self, role, approvals, outcome):
        assert votes.without_areas(role, "approved", approvals) == outcome


class TestStrictness:
    @pytest.mark.parametrize(("role", "areas", "rank"), [
        (None, None, 1), ("approver", None, 1), ("four_eyes", None, 2),
        (None, ["markt"], 1), (None, ["markt", "marktfolge"], 2),
    ])
    def test_two_areas_count_as_four_eyes(self, role, areas, rank):
        assert votes.strictness(role, areas) == rank


# ── what a request may carry ──────────────────────────────────────────────────

def _gate(**kw) -> NodeCreate:
    return NodeCreate(node_ref="votum", node_type="human_review", **kw)


class TestTheRequest:
    def test_a_gate_names_its_areas(self):
        assert _gate(review_areas=["markt", "marktfolge"]).review_areas == ["markt", "marktfolge"]

    @pytest.mark.parametrize("areas", [[], ["markt", "markt"], ["Markt"], ["1markt"],
                                       [f"a{i}" for i in range(17)]])
    def test_an_empty_repeated_misspelt_or_overlong_list_is_refused(self, areas):
        with pytest.raises(ValidationError):
            _gate(review_areas=areas)

    def test_a_vote_may_name_its_area(self):
        assert OversightDecision(decision="approved", area="marktfolge").area == "marktfolge"

    def test_the_area_of_a_vote_is_a_name_not_free_text(self):
        with pytest.raises(ValidationError):
            OversightDecision(decision="approved", area="Markt; DROP")


# ── activation ────────────────────────────────────────────────────────────────

DAY = timedelta(days=1)


def _n(ref: str, node_type: str = "human_review") -> Node:
    return Node(id=uuid4(), node_ref=ref, node_type=node_type)


def _row(node: Node, *, role=None, areas=None, timeout=None, action=None) -> dict:
    """A row as store.node_details gives it, reduced to what the gate rules read."""
    return {"id": node.id, "review_role": role, "review_timeout": timeout,
            "on_timeout": action, "review_areas": areas}


def _known(*names: str, members: int = 1) -> dict[str, Resolved]:
    return {n: Resolved(uuid4(), uuid4(), n, members) for n in names}


def _up(a: Node, b: Node) -> Edge:
    return Edge(id=uuid4(), source=a.id, target=b.id, condition=dict(conditions.ESCALATION))


def _rules(graph, rows, areas) -> list[str]:
    return sorted(v.rule for v in _gate_violations(graph, rows, areas))


AREAS = _known("markt", "marktfolge", "kreditkommission")


class TestActivation:
    def test_markt_and_marktfolge_escalating_to_the_credit_committee_pass(self):
        """The case of BTO 1.1 Tz. 6, as drawn: two areas, and an escalation edge
        to the credit committee, which decides with one vote. Allowed because
        the committee is an area the first gate does not name (09.11.2026)."""
        votum, kommission = _n("votum"), _n("kommission")
        graph = Graph([votum, kommission], [_up(votum, kommission)])
        rows = [_row(votum, areas=["markt", "marktfolge"]),
                _row(kommission, areas=["kreditkommission"])]
        assert _rules(graph, rows, AREAS) == []

    def test_escalating_to_one_of_the_same_areas_is_not_higher(self):
        """Counter-probe to the case above: the back office alone is not a
        higher authority than front and back office together."""
        votum, again = _n("votum"), _n("again")
        graph = Graph([votum, again], [_up(votum, again)])
        rows = [_row(votum, areas=["markt", "marktfolge"]), _row(again, areas=["marktfolge"])]
        assert _rules(graph, rows, AREAS) == ["escalation_to_weaker_gate"]

    def test_escalating_to_one_approver_without_an_area_is_not_higher(self):
        votum, chef = _n("votum"), _n("chef")
        graph = Graph([votum, chef], [_up(votum, chef)])
        rows = [_row(votum, areas=["markt", "marktfolge"]), _row(chef)]
        assert _rules(graph, rows, AREAS) == ["escalation_to_weaker_gate"]

    def test_two_areas_need_an_escalation_edge(self):
        """Their votes can differ, and then the gate has nowhere to go."""
        votum = _n("votum")
        assert _rules(Graph([votum], []), [_row(votum, areas=["markt", "marktfolge"])],
                      AREAS) == ["escalation_without_edge"]

    def test_one_area_needs_none(self):
        """Counter-probe: one vote cannot differ from itself."""
        kommission = _n("kommission")
        assert _rules(Graph([kommission], []), [_row(kommission, areas=["kreditkommission"])],
                      AREAS) == []

    def test_an_area_defined_nowhere_above_is_refused(self):
        votum = _n("votum")
        assert _rules(Graph([votum], []), [_row(votum, areas=["vorstand"])],
                      AREAS) == ["unknown_review_area"]

    def test_an_area_nobody_belongs_to_is_refused(self):
        votum = _n("votum")
        assert _rules(Graph([votum], []), [_row(votum, areas=["leer"])],
                      _known("leer", members=0)) == ["review_area_without_members"]

    @pytest.mark.parametrize(("role", "areas"), [("approver", ["markt", "marktfolge"]),
                                                 ("four_eyes", ["kreditkommission"])])
    def test_a_role_that_contradicts_the_areas_is_refused(self, role, areas):
        """One vote per area: approver with two areas promises one person where
        there are two, four_eyes with one area two where there is one."""
        votum, kommission = _n("votum"), _n("kommission")
        graph = Graph([votum, kommission], [_up(votum, kommission)])
        rows = [_row(votum, role=role, areas=areas), _row(kommission, areas=["kreditkommission"])]
        assert "review_role_contradicts_areas" in _rules(graph, rows, AREAS)

    def test_four_eyes_with_two_areas_is_consistent(self):
        votum, kommission = _n("votum"), _n("kommission")
        graph = Graph([votum, kommission], [_up(votum, kommission)])
        rows = [_row(votum, role="four_eyes", areas=["markt", "marktfolge"]),
                _row(kommission, areas=["kreditkommission"])]
        assert _rules(graph, rows, AREAS) == []

    def test_a_timeout_escalation_from_four_eyes_still_needs_as_many_people(self):
        """The rule of CHY-254 is unchanged where no area is involved."""
        gate, higher = _n("gate"), _n("higher")
        graph = Graph([gate, higher], [_up(gate, higher)])
        rows = [_row(gate, role="four_eyes", timeout=DAY, action="escalate"),
                _row(higher, role="approver")]
        assert _rules(graph, rows, AREAS) == ["escalation_to_weaker_gate"]
