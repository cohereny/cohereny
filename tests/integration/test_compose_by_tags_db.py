"""
tests/integration/test_compose_by_tags_db.py

Composition by tag on a real PostgreSQL and a real Redis (CHY-178, migration 046).

tests/test_compose_by_tags.py checks where the filter sits in the query. What
only the database and the approval path show:

- only blocks that carry every tag named are composed; without tags nothing
  changes, and the block type still filters
- the version decides, not the block: after a change both versions are active
  (the newest one is composed, ADR-042), and a tag the newest version dropped
  is gone — the older text does not come back for it
- a block of a parent scope is inherited by tag like any other
- two steps of one node asking for different tags get their own cache entry,
  and an approval moves the generation the entry is keyed on
- migration 046 stores a node's tags and refuses what is not a list of text;
  the CHECK of a node without tags is unchanged

The cases where 046 refuses through the service, with the sentence the caller
reads, are in test_workflow_node_checks.py.
"""
from __future__ import annotations

from uuid import UUID, uuid4

import asyncpg
import pytest

from api.v1._activation import HOOKS
from contexts.orchestration import service as orchestration
from contexts.orchestration.schema import ContextRequirements, NodeCreate
from contexts.repository import service as repository
from contexts.repository.schema import BlockCreate, BlockPatch, ComposeRequest
from kernel.schema import scope_label
from tests.integration._callers import caller

pytestmark = pytest.mark.integration

RISK, VOTE = "banking:risiko", "banking:votum"


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


async def _direct(pool, cache, scope: UUID, content: str, tags: list[str],
                  block_type: str = "system_prompt") -> UUID:
    """A block active at once: a project scope reviews directly (ADR-042)."""
    out = await repository.create_block(
        pool, cache, caller("contributor"),
        BlockCreate(scope_id=scope, block_type=block_type, content=content, tags=tags),
        hooks=HOOKS)
    assert out.status == "active"
    return out.block_id


async def _approved(pool, cache, scope: UUID, content: str, tags: list[str]) -> UUID:
    """A block through review: written, submitted, approved by somebody else."""
    author = caller("contributor")
    created = await repository.create_block(
        pool, cache, author,
        BlockCreate(scope_id=scope, block_type="system_prompt", content=content, tags=tags),
        hooks=HOOKS)
    await repository.transition(pool, cache, author, created.block_id, "submit",
                                expected_version=1)
    approval = await repository.approve(pool, cache, caller("reviewer"), created.block_id,
                                        expected_version=1, hooks=HOOKS)
    assert approval.block.status == "active"
    return created.block_id


async def _compose(pool, cache, scope: UUID, tags: list[str] | None, *,
                   node_ref: str = "prompt", block_types=("system_prompt",)):
    return await repository.compose_context(pool, cache, ComposeRequest(
        scope_id=scope, workflow_id=_WORKFLOW, node_ref=node_ref,
        block_types=list(block_types), tags=tags))


async def _composed(pool, cache, scope: UUID, tags: list[str] | None, **kw) -> dict[UUID, int]:
    """Which blocks a step asking for these tags receives, at which version."""
    composed = await _compose(pool, cache, scope, tags, **kw)
    return {b.block_id: b.version for b in composed.blocks}


#: One workflow for every composition here: the cache key carries it, and the
#: claims are about the parts of the key that differ.
_WORKFLOW = uuid4()


# ── which blocks ──────────────────────────────────────────────────────────────

class TestSelection:
    async def test_only_blocks_with_every_named_tag(self, pool, cache, scope):
        project = await _below(pool, scope, "project")
        risk = await _direct(pool, cache, project, "Stufe das Risiko ein.", [RISK])
        vote = await _direct(pool, cache, project, "Entwirf das Votum.", [VOTE])
        both = await _direct(pool, cache, project, "Antworte auf Deutsch.", [RISK, VOTE])
        untagged = await _direct(pool, cache, project, "Sei knapp.", [])

        assert set(await _composed(pool, cache, project, [RISK])) == {risk, both}
        assert set(await _composed(pool, cache, project, [RISK, VOTE])) == {both}
        assert set(await _composed(pool, cache, project, ["banking:unbekannt"])) == set()
        # Counter-probe: without tags, everything of the type, as before 046.
        assert set(await _composed(pool, cache, project, None)) == {risk, vote, both, untagged}

    async def test_the_block_type_still_filters(self, pool, cache, scope):
        project = await _below(pool, scope, "project")
        prompt = await _direct(pool, cache, project, "Stufe das Risiko ein.", [RISK])
        await _direct(pool, cache, project, "Die Bank heißt Musterbank.", [RISK],
                      block_type="user_context")

        assert set(await _composed(pool, cache, project, [RISK])) == {prompt}


class TestTheVersion:
    async def test_a_tag_the_newest_version_dropped_is_gone(self, pool, cache, scope):
        project = await _below(pool, scope, "project")
        block = await _direct(pool, cache, project, "Fassung eins.", [RISK])
        assert await _composed(pool, cache, project, [RISK]) == {block: 1}

        out, superseded = await repository.patch_block(
            pool, cache, caller("contributor"), block,
            BlockPatch(content="Fassung zwei.", tags=[VOTE], change_reason="anderer Schritt"),
            hooks=HOOKS)
        assert superseded and out.status == "active"
        # Both versions are active — the older one still carries the tag. This
        # is the case a filter inside DISTINCT ON would get wrong.
        assert await pool.fetchval(
            "SELECT count(*) FROM context_blocks "
            "WHERE block_id = $1 AND status = 'active' AND $2 = ANY(tags)", block, RISK) == 1
        assert await pool.fetchval(
            "SELECT count(*) FROM context_blocks WHERE block_id = $1 AND status = 'active'",
            block) == 2

        assert await _composed(pool, cache, project, [RISK]) == {}
        assert await _composed(pool, cache, project, [VOTE]) == {block: 2}

    async def test_an_unreviewed_version_does_not_move_the_choice(self, pool, cache, scope):
        """In a strict scope the change waits for review, and until then the
        step keeps the version a reviewer approved — tag and text."""
        block = await _approved(pool, cache, scope, "Fassung eins.", [RISK])
        draft, _ = await repository.patch_block(
            pool, cache, caller("contributor"), block,
            BlockPatch(content="Fassung zwei.", tags=[VOTE]), hooks=HOOKS)
        assert draft.status == "draft"

        composed = await _compose(pool, cache, scope, [RISK])
        assert composed.block_versions == {str(block): 1}
        assert "Fassung eins." in composed.as_prompt()
        assert await _composed(pool, cache, scope, [VOTE]) == {}


class TestTheScopeChain:
    async def test_a_parent_scopes_block_is_inherited_by_tag(self, pool, cache, scope):
        inherited = await _approved(pool, cache, scope, "Vorgabe der Bank.", [RISK])
        project = await _below(pool, scope, "project")
        own = await _direct(pool, cache, project, "Vorgabe des Projekts.", [RISK])
        await _direct(pool, cache, project, "Gehört zum Votum.", [VOTE])

        composed = await _compose(pool, cache, project, [RISK])
        assert [b.block_id for b in composed.blocks] == [inherited, own], (
            "parent first, as composition orders by depth")


class TestTheCache:
    async def test_two_tag_sets_on_one_node_are_two_entries(self, pool, cache, scope):
        project = await _below(pool, scope, "project")
        risk = await _direct(pool, cache, project, "Stufe das Risiko ein.", [RISK])
        vote = await _direct(pool, cache, project, "Entwirf das Votum.", [VOTE])

        first = await _compose(pool, cache, project, [RISK])
        again = await _compose(pool, cache, project, [RISK])
        other = await _compose(pool, cache, project, [VOTE])

        assert not first.cache_hit and again.cache_hit
        assert not other.cache_hit, "a key without the tags would have served the risk prompt"
        assert {b.block_id for b in other.blocks} == {vote}
        assert {b.block_id for b in again.blocks} == {risk}

    async def test_an_approval_moves_the_entry(self, pool, cache, scope):
        """The generation of the scope is part of the key; approving a new
        version bumps it, so the next composition reads the database again."""
        block = await _approved(pool, cache, scope, "Fassung eins.", [RISK])
        assert (await _compose(pool, cache, scope, [RISK])).block_versions == {str(block): 1}
        assert (await _compose(pool, cache, scope, [RISK])).cache_hit

        author, reviewer = caller("contributor"), caller("reviewer")
        await repository.patch_block(pool, cache, author, block,
                                     BlockPatch(content="Fassung zwei."), hooks=HOOKS)
        await repository.transition(pool, cache, author, block, "submit", expected_version=2)
        await repository.approve(pool, cache, reviewer, block, expected_version=2, hooks=HOOKS)

        after = await _compose(pool, cache, scope, [RISK])
        assert not after.cache_hit
        assert after.block_versions == {str(block): 2}
        assert "Fassung zwei." in after.as_prompt()


# ── the column (migration 046) ────────────────────────────────────────────────

class TestTheColumn:
    async def test_a_nodes_tags_are_stored_and_come_back(self, pool, workflow):
        wanted = ContextRequirements(block_types=["system_prompt"], tags=[RISK])
        node = await orchestration.add_node(
            pool, caller("contributor"), workflow,
            NodeCreate(node_ref="prompt_risiko", node_type="context_compose",
                       context_requirements=wanted))
        assert node.context_requirements == wanted
        assert await pool.fetchval(
            "SELECT context_requirements -> 'tags' FROM workflow_nodes WHERE id = $1",
            node.id) == [RISK]

    async def test_without_tags_the_column_has_no_tags_key(self, pool, workflow):
        """Not `"tags": null` — which the CHECK refuses, so every compose node
        without tags would have been unstorable."""
        node = await orchestration.add_node(
            pool, caller("contributor"), workflow,
            NodeCreate(node_ref="prompt", node_type="context_compose",
                       context_requirements=ContextRequirements(block_types=["system_prompt"])))
        assert await pool.fetchval(
            "SELECT context_requirements ? 'tags' FROM workflow_nodes WHERE id = $1",
            node.id) is False

    @pytest.mark.parametrize("requirements", [
        '{"block_types": ["system_prompt"], "tags": null}',
        '{"block_types": ["system_prompt"], "tags": [7]}',
        '{"block_types": ["system_prompt"], "tags": ["banking:risiko", null]}',
        '{"block_types": ["system_prompt"], "tags": "banking:risiko"}',
        '{"block_types": ["system_prompt"], "tags": {"banking": "risiko"}}',
        '{"block_types": ["system_prompt"], "labels": ["banking:risiko"]}',
    ], ids=["null", "number", "null-in-list", "text", "object", "other-key"])
    async def test_what_is_no_list_of_text_is_unstorable(self, pool, workflow, requirements):
        """For rows that do not pass through ContextRequirements."""
        with pytest.raises(asyncpg.CheckViolationError, match="requirements_shape"):
            await pool.execute(
                "INSERT INTO workflow_nodes (workflow_id, node_ref, node_type, "
                "context_requirements) VALUES ($1, 'c', 'context_compose', $2::text::jsonb)",
                workflow, requirements)
