"""
tests/test_workflows_api.py

The workflow endpoints (CHY-82).

`tests/test_workflow_graph.py` proves the rules; this file proves they are
consulted, and that the two states a workflow can be in — editable draft,
released — actually gate what they claim to.

The rule worth naming: **activation validates, retirement does not.** A
workflow being taken out of service must not be trapped by its own graph
having become invalid in the meantime; that is the one moment somebody most
wants to stop it.
"""
from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi import status

from tests.conftest import actor_row, client_for, make_pool

_WF = uuid4()
_SCOPE = uuid4()
_NOW = datetime(2026, 9, 4, 9, 0, tzinfo=UTC)


def _workflow_row(status: str = "draft", **kw) -> dict:
    return {
        "id": _WF, "scope_id": _SCOPE, "name": "study", "description": None,
        "version": 1, "superseded_by_version": None, "status": status,
        "risk_classification": {}, "created_by": uuid4(), "created_at": _NOW,
        **kw,
    }


def _node_row(node_ref: str, node_type: str, **kw) -> dict:
    return {
        "id": kw.pop("id", uuid4()), "workflow_id": _WF, "node_ref": node_ref,
        "node_type": node_type, "name": None, "description": None,
        "join_policy": None, "config": {}, "context_requirements": None,
        "task_type": "summarize" if node_type == "llm_call" else None,
        "target_workflow_id": None,
        "target_workflow_version": None, "input_mapping": {}, "output_mapping": {},
        "review_role": None, "review_timeout": None, "on_timeout": None, **kw,
    }


def _conn_row(source, target, loop_limit=None, condition=None) -> dict:
    return {
        "id": uuid4(), "workflow_id": _WF, "source_node_id": source["id"],
        "target_node_id": target["id"], "condition": condition, "label": None,
        "execution_mode": "sequential", "loop_limit": loop_limit,
    }


def _valid_graph() -> tuple[list[dict], list[dict]]:
    ctx = _node_row("compose", "context_compose")
    call = _node_row("summarize", "llm_call")
    review = _node_row("approve", "human_review")
    return [ctx, call, review], [_conn_row(ctx, call), _conn_row(call, review)]


def _graph_answers(nodes: list[dict], conns: list[dict], calls: list[dict] | None = None):
    """Answer each set-returning query with the rows it asks for.

    One fixed list would answer all three — nodes, connections and the
    sub-workflow call graph — and a test green for that reason has not
    consulted the validator at all.
    """
    def answer(sql, *args):
        if "FROM workflow_nodes" in sql and "node_type = 'sub_workflow'" in sql:
            return calls or []
        if "FROM workflow_nodes" in sql:
            return nodes
        if "FROM workflow_connections" in sql:
            return conns
        return []
    return answer


# ── reading ───────────────────────────────────────────────────────────────────

def test_the_graph_endpoint_returns_nodes_and_edges():
    nodes, conns = _valid_graph()
    pool = make_pool(fetchrow=_workflow_row(), fetch=None)
    pool.fetch.side_effect = _graph_answers(nodes, conns)
    with client_for(pool) as client:
        body = client.get(f"/v1/workflows/{_WF}/graph").json()

    assert {n["node_ref"] for n in body["nodes"]} == {"compose", "summarize", "approve"}
    assert len(body["connections"]) == 2


def test_a_missing_workflow_is_404():
    pool = make_pool(fetchrow=None)
    with client_for(pool) as client:
        assert client.get(f"/v1/workflows/{_WF}").status_code == 404


# ── the dry run ───────────────────────────────────────────────────────────────

def test_validate_reports_a_valid_graph_as_valid():
    nodes, conns = _valid_graph()
    pool = make_pool(fetchrow=_workflow_row())
    pool.fetch.side_effect = _graph_answers(nodes, conns)
    with client_for(pool) as client:
        body = client.post(f"/v1/workflows/{_WF}/validate").json()

    assert body["valid"] is True
    assert body["violations"] == []


def test_validate_answers_200_even_when_the_graph_is_broken():
    """A validation that ran and found problems *succeeded*.

    Reporting it as 4xx would conflate "your request was wrong" with "your
    workflow is", and a client would have to parse the body to tell them apart.
    """
    ctx = _node_row("compose", "context_compose")
    call = _node_row("summarize", "llm_call")
    pool = make_pool(fetchrow=_workflow_row())
    pool.fetch.side_effect = _graph_answers([ctx, call], [_conn_row(ctx, call)])
    with client_for(pool) as client:
        response = client.post(f"/v1/workflows/{_WF}/validate")

    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is False
    assert {v["rule"] for v in body["violations"]} == {
        "mandatory_node_missing", "unreviewed_output",
    }


def test_validate_changes_nothing():
    """The dry run must not activate anything.

    Asserted on the queries rather than trusted: `validate` and `set status`
    read the same rows, and the difference between them is one UPDATE.
    """
    nodes, conns = _valid_graph()
    pool = make_pool(fetchrow=_workflow_row())
    pool.fetch.side_effect = _graph_answers(nodes, conns)
    with client_for(pool) as client:
        client.post(f"/v1/workflows/{_WF}/validate")

    written = [c.args[0] for c in pool.conn.fetchrow.await_args_list]
    assert not any("UPDATE workflow_definitions" in sql for sql in written)


# ── activation ────────────────────────────────────────────────────────────────

def test_activating_a_valid_workflow_succeeds():
    nodes, conns = _valid_graph()
    pool = make_pool(
        actor=actor_row(role="reviewer"),
        conn_fetchrow=[_workflow_row(), _workflow_row(status="active")],
    )
    pool.conn.fetch.side_effect = _graph_answers(nodes, conns)
    with client_for(pool) as client:
        response = client.put(f"/v1/workflows/{_WF}", json={"status": "active"})

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "active"


def test_activating_an_invalid_workflow_is_409_and_names_every_reason():
    ctx = _node_row("compose", "context_compose")
    call = _node_row("summarize", "llm_call")
    pool = make_pool(
        actor=actor_row(role="reviewer"),
        conn_fetchrow=[_workflow_row(), _workflow_row(status="active")],
    )
    pool.conn.fetch.side_effect = _graph_answers([ctx, call], [_conn_row(ctx, call)])
    with client_for(pool) as client:
        response = client.put(f"/v1/workflows/{_WF}", json={"status": "active"})

    assert response.status_code == 409, response.text
    # Not just the first: fixing a graph one round trip at a time is how a
    # validator becomes something people route around.
    assert "human_review" in response.json()["detail"]


def test_retiring_an_invalid_workflow_is_allowed():
    """The counter-probe that matters most here.

    An active workflow whose graph has meanwhile become invalid is exactly the
    one somebody wants to stop. Validating on the way out would trap it.
    """
    ctx = _node_row("compose", "context_compose")
    pool = make_pool(
        actor=actor_row(role="reviewer"),
        conn_fetchrow=[_workflow_row(status="active"), _workflow_row(status="deprecated")],
    )
    pool.conn.fetch.side_effect = _graph_answers([ctx], [])
    with client_for(pool) as client:
        response = client.put(f"/v1/workflows/{_WF}", json={"status": "deprecated"})

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "deprecated"


def test_activation_requires_reviewer():
    pool = make_pool(actor=actor_row(role="contributor"))
    with client_for(pool) as client:
        assert client.put(f"/v1/workflows/{_WF}", json={"status": "active"}).status_code == 403


# ── the graph is frozen once released ─────────────────────────────────────────

class TestDraftOnly:
    @pytest.mark.parametrize("status", ["active", "deprecated"])
    def test_a_node_cannot_be_added_to_a_released_workflow(self, status: str):
        """The same rule context blocks follow: what a reviewer released must
        not change underneath the release.

        The assertion names the *reason*, not just the code. With only
        `== 409` this test stayed green when the draft check was removed — the
        pool double then answered the node_ref lookup with the workflow row,
        the duplicate-ref rule fired, and the same 409 came back from a
        completely different rule. Green for the wrong reason is worth less
        than red.
        """
        pool = make_pool(
            actor=actor_row(role="contributor"),
            conn_fetchrow=[_workflow_row(status=status), None],
        )
        with client_for(pool) as client:
            response = client.post(f"/v1/workflows/{_WF}/nodes",
                                   json={"node_ref": "extra", "node_type": "tool_call"})
        assert response.status_code == 409, response.text
        assert status in response.json()["detail"]
        assert "draft" in response.json()["detail"]

    @pytest.mark.parametrize("status", ["active", "deprecated"])
    def test_a_released_workflow_is_not_reopened(self, status: str):
        """CHY-248: back in draft, its graph could change underneath its runs.

        The double would answer the status update with a draft row, so without
        the check the request succeeds with 200 — the response, not only the
        code, says why it was refused.
        """
        pool = make_pool(
            actor=actor_row(role="reviewer"),
            conn_fetchrow=[_workflow_row(status=status), _workflow_row(status="draft")],
        )
        with client_for(pool) as client:
            response = client.put(f"/v1/workflows/{_WF}", json={"status": "draft"})
        assert response.status_code == 409, response.text
        assert status in response.json()["detail"]
        assert "new version" in response.json()["detail"]

    def test_a_node_can_be_added_to_a_draft(self):
        pool = make_pool(
            actor=actor_row(role="contributor"),
            conn_fetchrow=[_workflow_row(), None, _node_row("extra", "tool_call")],
        )
        with client_for(pool) as client:
            response = client.post(f"/v1/workflows/{_WF}/nodes",
                                   json={"node_ref": "extra", "node_type": "tool_call"})
        assert response.status_code == 201, response.text

    def test_a_duplicate_node_ref_is_refused(self):
        pool = make_pool(
            actor=actor_row(role="contributor"),
            conn_fetchrow=[_workflow_row(), {"id": uuid4(), "node_ref": "extra"}],
        )
        with client_for(pool) as client:
            response = client.post(f"/v1/workflows/{_WF}/nodes",
                                   json={"node_ref": "extra", "node_type": "tool_call"})
        assert response.status_code == 409, response.text
        assert "extra" in response.json()["detail"]


# ── the contract keeps its promises ───────────────────────────────────────────

def test_on_timeout_cannot_be_approve():
    """`approve` is absent from the vocabulary, and that absence is the rule.

    A human oversight gate that approves on silence is not a gate (ADR-004).
    The banking pilot fell back to `approved` twice — CHY-83 — and this is the
    layer that makes the same mistake unconfigurable.
    """
    pool = make_pool(actor=actor_row(role="contributor"))
    with client_for(pool) as client:
        response = client.post(f"/v1/workflows/{_WF}/nodes", json={
            "node_ref": "gate", "node_type": "human_review", "on_timeout": "approve",
        })
    assert response.status_code == 422, response.text


def test_a_loop_limit_of_zero_is_refused():
    """`> 0`, not `>= 0`. A limit of zero is a loop that may not be taken,
    which is an edge that should not exist — and reads as "unlimited" to
    anyone skimming."""
    pool = make_pool(actor=actor_row(role="contributor"))
    with client_for(pool) as client:
        response = client.post(f"/v1/workflows/{_WF}/connections", json={
            "source_node_ref": "a", "target_node_ref": "b", "loop_limit": 0,
        })
    assert response.status_code == 422, response.text


# ── guards are checked before a workflow is released ──────────────────────────

def test_a_malformed_guard_blocks_activation():
    """The whole point of choosing data over an expression (CHY-130).

    A structured condition can be judged before it runs, so the answer arrives
    while somebody is looking at the workflow — instead of failing a run half
    way through, which is a moment nobody chose.
    """
    ctx = _node_row("compose", "context_compose")
    call = _node_row("summarize", "llm_call")
    review = _node_row("approve", "human_review")
    broken = _conn_row(call, review, condition={"op": "eq"})  # no field
    pool = make_pool(
        actor=actor_row(role="reviewer"),
        conn_fetchrow=[_workflow_row(), _workflow_row(status="active")],
    )
    pool.conn.fetch.side_effect = _graph_answers(
        [ctx, call, review], [_conn_row(ctx, call), broken]
    )
    with client_for(pool) as client:
        response = client.put(f"/v1/workflows/{_WF}", json={"status": "active"})

    assert response.status_code == 409, response.text
    assert "field" in response.json()["detail"]


def test_a_well_formed_guard_does_not_block_activation():
    """Counter-probe: a check that refused every guard would pass the test
    above and make conditional edges unreleasable."""
    ctx = _node_row("compose", "context_compose")
    call = _node_row("summarize", "llm_call")
    review = _node_row("approve", "human_review")
    guarded = _conn_row(
        call, review, condition={"field": "risk", "op": "eq", "value": "high"}
    )
    pool = make_pool(
        actor=actor_row(role="reviewer"),
        conn_fetchrow=[_workflow_row(), _workflow_row(status="active")],
    )
    pool.conn.fetch.side_effect = _graph_answers(
        [ctx, call, review], [_conn_row(ctx, call), guarded]
    )
    with client_for(pool) as client:
        response = client.put(f"/v1/workflows/{_WF}", json={"status": "active"})

    assert response.status_code == 200, response.text


# ── a guard, created through the API (CHY-144) ────────────────────────────────
#
# The activation checks above have covered structured guards since CHY-130.
# What nobody exercised was the way in: `ConnectionCreate.condition` stayed
# `str` when migration 020 made the column JSONB, so the published contract
# described a value that could be sent and never activated. There was no value
# that was valid by the contract *and* valid in fact.
#
# Every test in this file builds connections as rows. That is why the gap
# survived — the same shape as `NodeContext` in CHY-131: correct code on both
# sides and the way between them unexercised.

def test_a_guard_as_a_string_is_refused_by_the_contract():
    """It used to be accepted, stored, and then rejected on activation.

    The error the caller got was about the condition, not about the type the
    contract had told them to send — which sends whoever reads it looking in
    the wrong place.
    """
    pool = make_pool(actor=actor_row(role="contributor"))
    with client_for(pool) as client:
        response = client.post(f"/v1/workflows/{_WF}/connections", json={
            "source_node_ref": "a", "target_node_ref": "b",
            "condition": "risk == high",
        })
    assert response.status_code == 422, response.text


def test_a_structured_guard_is_accepted_and_survives_the_round_trip():
    """Counter-probe: refusing every guard would pass the test above and make
    conditional edges impossible to create at all."""
    guard = {"op": "eq", "field": "risk", "value": "high"}
    source = _node_row("summarize", "llm_call")
    target = _node_row("approve", "human_review")
    created = _conn_row(source, target, condition=guard)

    pool = make_pool(
        actor=actor_row(role="contributor"),
        conn_fetchrow=[
            _workflow_row(),            # the workflow being edited
            source, target,             # resolving the two node refs
            created,                    # the inserted row
        ],
    )
    with client_for(pool) as client:
        response = client.post(f"/v1/workflows/{_WF}/connections", json={
            "source_node_ref": "summarize", "target_node_ref": "approve",
            "condition": guard,
        })

    assert response.status_code == 201, response.text
    assert response.json()["condition"] == guard, (
        "the guard did not survive the round trip"
    )


def test_a_run_with_unusable_identifiers_to_redact_is_refused_at_the_start():
    """Checked when the run starts, not at its first model call (CHY-63).

    A run that would fail at its third node over a typo in the list should not
    be started at all.
    """
    pool = make_pool(actor=actor_row(role="contributor"))
    with client_for(pool) as client:
        response = client.post(f"/v1/workflows/{_WF}/runs", json={
            "input_data": {"identifiers_to_redact": {"PERSON": "Erika"}},
        })
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT, response.text
    assert "must be a list" in response.text
