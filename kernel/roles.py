"""
domain/roles.py

The role vocabulary, defined once (INFRA-92).

It used to live in three places: the CHECK constraints in 001_initial.sql, the
ranking in core/authz.py, and the Authentik group mapping in api/config.py.
Three hand-maintained copies of one list, with nothing connecting them — and
CHY-33's ticket proposed a fourth (reader/editor) that matched none of them.

Two of the three are now derived from what is below. The ranking is the
declaration order, and the group names are the prefix plus the role name, so
adding a role here gives it a rank and a group automatically. There is no way
to add one and forget the others.

The third cannot be collapsed from here: SQL cannot import Python. The CHECK in
001_initial.sql remains a separate statement of the same list, and
tests/test_role_vocabulary.py exists solely to keep that one boundary honest.
Making the database derive it too would mean replacing the CHECK with a seeded
reference table and foreign keys — worth doing, tracked separately, and a
schema change rather than a refactor.

Why the order is the rank:
  A separate rank mapping is a second thing to keep in step, and two roles at
  the same rank would make `max()` in role_from_groups pick by dictionary
  order — a privilege decision resolved by chance. Declaration order cannot
  produce a tie.

Least privileged first. Adding a role in the middle shifts the ranks of the
ones above it, which is correct: the numbers are ordinal, never stored.

Called by:
  - core/authz.py    (ranking, has_at_least)
  - api/config.py    (group mapping, default role)

Dependencies:
  - none, deliberately. This module must be importable from anywhere, including
    from api.config, so it may not import from api or core.
"""
from __future__ import annotations

from enum import StrEnum

# The Authentik prefix for cohereny groups. Not "cc-": in the reference
# installation another project shares the identity provider and uses that
# prefix. A cohereny group prefixed cc- reads as belonging to it (INFRA-92).
GROUP_PREFIX = "chy-"


class Role(StrEnum):
    """The five roles, least privileged first. Order is meaning."""

    CONSUMER = "consumer"        # read
    CONTRIBUTOR = "contributor"  # create, edit and submit blocks
    REVIEWER = "reviewer"        # approve, reject, retire
    ARCHITECT = "architect"      # scope structure — reserved, see CHY-86
    ADMIN = "admin"              # actor anonymisation, other actors' audit trail


#: role name → rank. Derived from declaration order; ties are impossible.
ROLE_ORDER: dict[str, int] = {role.value: rank for rank, role in enumerate(Role)}

#: Every valid value of actors.role and scope_memberships.role.
ROLE_NAMES: frozenset[str] = frozenset(role.value for role in Role)

#: Authentik group → role. Plural of the role name, so a new role brings its
#: own group and the two cannot disagree.
GROUP_ROLE_MAP: dict[str, str] = {
    f"{GROUP_PREFIX}{role.value}s": role.value for role in Role
}

#: For a caller whose groups match nothing, and for the first sight of a caller
#: whose token carries no groups claim. Least privilege by construction: a
#: misconfigured mapping must not hand out access.
DEFAULT_ROLE: str = Role.CONSUMER.value

#: Authentik group that marks a technical account — CI, automation (CHY-163).
#: Not a role: a CI account needs a role to do its work and is still not a
#: person. It never counts towards the people who may approve, and never
#: approves a block that lets data leave the installation (ADR-045).
SERVICE_ACCOUNT_GROUP: str = f"{GROUP_PREFIX}service-accounts"

#: The roles that may approve a block — the minimum of the `approve`
#: transition and everything above it. Derived from the order, so a role added
#: above reviewer is counted without anyone remembering to list it.
APPROVING_ROLES: tuple[str, ...] = tuple(
    role.value for role in Role if ROLE_ORDER[role.value] >= ROLE_ORDER[Role.REVIEWER.value]
)
