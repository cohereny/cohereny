-- transparency_records: EU AI Act Art. 50 disclosure log per pilot run.
-- user_informed is always TRUE — the record's existence proves the user was shown
-- the transparency notice in agent-ui when the done event was rendered.
--
-- Also sets the pilot_runs.pilot_name default to 'study'.

ALTER TABLE pilot_runs ALTER COLUMN pilot_name SET DEFAULT 'study';

CREATE TABLE IF NOT EXISTS transparency_records (
    id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    pilot_run_id     UUID        NOT NULL REFERENCES pilot_runs(id) ON DELETE CASCADE,
    user_informed    BOOLEAN     NOT NULL DEFAULT TRUE,
    model_used       TEXT        NOT NULL,
    model_disclosure TEXT        NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS transparency_records_run_idx
    ON transparency_records (pilot_run_id);
