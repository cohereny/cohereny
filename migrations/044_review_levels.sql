-- 044_review_levels.sql — how strict a gate must be follows the data and the risk (CHY-223, ADR-050)
--
-- How strict a gate is was fixed in the node: `approver` or `four_eyes`, chosen
-- by whoever drew the workflow. A research summary carried the same weight as
-- a credit decision, and a workflow in a `critical` scope could be released by
-- one approver. `risk_classification` was stored since 018 and read by nothing.
--
-- From here on a gate has a minimum: the stricter of what the data class of the
-- workflow's scope asks (ADR-041) and what the workflow's risk class asks.
-- Activation refuses a gate below it; at run time the stricter of the gate and
-- the minimum *at that moment* applies, so a scope classified stricter after
-- the release makes its gates stricter without a new release. Which level
-- applied, and why, is written to the execution and to the audit log.
--
-- `risk_class` is a typed field: minimal, limited, high. A workflow without one
-- counts as `high` — a forgotten classification may not loosen anything, as a
-- scope without a data class counts as `critical`.
--
-- `review_level` is the level that applied when the run reached the gate. A
-- decision reads it instead of recomputing: a reclassification while a run
-- waits must not change the rules under the people already deciding. NULL for
-- gates reached before this migration; they keep their node's `review_role`.
--
-- Free of data migration: no workflow has a risk class yet, so all count as
-- high, and no execution has a level.

ALTER TABLE workflow_definitions ADD COLUMN risk_class TEXT
    CHECK (risk_class IS NULL OR risk_class IN ('minimal', 'limited', 'high'));

COMMENT ON COLUMN workflow_definitions.risk_class IS
    'How much harm a wrong output of this workflow can do (CHY-223, ADR-050): minimal, '
    'limited, high. Sets with the data class the minimum review level of its gates. '
    'NULL counts as high.';

COMMENT ON COLUMN workflow_definitions.risk_classification IS
    'Deprecated (CHY-223): a free object, read by nothing. risk_class replaces it.';

ALTER TABLE node_executions ADD COLUMN review_level TEXT
    CHECK (review_level IS NULL OR review_level IN ('approver', 'four_eyes'));

COMMENT ON COLUMN node_executions.review_level IS
    'human_review only: the level that applied when the run reached the gate — the '
    'stricter of the gate and the minimum of data class and risk class (CHY-223). '
    'NULL before migration 044.';
