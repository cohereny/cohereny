-- 021_routing_rules.sql — routing rules become context blocks (CHY-87, ADR-037)
--
-- The routing table lived in router/routing-config.yaml: a file in the image,
-- changed by a redeploy. The design has always said otherwise — a routing rule
-- is a ContextBlock, versioned and approval-bound — and ADR-017 named it in a
-- sentence that could not be implemented, because there were no such rows:
--
--     "ADR-017 hangs the field on `routing_rule.action`. There is no
--      routing_rule -- `router/` is a YAML config and a RouterConfig class"
--        (016_privacy_ratchet.sql, contexts/repository/ratchet.py)
--
-- Now there is one. Which means the machinery that already guards blocks —
-- versions, the review workflow, the privacy ratchet from CHY-62, scope
-- inheritance — guards routing too, without a second implementation of any of
-- it. A deeper scope may tighten a routing rule and may not loosen it, and
-- that falls out of ADR-017 rather than being built again here.

-- The one thing that does NOT fall out for free, and the reason ADR-037 exists:
-- `compose` took every active block in a scope when no type filter was given.
-- Correct while every block type was prompt material. A routing rule pasted
-- into a system prompt would tell the model which models exist and how one is
-- chosen — and it would do so silently, because a longer prompt looks like a
-- richer context, not like a leak.
--
-- The guard is in Python (COMPOSABLE_BLOCK_TYPES, held against this CHECK by
-- tests/test_status_vocabularies.py). It is deliberately an allow-list: a
-- seventh type added tomorrow is non-composable until somebody says otherwise,
-- which is the safe direction to be wrong in.

ALTER TABLE context_blocks
    DROP CONSTRAINT IF EXISTS context_blocks_block_type_check;

ALTER TABLE context_blocks
    ADD CONSTRAINT context_blocks_block_type_check
    CHECK (block_type IN (
        'system_prompt', 'user_context', 'tool_schema',
        'example_io', 'calibration_anchor',
        -- Not prompt material. See above.
        'routing_rule'
    ));

COMMENT ON COLUMN context_blocks.block_type IS
    'What kind of block. Five types are prompt material and get composed; '
    'routing_rule is configuration and never reaches a model (ADR-037). '
    'The composable set lives in contexts/repository/schema.py and is held '
    'against this CHECK by tests/test_status_vocabularies.py.';
