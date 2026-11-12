-- Migration 013: review workflow for context blocks (CHY-32)
--
-- Blocks could only ever be draft, active or deprecated (002), and nothing in
-- the codebase set `active` — so /v1/compose and /v1/complete, which both
-- filter on it, could only ever return nothing. This migration adds the two
-- states a review needs and makes the transitions between them enforceable.
--
-- Three changes:
--   1. the CHECK gains pending_review and rejected
--   2. cc_context_blocks_append_only gains a transition table
--   3. cc_supersede_block separates the editor from the carried-over author
--
-- On the retirement word: this stays `deprecated`, not `archived`. CHY-32
-- describes the latter, but `deprecated` is what 002 chose and what the DELETE
-- route, the default list filter, the trigger HINT and the fixtures all say.
-- workflow_definitions uses `archived` for its own status; unifying the two
-- belongs to CHY-82, which rewrites that table anyway. One more word for one
-- more meaning is how deprecated/archived became confusing in the first place.

-- ── 1. The states ─────────────────────────────────────────────────────────────
--
-- The constraint from 002 is unnamed in the DDL, so PostgreSQL generated
-- `context_blocks_status_check`. IF EXISTS because that name is an assumption
-- about the generator, not a promise.

ALTER TABLE context_blocks DROP CONSTRAINT IF EXISTS context_blocks_status_check;
ALTER TABLE context_blocks ADD CONSTRAINT context_blocks_status_check
    CHECK (status IN ('draft', 'pending_review', 'active', 'rejected', 'deprecated'));

COMMENT ON COLUMN context_blocks.status IS
    'draft → pending_review → active, with rejected as the way back and '
    'deprecated as retirement. Only active blocks are composed (CHY-32).';

-- ── 2. The transition table ───────────────────────────────────────────────────
--
-- 012 left status deliberately unconstrained, on the grounds that who may
-- change it is an authorization question. That still holds — but *which*
-- changes exist at all is structural, and 012's own premise is protection
-- "auch nicht durch Bugs in der Anwendung". Without this, a bug can move a
-- block straight from draft to active and skip the review the whole ticket is
-- about, and nothing would notice.
--
-- Everything else in the function is unchanged; it is restated in full because
-- CREATE OR REPLACE takes the whole body.

CREATE OR REPLACE FUNCTION cc_context_blocks_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    -- COALESCE is load-bearing. current_setting(..., true) returns NULL when the
    -- setting was never assigned in this session, and NULL = 'on' is NULL, not
    -- false. Written as `v := current_setting(...) = 'on'` the variable becomes
    -- NULL, `IF NOT v` is then NULL rather than true, and the RAISE below is
    -- skipped — the guard silently lets the write through (found in CHY-60 by
    -- probing, not by reading).
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
                              'tags, author_ref, editor_ref, change_reason) to '
                              'publish a new version.';
        END IF;
        IF OLD.superseded_by_version IS NOT NULL THEN
            RAISE EXCEPTION 'block version % is already superseded', OLD.version
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
    END IF;

    -- Content is frozen once the row leaves draft.
    --
    -- Deliberately still `<> 'draft'` and not a set that includes 'rejected'.
    -- A rejected block is revised by PATCH, which supersedes it into a new
    -- draft version: "v1 rejected, v2 submitted, v2 approved" is better
    -- provenance than a v1 quietly overwritten after the reviewer read it. It
    -- also keeps this condition identical to the two in the application
    -- (api/v1/blocks.py, domain/blocks.py) rather than adding a third variant.
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

    -- The review workflow (CHY-32). A status may only move along an edge that
    -- exists. Staying put is always fine — most UPDATEs touch something else.
    IF OLD.status IS DISTINCT FROM NEW.status THEN
        IF NOT (
               (OLD.status = 'draft'          AND NEW.status = 'pending_review')
            OR (OLD.status = 'pending_review' AND NEW.status IN ('active', 'rejected', 'draft'))
            OR (OLD.status = 'rejected'       AND NEW.status = 'pending_review')
            OR (OLD.status = 'active'         AND NEW.status = 'deprecated')
            OR (OLD.status = 'deprecated'     AND NEW.status = 'active')
        ) THEN
            RAISE EXCEPTION
                'context_blocks: % → % is not a valid transition', OLD.status, NEW.status
                USING
                    ERRCODE = 'integrity_constraint_violation',
                    HINT    = 'draft → pending_review → active; rejected and draft '
                              'lead back to pending_review; active ⇄ deprecated. '
                              'A block cannot skip review. See CHY-32.';
        END IF;
    END IF;

    RETURN NEW;
END;
$$;

COMMENT ON FUNCTION cc_context_blocks_append_only() IS
    'Append-only guard plus review state machine for context_blocks (CHY-60, '
    'CHY-32): identity columns never change, content only while draft, '
    'superseded_by_version only via cc_supersede_block, status only along a '
    'valid transition, and DELETE/TRUNCATE never.';

-- ── 3. Editor and author are different people ─────────────────────────────────
--
-- Until now cc_supersede_block took one actor reference and used it twice: as
-- the new version's author_id and as the audit entry's actor_id. While
-- author_id was always NULL that was merely empty. The moment it carries real
-- values — CHY-33 makes it possible, and this migration's callers do it — every
-- edit by somebody else would be recorded as the work of the *previous*
-- author, in a table that has been append-only since 011 and cannot be
-- corrected afterwards.
--
-- The old five-argument signature is dropped rather than kept as an overload:
-- two functions differing only in what they attribute is exactly the trap this
-- is fixing.

DROP FUNCTION IF EXISTS cc_supersede_block(UUID, TEXT, JSONB, TEXT[], UUID, TEXT);

CREATE OR REPLACE FUNCTION cc_supersede_block(
    p_block_id      UUID,
    p_content       TEXT,
    p_metadata      JSONB   DEFAULT '{}'::jsonb,
    p_tags          TEXT[]  DEFAULT '{}'::text[],
    p_author_ref    UUID    DEFAULT NULL,
    p_editor_ref    UUID    DEFAULT NULL,
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
    -- at commit. Verified the hard way in CHY-60 -- the reverse order failed
    -- against cohereny_dev with "duplicate key value violates
    -- idx_blocks_one_active".
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
        p_content, p_metadata, p_tags,
        -- The author of the *line*, carried over unless the caller names one.
        COALESCE(p_author_ref, v_current.author_id),
        'draft'
    )
    RETURNING * INTO v_new;

    -- Written here rather than by the caller: the function is the only way to
    -- supersede, so an entry written inside it cannot be forgotten. Same
    -- transaction, so the history and the record of it stand or fall together.
    --
    -- actor_id is the *editor* — who performed this change. Falling back to the
    -- author only when no editor was named keeps the pre-CHY-32 callers honest
    -- rather than silently attributing to nobody.
    INSERT INTO audit_log (actor_id, action, target_ref, payload)
    VALUES (
        COALESCE(p_editor_ref, p_author_ref),
        'block.superseded',
        'block:' || p_block_id::text,
        jsonb_build_object(
            'from_version',  v_current.version,
            'to_version',    v_new.version,
            'from_status',   v_current.status,
            'author_id',     v_new.author_id,
            'change_reason', p_change_reason
        )
    );

    RETURN v_new;
END;
$$;

COMMENT ON FUNCTION cc_supersede_block(UUID, TEXT, JSONB, TEXT[], UUID, UUID, TEXT) IS
    'Publishes a new version of a context block and marks the previous one '
    'superseded, atomically, with an audit entry. p_author_ref owns the line, '
    'p_editor_ref made this change — conflating them would attribute every '
    'edit to the previous author (CHY-32). The only sanctioned way to change a '
    'block (ADR-009, CHY-60).';

-- Review queues ask for "everything waiting", and the transition endpoints look
-- up the current version of one block. Both are covered by the existing
-- idx_blocks_block_id and idx_blocks_one_active; this one serves the queue.
CREATE INDEX IF NOT EXISTS idx_blocks_status_current
    ON context_blocks (status, scope_id)
    WHERE superseded_by_version IS NULL;
