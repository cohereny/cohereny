"""
kernel/least_privilege.py

Refuse to run with more database rights than the application needs (CHY-85, ADR-009).

The protections against rewriting history are triggers (audit log, blocks,
measurements) and a SECURITY DEFINER function (cc_supersede_block). None of them
holds against a superuser, and none holds against the owner of the table — an
owner may disable a trigger. So the role the application connects as must be
neither, and it must not be able to create objects: whoever can create a table
can shadow a name inside the function.

Separating the roles (migrations/roles.py, migration 033) makes this possible;
checking it at start makes it true. A configuration that still points at the
superuser fails at once and says why, instead of running and leaving the
triggers to stop nothing but mistakes. There is no switch to turn it off — the
same line as authentication since CHY-192.

Called by:
  - core.db.create_pool   (API start)
  - cli/retention.py      (the purge)
  - kernel.identity       (`cc actor` administration)
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

#: One row of facts about the connected role. `owns_objects` counts membership:
#: a role that is a member of the owner may do what the owner may.
FACTS_SQL = """
SELECT current_user AS role,
       r.rolsuper AS superuser,
       r.rolbypassrls AS bypass_rls,
       EXISTS (SELECT 1
                 FROM pg_class c
                 JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = 'public'
                  AND pg_has_role(current_user, c.relowner, 'USAGE')) AS owns_objects,
       has_schema_privilege(current_user, 'public', 'CREATE') AS can_create,
       has_database_privilege(current_user, current_database(), 'TEMP') AS can_temp
  FROM pg_roles r
 WHERE r.rolname = current_user
"""


class ExcessPrivilegeError(RuntimeError):
    """The database role has rights the application must not have."""


def problems(facts: Mapping[str, Any]) -> list[str]:
    """What is wrong with a role, in words an operator can act on. Empty if nothing."""
    found = []
    if facts["superuser"]:
        found.append("is a superuser")
    if facts["bypass_rls"]:
        found.append("has BYPASSRLS")
    if facts["owns_objects"] and not facts["superuser"]:
        found.append("owns tables in schema public (or is a member of their owner)")
    if facts["can_create"] and not facts["superuser"]:
        found.append("may CREATE in schema public")
    if facts["can_temp"] and not facts["superuser"]:
        found.append("may create temporary tables")
    return found


async def check(conn) -> None:
    """Raise ExcessPrivilegeError if the connection's role may do more than it should."""
    facts = await conn.fetchrow(FACTS_SQL)
    found = problems(facts)
    if found:
        message = (
            f"Refusing to run as database role {facts['role']!r}: it "
            + "; it ".join(found)
            + ". Connect as the application role created by "
              "`python -m migrations.roles` (<database>_runtime), see README (CHY-85)."
        )
        raise ExcessPrivilegeError(message)
