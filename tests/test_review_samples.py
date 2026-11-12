"""
tests/test_review_samples.py

The review level `sample`, without a database (CHY-223, ADR-050 step 3b).

At `sample` the run does not wait: the gate passes, and a share of its passes
goes into a review queue to be judged afterwards. Whether a pass is drawn is a
hash of the pass — the same answer every time it is asked, so the audit entry
that says so can be checked, and a test can name one drawn and one not.

A sample gate stays a gate: where the minimum of its scope and its workflow
asks for more, it waits as an approver gate — a stricter classification makes
it stricter at once, as any gate.

The queue itself — who sees it, who judges, the reason with placeholders, the
purge — is in tests/integration/test_review_samples_db.py.
"""
from __future__ import annotations

import typing
from uuid import uuid4

import pytest
from pydantic import ValidationError

from contexts.orchestration import service, votes
from contexts.orchestration.schema import NodeCreate, ReviewLevel, ReviewRole
from tests.test_workflow_advance import (
    FakeDb,
    _conn_row,
    _node,
    _NullPool,
    _registry,
    _run,
)


class TestTheDraw:
    def test_the_same_pass_gets_the_same_answer(self):
        execution = uuid4()
        assert {votes.drawn(execution, 0.5) for _ in range(20)} in ({True}, {False})

    def test_all_at_one_none_at_a_tiny_share(self):
        executions = [uuid4() for _ in range(500)]
        assert all(votes.drawn(e, 1) for e in executions)
        assert sum(votes.drawn(e, 0.000001) for e in executions) == 0

    def test_over_many_passes_the_share_is_the_rate(self):
        executions = [uuid4() for _ in range(4000)]
        share = sum(votes.drawn(e, 0.25) for e in executions) / len(executions)
        assert 0.21 < share < 0.29, share


class TestTheWords:
    def test_the_levels_are_the_roles(self):
        """One vocabulary in three places — the role a gate is drawn with, the
        level that applied, and the rank order — must not drift apart."""
        assert typing.get_args(ReviewLevel) == votes.LEVELS == typing.get_args(ReviewRole)


class TestTheRequest:
    def test_a_sample_gate_names_its_share(self):
        gate = NodeCreate(node_ref="check", node_type="human_review",
                          review_role="sample", review_sample_rate=0.1)
        assert gate.review_sample_rate == 0.1

    @pytest.mark.parametrize("rate", [0, -0.1, 1.5])
    def test_a_share_outside_zero_to_one_is_refused(self, rate):
        with pytest.raises(ValidationError):
            NodeCreate(node_ref="check", node_type="human_review",
                       review_role="sample", review_sample_rate=rate)

    @pytest.mark.parametrize("rate", [0.0004, 0.0505, 5e-324])
    def test_a_share_the_column_cannot_keep_is_refused(self, rate):
        """CHY-276, measured 10.11.2026 against Postgres 16: 0.0505 was stored as
        0.051 without a word, 0.0004 rounded to 0 and came back as a 422 that
        said "above 0". Now refused here, before anything is stored."""
        with pytest.raises(ValidationError, match="three decimal places|greater than or equal"):
            NodeCreate(node_ref="check", node_type="human_review",
                       review_role="sample", review_sample_rate=rate)

    @pytest.mark.parametrize("rate", [0.001, 0.05, 1])
    def test_a_share_the_column_keeps_is_taken(self, rate):
        """Counter-probe to the refusals above."""
        gate = NodeCreate(node_ref="check", node_type="human_review",
                          review_role="sample", review_sample_rate=rate)
        assert gate.review_sample_rate == rate


# ── at run time ───────────────────────────────────────────────────────────────

def _sample_workflow(monkeypatch, rate: float, **classification) -> tuple[FakeDb, dict]:
    a = _node("a")
    gate = {**_node("check", "human_review"), "review_role": "sample",
            "review_sample_rate": rate}
    after = _node("after")
    db = FakeDb(nodes=[a, gate, after],
                connections=[_conn_row(a, gate), _conn_row(gate, after)], **classification)
    db.install(monkeypatch)
    return db, gate


class TestAtRunTime:
    async def test_the_run_does_not_wait_and_a_drawn_pass_is_queued(self, monkeypatch):
        db, gate = _sample_workflow(monkeypatch, 1.0, data_class="internal",
                                    risk_class="minimal")
        run_id = _run(db)

        advance = await service.advance_run(_NullPool(), run_id, registry=_registry())

        assert advance.run.status == "complete"
        execution = next(e for e in db.executions if e["node_id"] == gate["id"])
        assert execution["output_data"] == {"decision": "sampled", "drawn": True}
        assert execution["review_level"] == "sample"
        assert db.samples == [{"execution_id": execution["id"], "run_id": run_id}]
        [entry] = [a for a in db.audits if a["action"] == "workflow.run_gate_reached"]
        assert entry["payload"]["drawn"] is True and entry["payload"]["sample_rate"] == 1.0

    async def test_a_pass_not_drawn_goes_on_without_a_trace_in_the_queue(self, monkeypatch):
        db, gate = _sample_workflow(monkeypatch, 0.000001, data_class="internal",
                                    risk_class="minimal")
        run_id = _run(db)

        advance = await service.advance_run(_NullPool(), run_id, registry=_registry())

        assert advance.run.status == "complete"
        assert db.samples == []
        execution = next(e for e in db.executions if e["node_id"] == gate["id"])
        assert execution["output_data"] == {"decision": "sampled", "drawn": False}

    async def test_where_the_minimum_asks_for_more_it_waits_as_an_approver(self, monkeypatch):
        """The same gate under a confidential scope: a person decides first."""
        db, gate = _sample_workflow(monkeypatch, 1.0, data_class="confidential",
                                    risk_class="minimal")
        run_id = _run(db)

        advance = await service.advance_run(_NullPool(), run_id, registry=_registry())

        assert advance.run.status == "awaiting_oversight"
        execution = next(e for e in db.executions if e["node_id"] == gate["id"])
        assert execution["review_level"] == "approver" and db.samples == []
