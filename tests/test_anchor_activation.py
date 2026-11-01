"""
tests/test_anchor_activation.py

A calibration anchor that could never be measured does not become active —
on either approval path (ADR-042, ADR-043, CHY-67). And benchmark runs can be
read.

Both paths, because they hand the check different rows: the explicit approve a
row locked by `lock_current_block`, the direct activation a row returned by
`RETURNING *`. The first lacked `content` until CHY-67, and an anchor's
reference answer *is* its content — tested on the direct path alone, the
strict path would have answered 500.

The pool is a double. The tables of migration 028 are in
tests/integration/test_benchmark_tables_db.py.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from tests.conftest import actor_row, client_for, make_pool

_BLOCK, _SCOPE, _ROW = uuid4(), uuid4(), uuid4()
_GOOD = {"task_type": "summarize", "input": "A credit file.",
         "raw_prompt": "Summarize the credit file.",
         "validators": {"required_terms": ["loan"]}}
_NO_RAW_PROMPT = {k: val for k, val in _GOOD.items() if k != "raw_prompt"}


def _locked(metadata: dict, status: str = "pending_review") -> dict:
    """The row `lock_current_block` returns — its columns, no more."""
    return {"id": _ROW, "block_id": _BLOCK, "version": 1, "status": status,
            "scope_id": _SCOPE, "author_id": uuid4(), "content": "The loan is approved.",
            "governance_key": None, "privacy_impact": None, "locked": False,
            "block_type": "calibration_anchor", "metadata": metadata}


def _full(metadata: dict, status: str) -> dict:
    """A row as RETURNING * gives it."""
    return {**_locked(metadata, status), "superseded_by_version": None, "tags": [],
            "created_at": "2026-10-06T09:00:00+00:00"}


class TestTheStrictPath:
    def _approve(self, metadata):
        pool = make_pool(actor=actor_row(role="reviewer"),
                         conn_fetchrow=[_locked(metadata), _full(metadata, "active")])
        with client_for(pool) as client:
            return client.post(f"/v1/blocks/{_BLOCK}/approve", json={"version": 1}), pool

    def test_an_incomplete_anchor_is_refused_with_the_reason(self):
        response, pool = self._approve(_NO_RAW_PROMPT)
        assert response.status_code == 422, response.text
        assert "raw_prompt" in response.json()["detail"]
        assert not [c for c in pool.conn.fetchrow.call_args_list
                    if c.args[0].startswith("UPDATE context_blocks SET status")]

    def test_a_complete_anchor_is_approved(self):
        """The counter-probe: a check that refuses every anchor passes the test above."""
        response, _ = self._approve(_GOOD)
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "active"


class TestTheDirectPath:
    def _create(self, metadata):
        pool = make_pool(actor=actor_row(role="contributor"), conn_fetchrow=[
            _full(metadata, "draft"), _full(metadata, "pending_review"),
            _full(metadata, "active")])

        async def fetchval(sql, *args):
            return "personal" if "FROM scope_nodes" in sql else uuid4()

        pool.conn.fetchval = AsyncMock(side_effect=fetchval)
        with client_for(pool) as client:
            return client.post("/v1/blocks", json={
                "scope_id": str(_SCOPE), "block_type": "calibration_anchor",
                "content": "The loan is approved.", "metadata": metadata}), pool

    def test_an_incomplete_anchor_is_refused_where_writing_activates(self):
        response, pool = self._create(_NO_RAW_PROMPT)
        assert response.status_code == 422, response.text
        assert "raw_prompt" in response.json()["detail"]
        changes = [c.args[1] for c in pool.conn.fetchrow.call_args_list
                   if c.args[0].startswith("UPDATE context_blocks SET status")]
        assert "active" not in changes

    def test_a_complete_anchor_goes_live(self):
        response, _ = self._create(_GOOD)
        assert response.status_code == 201, response.text
        assert response.json()["status"] == "active"


# ── Reading runs ──────────────────────────────────────────────────────────────

_RUN = uuid4()
_NOW = datetime(2026, 10, 6, 9, 0, tzinfo=UTC)


def _run(**kw) -> dict:
    return {"id": _RUN, "mode": "drift", "status": "complete", "triggered_by": None,
            "review_needed": False, "started_at": _NOW, "finished_at": _NOW, **kw}


def _measurement(**kw) -> dict:
    return {"id": uuid4(), "anchor_block_id": _BLOCK, "anchor_version": 1,
            "model": "phi3-mini", "variant": "composed", "outcome": "measured",
            "score": Decimal("0.8125"), "scores": json.dumps({"rouge_l": 0.625, "regex": 1.0}),
            "output": "The loan…", "detail": None, "latency_ms": 2100,
            "tokens_in": 120, "tokens_out": 40, "created_at": _NOW, **kw}


class TestReadingRuns:
    def test_the_list(self):
        pool = make_pool(actor=actor_row(role="reviewer"), fetch=[_run()], fetchval=1)
        with client_for(pool) as client:
            response = client.get("/v1/benchmarks", params={"limit": 10})
        assert response.status_code == 200, response.text
        body = response.json()
        assert (body["total"], body["limit"], len(body["items"])) == (1, 10, 1)
        assert body["items"][0]["mode"] == "drift"

    def test_one_run_with_what_it_measured(self):
        outage = _measurement(model="gemma4-e4b", outcome="unreachable", score=None,
                              scores="{}", output=None, detail="connection refused")
        pool = make_pool(actor=actor_row(role="reviewer"), fetchrow=_run(),
                         fetch=[_measurement(), outage])
        with client_for(pool) as client:
            response = client.get(f"/v1/benchmarks/{_RUN}")
        assert response.status_code == 200, response.text
        measured, unreachable = response.json()["measurements"]
        assert measured["score"] == 0.8125
        assert measured["scores"] == {"rouge_l": 0.625, "regex": 1.0}
        # An outage is not a measurement: no score, and a reason instead.
        assert (unreachable["score"], unreachable["detail"]) == (None, "connection refused")

    def test_an_unknown_run_is_404(self):
        pool = make_pool(actor=actor_row(role="reviewer"), fetchrow=None)
        with client_for(pool) as client:
            assert client.get(f"/v1/benchmarks/{uuid4()}").status_code == 404

    @pytest.mark.parametrize("path", ["/v1/benchmarks", f"/v1/benchmarks/{_RUN}"])
    def test_reading_needs_a_reviewer(self, path):
        pool = make_pool(actor=actor_row(role="contributor"), fetchrow=_run(), fetch=[])
        with client_for(pool) as client:
            assert client.get(path).status_code == 403
