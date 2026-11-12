-- 046_compose_by_tags.sql — a context_compose node may ask for blocks by tag (CHY-178)
--
-- A compose node chose blocks by type and by the workflow's scope, nothing
-- else. Every model step of one workflow therefore received the same system
-- prompts: a credit workflow with five steps — classify the document, read the
-- figures, assess the anomalies, rate the risk, draft the vote — could not give
-- each step its own instruction from the repository, only one composed context
-- for all of them, or literals in the definition that bypass the review of
-- blocks (ADR-042).
--
-- `context_blocks.tags` has existed since 001 and is described in the schema as
-- "categorization tags for selective composition"; nothing read it. Now a node
-- may say `{"block_types": ["system_prompt"], "tags": ["banking:risiko"]}`: only
-- blocks that carry every tag named. Without `tags`, nothing changes.
--
-- The filter applies to the version that would be composed, the newest active
-- one, not to any version: a block whose newest active version dropped a tag is
-- not composed for it, even if an older active version carried it. Filtering
-- before choosing the version would put the older text back into the prompt,
-- silently (contexts/repository/store.py).
--
-- `tags`, if present, is a non-empty array of non-empty strings — `[]` would
-- read as "no filter" and say the opposite of what it means, as for
-- `block_types` in 022; `""` is a tag no one can mean.
--
-- The composable types are the ones of 028, which took calibration_anchor out
-- (ADR-043). tests/test_status_vocabularies.py holds the latest definition of
-- this constraint against COMPOSABLE_BLOCK_TYPES.

ALTER TABLE workflow_nodes DROP CONSTRAINT workflow_nodes_requirements_shape;
ALTER TABLE workflow_nodes ADD CONSTRAINT workflow_nodes_requirements_shape
    CHECK (
        context_requirements IS NULL
        OR (
            jsonb_typeof(context_requirements) = 'object'
            AND context_requirements ? 'block_types'
            AND context_requirements - 'block_types' - 'tags' = '{}'::jsonb
            AND jsonb_typeof(context_requirements -> 'block_types') = 'array'
            AND jsonb_array_length(context_requirements -> 'block_types') > 0
            AND context_requirements -> 'block_types' <@
                '["system_prompt", "user_context", "tool_schema", "example_io"]'::jsonb
            AND (NOT context_requirements ? 'tags'
                 OR (jsonb_typeof(context_requirements -> 'tags') = 'array'
                     AND jsonb_array_length(context_requirements -> 'tags') > 0
                     AND NOT jsonb_path_exists(context_requirements -> 'tags',
                             '$[*] ? (@.type() != "string" || @ == "")')))
        )
    );

COMMENT ON COLUMN workflow_nodes.context_requirements IS
    'Only for context_compose: {"block_types": [...], "tags": [...]}. block_types '
    'non-empty, composable types only (ADR-037); tags optional, non-empty, and a '
    'block must carry every one (CHY-178). NULL means everything the scope makes '
    'available. Typed rather than a key in `config`, because it decides which '
    'context reaches a model (CHY-134).';
