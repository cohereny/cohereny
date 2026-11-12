"""
tests/test_direct_calls.py

/v1/complete answers only where an answer may be used unchecked (CHY-199,
ADR-050).

The minimum review for a direct call is the stricter of the scope's data class
and the risk class of direct calls in it; only `sample` lets /v1/complete
answer. Four layers: reading the classification off a chain, the rule and its
sentence, the endpoints that report and set it, and /v1/complete through the
real route — a rule the route never asks would leave every test above green.
"""
from __future__ import annotations

import itertools
from decimal import Decimal
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from api.v1 import _direct_calls
from kernel.schema import PRIVACY_LEVELS, RISK_CLASSES
from contexts.orchestration import service as orchestration
from contexts.orchestration import store
from kernel.scopes import (
    DirectCallRisk,
    direct_call_risk,
    effective_direct_call_risk_class,
    effective_direct_call_sample_rate,
)
from tests.conftest import actor_row, client_for, make_pool

ORG, PROJECT = uuid4(), uuid4()


#: A share of direct calls drawn for review, as `minimal` needs one (CHY-269).
SHARE = Decimal("0.05")


def _row(scope_id, depth, *, privacy=None, risk=None, rate=None) -> dict:
    """A chain row as kernel.scopes.ancestor_chain returns it."""
    return {"id": scope_id, "scope_path": "org" if depth == 1 else "org.project",
            "node_type": "org" if depth == 1 else "project", "name": "x", "depth": depth,
            "privacy_level": privacy, "direct_call_risk_class": risk,
            "direct_call_sample_rate": rate if rate is not None or risk != "minimal" else SHARE}


def _chain(*, org_privacy=None, org_risk=None, project_privacy=None, project_risk=None,
           org_rate=None, project_rate=None):
    return [_row(ORG, 1, privacy=org_privacy, risk=org_risk, rate=org_rate),
            _row(PROJECT, 2, privacy=project_privacy, risk=project_risk, rate=project_rate)]


def _risk(data_class: str, effective: str | None, rate=SHARE) -> DirectCallRisk:
    return DirectCallRisk(scope_id=PROJECT, own=effective, effective=effective,
                          data_class=data_class, own_sample_rate=rate, sample_rate=rate)


# ── reading the classification ────────────────────────────────────────────────

class TestTheClassification:
    def test_nobody_classified_reads_as_none_not_as_high(self):
        """None, so a reader can tell "decided high" from "never decided"; the
        rule counts both as high."""
        assert effective_direct_call_risk_class(_chain()) is None

    @pytest.mark.parametrize(("org", "project", "effective"), [
        ("minimal", None, "minimal"),        # inherited
        ("minimal", "high", "high"),         # the project is stricter
        ("high", "minimal", "high"),         # a looser one below has no effect
        ("limited", "limited", "limited"),
    ])
    def test_the_strictest_along_the_chain_applies(self, org, project, effective):
        assert effective_direct_call_risk_class(_chain(org_risk=org, project_risk=project)) == effective

    def test_a_scope_reads_its_own_setting_the_effective_one_and_its_data_class(self):
        risk = direct_call_risk(_chain(org_privacy="internal", org_risk="limited", project_risk="high"))
        assert risk == DirectCallRisk(scope_id=PROJECT, own="high", effective="high",
                                      data_class="internal")


# ── the rule ──────────────────────────────────────────────────────────────────

class TestTheRule:
    @pytest.mark.parametrize(("data_class", "risk_class"),
                             list(itertools.product(PRIVACY_LEVELS, (*RISK_CLASSES, None))))
    def test_direct_calls_answer_only_for_low_data_and_minimal_risk(self, data_class, risk_class):
        """All 16 combinations. Two allow a direct call; nothing else does,
        unclassified included."""
        allowed = data_class in ("public", "internal") and risk_class == "minimal"
        verdict = _direct_calls.classification(_risk(data_class, risk_class))
        assert verdict.direct_calls is allowed
        assert (_direct_calls.refusal(_risk(data_class, risk_class)) is None) is allowed

    def test_unclassified_says_so_and_offers_to_classify(self):
        text = _direct_calls.refusal(_risk("internal", None))
        assert "four_eyes" in text
        assert "nobody classified direct calls here, which counts as high" in text
        assert f"PUT /v1/scopes/{PROJECT}/direct-call-risk-class" in text

    def test_where_the_data_class_decides_classifying_is_not_offered(self):
        text = _direct_calls.refusal(_risk("confidential", "minimal"))
        assert "its data class confidential asks for approver" in text
        assert "PUT /v1/scopes" not in text
        assert "workflow with a review gate" in text

    def test_both_reasons_are_named_when_both_ask_for_more(self):
        """Counter-probe to the line above, and the case that went wrong in the
        first draft: the risk class sets four_eyes, but the data class alone
        would still refuse. Offering to classify would lead nowhere."""
        text = _direct_calls.refusal(_risk("confidential", None))
        assert "its data class confidential asks for approver" in text
        assert "nobody classified" in text
        assert "PUT /v1/scopes" not in text


# ── the endpoints ─────────────────────────────────────────────────────────────

def _pool(chain, role="architect"):
    def fetch(sql, *_args):
        return chain if "scope_nodes" in sql else []
    return make_pool(actor=actor_row(role=role), fetch=fetch)


class TestTheEndpoints:
    _URL = f"/v1/scopes/{PROJECT}/direct-call-risk-class"

    def test_reading_says_whether_direct_calls_answer_and_why(self):
        with client_for(_pool(_chain(org_privacy="internal"))) as client:
            response = client.get(self._URL)
        assert response.status_code == 200, response.text
        assert response.json() == {
            "scope_id": str(PROJECT), "risk_class": None, "effective_risk_class": None,
            "data_class": "internal", "review_level": "four_eyes", "sample_rate": None,
            "effective_sample_rate": None, "direct_calls": False,
        }

    def test_reading_knows_who_asked(self, caplog):
        """The request line names the actor (CHY-97): the endpoint resolves its
        caller instead of only checking the token."""
        import logging

        with caplog.at_level(logging.INFO, logger="cohereny.request"), \
                client_for(_pool(_chain(org_privacy="internal"))) as client:
            assert client.get(self._URL).status_code == 200
        [line] = [r for r in caplog.records if r.name == "cohereny.request"]
        assert line.actor_id == str(actor_row()["id"])

    def test_an_architect_classifies_and_the_answer_says_it_now_answers(self):
        pool = _pool(_chain(org_privacy="internal"))
        audit = AsyncMock()
        with patch("kernel.scopes.write_audit", audit), client_for(pool) as client:
            response = client.put(self._URL, json={"risk_class": "minimal", "sample_rate": 0.05})
        assert response.status_code == 200, response.text
        assert response.json()["direct_calls"] is True
        assert response.json()["review_level"] == "sample"
        assert response.json()["effective_sample_rate"] == 0.05

        [insert] = [c for c in pool.conn.execute.call_args_list
                    if "direct_call_risk_classes" in c.args[0]]
        assert insert.args[1:4] == (PROJECT, "minimal", SHARE)
        assert audit.await_args.kwargs["action"] == "scope.direct_call_risk_class_set"
        assert audit.await_args.kwargs["payload"] == {
            "from": None, "to": "minimal", "effective": "minimal",
            "sample_rate": {"from": None, "to": 0.05, "effective": 0.05}}

    def test_null_takes_the_own_setting_back(self):
        pool = _pool(_chain(org_privacy="internal", org_risk="limited", project_risk="high"))
        with patch("kernel.scopes.write_audit", AsyncMock()), client_for(pool) as client:
            response = client.put(self._URL, json={"risk_class": None})
        assert response.status_code == 200, response.text
        assert (response.json()["risk_class"], response.json()["effective_risk_class"]) == (None, "limited")

    def test_a_class_looser_than_the_scopes_above_is_refused(self):
        pool = _pool(_chain(org_privacy="internal", org_risk="limited"))
        with patch("kernel.scopes.write_audit", AsyncMock()) as audit, client_for(pool) as client:
            response = client.put(self._URL, json={"risk_class": "minimal", "sample_rate": 0.05})
        assert response.status_code == 422, response.text
        assert "classifies them as 'limited'" in response.json()["detail"]
        audit.assert_not_awaited()

    def test_a_contributor_may_not_classify(self):
        pool = _pool(_chain(org_privacy="internal"), role="contributor")
        with client_for(pool) as client:
            response = client.put(self._URL, json={"risk_class": "minimal", "sample_rate": 0.05})
        assert response.status_code == 403, response.text

    def test_an_unknown_scope_is_a_404(self):
        with client_for(_pool([])) as client:
            assert client.get(self._URL).status_code == 404
            assert client.put(self._URL, json={"risk_class": "high"}).status_code == 404


# ── the share drawn for review (CHY-269) ──────────────────────────────────────

class TestTheShare:
    _URL = f"/v1/scopes/{PROJECT}/direct-call-risk-class"

    @pytest.mark.parametrize(("org", "project", "effective"), [
        (None, None, None),                                          # nobody set one
        (SHARE, None, SHARE),                                        # inherited
        (Decimal("0.1"), Decimal("0.3"), Decimal("0.3")),            # the project checks more
        (Decimal("0.3"), Decimal("0.1"), Decimal("0.3")),            # a lower one has no effect
    ])
    def test_the_highest_share_along_the_chain_applies(self, org, project, effective):
        chain = _chain(org_risk="minimal" if org else None, org_rate=org,
                       project_risk="minimal" if project else None, project_rate=project)
        assert effective_direct_call_sample_rate(chain) == effective

    def test_minimal_without_a_share_is_refused_and_says_how_to_set_one(self):
        """Only a classification from before migration 048 has none: refused, not
        answered unchecked — a missing share may not read as "no check"."""
        risk = _risk("internal", "minimal", rate=None)
        text = _direct_calls.refusal(risk)
        assert "no share of them is drawn" in text and "sample_rate" in text
        assert _direct_calls.classification(risk).direct_calls is False
        # Counter-probe: the same scope with a share answers.
        assert _direct_calls.refusal(_risk("internal", "minimal")) is None

    @pytest.mark.parametrize("body", [
        {"risk_class": "minimal"},                         # minimal needs a share
        {"risk_class": "limited", "sample_rate": 0.1},     # no other class takes one
        {"risk_class": None, "sample_rate": 0.1},          # nor does taking the setting back
        {"risk_class": "minimal", "sample_rate": 0},       # above 0
        {"risk_class": "minimal", "sample_rate": 1.5},     # at most 1
        # Smaller than the column keeps: rounded to 0, it broke the CHECK with a
        # 500 (contract fuzzer, 10.11.2026). More places would be rounded silently.
        {"risk_class": "minimal", "sample_rate": 5e-324},
        {"risk_class": "minimal", "sample_rate": 0.0505},
    ])
    def test_the_share_goes_with_minimal_and_only_there(self, body):
        pool = _pool(_chain(org_privacy="internal"))
        with patch("kernel.scopes.write_audit", AsyncMock()) as audit, client_for(pool) as client:
            response = client.put(self._URL, json=body)
        assert response.status_code == 422, response.text
        audit.assert_not_awaited()

    def test_a_share_below_the_scopes_above_is_refused(self):
        pool = _pool(_chain(org_privacy="internal", org_risk="minimal", org_rate=Decimal("0.2")))
        with patch("kernel.scopes.write_audit", AsyncMock()) as audit, client_for(pool) as client:
            response = client.put(self._URL, json={"risk_class": "minimal", "sample_rate": 0.1})
        assert response.status_code == 422, response.text
        assert "draws 0.2" in response.json()["detail"]
        audit.assert_not_awaited()

    @pytest.mark.parametrize("rate", [0.001, 0.05, 1])
    def test_a_share_the_column_keeps_is_taken(self, rate):
        """Counter-probe to the refusals: from one in a thousand to all."""
        pool = _pool(_chain(org_privacy="internal"))
        with patch("kernel.scopes.write_audit", AsyncMock()), client_for(pool) as client:
            response = client.put(self._URL, json={"risk_class": "minimal", "sample_rate": rate})
        assert response.status_code == 200, response.text

    def test_a_higher_share_is_taken(self):
        """Counter-probe: checking more than the scopes above is allowed."""
        pool = _pool(_chain(org_privacy="internal", org_risk="minimal", org_rate=Decimal("0.2")))
        with patch("kernel.scopes.write_audit", AsyncMock()), client_for(pool) as client:
            response = client.put(self._URL, json={"risk_class": "minimal", "sample_rate": 0.3})
        assert response.status_code == 200, response.text
        assert response.json()["effective_sample_rate"] == 0.3


class TestTheDraw:
    """Whether a call is drawn is a hash of its id (votes.drawn); the draw itself
    keeps the call or does not."""

    @staticmethod
    def _call() -> orchestration.DirectCall:
        return orchestration.DirectCall(
            scope_id=PROJECT, called_by=uuid4(), task_type="summarize", model_used="m",
            block_versions={"b": 2}, prompt="Brief an {{PERSON_1}}", answer="Liebe {{PERSON_1}}")

    async def test_a_call_not_drawn_keeps_nothing(self, monkeypatch):
        monkeypatch.setattr(orchestration.votes, "drawn", lambda *_: False)
        insert = AsyncMock()
        monkeypatch.setattr(store, "insert_direct_sample", insert)
        assert await orchestration.draw_direct_call(make_pool(), self._call(), SHARE) is None
        insert.assert_not_awaited()

    async def test_a_drawn_call_is_kept_under_the_id_it_returns(self, monkeypatch):
        seen = []
        monkeypatch.setattr(orchestration.votes, "drawn",
                            lambda sample_id, rate: seen.append((sample_id, rate)) or True)
        insert = AsyncMock()
        monkeypatch.setattr(store, "insert_direct_sample", insert)
        call = self._call()
        sample_id = await orchestration.draw_direct_call(make_pool(), call, SHARE)
        assert seen == [(sample_id, SHARE)]
        _conn, kept_id, kept, kept_rate = insert.await_args.args
        assert (kept_id, kept, kept_rate) == (sample_id, call, SHARE)


# ── /v1/complete through the real route ───────────────────────────────────────

class TestTheEndpointThatAnswers:
    @staticmethod
    def _complete(chain):
        from tests.test_complete import _cleanup, _make_client, _make_pool, _mock_http

        with patch("tests.test_complete._CHAIN", chain), \
                patch("api.v1.complete.orchestration.draw_direct_call",
                      AsyncMock(return_value=None)), \
                patch("core.completion.httpx.AsyncClient") as mock_cls:
            http = _mock_http()
            mock_cls.return_value = http
            pool = _make_pool()
            client = _make_client(pool)
            try:
                response = client.post("/v1/complete", json={
                    "scope_id": str(chain[-1]["id"]), "prompt": "p", "task_type": "summarize"})
            finally:
                _cleanup()
        return response, http, pool

    def test_an_unclassified_scope_is_refused_before_any_model_is_asked(self):
        response, http, pool = self._complete(_chain(org_privacy="internal"))
        assert response.status_code == 409, response.text
        assert "nobody classified direct calls here" in response.json()["detail"]
        http.post.assert_not_called()
        # nor is a routing decision taken for a request that cannot be answered
        assert not any("routing_rule" in c.args[0] for c in pool.fetch.call_args_list)

    def test_a_classified_scope_answers(self):
        """Counter-probe: the same request, the scope classified minimal."""
        response, http, _ = self._complete(_chain(org_privacy="internal", project_risk="minimal"))
        assert response.status_code == 200, response.text
        http.post.assert_called_once()

    def test_confidential_data_is_refused_whatever_the_risk_class(self):
        response, http, _ = self._complete(_chain(org_privacy="confidential", org_risk="minimal"))
        assert response.status_code == 409, response.text
        assert "its data class confidential asks for approver" in response.json()["detail"]
        http.post.assert_not_called()
