-- 034_supersede_by_owner.sql — only cc_supersede_block may supersede a block (CHY-85, ADR-009)
--
-- The guard from 012 lets `superseded_by_version` change only while the session
-- setting `cc.superseding` is 'on', and cc_supersede_block sets it. But any
-- session may set it: `SET cc.superseding = 'on'` followed by a plain UPDATE
-- passed the guard. Harmless while the application was the superuser anyway —
-- it could have switched the trigger off just as well — and a hole as soon as
-- it is not (033).
--
-- What a session cannot fake is who it is. cc_supersede_block is SECURITY
-- DEFINER and owned by the migration role, which also owns the table; inside
-- it, current_user is that owner. Outside it, the application is not. So the
-- column changes only when both hold: the setting is on, and the writer owns the
-- table.
--
-- A second trigger rather than a fourth restatement of
-- cc_context_blocks_append_only: this rule is about who writes, the existing
-- one about what may change, and each reads clearer alone. Both are BEFORE
-- UPDATE row triggers; either one refusing is enough.

CREATE OR REPLACE FUNCTION cc_context_blocks_supersede_by_owner() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.superseded_by_version IS DISTINCT FROM NEW.superseded_by_version
       AND NOT pg_has_role(current_user,
                           (SELECT relowner FROM pg_class WHERE oid = TG_RELID),
                           'USAGE')
    THEN
        RAISE EXCEPTION
            'context_blocks: superseded_by_version may only be set by '
            'cc_supersede_block'
            USING
                ERRCODE = 'insufficient_privilege',
                HINT    = 'Setting cc.superseding yourself does not count; call '
                          'cc_supersede_block (ADR-009, CHY-85).';
    END IF;
    RETURN NEW;
END;
$$;

COMMENT ON FUNCTION cc_context_blocks_supersede_by_owner() IS
    'Refuses a change of superseded_by_version by anyone but the table owner, '
    'i.e. outside the SECURITY DEFINER function cc_supersede_block (CHY-85).';

DROP TRIGGER IF EXISTS context_blocks_supersede_by_owner ON context_blocks;
CREATE TRIGGER context_blocks_supersede_by_owner
    BEFORE UPDATE ON context_blocks
    FOR EACH ROW EXECUTE FUNCTION cc_context_blocks_supersede_by_owner();
