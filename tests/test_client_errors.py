"""
tests/test_client_errors.py

A caller's mistake is a 4xx, never a 500 (CHY-165).

Schemathesis found five classes on its first run against the real app, each
answering 500 for input that was the caller's to fix. The five, and where each
is now refused:

    unbounded integer        in the schema (`le=PG_INT_MAX`), so the bound is
                             in the contract and a client can read it
    unknown scope            translated by the store: ScopeNotFound, 404
    incoherent governance    translated by the store: BlockNotStorable, 422
    NUL in text / in JSONB   one handler in the transport, 422 — it can arrive
                             in any field and no constraint name identifies it

These drive the API with doubles that raise what PostgreSQL raises. That the
database really raises it is in tests/integration/test_client_errors_db.py.
"""
from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import asyncpg
import pytest

from kernel.schema import PG_INT_MAX
from tests.conftest import actor_row, client_for, make_pool

_TOO_BIG = PG_INT_MAX + 1


# ── unbounded integers ────────────────────────────────────────────────────────

@pytest.mark.parametrize("url", [
    f"/v1/blocks?page={_TOO_BIG}",
    f"/v1/questions?offset={_TOO_BIG}",
    f"/v1/audit-log?offset={_TOO_BIG}",
])
def test_an_integer_beyond_the_column_is_refused_before_the_database(url):
    pool = make_pool()
    with client_for(pool) as client:
        response = client.get(url)
    assert response.status_code == 422, response.text
    assert not pool.fetch.called and not pool.fetchrow.called, (
        "the value reached the database — the bound is not where it should be"
    )


def test_the_bound_itself_is_accepted():
    """Counter-probe: `le`, not `lt`. PG_INT_MAX fits the column."""
    with client_for(make_pool()) as client:
        assert client.get(f"/v1/questions?offset={PG_INT_MAX}").status_code != 422


@pytest.mark.parametrize(("url", "body"), [
    (f"/v1/blocks/{uuid4()}/approve", {"version": _TOO_BIG}),
    (f"/v1/workflows/{uuid4()}/nodes",
     {"node_ref": "s", "node_type": "sub_workflow",
      "target_workflow_id": str(uuid4()), "target_workflow_version": _TOO_BIG}),
    (f"/v1/workflows/{uuid4()}/connections",
     {"source_node_ref": "a", "target_node_ref": "b", "loop_limit": _TOO_BIG}),
])
def test_an_integer_in_a_body_beyond_the_column_is_refused(url, body):
    with client_for(make_pool(actor=actor_row(role="admin"))) as client:
        response = client.post(url, json=body)
    assert response.status_code == 422, response.text


# ── what the store translates ─────────────────────────────────────────────────

def _raising_on_insert(table: str, exc: Exception):
    """A pool that raises on one INSERT and answers everything else as before.

    Delegating matters: the handler resolves the caller first, and a double
    that swallowed that lookup made the first draft of these tests fail with
    "actor record inconsistent" — green or red for a reason that had nothing
    to do with the insert."""
    pool = make_pool(actor=actor_row(role="architect"))
    for target in (pool, pool.conn):
        original = target.fetchrow.side_effect

        async def fetchrow(sql, *args, _original=original):
            if f"INSERT INTO {table}" in sql:
                raise exc
            return await _original(sql, *args)

        target.fetchrow = AsyncMock(side_effect=fetchrow)
    return pool


def _violation(cls, constraint: str):
    exc = cls("refused")
    exc.constraint_name = constraint
    return exc


def test_a_block_in_an_unknown_scope_is_a_404():
    pool = _raising_on_insert("context_blocks", _violation(
        asyncpg.exceptions.ForeignKeyViolationError, "context_blocks_scope_id_fkey"))
    scope = uuid4()
    with client_for(pool) as client:
        response = client.post("/v1/blocks", json={
            "scope_id": str(scope), "block_type": "system_prompt", "content": "x"})
    assert response.status_code == 404, response.text
    assert str(scope) in response.json()["detail"]


def test_an_incoherent_governance_claim_is_a_422_with_the_rule():
    pool = _raising_on_insert("context_blocks", _violation(
        asyncpg.exceptions.CheckViolationError, "context_blocks_governance_coherent"))
    with client_for(pool) as client:
        response = client.post("/v1/blocks", json={
            "scope_id": str(uuid4()), "block_type": "system_prompt", "content": "x"})
    assert response.status_code == 422, response.text
    assert "belong together" in response.json()["detail"]


def test_a_workflow_in_an_unknown_scope_is_a_404():
    pool = _raising_on_insert("workflow_definitions", _violation(
        asyncpg.exceptions.ForeignKeyViolationError, "workflow_definitions_scope_id_fkey"))
    with client_for(pool) as client:
        response = client.post("/v1/workflows", json={"scope_id": str(uuid4()), "name": "w"})
    assert response.status_code == 404, response.text


def test_another_foreign_key_is_not_mistaken_for_the_scope():
    """Counter-probe: only the scope constraint means 'scope not found'. A
    translation keyed on the exception type alone would call every foreign
    key a missing scope and send the caller looking in the wrong place."""
    pool = _raising_on_insert("context_blocks", _violation(
        asyncpg.exceptions.ForeignKeyViolationError, "context_blocks_author_id_fkey"))
    with client_for(pool) as client, pytest.raises(
        asyncpg.exceptions.ForeignKeyViolationError
    ):
        client.post("/v1/blocks", json={
            "scope_id": str(uuid4()), "block_type": "system_prompt", "content": "x"})


# ── NUL, anywhere ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("exc", [
    asyncpg.exceptions.CharacterNotInRepertoireError('invalid byte sequence for encoding "UTF8": 0x00'),
    asyncpg.exceptions.UntranslatableCharacterError("unsupported Unicode escape sequence"),
], ids=["text", "jsonb"])
def test_a_nul_character_is_a_422(exc):
    pool = _raising_on_insert("context_blocks", exc)
    with client_for(pool) as client:
        response = client.post("/v1/blocks", json={
            "scope_id": str(uuid4()), "block_type": "system_prompt", "content": "x\u0000y"})
    assert response.status_code == 422, response.text
    assert "NUL" in response.json()["detail"]


# ── every CHECK on context_blocks has a sentence, or a reason not to ─────────

#: Refused by the request model's Literal types before a row is written; the
#: CHECKs are the net for paths that bypass the model.
_BLOCK_CHECKS_EXPLAINED_ELSEWHERE = {
    "context_blocks_block_type_check",
    "context_blocks_privacy_impact_check",
    "context_blocks_status_check",
}


def test_every_block_check_is_explained_or_exempted():
    """The same two-way hold NodeNotStorable has (CHY-145). A new CHECK on
    context_blocks without a sentence would still answer 422 — with the DDL
    name instead of the rule."""
    import re
    from pathlib import Path

    from contexts.repository.errors import BlockNotStorable

    sql = "\n".join(p.read_text(encoding="utf-8") for p in sorted(
        (Path(__file__).resolve().parents[1] / "migrations").glob("[0-9]*.sql")))
    declared = set(re.findall(r"ADD CONSTRAINT (context_blocks_\w+)\s+CHECK", sql))
    unexplained = declared - set(BlockNotStorable.REASONS) - _BLOCK_CHECKS_EXPLAINED_ELSEWHERE
    assert not unexplained, f"CHECKs on context_blocks with no sentence: {sorted(unexplained)}"
    stale = set(BlockNotStorable.REASONS) - declared
    assert not stale, f"REASONS explains constraints no migration creates: {sorted(stale)}"


# ── found on 25.09.2026, on the first run with everything merged ──────────────

class _FailingConn:
    """Stands in for a pool or connection whose single statement fails."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    async def fetchrow(self, *_args):
        raise self._exc


async def test_an_incoherent_claim_on_a_draft_edit_is_block_not_storable():
    """PATCH on a draft took the in-place path, and only the insert was
    translated. `privacy_impact` without a `governance_key` answered 500."""
    from contexts.repository import store
    from contexts.repository.errors import BlockNotStorable

    conn = _FailingConn(_violation(
        asyncpg.exceptions.CheckViolationError, "context_blocks_governance_coherent"))
    with pytest.raises(BlockNotStorable) as refused:
        await store.edit_draft(conn, uuid4(), {"privacy_impact": "permissive"})
    assert refused.value.constraint == "context_blocks_governance_coherent"


def _sub_workflow(target):
    from contexts.orchestration.schema import NodeCreate
    return NodeCreate(node_ref="sub", node_type="sub_workflow",
                      target_workflow_id=target, target_workflow_version=1)


async def test_a_sub_workflow_pointing_nowhere_is_target_workflow_not_found():
    from contexts.orchestration import store
    from contexts.orchestration.errors import TargetWorkflowNotFound

    target = uuid4()
    body = _sub_workflow(target)
    conn = _FailingConn(_violation(
        asyncpg.exceptions.ForeignKeyViolationError, "workflow_nodes_target_workflow_id_fkey"))
    with pytest.raises(TargetWorkflowNotFound) as refused:
        await store.insert_node(conn, uuid4(), body)
    assert str(target) in str(refused.value)


async def test_another_node_foreign_key_is_not_called_a_missing_target():
    """Counter-probe: keyed on the constraint, not on the exception type. The
    path workflow's key must not be reported as a missing sub_workflow target."""
    from contexts.orchestration import store

    conn = _FailingConn(_violation(
        asyncpg.exceptions.ForeignKeyViolationError, "workflow_nodes_workflow_id_fkey"))
    with pytest.raises(asyncpg.exceptions.ForeignKeyViolationError):
        await store.insert_node(conn, uuid4(), _sub_workflow(uuid4()))


def test_a_missing_target_is_a_404_that_names_it(monkeypatch):
    from contexts.orchestration import service
    from contexts.orchestration.errors import TargetWorkflowNotFound

    target = uuid4()

    async def add_node(*_a, **_k):
        raise TargetWorkflowNotFound(target)

    monkeypatch.setattr(service, "add_node", add_node)
    with client_for(make_pool(actor=actor_row(role="architect"))) as client:
        response = client.post(f"/v1/workflows/{uuid4()}/nodes", json={
            "node_ref": "sub", "node_type": "sub_workflow",
            "target_workflow_id": str(target), "target_workflow_version": 1})
    assert response.status_code == 404, response.text
    assert str(target) in response.json()["detail"]
