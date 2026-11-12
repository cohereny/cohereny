-- Migration 015: a pilot run cannot take its transparency records with it (CHY-90)
--
-- DELETE /v1/pilots/runs/{id} hard-deletes any terminal run. transparency_records
-- referenced pilot_runs with ON DELETE CASCADE, so the delete silently took the
-- AI Act Article 50 records with it. Measured on cohereny_dev on 19.08.2026:
-- 18 runs, all of them terminal and therefore deletable, and 9 transparency
-- records hanging off them.
--
-- The endpoint took `actor: CurrentUser = Depends(require_auth)` and never read
-- it, so any authenticated caller could do this to any run, and nothing was
-- written to the audit log afterwards. That part is fixed in api/v1/pilots.py.
-- This migration exists because an application check is not where a rule of this
-- weight belongs on its own: the same reasoning as migration 013, where the
-- block transition table is enforced by a trigger *and* checked in Python. The
-- application produces the comprehensible error; the database refuses.
--
-- run_coverage keeps ON DELETE CASCADE on purpose. It is derived data — which
-- question a run covered — and is meaningless once the run is gone. Article 50
-- records are evidence and outlive their subject.

BEGIN;

ALTER TABLE transparency_records
    DROP CONSTRAINT IF EXISTS transparency_records_pilot_run_id_fkey;

ALTER TABLE transparency_records
    ADD CONSTRAINT transparency_records_pilot_run_id_fkey
    FOREIGN KEY (pilot_run_id) REFERENCES pilot_runs(id)
    ON DELETE RESTRICT;

COMMENT ON CONSTRAINT transparency_records_pilot_run_id_fkey ON transparency_records IS
    'RESTRICT, not CASCADE: an Article 50 record must not disappear because '
    'somebody deleted the run it documents (CHY-90, migration 015).';

-- Index on the foreign key. RESTRICT makes PostgreSQL check the child table on
-- every parent delete, and without an index that check is a sequential scan.
-- Part of the wider finding in CHY-112 -- 21 of 28 foreign keys carry no leading
-- index -- but this one becomes load-bearing with this migration, so it comes now.
CREATE INDEX IF NOT EXISTS idx_transparency_records_pilot_run_id
    ON transparency_records (pilot_run_id);

COMMIT;
