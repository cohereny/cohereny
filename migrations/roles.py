"""
migrations/roles.py

Creates the database roles Cohereny runs with, once per database (CHY-85, ADR-009).

Two login roles per database, and one group:

  <db>_migrator   owns the database and every object in it. migrations/runner.py
                  connects as this role, and nothing else does.
  <db>_runtime    what the API, the CLI and the purge connect as: neither
                  superuser nor owner, no BYPASSRLS, no CREATE, no temporary
                  tables. Member of cohereny_app.
  cohereny_app    NOLOGIN. Holds the table privileges migration 033 grants, so
                  a migration can name one role whatever an installation calls
                  its login roles.

Why the owner must not be the application: an owner may ALTER TABLE ... DISABLE
TRIGGER, a superuser may do anything, and the triggers that keep the audit log
and the blocks append-only (migrations 011, 012) hold only against a role that
is neither. api/main.py refuses to start with more (kernel/least_privilege.py).

CONNECT is revoked from PUBLIC and granted to the two roles of the database, so
on a server with several databases — the reference installation runs dev, test
and prod on one — the credentials of one do not open another. TEMP goes with it:
a temporary table can shadow a name inside a SECURITY DEFINER function.

Idempotent. A second run changes nothing but the passwords, which is how they
are rotated. Run with a superuser connection, before the migrations:

  DB_SUPERUSER_URL=postgresql://postgres:...@host:5432/postgres \\
  COHERENY_MIGRATOR_PASSWORD=... COHERENY_RUNTIME_PASSWORD=... \\
  python -m migrations.roles --database cohereny

The passwords come from the environment and are named after the database: for
`cohereny_dev` they are COHERENY_DEV_MIGRATOR_PASSWORD and
COHERENY_DEV_RUNTIME_PASSWORD. Never from the command line, where `ps` shows them.

An installation from before CHY-85 has every object owned by the superuser it
connected as. This script hands them to <db>_migrator — tables, functions and
types in the schema `public`, not what an extension brought along.

Dependencies:
  - asyncpg
"""
from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys

import asyncpg

#: The group the migrations grant to. Fixed, so 033 can name it.
APP_GROUP = "cohereny_app"

#: What a database name may look like here. Role names are derived from it, and
#: an unexpected character would end up in a role name.
_DATABASE_NAME = re.compile(r"^[a-z_][a-z0-9_]{0,40}$")

#: The two login roles, created and then narrowed again on every run, so that a
#: role someone widened by hand is put back. %I is the role, %L its password.
_CREATE_LOGIN = "CREATE ROLE %I LOGIN"
_NARROW_LOGIN = ("ALTER ROLE %I LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
                 "NOREPLICATION NOBYPASSRLS PASSWORD %L")
_CREATE_GROUP = "CREATE ROLE %I NOLOGIN"
_NARROW_GROUP = ("ALTER ROLE %I NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
                 "NOREPLICATION NOBYPASSRLS")

#: ALTER <kind> <name> OWNER TO <migrator>, by the kind the catalog query names.
_OWNER_TO = {
    kind: f"ALTER {kind} %s OWNER TO %I"
    for kind in ("TABLE", "VIEW", "MATERIALIZED VIEW", "SEQUENCE", "FOREIGN TABLE",
                 "ROUTINE", "TYPE", "DOMAIN")
}


def role_names(database: str) -> tuple[str, str]:
    """(migration role, application role) for a database."""
    return f"{database}_migrator", f"{database}_runtime"


def password_variables(database: str) -> tuple[str, str]:
    """The environment variables holding the two passwords for a database."""
    prefix = database.upper()
    return f"{prefix}_MIGRATOR_PASSWORD", f"{prefix}_RUNTIME_PASSWORD"


async def _run(conn: asyncpg.Connection, template: str, *args: str) -> None:
    """Execute a statement that PostgreSQL assembles from a template.

    `format()` runs on the server, with the template and the values bound as
    parameters: %I quotes an identifier, %L a literal, %s takes a name the
    catalog already quoted. Nothing is pasted into SQL on this side — a role name
    or a password cannot change what the statement does.
    """
    statement = await conn.fetchval(
        "SELECT format($1::text, VARIADIC $2::text[])", template, list(args)
    )
    await conn.execute(statement)


async def _ensure_role(conn: asyncpg.Connection, name: str, create: str) -> bool:
    """Create the role if it is missing. True if it was created."""
    if await conn.fetchval("SELECT 1 FROM pg_roles WHERE rolname = $1", name):
        return False
    await _run(conn, create, name)
    return True


async def _transfer_ownership(conn: asyncpg.Connection, migrator: str) -> int:
    """Hand every object in `public` that another role owns to the migrator.

    Skips what an extension owns (ltree, uuid-ossp and their functions) and
    sequences that belong to a column: those follow their table.
    """
    rows = await conn.fetch(
        """
        SELECT CASE c.relkind WHEN 'v' THEN 'VIEW'
                              WHEN 'm' THEN 'MATERIALIZED VIEW'
                              WHEN 'S' THEN 'SEQUENCE'
                              WHEN 'f' THEN 'FOREIGN TABLE'
                              ELSE 'TABLE' END AS kind,
               format('%I.%I', n.nspname, c.relname) AS name
          FROM pg_class c
          JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE n.nspname = 'public'
           AND c.relkind IN ('r', 'p', 'v', 'm', 'f', 'S')
           AND c.relowner <> (SELECT oid FROM pg_roles WHERE rolname = $1)
           AND NOT EXISTS (SELECT 1 FROM pg_depend d
                            WHERE d.classid = 'pg_class'::regclass
                              AND d.objid = c.oid AND d.deptype = 'e')
           -- a SERIAL or IDENTITY sequence follows its table
           AND NOT (c.relkind = 'S' AND EXISTS (
                     SELECT 1 FROM pg_depend d
                      WHERE d.classid = 'pg_class'::regclass
                        AND d.objid = c.oid AND d.deptype IN ('a', 'i')))
        UNION ALL
        SELECT 'ROUTINE', p.oid::regprocedure::text
          FROM pg_proc p
         WHERE p.pronamespace = 'public'::regnamespace
           AND p.proowner <> (SELECT oid FROM pg_roles WHERE rolname = $1)
           AND NOT EXISTS (SELECT 1 FROM pg_depend d
                            WHERE d.classid = 'pg_proc'::regclass
                              AND d.objid = p.oid AND d.deptype = 'e')
        UNION ALL
        SELECT CASE t.typtype WHEN 'd' THEN 'DOMAIN' ELSE 'TYPE' END,
               format('%I.%I', 'public', t.typname)
          FROM pg_type t
         WHERE t.typnamespace = 'public'::regnamespace
           AND t.typtype IN ('e', 'd', 'r')
           AND t.typowner <> (SELECT oid FROM pg_roles WHERE rolname = $1)
           AND NOT EXISTS (SELECT 1 FROM pg_depend d
                            WHERE d.classid = 'pg_type'::regclass
                              AND d.objid = t.oid AND d.deptype = 'e')
        """,
        migrator,
    )
    for row in rows:
        # The name came out of format(%I) or regprocedure, quoted the way
        # PostgreSQL quotes; the kind picks one of the constant templates.
        await _run(conn, _OWNER_TO[row["kind"]], row["name"], migrator)
    return len(rows)


async def provision(superuser_url: str, database: str,
                    migrator_password: str, runtime_password: str) -> dict[str, int | bool]:
    """Create or update the roles of one database. Returns what changed."""
    if not _DATABASE_NAME.match(database):
        message = f"not a database name this script accepts: {database!r}"
        raise ValueError(message)
    migrator, runtime = role_names(database)

    conn = await asyncpg.connect(superuser_url)
    try:
        if not await conn.fetchval("SELECT rolsuper FROM pg_roles WHERE rolname = current_user"):
            message = "DB_SUPERUSER_URL must connect as a superuser: it creates roles"
            raise PermissionError(message)
        if not await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", database):
            message = f"database {database!r} does not exist"
            raise LookupError(message)

        created_group = await _ensure_role(conn, APP_GROUP, _CREATE_GROUP)
        await _run(conn, _NARROW_GROUP, APP_GROUP)

        created_migrator = await _ensure_role(conn, migrator, _CREATE_LOGIN)
        await _run(conn, _NARROW_LOGIN, migrator, migrator_password)
        created_runtime = await _ensure_role(conn, runtime, _CREATE_LOGIN)
        await _run(conn, _NARROW_LOGIN, runtime, runtime_password)
        await _run(conn, "GRANT %I TO %I", APP_GROUP, runtime)

        await _run(conn, "ALTER DATABASE %I OWNER TO %I", database, migrator)
        await _run(conn, "REVOKE ALL ON DATABASE %I FROM PUBLIC", database)
        await _run(conn, "GRANT CONNECT ON DATABASE %I TO %I, %I", database, migrator, runtime)
    finally:
        await conn.close()

    # The objects live in the database itself, not in the one we connected to.
    conn = await asyncpg.connect(superuser_url, database=database)
    try:
        transferred = await _transfer_ownership(conn, migrator)
    finally:
        await conn.close()

    return {"created_group": created_group, "created_migrator": created_migrator,
            "created_runtime": created_runtime, "objects_transferred": transferred}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m migrations.roles",
        description="Create the migration and application roles of a Cohereny database (CHY-85).",
    )
    parser.add_argument("--database", action="append", required=True,
                        help="database name; repeat for several")
    args = parser.parse_args(argv)

    superuser_url = os.environ.get("DB_SUPERUSER_URL")
    if not superuser_url:
        print("DB_SUPERUSER_URL is not set: it must connect as a superuser.", file=sys.stderr)
        return 2

    for database in args.database:
        migrator_var, runtime_var = password_variables(database)
        missing = [v for v in (migrator_var, runtime_var) if not os.environ.get(v)]
        if missing:
            print(f"{database}: {', '.join(missing)} not set.", file=sys.stderr)
            return 2
        result = asyncio.run(provision(superuser_url, database,
                                       os.environ[migrator_var], os.environ[runtime_var]))
        migrator, runtime = role_names(database)
        print(f"{database}: roles {migrator} and {runtime} ready "
              f"(created: group={result['created_group']}, migrator={result['created_migrator']}, "
              f"runtime={result['created_runtime']}; objects handed to {migrator}: "
              f"{result['objects_transferred']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
