-- 036_pii_mappings.sql — what each placeholder of a run stands for, encrypted (CHY-234, ADR-018)
--
-- Until now identifiers were replaced for the length of one model call:
-- core/completion.py swapped them for placeholders on the way out and back on
-- the way in, and the run stored what came back — with the real values. From
-- here on a workflow run keeps its placeholders: what it took in, every step's
-- input and output. What each placeholder stands for is kept here, one row per
-- run.
--
-- Encrypted by the application, not by the database (kernel/mapping_cipher.py):
-- AES-256-GCM under a key from PII_MAPPING_KEYS, which the database never sees,
-- with the run's id as associated data, so a mapping cannot be moved to another
-- run. Whoever reads this table — a backup, a replica, the owner of the
-- tables — gets ciphertext. `key_id` names the key that sealed the row, so keys
-- can be rotated: the newest one seals, every listed one opens. Each seal draws
-- a new nonce, so the row is rewritten as a whole when the mapping grows.
--
-- Run content like the rest (ADR-046): nonce and ciphertext are cleared with
-- the run's other content columns, and the row goes with the run (ON DELETE
-- CASCADE, which runs as the owner and needs no grant). Visible to whoever sees
-- the run, through the same policy as its steps (035); who may have it
-- decrypted is decided where it is decrypted, not here.
--
-- The application reads it, inserts it when the run starts, and updates it
-- when a step finds a value the run did not know yet — under a row lock, so
-- two steps cannot hand out the same number — and when the purge clears it.

CREATE TABLE IF NOT EXISTS pii_mappings (
    run_id     UUID        PRIMARY KEY REFERENCES workflow_runs (id) ON DELETE CASCADE,
    key_id     TEXT        NOT NULL,
    nonce      BYTEA,
    ciphertext BYTEA,
    sealed_at  TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT pii_mappings_sealed_or_cleared CHECK ((nonce IS NULL) = (ciphertext IS NULL))
);

COMMENT ON TABLE pii_mappings IS
    'What each placeholder of a workflow run stands for, sealed with AES-256-GCM '
    'by the application under a key the database never sees (CHY-234). Cleared '
    'with the run''s content, deleted with the run.';

GRANT SELECT, INSERT, UPDATE ON pii_mappings TO cohereny_app;

ALTER TABLE pii_mappings ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS pii_mappings_visible ON pii_mappings;
CREATE POLICY pii_mappings_visible ON pii_mappings FOR ALL TO cohereny_app
    USING (EXISTS (SELECT 1 FROM workflow_runs r WHERE r.id = pii_mappings.run_id))
    WITH CHECK (EXISTS (SELECT 1 FROM workflow_runs r WHERE r.id = pii_mappings.run_id));
