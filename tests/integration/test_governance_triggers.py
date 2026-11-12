"""
tests/integration/test_governance_triggers.py

The guarantees that live in the database, exercised against one (CHY-124).

CHY-124 named them: *"the triggers and CHECK constraints of migrations 013, 014
and 015 cannot be set off by any mock."* Each of these is a legal or governance
promise that the code only *relies* on — the audit log cannot be rewritten
(ADR-009), a published block cannot be edited in place (ADR-009, CHY-60), an
Art. 14 decision names who decided (CHY-88), an Art. 50 record outlives the run
it describes (CHY-86). A unit test with a pool double can only show that the
code does not try to break them; whether the database would stop it has never
been run.

Plus one use case end to end: Art. 17 anonymization through the kernel, not
through SQL, because the obligation is that the person disappears and the
record of what they did does not.
"""
from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import asyncpg
import pytest

from contexts.repository import service as repository
from contexts.repository.schema import BlockCreate
from kernel.audit import target_ref, write_audit
from kernel.identity import anonymize_actor, resolve_actor
from kernel.schema import ANONYMIZED_DISPLAY_NAME
from tests.integration._callers import caller as _caller
from tests.integration._redis import Generations

pytestmark = pytest.mark.integration


# ── 011: the audit log is append-only ─────────────────────────────────────────

class TestAuditLogIsAppendOnly:
    async def _entry(self, pool):
        actor = await resolve_actor(pool, _caller("admin"))
        await write_audit(pool, action="it.probe", actor_id=actor.id,
                          target=target_ref("it", uuid4()), payload={})

    @pytest.mark.parametrize("statement", [
        "UPDATE audit_log SET action = 'rewritten'",
        "DELETE FROM audit_log",
        "TRUNCATE audit_log",
    ], ids=["update", "delete", "truncate"])
    async def test_rewriting_the_log_is_refused(self, pool, owner_pool, statement):
        """TRUNCATE is the case the DO INSTEAD NOTHING rules of 001 never
        covered — and they failed silently for the other two (CHY-31).

        Tried as the owner: the trigger refuses even the role that owns the
        table. The application role may not even try (test_app_role_db.py)."""
        await self._entry(pool)
        with pytest.raises(asyncpg.exceptions.IntegrityConstraintViolationError,
                           match="append-only"):
            await owner_pool.execute(statement)

    async def test_writing_an_entry_still_works(self, pool):
        """Counter-probe: an append-only log that refuses appends is a log
        that records nothing.

        The actor is resolved before counting: provisioning a new actor writes
        an audit entry of its own, and counting across it measured two."""
        actor = await resolve_actor(pool, _caller("admin"))
        before = await pool.fetchval("SELECT count(*) FROM audit_log")
        await write_audit(pool, action="it.probe", actor_id=actor.id,
                          target=target_ref("it", uuid4()), payload={})
        assert await pool.fetchval("SELECT count(*) FROM audit_log") == before + 1


# ── 013: a block is versioned, not edited ─────────────────────────────────────

class TestBlocksAreAppendOnly:
    async def _block(self, pool, scope):
        author = _caller("architect")
        out = await repository.create_block(pool, Generations(), author, BlockCreate(
            scope_id=scope, block_type="system_prompt", content="Antworte knapp.",
        ))
        return out.block_id

    async def test_a_block_cannot_be_deleted(self, pool, owner_pool, scope):
        """As the owner, like the audit log above: the application has no DELETE."""
        block_id = await self._block(pool, scope)
        with pytest.raises(asyncpg.exceptions.IntegrityConstraintViolationError,
                           match="append-only"):
            await owner_pool.execute("DELETE FROM context_blocks WHERE block_id = $1", block_id)

    async def test_identity_columns_are_immutable(self, pool, scope):
        """A block that could change its type after approval could turn from
        prompt material into a routing rule without passing review again."""
        block_id = await self._block(pool, scope)
        with pytest.raises(asyncpg.exceptions.IntegrityConstraintViolationError,
                           match="identity columns are immutable"):
            await pool.execute(
                "UPDATE context_blocks SET block_type = 'routing_rule' WHERE block_id = $1",
                block_id,
            )

    async def test_supersession_belongs_to_the_function(self, pool, scope):
        block_id = await self._block(pool, scope)
        with pytest.raises(asyncpg.exceptions.IntegrityConstraintViolationError,
                           match="cc_supersede_block"):
            await pool.execute(
                "UPDATE context_blocks SET superseded_by_version = 2 WHERE block_id = $1",
                block_id,
            )

    async def test_a_draft_may_still_be_edited(self, pool, scope):
        """Counter-probe: if drafts were frozen too, the three refusals above
        could pass because the trigger refuses everything."""
        block_id = await self._block(pool, scope)
        await pool.execute(
            "UPDATE context_blocks SET content = 'Antworte ausführlich.' WHERE block_id = $1",
            block_id,
        )


# ── 014, 037: an oversight decision names who decided and when (Art. 12) ──────

class TestOversightNeedsAnAuthor:
    async def _run(self, pool):
        return await pool.fetchval(
            "INSERT INTO pilot_runs (thread_id, input_file) VALUES ($1, 'it.pdf') RETURNING id",
            f"it-{uuid4().hex}",
        )

    async def test_a_decision_without_a_person_is_refused(self, system_pool):
        run_id = await self._run(system_pool)
        with pytest.raises(asyncpg.exceptions.IntegrityConstraintViolationError,
                           match="requires oversight_by and oversight_at"):
            await system_pool.execute(
                "UPDATE pilot_runs SET oversight_decision = 'approved' WHERE id = $1", run_id,
            )

    async def test_the_refusal_and_the_columns_cite_the_record_keeping_article(self, system_pool):
        """Art. 12 asks for the record, Art. 14 for the oversight (CHY-152).
        014 cited Art. 14 for the record and Art. 12(3), which concerns
        biometrics only; 037 corrects both without touching 014."""
        run_id = await self._run(system_pool)
        with pytest.raises(asyncpg.exceptions.IntegrityConstraintViolationError) as refused:
            await system_pool.execute(
                "UPDATE pilot_runs SET oversight_decision = 'approved' WHERE id = $1", run_id,
            )
        assert refused.value.hint.startswith("EU AI Act Art. 12:")
        comments = await system_pool.fetch(
            "SELECT a.attname, col_description(a.attrelid, a.attnum) AS comment "
            "FROM pg_attribute a WHERE a.attrelid = 'pilot_runs'::regclass "
            "AND a.attname IN ('oversight_by', 'oversight_at')")
        for row in comments:
            assert "Art. 12" in row["comment"] and "Art. 12(3)" not in row["comment"], row

    async def test_a_decision_with_a_person_and_a_time_is_recorded(self, system_pool):
        run_id = await self._run(system_pool)
        reviewer = await resolve_actor(system_pool, _caller("reviewer"))
        await system_pool.execute(
            "UPDATE pilot_runs SET oversight_decision = 'approved', oversight_by = $2, "
            "oversight_at = $3 WHERE id = $1",
            run_id, reviewer.id, datetime.now(UTC),
        )
        assert await system_pool.fetchval(
            "SELECT oversight_by FROM pilot_runs WHERE id = $1", run_id
        ) == reviewer.id


# ── 015: an Art. 50 record outlives its run ───────────────────────────────────

async def test_a_run_with_a_transparency_record_cannot_be_deleted(system_pool):
    """Until 015 the foreign key cascaded: deleting a run silently took the
    record that users had been told about with it."""
    run_id = await system_pool.fetchval(
        "INSERT INTO pilot_runs (thread_id, input_file) VALUES ($1, 'it.pdf') RETURNING id",
        f"it-{uuid4().hex}",
    )
    await system_pool.execute(
        "INSERT INTO transparency_records (pilot_run_id, model_used, model_disclosure) "
        "VALUES ($1, 'gemma3-4b', 'KI-generiert')",
        run_id,
    )
    with pytest.raises(asyncpg.exceptions.ForeignKeyViolationError):
        await system_pool.execute("DELETE FROM pilot_runs WHERE id = $1", run_id)


# ── 010 + kernel: Art. 17, end to end ─────────────────────────────────────────

async def test_anonymization_removes_the_person_and_keeps_the_record(pool):
    """The obligation has two halves, and each is easy to satisfy alone.

    Deleting the row removes the person and breaks the eleven foreign keys
    that record what they did (ADR-025). Keeping the row keeps the record and
    keeps the person. Both halves are asserted here, against the tables.
    """
    admin = _caller("admin")
    await resolve_actor(pool, admin)                    # the one who erases
    subject = _caller("contributor")
    person = await resolve_actor(pool, subject)
    await write_audit(pool, action="it.did_something", actor_id=person.id,
                      target=target_ref("it", uuid4()), payload={})

    result = await anonymize_actor(pool, admin, person.id)
    assert result.already_anonymized is False

    row = await pool.fetchrow(
        "SELECT idp_sub, display_name, is_anonymized FROM actors WHERE id = $1", person.id
    )
    assert row["idp_sub"] is None
    assert row["display_name"] == ANONYMIZED_DISPLAY_NAME
    assert row["is_anonymized"] is True

    # The record of what they did survives, still pointing at the same row.
    assert await pool.fetchval(
        "SELECT count(*) FROM audit_log WHERE actor_id = $1 AND action = 'it.did_something'",
        person.id,
    ) == 1
    # And the erasure itself is on record, without a trace of who it was.
    erasure = await pool.fetchrow(
        "SELECT payload FROM audit_log WHERE action = 'actor.anonymized' AND target_ref = $1",
        target_ref("actor", person.id),
    )
    assert erasure is not None
    assert subject.sub not in str(erasure["payload"])
    assert subject.username not in str(erasure["payload"])

    # The old identity no longer leads back to the person.
    again = await resolve_actor(pool, subject)
    assert again.id != person.id, "the erased identity still resolves to the erased row"
    # Nor does it lock anyone out (CHY-270): the same login is a new account
    # with the role its groups carry, and that account is on record — which is
    # why DATA-PROTECTION.md §6 has a person who leaves locked at the identity
    # provider first.
    assert again.role == "contributor"
    assert await pool.fetchval(
        "SELECT count(*) FROM audit_log WHERE action = 'actor.provisioned' AND target_ref = $1",
        target_ref("actor", again.id),
    ) == 1
