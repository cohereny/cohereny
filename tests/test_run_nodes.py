"""A run shows what its nodes produced (CHY-229).

Until CHY-229 the outputs of a run's nodes never left the database. A run
waiting at a `human_review` gate showed its status and its input, so whoever
decided there decided without seeing what the decision was about — the opposite
of what Article 14 of the AI Act asks of human oversight.

These tests go through the HTTP route with the pool doubled. What the join
against `workflow_nodes` returns from a real database is held in
tests/integration/test_run_nodes_db.py.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import uuid4

from tests.conftest import actor_row, client_for, make_pool

_RUN = uuid4()


def _run_row(status: str = "awaiting_oversight") -> dict:
    return {"id": _RUN, "workflow_id": uuid4(), "workflow_version": 1, "status": status,
            "actor_id": uuid4(), "input_data": {"message": "Where is my order?"},
            "result": None, "error_message": None, "started_at": None,
            "completed_at": None, "created_at": datetime.now(UTC),
            "parent_run_id": None, "parent_node_id": None}


def _execution(node_ref, node_type, status, output, model=None, error=None) -> dict:
    return {"node_ref": node_ref, "node_type": node_type, "name": None, "iteration": 1,
            "status": status, "output_data": output, "model_used": model,
            "error_message": error, "started_at": datetime.now(UTC), "completed_at": None}


_PAUSED = [
    _execution("compose", "context_compose", "complete", {"prompt": "Be friendly."}),
    # asyncpg hands JSONB back as text unless a codec is registered; both shapes
    # occur (tests/integration/test_jsonb_lands_as_objects.py), so one is text.
    _execution("draft", "llm_call", "complete",
               json.dumps({"content": "Your parcel ships tomorrow."}), model="qwen2.5:1.5b"),
    _execution("review", "human_review", "running", None),
]


def _get(pool):
    with client_for(pool) as client:
        return client.get(f"/v1/workflows/runs/{_RUN}/nodes")


def _rows(executions, *, nodes=(), edges=(), counters=()):
    """Answer each query of the route: the passes, the graph, the loop counters.

    Since CHY-150 the route also reads the graph and `loop_counters`, to name a
    loop that ran out; one list for every query would hand executions to both.
    """
    def fetch(sql, *_args):
        if "node_executions" in sql:
            return list(executions)
        if "loop_counters" in sql:
            return list(counters)
        if "FROM workflow_connections" in sql:
            return list(edges)
        if "FROM workflow_nodes" in sql:
            return list(nodes)
        return []
    return fetch


def test_a_paused_run_shows_the_draft_the_gate_is_about():
    """The acceptance criterion: the llm_call's answer, before anyone decides."""
    resp = _get(make_pool(fetchrow=_run_row(), fetch=_rows(_PAUSED)))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "awaiting_oversight"
    draft = next(n for n in body["nodes"] if n["node_ref"] == "draft")
    assert draft["output"] == {"content": "Your parcel ships tomorrow."}
    assert draft["model_used"] == "qwen2.5:1.5b"


def test_the_nodes_come_in_the_order_they_ran():
    body = _get(make_pool(fetchrow=_run_row(), fetch=_rows(_PAUSED))).json()
    assert [n["node_ref"] for n in body["nodes"]] == ["compose", "draft", "review"]


def test_a_node_that_has_not_finished_has_no_output_yet():
    body = _get(make_pool(fetchrow=_run_row(), fetch=_rows(_PAUSED))).json()
    assert next(n for n in body["nodes"] if n["node_ref"] == "review")["output"] is None


def test_a_failed_node_carries_its_reason():
    failed = [_execution("draft", "llm_call", "failed", None,
                         error="no model for task type 'draft_reply'")]
    body = _get(make_pool(fetchrow=_run_row("failed"), fetch=_rows(failed))).json()
    assert "draft_reply" in body["nodes"][0]["error_message"]


def test_an_unknown_run_is_404():
    assert _get(make_pool(fetchrow=None)).status_code == 404


def test_the_lowest_role_reads_it_as_it_reads_the_run():
    """Same role as GET /runs/{id}: whoever may see the input may see what was
    made of it. Authentication itself is held by tests/test_no_auth_bypass.py."""
    assert _get(make_pool(actor=actor_row(role="consumer"), fetchrow=_run_row(),
                          fetch=_rows(_PAUSED))).status_code == 200


# ── a loop that ran out (CHY-150) ─────────────────────────────────────────────

_DRAFT, _CHECK, _BACK = uuid4(), uuid4(), uuid4()
_NODES = [{"id": _DRAFT, "node_ref": "draft", "node_type": "llm_call", "join_policy": None},
          {"id": _CHECK, "node_ref": "check", "node_type": "llm_call", "join_policy": None}]
_EDGES = [{"id": uuid4(), "source_node_id": _DRAFT, "target_node_id": _CHECK,
           "loop_limit": None, "condition": None, "label": None},
          {"id": _BACK, "source_node_id": _CHECK, "target_node_id": _DRAFT,
           "loop_limit": 2, "condition": None, "label": None}]


def _loops(traversals: int) -> list:
    rows = _rows(_PAUSED, nodes=_NODES, edges=_EDGES,
                 counters=[{"connection_id": _BACK, "traversals": traversals}])
    return _get(make_pool(fetchrow=_run_row(), fetch=rows)).json()["exhausted_loops"]


def test_a_loop_that_ran_out_is_named_for_whoever_decides():
    """An output that reaches the gate after its loop ran out has not met the
    loop's criterion. The person deciding reads it here, with the outputs."""
    assert _loops(2) == [{"from_ref": "check", "to_ref": "draft", "loop_limit": 2}]


def test_a_loop_still_within_its_limit_is_not():
    """Counter-probe: looping is not running out."""
    assert _loops(1) == []
