"""
tests/integration/test_workflow_node_checks.py

The CHECK constraints of `workflow_nodes`, parsed by a real PostgreSQL (CHY-124).

Migrations 022 (CHY-134) and 023 (CHY-145) put the rules of which field belongs
to which node type into the table, and `NodeNotStorable` turns a refusal into a
sentence and a 422. Until now both halves were tested apart: the constraints by
hand in a scratch database, the translation against a hand-made
`CheckViolationError` with the constraint name set by the test itself. Nothing
proved that the name the database sends is the name the translation expects.

These go through `contexts.orchestration.store.insert_node` — the path the API
takes — so a renamed constraint, a changed column or a mapping that drifted
from the DDL fails here.
"""
from __future__ import annotations

import pytest

from contexts.orchestration import store
from contexts.orchestration.errors import NodeNotStorable
from contexts.orchestration.schema import ContextRequirements, NodeCreate

pytestmark = pytest.mark.integration


def _node(**kw) -> NodeCreate:
    """A NodeCreate built past its own validation.

    `model_construct` skips pydantic, which refuses several of these values
    before they reach the table. That is the point: the database has to refuse
    them on its own, because rows reach it by paths that do not pass through
    this model (ratchet.py's argument for two enforcement points).
    """
    defaults = {
        "node_ref": "n", "node_type": "tool_call", "name": None, "description": None,
        "join_policy": None, "config": {}, "context_requirements": None,
        "task_type": None, "target_workflow_id": None, "target_workflow_version": None,
        "input_mapping": {}, "output_mapping": {}, "review_role": None,
        "review_timeout": None, "on_timeout": None,
    }
    return NodeCreate.model_construct(**{**defaults, **kw})


async def _insert(pool, workflow, node):
    async with pool.acquire() as conn:
        return await store.insert_node(conn, workflow, node)


# ── accepted ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("node", [
    _node(node_ref="compose", node_type="context_compose",
          context_requirements=ContextRequirements(block_types=["system_prompt"])),
    _node(node_ref="bare", node_type="context_compose"),
    _node(node_ref="ask", node_type="llm_call", task_type="summarize",
          config={"prompt": "hi"}),
    _node(node_ref="tool", node_type="tool_call"),
], ids=lambda n: n.node_ref)
async def test_a_valid_node_is_stored(pool, workflow, node):
    """Counter-probe to everything below: the table must not refuse the
    ordinary cases, or every refusal test passes for the wrong reason."""
    row = await _insert(pool, workflow, node)
    assert row["node_ref"] == node.node_ref


# ── refused, with the constraint the translation expects ─────────────────────

@pytest.mark.parametrize(("node", "constraint"), [
    # 022, CHY-134
    (_node(node_ref="a", node_type="context_compose",
           context_requirements=ContextRequirements.model_construct(block_types=["routing_rule"])),
     "workflow_nodes_requirements_shape"),
    (_node(node_ref="b", node_type="context_compose",
           context_requirements=ContextRequirements.model_construct(block_types=[])),
     "workflow_nodes_requirements_shape"),
    (_node(node_ref="c", node_type="llm_call", task_type="summarize",
           context_requirements=ContextRequirements(block_types=["system_prompt"])),
     "workflow_nodes_requirements_only_on_compose"),
    (_node(node_ref="d", node_type="context_compose", config={"block_types": ["system_prompt"]}),
     "workflow_nodes_config_without_block_types"),
    # 023, CHY-145
    (_node(node_ref="e", node_type="llm_call"),
     "workflow_nodes_llm_call_has_task_type"),
    (_node(node_ref="f", node_type="llm_call", task_type="   "),
     "workflow_nodes_llm_call_has_task_type"),
    (_node(node_ref="g", node_type="tool_call", task_type="summarize"),
     "workflow_nodes_task_type_only_on_llm_call"),
    (_node(node_ref="h", node_type="llm_call", task_type="summarize",
           config={"model": "gemma3-4b"}),
     "workflow_nodes_config_without_model"),
    (_node(node_ref="i", node_type="llm_call", task_type="summarize",
           config={"task_type": "x"}),
     "workflow_nodes_config_without_task_type"),
], ids=lambda v: v if isinstance(v, str) else v.node_ref)
async def test_the_table_refuses_and_the_refusal_is_a_sentence(pool, workflow, node, constraint):
    with pytest.raises(NodeNotStorable) as refused:
        await _insert(pool, workflow, node)
    assert refused.value.constraint == constraint, (
        f"the database refused with {refused.value.constraint!r}, the test "
        f"expected {constraint!r}"
    )
    assert constraint in NodeNotStorable.REASONS, (
        "refused by a constraint the translation has no sentence for"
    )
    assert "cannot be stored" in str(refused.value)
