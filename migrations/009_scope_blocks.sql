-- Migration 009: scope_path as ltree, per ADR-008 (CHY-59)
--
-- 001_initial created scope_nodes with `scope_path TEXT[]` and left a note that
-- ltree was the alternative. ADR-008 settled it on 2026-07-11: ltree, encoded as
-- `{node_type}_{uuid}` per label with dots as the hierarchy separator, e.g.
--
--     org_acme.team_it.project_study
--
-- Why the switch matters now: with TEXT[] every ancestor query is a hand-rolled
-- prefix comparison. ltree answers `scope_path <@ 'org_acme.team_it'` natively
-- and indexes it, which is the whole read path of the context repository
-- (CHY-61 /v1/compose). Doing it later means rewriting those queries twice.
--
-- Safe to run as a type change: scope_nodes, context_blocks, scope_memberships
-- and actors are empty on dev, test and prod (verified 2026-08-07). The USING
-- clause below still handles data, so the migration stays correct if that
-- changes before it is applied somewhere.

CREATE EXTENSION IF NOT EXISTS ltree;

-- The GIN index has to go before the type change: gin(ltree) has no operator
-- class, so ALTER COLUMN would fail while trying to rebuild it.
DROP INDEX IF EXISTS idx_scope_nodes_path;

-- TEXT[] → ltree.
--
-- The old sample data used colons (`org:codecrafter`), which ltree rejects --
-- labels are [A-Za-z0-9_] only. Everything outside that set collapses to an
-- underscore so the conversion cannot fail on legacy rows. UUID hyphens are
-- covered by the same rule, which is exactly what ADR-008 prescribes.
ALTER TABLE scope_nodes
    ALTER COLUMN scope_path TYPE ltree
    USING regexp_replace(
        array_to_string(scope_path, '.'),
        '[^A-Za-z0-9_.]', '_', 'g'
    )::ltree;

-- GiST, not GIN. The ticket said GIN, but for a single ltree column GiST is the
-- index that serves <@, @>, ~ and ?. GIN only has an operator class for ltree[]
-- (gin__ltree_ops), which is not what this column is.
CREATE INDEX IF NOT EXISTS idx_scope_nodes_path
    ON scope_nodes USING gist (scope_path);

-- Composition filters on block metadata (model, locale, privacy_impact) while
-- resolving a scope, so this pairs with the index above rather than standing
-- alone. `content` stays TEXT -- it holds prompt text, not queryable structure.
CREATE INDEX IF NOT EXISTS idx_blocks_metadata
    ON context_blocks USING gin (metadata jsonb_path_ops);

-- Encoding helper.
--
-- Kept in SQL rather than only in Python because both the API and any migration
-- or fixture needs to build these labels. Two implementations of one convention
-- drift, and a drifted scope label is invisible until a lookup silently returns
-- nothing.
--
-- IMMUTABLE so it can be used in generated columns and index expressions later.
CREATE OR REPLACE FUNCTION cc_scope_label(node_type TEXT, node_id UUID)
RETURNS TEXT
LANGUAGE sql
IMMUTABLE
STRICT
AS $$
    SELECT node_type || '_' || replace(node_id::TEXT, '-', '_');
$$;

COMMENT ON FUNCTION cc_scope_label(TEXT, UUID) IS
    'Builds one ltree label per ADR-008: {node_type}_{uuid with underscores}.';

COMMENT ON COLUMN scope_nodes.scope_path IS
    'Full hierarchy path as ltree, e.g. org_acme.team_it.project_study. '
    'Build labels with cc_scope_label(). Descendant lookup: scope_path <@ $1.';
