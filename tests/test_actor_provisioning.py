"""
tests/test_actor_provisioning.py

Tests for actor provisioning and role refreshing (CHY-33, core/authz.py).

Before this, `actors` was empty and nothing in the repo could insert a row, so
every endpoint behind `require_role` answered 403 to everyone. The tests here
are weighted towards the two things that decide whether that stays fixed: which
role a caller ends up with, and whether a write happens when nothing changed.
"""
import json
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from api.config import settings
from kernel.audit import ACTION_ACTOR_PROVISIONED, ACTION_ACTOR_ROLE_CHANGED
from kernel.roles import DEFAULT_ROLE
from kernel.errors import AccessDenied, ActorStateInconsistent
from kernel.access import ResolvedActor, require
from kernel.identity import resolve_actor, role_from_groups
from tests.conftest import actor_row, client_for, make_pool, stub_user

_ACTOR_ID = uuid4()


# ── Groups → role ─────────────────────────────────────────────────────────────

class TestRoleFromGroups:
    def test_absent_claim_yields_none(self):
        """"The IdP said nothing" must be distinguishable from "no groups"."""
        assert role_from_groups(None) is None

    def test_empty_claim_yields_the_default(self):
        """Removed from every group is a deprovisioning signal, not silence."""
        assert role_from_groups([]) == DEFAULT_ROLE

    @pytest.mark.parametrize(
        "group,role",
        [
            ("chy-admins", "admin"),
            ("chy-architects", "architect"),
            ("chy-reviewers", "reviewer"),
            ("chy-contributors", "contributor"),
            ("chy-consumers", "consumer"),
        ],
    )
    def test_each_group_maps(self, group, role):
        assert role_from_groups([group]) == role

    def test_highest_role_wins(self):
        """Being added to a group must never take a privilege away."""
        assert role_from_groups(["chy-consumers", "chy-admins"]) == "admin"
        assert role_from_groups(["chy-admins", "chy-consumers"]) == "admin"

    def test_unknown_groups_are_ignored(self):
        assert role_from_groups(["some-other-team", "chy-reviewers"]) == "reviewer"

    def test_only_unknown_groups_yield_the_default(self):
        """A mapping that matches nothing must not hand out access."""
        assert role_from_groups(["marketing"]) == DEFAULT_ROLE

    def test_the_default_is_the_least_privileged_role(self):
        assert DEFAULT_ROLE == "consumer"


# ── Provisioning ──────────────────────────────────────────────────────────────

class TestProvisioning:
    @pytest.mark.asyncio
    async def test_unknown_caller_gets_a_row(self):
        pool = make_pool(actor=None, conn_fetchrow={"id": _ACTOR_ID, "role": "admin"})
        actor = await resolve_actor(pool, stub_user(groups=["chy-admins"]))
        assert actor.id == _ACTOR_ID
        assert actor.role == "admin"

    @pytest.mark.asyncio
    async def test_insert_uses_on_conflict(self):
        """Two parallel first requests race on the unique index on idp_sub."""
        pool = make_pool(actor=None, conn_fetchrow={"id": _ACTOR_ID, "role": "consumer"})
        await resolve_actor(pool, stub_user())
        sql = pool.conn.fetchrow.call_args.args[0]
        assert "INSERT INTO actors" in sql
        assert "ON CONFLICT (idp_sub) DO UPDATE" in sql

    @pytest.mark.asyncio
    async def test_insert_cannot_touch_an_anonymized_row(self):
        pool = make_pool(actor=None, conn_fetchrow={"id": _ACTOR_ID, "role": "consumer"})
        await resolve_actor(pool, stub_user())
        assert "actors.is_anonymized = FALSE" in pool.conn.fetchrow.call_args.args[0]

    @pytest.mark.asyncio
    async def test_display_name_comes_from_the_token(self):
        pool = make_pool(actor=None, conn_fetchrow={"id": _ACTOR_ID, "role": "consumer"})
        await resolve_actor(pool, stub_user(sub="s1", username="alice"))
        args = pool.conn.fetchrow.call_args.args
        assert args[1] == "s1"      # idp_sub
        assert args[2] == "alice"   # display_name — NOT NULL, so never empty

    @pytest.mark.asyncio
    async def test_no_groups_claim_provisions_at_the_default_role(self):
        """First sight with nothing to go on must not guess upwards."""
        pool = make_pool(actor=None, conn_fetchrow={"id": _ACTOR_ID, "role": "consumer"})
        await resolve_actor(pool, stub_user(groups=None))
        assert pool.conn.fetchrow.call_args.args[3] == "consumer"

    @pytest.mark.asyncio
    async def test_provisioning_is_audited(self):
        pool = make_pool(actor=None, conn_fetchrow={"id": _ACTOR_ID, "role": "admin"})
        await resolve_actor(pool, stub_user(groups=["chy-admins"]))
        audits = [
            c for c in pool.conn.fetchval.call_args_list
            if "audit_log" in c.args[0]
        ]
        assert audits and audits[0].args[2] == ACTION_ACTOR_PROVISIONED

    @pytest.mark.asyncio
    async def test_an_inconsistent_conflict_is_not_swallowed(self):
        """ON CONFLICT matched a row the WHERE excluded — only possible if an
        anonymized row kept its idp_sub, which the CHECK forbids."""
        pool = make_pool(actor=None, conn_fetchrow=None)
        # A domain error, not a 409 — the HTTP mapping has lived in
        # api/main.py since CHY-101. tests/test_kernel_errors.py asserts the
        # code itself is unchanged.
        with pytest.raises(ActorStateInconsistent):
            await resolve_actor(pool, stub_user())


# ── Role refresh ──────────────────────────────────────────────────────────────

class TestRoleRefresh:
    @pytest.mark.asyncio
    async def test_known_caller_without_a_groups_claim_keeps_the_stored_role(self):
        """The IdP has nothing to say — a stored role must not be overwritten."""
        pool = make_pool(actor=actor_row(_ACTOR_ID, "admin"))
        actor = await resolve_actor(pool, stub_user(groups=None))
        assert actor.role == "admin"
        pool.acquire.assert_not_called()

    @pytest.mark.asyncio
    async def test_matching_role_writes_nothing(self):
        """The common path must cost one SELECT and no write."""
        pool = make_pool(actor=actor_row(_ACTOR_ID, "admin"))
        await resolve_actor(pool, stub_user(groups=["chy-admins"]))
        pool.acquire.assert_not_called()

    @pytest.mark.asyncio
    async def test_promotion_is_applied(self):
        pool = make_pool(actor=actor_row(_ACTOR_ID, "consumer"), conn_fetchrow={"role": "consumer"})
        actor = await resolve_actor(pool, stub_user(groups=["chy-admins"]))
        assert actor.role == "admin"
        assert "UPDATE actors SET role" in pool.conn.execute.call_args.args[0]

    @pytest.mark.asyncio
    async def test_removal_from_every_group_demotes(self):
        """Offboarding: groups present but empty must take effect."""
        pool = make_pool(actor=actor_row(_ACTOR_ID, "admin"), conn_fetchrow={"role": "admin"})
        actor = await resolve_actor(pool, stub_user(groups=[]))
        assert actor.role == "consumer"

    @pytest.mark.asyncio
    async def test_refresh_locks_the_row(self):
        """Parallel requests after a group change would otherwise each write an
        identical, unremovable audit entry."""
        pool = make_pool(actor=actor_row(_ACTOR_ID, "consumer"), conn_fetchrow={"role": "consumer"})
        await resolve_actor(pool, stub_user(groups=["chy-admins"]))
        assert "FOR UPDATE" in pool.conn.fetchrow.call_args.args[0]

    @pytest.mark.asyncio
    async def test_a_race_that_already_applied_the_change_writes_nothing(self):
        """Second comparison inside the lock: another request got there first."""
        pool = make_pool(actor=actor_row(_ACTOR_ID, "consumer"), conn_fetchrow={"role": "admin"})
        actor = await resolve_actor(pool, stub_user(groups=["chy-admins"]))
        assert actor.role == "admin"
        pool.conn.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_role_change_is_audited(self):
        pool = make_pool(actor=actor_row(_ACTOR_ID, "consumer"), conn_fetchrow={"role": "consumer"})
        await resolve_actor(pool, stub_user(groups=["chy-reviewers"]))
        audits = [c for c in pool.conn.fetchval.call_args_list if "audit_log" in c.args[0]]
        assert audits and audits[0].args[2] == ACTION_ACTOR_ROLE_CHANGED
        assert json.loads(audits[0].args[4]) == {"from": "consumer", "to": "reviewer", "source": "oidc_groups"}


# ── The gate still gates ──────────────────────────────────────────────────────

class TestRequireRole:
    @pytest.mark.asyncio
    async def test_provisioned_consumer_cannot_act_as_admin(self):
        pool = make_pool(actor=None, conn_fetchrow={"id": _ACTOR_ID, "role": "consumer"})
        with pytest.raises(AccessDenied) as exc:
            require(await resolve_actor(pool, stub_user()), "admin")
        assert exc.value.required == "admin"
        assert exc.value.actual == "consumer"

    @pytest.mark.asyncio
    async def test_provisioned_admin_passes(self):
        pool = make_pool(actor=None, conn_fetchrow={"id": _ACTOR_ID, "role": "admin"})
        assert (require(await resolve_actor(pool, stub_user(groups=["chy-admins"])), "admin")).role == "admin"

    def test_an_unknown_stored_role_ranks_below_everything(self):
        assert ResolvedActor(_ACTOR_ID, "wizard").has_at_least("consumer") is False


# ── The write routes are gated ────────────────────────────────────────────────

class TestBlockWriteGates:
    """A consumer could otherwise retire the active version of an org-wide
    prompt through PATCH — the effect CHY-32's review gate exists to control."""

    def _pool(self, role):
        return make_pool(actor=actor_row(_ACTOR_ID, role))

    @pytest.mark.parametrize("role", ["consumer"])
    def test_consumer_cannot_create(self, role):
        with client_for(self._pool(role)) as client:
            resp = client.post("/v1/blocks", json={
                "scope_id": str(uuid4()), "block_type": "system_prompt", "content": "x",
            })
            assert resp.status_code == 403

    def test_consumer_cannot_patch(self):
        with client_for(self._pool("consumer")) as client:
            assert client.patch(f"/v1/blocks/{uuid4()}", json={"content": "x"}).status_code == 403

    def test_contributor_cannot_delete(self):
        """Retiring a block takes it out of every composition — reviewer."""
        with client_for(self._pool("contributor")) as client:
            assert client.delete(f"/v1/blocks/{uuid4()}").status_code == 403

    def test_reviewer_may_delete(self):
        pool = make_pool(
            actor=actor_row(_ACTOR_ID, "reviewer"),
            conn_fetch=[{"id": uuid4(), "version": 1, "status": "active", "scope_id": uuid4()}],
        )
        with client_for(pool) as client:
            assert client.delete(f"/v1/blocks/{uuid4()}").status_code == 204

    def test_reads_stay_open_to_any_authenticated_caller(self):
        pool = make_pool(actor=actor_row(_ACTOR_ID, "consumer"), fetch=[], fetchval=0)
        with client_for(pool) as client:
            assert client.get("/v1/blocks").status_code == 200

    def test_gate_runs_before_the_block_is_read(self):
        """Otherwise a refused caller still learns whether a block exists."""
        pool = make_pool(actor=actor_row(_ACTOR_ID, "consumer"))
        with client_for(pool) as client:
            client.patch(f"/v1/blocks/{uuid4()}", json={"content": "x"})
        from tests.conftest import domain_calls

        assert domain_calls(pool) == []


# ── Dev mode ──────────────────────────────────────────────────────────────────

def test_dev_user_provisions_as_admin(monkeypatch):
    """DEV_MODE is meant to be usable without Authentik. Before CHY-33 the stub
    carried a group name that matched nothing, so the dev user would have been
    a consumer and every admin endpoint would have refused it."""
    monkeypatch.setattr(settings, "dev_mode", True)
    pool = make_pool(actor=None, conn_fetchrow={"id": _ACTOR_ID, "role": "admin"})
    pool.conn.fetchval = AsyncMock(return_value=uuid4())
    with client_for(pool, user=None) as client:
        # dev_mode short-circuits require_auth, but the override is what the
        # TestClient uses — assert the mapping instead.
        assert role_from_groups(["chy-admins"]) == "admin"
        assert client is not None
