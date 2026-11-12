"""
tests/integration/test_benchmark_tables_db.py

Migration 028 and the anchor's activation check on a real database (CHY-67).

What a pool double cannot show:

  - the strict approval hands the check the row `lock_current_block` really
    selects. The unit test supplies its own row and would pass whether the SQL
    reads `content` or not; here a missing column is a KeyError
  - the constraints of 028: a score exactly when measured, the anchor version
    must exist, one measurement per pair and run, evidence is not edited
  - calibration_anchor is no longer accepted where a node asks for context
  - the new UNIQUE (block_id, version) on context_blocks

Runs and measurements are written here as the migration role (`owner_pool`):
the application only reads them until CHY-68 writes them, and the constraints
are the schema's, not the application's. The trigger is shown to refuse even the
owner; that the application may not even try is tests/integration/test_app_role_db.py.
"""
from __future__ import annotations

import json
from uuid import uuid4

import asyncpg
import pytest

from contexts.learning import service as learning
from contexts.learning.errors import AnchorNotApprovable
from contexts.repository import service as repository
from contexts.repository.schema import BlockCreate
from tests.integration._callers import caller

_ANCHOR_CHECK = repository.ActivationHooks(
    before_activation=learning.check_anchor_before_approval)

pytestmark = pytest.mark.integration

_CASE = {"task_type": "summarize", "input": "A credit file.",
         "raw_prompt": "Summarize the credit file.",
         "validators": {"required_terms": ["loan"]}}


async def _anchor(pool, redis, scope, metadata=None):
    """A calibration anchor, submitted — strict scope, so it waits for review."""
    author = caller("contributor")
    block = await repository.create_block(pool, redis, author, BlockCreate(
        scope_id=scope, block_type="calibration_anchor",
        content="The loan is approved.", metadata=metadata or _CASE))
    await repository.transition(pool, redis, author, block.block_id, "submit",
                                expected_version=1)
    return block


class TestTheStrictApproval:
    async def test_a_complete_anchor_is_approved(self, pool, redis, scope):
        block = await _anchor(pool, redis, scope)
        approved = await repository.transition(
            pool, redis, caller("reviewer"), block.block_id, "approve", expected_version=1,
            hooks=_ANCHOR_CHECK)
        assert approved.status == "active"

    async def test_an_incomplete_anchor_is_refused_and_stays_in_review(self, pool, redis, scope):
        case = {k: v for k, v in _CASE.items() if k != "raw_prompt"}
        block = await _anchor(pool, redis, scope, case)
        with pytest.raises(AnchorNotApprovable, match="raw_prompt"):
            await repository.transition(
                pool, redis, caller("reviewer"), block.block_id, "approve", expected_version=1,
                hooks=_ANCHOR_CHECK)
        assert await pool.fetchval(
            "SELECT status FROM context_blocks WHERE block_id = $1", block.block_id,
        ) == "pending_review"


async def _run(pool, status="running"):
    finished = "clock_timestamp()" if status != "running" else "NULL"
    return await pool.fetchval(
        f"INSERT INTO benchmark_runs (mode, status, finished_at) VALUES ('drift', $1, {finished}) "  # noqa: S608
        "RETURNING id", status)


async def _measure(pool, run_id, block, **kw):
    values = {"model": "phi3-mini", "variant": "composed", "outcome": "measured",
              "score": 0.75, "scores": json.dumps({"rouge_l": 0.75}), **kw}
    return await pool.execute(
        "INSERT INTO benchmark_measurements (run_id, anchor_block_id, anchor_version, "
        "model, variant, outcome, score, scores, detail) "
        "VALUES ($1, $2, $3, $4, $5, $6, $7, $8::text::jsonb, $9)",
        run_id, block.block_id, kw.get("version", block.version), values["model"],
        values["variant"], values["outcome"], values["score"], values["scores"],
        values.get("detail"),
    )


class TestMeasurements:
    async def test_an_outage_carries_no_score(self, owner_pool, pool, redis, scope):
        block, run_id = await _anchor(pool, redis, scope), await _run(owner_pool)
        await _measure(owner_pool, run_id, block, model="gemma4-e4b", outcome="unreachable",
                       score=None, scores="{}", detail="connection refused")
        with pytest.raises(asyncpg.exceptions.CheckViolationError,
                           match="score_iff_measured"):
            await _measure(owner_pool, run_id, block, model="gemma3-4b", outcome="unreachable",
                           score=0.0)

    async def test_a_measurement_carries_one(self, owner_pool, pool, redis, scope):
        block, run_id = await _anchor(pool, redis, scope), await _run(owner_pool)
        with pytest.raises(asyncpg.exceptions.CheckViolationError,
                           match="score_iff_measured"):
            await _measure(owner_pool, run_id, block, score=None)

    async def test_the_anchor_version_must_exist(self, owner_pool, pool, redis, scope):
        block, run_id = await _anchor(pool, redis, scope), await _run(owner_pool)
        with pytest.raises(asyncpg.exceptions.ForeignKeyViolationError,
                           match="anchor_version"):
            await _measure(owner_pool, run_id, block, version=7)

    async def test_one_measurement_per_pair_and_run(self, owner_pool, pool, redis, scope):
        block, run_id = await _anchor(pool, redis, scope), await _run(owner_pool)
        await _measure(owner_pool, run_id, block)
        with pytest.raises(asyncpg.exceptions.UniqueViolationError, match="once_per_run"):
            await _measure(owner_pool, run_id, block)
        await _measure(owner_pool, run_id, block, variant="raw")  # another variant is another pair

    @pytest.mark.parametrize("statement", [
        "UPDATE benchmark_measurements SET score = 1 WHERE run_id = $1",
        "DELETE FROM benchmark_measurements WHERE run_id = $1",
    ])
    async def test_evidence_is_not_edited(self, owner_pool, pool, redis, scope, statement):
        block, run_id = await _anchor(pool, redis, scope), await _run(owner_pool)
        await _measure(owner_pool, run_id, block)
        with pytest.raises(asyncpg.exceptions.IntegrityConstraintViolationError,
                           match="append-only"):
            await owner_pool.execute(statement, run_id)

    async def test_and_not_truncated(self, owner_pool):
        with pytest.raises(asyncpg.exceptions.IntegrityConstraintViolationError,
                           match="append-only"):
            await owner_pool.execute("TRUNCATE benchmark_measurements")


class TestRuns:
    async def test_a_finished_run_has_an_end_and_a_running_one_none(self, owner_pool):
        with pytest.raises(asyncpg.exceptions.CheckViolationError, match="end_matches_status"):
            await owner_pool.execute("INSERT INTO benchmark_runs (mode, status) VALUES ('drift', 'complete')")
        with pytest.raises(asyncpg.exceptions.CheckViolationError, match="end_matches_status"):
            await owner_pool.execute(
                "INSERT INTO benchmark_runs (mode, status, finished_at) "
                "VALUES ('drift', 'running', clock_timestamp())")

    async def test_read_back_through_the_service(self, owner_pool, pool, redis, scope):
        block, run_id = await _anchor(pool, redis, scope), await _run(owner_pool, "complete")
        await _measure(owner_pool, run_id, block)
        await _measure(owner_pool, run_id, block, model="gemma4-e4b", outcome="unreachable",
                       score=None, scores="{}", detail="connection refused")

        detail = await learning.get_run(owner_pool, caller("reviewer"), run_id)

        assert detail.status == "complete"
        measured, outage = detail.measurements
        assert (measured.score, measured.scores) == (0.75, {"rouge_l": 0.75})
        assert (measured.anchor_block_id, measured.anchor_version) == (block.block_id, 1)
        assert (outage.outcome, outage.score, outage.detail) == (
            "unreachable", None, "connection refused")
        listed = await learning.list_runs(pool, caller("reviewer"), limit=200, offset=0)
        assert run_id in {r.id for r in listed.items}


class TestTheSchemaAroundIt:
    async def test_a_node_may_not_ask_for_anchors(self, pool, workflow):
        with pytest.raises(asyncpg.exceptions.CheckViolationError,
                           match="requirements_shape"):
            await pool.execute(
                "INSERT INTO workflow_nodes (workflow_id, node_ref, node_type, context_requirements) "
                "VALUES ($1, 'c', 'context_compose', $2::text::jsonb)",
                workflow, json.dumps({"block_types": ["calibration_anchor"]}))

    async def test_a_node_may_still_ask_for_prompt_material(self, pool, workflow):
        """The counter-probe: a CHECK that refuses everything passes the test above."""
        await pool.execute(
            "INSERT INTO workflow_nodes (workflow_id, node_ref, node_type, context_requirements) "
            "VALUES ($1, 'c', 'context_compose', $2::text::jsonb)",
            workflow, json.dumps({"block_types": ["system_prompt", "example_io"]}))

    async def test_one_row_per_block_version(self, pool, scope):
        """Two superseded rows with the same version: idx_blocks_one_active does
        not see them (it covers current rows only), the new constraint does."""
        line = uuid4()
        insert = ("INSERT INTO context_blocks (block_id, version, superseded_by_version, "
                  "scope_id, block_type, content, status) "
                  "VALUES ($1, 5, 6, $2, 'system_prompt', 'x', 'draft')")
        await pool.execute(insert, line, scope)
        with pytest.raises(asyncpg.exceptions.UniqueViolationError,
                           match="one_row_per_version"):
            await pool.execute(insert, line, scope)
