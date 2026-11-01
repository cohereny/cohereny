-- 022_context_requirements.sql — what a context_compose node asks for (CHY-134)
--
-- The whitepaper has carried this field since v2.x:
--
--     # Nur für node_type = "context_compose":
--     context_requirements: Optional[ContextRequirements]
--
-- There was no column, no code and no contract entry. What the executor of
-- CHY-133 actually reads is `workflow_nodes.config["block_types"]` — the same
-- kind of gap as `condition TEXT` against `condition JSONB` before CHY-130:
-- model and schema said different things, and as long as nothing evaluated the
-- field, nothing noticed. Since CHY-133 something evaluates it.
--
-- Resolved towards the model, and the reason is what the field decides: it
-- picks **which context reaches a model**. A free JSONB that anyone may write
-- anything into is the wrong place for that.
--
-- The column is the first step of emptying `config` (decision of 07.09.2026).
-- `model` follows in CHY-145, `prompt`/`system` in CHY-146. Each step is one
-- migration and one review, rather than one rewrite nobody can check.
--
-- Free of data migration: workflow_definitions, workflow_nodes and
-- workflow_connections hold zero rows in dev, test and prod — checked at the
-- host for migration 018 on 2026-09-04 and unchanged since, because the ten
-- write endpoints only stopped answering 501 in this milestone.

ALTER TABLE workflow_nodes
    ADD COLUMN IF NOT EXISTS context_requirements JSONB;

-- ── 1. Only where it means something ──────────────────────────────────────────
--
-- The same rule as workflow_nodes_target_only_on_sub in migration 018: a
-- requirement on a node that composes nothing reads as configuration and does
-- nothing. Refused rather than ignored (ADR-032, one layer down).
ALTER TABLE workflow_nodes DROP CONSTRAINT IF EXISTS workflow_nodes_requirements_only_on_compose;
ALTER TABLE workflow_nodes ADD CONSTRAINT workflow_nodes_requirements_only_on_compose
    CHECK (node_type = 'context_compose' OR context_requirements IS NULL);

-- The other direction is deliberately absent, unlike the sub_workflow pair: a
-- context_compose node **without** requirements is the ordinary case. It means
-- "everything this scope makes available", which is a complete answer — where
-- a sub_workflow without a target is a node that cannot run.

-- ── 2. What may be in it ──────────────────────────────────────────────────────
--
-- `block_types` and nothing else, non-empty, and every entry a type that may
-- actually be composed.
--
-- The allow-list repeats COMPOSABLE_BLOCK_TYPES (contexts/repository/schema.py,
-- ADR-037). SQL cannot import Python; the two are held together by
-- tests/test_status_vocabularies.py, which reads this literal.
--
-- Why the database and not the model: the vocabulary belongs to the repository
-- context, and orchestration may not import it (ADR-030). The database knows
-- both tables and is the one enforcement point no write path goes around —
-- the same argument ratchet.py makes for checking at two places rather than
-- only at the approve endpoint.
--
-- What it prevents concretely: `active_blocks_in_scopes` intersects the
-- requested types with COMPOSABLE_BLOCK_TYPES, so a node asking for
-- `routing_rule` does not leak the rule into a prompt — it composes **nothing**
-- and reports success. An empty context reaching a model looks exactly like a
-- real one, which is why the value is refused at write time instead.
--
-- The empty array is refused for the same reason: `[]` is falsy in Python and
-- the store reads it as "no filter given", i.e. as *everything*. A value that
-- means the opposite of what it says has no business being storable.
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
                '["system_prompt", "user_context", "tool_schema",
                  "example_io", "calibration_anchor"]'::jsonb
        )
    );

-- ── 3. The old spelling stops being accepted ──────────────────────────────────
--
-- `config["block_types"]` was the only reader until this migration. Leaving it
-- storable would mean a workflow author can keep writing it, keep seeing 201,
-- and get a composition that ignores it — the failure this project keeps
-- meeting under other names. One key, one place.
ALTER TABLE workflow_nodes DROP CONSTRAINT IF EXISTS workflow_nodes_config_without_block_types;
ALTER TABLE workflow_nodes ADD CONSTRAINT workflow_nodes_config_without_block_types
    CHECK (NOT (config ? 'block_types'));

COMMENT ON COLUMN workflow_nodes.context_requirements IS
    'Only for context_compose: {"block_types": [...]}, non-empty, composable '
    'types only (ADR-037). NULL means everything the scope makes available. '
    'Typed rather than a key in `config`, because it decides which context '
    'reaches a model (CHY-134).';
