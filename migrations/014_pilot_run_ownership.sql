-- Migration 014: who started a pilot run, and who approved it (CHY-88)
--
-- Migration 006 is headed "Human Oversight support (EU AI Act Art. 14)" and adds
-- exactly two columns: oversight_pending and oversight_decision. It records
-- *what* was decided. It does not record *who* decided, or *when*.
--
-- The endpoint was no better: submit_oversight takes `actor: CurrentUser` as a
-- dependency, authenticates the caller, and then never reads it. A row saying
-- "approved" with no name and no timestamp does not evidence human oversight —
-- it evidences that a field was set. The first question in an audit is "by
-- whom", and the system had no answer.
--
-- Same shape as CHY-83, where this very gate fell back to "approved" on timeout:
-- a control that looks sharp and cannot carry weight.
--
-- pilot_runs also had no owner at all. POST /v1/pilots/runs authenticated the
-- caller and dropped the identity on the floor.

ALTER TABLE pilot_runs
    ADD COLUMN IF NOT EXISTS actor_id     UUID REFERENCES actors(id),
    ADD COLUMN IF NOT EXISTS oversight_by UUID REFERENCES actors(id),
    ADD COLUMN IF NOT EXISTS oversight_at TIMESTAMPTZ;

COMMENT ON COLUMN pilot_runs.actor_id IS
    'Who started the run. NULL for runs created before CHY-88.';
COMMENT ON COLUMN pilot_runs.oversight_by IS
    'Who made the Art. 14 decision. References actors(id), not the OIDC subject, '
    'so a DSGVO erasure (CHY-34) reaches it.';
COMMENT ON COLUMN pilot_runs.oversight_at IS
    'When the Art. 14 decision was made. "Whether" is not enough — Art. 12(3) '
    'and any audit ask "when".';

-- ── Why a trigger and not a CHECK ─────────────────────────────────────────────
--
-- The obvious form is a CHECK that decision, by and at are set together, added
-- NOT VALID so the 11 existing dev rows with a decision and no approver are not
-- rejected. That would have been wrong, and quietly so:
--
--   NOT VALID skips *existing rows at validation time*. It does not exempt them
--   afterwards. Any later UPDATE to such a row — a status change, a result being
--   written, a cancellation — is checked against the constraint and fails,
--   because the row still has a decision without an approver. A migration meant
--   to record provenance would have made the legacy runs unwritable.
--
-- A trigger on the *transition* has none of that. It fires only when the
-- decision actually changes, which is exactly the moment the provenance must
-- exist. Legacy rows keep their gap and stay editable. Same reasoning as the
-- status machine in 013: constrain the change, not the state.
--
-- The gap this leaves, stated rather than hidden: a later UPDATE can null
-- oversight_by on a row whose decision is unchanged, and the trigger will not
-- object. That is acceptable because this column is a convenience, not the
-- record. The evidence lives in audit_log, which has been append-only and
-- protected against UPDATE, DELETE and TRUNCATE since 011 (CHY-31/CHY-60) —
-- tampering with the run row cannot remove the entry that says who decided.
-- Making pilot_runs append-only too would be the wrong trade: it is a mutable
-- work record, and every status change would need a new version.

CREATE OR REPLACE FUNCTION cc_pilot_oversight_needs_author() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.oversight_decision IS DISTINCT FROM OLD.oversight_decision
       AND NEW.oversight_decision IS NOT NULL
    THEN
        IF NEW.oversight_by IS NULL OR NEW.oversight_at IS NULL THEN
            RAISE EXCEPTION
                'pilot_runs: an oversight decision requires oversight_by and oversight_at'
                USING
                    ERRCODE = 'integrity_constraint_violation',
                    HINT    = 'EU AI Act Art. 14: recording that a human decided, '
                              'without recording which human and when, is not evidence '
                              'of oversight. See CHY-88.';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

COMMENT ON FUNCTION cc_pilot_oversight_needs_author() IS
    'Refuses an Art. 14 decision that names neither an approver nor a time '
    '(CHY-88). Fires on the transition, so pre-existing rows stay writable.';

DROP TRIGGER IF EXISTS trg_pilot_oversight_needs_author ON pilot_runs;
CREATE TRIGGER trg_pilot_oversight_needs_author
    BEFORE UPDATE ON pilot_runs
    FOR EACH ROW
    EXECUTE FUNCTION cc_pilot_oversight_needs_author();

-- "Which runs did this actor start" is the DSGVO question; the partial index
-- keeps the legacy NULLs out of it.
CREATE INDEX IF NOT EXISTS idx_pilot_runs_actor
    ON pilot_runs (actor_id)
    WHERE actor_id IS NOT NULL;
