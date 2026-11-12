"""
tests/test_role_vocabulary.py

Keeps the role vocabulary honest across the one boundary that cannot be
collapsed, and belt-and-braces for the ones that can.

Since INFRA-92 the vocabulary is defined once, in kernel/roles.py (moved there
from the dissolved domain/ by CHY-101). The ranking
is the declaration order and the Authentik group names are the prefix plus the
role name, so kernel/access.py and api/config.py derive rather than restate. Most
of what this file used to catch is now structurally impossible.

What is left is real: **SQL cannot import Python.** The CHECK constraints in
001_initial.sql — on actors.role and on scope_memberships.role — are a separate
statement of the same list, and nothing but this file connects them to the enum.
Add a role to the enum and the application will hand out a value the database
refuses on write; the failure lands on whoever logs in next.

Collapsing that too means replacing the CHECK with a seeded reference table and
foreign keys, so the list becomes data instead of DDL. Worth doing, tracked
separately, and a schema change rather than a refactor.

The assertions that are now structurally guaranteed are kept anyway. They cost
nothing to run and they fail loudly if somebody re-hardcodes a copy — which is
precisely how the three definitions came apart the first time. A guard whose
subject has been made safe is not wasted; it is the thing that notices when the
safety is removed.

Not hypothetical: CHY-33's ticket specified cc-readers → reader and
cc-editors → editor. Neither role exists in the CHECK.
"""
from __future__ import annotations

import re
from pathlib import Path

from kernel import access as authz
from kernel.roles import (
    DEFAULT_ROLE,
    GROUP_PREFIX,
    GROUP_ROLE_MAP,
    ROLE_ORDER as _ROLE_ORDER,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
INITIAL_SQL = REPO_ROOT / "migrations" / "001_initial.sql"

_CHECK = re.compile(r"role\s+TEXT\s+NOT NULL\s+CHECK\s*\(\s*role\s+IN\s*\(([^)]*)\)", re.I)


def _roles_in_schema() -> list[set[str]]:
    """Every CHECK on a role column, as a set per occurrence.

    A list rather than a union: actors and scope_memberships each carry their
    own constraint, and the interesting failure is them drifting apart from one
    another, not just from the code.
    """
    sql = INITIAL_SQL.read_text(encoding="utf-8")
    return [
        {v.strip().strip("'") for v in match.split(",")}
        for match in _CHECK.findall(sql)
    ]


def test_every_role_check_lists_the_same_roles():
    occurrences = _roles_in_schema()
    assert len(occurrences) >= 2, "expected a role CHECK on actors and scope_memberships"
    assert all(o == occurrences[0] for o in occurrences), (
        f"the role CHECKs disagree with each other: {occurrences}"
    )


def test_the_ranking_covers_exactly_the_schema_roles():
    schema = _roles_in_schema()[0]
    assert set(_ROLE_ORDER) == schema, (
        f"_ROLE_ORDER and the database disagree.\n"
        f"  only in code:   {set(_ROLE_ORDER) - schema}\n"
        f"  only in schema: {schema - set(_ROLE_ORDER)}"
    )


def test_the_ranking_is_a_total_order():
    """Two roles at the same rank would make has_at_least ambiguous and
    role_from_groups non-deterministic — max() would pick by dict order."""
    ranks = list(_ROLE_ORDER.values())
    assert len(set(ranks)) == len(ranks), f"duplicate ranks: {_ROLE_ORDER}"


def test_every_group_maps_to_a_role_the_database_accepts():
    schema = _roles_in_schema()[0]
    unknown = {g: r for g, r in GROUP_ROLE_MAP.items() if r not in schema}
    assert not unknown, (
        f"these Authentik groups map to roles the database will reject: {unknown}. "
        f"CHY-33 originally specified reader/editor, which are not schema roles."
    )


def test_every_role_is_reachable_from_some_group():
    """A role nobody can be granted is decoration. `architect` currently gates
    no endpoint — that is fine, CHY-86 will use it for POST /v1/scopes — but it
    must at least be assignable, or the gate lands on an empty set."""
    reachable = set(GROUP_ROLE_MAP.values())
    schema = _roles_in_schema()[0]
    assert schema == reachable, (
        f"roles nobody can be granted: {schema - reachable}"
    )


def test_the_default_role_exists_and_is_the_least_privileged():
    schema = _roles_in_schema()[0]
    assert DEFAULT_ROLE in schema
    assert _ROLE_ORDER[DEFAULT_ROLE] == min(_ROLE_ORDER.values()), (
        "a caller the IdP says nothing about must land on the lowest rung"
    )


def test_the_derivation_has_not_been_unpicked():
    """The consolidation itself. If someone pastes a literal dict back into
    kernel/access.py or api/config.py, everything above still passes — the copy
    would simply happen to be correct on the day it was written, which is how
    this started."""
    assert authz._ROLE_ORDER is _ROLE_ORDER, (
        "kernel/access.py no longer derives its ranking from kernel/roles.py"
    )
    # The second half of this assertion is gone with CHY-101: api/config.py no
    # longer mirrors the map at all, so there is nothing left to drift.


def test_groups_use_the_cohereny_prefix():
    wrong = [g for g in GROUP_ROLE_MAP if not g.startswith(GROUP_PREFIX)]
    assert not wrong, (
        f"cohereny groups must be prefixed chy-, not: {wrong}. "
        f"cc- belongs to another project on the same identity provider "
        f"(INFRA-92)."
    )
