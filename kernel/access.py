"""
kernel/access.py

May this actor do this? Nothing else (ADR-030, CHY-120).

Split out of what CHY-101 moved here wholesale. That version carried the actor
lifecycle and ten SQL statements, which made a role check untestable without a
database — and arc42 §5.7 had said since v2.9 why that is the wrong shape:

    Dass access.py SQL-frei ist, ist kein Zufall, sondern die Bedingung:
    Rollenlogik wird damit ohne Datenbank testbar.

The line runs between two different questions. *Who is calling* — resolving a
token subject to an actor row, projecting IdP groups onto a role — is
kernel/identity.py. *What may they do* is here, and it is a pure function over
a value object.

This module therefore imports no pool, no identity, no IdP vocabulary beyond
the ranking. The dependency points one way: identity → access.

Called by:
  - kernel.identity (constructs ResolvedActor)
  - api/v1/{admin,audit,blocks,scopes}.py (require)

Dependencies:
  - kernel.errors (AccessDenied)
  - kernel.roles  (the ranking)
"""
from __future__ import annotations

from uuid import UUID

from kernel.errors import AccessDenied
from kernel.roles import ROLE_ORDER

# Derived, not restated. The vocabulary lives in kernel/roles.py (INFRA-92);
# a second copy here is how the three definitions drifted apart in the first
# place. The private name stays so call sites need no change.
_ROLE_ORDER: dict[str, int] = ROLE_ORDER


class ResolvedActor:
    """The caller's actor row, as far as authorization cares about it.

    Lives here rather than in identity.py because its only behaviour is a
    permission question. identity.py produces it; this module interrogates it.
    """

    __slots__ = ("id", "role")

    def __init__(self, actor_id: UUID, role: str) -> None:
        self.id = actor_id
        self.role = role

    def has_at_least(self, minimum: str) -> bool:
        return _ROLE_ORDER.get(self.role, -1) >= _ROLE_ORDER[minimum]


def require(actor: ResolvedActor, minimum: str) -> ResolvedActor:
    """Refuse unless the actor holds at least `minimum`.

    No pool, no await, no database. That is the whole point of this module:
    the rule can be exercised by constructing a ResolvedActor and calling this,
    which is what tests/test_access.py does.

    Returns the actor so a call site can read as one expression where that is
    clearer than two statements.
    """
    if minimum not in _ROLE_ORDER:
        raise ValueError(f"unknown role {minimum!r}; expected one of {list(_ROLE_ORDER)}")
    if not actor.has_at_least(minimum):
        raise AccessDenied(required=minimum, actual=actor.role)
    return actor
