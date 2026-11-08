-- 037_oversight_record_basis.sql — the record of an oversight decision rests on Art. 12, not Art. 14 (CHY-152)
--
-- Migration 014 made a pilot run's oversight decision name who decided and
-- when, and gave the wrong article for it. Art. 14 of the AI Act asks that
-- human oversight be possible and effective. That the decision is recorded —
-- by whom, and when — follows from Art. 12(1) and (2) on record-keeping, (2)(c)
-- in particular, together with Art. 26(5) for the deployer. Art. 12(3)(d) names
-- the overseeing person explicitly, but only for Annex III point 1(a),
-- biometrics; 014 cited it for every run.
--
-- 014 stays as it is: migrations only go forward (CHY-113). This one replaces
-- the function with the corrected hint and rewrites the comments. The body is
-- 014's, unchanged, and so is the trigger: it still fires on the transition
-- only, and pre-existing rows stay writable.

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
                    HINT    = 'EU AI Act Art. 12: a record that a human decided, without '
                              'which human and when, is not a record of the oversight '
                              'Art. 14 asks for. See CHY-88, CHY-152.';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

COMMENT ON FUNCTION cc_pilot_oversight_needs_author() IS
    'Refuses an oversight decision that names neither an approver nor a time '
    '(CHY-88). The oversight is Art. 14, its record Art. 12 (CHY-152). Fires on '
    'the transition, so pre-existing rows stay writable.';

COMMENT ON COLUMN pilot_runs.oversight_by IS
    'Who made the oversight decision, recorded as Art. 12 of the AI Act requires '
    '(CHY-152). References actors(id), not the OIDC subject, so a DSGVO erasure '
    '(CHY-34) reaches it.';
COMMENT ON COLUMN pilot_runs.oversight_at IS
    'When the oversight decision was made. "Whether" is not enough — Art. 12(1) '
    'and (2) of the AI Act and any audit ask "when" (CHY-152).';
