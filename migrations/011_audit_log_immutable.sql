-- Migration 011: make the audit log actually immutable (CHY-31)
--
-- 001_initial protected audit_log with two rewrite rules:
--
--     CREATE RULE no_update_audit_log AS ON UPDATE TO audit_log DO INSTEAD NOTHING;
--     CREATE RULE no_delete_audit_log AS ON DELETE TO audit_log DO INSTEAD NOTHING;
--
-- Probed against cohereny_dev on 2026-08-12. Two problems, one of them serious:
--
--   UPDATE    row survives, but NO ERROR is raised -- the caller gets "UPDATE 0"
--             and cannot distinguish "refused" from "matched nothing". A buggy
--             correction attempt reads as success.
--   DELETE    same: silently discarded.
--   TRUNCATE  *succeeded and emptied the table*. Rules do not apply to TRUNCATE.
--
-- So the table documented as "unveränderlich" could be wiped with one statement,
-- and the two guards that did exist failed quietly. For a log whose entire value
-- is being trustworthy after the fact, silence is the wrong failure mode: a
-- tamper attempt must leave a trace, and the only trace a database can leave at
-- that moment is an error.
--
-- Triggers fix both. They can RAISE, and a statement-level BEFORE TRUNCATE
-- trigger is the one mechanism that stops TRUNCATE.
--
-- Scope note: this covers the physical guarantee only. The append-only rule for
-- context_blocks is ADR-009 / CHY-60 (cc_supersede_block) and stays there.

-- The rules go first. Leaving them alongside the triggers would mean UPDATE is
-- swallowed by the rule before any trigger fires -- still no error.
DROP RULE IF EXISTS no_update_audit_log ON audit_log;
DROP RULE IF EXISTS no_delete_audit_log ON audit_log;

CREATE OR REPLACE FUNCTION cc_audit_log_immutable() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION
        'audit_log is append-only: % is not permitted', TG_OP
        USING
            ERRCODE = 'integrity_constraint_violation',
            HINT    = 'Corrections are written as a new entry that references the '
                      'erroneous one. See ADR-009 and CHY-31.';
END;
$$;

COMMENT ON FUNCTION cc_audit_log_immutable() IS
    'Raises on any UPDATE, DELETE or TRUNCATE of audit_log (CHY-31). Replaces the '
    'DO INSTEAD NOTHING rules from 001_initial, which failed silently and did not '
    'cover TRUNCATE.';

DROP TRIGGER IF EXISTS audit_log_no_update ON audit_log;
CREATE TRIGGER audit_log_no_update
    BEFORE UPDATE ON audit_log
    FOR EACH STATEMENT EXECUTE FUNCTION cc_audit_log_immutable();

DROP TRIGGER IF EXISTS audit_log_no_delete ON audit_log;
CREATE TRIGGER audit_log_no_delete
    BEFORE DELETE ON audit_log
    FOR EACH STATEMENT EXECUTE FUNCTION cc_audit_log_immutable();

-- The hole the rules never covered.
DROP TRIGGER IF EXISTS audit_log_no_truncate ON audit_log;
CREATE TRIGGER audit_log_no_truncate
    BEFORE TRUNCATE ON audit_log
    FOR EACH STATEMENT EXECUTE FUNCTION cc_audit_log_immutable();

-- Statement-level rather than row-level on purpose: the refusal does not depend
-- on which rows are affected, and a row-level trigger on a DELETE covering a
-- million rows would evaluate a million times to raise the same error once.

COMMENT ON TABLE audit_log IS
    'Append-only record of state changes. INSERT only -- UPDATE, DELETE and '
    'TRUNCATE raise (CHY-31). Corrections are new entries referencing the old one.';

-- Read paths from CHY-31: the history of one entity, and the actions of one
-- actor. Both are newest-first, so the index carries the sort order.
CREATE INDEX IF NOT EXISTS idx_audit_log_target
    ON audit_log (target_ref, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_audit_log_actor
    ON audit_log (actor_id, created_at DESC);

-- Unfiltered listing, newest first (admin overview).
CREATE INDEX IF NOT EXISTS idx_audit_log_created
    ON audit_log (created_at DESC);
