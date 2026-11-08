"""
tests/test_four_eyes.py

Four eyes where data changes place (CHY-163, ADR-045).

Before this, approving a block recorded author and approver and enforced
nothing: one reviewer could write a routing rule with a hosted model and
approve it, and in a project or personal scope a contributor did not even need
to approve — the rule went live on write. What must hold now, each with the
case it refuses and the case it lets through:

  - a data-flow block — a routing rule naming a model below `critical`, or a
    block loosening a privacy rule — is approved by someone who wrote nothing
    in this round. Writing includes editing someone else's draft
  - a single operator may approve their own, and is told so
  - a technical account approves no data-flow block at all
  - in a direct scope a data-flow block waits for review instead of going live
  - everything else is approved as before

The pool is a double. What only a database shows — that the audit entries the
rule reads are really written, and migration 029 — is in
tests/integration/test_four_eyes_db.py.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from contexts.repository import block as rules
from contexts.repository.errors import SecondPersonRequired, ServiceAccountMayNotApprove
from contexts.repository.four_eyes import (
    Verdict,
    is_data_flow,
    judge_approval,
    writers_since_last_approval,
)
from kernel.audit import (
    ACTION_ACTOR_ROLE_CHANGED,
    ACTION_ACTOR_SERVICE_ACCOUNT_CHANGED,
    ACTION_BLOCK_APPROVED,
    ACTION_BLOCK_CREATED,
    ACTION_BLOCK_EDITED,
    ACTION_BLOCK_PROMOTED,
    ACTION_BLOCK_SUBMITTED,
    ACTION_BLOCK_SUPERSEDED,
    AuditLogEntry,
)
from kernel.identity import resolve_actor, service_account_from_groups
from kernel.roles import APPROVING_ROLES, ROLE_ORDER
from router.config import RouterConfig
from router.rules import RoutingRule, names_model_below_critical
from tests.conftest import DEFAULT_ACTOR_ID, actor_row, client_for, make_pool, stub_user

_ME = DEFAULT_ACTOR_ID
_OTHER, _THIRD = uuid4(), uuid4()
_SCOPE = uuid4()
_CLOUD, _LOCAL = "gemma4-cloud", "gemma4-e4b"


def _entry(action: str, actor: UUID, **payload) -> AuditLogEntry:
    return AuditLogEntry(id=uuid4(), actor_id=actor, action=action,
                         target_ref="block:x", payload=payload,
                         created_at=datetime(2026, 11, 5, tzinfo=UTC))


# ── which blocks are data-flow blocks ─────────────────────────────────────────

class TestWhatCounts:
    def test_a_loosening_claim_counts_without_asking_the_router(self):
        assert is_data_flow({"privacy_impact": "permissive"}, None)

    def test_a_routing_rule_counts_when_the_router_says_so(self):
        assert is_data_flow({"privacy_impact": None}, lambda block: True)

    def test_a_tightening_claim_and_plain_prompt_material_do_not(self):
        """Counter-probe: four eyes on every prompt would be a ritual."""
        assert not is_data_flow({"privacy_impact": "restrictive"}, lambda block: False)
        assert not is_data_flow({"privacy_impact": None}, lambda block: False)
        assert not is_data_flow({"privacy_impact": None}, None)


class TestTheRouterAnswer:
    """Against the shipped declaration: local models are critical, hosted public."""

    def _rule(self, *candidates) -> RoutingRule:
        return RoutingRule(task_type="summarize", candidates=candidates, scope_id=_SCOPE,
                           block_id=uuid4(), version=1, max_tokens=256)

    def test_a_hosted_model_anywhere_in_the_chain_is_below_critical(self):
        config = RouterConfig()
        assert names_model_below_critical(self._rule(_CLOUD, _LOCAL), config)
        assert names_model_below_critical(self._rule(_LOCAL, _CLOUD), config)

    def test_local_models_only_are_not(self):
        assert not names_model_below_critical(self._rule(_LOCAL, "gemma3-4b"), RouterConfig())

    def test_an_undeclared_model_is_left_to_check_chain(self):
        """Not the four-eyes rule's to judge: check_chain refuses the rule."""
        assert not names_model_below_critical(self._rule("nowhere-7b"), RouterConfig())


# ── who wrote a version ───────────────────────────────────────────────────────

class TestWriters:
    def test_the_creator_wrote_it(self):
        entries = [_entry(ACTION_BLOCK_CREATED, _OTHER, version=1)]
        assert writers_since_last_approval(entries, line_author_id=_OTHER) == {_OTHER}

    def test_editing_someone_elses_draft_makes_you_a_writer(self):
        """The case the line's author alone could not catch: a reviewer adds a
        hosted model to another person's draft and then approves it."""
        entries = [_entry(ACTION_BLOCK_CREATED, _OTHER, version=1),
                   _entry(ACTION_BLOCK_EDITED, _ME, version=1, fields=["metadata"])]
        assert writers_since_last_approval(entries, line_author_id=_OTHER) == {_OTHER, _ME}

    def test_a_round_starts_after_the_last_approval(self):
        """Version 1 by me, approved by somebody else; version 2 by a third
        person. I wrote nothing in this round."""
        entries = [_entry(ACTION_BLOCK_CREATED, _ME, version=1),
                   _entry(ACTION_BLOCK_APPROVED, _OTHER, version=1),
                   _entry(ACTION_BLOCK_SUPERSEDED, _THIRD, from_version=1, to_version=2)]
        assert writers_since_last_approval(entries, line_author_id=_ME) == {_THIRD}

    def test_an_unapproved_predecessor_stays_in_the_round(self):
        """Rejected version 2 carried into version 3: its writer still wrote it."""
        entries = [_entry(ACTION_BLOCK_CREATED, _OTHER, version=1),
                   _entry(ACTION_BLOCK_APPROVED, _THIRD, version=1),
                   _entry(ACTION_BLOCK_SUPERSEDED, _ME, to_version=2),
                   _entry(ACTION_BLOCK_SUPERSEDED, _OTHER, to_version=3)]
        assert writers_since_last_approval(entries, line_author_id=_OTHER) == {_ME, _OTHER}

    def test_a_promotion_writes_the_first_version_of_the_copy(self):
        entries = [_entry(ACTION_BLOCK_PROMOTED, _ME, from_version=4)]
        assert writers_since_last_approval(entries, line_author_id=_ME) == {_ME}

    def test_a_line_from_before_chy_171_still_has_its_author(self):
        """No `block.created` entry existed then; the author wrote version 1."""
        assert writers_since_last_approval([], line_author_id=_OTHER) == {_OTHER}


# ── the verdict ───────────────────────────────────────────────────────────────

class TestJudge:
    def test_someone_who_wrote_nothing_approves(self):
        assert judge_approval(approver_id=_ME, approver_is_service_account=False,
                              writer_ids=frozenset({_OTHER}),
                              people_who_may_approve=2) is Verdict.SECOND_PERSON

    def test_a_writer_is_refused_when_somebody_else_could_approve(self):
        with pytest.raises(SecondPersonRequired) as refused:
            judge_approval(approver_id=_ME, approver_is_service_account=False,
                           writer_ids=frozenset({_ME}), people_who_may_approve=2)
        assert refused.value.people_who_may_approve == 2

    def test_a_single_operator_may_approve_their_own(self):
        assert judge_approval(approver_id=_ME, approver_is_service_account=False,
                              writer_ids=frozenset({_ME}),
                              people_who_may_approve=1) is Verdict.SELF_APPROVED

    def test_a_service_account_is_refused_even_if_it_wrote_nothing(self):
        with pytest.raises(ServiceAccountMayNotApprove):
            judge_approval(approver_id=_ME, approver_is_service_account=True,
                           writer_ids=frozenset({_OTHER}), people_who_may_approve=1)


def test_the_approving_roles_are_those_the_approve_transition_admits():
    """kernel/roles.py cannot import the repository; this keeps the two in step."""
    floor = ROLE_ORDER[rules.TRANSITIONS["approve"].role]
    assert set(APPROVING_ROLES) == {r for r, rank in ROLE_ORDER.items() if rank >= floor}


# ── approval through the endpoint ─────────────────────────────────────────────

def _public_chain():
    return [{"id": _SCOPE, "scope_path": "org_x", "node_type": "team", "name": "x",
             "depth": 1, "privacy_level": "public"}]


def _approve(*, entries, people: int, candidates=(_CLOUD, _LOCAL), approver=None,
             block_type="routing_rule", claim=None, author=_OTHER, version=1):
    """POST /approve on a pending block, with the audit entries the rule reads
    and the number of people who may approve."""
    block_id = uuid4()
    locked = {
        "id": uuid4(), "block_id": block_id, "version": version,
        "status": "pending_review", "scope_id": _SCOPE, "author_id": author,
        "governance_key": "cloud_routing" if claim else None,
        "privacy_impact": claim, "locked": False, "block_type": block_type,
        "metadata": {"task_type": "summarize", "candidates": list(candidates),
                     "max_tokens": 256},
    }
    updated = {**locked, "status": "active", "superseded_by_version": None,
               "content": "so entschieden", "tags": [],
               "created_at": "2026-11-05T09:00:00+00:00"}

    def conn_fetch(sql, *args):
        if "FROM audit_log" in sql:
            return [e.model_dump() for e in entries]
        if "scope_nodes" in sql:
            return _public_chain()
        return []

    pool = make_pool(actor=approver or actor_row(role="reviewer"),
                     conn_fetchrow=[locked, updated], conn_fetch=conn_fetch)

    async def conn_fetchval(sql, *args):
        if "count(*) FROM actors" in sql:
            return people
        return uuid4()  # an audit entry's id

    pool.conn.fetchval.side_effect = conn_fetchval
    with client_for(pool) as client:
        response = client.post(f"/v1/blocks/{block_id}/approve", json={"version": version})
    return response, pool


def _approval_entry(pool) -> dict | None:
    for call in pool.conn.fetchval.call_args_list:
        if "audit_log" in call.args[0] and call.args[2] == ACTION_BLOCK_APPROVED:
            return json.loads(call.args[4])
    return None


def _went_active(pool) -> bool:
    return any(c.args[0].startswith("UPDATE context_blocks SET status")
               for c in pool.conn.fetchrow.call_args_list)


class TestApproval:
    def test_the_writer_of_a_cloud_rule_is_refused(self):
        """The case of the ticket: one person writes it and approves it."""
        response, pool = _approve(entries=[_entry(ACTION_BLOCK_CREATED, _ME, version=1)],
                                  people=2, author=_ME)
        assert response.status_code == 403, response.text
        assert "ADR-045" in response.json()["detail"]
        assert not _went_active(pool), "the rule went active anyway"

    def test_whoever_edited_the_draft_is_refused_too(self):
        response, pool = _approve(
            entries=[_entry(ACTION_BLOCK_CREATED, _OTHER, version=1),
                     _entry(ACTION_BLOCK_EDITED, _ME, version=1, fields=["metadata"])],
            people=2)
        assert response.status_code == 403, response.text
        assert not _went_active(pool)

    def test_a_second_person_approves(self):
        """Counter-probe: four eyes must not refuse every cloud rule."""
        response, pool = _approve(entries=[_entry(ACTION_BLOCK_CREATED, _OTHER, version=1)],
                                  people=2)
        assert response.status_code == 200, response.text
        assert _approval_entry(pool)["four_eyes"] == "second_person"
        assert "Cohereny-Notice" not in response.headers

    def test_a_single_operator_approves_their_own_and_is_told(self, caplog):
        response, pool = _approve(entries=[_entry(ACTION_BLOCK_CREATED, _ME, version=1)],
                                  people=1, author=_ME)
        assert response.status_code == 200, response.text
        assert "no second person" in response.headers["Cohereny-Notice"]
        assert _approval_entry(pool)["four_eyes"] == "self_approved"
        assert any("no second person checked" in r.getMessage() for r in caplog.records)

    def test_a_service_account_approves_no_cloud_rule(self):
        response, pool = _approve(
            entries=[_entry(ACTION_BLOCK_CREATED, _OTHER, version=1)], people=2,
            approver=actor_row(role="architect", is_service_account=True))
        assert response.status_code == 403, response.text
        assert "technical account" in response.json()["detail"]
        assert not _went_active(pool)

    def test_a_loosening_claim_needs_a_second_person(self):
        response, pool = _approve(
            entries=[_entry(ACTION_BLOCK_CREATED, _ME, version=1)], people=2,
            block_type="system_prompt", claim="permissive", author=_ME)
        assert response.status_code == 403, response.text
        assert not _went_active(pool)

    def test_the_writer_of_a_local_rule_approves_as_before(self):
        """Counter-probe: a rule that keeps data local is not a data-flow block."""
        response, pool = _approve(entries=[_entry(ACTION_BLOCK_CREATED, _ME, version=1)],
                                  people=2, candidates=(_LOCAL,), author=_ME)
        assert response.status_code == 200, response.text
        assert "four_eyes" not in _approval_entry(pool)

    def test_prompt_material_asks_nothing(self):
        """Counter-probe: no audit read and no count for an ordinary block."""
        response, pool = _approve(entries=[], people=2, block_type="system_prompt",
                                  author=_ME)
        assert response.status_code == 200, response.text
        assert not any("FROM audit_log" in c.args[0] for c in pool.conn.fetch.call_args_list)
        assert "four_eyes" not in _approval_entry(pool)


# ── the direct path ───────────────────────────────────────────────────────────

def _row(status: str, *, candidates, version: int = 1) -> dict:
    return {
        "id": uuid4(), "block_id": uuid4(), "version": version,
        "superseded_by_version": None, "scope_id": _SCOPE, "block_type": "routing_rule",
        "content": "so entschieden", "tags": [], "author_id": _ME, "status": status,
        "metadata": {"task_type": "summarize", "candidates": list(candidates),
                     "max_tokens": 256},
        "governance_key": None, "privacy_impact": None, "locked": False,
        "created_at": "2026-11-05T09:00:00+00:00",
    }


def _create_in_personal_scope(candidates, *, level="public", writes=None):
    chain = [{"id": _SCOPE, "scope_path": "org_x.me", "node_type": "personal",
              "name": "me", "depth": 1, "privacy_level": level}]
    pool = make_pool(actor=actor_row(role="contributor"),
                     conn_fetchrow=writes or [_row("draft", candidates=candidates)],
                     conn_fetch=lambda sql, *a: chain if "scope_path @>" in sql else [])

    async def fetchval(sql, *args):
        if "FROM scope_nodes" in sql:
            return "personal"
        return uuid4()

    pool.conn.fetchval.side_effect = fetchval
    with client_for(pool) as client:
        response = client.post("/v1/blocks", json={
            "scope_id": str(_SCOPE), "block_type": "routing_rule", "content": "so entschieden",
            "metadata": {"task_type": "summarize", "candidates": list(candidates),
                         "max_tokens": 256},
        })
    return response, pool


def _audit(pool) -> list[tuple[str, dict]]:
    return [(c.args[2], json.loads(c.args[4]))
            for c in pool.conn.fetchval.call_args_list if "audit_log" in c.args[0]]


class TestDirectPath:
    def test_a_cloud_rule_in_a_personal_scope_waits_for_a_second_person(self):
        """The larger of the two gaps: until now it went live on write."""
        candidates = (_CLOUD, _LOCAL)
        response, pool = _create_in_personal_scope(
            candidates, writes=[_row("draft", candidates=candidates),
                                _row("pending_review", candidates=candidates)])
        assert response.status_code == 201, response.text
        assert response.json()["status"] == "pending_review"
        actions = _audit(pool)
        assert [a for a, _ in actions] == [ACTION_BLOCK_CREATED, ACTION_BLOCK_SUBMITTED]
        assert actions[1][1]["mode"] == "direct"
        assert actions[1][1]["four_eyes"] == "required"

    def test_a_local_rule_in_a_personal_scope_is_live_at_once(self):
        """Counter-probe: ADR-042 point 2 still holds for everything else."""
        candidates = (_LOCAL,)
        response, _ = _create_in_personal_scope(
            candidates, level=None,
            writes=[_row(s, candidates=candidates) for s in ("draft", "pending_review", "active")])
        assert response.status_code == 201, response.text
        assert response.json()["status"] == "active"

    def test_a_cloud_rule_the_scope_could_never_use_is_refused_to_its_writer(self):
        """Checked before it is handed over, so the second person does not
        inherit a rule nobody could approve."""
        response, pool = _create_in_personal_scope((_CLOUD, _LOCAL), level=None)
        assert response.status_code == 409, response.text
        assert [a for a, _ in _audit(pool)] == [ACTION_BLOCK_CREATED]


class TestDraftEdits:
    def test_an_edit_of_a_draft_is_recorded_without_its_content(self):
        draft = {**_row("draft", candidates=(_LOCAL,)), "block_type": "system_prompt",
                 "metadata": {}}
        pool = make_pool(actor=actor_row(role="contributor"), fetchrow=draft,
                         fetch=[], conn_fetchrow=[{**draft, "content": "geheim"}])

        async def fetchval(sql, *args):
            if "FROM scope_nodes" in sql:
                return "team"
            return uuid4()

        pool.conn.fetchval.side_effect = fetchval
        with client_for(pool) as client:
            response = client.patch(f"/v1/blocks/{draft['block_id']}", json={"content": "geheim"})
        assert response.status_code == 200, response.text
        (action, payload), = _audit(pool)
        assert action == ACTION_BLOCK_EDITED
        assert payload == {"version": 1, "fields": ["content"]}


# ── technical accounts ────────────────────────────────────────────────────────

class TestServiceAccounts:
    def test_the_group_marks_it_and_silence_says_nothing(self):
        assert service_account_from_groups(["chy-architects", "chy-service-accounts"])
        assert service_account_from_groups(["chy-architects"]) is False
        assert service_account_from_groups(None) is None

    @pytest.mark.asyncio
    async def test_first_sight_with_the_group_provisions_a_technical_account(self):
        pool = make_pool(actor=None, conn_fetchrow={"id": _OTHER, "role": "architect",
                                                    "is_service_account": True})
        actor = await resolve_actor(
            pool, stub_user(groups=["chy-architects", "chy-service-accounts"]))
        insert = pool.conn.fetchrow.call_args
        assert "INSERT INTO actors" in insert.args[0]
        assert insert.args[4] is True
        assert actor.is_service_account

    @pytest.mark.asyncio
    async def test_joining_the_group_later_is_written_and_audited_on_its_own(self):
        pool = make_pool(actor=actor_row(_OTHER, "architect"),
                         conn_fetchrow={"role": "architect", "is_service_account": False})
        actor = await resolve_actor(
            pool, stub_user(groups=["chy-architects", "chy-service-accounts"]))
        assert actor.is_service_account
        actions = [c.args[2] for c in pool.conn.fetchval.call_args_list
                   if "audit_log" in c.args[0]]
        assert actions == [ACTION_ACTOR_SERVICE_ACCOUNT_CHANGED]
        assert ACTION_ACTOR_ROLE_CHANGED not in actions

    @pytest.mark.asyncio
    async def test_a_token_without_groups_leaves_the_flag_as_stored(self):
        pool = make_pool(actor=actor_row(_OTHER, "architect", is_service_account=True))
        actor = await resolve_actor(pool, stub_user(groups=None))
        assert actor.is_service_account
        pool.acquire.assert_not_called()
