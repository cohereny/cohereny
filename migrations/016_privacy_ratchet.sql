-- Migration 016: the privacy ratchet (CHY-62, ADR-017)
--
-- ADR-017 decided that privacy-relevant rules may only be tightened as they
-- move down the scope tree, never loosened: a project block must not be able
-- to switch cloud routing back on against a healthcare template that switched
-- it off. Until now that promise existed only in the reference designs
-- (§6.6/§6.7) and in prose.
--
-- Three things had to be settled before this could be written down.
--
-- 1. ADR-017 hangs the field on `routing_rule.action`. There is no
--    routing_rule -- `router/` is a YAML config and a RouterConfig class,
--    nothing that carries rules as rows. CHY-62 puts the field on blocks
--    instead, which is where governance statements actually live. The
--    consequence is not cosmetic: nothing is *derived* any more, a block
--    *declares* what it does.
--
-- 2. Composition does not override anything. as_prompt() concatenates every
--    block shallow to deep; the deeper one merely comes later. A contradiction
--    between two pieces of prose is not machine-detectable, so a declaration
--    is the only thing that makes the conflict checkable at all. This is why
--    governance_key is mandatory rather than nice to have: without it two
--    blocks cannot be known to be about the same rule.
--
-- 3. The key is a foreign key, not free text. A typo in a free-text key would
--    make two blocks look like they discuss different rules and would switch
--    the protection off silently -- the failure mode this whole estate keeps
--    running into. With a reference table a typo is a write error instead.
--    Adding a key costs a deliberate INSERT; for governance vocabulary that is
--    a feature.

-- ── 1. The vocabulary ─────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS governance_keys (
    key         TEXT PRIMARY KEY CHECK (key ~ '^[a-z][a-z0-9_]{2,39}$'),
    description TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE governance_keys IS
    'Controlled vocabulary for the privacy ratchet (CHY-62, ADR-017). Two '
    'blocks are compared only when they name the same key. A reference table '
    'rather than free text so a typo fails loudly instead of silently '
    'disabling the comparison.';

-- Seeded with the two rules that exist as decisions today. Everything else is
-- added when the rule it names is actually decided -- an empty vocabulary
-- entry invites blocks that claim to govern something nobody defined.
INSERT INTO governance_keys (key, description) VALUES
    ('cloud_routing',
     'Whether prompts from this scope may be sent to a cloud LLM. The worked '
     'example of ADR-017: a healthcare template forbids it, and no project '
     'below may allow it again.'),
    ('pii_redaction',
     'Whether personal data is removed before the prompt reaches a model '
     '(ADR-018). Restrictive means redaction is required.')
ON CONFLICT (key) DO NOTHING;

-- ── 2. What a block declares ──────────────────────────────────────────────────
--
-- All three are NULL/false for the overwhelming majority of blocks: only a
-- block that makes a governance claim takes part in the ratchet. That keeps
-- the check cheap and, more importantly, keeps the meaning of "restrictive"
-- from being diluted across blocks that never meant to say anything.

ALTER TABLE context_blocks
    ADD COLUMN IF NOT EXISTS governance_key TEXT REFERENCES governance_keys(key),
    ADD COLUMN IF NOT EXISTS privacy_impact TEXT,
    ADD COLUMN IF NOT EXISTS locked         BOOLEAN NOT NULL DEFAULT false;

ALTER TABLE context_blocks DROP CONSTRAINT IF EXISTS context_blocks_privacy_impact_check;
ALTER TABLE context_blocks ADD CONSTRAINT context_blocks_privacy_impact_check
    CHECK (privacy_impact IS NULL OR privacy_impact IN ('restrictive', 'permissive'));

-- The three columns are one statement or none of them.
--
-- A privacy_impact without a key cannot be compared to anything, so it would
-- read as governance and do nothing -- the worst of both. `locked` without a
-- key has the same problem: locked against what?
ALTER TABLE context_blocks DROP CONSTRAINT IF EXISTS context_blocks_governance_coherent;
ALTER TABLE context_blocks ADD CONSTRAINT context_blocks_governance_coherent
    CHECK (
        (governance_key IS NULL AND privacy_impact IS NULL AND locked = false)
     OR (governance_key IS NOT NULL AND privacy_impact IS NOT NULL)
    );

COMMENT ON COLUMN context_blocks.governance_key IS
    'Which rule this block speaks to. Two blocks are compared by the ratchet '
    'only when this matches. NULL = the block makes no governance claim.';
COMMENT ON COLUMN context_blocks.privacy_impact IS
    'restrictive tightens the rule, permissive loosens it. A permissive block '
    'may not become active below a restrictive ancestor with the same key '
    '(ADR-017).';
COMMENT ON COLUMN context_blocks.locked IS
    'No descendant scope may carry this governance_key at all -- not even to '
    'tighten it. A legitimate tightening belongs in this block''s own scope, '
    'or under a different key: one rule, one place.';

-- The ratchet asks, for a set of ancestor scopes, which governance blocks are
-- active there. Partial because governance blocks are the rare case.
CREATE INDEX IF NOT EXISTS idx_blocks_governance
    ON context_blocks (governance_key, scope_id)
    WHERE governance_key IS NOT NULL AND status = 'active';

-- ── 3. Governance is settled in draft and frozen afterwards ───────────────────
--
-- Without this the ratchet has a door in the back: a block reaches `active`
-- with locked = true, and a plain UPDATE sets it to false afterwards. The
-- three columns therefore join content/metadata/tags in the freeze that
-- migration 013 already applies outside `draft`.
--
-- That freeze *is* the governance path CHY-62 asks for. Changing a published
-- block's governance means superseding it -- which produces a new draft, and
-- publishing that draft needs the reviewer role in the block's own scope.
-- ADR-017 calls this "MR in den höheren Scope"; CHY-65 will give it the shape
-- of a merge request without changing who may do it.
--
-- Restated in full because CREATE OR REPLACE takes the whole body. Only the
-- content-freeze condition changed; everything else is migration 013 verbatim.

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

    -- Content and governance are frozen once the row leaves draft.
    --
    -- Deliberately still `<> 'draft'` and not a set that includes 'rejected'.
    -- A rejected block is revised by PATCH, which supersedes it into a new
    -- draft version: "v1 rejected, v2 submitted, v2 approved" is better
    -- provenance than a v1 quietly overwritten after the reviewer read it.
    --
    -- The three governance columns joined this list in 016. A reviewer
    -- approves a specific claim; letting the claim change afterwards would
    -- make the approval meaningless (CHY-62).
    IF OLD.status <> 'draft'
       AND (OLD.content        IS DISTINCT FROM NEW.content
         OR OLD.metadata       IS DISTINCT FROM NEW.metadata
         OR OLD.tags           IS DISTINCT FROM NEW.tags
         OR OLD.governance_key IS DISTINCT FROM NEW.governance_key
         OR OLD.privacy_impact IS DISTINCT FROM NEW.privacy_impact
         OR OLD.locked         IS DISTINCT FROM NEW.locked)
    THEN
        RAISE EXCEPTION
            'context_blocks: content and governance of a % block are immutable', OLD.status
            USING
                ERRCODE = 'integrity_constraint_violation',
                HINT    = 'Use cc_supersede_block to publish a new version '
                          'instead of editing a published one. Governance '
                          'attributes are changed on the resulting draft.';
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
    'CHY-32, CHY-62): identity columns never change, content and governance '
    'only while draft, superseded_by_version only via cc_supersede_block, '
    'status only along a valid transition, and DELETE/TRUNCATE never.';

-- ── 4. Superseding carries the claim over ─────────────────────────────────────
--
-- This is the back door the freeze above would otherwise leave open.
-- cc_supersede_block lists its INSERT columns explicitly, so the three new
-- ones would take their defaults: superseding a locked, restrictive block
-- would produce an unlocked successor with no claim at all -- silently, and
-- through the one function that is *supposed* to be the sanctioned path.
--
-- A new version therefore inherits the claim. Changing it is a second,
-- deliberate step on the resulting draft, where the freeze permits it and the
-- review that follows makes somebody sign for it.
--
-- The signature is unchanged on purpose. Adding p_privacy_impact and friends
-- would need a "not specified" sentinel to tell "carry over" from "clear",
-- and a sentinel that defaults to NULL is exactly how the silent unlock comes
-- back in through the parameter list.
--
-- Restated in full because CREATE OR REPLACE takes the whole body. Only the
-- INSERT column list and its VALUES changed.

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
        content, metadata, tags, author_id, status,
        -- Carried over, never defaulted (CHY-62). See the note above.
        governance_key, privacy_impact, locked
    ) VALUES (
        p_block_id, v_current.version + 1, v_current.scope_id, v_current.block_type,
        p_content, p_metadata, p_tags,
        -- The author of the *line*, carried over unless the caller names one.
        COALESCE(p_author_ref, v_current.author_id),
        'draft',
        v_current.governance_key, v_current.privacy_impact, v_current.locked
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
    'edit to the previous author (CHY-32). Governance attributes are carried '
    'over, never defaulted (CHY-62). The only sanctioned way to change a '
    'block (ADR-009, CHY-60).';
