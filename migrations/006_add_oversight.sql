-- Migration 006: Human Oversight support (EU AI Act Art. 14)
--
-- Adds two columns to pilot_runs so the API can persist oversight state:
--   oversight_pending  — TRUE while the workflow is blocked waiting for a human decision
--   oversight_decision — "approved" or "rejected" once the human has responded
--
-- The "rejected" value is also added to the status check constraint so the
-- run can be marked terminal when a human rejects the AI analysis.

ALTER TABLE pilot_runs
    ADD COLUMN IF NOT EXISTS oversight_pending  BOOLEAN DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS oversight_decision TEXT;

-- Drop and recreate the status constraint to include "rejected"
ALTER TABLE pilot_runs DROP CONSTRAINT IF EXISTS pilot_runs_status_check;
ALTER TABLE pilot_runs ADD CONSTRAINT pilot_runs_status_check
    CHECK (status IN ('pending', 'running', 'awaiting_oversight', 'complete', 'failed', 'rejected'));
