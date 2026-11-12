-- 023_node_task_type.sql — the routing key gets a column (CHY-145, ADR-040)
--
-- Until this migration an `llm_call` read three keys from `workflow_nodes.config`:
--
--     model      = ctx.config.get("model")
--     task_type  = ctx.config.get("task_type", ctx.node_type)
--
-- The first bypassed everything CHY-87 built. A node naming its own model
-- skipped the approval of a routing rule, scope inheritance, the privacy
-- ratchet and the fallback chain — a run through the runner could reach a
-- model that was never released for its scope. The second fell back silently
-- to the node type, so a typo routed the node as task "llm_call".
--
-- Since CHY-145 the runner resolves its model the way /v1/complete does: by
-- task type, from the routing rules of the workflow's scope. That makes
-- `task_type` the key that decides which model sees the data. By the rule of
-- ADR-040 it gets a typed column; `model` leaves `config` entirely.
--
-- Step 2 of emptying `config` (ADR-040). Step 1 was `block_types` (022),
-- step 3 is `prompt`/`system` (CHY-146).
--
-- Free of data migration: `workflow_nodes` holds zero rows in dev, test and
-- prod, and none carries `config.model` or `config.task_type` — counted on
-- 2026-09-24 before writing this file.

ALTER TABLE workflow_nodes
    ADD COLUMN IF NOT EXISTS task_type TEXT;

-- ── 1. Both directions, unlike 022 ────────────────────────────────────────────
--
-- A task type on a node that calls no model reads as configuration and does
-- nothing: refused, as in 018 and 022.
ALTER TABLE workflow_nodes DROP CONSTRAINT IF EXISTS workflow_nodes_task_type_only_on_llm_call;
ALTER TABLE workflow_nodes ADD CONSTRAINT workflow_nodes_task_type_only_on_llm_call
    CHECK (node_type = 'llm_call' OR task_type IS NULL);

-- And the other way round, which 022 deliberately left out for compose: an
-- `llm_call` without a task type cannot be routed. It is the sub_workflow
-- without a target of migration 018 — a node that cannot run. Blank counts as
-- missing: a routing rule for the task type "" is not a decision anybody made.
ALTER TABLE workflow_nodes DROP CONSTRAINT IF EXISTS workflow_nodes_llm_call_has_task_type;
ALTER TABLE workflow_nodes ADD CONSTRAINT workflow_nodes_llm_call_has_task_type
    CHECK (node_type <> 'llm_call' OR length(btrim(coalesce(task_type, ''))) > 0);

-- ── 2. The old spellings stop being accepted ──────────────────────────────────
--
-- Both were read until this migration. Left storable, an author would keep
-- writing them, keep seeing 201, and get a node that ignores the model it names
-- — the silence 022 refused for `block_types`.
ALTER TABLE workflow_nodes DROP CONSTRAINT IF EXISTS workflow_nodes_config_without_model;
ALTER TABLE workflow_nodes ADD CONSTRAINT workflow_nodes_config_without_model
    CHECK (NOT (config ? 'model'));

ALTER TABLE workflow_nodes DROP CONSTRAINT IF EXISTS workflow_nodes_config_without_task_type;
ALTER TABLE workflow_nodes ADD CONSTRAINT workflow_nodes_config_without_task_type
    CHECK (NOT (config ? 'task_type'));

COMMENT ON COLUMN workflow_nodes.task_type IS
    'Only for llm_call, and required there. The key the routing rules of the '
    'workflow''s scope are resolved by — which makes it the value that decides '
    'which model sees the data (CHY-145, ADR-038, ADR-040).';
