"""
tests/integration/test_workflow_release_db.py

A released workflow stays released, on a real PostgreSQL (CHY-248).

The contract fuzzer found it on 2026-11-07: it set an active workflow back to
`draft`, which was accepted, and then tried to delete an edge its runs had
traversed. The foreign key from `loop_counters` refused, and the API answered
500. The edge is only where it surfaced; the defect is the way back to the
draft, where the graph could change underneath the runs that used it.

The workflow and its run come from test_run_placeholders_db.py: draft → revise
→ approve, run to the gate, so both edges carry a traversal.
"""
from __future__ import annotations

from uuid import uuid4

import pytest

from contexts.orchestration import service as orchestration
from contexts.orchestration.errors import WorkflowNotEditable
from contexts.orchestration.schema import WorkflowStatusPut
from tests.integration._callers import caller
from tests.integration.test_run_placeholders_db import _start, echo  # noqa: F401 -- fixture

pytestmark = pytest.mark.integration


async def test_an_active_workflow_with_runs_is_not_reopened_and_its_graph_stays(
        pool, system_pool, scope, echo):  # noqa: F811
    advance = await _start(pool, system_pool, scope, echo,
                           email=f"erika.{uuid4().hex[:8]}@example.de")
    workflow_id = advance.run.workflow_id
    edges = await system_pool.fetch(
        "SELECT c.id FROM workflow_connections c "
        "JOIN loop_counters l ON l.connection_id = c.id "
        "WHERE c.workflow_id = $1", workflow_id)
    assert edges, "the run traversed the graph"

    with pytest.raises(WorkflowNotEditable):
        await orchestration.set_workflow_status(
            pool, caller("reviewer"), workflow_id, WorkflowStatusPut(status="draft"))
    # What the fuzzer did next: delete an edge the run had counted.
    with pytest.raises(WorkflowNotEditable):
        await orchestration.remove_connection(
            pool, caller("contributor"), workflow_id, edges[0]["id"])

    assert await system_pool.fetchval(
        "SELECT status FROM workflow_definitions WHERE id = $1", workflow_id) == "active"
    assert await system_pool.fetchval(
        "SELECT count(*) FROM workflow_connections WHERE workflow_id = $1", workflow_id) == 2
