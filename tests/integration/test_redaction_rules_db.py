"""
tests/integration/test_redaction_rules_db.py

A scope's redaction rules on a real database (CHY-63 part 2, migration 032).

tests/test_redaction_rules.py hands the reading its rows. What only PostgreSQL
and the real approval path can show:

  - migration 032 lets a `redaction_rule` block be stored, and it is read for
    the model call but never composed into a prompt
  - switching a shipped rule off below the top of the tree is refused at
    approval, with the reason
  - at the top it needs a second person: the writer cannot approve it (ADR-045)
  - a `restrictive` claim on `pii_redaction` in the same scope forbids it
    (ADR-017)
"""
from __future__ import annotations

import os
from uuid import UUID, uuid4

import pytest
import redis.asyncio as aioredis

from api.v1._activation import HOOKS
from api.v1._redaction import redaction_policy_for
from contexts.repository import service as repository
from contexts.repository.errors import PrivacyRatchetViolation, SecondPersonRequired
from contexts.repository.schema import BlockCreate, ComposeRequest
from kernel.errors import RedactionRuleNotApprovable
from kernel.schema import scope_label
from tests.integration._callers import caller

pytestmark = pytest.mark.integration


async def _below(pool, parent: UUID, node_type: str) -> UUID:
    scope_id = uuid4()
    parent_path = await pool.fetchval(
        "SELECT scope_path::text FROM scope_nodes WHERE id = $1", parent)
    await pool.execute(
        "INSERT INTO scope_nodes (id, scope_path, node_type, name, parent_id) "
        "VALUES ($1, $2::ltree, $3, 'it', $4)",
        scope_id, f"{parent_path}.{scope_label(node_type, scope_id)}", node_type, parent,
    )
    return scope_id


def _rule(scope_id: UUID, metadata: dict, **kw) -> BlockCreate:
    return BlockCreate(scope_id=scope_id, block_type="redaction_rule",
                       content="so entschieden", metadata=metadata, **kw)


def _switch_off(scope_id: UUID, *categories: str) -> BlockCreate:
    return _rule(scope_id, {"disable": list(categories)},
                 governance_key="pii_redaction", privacy_impact="permissive")


async def _submitted(pool, redis, author, block: BlockCreate) -> UUID:
    created = await repository.create_block(pool, redis, author, block, hooks=HOOKS)
    await repository.transition(pool, redis, author, created.block_id, "submit",
                                expected_version=1)
    return created.block_id


@pytest.fixture
async def cache():
    """A real Redis for the one test that composes.

    The `redis` fixture counts scope generations and nothing else; composition
    reads and writes its cache, and the claim here is about the real
    composition query -- that it never hands a redaction rule over.
    """
    client = aioredis.from_url(os.environ["REDIS_URL"])
    yield client
    await client.aclose()


async def test_a_rule_is_stored_read_for_the_call_and_never_composed(
        pool, redis, cache, scope):
    author, reviewer = caller("contributor"), caller("reviewer")
    block_id = await _submitted(pool, redis, author, _rule(
        scope, {"category": "STAFF", "texts": ["Dr. Schmidt"]}))
    await repository.approve(pool, redis, reviewer, block_id, expected_version=1, hooks=HOOKS)

    team = await _below(pool, scope, "team")
    policy = await redaction_policy_for(pool, team)
    assert policy.identifiers == {"STAFF": ("Dr. Schmidt",)}

    composed = await repository.compose_context(pool, cache, ComposeRequest(scope_id=team))
    assert "so entschieden" not in composed.as_prompt()
    assert str(block_id) not in composed.block_versions


async def test_switching_off_below_the_top_is_refused_at_approval(pool, redis, scope):
    team = await _below(pool, scope, "team")
    author, reviewer = caller("contributor"), caller("reviewer")
    block_id = await _submitted(pool, redis, author, _switch_off(team, "PHONE"))

    with pytest.raises(RedactionRuleNotApprovable, match="without a parent"):
        await repository.approve(pool, redis, reviewer, block_id,
                                 expected_version=1, hooks=HOOKS)

    assert await pool.fetchval(
        "SELECT status FROM context_blocks WHERE block_id = $1", block_id) == "pending_review"
    assert "PHONE" in [r.category for r in (await redaction_policy_for(pool, team)).rules]


async def test_switching_off_at_the_top_needs_a_second_person(pool, redis, scope):
    writer, second = caller("reviewer"), caller("reviewer")
    block_id = await _submitted(pool, redis, writer, _switch_off(scope, "PHONE"))

    with pytest.raises(SecondPersonRequired):
        await repository.approve(pool, redis, writer, block_id,
                                 expected_version=1, hooks=HOOKS)
    approval = await repository.approve(pool, redis, second, block_id,
                                        expected_version=1, hooks=HOOKS)

    assert approval.four_eyes is repository.Verdict.SECOND_PERSON
    rules = [r.category for r in (await redaction_policy_for(pool, scope)).rules]
    assert "PHONE" not in rules
    assert "EMAIL" in rules


async def test_a_restrictive_claim_in_the_scope_forbids_switching_off(pool, redis, scope):
    author, reviewer, other = caller("contributor"), caller("reviewer"), caller("reviewer")
    claim = await _submitted(pool, redis, author, _rule(
        scope, {"category": "STAFF", "texts": ["Dr. Schmidt"]},
        governance_key="pii_redaction", privacy_impact="restrictive"))
    await repository.approve(pool, redis, reviewer, claim, expected_version=1, hooks=HOOKS)

    loosening = await _submitted(pool, redis, author, _switch_off(scope, "PHONE"))
    with pytest.raises(PrivacyRatchetViolation):
        await repository.approve(pool, redis, other, loosening,
                                 expected_version=1, hooks=HOOKS)

    assert "PHONE" in [r.category for r in (await redaction_policy_for(pool, scope)).rules]
