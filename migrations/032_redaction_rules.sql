-- 032_redaction_rules.sql — redaction rules become context blocks (CHY-63, ADR-018)
--
-- Since CHY-63 part 1 every model call replaces identifiers first: the shipped
-- patterns (e-mail, IBAN, phone, tax id, health insurance number, labelled date
-- of birth) and whatever the caller names. What a scope adds on its own -- the
-- names of its staff, the format of its case numbers -- needs a home.
--
-- Decided 05.11.2026: a block, not a table with an admin API. A routing rule
-- decides where data goes and is a reviewed block since 021; a redaction rule
-- decides what leaves, and belongs in the same review. So it inherits the
-- machinery without a second implementation of any of it: versions, the review
-- workflow, inheritance along the scope tree, the privacy ratchet (ADR-017) and
-- four eyes for anything that loosens (ADR-045).
--
-- Three shapes, all in `metadata` (kernel/redaction.py reads them):
--
--     {"category": "STAFF", "texts": ["Dr. Schmidt"]}       values to replace
--     {"category": "CASE_NUMBER", "pattern": "..."}         a pattern to replace
--     {"disable": ["PHONE"]}                                switch a shipped rule off
--
-- Rules add up down the tree; no scope removes what a scope above added.
-- Switching a shipped rule off is the one loosening, and it is fenced three
-- times: only in a scope without a parent, only by a block that declares
-- `pii_redaction` as `permissive` -- which makes it a data-flow block and sends
-- it to a second person -- and the ratchet refuses it where a `restrictive`
-- claim on the key stands in the same scope.
--
-- Like routing_rule, never composed: COMPOSABLE_BLOCK_TYPES is an allow-list,
-- and a redaction rule pasted into a prompt would hand the model the very
-- values it exists to keep away from it.

ALTER TABLE context_blocks
    DROP CONSTRAINT IF EXISTS context_blocks_block_type_check;

ALTER TABLE context_blocks
    ADD CONSTRAINT context_blocks_block_type_check
    CHECK (block_type IN (
        'system_prompt', 'user_context', 'tool_schema',
        'example_io', 'calibration_anchor',
        -- Not prompt material: configuration that never reaches a model.
        'routing_rule', 'redaction_rule'
    ));

COMMENT ON COLUMN context_blocks.block_type IS
    'What kind of block. Four types are prompt material and get composed; '
    'routing_rule (ADR-037), calibration_anchor (ADR-043) and redaction_rule '
    '(ADR-018) never reach a model as context. The composable set lives in '
    'contexts/repository/schema.py and is held against the CHECK on '
    'workflow_nodes.context_requirements.';

-- The key was seeded in 016 with a meaning nothing enforced. It has one now.
UPDATE governance_keys
   SET description =
       'Whether personal data is replaced before the prompt reaches a model '
       '(ADR-018). Replacement always runs since CHY-63. A redaction_rule '
       'block that switches a shipped rule off declares this key as '
       'permissive and may stand only in a scope without a parent; a '
       'restrictive claim in that scope forbids it.'
 WHERE key = 'pii_redaction';
