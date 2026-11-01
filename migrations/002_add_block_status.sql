-- Add status column to context_blocks for Phase 1 CRUD.
--
-- Phase 1 allows direct PATCH on draft blocks — the strict append-only
-- enforcement (no_update rule) is dropped here and will be reintroduced
-- via the merge-request review workflow in Phase 2.
--
-- status values:
--   draft      → newly created, editable
--   active     → promoted (Phase 2, not used in Phase 1)
--   deprecated → soft-deleted via DELETE /v1/context-blocks/{id}

ALTER TABLE context_blocks
    ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft', 'active', 'deprecated'));

-- Drop Phase 1 append-only guard — re-added in Phase 2 via review workflow
DROP RULE IF EXISTS no_update_context_blocks ON context_blocks;
