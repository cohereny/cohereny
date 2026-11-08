-- 031_run_content_purged.sql — a run says when its content was cleared (CHY-17, ADR-046)
--
-- The purge clears what went into a run and came out once the retention period
-- for `run_content` has passed, and leaves the row as its trace (migration 030).
-- Afterwards `input_data` is '{}' and `result` is NULL — exactly what a run with
-- empty input and no result looks like. Without a mark, a reviewer reading the
-- run could not tell "there was nothing" from "it was removed on purpose", and
-- the next purge would clear the same rows again.
--
-- NULL: the content is still there. A timestamp: cleared then, by the purge,
-- which wrote a `retention.purged` entry into the audit log in the same
-- transaction.

ALTER TABLE workflow_runs
    ADD COLUMN IF NOT EXISTS content_purged_at TIMESTAMPTZ;

ALTER TABLE pilot_runs
    ADD COLUMN IF NOT EXISTS content_purged_at TIMESTAMPTZ;

COMMENT ON COLUMN workflow_runs.content_purged_at IS
    'When the purge cleared this run''s content and that of its node '
    'executions and votes (CHY-17, ADR-046). NULL while the content is there.';

COMMENT ON COLUMN pilot_runs.content_purged_at IS
    'When the purge cleared this run''s content (CHY-17, ADR-046). NULL while '
    'the content is there.';
