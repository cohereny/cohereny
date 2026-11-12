"""
tests/test_review_levels.py

How strict a gate must be, without a database (CHY-223, ADR-050 step 3a).

The minimum is the stricter of what the data class of the workflow's scope and
what the workflow's risk class ask. A forgotten classification loosens nothing:
a scope without a data class counts as critical (ADR-041), a workflow without a
risk class as high. Activation refuses a gate below the minimum — except one
reached only after the areas of the gate before disagreed (Thomas,
09.11.2026). At run time the level is fixed when the run reaches the gate, the
stricter of gate and minimum as they are then, and written to the audit log.

What only PostgreSQL shows — a scope classified stricter after the release, the
credit committee under `critical`, the gate with areas that falls below — is in
tests/integration/test_review_levels_db.py.
"""
from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from contexts.orchestration import conditions, service, votes
from contexts.orchestration.graph import Edge, Graph, Node
from contexts.orchestration.schema import OversightDecision, WorkflowCreate
from contexts.orchestration.service import _gate_violations
from kernel.review_areas import Resolved
from tests.test_workflow_advance import _at_the_gate, _caller, _engine, _NullPool


# ── the minimum ───────────────────────────────────────────────────────────────

class TestTheMinimum:
    @pytest.mark.parametrize(("data_class", "risk_class", "level", "because"), [
        ("public", "minimal", "sample", ("data_class", "risk_class")),
        ("internal", "limited", "approver", ("risk_class",)),
        ("confidential", "minimal", "approver", ("data_class",)),
        ("confidential", "limited", "approver", ("data_class", "risk_class")),
        ("critical", "minimal", "four_eyes", ("data_class",)),
        ("internal", "high", "four_eyes", ("risk_class",)),
    ])
    def test_the_stricter_of_data_and_risk(self, data_class, risk_class, level, because):
        floor = votes.minimum(data_class, risk_class)
        assert (floor.level, floor.because) == (level, because)

    def test_a_workflow_nobody_classified_counts_as_high(self):
        """A forgotten classification may not loosen anything — as a scope
        without a data class counts as critical."""
        assert votes.minimum("public", None).level == "four_eyes"

    def test_an_unknown_data_class_counts_as_critical(self):
        assert votes.minimum("geheim", "minimal").level == "four_eyes"


class TestTheLevelThatApplies:
    def test_the_stricter_of_gate_and_minimum(self):
        assert votes.applies(1, votes.minimum("critical", "high"),
                             after_differing_votes=False) == "four_eyes"
        assert votes.applies(2, votes.minimum("internal", "limited"),
                             after_differing_votes=False) == "four_eyes"

    def test_after_differing_votes_the_gate_decides_as_drawn(self):
        """The credit committee with one vote, under critical: the areas before
        have looked."""
        assert votes.applies(1, votes.minimum("critical", "high"),
                             after_differing_votes=True) == "approver"


class TestTheRequest:
    def test_a_workflow_takes_a_risk_class(self):
        body = WorkflowCreate(scope_id=uuid4(), name="kredit", risk_class="high")
        assert body.risk_class == "high"

    def test_anything_else_is_refused(self):
        with pytest.raises(ValueError, match="risk_class"):
            WorkflowCreate(scope_id=uuid4(), name="kredit", risk_class="medium")


# ── activation ────────────────────────────────────────────────────────────────

DAY = timedelta(days=1)
AREAS = {n: Resolved(uuid4(), uuid4(), n, 1) for n in ("markt", "marktfolge", "kreditkommission")}
CRITICAL = votes.minimum("critical", "high")


def _n(ref: str) -> Node:
    return Node(id=uuid4(), node_ref=ref, node_type="human_review")


def _row(node: Node, *, role=None, areas=None, action=None) -> dict:
    return {"id": node.id, "review_role": role, "review_areas": areas,
            "review_timeout": DAY if action else None, "on_timeout": action}


def _up(a: Node, b: Node) -> Edge:
    return Edge(id=uuid4(), source=a.id, target=b.id, condition=dict(conditions.ESCALATION))


def _below(graph, rows, floor=CRITICAL) -> list[str]:
    return [v.node_ref for v in _gate_violations(graph, rows, AREAS, floor)
            if v.rule == "review_below_minimum"]


class TestActivation:
    def test_one_approver_is_too_few_under_critical(self):
        gate = _n("approve")
        assert _below(Graph([gate], []), [_row(gate, role="approver")]) == ["approve"]

    @pytest.mark.parametrize("row", [{"role": "four_eyes"},
                                     {"areas": ["markt", "marktfolge"]}])
    def test_four_eyes_or_two_areas_are_enough(self, row):
        gate = _n("approve")
        assert _below(Graph([gate], []), [_row(gate, **row)]) == []

    def test_a_low_minimum_lets_one_approver_pass(self):
        """Counter-probe: the rule follows the classification, it does not ask
        for four eyes everywhere."""
        gate = _n("approve")
        assert _below(Graph([gate], []), [_row(gate)], votes.minimum("internal", "minimal")) == []

    def test_the_credit_committee_after_differing_votes_decides_alone(self):
        """Thomas, 09.11.2026: the areas before have looked."""
        votum, kommission = _n("votum"), _n("kommission")
        graph = Graph([votum, kommission], [_up(votum, kommission)])
        rows = [_row(votum, areas=["markt", "marktfolge"]),
                _row(kommission, areas=["kreditkommission"])]
        assert _below(graph, rows) == []

    def test_not_if_a_timeout_can_bring_the_run_there(self):
        """After a timeout nobody has looked: the target meets the minimum itself."""
        votum, kommission = _n("votum"), _n("kommission")
        graph = Graph([votum, kommission], [_up(votum, kommission)])
        rows = [_row(votum, areas=["markt", "marktfolge"], action="escalate"),
                _row(kommission, areas=["kreditkommission"])]
        assert _below(graph, rows) == ["kommission"]

    def test_not_if_another_connection_leads_there(self):
        start, votum, kommission = _n("start"), _n("votum"), _n("kommission")
        plain = Edge(id=uuid4(), source=start.id, target=kommission.id)
        graph = Graph([start, votum, kommission], [_up(votum, kommission), plain])
        rows = [_row(start, role="four_eyes"), _row(votum, areas=["markt", "marktfolge"]),
                _row(kommission, areas=["kreditkommission"])]
        assert _below(graph, rows) == ["kommission"]

    def test_not_after_a_gate_whose_one_vote_cannot_differ(self):
        """Escalating from a gate with one area or none is never `votes_differ`."""
        chef, kommission = _n("chef"), _n("kommission")
        graph = Graph([chef, kommission], [_up(chef, kommission)])
        rows = [_row(chef, role="four_eyes"), _row(kommission, areas=["kreditkommission"])]
        assert _below(graph, rows) == ["kommission"]


# ── at run time ───────────────────────────────────────────────────────────────

def _as_people(monkeypatch, n: int) -> None:
    queue = [SimpleNamespace(id=uuid4(), role="reviewer", is_service_account=False) for _ in range(n)]

    async def resolve(_pool, _caller):
        return queue.pop(0)

    monkeypatch.setattr(service, "resolve_actor", resolve)


async def _approve(run_id):
    return await service.record_oversight(
        _NullPool(), _caller(), run_id, OversightDecision(decision="approved"), engine=_engine())


class TestAtRunTime:
    async def test_an_unclassified_workflow_asks_four_eyes_of_an_approver_gate(self, monkeypatch):
        db, run_id, _ = await _at_the_gate(monkeypatch, "approver", risk_class=None)
        _as_people(monkeypatch, 2)

        assert (await _approve(run_id)).run.status == "awaiting_oversight"
        assert (await _approve(run_id)).run.status != "awaiting_oversight"

    async def test_a_classified_one_asks_what_the_gate_was_drawn_with(self, monkeypatch):
        """Counter-probe: the FakeDb's workflow is `limited` in an `internal`
        scope — the minimum is one approver, and one approval releases."""
        _db, run_id, _ = await _at_the_gate(monkeypatch, "approver")
        _as_people(monkeypatch, 1)
        assert (await _approve(run_id)).run.status != "awaiting_oversight"

    async def test_the_level_is_written_to_the_gate_and_to_the_audit(self, monkeypatch):
        db, run_id, gate = await _at_the_gate(monkeypatch, "approver",
                                              data_class="critical", risk_class="limited")
        execution = next(e for e in db.executions if e["node_id"] == gate["id"])
        assert execution["review_level"] == "four_eyes"
        [entry] = [a for a in db.audits if a["action"] == "workflow.run_gate_reached"]
        assert entry["actor_id"] is None
        assert entry["payload"] == {
            "node_ref": "approve", "level": "four_eyes", "gate": "approver",
            "minimum": "four_eyes", "because": ["data_class"], "data_class": "critical",
            "risk_class": "limited", "areas": [], "after_differing_votes": False}

    async def test_the_level_holds_while_the_run_waits(self, monkeypatch):
        """Fixed when the run reached the gate: a classification changed while
        it waits does not change the rules under the people deciding."""
        db, run_id, _ = await _at_the_gate(monkeypatch, "approver", risk_class=None)
        db.risk_class = "minimal"
        _as_people(monkeypatch, 1)
        assert (await _approve(run_id)).run.status == "awaiting_oversight"

    async def test_a_gate_reached_before_migration_044_keeps_its_role(self, monkeypatch):
        db, run_id, gate = await _at_the_gate(monkeypatch, "approver", risk_class=None)
        next(e for e in db.executions if e["node_id"] == gate["id"])["review_level"] = None
        _as_people(monkeypatch, 1)
        assert (await _approve(run_id)).run.status != "awaiting_oversight"
