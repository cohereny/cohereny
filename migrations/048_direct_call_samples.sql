-- 048_direct_call_samples.sql — a spot check of direct calls (CHY-269, ADR-050)
--
-- /v1/complete answers only where the minimum review is `sample` (047). At a
-- sample gate in a workflow a share of the outputs goes into the review queue
-- and a person judges it afterwards (045). A direct call had no such share:
-- the answer went to the caller and nothing kept it, so `sample` meant "no
-- review" there. Decided by Thomas, 10.11.2026: the spot check applies to direct
-- calls too.
--
-- Two parts:
--
--   direct_call_risk_classes.sample_rate — the share drawn, set together with
--     the classification. Required with `minimal`, the one class that lets a
--     direct call through, and refused with any other. Along the chain the
--     highest share applies, as the strictest class does (kernel/scopes.py).
--
--   direct_call_samples — the drawn calls, the same thing review_samples is for
--     a workflow's passes. What the model was asked and what it answered, both
--     with placeholders only (CHY-63): the values are put back for the caller
--     and kept nowhere. No mapping is stored, so nothing here turns a
--     placeholder back into a name. The context is named by its block versions,
--     not copied: blocks are kept, and their history is append-only (013).
--
-- Rows from before this migration: dev has scopes classified `minimal` without
-- a share. The pairing CHECK is NOT VALID for that reason. It is safe here, and
-- would not be on most tables (014 explains why): NOT VALID still checks any
-- later UPDATE of an old row, and this table takes none — the application holds
-- SELECT and INSERT only, and each setting is a new row. A scope whose newest
-- row is such an old `minimal` has no share, and /v1/complete refuses there
-- until an architect sets one: a missing share may not mean "no check".

ALTER TABLE direct_call_risk_classes ADD COLUMN sample_rate NUMERIC(4, 3);

ALTER TABLE direct_call_risk_classes ADD CONSTRAINT direct_call_risk_classes_sample_rate_range
    CHECK (sample_rate IS NULL OR (sample_rate > 0 AND sample_rate <= 1));

ALTER TABLE direct_call_risk_classes ADD CONSTRAINT direct_call_risk_classes_sample_rate_pairing
    CHECK ((risk_class IS NOT DISTINCT FROM 'minimal') = (sample_rate IS NOT NULL)) NOT VALID;

COMMENT ON COLUMN direct_call_risk_classes.sample_rate IS
    'The share of direct calls drawn for review, above 0 and at most 1; set with '
    'minimal and only there. Highest along the chain applies (CHY-269).';

CREATE TABLE direct_call_samples (
    -- The call's own id, made for it by /v1/complete; whether it is drawn is a
    -- hash of it (contexts/orchestration/votes.py), so a drawn call can be shown.
    id                UUID          PRIMARY KEY,
    scope_id          UUID          NOT NULL REFERENCES scope_nodes (id),
    called_by         UUID          NOT NULL REFERENCES actors (id),
    drawn_at          TIMESTAMPTZ   NOT NULL DEFAULT clock_timestamp(),
    sample_rate       NUMERIC(4, 3) NOT NULL CHECK (sample_rate > 0 AND sample_rate <= 1),
    task_type         TEXT          NOT NULL,
    model_used        TEXT          NOT NULL,
    block_versions    JSONB         NOT NULL DEFAULT '{}'::jsonb,
    prompt            TEXT,
    answer            TEXT,
    content_purged_at TIMESTAMPTZ,
    verdict           TEXT          CHECK (verdict IN ('confirmed', 'objected')),
    reason            TEXT,
    reviewed_by       UUID          REFERENCES actors (id),
    reviewed_at       TIMESTAMPTZ,
    -- Content until the period clears it, and nothing of it after.
    CHECK ((content_purged_at IS NULL) = (prompt IS NOT NULL AND answer IS NOT NULL)),
    CHECK (content_purged_at IS NULL OR reason IS NULL),
    -- A verdict has an author and a time, and nothing has a reason without one.
    CHECK ((verdict IS NULL) = (reviewed_by IS NULL)
           AND (verdict IS NULL) = (reviewed_at IS NULL)
           AND (verdict IS NOT NULL OR reason IS NULL))
);

CREATE INDEX direct_call_samples_scope_idx ON direct_call_samples (scope_id, drawn_at);

COMMENT ON TABLE direct_call_samples IS
    'Direct calls (/v1/complete) drawn for review, with placeholders only, and the '
    'verdict on each (CHY-269, ADR-050). Content cleared after the scope''s '
    'run_content period, the row deleted after its run_trace period.';
COMMENT ON COLUMN direct_call_samples.prompt IS
    'What the caller asked, as the model received it: placeholders, no values.';
COMMENT ON COLUMN direct_call_samples.answer IS
    'What the model answered, checked by the same rules before it was kept.';
COMMENT ON COLUMN direct_call_samples.block_versions IS
    'The context the model saw, by block id and version; the blocks are kept.';

-- Who may read a drawn call: as a workflow run (035) — the system, admin, the
-- caller, and the members of its scope and the scopes above.
ALTER TABLE direct_call_samples ENABLE ROW LEVEL SECURITY;
CREATE POLICY direct_call_samples_visible ON direct_call_samples FOR ALL TO cohereny_app
    USING (cc_acting_as_system() OR cc_acting_admin()
           OR called_by = cc_acting_actor() OR cc_acting_member_of(scope_id))
    WITH CHECK (cc_acting_as_system() OR cc_acting_admin()
                OR called_by = cc_acting_actor() OR cc_acting_member_of(scope_id));

GRANT SELECT, INSERT, UPDATE, DELETE ON direct_call_samples TO cohereny_app;
