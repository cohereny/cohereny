-- Migration 012: append-only for context_blocks via cc_supersede_block (CHY-60, ADR-009)
--
-- State before this migration, probed against cohereny_dev on 2026-08-12:
--
--   UPDATE   allowed. 002_add_block_status dropped no_update_context_blocks for
--            "Phase 1 CRUD" and noted it would return in Phase 2. This is Phase 2.
--   DELETE   silently discarded. 002 dropped only the update rule; the delete
--            rule from 001_initial was left behind -- not by decision, since the
--            comment names only no_update.
--   triggers none.
--
-- So the two halves were inconsistent in the worst direction: the operation that
-- rewrites history was permitted, and the harmless one failed quietly.
--
-- Both rules go. Enforcement moves to triggers that RAISE, for the same reason
-- as CHY-31: a guard that discards a statement without a word turns a bug into
-- a silent data problem, and the caller cannot tell "refused" from "matched
-- nothing".
--
-- SCOPE LIMIT (CHY-85): ADR-009 assumes two database roles, cohereny_app without
-- UPDATE rights and cohereny_version_manager with them. Neither exists -- there
-- is one role, it is superuser, and it owns every table. SECURITY DEFINER
-- therefore bridges no privilege gap here, and a superuser can DISABLE TRIGGER.
-- What this migration delivers is enforcement against application faults, which
-- is what ADR-009 names first ("auch nicht durch Bugs in der Anwendung"). It is
-- not protection against someone holding the application's credentials on
-- purpose. The function is written SECURITY DEFINER anyway so it starts working
-- the moment CHY-85 lands, without a second migration.

DROP RULE IF EXISTS no_update_context_blocks ON context_blocks;
DROP RULE IF EXISTS no_delete_context_blocks ON context_blocks;

-- ── The sanctioned path marker ───────────────────────────────────────────────
--
-- The trigger has to let cc_supersede_block through while refusing everyone
-- else. It recognises the function by a transaction-local setting that only the
-- function sets. `set_config(..., true)` scopes it to the transaction, so it
-- cannot leak into the next statement on a pooled connection.

-- Three classes of column, three rules. A blanket "no UPDATE" was the first
-- attempt and it broke the running system: api/v1/blocks.py is implemented, not
-- a placeholder, and its PATCH and DELETE routes both issue UPDATE statements.
-- The unit tests would not have caught it — they mock the pool, so the failure
-- would only have appeared after deploy.
--
--   identity    block_id, version, scope_id, block_type, author_id, created_at
--               Never change. These say which block, which version, written by
--               whom and when — the facts a revision history exists to preserve.
--   content     content, metadata, tags
--               Editable while the row is a draft. A draft was never published,
--               so there is no history to protect yet; that is exactly what
--               002_add_block_status meant by "draft → newly created, editable".
--               Once it leaves draft the row is frozen and changes go through
--               cc_supersede_block.
--   lifecycle   status, superseded_by_version
--               status may move (approve, deprecate) — retiring a block must
--               stay possible. superseded_by_version is the function's alone.
--
-- Who may trigger a status change is an authorization question, answered in the
-- API by core.authz, not here. This trigger answers what is structurally
-- allowed, not who is allowed to ask.

CREATE OR REPLACE FUNCTION cc_context_blocks_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    -- COALESCE is load-bearing. current_setting(..., true) returns NULL when the
    -- setting was never assigned in this session, and NULL = 'on' is NULL, not
    -- false. Written as `v := current_setting(...) = 'on'` the variable becomes
    -- NULL, `IF NOT v` is then NULL rather than true, and the RAISE below is
    -- skipped — the guard silently lets the write through.
    --
    -- Found by probing cohereny_dev, not by reading: a hand-written
    -- `UPDATE ... SET superseded_by_version = 2` succeeded on a fresh session and
    -- only started failing after cc_supersede_block had run once in the same
    -- session and left the setting at 'off'. A mocked test could not have seen it.
    v_superseding boolean := COALESCE(current_setting('cc.superseding', true), 'off') = 'on';
BEGIN
    IF TG_OP <> 'UPDATE' THEN
        RAISE EXCEPTION
            'context_blocks is append-only: % is not permitted', TG_OP
            USING
                ERRCODE = 'integrity_constraint_violation',
                HINT    = 'Blocks are retired with status = ''deprecated'', never '
                          'removed. See ADR-009 and CHY-60.';
    END IF;

    -- Identity is immutable in every case, including inside the function.
    IF OLD.block_id   IS DISTINCT FROM NEW.block_id
    OR OLD.version    IS DISTINCT FROM NEW.version
    OR OLD.scope_id   IS DISTINCT FROM NEW.scope_id
    OR OLD.block_type IS DISTINCT FROM NEW.block_type
    OR OLD.author_id  IS DISTINCT FROM NEW.author_id
    OR OLD.created_at IS DISTINCT FROM NEW.created_at
    THEN
        RAISE EXCEPTION
            'context_blocks: identity columns are immutable (block_id, version, '
            'scope_id, block_type, author_id, created_at)'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;

    -- superseded_by_version belongs to cc_supersede_block and nobody else.
    IF OLD.superseded_by_version IS DISTINCT FROM NEW.superseded_by_version THEN
        IF NOT v_superseding THEN
            RAISE EXCEPTION
                'context_blocks: superseded_by_version may only be set by '
                'cc_supersede_block'
                USING
                    ERRCODE = 'integrity_constraint_violation',
                    HINT    = 'Call cc_supersede_block(block_id, content, metadata, '
                              'tags, actor_ref, change_reason) to publish a new '
                              'version.';
        END IF;
        IF OLD.superseded_by_version IS NOT NULL THEN
            RAISE EXCEPTION 'block version % is already superseded', OLD.version
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
    END IF;

    -- Content is frozen once the row leaves draft.
    IF OLD.status <> 'draft'
       AND (OLD.content  IS DISTINCT FROM NEW.content
         OR OLD.metadata IS DISTINCT FROM NEW.metadata
         OR OLD.tags     IS DISTINCT FROM NEW.tags)
    THEN
        RAISE EXCEPTION
            'context_blocks: content of a % block is immutable', OLD.status
            USING
                ERRCODE = 'integrity_constraint_violation',
                HINT    = 'Use cc_supersede_block to publish a new version '
                          'instead of editing a published one.';
    END IF;

    RETURN NEW;
END;
$$;

COMMENT ON FUNCTION cc_context_blocks_append_only() IS
    'Append-only guard for context_blocks (CHY-60, ADR-009): identity columns '
    'never change, content only while draft, superseded_by_version only via '
    'cc_supersede_block, and DELETE/TRUNCATE never.';

-- Row-level throughout for UPDATE: the decision depends on which columns
-- changed, so the trigger needs OLD and NEW. Statement-level for DELETE and
-- TRUNCATE, where it does not.
DROP TRIGGER IF EXISTS context_blocks_no_update ON context_blocks;
CREATE TRIGGER context_blocks_no_update
    BEFORE UPDATE ON context_blocks
    FOR EACH ROW EXECUTE FUNCTION cc_context_blocks_append_only();

DROP TRIGGER IF EXISTS context_blocks_no_delete ON context_blocks;
CREATE TRIGGER context_blocks_no_delete
    BEFORE DELETE ON context_blocks
    FOR EACH STATEMENT EXECUTE FUNCTION cc_context_blocks_append_only();

DROP TRIGGER IF EXISTS context_blocks_no_truncate ON context_blocks;
CREATE TRIGGER context_blocks_no_truncate
    BEFORE TRUNCATE ON context_blocks
    FOR EACH STATEMENT EXECUTE FUNCTION cc_context_blocks_append_only();

-- ── The sanctioned path itself ───────────────────────────────────────────────

CREATE OR REPLACE FUNCTION cc_supersede_block(
    p_block_id      UUID,
    p_content       TEXT,
    p_metadata      JSONB   DEFAULT '{}'::jsonb,
    p_tags          TEXT[]  DEFAULT '{}'::text[],
    p_actor_ref     UUID    DEFAULT NULL,
    p_change_reason TEXT    DEFAULT NULL
) RETURNS context_blocks
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_current context_blocks;
    v_new     context_blocks;
BEGIN
    -- FOR UPDATE, not a plain SELECT: two concurrent supersedes would otherwise
    -- both read the same current version and both try to insert version n+1.
    -- The partial unique index would catch it, but as a constraint violation
    -- rather than a wait -- one caller would fail for no reason it can act on.
    SELECT * INTO v_current
      FROM context_blocks
     WHERE block_id = p_block_id AND superseded_by_version IS NULL
     FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'no current version for block_id %', p_block_id
            USING ERRCODE = 'no_data_found';
    END IF;

    -- Retire the old version BEFORE inserting the new one. The order is forced
    -- by idx_blocks_one_active, the partial unique index on block_id WHERE
    -- superseded_by_version IS NULL: inserting first would leave two rows with
    -- NULL for one statement, and a unique index is checked per statement, not
    -- at commit. Verified the hard way -- the reverse order failed against
    -- cohereny_dev with "duplicate key value violates idx_blocks_one_active".
    --
    -- The new version number is known in advance, so nothing is lost by doing
    -- it this way round.
    PERFORM set_config('cc.superseding', 'on', true);
    UPDATE context_blocks
       SET superseded_by_version = v_current.version + 1
     WHERE id = v_current.id;
    PERFORM set_config('cc.superseding', 'off', true);

    INSERT INTO context_blocks (
        block_id, version, scope_id, block_type,
        content, metadata, tags, author_id, status
    ) VALUES (
        p_block_id, v_current.version + 1, v_current.scope_id, v_current.block_type,
        p_content, p_metadata, p_tags, p_actor_ref, 'draft'
    )
    RETURNING * INTO v_new;

    -- Written here rather than by the caller: the function is the only way to
    -- supersede, so an entry written inside it cannot be forgotten. Same
    -- transaction, so the history and the record of it stand or fall together.
    INSERT INTO audit_log (actor_id, action, target_ref, payload)
    VALUES (
        p_actor_ref,
        'block.superseded',
        'block:' || p_block_id::text,
        jsonb_build_object(
            'from_version', v_current.version,
            'to_version',   v_new.version,
            'change_reason', p_change_reason
        )
    );

    RETURN v_new;
END;
$$;

COMMENT ON FUNCTION cc_supersede_block(UUID, TEXT, JSONB, TEXT[], UUID, TEXT) IS
    'Publishes a new version of a context block and marks the previous one '
    'superseded, atomically, with an audit entry. The only sanctioned way to '
    'change a block (ADR-009, CHY-60). SECURITY DEFINER takes effect once the '
    'role separation from CHY-85 exists.';
