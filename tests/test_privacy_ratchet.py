"""
tests/test_privacy_ratchet.py

The privacy ratchet (CHY-62, ADR-017): a privacy rule may be tightened as it
moves down the scope tree, never loosened.

The mandatory case ADR-017 names is `test_the_case_adr_017_demanded` — a
project block trying to switch cloud routing back on against a healthcare
template that switched it off. It is worth stating why the counter-probes
around it matter just as much: a check that refused *every* descendant claim
would pass that test and be a wall rather than a ratchet, and nobody would
notice until a team could no longer tighten a rule for itself.
"""
from __future__ import annotations

import re
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from contexts.repository import ratchet
from contexts.repository.errors import (
    GovernanceKeyLocked,
    GovernanceNotEditable,
    PrivacyRatchetViolation,
    UnknownGovernanceKey,
)
from contexts.repository.ratchet import GovernanceClaim
from contexts.repository.schema import PrivacyImpact
from tests.conftest import actor_row, client_for, make_pool

_MIGRATION = (Path(__file__).resolve().parents[1]
              / "migrations" / "016_privacy_ratchet.sql").read_text(encoding="utf-8")

_ORG = UUID("11111111-1111-1111-1111-111111111111")
_TEAM = UUID("22222222-2222-2222-2222-222222222222")
_PROJECT = UUID("33333333-3333-3333-3333-333333333333")

_KEY = "cloud_routing"


def _claim(scope, depth, impact, *, key=_KEY, locked=False, block_id=None):
    return GovernanceClaim(
        block_id=block_id or uuid4(),
        scope_id=scope,
        depth=depth,
        governance_key=key,
        privacy_impact=impact,
        locked=locked,
    )


# ── The rule ──────────────────────────────────────────────────────────────────

class TestTheRatchet:
    def test_the_case_adr_017_demanded(self):
        """A project block may not switch cloud routing back on.

        The worked example of ADR-017, verbatim: the healthcare template
        forbids cloud routing at the org level, and a project block below it
        tries to allow it again.
        """
        org = _claim(_ORG, 1, "restrictive")
        project = _claim(_PROJECT, 3, "permissive")

        with pytest.raises(PrivacyRatchetViolation) as refused:
            ratchet.check_claim(project, [org])

        assert refused.value.key == _KEY
        assert refused.value.ancestor_scope == _ORG
        # The message has to name where the rule is stated, or the author is
        # told they may not do something without being told where to go.
        assert str(_ORG) in str(refused.value)

    def test_tightening_downwards_is_the_whole_point(self):
        """The counter-probe to the test above.

        A check that refused every descendant claim would pass the mandatory
        case and still be wrong. This is the assertion that tells a ratchet
        from a wall.
        """
        org = _claim(_ORG, 1, "permissive")
        project = _claim(_PROJECT, 3, "restrictive")
        ratchet.check_claim(project, [org])  # must not raise

    def test_two_restrictive_statements_do_not_collide(self):
        ratchet.check_claim(
            _claim(_PROJECT, 3, "restrictive"), [_claim(_ORG, 1, "restrictive")]
        )

    def test_two_permissive_statements_do_not_collide(self):
        """Nothing is being loosened — the rule was already permissive."""
        ratchet.check_claim(
            _claim(_PROJECT, 3, "permissive"), [_claim(_ORG, 1, "permissive")]
        )

    def test_a_different_key_is_a_different_rule(self):
        """The reason the key exists at all.

        Without it every governance block in a chain would be compared with
        every other, and a team's PII rule would be refused for contradicting
        an org rule about cloud routing.
        """
        ratchet.check_claim(
            _claim(_PROJECT, 3, "permissive", key="pii_redaction"),
            [_claim(_ORG, 1, "restrictive", key="cloud_routing")],
        )

    def test_a_block_without_a_claim_is_never_refused(self):
        """The overwhelming majority of blocks. If this ever raises, the
        ratchet has become a tax on every approval instead of a rule about
        governance blocks."""
        ratchet.check_claim(None, [_claim(_ORG, 1, "restrictive")])

    def test_a_restrictive_sibling_at_the_same_depth_still_binds(self):
        """Same scope, contradicting claims, and no depth to break the tie.

        Refused: there is no rule that says which of two statements in one
        scope wins, and inventing one would make the outcome depend on row
        order.
        """
        with pytest.raises(PrivacyRatchetViolation):
            ratchet.check_claim(
                _claim(_TEAM, 2, "permissive"), [_claim(_TEAM, 2, "restrictive")]
            )


# ── locked ────────────────────────────────────────────────────────────────────

class TestLocked:
    def test_a_locked_ancestor_refuses_even_a_tightening(self):
        """`locked` is stronger than the ratchet on purpose.

        A team restating an org rule — however strictly — creates a second
        place where one rule lives. Two sources for one truth drift.
        """
        with pytest.raises(GovernanceKeyLocked) as refused:
            ratchet.check_claim(
                _claim(_PROJECT, 3, "restrictive"), [_claim(_ORG, 1, "restrictive", locked=True)]
            )
        assert refused.value.ancestor_scope == _ORG

    def test_locked_is_reported_before_the_ratchet(self):
        """Both rules apply; the caller must hear the real reason.

        A permissive claim under a locked restrictive ancestor breaks both.
        Reporting the ratchet would send the author off to write a restrictive
        version that is also going to be refused.
        """
        with pytest.raises(GovernanceKeyLocked):
            ratchet.check_claim(
                _claim(_PROJECT, 3, "permissive"), [_claim(_ORG, 1, "restrictive", locked=True)]
            )

    def test_locked_does_not_bind_its_own_scope(self):
        """locked speaks about descendants.

        A second block in the same scope is that scope's own business —
        refusing it here would leave a scope unable to revise its own
        governance, with an error nobody can act on.
        """
        ratchet.check_claim(
            _claim(_ORG, 1, "restrictive"), [_claim(_ORG, 1, "restrictive", locked=True)]
        )

    def test_a_locked_key_below_is_not_retroactive(self):
        """A locked claim deeper down does not bind what is above it."""
        ratchet.check_claim(
            _claim(_ORG, 1, "permissive"), [_claim(_PROJECT, 3, "restrictive", locked=True)]
        )


# ── The whole chain at once ───────────────────────────────────────────────────

class TestComposition:
    def test_a_conflicting_chain_is_refused(self):
        with pytest.raises(PrivacyRatchetViolation):
            ratchet.check_composition(
                [_claim(_ORG, 1, "restrictive"), _claim(_PROJECT, 3, "permissive")]
            )

    def test_order_of_input_does_not_decide_who_is_blamed(self):
        """Shuffled input must accuse the project block, not the org block.

        Unsorted, the first comparison would judge the org claim against the
        project claim and refuse the org block for contradicting something
        below it — a refusal that names the wrong scope sends the fix to the
        wrong place.
        """
        org = _claim(_ORG, 1, "restrictive")
        project = _claim(_PROJECT, 3, "permissive")
        with pytest.raises(PrivacyRatchetViolation) as refused:
            ratchet.check_composition([project, org])
        assert refused.value.ancestor_scope == _ORG

    def test_a_clean_chain_passes(self):
        ratchet.check_composition(
            [
                _claim(_ORG, 1, "permissive"),
                _claim(_TEAM, 2, "restrictive"),
                _claim(_PROJECT, 3, "restrictive", key="pii_redaction"),
            ]
        )

    def test_an_empty_chain_passes(self):
        ratchet.check_composition([])


# ── The vocabulary ────────────────────────────────────────────────────────────

class TestVocabulary:
    def test_an_unknown_key_is_refused_by_name(self):
        with pytest.raises(UnknownGovernanceKey) as refused:
            ratchet.check_key_known("clod_routing", ("cloud_routing", "pii_redaction"))
        # The alternatives belong in the message: a typo is the expected cause,
        # and the fix is one of the values it does not list otherwise.
        assert "cloud_routing" in str(refused.value)

    def test_no_key_is_not_an_unknown_key(self):
        ratchet.check_key_known(None, ("cloud_routing",))

    def test_creating_a_block_with_an_unknown_key_answers_422(self):
        """Driven through the endpoint, not by calling check_key_known again.

        The unit test above proves the decision; this proves it is consulted.
        A probe that deleted the call from create_block left the unit test
        green — which is how a check ends up existing and protecting nothing.
        """
        pool = make_pool(
            actor=actor_row(role="contributor"),
            fetch=[{"key": "cloud_routing"}, {"key": "pii_redaction"}],
        )
        with client_for(pool) as client:
            response = client.post("/v1/blocks", json={
                "scope_id": str(_PROJECT),
                "block_type": "system_prompt",
                "content": "x",
                "governance_key": "clod_routing",
                "privacy_impact": "restrictive",
            })

        assert response.status_code == 422, response.text
        assert "cloud_routing" in response.json()["detail"]
        # Nothing was inserted — on the pool or in a transaction. The insert
        # moved onto the connection with ADR-042, and a check that looked only
        # at the pool would have gone on passing without looking at anything.
        inserted = [c.args[0] for c in (*pool.fetchrow.await_args_list,
                                        *pool.conn.fetchrow.await_args_list)
                    if "INSERT INTO context_blocks" in c.args[0]]
        assert not inserted, "the block was written before the key was checked"

    def test_creating_a_block_with_a_known_key_succeeds(self):
        """The counter-probe. Without it the test above would also pass against
        a create endpoint that refuses every governance key."""
        pool = make_pool(
            actor=actor_row(role="contributor"),
            fetch=[{"key": "cloud_routing"}],
            conn_fetchrow={
                "id": uuid4(), "block_id": uuid4(), "version": 1,
                "superseded_by_version": None, "scope_id": _PROJECT,
                "block_type": "system_prompt", "content": "x", "metadata": {},
                "tags": [], "author_id": uuid4(), "status": "draft",
                "governance_key": _KEY, "privacy_impact": "restrictive", "locked": False,
                "created_at": "2026-09-03T09:00:00+00:00",
            },
        )
        with client_for(pool) as client:
            response = client.post("/v1/blocks", json={
                "scope_id": str(_PROJECT),
                "block_type": "system_prompt",
                "content": "x",
                "governance_key": _KEY,
                "privacy_impact": "restrictive",
            })

        assert response.status_code == 201, response.text
        assert response.json()["governance_key"] == _KEY

    def test_the_seeded_keys_exist_in_the_migration(self):
        """Python and SQL name the same vocabulary.

        The seeds are the only keys a fresh database has, so a test that used
        an invented key would be testing against a database nobody can create.
        """
        seeded = set(re.findall(r"^\s*\('([a-z_]+)',", _MIGRATION, re.M))
        assert _KEY in seeded, f"{_KEY!r} is not seeded by migration 016: {sorted(seeded)}"

    def test_the_two_impacts_match_the_check_constraint(self):
        """PRIVACY_IMPACTS, the Literal and the CHECK are one list in three
        places. Same drift guard as ALL_STATUSES against migration 013."""
        constraint = re.search(
            r"privacy_impact IN \(([^)]*)\)", _MIGRATION
        )
        assert constraint, "privacy_impact CHECK not found in migration 016"
        in_sql = set(re.findall(r"'([a-z]+)'", constraint.group(1)))
        assert in_sql == set(ratchet.PRIVACY_IMPACTS)
        assert in_sql == set(PrivacyImpact.__args__)


# ── Governance is settled in draft ────────────────────────────────────────────

class TestGovernanceEditable:
    def test_a_supersede_may_not_carry_a_governance_change(self):
        with pytest.raises(GovernanceNotEditable) as refused:
            ratchet.check_governance_editable(
                plan_supersedes=True,
                requested={"locked": True, "governance_key": None, "privacy_impact": None},
            )
        assert "locked" in str(refused.value)

    def test_a_supersede_without_a_governance_change_is_fine(self):
        ratchet.check_governance_editable(
            plan_supersedes=True,
            requested={"locked": None, "governance_key": None, "privacy_impact": None},
        )

    def test_a_draft_may_change_its_claim(self):
        ratchet.check_governance_editable(
            plan_supersedes=False, requested={"locked": True}
        )


# ── Through the endpoint ──────────────────────────────────────────────────────

_ANCESTOR_QUERY = "scope_path @>"
_CLAIM_QUERY = "governance_key = $2"


def _chain_and_claims(claims: list[dict], seen: list[str] | None = None):
    """Answer the two set-returning queries the approval path issues.

    One fixed list would answer both, which is how a test goes green without
    the rule ever being consulted.

    `seen` collects the SQL that was asked. Recorded here rather than read off
    the mock afterwards: what the handler asked for is the assertion, and
    reaching into a MagicMock's call list ties the test to the shape of the
    double instead of to the behaviour.
    """
    chain = [
        {"id": _ORG, "scope_path": "org", "node_type": "org", "name": "org", "depth": 1, "privacy_level": None},
        {"id": _TEAM, "scope_path": "org.team", "node_type": "team", "name": "team", "depth": 2, "privacy_level": None},
        {"id": _PROJECT, "scope_path": "org.team.p", "node_type": "project", "name": "p", "depth": 3, "privacy_level": None},
    ]

    def answer(sql, *args):
        if seen is not None:
            seen.append(sql)
        if _ANCESTOR_QUERY in sql:
            return chain
        if _CLAIM_QUERY in sql:
            return claims
        return []

    return answer


def _block_row(**kw):
    return {
        "id": uuid4(),
        "block_id": kw.get("block_id", uuid4()),
        "version": 1,
        "status": "pending_review",
        "scope_id": kw.get("scope_id", _PROJECT),
        "author_id": uuid4(),
        "governance_key": kw.get("governance_key"),
        "privacy_impact": kw.get("privacy_impact"),
        "locked": kw.get("locked", False),
        "block_type": kw.get("block_type", "system_prompt"),
        "metadata": kw.get("metadata", {}),
    }


def test_approving_a_loosening_block_answers_409():
    """The rule as a client sees it.

    The unit tests above prove the decision; this proves it is actually
    consulted on the way to `active` and arrives as 409 rather than as a 500
    from somewhere deeper.
    """
    block_id = uuid4()
    pool = make_pool(
        actor=actor_row(role="reviewer"),
        conn_fetchrow=_block_row(block_id=block_id, governance_key=_KEY,
                                 privacy_impact="permissive"),
        conn_fetch=_chain_and_claims([
            {"block_id": uuid4(), "scope_id": _ORG, "governance_key": _KEY,
             "privacy_impact": "restrictive", "locked": False},
        ]),
    )
    with client_for(pool) as client:
        response = client.post(f"/v1/blocks/{block_id}/approve", json={"version": 1})

    assert response.status_code == 409
    assert _KEY in response.json()["detail"]


def test_approving_a_tightening_block_succeeds():
    """The counter-probe at endpoint level.

    Without it the test above would also pass against an approve endpoint that
    refuses everything.
    """
    block_id = uuid4()
    pool = make_pool(
        actor=actor_row(role="reviewer"),
        conn_fetchrow=[
            _block_row(block_id=block_id, governance_key=_KEY, privacy_impact="restrictive"),
            {"id": uuid4(), "block_id": block_id, "version": 1,
             "superseded_by_version": None, "scope_id": _PROJECT,
             "block_type": "system_prompt", "content": "x", "metadata": {},
             "tags": [], "author_id": uuid4(), "status": "active",
             "governance_key": _KEY, "privacy_impact": "restrictive", "locked": False,
             "created_at": "2026-09-03T09:00:00+00:00"},
        ],
        conn_fetch=_chain_and_claims([
            {"block_id": uuid4(), "scope_id": _ORG, "governance_key": _KEY,
             "privacy_impact": "permissive", "locked": False},
        ]),
    )
    with client_for(pool) as client:
        response = client.post(f"/v1/blocks/{block_id}/approve", json={"version": 1})

    assert response.status_code == 200, response.text


def test_approving_a_block_without_a_claim_asks_nothing():
    """A block that makes no claim must not pay for the ratchet.

    The assertion is on the queries, not on the status code: if the chain were
    loaded anyway, every approval in the system would grow two round trips for
    a rule that cannot apply to it.
    """
    block_id = uuid4()
    pool = make_pool(
        actor=actor_row(role="reviewer"),
        conn_fetchrow=[
            _block_row(block_id=block_id),
            {"id": uuid4(), "block_id": block_id, "version": 1,
             "superseded_by_version": None, "scope_id": _PROJECT,
             "block_type": "system_prompt", "content": "x", "metadata": {},
             "tags": [], "author_id": uuid4(), "status": "active",
             "governance_key": None, "privacy_impact": None, "locked": False,
             "created_at": "2026-09-03T09:00:00+00:00"},
        ],
        conn_fetch=_chain_and_claims([], seen := []),
    )
    with client_for(pool) as client:
        assert client.post(f"/v1/blocks/{block_id}/approve", json={"version": 1}).status_code == 200

    assert not any(_ANCESTOR_QUERY in sql or _CLAIM_QUERY in sql for sql in seen), (
        "the ratchet loaded the scope chain for a block that makes no claim"
    )


# ── Through the composition path ──────────────────────────────────────────────

@pytest.mark.anyio
async def test_composing_a_conflicting_scope_is_refused():
    """The backstop, exercised through compose_from_db rather than asserted.

    `approve` should make this unreachable. It is here because several versions
    of a block can be active at once by design, and rows reach `active` by
    paths that do not pass through the endpoint — the same gap that let a
    retired block reappear on 2026-08-13.

    Driven through the caller, not by calling check_composition again: the unit
    tests above already cover the decision, and a probe that removes the call
    from compose_from_db has to turn this red.
    """
    from unittest.mock import AsyncMock, MagicMock

    from contexts.repository.compose import compose_from_db
    from contexts.repository.schema import ComposeRequest

    chain = [
        {"id": _ORG, "scope_path": "org", "depth": 1, "privacy_level": None},
        {"id": _PROJECT, "scope_path": "org.p", "depth": 3, "privacy_level": None},
    ]
    rows = [
        {"block_id": uuid4(), "version": 1, "block_type": "system_prompt",
         "content": "no cloud", "scope_id": _ORG, "tags": [],
         "created_at": "2026-01-01T00:00:00+00:00",
         "governance_key": _KEY, "privacy_impact": "restrictive", "locked": False},
        {"block_id": uuid4(), "version": 1, "block_type": "system_prompt",
         "content": "cloud is fine", "scope_id": _PROJECT, "tags": [],
         "created_at": "2026-01-02T00:00:00+00:00",
         "governance_key": _KEY, "privacy_impact": "permissive", "locked": False},
    ]
    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=rows)

    with pytest.raises(PrivacyRatchetViolation):
        await compose_from_db(pool, ComposeRequest(scope_id=_PROJECT), chain)


@pytest.mark.anyio
async def test_composing_a_clean_scope_returns_the_claims():
    """The counter-probe, and the reason the fields are on ComposedBlock.

    A composed context that carries the claims is the record that the ratchet
    was satisfied at compile time — without them a caller cannot tell a checked
    composition from an unchecked one.
    """
    from unittest.mock import AsyncMock, MagicMock

    from contexts.repository.compose import compose_from_db
    from contexts.repository.schema import ComposeRequest

    chain = [
        {"id": _ORG, "scope_path": "org", "depth": 1, "privacy_level": None},
        {"id": _PROJECT, "scope_path": "org.p", "depth": 3, "privacy_level": None},
    ]
    rows = [
        {"block_id": uuid4(), "version": 1, "block_type": "system_prompt",
         "content": "no cloud", "scope_id": _ORG, "tags": [],
         "created_at": "2026-01-01T00:00:00+00:00",
         "governance_key": _KEY, "privacy_impact": "permissive", "locked": False},
        {"block_id": uuid4(), "version": 1, "block_type": "system_prompt",
         "content": "definitely no cloud", "scope_id": _PROJECT, "tags": [],
         "created_at": "2026-01-02T00:00:00+00:00",
         "governance_key": _KEY, "privacy_impact": "restrictive", "locked": False},
    ]
    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=rows)

    blocks = await compose_from_db(pool, ComposeRequest(scope_id=_PROJECT), chain)

    assert [b.privacy_impact for b in blocks] == ["permissive", "restrictive"]
    assert all(b.governance_key == _KEY for b in blocks)
