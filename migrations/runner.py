"""
migrations/runner.py

Simple SQL migration runner — executes numbered .sql files in order.

Each migration is a .sql file with the format `{version}_{name}.sql`, e.g.:
  0001_create_scopes.sql
  0002_create_blocks.sql

The version number (integer prefix) determines the execution order.
Already applied versions are recorded in the `schema_migrations` table
and skipped on the next run — idempotent, can be run any number of times.

Why not Alembic/Django migrations:
  The approach is intentionally minimal. No ORM, no framework lock-in.
  SQL files are directly readable and versionable. For the current complexity
  level this is sufficient — can be migrated to Alembic if automatic schema
  diffing becomes necessary.

Running in container:
  python -m migrations.runner  (via Dockerfile CMD or startup hook)
  DATABASE_URL comes from the environment, else from .env (api/config.py).

Running locally:
  DATABASE_URL=postgresql://... python migrations/runner.py

Dependencies:
  - asyncpg    (direct PostgreSQL connection without pool — one-off on startup)
"""
import asyncio
import asyncpg
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).parent


async def run_migrations(db_url: str) -> None:
    """Run all not-yet-applied migrations in order.

    Uses a direct connection (no pool) since migrations only run once on startup
    — no need for connection pooling.
    """
    conn = await asyncpg.connect(db_url)
    try:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)

        applied = {row["version"] for row in await conn.fetch("SELECT version FROM schema_migrations ORDER BY version")}

        for sql_file in sorted(MIGRATIONS_DIR.glob("*.sql")):
            version = int(sql_file.stem.split("_")[0])
            if version in applied:
                continue
            print(f"Applying migration {version}: {sql_file.name}")
            await conn.execute(sql_file.read_text())
            await conn.execute("INSERT INTO schema_migrations (version) VALUES ($1)", version)

    finally:
        await conn.close()


if __name__ == "__main__":
    # DATABASE_URL the way the API reads it: from the environment, else from
    # .env in the working directory. Reading only os.environ made README
    # "Getting started" fail right after `cp .env.example .env` (CHY-188).
    from api.config import settings
    asyncio.run(run_migrations(settings.database_url))
