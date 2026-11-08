-- 035_run_isolation.sql — who may read a run, enforced by the database (CHY-92)
--
-- Until now every signed-in person read every run of the installation: its
-- input, every step's output, every reason given at the gate. The scope tree
-- separated what models may see (ADR-041), not what people may see. A clinic
-- would have had to write into its DPIA that whoever signs in sees the runs of
-- every patient.
--
-- Decided 06.11.2026 (Thomas): a workflow run is visible to whoever started
-- it, to the members of its workflow's scope and of every scope above it, and
-- to `admin`. A pilot run, which has no scope, to whoever started it and to
-- `admin`. Enforced here by row-level security rather than by a filter in each
-- query: a query that forgets to say for whom sees nothing.
--
-- The policies read two session settings, set by kernel/acting.py:
--
--   cohereny.actor_id   the person a query is made for — set per request
--   cohereny.system     'on' for the engine, the pilot threads and the purge,
--                       which work for the installation and not for a person
--
-- With neither set, these tables answer with no rows and refuse every write.
--
-- What this does not do: protect against someone holding the application's
-- credentials. They can set either setting. RLS holds the rule against every
-- query the application makes, and against a forgotten one in particular;
-- against a stolen password it holds as much as the password does.
--
-- Which tables: every table with content columns in kernel/retention.py —
-- tests/test_run_isolation.py holds the two together. The children of a run
-- inherit its visibility through their policy; ON DELETE CASCADE runs without
-- row security and is unaffected. The owner of the tables (the migration role)
-- is not subject to these policies; the application is not the owner (033).

CREATE OR REPLACE FUNCTION cc_acting_actor() RETURNS uuid
LANGUAGE sql STABLE AS $$
    SELECT nullif(current_setting('cohereny.actor_id', true), '')::uuid
$$;

CREATE OR REPLACE FUNCTION cc_acting_as_system() RETURNS boolean
LANGUAGE sql STABLE AS $$
    SELECT coalesce(current_setting('cohereny.system', true), '') = 'on'
$$;

-- An anonymized account keeps its row but no longer acts (ADR-016).
CREATE OR REPLACE FUNCTION cc_acting_admin() RETURNS boolean
LANGUAGE sql STABLE AS $$
    SELECT EXISTS (SELECT 1 FROM actors
                    WHERE id = cc_acting_actor() AND role = 'admin' AND NOT is_anonymized)
$$;

-- Membership in the scope or in any scope above it: a member of the
-- organisation sees the runs of its projects.
CREATE OR REPLACE FUNCTION cc_acting_member_of(p_scope uuid) RETURNS boolean
LANGUAGE sql STABLE AS $$
    SELECT EXISTS (
        SELECT 1
          FROM scope_memberships m
          JOIN scope_nodes granted ON granted.id = m.scope_id
          JOIN scope_nodes target  ON target.id = p_scope
         WHERE m.actor_id = cc_acting_actor()
           AND granted.scope_path @> target.scope_path)
$$;

CREATE OR REPLACE FUNCTION cc_may_see_workflow_run(p_starter uuid, p_workflow uuid) RETURNS boolean
LANGUAGE sql STABLE AS $$
    SELECT cc_acting_as_system()
        OR cc_acting_admin()
        OR (p_starter IS NOT NULL AND p_starter = cc_acting_actor())
        OR cc_acting_member_of((SELECT scope_id FROM workflow_definitions WHERE id = p_workflow))
$$;

COMMENT ON FUNCTION cc_may_see_workflow_run(uuid, uuid) IS
    'Row-level security of workflow runs (CHY-92): the system, admin, whoever '
    'started the run, members of its workflow''s scope and the scopes above.';

-- The lookup the policies do on every row: memberships of one actor.
CREATE INDEX IF NOT EXISTS scope_memberships_actor_idx ON scope_memberships (actor_id);

-- workflow runs and what belongs to them
ALTER TABLE workflow_runs ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS workflow_runs_visible ON workflow_runs;
CREATE POLICY workflow_runs_visible ON workflow_runs FOR ALL TO cohereny_app
    USING (cc_may_see_workflow_run(actor_id, workflow_id))
    WITH CHECK (cc_may_see_workflow_run(actor_id, workflow_id));

ALTER TABLE node_executions ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS node_executions_visible ON node_executions;
CREATE POLICY node_executions_visible ON node_executions FOR ALL TO cohereny_app
    USING (EXISTS (SELECT 1 FROM workflow_runs r WHERE r.id = node_executions.run_id))
    WITH CHECK (EXISTS (SELECT 1 FROM workflow_runs r WHERE r.id = node_executions.run_id));

ALTER TABLE review_votes ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS review_votes_visible ON review_votes;
CREATE POLICY review_votes_visible ON review_votes FOR ALL TO cohereny_app
    USING (EXISTS (SELECT 1 FROM node_executions e WHERE e.id = review_votes.execution_id))
    WITH CHECK (EXISTS (SELECT 1 FROM node_executions e WHERE e.id = review_votes.execution_id));

-- pilot runs: no scope, so whoever started it and admin
ALTER TABLE pilot_runs ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS pilot_runs_visible ON pilot_runs;
CREATE POLICY pilot_runs_visible ON pilot_runs FOR ALL TO cohereny_app
    USING (cc_acting_as_system() OR cc_acting_admin()
           OR (actor_id IS NOT NULL AND actor_id = cc_acting_actor()))
    WITH CHECK (cc_acting_as_system() OR cc_acting_admin()
                OR (actor_id IS NOT NULL AND actor_id = cc_acting_actor()));

-- feedback: nothing reads it for a person yet (CHY-40); the purge works on it
ALTER TABLE feedback_events ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS feedback_events_visible ON feedback_events;
CREATE POLICY feedback_events_visible ON feedback_events FOR ALL TO cohereny_app
    USING (cc_acting_as_system() OR cc_acting_admin())
    WITH CHECK (cc_acting_as_system() OR cc_acting_admin());
