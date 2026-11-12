-- 039_node_output_schema.sql — an llm_call can name the shape of its answer (CHY-175)
--
-- Until this migration nothing checked what a model answered inside a run. A
-- step that asked for JSON got text, and whatever came back went on to the next
-- node. The banking pilot parsed its answers itself and fell back to defaults
-- when that failed — so a misread figure travelled on as if it were read, and
-- `extract_kpis` decides whether a person ever sees the case (Buch 10.2, K-057).
--
-- An `llm_call` may now carry a JSON Schema. The runner reads the answer as one
-- JSON object, checks it, and puts the checked object into the node's output as
-- `data`, where edges and successors can address it (`data.risk_level`). A
-- mismatch fails the node; there is no default to fall back to (ADR-004: a gate
-- that passes on silence is not a gate). This is layer 1 of ADR-006 — the
-- deterministic check — inside the runner instead of only in the benchmark.
--
-- A typed column, not a key in `config`, by the rule of ADR-040: what changes
-- how a node runs is a column the database can hold to its node type.
--
-- Free of data migration: the column is new and NULL everywhere, which is what
-- "no schema" means.

ALTER TABLE workflow_nodes
    ADD COLUMN IF NOT EXISTS output_schema JSONB;

-- Only a model's answer has a shape to check. On any other node type the
-- schema would be stored and never read — refused, as in 022 and 023.
ALTER TABLE workflow_nodes DROP CONSTRAINT IF EXISTS workflow_nodes_output_schema_only_on_llm_call;
ALTER TABLE workflow_nodes ADD CONSTRAINT workflow_nodes_output_schema_only_on_llm_call
    CHECK (node_type = 'llm_call' OR output_schema IS NULL);

-- The answer is read as one JSON object, so the schema describes an object. Its
-- fields are what an edge or a successor addresses; an array or a bare string
-- would leave nothing to address. Whether the rest is a valid JSON Schema is
-- checked by the API before the row is written — the database cannot.
ALTER TABLE workflow_nodes DROP CONSTRAINT IF EXISTS workflow_nodes_output_schema_is_object;
ALTER TABLE workflow_nodes ADD CONSTRAINT workflow_nodes_output_schema_is_object
    CHECK (output_schema IS NULL
           OR (jsonb_typeof(output_schema) = 'object'
               AND output_schema ->> 'type' = 'object'));

-- Written into `config` it would be stored and ignored, and the author would
-- believe the answer is checked. Refused, like `model` and `task_type` in 023.
ALTER TABLE workflow_nodes DROP CONSTRAINT IF EXISTS workflow_nodes_config_without_output_schema;
ALTER TABLE workflow_nodes ADD CONSTRAINT workflow_nodes_config_without_output_schema
    CHECK (NOT (config ? 'output_schema'));

COMMENT ON COLUMN workflow_nodes.output_schema IS
    'Only for llm_call. A JSON Schema of type object the model''s answer must '
    'match; the checked object is in the node''s output as data, a mismatch '
    'fails the node (CHY-175, ADR-006 layer 1).';
