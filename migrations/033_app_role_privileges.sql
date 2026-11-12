-- 033_app_role_privileges.sql — what the application may do, table by table (CHY-85, ADR-009)
--
-- Until now the application connected as the superuser that owns every table.
-- ADR-009 promised four mechanisms against changing history; two of them did
-- not hold against that role. A superuser may `SET session_replication_role =
-- replica`, an owner may `ALTER TABLE ... DISABLE TRIGGER`, and a SECURITY
-- DEFINER function bridges no gap when the caller already is its owner. The
-- triggers stopped application bugs, not someone with the application's
-- credentials: "refused unless deliberately switched off", not "not possible".
--
-- From here on two roles do the work, created per database by
-- migrations/roles.py before this migration runs:
--
--   <db>_migrator   owns every object and runs the migrations
--   <db>_runtime    what the API, the CLI and the purge connect as; member of
--                   the group role `cohereny_app`, which holds the grants below
--
-- The grants are what the code needs and nothing else, decided per table in
-- kernel/retention.py (INVENTORY, field `access`). tests/test_app_privileges.py
-- holds this file to that list, and the integration suite holds the database to
-- it. A new table gets its grant in the migration that creates it, and its entry
-- in the same change.
--
-- No TRUNCATE, REFERENCES or TRIGGER anywhere, and no CREATE on the schema: the
-- application runs no DDL. The LangGraph checkpoint tables are created and
-- granted by migrations/runner.py, which runs as the migration role.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'cohereny_app') THEN
        RAISE EXCEPTION 'role cohereny_app does not exist'
            USING HINT = 'Run `python -m migrations.roles` with a superuser '
                         'connection first; it creates the roles (CHY-85).';
    END IF;
END $$;

-- The schema: usable, nothing created in it. CREATE is already withheld from
-- PUBLIC since PostgreSQL 15; said again so it does not depend on the version.
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
REVOKE ALL ON SCHEMA public FROM cohereny_app;
GRANT USAGE ON SCHEMA public TO cohereny_app;

-- Start from nothing, so a second run or an earlier manual grant leaves no rest.
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM cohereny_app;

-- orchestration
GRANT SELECT, INSERT, UPDATE, DELETE ON workflow_runs        TO cohereny_app;
GRANT SELECT, INSERT, UPDATE         ON node_executions      TO cohereny_app;
GRANT SELECT, INSERT, UPDATE         ON loop_counters        TO cohereny_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON review_votes         TO cohereny_app;
GRANT SELECT, INSERT, UPDATE         ON workflow_definitions TO cohereny_app;
GRANT SELECT, INSERT                 ON workflow_nodes       TO cohereny_app;
GRANT SELECT, INSERT, DELETE         ON workflow_connections TO cohereny_app;

-- pilots
GRANT SELECT, INSERT, UPDATE, DELETE ON pilot_runs           TO cohereny_app;
GRANT SELECT, INSERT, DELETE         ON transparency_records TO cohereny_app;
GRANT SELECT, INSERT, UPDATE         ON run_coverage         TO cohereny_app;
GRANT SELECT, INSERT                 ON question_bank        TO cohereny_app;
GRANT SELECT, INSERT, UPDATE         ON workflow_configs     TO cohereny_app;

-- learning
GRANT SELECT, UPDATE, DELETE         ON feedback_events        TO cohereny_app;
GRANT SELECT                         ON learning_proposals     TO cohereny_app;
GRANT SELECT                         ON benchmark_runs         TO cohereny_app;
GRANT SELECT                         ON benchmark_measurements TO cohereny_app;
GRANT SELECT                         ON benchmark_reports      TO cohereny_app;

-- repository
GRANT SELECT, INSERT, UPDATE         ON context_blocks       TO cohereny_app;
GRANT SELECT                         ON governance_keys      TO cohereny_app;

-- kernel
GRANT SELECT, INSERT                 ON audit_log            TO cohereny_app;
GRANT SELECT, INSERT, UPDATE         ON actors               TO cohereny_app;
GRANT SELECT, INSERT                 ON scope_memberships    TO cohereny_app;
GRANT SELECT, INSERT                 ON scope_nodes          TO cohereny_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON retention_policies   TO cohereny_app;

-- schema_migrations: nothing. The application never reads it.
