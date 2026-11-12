"""
tests/integration/test_run_nodes_db.py

What a run's nodes produced, read back from a real database (CHY-229).

The unit tests in tests/test_run_nodes.py hand the route rows of the expected
shape. What they cannot show is the join: that every pass finds its node's ref
and type in `workflow_nodes`, and that `output_data` comes back as an object
and not as text the client would have to parse a second time.
"""
from __future__ import annotations

import pytest

from contexts.orchestration import service, store
from contexts.orchestration.schema import NodeCreate
from kernel.identity import resolve_actor
from tests.integration._callers import caller as _caller

pytestmark = pytest.mark.integration


def _node(ref: str, node_type: str, task_type: str | None = None) -> NodeCreate:
    return NodeCreate.model_construct(
        node_ref=ref, node_type=node_type, name=f"step {ref}", description=None,
        join_policy=None, config={}, context_requirements=None, task_type=task_type,
        target_workflow_id=None, target_workflow_version=None,
        input_mapping={}, output_mapping={}, review_role=None,
        review_timeout=None, on_timeout=None,
    )


async def test_a_run_at_the_gate_shows_the_draft(pool, system_pool, workflow):
    """Set up the way the engine writes it, read the way the starter does.

    Since CHY-92 only the starter, members of the workflow's scope chain and
    admin see a run: reading it as a fresh consumer would be not found.
    """
    starter_caller = _caller("contributor")
    starter = await resolve_actor(pool, starter_caller)
    async with system_pool.acquire() as conn:
        draft = await store.insert_node(conn, workflow, _node("draft", "llm_call", "draft_reply"))
        gate = await store.insert_node(conn, workflow, _node("review", "human_review"))
        run = await store.insert_run(conn, store.NewRun(
            workflow_id=workflow, workflow_version=1,
            actor_id=starter.id, input_data={"message": "Where is my order?"},
        ))
        done = await store.start_execution(conn, store.NewExecution(
            run_id=run["id"], node_id=draft["id"], iteration=1, consumed=[], input_data={},
        ))
        await store.complete_execution(conn, done["id"],
                                       output={"content": "It ships tomorrow."},
                                       model_used="qwen2.5:1.5b")
        await store.start_execution(conn, store.NewExecution(
            run_id=run["id"], node_id=gate["id"], iteration=1, consumed=[], input_data={},
        ))

    out = await service.run_nodes(pool, starter_caller, run["id"])

    assert [(n.node_ref, n.node_type) for n in out.nodes] == [
        ("draft", "llm_call"), ("review", "human_review")]
    assert out.nodes[0].output == {"content": "It ships tomorrow."}
    assert out.nodes[0].model_used == "qwen2.5:1.5b"
    assert out.nodes[0].name == "step draft"
    assert out.nodes[1].output is None
