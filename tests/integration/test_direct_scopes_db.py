"""
tests/integration/test_direct_scopes_db.py

The direct path of ADR-042 on a real database (CHY-65).

tests/test_direct_scopes.py shows what the service asks for. What only
PostgreSQL can show:

  - migration 013 accepts the two steps in one transaction — draft →
    pending_review → active — and still refuses the shortcut
  - a refused activation takes the insert with it. With a double the rollback
    is whatever the double does; here it is the database's, and a draft left
    behind by a refused direct write would be a block nobody asked for
  - a governance claim can ride along with an edit, because the new version is
    still a draft when the claim is written (migration 016 freezes it after)

The acceptance criteria of CHY-65, one test each where they are about data —
promote (criterion 3) included.
"""
from __future__ import annotations

from uuid import UUID, uuid4

import asyncpg
import pytest

from contexts.repository import service as repository
from contexts.repository.errors import PrivacyRatchetViolation
from contexts.repository.schema import BlockCreate, BlockPatch, BlockPromote
from kernel.audit import (
    ACTION_BLOCK_APPROVED,
    ACTION_BLOCK_PROMOTED,
    ACTION_BLOCK_SUBMITTED,
    ACTION_BLOCK_SUPERSEDED,
)
from kernel.identity import resolve_actor
from kernel.schema import scope_label
from tests.integration._callers import caller

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

_KEY = "cloud_routing"


async def _child(pool, parent: UUID, node_type: str) -> UUID:
    scope_id = uuid4()
    parent_path = await pool.fetchval(
        "SELECT scope_path::text FROM scope_nodes WHERE id = $1", parent)
    await pool.execute(
        "INSERT INTO scope_nodes (id, scope_path, node_type, name, parent_id) "
        "VALUES ($1, $2::ltree, $3, 'it', $4)",
        scope_id, f"{parent_path}.{scope_label(node_type, scope_id)}", node_type, parent,
    )
    return scope_id


async def _audit(pool, block_id: UUID) -> list[tuple[str, str | None]]:
    """The block's entries, oldest first — by the key the audit endpoint uses.

    Ordered by created_at and id, as kernel/audit.py reads them (reversed). Not
    by the payload's from/to states: a trail that is only in order once you
    reconstruct it is not what a reader of /v1/audit-log gets (migration 026).
    """
    rows = await pool.fetch(
        "SELECT action, payload->>'mode' AS mode FROM audit_log "
        "WHERE target_ref = $1 ORDER BY created_at, id",
        f"block:{block_id}",
    )
    return [(r["action"], r["mode"]) for r in rows]


def _block(scope_id: UUID, **kw) -> BlockCreate:
    return BlockCreate(scope_id=scope_id, block_type="system_prompt", content="x", **kw)


async def test_a_block_in_a_personal_scope_is_active_at_once(pool, redis, scope):
    """Acceptance criterion 1."""
    personal = await _child(pool, scope, "personal")

    out = await repository.create_block(pool, redis, caller("contributor"), _block(personal))

    assert out.status == "active"
    assert await _audit(pool, out.block_id) == [
        (ACTION_BLOCK_SUBMITTED, "direct"), (ACTION_BLOCK_APPROVED, "direct"),
    ]
    assert redis.bumped, "a block went live and the composed cache was not told"


async def test_a_block_in_a_team_scope_waits_for_review(pool, redis, scope):
    """Acceptance criterion 2 — as before ADR-042."""
    team = await _child(pool, scope, "team")

    out = await repository.create_block(pool, redis, caller("contributor"), _block(team))

    assert out.status == "draft"
    assert await _audit(pool, out.block_id) == []
    assert redis.bumped == []


async def test_the_shortcut_is_still_refused(pool, redis, scope):
    """The direct path goes through pending_review because 013 still says so.
    If this ever passed, the state machine would have been loosened, not used."""
    team = await _child(pool, scope, "team")
    out = await repository.create_block(pool, redis, caller("contributor"), _block(team))

    with pytest.raises(asyncpg.exceptions.IntegrityConstraintViolationError,
                       match="draft → active is not a valid transition"):
        await pool.execute(
            "UPDATE context_blocks SET status = 'active' WHERE block_id = $1", out.block_id)


class TestTheRatchetOnTheDirectPath:
    """Acceptance criterion 4, with its counter-probe."""

    async def _restrictive_org(self, pool, redis, scope) -> None:
        author, reviewer = caller("contributor"), caller("reviewer")
        claim = await repository.create_block(pool, redis, author, _block(
            scope, governance_key=_KEY, privacy_impact="restrictive"))
        await repository.transition(pool, redis, author, claim.block_id, "submit",
                                    expected_version=1)
        await repository.transition(pool, redis, reviewer, claim.block_id, "approve",
                                    expected_version=1)

    async def test_a_loosening_claim_is_refused_and_leaves_nothing(self, pool, redis, scope):
        await self._restrictive_org(pool, redis, scope)
        personal = await _child(pool, scope, "personal")
        # Provisioned first: a new caller's first request writes
        # actor.provisioned, outside the block's transaction and rightly kept.
        who = caller("contributor")
        await resolve_actor(pool, who)
        entries_before = await pool.fetchval("SELECT count(*) FROM audit_log")

        with pytest.raises(PrivacyRatchetViolation):
            await repository.create_block(pool, redis, who, _block(
                personal, governance_key=_KEY, privacy_impact="permissive"))

        assert await pool.fetchval(
            "SELECT count(*) FROM context_blocks WHERE scope_id = $1", personal) == 0
        assert await pool.fetchval("SELECT count(*) FROM audit_log") == entries_before

    async def test_a_tightening_claim_goes_live(self, pool, redis, scope):
        await self._restrictive_org(pool, redis, scope)
        personal = await _child(pool, scope, "personal")

        out = await repository.create_block(pool, redis, caller("contributor"), _block(
            personal, governance_key=_KEY, privacy_impact="restrictive"))

        assert out.status == "active"


class TestEditingInADirectScope:
    async def test_the_new_version_goes_live_next_to_the_old_one(self, pool, redis, scope):
        """The predecessor stays active and superseded — the newest active
        version is what composes, as with a strict approval."""
        project = await _child(pool, scope, "project")
        author = caller("contributor")
        first = await repository.create_block(pool, redis, author, _block(project))

        out, superseded = await repository.patch_block(
            pool, redis, author, first.block_id, BlockPatch(content="y"))

        assert (superseded, out.version, out.status) == (True, 2, "active")
        rows = await pool.fetch(
            "SELECT version, status, superseded_by_version FROM context_blocks "
            "WHERE block_id = $1 ORDER BY version", first.block_id)
        assert [tuple(r) for r in rows] == [(1, "active", 2), (2, "active", None)]
        # Three entries in one transaction — the supersede from inside
        # cc_supersede_block, then the two steps — in the order they happened.
        assert [a for a, _ in await _audit(pool, first.block_id)][2:] == [
            ACTION_BLOCK_SUPERSEDED, ACTION_BLOCK_SUBMITTED, ACTION_BLOCK_APPROVED,
        ]

    async def test_a_claim_rides_along(self, pool, redis, scope):
        """Written on the new draft, before the activation freezes it."""
        project = await _child(pool, scope, "project")
        author = caller("contributor")
        first = await repository.create_block(pool, redis, author, _block(project))

        out, _ = await repository.patch_block(
            pool, redis, author, first.block_id,
            BlockPatch(content="y", governance_key=_KEY, privacy_impact="restrictive"))

        assert (out.status, out.governance_key, out.privacy_impact) == (
            "active", _KEY, "restrictive")


class TestPromote:
    """Acceptance criterion 3: from a project to its team, as a draft, and the
    original untouched — on the database, where "untouched" means the rows."""

    async def test_a_proven_block_becomes_a_draft_in_the_team(self, pool, redis, scope):
        team = await _child(pool, scope, "team")
        project = await _child(pool, team, "project")
        author, reviewer = caller("contributor"), caller("reviewer")
        original = await repository.create_block(pool, redis, author, _block(project))
        before = [tuple(r) for r in await pool.fetch(
            "SELECT id, version, status, content, superseded_by_version FROM context_blocks "
            "WHERE block_id = $1", original.block_id)]

        copy = await repository.promote_block(
            pool, author, original.block_id, BlockPromote(target_scope_id=team))

        assert (copy.scope_id, copy.status, copy.version) == (team, "draft", 1)
        assert copy.block_id != original.block_id
        after = [tuple(r) for r in await pool.fetch(
            "SELECT id, version, status, content, superseded_by_version FROM context_blocks "
            "WHERE block_id = $1", original.block_id)]
        assert after == before, "promote changed the original"
        assert await _audit(pool, copy.block_id) == [(ACTION_BLOCK_PROMOTED, None)]

        # And from here on it is an ordinary team block: review applies.
        await repository.transition(pool, redis, author, copy.block_id, "submit",
                                    expected_version=1)
        approved = await repository.transition(pool, redis, reviewer, copy.block_id,
                                               "approve", expected_version=1)
        assert approved.status == "active"

    async def test_the_claim_travels_and_is_judged_where_it_lands(self, pool, redis, scope):
        """A permissive claim that was fine in the project meets a restrictive
        one on the team's chain at approval — the ratchet of the new scope."""
        team = await _child(pool, scope, "team")
        project = await _child(pool, team, "project")
        author, reviewer = caller("contributor"), caller("reviewer")
        loose = await repository.create_block(pool, redis, author, _block(
            project, governance_key=_KEY, privacy_impact="permissive"))
        strict = await repository.create_block(pool, redis, author, _block(
            team, governance_key=_KEY, privacy_impact="restrictive"))
        await repository.transition(pool, redis, author, strict.block_id, "submit",
                                    expected_version=1)
        await repository.transition(pool, redis, reviewer, strict.block_id, "approve",
                                    expected_version=1)

        copy = await repository.promote_block(
            pool, author, loose.block_id, BlockPromote(target_scope_id=team))
        assert (copy.governance_key, copy.privacy_impact) == (_KEY, "permissive")

        await repository.transition(pool, redis, author, copy.block_id, "submit",
                                    expected_version=1)
        with pytest.raises(PrivacyRatchetViolation):
            await repository.transition(pool, redis, reviewer, copy.block_id, "approve",
                                        expected_version=1)
