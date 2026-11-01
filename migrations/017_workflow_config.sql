-- Migration 017: workflow configuration as data (CHY-42)
--
-- Model choice and workflow tuning live as module constants read from the
-- process environment (pilots/study/study/config.py, pilots/banking/...).
-- Changing a model therefore means an env change and a redeploy, and nothing
-- records which value a given run actually used.
--
-- On the number: the ticket says "Migration 010". That was itself a correction
-- from 007, and 010 has since been taken by 010_actor_anonymization. Twice
-- stale for the same reason — a number reserved in prose is not reserved.
--
-- Two decisions that shape what is in here.
--
-- 1. NOT everything in config.py belongs in a table.
--
--    Those files mix two kinds of value. Infrastructure — LiteLLM base URL,
--    Langfuse keys, POSTGRES_URI — stays in the environment and SOPS. A base
--    URL that a PUT on the API can change is an SSRF vector, and a secret in a
--    row is a secret in a backup. Only workflow tuning is data.
--
-- 2. effective_config may not claim more than it can keep.
--
--    It is meant to be the immutable record of what a run actually used (EU AI
--    Act Art. 12). A value recorded there that no node reads makes the record
--    *false* — and unnoticeably so, because it looks complete.
--
--    ADR-028 point 4 says Study and Banking are not to be rebuilt: the runner
--    of CHY-82 replaces them. So the snapshot carries `model_map` only, which
--    is the part being wired through in this ticket. `params` lives in the
--    table and is served by the API, but is deliberately NOT snapshotted until
--    the runner makes it take effect. contexts/pilots/config.py names the
--    effective sections, and a test refuses a snapshot that grows past them.

CREATE TABLE IF NOT EXISTS workflow_configs (
    pilot_name TEXT PRIMARY KEY,
    -- Bumped on every write. A run records the version it composed from, so a
    -- later reader can tell "the config changed since" from "the run differed".
    version    INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
    -- Which model answers at which tier. The keys are tiers, not model names:
    -- a tier is what a node asks for, and the mapping is the decision.
    model_map  JSONB   NOT NULL DEFAULT '{}',
    -- Thresholds, batch sizes, iteration limits. Served and editable, and NOT
    -- yet in effect — see the note above.
    params     JSONB   NOT NULL DEFAULT '{}',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Objects, not arrays or scalars. Without this a PUT could store a list in
    -- model_map and every reader would fail somewhere far from the write.
    CONSTRAINT workflow_configs_shapes CHECK (
        jsonb_typeof(model_map) = 'object' AND jsonb_typeof(params) = 'object'
    )
);

COMMENT ON TABLE workflow_configs IS
    'Per-pilot workflow configuration (CHY-42). Tuning only — infrastructure '
    'URLs and secrets stay in the environment and SOPS.';
COMMENT ON COLUMN workflow_configs.model_map IS
    'Tier → model id. Takes effect: the pilots read this per run.';
COMMENT ON COLUMN workflow_configs.params IS
    'Thresholds and limits. Editable and served, but NOT yet in effect — the '
    'pilots still read their own constants (ADR-028 point 4). Deliberately '
    'excluded from pilot_runs.effective_config until CHY-82 wires it through, '
    'because a record of a value that did nothing is a false record.';

-- Seeded from the values the pilots use today, so the first read after this
-- migration composes exactly what the previous release ran. A table that
-- starts empty would silently change behaviour on deploy, which is the one
-- thing a configuration migration must not do.
INSERT INTO workflow_configs (pilot_name, model_map, params) VALUES
    ('study',
     '{"small": "phi3-mini", "medium": "gemma4-e4b"}'::jsonb,
     '{"max_quality_iterations": 3, "max_quiz_iterations": 2, "chunk_size": 1200,
       "batch_size": 10, "merge_group_size": 5, "min_facts": 40,
       "coverage_threshold": 0.90, "question_batch_size": 50}'::jsonb),
    ('banking',
     '{"small": "phi3-mini", "medium": "gemma4-e4b", "cloud": "gemma4-e4b"}'::jsonb,
     '{"anomaly_threshold_yoy": 0.30, "anomaly_threshold_cr": 1.0,
       "anomaly_threshold_eq": 0.20}'::jsonb)
ON CONFLICT (pilot_name) DO NOTHING;

-- ── Per-run overrides and the record of what was used ────────────────────────

ALTER TABLE pilot_runs
    ADD COLUMN IF NOT EXISTS run_config       JSONB NOT NULL DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS effective_config JSONB;

ALTER TABLE pilot_runs DROP CONSTRAINT IF EXISTS pilot_runs_run_config_object;
ALTER TABLE pilot_runs ADD CONSTRAINT pilot_runs_run_config_object
    CHECK (jsonb_typeof(run_config) = 'object');

COMMENT ON COLUMN pilot_runs.run_config IS
    'What the caller asked to override for this run. The request, not the '
    'result — kept separately from effective_config so a later reader can tell '
    'an override that was honoured from one that was ignored.';
COMMENT ON COLUMN pilot_runs.effective_config IS
    'Immutable record of the configuration this run actually used (EU AI Act '
    'Art. 12). Written once at the transition to running. Contains only '
    'sections that take effect — see contexts/pilots/config.py.';

-- effective_config is a record, and a record that can be rewritten afterwards
-- is not one. NULL → value is the write at run start; anything else is refused.
--
-- A trigger rather than a convention: the same table is written by the service,
-- by migrations and by hand during an incident, and "we always write it once"
-- is not a property anybody can check later.
CREATE OR REPLACE FUNCTION cc_effective_config_write_once() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.effective_config IS NOT NULL
       AND NEW.effective_config IS DISTINCT FROM OLD.effective_config THEN
        RAISE EXCEPTION
            'pilot_runs.effective_config is written once, at the start of the run'
            USING
                ERRCODE = 'integrity_constraint_violation',
                HINT    = 'It records what this run used (EU AI Act Art. 12). '
                          'Correcting it afterwards would make the record a '
                          'description of the correction. See CHY-42.';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_effective_config_write_once ON pilot_runs;
CREATE TRIGGER trg_effective_config_write_once
    BEFORE UPDATE ON pilot_runs
    FOR EACH ROW EXECUTE FUNCTION cc_effective_config_write_once();

COMMENT ON FUNCTION cc_effective_config_write_once() IS
    'Keeps pilot_runs.effective_config append-once (CHY-42): NULL may become a '
    'value, a value may not change.';
