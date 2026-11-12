-- 024_scope_privacy_level.sql — a scope declares what data lives in it (CHY-160)
--
-- Three mechanisms guarded the choice of model, and none of them compared the
-- class of the data with the ceiling of the model:
--
--   check_chain (router/rules.py)  no descent relative to the FIRST candidate
--   the privacy ratchet (ADR-017)  the DECLARED direction of a block
--   approval of a rule             a human looks at it
--
-- `max_privacy_level` in router/routing-config.yaml was only ever used
-- relatively. Harmless while every model was `critical`; since CHY-157 two
-- models are `public`, and an approved rule [gemma4-cloud, ...] for a scope
-- full of critical data would have passed every machine check.
--
-- Decided on 2026-09-24 (Thomas): the data class belongs to the SCOPE. It is a
-- property of the place the data lives, not of the path it happens to take —
-- and a scope is what both /v1/complete and the runner route from.
--
-- NULL means "not declared at this node", not "unrestricted". The effective
-- class of a scope is the strictest one declared anywhere on its ancestor
-- chain, and **a chain on which nobody declared anything counts as critical**
-- (kernel/scopes.py). Unclassified data goes nowhere a local model does not.
--
-- What cannot be a CHECK: that a child is not looser than its parent. That
-- needs another row. It is enforced where a scope is created, and it could not
-- do harm if it were bypassed: the strictest value on the chain wins, so a
-- looser child is ineffective rather than dangerous — which is exactly why it
-- is refused instead of stored.

ALTER TABLE scope_nodes
    ADD COLUMN IF NOT EXISTS privacy_level TEXT;

ALTER TABLE scope_nodes DROP CONSTRAINT IF EXISTS scope_nodes_privacy_level_check;
ALTER TABLE scope_nodes ADD CONSTRAINT scope_nodes_privacy_level_check
    CHECK (privacy_level IS NULL OR privacy_level IN ('public', 'internal', 'confidential', 'critical'));

COMMENT ON COLUMN scope_nodes.privacy_level IS
    'The class of data this scope holds, if declared here. The effective class '
    'is the strictest on the ancestor chain, critical when none is declared. '
    'Every candidate of a routing rule must be allowed to see it (CHY-160).';
