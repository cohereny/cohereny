-- 040_compute_steps.sql — a node type for pure, registered computation (CHY-173, ADR-048)
--
-- The banking workflow has steps that need no model and must not have one:
-- ratios from the figures a model read, findings from thresholds. The runner
-- had no node type for them; `tool_call` stays refused because of its side
-- effects. A `compute` node names a pure function registered in the code
-- (`steps/`) by name and version — no code in the database, no expression
-- language in a definition.
--
-- Two typed columns, by the rule of ADR-040, held to the node type in both
-- directions as `task_type` is in 023. Whether the named function exists is
-- checked at activation (rule `unknown_function`): the database cannot see the
-- code.
--
-- Free of data migration: both columns are new and NULL everywhere, and no
-- existing node is of the new type.

ALTER TABLE workflow_nodes
    ADD COLUMN IF NOT EXISTS function         TEXT,
    ADD COLUMN IF NOT EXISTS function_version INTEGER;

-- Without IF EXISTS, as in 038: the constraint has this name since 018, and a
-- different one would have to fail here rather than leave the old list in place.
ALTER TABLE workflow_nodes DROP CONSTRAINT workflow_nodes_node_type_check;
ALTER TABLE workflow_nodes ADD CONSTRAINT workflow_nodes_node_type_check
    CHECK (node_type IN (
        'context_compose', 'llm_call', 'tool_call',
        'fork_gateway', 'join_gateway', 'human_review',
        'sub_workflow', 'compute'
    ));

-- A function on any other node type would be stored and never run.
ALTER TABLE workflow_nodes DROP CONSTRAINT IF EXISTS workflow_nodes_function_only_on_compute;
ALTER TABLE workflow_nodes ADD CONSTRAINT workflow_nodes_function_only_on_compute
    CHECK (node_type = 'compute' OR (function IS NULL AND function_version IS NULL));

-- And a compute node without one cannot run at all. Blank counts as missing; a
-- version is pinned and starts at 1, so a definition keeps computing what it
-- was reviewed against.
ALTER TABLE workflow_nodes DROP CONSTRAINT IF EXISTS workflow_nodes_compute_names_function;
ALTER TABLE workflow_nodes ADD CONSTRAINT workflow_nodes_compute_names_function
    CHECK (node_type <> 'compute'
           OR (length(btrim(coalesce(function, ''))) > 0
               AND function_version IS NOT NULL AND function_version >= 1));

-- Written into `config` it would be stored and ignored. Refused, as in 023 and 039.
ALTER TABLE workflow_nodes DROP CONSTRAINT IF EXISTS workflow_nodes_config_without_function;
ALTER TABLE workflow_nodes ADD CONSTRAINT workflow_nodes_config_without_function
    CHECK (NOT (config ? 'function') AND NOT (config ? 'function_version'));

COMMENT ON COLUMN workflow_nodes.function IS
    'Only for compute, and required there: the registered pure function the node '
    'runs (steps/, ADR-048), e.g. banking.ratios.';
COMMENT ON COLUMN workflow_nodes.function_version IS
    'Only for compute, and required there: the pinned version of the function, '
    'from 1. A changed calculation is a new version.';
