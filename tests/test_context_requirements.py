"""
tests/test_context_requirements.py

`context_requirements` — the typed column that replaced `config["block_types"]`
(CHY-134, migration 022).

The whitepaper carried the field from v2.x, and there was no column, no code
and no contract entry. That is the same gap as `condition TEXT` against
`condition JSONB` before CHY-130, and it stays invisible for exactly as long as
nothing evaluates the field. Since CHY-133 something evaluates it.

What this file cannot see
-------------------------
The two CHECK constraints of migration 022 are **not** exercised here. The pool
is a double; no Postgres parses the DDL, and a test that asserted "the database
refuses this" against a mock would be asserting that the mock refuses it. What
is testable here is the layer above: that a refusal arriving from the table
becomes a sentence with a status code, and that the request model refuses what
it can refuse on its own.

The constraints themselves were verified against a real Postgres 16 before the
merge: all 21 migration files applied into a scratch database, ten probes —
three accepted, seven refused, each with the constraint name it should carry
(07.09.2026, then the database dropped). The allow-list inside them is held
against COMPOSABLE_BLOCK_TYPES by tests/test_status_vocabularies.py.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from asyncpg.exceptions import CheckViolationError
from pydantic import ValidationError

from contexts.orchestration.errors import NodeNotStorable
from contexts.orchestration.schema import ContextRequirements, NodeCreate
from tests.conftest import actor_row, client_for, make_pool
from tests.test_workflows_api import _WF, _node_row, _workflow_row


# ── the model ─────────────────────────────────────────────────────────────────

class TestTheModel:
    def test_a_list_of_types_is_accepted(self):
        req = ContextRequirements(block_types=["system_prompt", "user_context"])
        assert req.block_types == ["system_prompt", "user_context"]

    def test_the_empty_list_is_refused(self):
        """`[]` would mean the opposite of what it says.

        `active_blocks_in_scopes` reads a falsy filter as "none given", i.e. as
        *everything*. An author writing `[]` means "nothing" and would get the
        whole scope — so the value has no business being storable.
        """
        with pytest.raises(ValidationError):
            ContextRequirements(block_types=[])

    def test_an_unknown_key_is_refused(self):
        """`extra="forbid"`, and the reason is the field's own history.

        `required_tags` and `exclude_tags` stand next to `required_block_types`
        in arc42 §5.6 and nothing composes by tag. Accepting them here would
        rebuild in a typed column the thing this ticket removes from `config`:
        a key that is stored, read by nobody, and looks like it works.
        """
        with pytest.raises(ValidationError):
            ContextRequirements(block_types=["system_prompt"], required_tags=["de"])


class TestTheOldSpelling:
    def test_block_types_in_the_config_is_refused_not_ignored(self):
        with pytest.raises(ValidationError) as exc:
            NodeCreate(node_ref="compose", node_type="context_compose",
                       config={"block_types": ["system_prompt"]})
        assert "context_requirements" in str(exc.value)

    def test_other_config_keys_are_untouched(self):
        """The counter-probe. A validator that refused every config would pass
        the test above and break every node that still needs `prompt`.

        This used `model` as its example until CHY-145 moved that key out too —
        a counter-probe has to use a key that is still meant to be there."""
        node = NodeCreate(node_ref="ask", node_type="llm_call", task_type="summarize",
                          config={"prompt": "hi", "temperature": 0.2})
        assert node.config["prompt"] == "hi"


# ── through the API ───────────────────────────────────────────────────────────

class TestThroughTheApi:
    def _stored(self, pool) -> tuple:
        """The arguments the INSERT was called with."""
        for call in pool.conn.fetchrow.call_args_list:
            if "INSERT INTO workflow_nodes" in call.args[0]:
                return call.args
        never_inserted = "no node was inserted"
        raise AssertionError(never_inserted)

    def test_a_compose_node_carries_its_requirements_into_the_column(self):
        stored = _node_row("compose", "context_compose",
                           context_requirements={"block_types": ["system_prompt"]})
        pool = make_pool(
            actor=actor_row(role="contributor"),
            conn_fetchrow=[_workflow_row(), None, stored],
        )
        with client_for(pool) as client:
            response = client.post(f"/v1/workflows/{_WF}/nodes", json={
                "node_ref": "compose", "node_type": "context_compose",
                "context_requirements": {"block_types": ["system_prompt"]},
            })
        assert response.status_code == 201, response.text
        assert response.json()["context_requirements"] == {
            "block_types": ["system_prompt"]
        }
        # Parameter $8 of the INSERT, cast to jsonb. Asserted on the wire and
        # not only on the response, because the response is built from the row
        # the double handed back — which would look identical if the value
        # never reached the query.
        assert json.loads(self._stored(pool)[8]) == {"block_types": ["system_prompt"]}

    def test_a_node_without_requirements_stores_null_not_an_empty_object(self):
        """NULL and `{}` would be two spellings of "no requirements", and the
        CHECK in 022 refuses the second one anyway."""
        pool = make_pool(
            actor=actor_row(role="contributor"),
            conn_fetchrow=[_workflow_row(), None, _node_row("run", "tool_call")],
        )
        with client_for(pool) as client:
            response = client.post(f"/v1/workflows/{_WF}/nodes",
                                   json={"node_ref": "run", "node_type": "tool_call"})
        assert response.status_code == 201, response.text
        assert self._stored(pool)[8] is None

    @pytest.mark.parametrize(
        ("constraint", "expected"),
        [
            ("workflow_nodes_requirements_shape", "composed"),
            ("workflow_nodes_requirements_only_on_compose", "context_compose"),
            ("workflow_nodes_config_without_block_types", "CHY-134"),
        ],
    )
    def test_a_refusal_by_the_table_is_a_sentence_and_a_422(self, constraint, expected):
        """Not a 500.

        The rules these constraints carry cannot live in the request model:
        they span two columns, or they use the repository's block-type
        vocabulary, which this context may not import (ADR-030). Arriving as a
        `CheckViolationError` they would tell the caller the server broke.
        """
        pool = make_pool(
            actor=actor_row(role="contributor"),
            conn_fetchrow=[_workflow_row(), None],
        )
        rows = pool.conn.fetchrow.side_effect

        async def _refuse_the_insert(sql, *args):
            if "INSERT INTO workflow_nodes" in sql:
                violation = CheckViolationError("violates check constraint")
                violation.constraint_name = constraint
                raise violation
            return await rows(sql, *args)

        pool.conn.fetchrow = AsyncMock(side_effect=_refuse_the_insert)
        with client_for(pool) as client:
            response = client.post(f"/v1/workflows/{_WF}/nodes", json={
                "node_ref": "compose", "node_type": "context_compose",
                "context_requirements": {"block_types": ["system_prompt"]},
            })
        assert response.status_code == 422, response.text
        assert expected in response.json()["detail"]

    def test_an_unlisted_constraint_still_names_itself(self):
        """A constraint added later must not fall back to a 500 or to silence.

        The message is worse than a written one — it names the DDL rather than
        the rule — and that is the point: the default has to be usable, or the
        next constraint arrives as a mystery.
        """
        pool = make_pool(actor=actor_row(role="contributor"),
                         conn_fetchrow=[_workflow_row(), None])
        rows = pool.conn.fetchrow.side_effect

        async def _refuse_the_insert(sql, *args):
            if "INSERT INTO workflow_nodes" in sql:
                violation = CheckViolationError("violates check constraint")
                violation.constraint_name = "workflow_nodes_something_new"
                raise violation
            return await rows(sql, *args)

        pool.conn.fetchrow = AsyncMock(side_effect=_refuse_the_insert)
        with client_for(pool) as client:
            response = client.post(f"/v1/workflows/{_WF}/nodes",
                                   json={"node_ref": "n", "node_type": "tool_call"})
        assert response.status_code == 422, response.text
        assert "workflow_nodes_something_new" in response.json()["detail"]


def test_every_constraint_the_error_explains_exists_in_the_migrations():
    """The other direction: a message about a rule nobody enforces.

    `REASONS` is written by hand, and a renamed or dropped constraint would
    leave an entry that keeps reading well and never fires again — the same
    failure mode VOCABULARIES guards against one table over.
    """
    sql = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(
            (Path(__file__).resolve().parents[1] / "migrations").glob("[0-9]*.sql")
        )
    )
    missing = [name for name in NodeNotStorable.REASONS
               if f"ADD CONSTRAINT {name}" not in sql]
    assert not missing, (
        f"NodeNotStorable.REASONS explains constraints that no migration "
        f"creates: {missing}"
    )


#: Constraints of `workflow_nodes` that need no sentence of their own. Written
#: as data with the reason, so "not needed" and "not noticed" stay apart.
_EXPLAINED_ELSEWHERE = {
    # The request model's `NodeType` Literal refuses an unknown type with a
    # 422 before any row is written; the CHECK is the net for other paths.
    "workflow_nodes_node_type_check",
}


def test_every_node_constraint_is_explained_or_exempted():
    """The direction the test above does not cover (CHY-145).

    A new CHECK on `workflow_nodes` without an entry in REASONS still answers
    422, but with the DDL name instead of the rule — and nothing would notice.
    Found by counting: the docstring said six while the table had eight, and
    CHY-145 brings it to twelve.
    """
    import re

    sql = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(
            (Path(__file__).resolve().parents[1] / "migrations").glob("[0-9]*.sql")
        )
    )
    declared = set(re.findall(r"ADD CONSTRAINT (workflow_nodes_\w+)", sql))
    unexplained = declared - set(NodeNotStorable.REASONS) - _EXPLAINED_ELSEWHERE
    assert not unexplained, (
        f"CHECKs on workflow_nodes with no sentence in NodeNotStorable.REASONS: "
        f"{sorted(unexplained)}. Add one, or exempt it with a reason."
    )
