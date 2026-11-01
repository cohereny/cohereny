-- Migration 007: Question bank for study pilot coverage scoring (CHY-23)
--
-- question_bank  — stores exam questions ingested by the offline
--                  ingest script (pilots/study/scripts/ingest_questions.py).
--                  Each question is checked against the extracted facts in
--                  node_coverage_check.
--
-- run_coverage   — records per-run coverage results so /v1/pilots/runs/{id}/coverage
--                  can surface coverage statistics after a run completes.

CREATE TABLE IF NOT EXISTS question_bank (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    question    TEXT        NOT NULL,
    topic       TEXT,
    source      TEXT,
    difficulty  SMALLINT    DEFAULT 3 CHECK (difficulty BETWEEN 1 AND 5),
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_question_bank_topic ON question_bank (topic);

CREATE TABLE IF NOT EXISTS run_coverage (
    run_id      UUID    NOT NULL REFERENCES pilot_runs(id) ON DELETE CASCADE,
    question_id UUID    NOT NULL REFERENCES question_bank(id) ON DELETE CASCADE,
    covered     BOOLEAN NOT NULL DEFAULT FALSE,
    PRIMARY KEY (run_id, question_id)
);

CREATE INDEX IF NOT EXISTS idx_run_coverage_run_id ON run_coverage (run_id);
