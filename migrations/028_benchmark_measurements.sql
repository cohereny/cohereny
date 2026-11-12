-- 028_benchmark_measurements.sql — anchors are test cases, measurements are evidence (CHY-67, ADR-043)
--
-- Two things, both decided on 2026-10-06 (Thomas, ADR-043):
--
-- 1. A calibration anchor is not prompt material.
--
--    `calibration_anchor` was in the composable allow-list since 021 and in the
--    jsonb allow-list of workflow_nodes.context_requirements since 022. An
--    anchor carries the expected answer of a benchmark case; composed into a
--    prompt, it would hand the model the answer it is being measured on, and
--    every measurement in its scope would be worthless. It stayed harmless only
--    because no anchor existed: on 2026-10-06 dev, test and prod held none, and
--    no node asked for one.
--
--    The Python side is contexts/repository/schema.py (COMPOSABLE_BLOCK_TYPES);
--    tests/test_status_vocabularies.py holds the two together and reads the
--    latest definition of this constraint, which is now the one below.
--
-- 2. Benchmark runs and their measurements get tables of their own.
--
--    The test cases do not: they are calibration_anchor blocks, versioned and
--    reviewed like any other content. A measurement is an observation — it is
--    not approved, it is evaluated — so it lives here, owned by the learning
--    context.
--
--    One rule is enforced here rather than trusted to the writer: an outage is
--    not a measurement. A model that did not answer, was refused by the data
--    class (ADR-041) or failed gets an outcome saying so and NO score — a score
--    of 0 would read as the worst regression in the table.
--
-- Not reversible, like every migration here (CHY-113).

-- ── 1. Anchors leave the composable set ──────────────────────────────────────

ALTER TABLE workflow_nodes DROP CONSTRAINT IF EXISTS workflow_nodes_requirements_shape;
ALTER TABLE workflow_nodes ADD CONSTRAINT workflow_nodes_requirements_shape
    CHECK (
        context_requirements IS NULL
        OR (
            jsonb_typeof(context_requirements) = 'object'
            AND context_requirements ? 'block_types'
            AND context_requirements - 'block_types' = '{}'::jsonb
            AND jsonb_typeof(context_requirements -> 'block_types') = 'array'
            AND jsonb_array_length(context_requirements -> 'block_types') > 0
            AND context_requirements -> 'block_types' <@
                '["system_prompt", "user_context", "tool_schema", "example_io"]'::jsonb
        )
    );

COMMENT ON COLUMN context_blocks.block_type IS
    'What kind of block. Four types are prompt material and get composed; '
    'routing_rule (ADR-037) and calibration_anchor (ADR-043) never reach a '
    'model as context. The composable set lives in contexts/repository/schema.py '
    'and is held against the CHECK on workflow_nodes.context_requirements.';

-- ── 2. One row per block version ─────────────────────────────────────────────
--
-- The repository has always assumed it — cc_supersede_block writes version
-- n+1 under FOR UPDATE, idx_blocks_one_active keeps one current row per line —
-- and never declared it. A measurement names the anchor version it measured,
-- by line and version, and the foreign key below needs the pair to be unique.
-- Counted on 2026-10-06 before adding it: no duplicate pair on dev (165 rows),
-- test (14) or prod (0).
--
-- Named by line and version rather than by the row id, because the learning
-- context may not read context_blocks (ADR-030): with the row id, every report
-- would have to join the repository's table to say which version it means.

ALTER TABLE context_blocks
    ADD CONSTRAINT context_blocks_one_row_per_version UNIQUE (block_id, version);

-- ── 3. Runs ───────────────────────────────────────────────────────────────────

CREATE TABLE benchmark_runs (
    id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    mode          TEXT NOT NULL CHECK (mode IN ('drift', 'ab_adaptation')),
    status        TEXT NOT NULL DEFAULT 'running' CHECK (status IN ('running', 'complete', 'failed')),
    -- Nullable like audit_log.actor_id: a run started by the systemd unit has
    -- no human author. A run started by a person names them.
    triggered_by  UUID REFERENCES actors(id),
    -- Set by the drift comparison (CHY-68), cleared by a reviewer (CHY-64).
    review_needed BOOLEAN NOT NULL DEFAULT false,
    started_at    TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    finished_at   TIMESTAMPTZ,
    -- A finished run has an end, a running one does not. Without this a run
    -- that died could sit at `running` with an end time, or be `complete` with
    -- none, and a freshness check would not know which to believe.
    CONSTRAINT benchmark_runs_end_matches_status
        CHECK ((status = 'running') = (finished_at IS NULL))
);

COMMENT ON TABLE benchmark_runs IS
    'One benchmark run over the active calibration anchors (ADR-043). Started on '
    'demand — CLI or the cohereny-benchmark systemd unit — not on a schedule yet '
    '(CHY-181).';

-- ── 4. Measurements ───────────────────────────────────────────────────────────

CREATE TABLE benchmark_measurements (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    run_id      UUID NOT NULL REFERENCES benchmark_runs(id),
    -- The anchor version measured. A new anchor version is a new test case
    -- with a baseline of its own (CHY-68). Blocks are append-only (013), so
    -- the reference never dangles.
    anchor_block_id UUID NOT NULL,
    anchor_version  INTEGER NOT NULL,
    model       TEXT NOT NULL,
    variant     TEXT NOT NULL CHECK (variant IN ('composed', 'raw')),
    outcome     TEXT NOT NULL CHECK (outcome IN ('measured', 'unreachable', 'refused', 'error')),
    score       NUMERIC(5, 4) CHECK (score BETWEEN 0 AND 1),
    -- One value per validator that applied, each in [0, 1]. `score` is their
    -- mean; keeping the parts shows which rule moved.
    scores      JSONB NOT NULL DEFAULT '{}'::jsonb,
    output      TEXT,
    -- Why there is no score: the refusal, the timeout, the error.
    detail      TEXT,
    latency_ms  INTEGER CHECK (latency_ms >= 0),
    tokens_in   INTEGER CHECK (tokens_in >= 0),
    tokens_out  INTEGER CHECK (tokens_out >= 0),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT benchmark_measurements_score_iff_measured
        CHECK ((outcome = 'measured') = (score IS NOT NULL)),
    CONSTRAINT benchmark_measurements_scores_object
        CHECK (jsonb_typeof(scores) = 'object'),
    -- A run measures each pair once. Repeating a run (CHY-68) skips what it
    -- already has instead of adding a second, competing value.
    CONSTRAINT benchmark_measurements_anchor_version
        FOREIGN KEY (anchor_block_id, anchor_version)
        REFERENCES context_blocks (block_id, version),
    CONSTRAINT benchmark_measurements_once_per_run
        UNIQUE (run_id, anchor_block_id, anchor_version, model, variant)
);

-- The baseline lookup: the first measurement of an anchor version against a
-- model, in a variant (CHY-68).
CREATE INDEX idx_benchmark_measurements_baseline
    ON benchmark_measurements (anchor_block_id, anchor_version, model, variant, created_at);

COMMENT ON TABLE benchmark_measurements IS
    'One model answering one calibration anchor version in one run (ADR-043). '
    'Append-only: evidence that can be rewritten is not evidence. An outcome '
    'other than measured carries no score (benchmark_measurements_score_iff_measured).';

-- Evidence is not edited. Same reasoning and same shape as the audit log (011):
-- refuse loudly instead of the silent DO INSTEAD NOTHING rule 001 started with.
CREATE OR REPLACE FUNCTION cc_benchmark_measurements_append_only()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'benchmark_measurements is append-only: % refused', TG_OP
        USING ERRCODE = 'integrity_constraint_violation',
              HINT    = 'A measurement is evidence. Run the benchmark again '
                        'instead of changing what an earlier run observed.';
END;
$$;

CREATE TRIGGER benchmark_measurements_append_only
    BEFORE UPDATE OR DELETE ON benchmark_measurements
    FOR EACH ROW EXECUTE FUNCTION cc_benchmark_measurements_append_only();

CREATE TRIGGER benchmark_measurements_no_truncate
    BEFORE TRUNCATE ON benchmark_measurements
    FOR EACH STATEMENT EXECUTE FUNCTION cc_benchmark_measurements_append_only();
