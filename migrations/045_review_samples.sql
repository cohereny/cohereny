-- 045_review_samples.sql — the review level `sample`: checked afterwards, by a share (CHY-223, ADR-050)
--
-- Step 3b of ADR-050. Where neither the data nor the risk asks for a person
-- before the output goes on — public or internal data, a workflow of minimal
-- risk — a gate may be `sample`: the run does not wait, and a share of its
-- passes, `review_sample_rate`, goes into a review queue to be judged
-- afterwards. The gate stays a node in the graph the validator demands (ADR-004):
-- what changes is only whether the run waits there.
--
-- Whether a pass is drawn is decided by a hash of the pass, not by a random
-- generator: the same pass is drawn or not every time it is asked, and the
-- audit entry that says so can be checked against it.
--
-- `review_samples` holds the drawn passes, one per gate execution, and the
-- verdict once somebody gave it: confirmed or objected, with a reason. An
-- objection recalls nothing — the output is out — and is counted, a signal from
-- operation (ADR-044, way 3). The reason is run content: stored with the run's
-- placeholders and cleared with its period; the row goes with the run's trace.
-- No cascade, like the votes of 027: a verdict is evidence that a person looked.
--
-- Visible to whoever may see the run, as its steps are (CHY-92).
--
-- Free of data migration: no gate is `sample` yet.

ALTER TABLE workflow_nodes DROP CONSTRAINT workflow_nodes_review_role_check;
ALTER TABLE workflow_nodes ADD CONSTRAINT workflow_nodes_review_role_check
    CHECK (review_role IS NULL OR review_role IN ('sample', 'approver', 'four_eyes'));

ALTER TABLE workflow_nodes ADD COLUMN review_sample_rate NUMERIC(4, 3);

-- A rate exactly on a sample gate, and a sample gate without areas or time
-- limit: it decides nothing and waits for nothing. IS NOT DISTINCT FROM, so a
-- NULL role compares as false and not as NULL — a CHECK that evaluates to NULL
-- passes.
ALTER TABLE workflow_nodes ADD CONSTRAINT workflow_nodes_sample_gate
    CHECK ((review_role IS NOT DISTINCT FROM 'sample') = (review_sample_rate IS NOT NULL)
           AND (review_sample_rate IS NULL
                OR (review_sample_rate > 0 AND review_sample_rate <= 1))
           AND (review_role IS DISTINCT FROM 'sample'
                OR (review_areas IS NULL AND review_timeout IS NULL AND on_timeout IS NULL)));

COMMENT ON COLUMN workflow_nodes.review_sample_rate IS
    'human_review with review_role sample only, and required there: the share of '
    'passes drawn into the review queue, above 0 and at most 1 (CHY-223, ADR-050).';

ALTER TABLE node_executions DROP CONSTRAINT node_executions_review_level_check;
ALTER TABLE node_executions ADD CONSTRAINT node_executions_review_level_check
    CHECK (review_level IS NULL OR review_level IN ('sample', 'approver', 'four_eyes'));

CREATE TABLE review_samples (
    execution_id UUID        PRIMARY KEY REFERENCES node_executions (id),
    run_id       UUID        NOT NULL REFERENCES workflow_runs (id),
    drawn_at     TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    verdict      TEXT        CHECK (verdict IN ('confirmed', 'objected')),
    reason       TEXT,
    reviewed_by  UUID        REFERENCES actors (id),
    reviewed_at  TIMESTAMPTZ,
    -- A verdict has an author and a time, and nothing has a reason without one.
    CHECK ((verdict IS NULL) = (reviewed_by IS NULL)
           AND (verdict IS NULL) = (reviewed_at IS NULL)
           AND (verdict IS NOT NULL OR reason IS NULL))
);

-- Runs are deleted at the end of their trace period, and the check of this key
-- would otherwise scan the table for each (scripts/fk_indexes.py, rule 1).
CREATE INDEX review_samples_run_id_idx ON review_samples (run_id);

COMMENT ON TABLE review_samples IS
    'Passes of a sample gate drawn for review, and the verdict on each (CHY-223, '
    'ADR-050). An objection recalls nothing; it is counted.';

ALTER TABLE review_samples ENABLE ROW LEVEL SECURITY;
CREATE POLICY review_samples_visible ON review_samples FOR ALL TO cohereny_app
    USING (EXISTS (SELECT 1 FROM workflow_runs r WHERE r.id = review_samples.run_id))
    WITH CHECK (EXISTS (SELECT 1 FROM workflow_runs r WHERE r.id = review_samples.run_id));

GRANT SELECT, INSERT, UPDATE, DELETE ON review_samples TO cohereny_app;
