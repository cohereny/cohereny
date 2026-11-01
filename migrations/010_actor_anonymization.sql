-- Migration 010: explicit anonymization flag on actors, per ADR-025 (CHY-34)
--
-- CHY-34 originally called for an `ActorReference(actor_id, entity_type, entity_id,
-- role)` intermediary and justified it with the GDPR right to erasure. ADR-025
-- (2026-08-11) rejected that: eleven places already reference `actors(id)` directly,
-- and a polymorphic `entity_type`/`entity_id` pair cannot be covered by a foreign
-- key. For a system whose purpose is provable governance, the table recording who
-- did what would have been the only one without an integrity guarantee.
--
-- Erasure works without it. Anonymization clears the personal attributes and keeps
-- the row, so every existing foreign key stays valid and the audit trail stays
-- consistent: the record that an action happened survives, the person behind it
-- does not.
--
-- What was missing is a way to *tell*. Until now the only marker was
-- `idp_sub IS NULL`, which conflates two different states:
--
--   idp_sub IS NULL, is_anonymized = FALSE  → a local actor that never had an IdP
--                                             identity (service account, seed data)
--   idp_sub IS NULL, is_anonymized = TRUE   → a person who exercised Art. 17
--
-- Those need to be distinguishable. An erasure request must be provable after the
-- fact, and a service account must not look like a former colleague.
--
-- Backfill: FALSE for every existing row is correct — no anonymization has been
-- performed yet (the endpoint is a not_implemented placeholder as of this
-- migration), so no existing row can be a false negative.

ALTER TABLE actors
    ADD COLUMN IF NOT EXISTS is_anonymized  BOOLEAN     NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS anonymized_at  TIMESTAMPTZ;

-- The two columns must agree. Without this an anonymization could be recorded
-- without a timestamp, which is unusable as evidence of when the request was met
-- (GDPR Art. 12(3): one month).
ALTER TABLE actors DROP CONSTRAINT IF EXISTS actors_anonymized_consistent;
ALTER TABLE actors ADD CONSTRAINT actors_anonymized_consistent
    CHECK (
        (is_anonymized = FALSE AND anonymized_at IS NULL)
        OR
        (is_anonymized = TRUE  AND anonymized_at IS NOT NULL)
    );

-- An anonymized actor must not carry an IdP identity — that is the whole point.
-- Enforced here rather than trusted to the endpoint: this is the invariant the
-- erasure claim rests on, so it belongs where it cannot be bypassed.
ALTER TABLE actors DROP CONSTRAINT IF EXISTS actors_anonymized_has_no_idp_sub;
ALTER TABLE actors ADD CONSTRAINT actors_anonymized_has_no_idp_sub
    CHECK (is_anonymized = FALSE OR idp_sub IS NULL);

COMMENT ON COLUMN actors.is_anonymized IS
    'TRUE after a GDPR Art. 17 erasure. Distinguishes an erased person from a '
    'local actor that never had an IdP identity — both have idp_sub IS NULL.';
COMMENT ON COLUMN actors.anonymized_at IS
    'When the erasure was carried out. Evidence for the Art. 12(3) deadline.';
COMMENT ON COLUMN actors.idp_sub IS
    'Authentik subject. NULL for local actors and for anonymized ones — see '
    'is_anonymized to tell them apart.';

-- Partial index: erasure audits ask "which actors were anonymized, and when".
-- Partial because the overwhelming majority of rows are FALSE and never queried
-- this way.
CREATE INDEX IF NOT EXISTS idx_actors_anonymized
    ON actors (anonymized_at DESC)
    WHERE is_anonymized = TRUE;
