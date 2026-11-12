"""The scope hierarchy can be built through its own API (CHY-86).

Until now it could not. `api/v1/scopes.py` answered 501, CHY-59 had built the
table and the ltree path, and core/compose.py walked the tree at runtime — but
the only way to put a scope into the system was an INSERT.
scripts/e2e_context_lifecycle.py carried a --database-url flag for exactly that
reason, which meant the end-to-end check needed database credentials to test an
HTTP API.

The sharpest of these tests is the one about the path: a client-supplied ltree
path is the failure that does not announce itself. A typo does not raise — the
lookup simply returns nothing, and "no context here" is a plausible answer.
"""
from __future__ import annotations

import pathlib
import sys
from datetime import datetime, UTC
from uuid import UUID, uuid4

import asyncpg
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from kernel.audit import ACTION_SCOPE_CREATED  # noqa: E402
from kernel.schema import scope_label
from tests.conftest import actor_row, client_for, make_pool  # noqa: E402

_SCOPE = UUID("11111111-1111-1111-1111-111111111111")
_PARENT = UUID("22222222-2222-2222-2222-222222222222")
_ACTOR = UUID("33333333-3333-3333-3333-333333333333")
_NOW = datetime(2026, 8, 21, 12, 0, tzinfo=UTC)


def _node(**over) -> dict:
    base = {"id": _SCOPE, "scope_path": "org_x", "node_type": "org", "name": "Acme",
            "description": None, "parent_id": None, "created_by": _ACTOR,
            "created_at": _NOW}
    return {**base, **over}


class TestCreatingAScope:
    def test_a_root_scope_gets_a_single_label_path(self):
        pool = make_pool(actor=actor_row(_ACTOR, role="architect"),
                         conn_fetchrow=_node())
        with client_for(pool) as client:
            resp = client.post("/v1/scopes", json={"name": "Acme", "node_type": "org"})
            assert resp.status_code == 201, resp.text

        sql, args = pool.conn.fetchrow.await_args_list[-1].args[0], \
            pool.conn.fetchrow.await_args_list[-1].args[1:]
        assert "INSERT INTO scope_nodes" in sql
        path = args[1]
        assert "." not in path, f"a root scope has one label, got {path!r}"

    def test_a_child_scope_is_appended_to_its_parent(self):
        """The whole point of deriving rather than accepting."""
        pool = make_pool(
            actor=actor_row(_ACTOR, role="architect"),
            conn_fetchval="org_acme",                       # the parent's path
            conn_fetchrow=_node(parent_id=_PARENT, node_type="team"),
        )
        with client_for(pool) as client:
            resp = client.post("/v1/scopes", json={
                "name": "IT", "node_type": "team", "parent_id": str(_PARENT),
            })
            assert resp.status_code == 201, resp.text

        path = pool.conn.fetchrow.await_args_list[-1].args[2]
        assert path.startswith("org_acme."), path
        assert path.count(".") == 1, path

    def test_the_caller_cannot_supply_a_path(self):
        """A wrong ltree path does not fail loudly — it returns nothing.

        Pydantic ignores unknown fields, so the danger is not an error but a
        caller believing the field did something.
        """
        pool = make_pool(actor=actor_row(_ACTOR, role="architect"), conn_fetchrow=_node())
        with client_for(pool) as client:
            client.post("/v1/scopes", json={
                "name": "Acme", "node_type": "org", "scope_path": "totally.made.up",
            })
        path = pool.conn.fetchrow.await_args_list[-1].args[2]
        assert path != "totally.made.up"
        assert path == scope_label("org", path.split("_", 1)[1].replace("_", "-"))

    def test_an_unknown_parent_is_404_not_a_broken_tree(self):
        pool = make_pool(actor=actor_row(_ACTOR, role="architect"), conn_fetchval=None)
        with client_for(pool) as client:
            resp = client.post("/v1/scopes", json={
                "name": "IT", "node_type": "team", "parent_id": str(uuid4()),
            })
            assert resp.status_code == 404

    def test_a_blank_name_is_refused(self):
        pool = make_pool(actor=actor_row(_ACTOR, role="architect"))
        with client_for(pool) as client:
            assert client.post(
                "/v1/scopes", json={"name": "   ", "node_type": "org"}
            ).status_code == 422

    def test_an_unknown_node_type_is_refused(self):
        pool = make_pool(actor=actor_row(_ACTOR, role="architect"))
        with client_for(pool) as client:
            assert client.post(
                "/v1/scopes", json={"name": "X", "node_type": "galaxy"}
            ).status_code == 422

    def test_a_contributor_may_not_create_scopes(self):
        """Unlike the pilot endpoints, the role gate is real here.

        There it would have made an Article 14 decision unreachable in
        production. Structural work has no such problem: refusing until the
        chy-* groups exist (INFRA-92) is correct, not a gap.
        """
        pool = make_pool(actor=actor_row(_ACTOR, role="contributor"))
        with client_for(pool) as client:
            assert client.post(
                "/v1/scopes", json={"name": "Acme", "node_type": "org"}
            ).status_code == 403

    def test_creation_is_audited(self):
        """Same shape as _audit_payload in test_block_review.py.

        The first version of this test was an `or` chain across three call
        lists — one branch empty, one unfalsifiable. It passed for the wrong
        reason until the assertion was written the way the rest of the suite
        already does it.
        """
        pool = make_pool(actor=actor_row(_ACTOR, role="architect"), conn_fetchrow=_node())
        with client_for(pool) as client:
            client.post("/v1/scopes", json={"name": "Acme", "node_type": "org"})

        entry = next(c for c in pool.conn.fetchval.call_args_list
                     if "audit_log" in c.args[0])
        assert entry.args[2] == ACTION_SCOPE_CREATED
        assert entry.args[1] == _ACTOR, "the record has to name who created it"


class TestMembership:
    def test_admin_may_grant_a_role(self):
        pool = make_pool(
            actor=actor_row(_ACTOR, role="admin"),
            conn_fetchval=1,
            conn_fetchrow={"actor_id": _ACTOR, "role": "reviewer",
                           "granted_at": _NOW, "granted_by": _ACTOR},
        )
        with client_for(pool) as client:
            resp = client.post(f"/v1/scopes/{_SCOPE}/members",
                               json={"actor_id": str(_ACTOR), "role": "reviewer"})
            assert resp.status_code == 201, resp.text

    def test_a_reviewer_may_not_grant_roles(self):
        pool = make_pool(actor=actor_row(_ACTOR, role="reviewer"))
        with client_for(pool) as client:
            assert client.post(
                f"/v1/scopes/{_SCOPE}/members",
                json={"actor_id": str(_ACTOR), "role": "reviewer"},
            ).status_code == 403

    def test_a_second_membership_for_the_same_actor_is_409(self):
        """UNIQUE (scope_id, actor_id) from migration 001 is the rule.

        Checking first would be a second round trip and a race; this only
        translates the constraint into an explanation.
        """
        pool = make_pool(actor=actor_row(_ACTOR, role="admin"), conn_fetchval=1)
        pool.conn.fetchrow.side_effect = asyncpg.UniqueViolationError("duplicate key")
        with client_for(pool) as client:
            resp = client.post(f"/v1/scopes/{_SCOPE}/members",
                               json={"actor_id": str(_ACTOR), "role": "reviewer"})
            assert resp.status_code == 409

    def test_an_unknown_role_is_refused_before_it_reaches_the_database(self):
        """The CHECK on scope_memberships.role lists the same five values.

        Two places, one vocabulary — domain/roles.py (INFRA-92). This asserts
        the application half; tests/test_role_vocabulary.py guards the pair.
        """
        pool = make_pool(actor=actor_row(_ACTOR, role="admin"))
        with client_for(pool) as client:
            assert client.post(
                f"/v1/scopes/{_SCOPE}/members",
                json={"actor_id": str(_ACTOR), "role": "overlord"},
            ).status_code == 422

    @pytest.mark.parametrize("missing", ["scope", "actor"])
    def test_granting_into_nothing_is_404(self, missing):
        vals = {"scope": [None], "actor": [1, None]}[missing]
        pool = make_pool(actor=actor_row(_ACTOR, role="admin"), conn_fetchval=vals[0])
        if missing == "actor":
            pool.conn.fetchval.side_effect = vals
        with client_for(pool) as client:
            assert client.post(
                f"/v1/scopes/{_SCOPE}/members",
                json={"actor_id": str(uuid4()), "role": "reviewer"},
            ).status_code == 404


class TestListing:
    def test_scopes_come_back_ordered_by_path(self):
        pool = make_pool(fetch=[_node(scope_path="org_a"), _node(scope_path="org_a.team_b")])
        with client_for(pool) as client:
            resp = client.get("/v1/scopes")
            assert resp.status_code == 200
            assert len(resp.json()) == 2
        assert "ORDER BY scope_path" in pool.fetch.await_args_list[-1].args[0]

    def test_members_of_an_unknown_scope_are_404(self):
        pool = make_pool(fetchval=None)
        with client_for(pool) as client:
            assert client.get(f"/v1/scopes/{_SCOPE}/members").status_code == 404
