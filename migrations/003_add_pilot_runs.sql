-- Pilot workflow runs — one row per execution started via POST /v1/pilots/runs.
-- Status transitions: pending → running → complete | failed.
-- result_json stores the final LangGraph state (fakten, zusammenfassung, etc.) on success.

CREATE TABLE IF NOT EXISTS pilot_runs (
    id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    thread_id     TEXT        UNIQUE NOT NULL,
    pilot_name    TEXT        NOT NULL DEFAULT 'study',
    input_file    TEXT        NOT NULL,
    status        TEXT        NOT NULL DEFAULT 'pending'
                              CHECK (status IN ('pending', 'running', 'complete', 'failed')),
    started_at    TIMESTAMPTZ,
    completed_at  TIMESTAMPTZ,
    result_json   JSONB,
    error_message TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS pilot_runs_status_idx     ON pilot_runs (status);
CREATE INDEX IF NOT EXISTS pilot_runs_created_at_idx ON pilot_runs (created_at DESC);
