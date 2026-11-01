"""
tests/test_deps_claims.py

Tests for JWT claim extraction in `api/deps.py`, and for the shared pool double
in `conftest.py` (CHY-33 preparation).

The claim tests exist for one distinction: a token without a groups claim and a
token whose groups claim is empty must not produce the same `CurrentUser`. Role
refreshing keys on exactly that difference — the first means "the IdP has
nothing to say", the second means "this person was removed from every group" —
and until now both arrived as `[]`.
"""
from unittest.mock import AsyncMock, patch

import pytest

from api.config import settings
from api.deps import CurrentUser, require_auth
from kernel.surfaces import issuers
from tests.conftest import (
    actor_calls,
    actor_row,
    domain_calls,
    make_pool,
    stub_user,
)


_ISSUER = next(iter(issuers(settings.oidc_base_url)))


async def _auth_with(claims: dict) -> CurrentUser:
    """Run require_auth against a fixed claim set, skipping JWKS and signature.

    The issuer lookup is stubbed too since INFRA-92: require_auth reads it off
    the unverified payload to decide which JWKS applies, and "token" is not a
    JWT. What that step does in earnest is covered in tests/test_surfaces.py.
    """
    creds = type("Creds", (), {"credentials": "token"})()
    with patch("api.deps._require_known_issuer", return_value=_ISSUER), \
         patch("api.deps._fetch_jwks", AsyncMock(return_value={"keys": []})), \
         patch("api.deps._validate_token", return_value=claims):
        return await require_auth(creds)


# ── Claim extraction ──────────────────────────────────────────────────────────

class TestGroupsClaim:
    @pytest.mark.asyncio
    async def test_absent_claim_becomes_none(self):
        """"The IdP sends no groups" — the stored role must be left alone."""
        user = await _auth_with({"sub": "s1", "preferred_username": "alice"})
        assert user.groups is None

    @pytest.mark.asyncio
    async def test_empty_claim_stays_an_empty_list(self):
        """"Removed from every group" — a deprovisioning signal that must act."""
        user = await _auth_with({"sub": "s1", "groups": []})
        assert user.groups == []

    @pytest.mark.asyncio
    async def test_the_two_are_distinguishable(self):
        absent = await _auth_with({"sub": "s1"})
        empty = await _auth_with({"sub": "s1", "groups": []})
        assert absent.groups is not empty.groups
        assert absent.groups is None and empty.groups == []

    @pytest.mark.asyncio
    async def test_groups_pass_through(self):
        user = await _auth_with({"sub": "s1", "groups": ["chy-admins", "other"]})
        assert user.groups == ["chy-admins", "other"]

    @pytest.mark.asyncio
    async def test_username_falls_back_to_sub(self):
        assert (await _auth_with({"sub": "s1"})).username == "s1"

    @pytest.mark.asyncio
    async def test_default_is_none_not_empty(self):
        """A CurrentUser built without groups must not claim the IdP said so."""
        assert CurrentUser(sub="s", username="u").groups is None


class TestDevMode:
    @pytest.mark.asyncio
    async def test_dev_user_carries_a_group_the_mapping_knows(self, monkeypatch):
        """Was ["admins"], which matches no role — the dev user would have
        provisioned as a consumer and then failed every admin endpoint."""
        monkeypatch.setattr(settings, "dev_mode", True)
        user = await require_auth(None)
        assert user.groups == ["chy-admins"]
        assert user.sub == "dev-actor"


# ── The shared pool double ────────────────────────────────────────────────────

class TestPoolDouble:
    @pytest.mark.asyncio
    async def test_actor_lookup_and_domain_query_are_answered_separately(self):
        """The whole point: a handler that looks up an actor and then queries
        its own table must get the right row for each."""
        pool = make_pool(actor=actor_row(role="reviewer"), fetchrow={"id": 1, "name": "block"})

        found = await pool.fetchrow("SELECT id, role FROM actors WHERE idp_sub = $1", "s")
        domain = await pool.fetchrow("SELECT * FROM context_blocks WHERE block_id = $1", "b")

        assert found["role"] == "reviewer"
        assert domain["name"] == "block"

    @pytest.mark.asyncio
    async def test_absent_actor_is_expressible(self):
        pool = make_pool(actor=None)
        assert await pool.fetchrow("SELECT id, role FROM actors WHERE idp_sub = $1", "s") is None

    @pytest.mark.asyncio
    async def test_a_single_domain_row_answers_repeated_calls(self):
        """Most handlers read the same row twice; a one-element side_effect list
        would raise StopIteration on the second read."""
        pool = make_pool(fetchrow={"v": 1})
        assert (await pool.fetchrow("SELECT 1"))["v"] == 1
        assert (await pool.fetchrow("SELECT 1"))["v"] == 1

    @pytest.mark.asyncio
    async def test_a_queue_is_consumed_in_order_then_yields_none(self):
        """None rather than StopIteration: an unanticipated extra query should
        surface as the endpoint's own 404, not as a traceback."""
        pool = make_pool(fetchrow=[{"v": 1}, {"v": 2}])
        assert (await pool.fetchrow("SELECT 1"))["v"] == 1
        assert (await pool.fetchrow("SELECT 1"))["v"] == 2
        assert await pool.fetchrow("SELECT 1") is None

    @pytest.mark.asyncio
    async def test_call_inspection_separates_the_two_kinds(self):
        """An assertion pinned to call_args_list[-1] silently starts inspecting
        an actor lookup as soon as a handler gains one."""
        pool = make_pool()
        await pool.fetchrow("SELECT id, role FROM actors WHERE idp_sub = $1", "s")
        await pool.fetchrow("SELECT * FROM context_blocks")

        assert len(actor_calls(pool)) == 1
        assert len(domain_calls(pool)) == 1
        assert "context_blocks" in domain_calls(pool)[0].args[0]

    @pytest.mark.asyncio
    async def test_transaction_queries_are_answered_too(self):
        pool = make_pool(conn_fetchrow={"id": 7})
        async with pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow("SELECT * FROM actors WHERE id = $1 FOR UPDATE", 7)
        assert row["id"] == 7

    def test_stub_user_defaults_to_no_groups_claim(self):
        """Test callers should not accidentally assert a deprovisioning signal."""
        assert stub_user().groups is None
