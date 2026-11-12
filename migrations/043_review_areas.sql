-- 043_review_areas.sql — who votes at a gate, by area (CHY-180, ADR-050)
--
-- four_eyes (027) makes sure two different people approve. Which part of the
-- organisation they come from, nothing checked: for a credit decision both
-- could come from the front office, where the MaRisk ask for one vote from the
-- front office and one from the back office, and for differing votes a higher
-- authority (BTO 1.1 Tz. 2 and 6). Listing 10.2-1 of the book sketched it and
-- ran it against PostgreSQL; this is that sketch, generalised.
--
-- Areas are data, not code: per scope a list of areas (front office, back
-- office, credit committee — names the organisation chooses) and who belongs
-- to each. An area applies to the scope it is defined on and every scope below,
-- like a membership. A gate names its areas in `review_areas`.
--
-- Three lines carry the rule, all in the database rather than only in Python,
-- because a vote that reaches this table by another path must be refused too:
--
--   UNIQUE (execution_id, area_id)          one vote per area
--   PRIMARY KEY (execution_id, actor_id)    one person, one vote (027) — so
--                                           nobody votes for two areas
--   FOREIGN KEY (area_id, actor_id)         a vote needs membership
--
-- And a fourth, because membership can be revoked (Thomas, 09.11.2026):
-- somebody who moves from the front office to the back office must lose the
-- front-office vote at once, and a lock at the identity provider does not do
-- that — the person keeps working. A revoked membership keeps its row, with
-- the date, so a vote cast before stays provable; the trigger refuses a new
-- vote under it. A foreign key cannot say "current", which is why it is a
-- trigger and not a constraint.
--
-- A vote without an area (area_id NULL) is a vote at a gate without areas,
-- exactly as before: a unique key and a foreign key with a NULL column do not
-- apply, and the trigger lets it pass.
--
-- No index beyond the keys: neither table is ever deleted from, and no query
-- asks by member alone (scripts/fk_indexes.py, CHY-112).
--
-- Free of data migration: no area exists, and every vote so far has none.

CREATE TABLE review_areas (
    id         UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    scope_id   UUID        NOT NULL REFERENCES scope_nodes (id),
    name       TEXT        NOT NULL CHECK (name ~ '^[a-z][a-z0-9_]{0,62}$'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by UUID        REFERENCES actors (id),
    UNIQUE (scope_id, name)
);

COMMENT ON TABLE review_areas IS
    'The areas of a scope that vote at a gate (CHY-180, ADR-050) — front office, '
    'back office, credit committee. Apply to the scope and every scope below.';

CREATE TABLE review_area_members (
    area_id    UUID        NOT NULL REFERENCES review_areas (id),
    actor_id   UUID        NOT NULL REFERENCES actors (id),
    granted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    granted_by UUID        REFERENCES actors (id),
    revoked_at TIMESTAMPTZ,
    revoked_by UUID        REFERENCES actors (id),
    PRIMARY KEY (area_id, actor_id),
    CHECK (revoked_at IS NULL OR revoked_at >= granted_at)
);

COMMENT ON TABLE review_area_members IS
    'Who belongs to which area. Never deleted: a revoked membership keeps its row '
    'and its date, so a vote cast under it stays provable (Thomas, 09.11.2026). '
    'Granted again, the row is current again; the audit log keeps each change.';

ALTER TABLE review_votes ADD COLUMN area_id UUID;

ALTER TABLE review_votes ADD CONSTRAINT review_votes_one_per_area
    UNIQUE (execution_id, area_id);

ALTER TABLE review_votes ADD CONSTRAINT review_votes_area_membership
    FOREIGN KEY (area_id, actor_id) REFERENCES review_area_members (area_id, actor_id);

COMMENT ON COLUMN review_votes.area_id IS
    'The area this vote is cast for, at a gate with review_areas (CHY-180). '
    'NULL at a gate without areas.';

CREATE OR REPLACE FUNCTION review_vote_needs_current_membership() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.area_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM review_area_members m
        WHERE m.area_id = NEW.area_id AND m.actor_id = NEW.actor_id
          AND m.revoked_at IS NULL
    ) THEN
        RAISE EXCEPTION 'actor % is not a current member of area %', NEW.actor_id, NEW.area_id
            USING ERRCODE = 'foreign_key_violation',
                  CONSTRAINT = 'review_votes_area_membership';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER review_votes_current_membership
    BEFORE INSERT OR UPDATE OF area_id, actor_id ON review_votes
    FOR EACH ROW EXECUTE FUNCTION review_vote_needs_current_membership();

-- The gate names its areas. Names, not ids: a definition is written by people
-- and released by a reviewer, and a reviewer reads `marktfolge`, not a UUID.
-- Resolved along the workflow's scope chain, nearest first, at activation and
-- at every vote. The element check is a string match over the joined array —
-- a CHECK may not hold a subquery — and NULL elements are refused separately,
-- because array_to_string skips them.
ALTER TABLE workflow_nodes ADD COLUMN review_areas TEXT[];

ALTER TABLE workflow_nodes ADD CONSTRAINT workflow_nodes_review_areas_gate
    CHECK (review_areas IS NULL
           OR (node_type = 'human_review'
               AND cardinality(review_areas) BETWEEN 1 AND 16
               AND array_position(review_areas, NULL) IS NULL
               AND array_to_string(review_areas, ',')
                   ~ '^[a-z][a-z0-9_]{0,62}(,[a-z][a-z0-9_]{0,62})*$'));

COMMENT ON COLUMN workflow_nodes.review_areas IS
    'human_review only: the areas that each cast one vote (CHY-180, ADR-050). All '
    'approve → released; all reject → rejected; votes differ → escalated along '
    'the escalation edge. Two or more areas count as four_eyes.';

-- The application maintains areas and memberships; it never deletes either.
-- UPDATE on the memberships is for revoking and granting again.
GRANT SELECT, INSERT         ON review_areas        TO cohereny_app;
GRANT SELECT, INSERT, UPDATE ON review_area_members TO cohereny_app;
