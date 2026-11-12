-- 047_direct_call_risk_classes.sql — how risky direct model calls are, per scope (CHY-199, ADR-050)
--
-- A workflow has a risk class (044), and its gates ask at least what that class
-- and the scope's data class ask. /v1/complete has no workflow and no gate: it
-- returns the model's answer to the caller as it is. Until now it did so in
-- every scope, also where a workflow's output would wait for one or two people.
--
-- From here on /v1/complete answers only where the minimum for a direct call is
-- `sample`, the one level without a gate before the output. That minimum is the
-- stricter of the scope's data class and the risk class of direct calls in it,
-- which this table holds (decided by Thomas, 10.11.2026). A scope nobody
-- classified counts as `high`, as an unclassified workflow does: a forgotten
-- classification may not loosen anything.
--
-- Append-only, like scope_nodes: each setting is a row, the newest one per
-- scope applies, and the history stays where the classification is read. NULL
-- takes a scope's own classification back, so it inherits again. Along the
-- chain the strictest applies; kernel/scopes.py refuses a setting looser than
-- what the ancestors hold, because it would be stored and have no effect.
--
-- Free of data migration: no scope has a classification yet, so direct calls
-- are refused everywhere until somebody classifies a scope.

CREATE TABLE direct_call_risk_classes (
    id         UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    scope_id   UUID        NOT NULL REFERENCES scope_nodes (id),
    risk_class TEXT        CHECK (risk_class IS NULL OR risk_class IN ('minimal', 'limited', 'high')),
    set_by     UUID        NOT NULL REFERENCES actors (id),
    set_at     TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

-- The newest row per scope is what every read asks for, along a whole chain.
-- clock_timestamp() gives each row its own instant, also within a transaction.
CREATE INDEX direct_call_risk_classes_scope_idx
    ON direct_call_risk_classes (scope_id, set_at DESC);

COMMENT ON TABLE direct_call_risk_classes IS
    'Risk class of direct model calls (/v1/complete) per scope, newest row applies, '
    'strictest along the chain; none set counts as high (CHY-199, ADR-050).';
COMMENT ON COLUMN direct_call_risk_classes.risk_class IS
    'minimal, limited or high; NULL takes the scope''s own classification back.';

GRANT SELECT, INSERT ON direct_call_risk_classes TO cohereny_app;
