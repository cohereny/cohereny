-- 030_retention_policies.sql — how long run data is kept is configuration, not code (CHY-17, ADR-046)
--
-- Until now nothing expired. A grep for retention, purge or expires_at over the
-- source tree found only Redis cache TTLs, and a cache TTL frees memory, it
-- does not discharge an obligation (comment on CHY-17, 08.09.2026). The run
-- tables are the richest store in the system and the only one that grows with
-- every use.
--
-- Decided on 2026-11-05 (Thomas):
--
-- 1. The content of a run expires before its trace. `run_content` is what went
--    in and came out — inputs, outputs, free text; it is cleared. `run_trace`
--    is the row itself — who started what, which model answered, who decided
--    at the gate; it is deleted later. Art. 26(6) of the AI Act asks deployers
--    of high-risk systems to keep the logs for at least six months.
-- 2. Periods are configuration. They differ by scenario — a credit process
--    keeps longer than a study aid — so they are rows here, changed through
--    the admin API and audited, readable by anyone supervising the
--    installation. A row with `scope_id` NULL applies to the whole
--    installation; a row on a scope applies to everything below it until a
--    deeper scope sets its own. The nearest one wins, not the strictest:
--    longer is not stricter for retention, and neither is shorter.
-- 3. Which tables belong to which retention class is code (kernel/retention.py),
--    held against the migrations by a test. A new table comes with a
--    migration anyway; the test makes whoever adds it say how long it stays.
--
-- Only the two run classes accept a period here. The audit log, context blocks
-- and benchmark measurements refuse UPDATE and DELETE by trigger, and with the
-- application connected as superuser a way past them is CHY-85's to build.
-- Accounts leave through anonymization (ADR-016). A period nothing can act on
-- would be a promise the system does not keep.

CREATE TABLE IF NOT EXISTS retention_policies (
    retention_class TEXT        NOT NULL
        CHECK (retention_class IN ('run_content', 'run_trace')),
    scope_id        UUID        REFERENCES scope_nodes (id),
    -- NULL: kept without limit. A credit file may have to outlive any default.
    keep_days       INTEGER     CHECK (keep_days IS NULL OR keep_days >= 1),
    set_by          UUID        REFERENCES actors (id),
    set_at          TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    -- One row per class and scope, and exactly one installation-wide row per
    -- class: NULLS NOT DISTINCT makes two NULL scopes collide (PostgreSQL 15+).
    CONSTRAINT retention_policies_one_per_scope
        UNIQUE NULLS NOT DISTINCT (retention_class, scope_id)
);

COMMENT ON TABLE retention_policies IS
    'How long run data is kept, per retention class and scope (CHY-17, '
    'ADR-046). scope_id NULL is the installation-wide period; the nearest '
    'row on a scope chain wins. keep_days NULL keeps without limit.';

-- The defaults Cohereny ships. An operator changes them through the admin API;
-- a fresh installation does not start without any period at all.
INSERT INTO retention_policies (retention_class, scope_id, keep_days)
VALUES ('run_content', NULL, 30),
       ('run_trace',   NULL, 183)
ON CONFLICT ON CONSTRAINT retention_policies_one_per_scope DO NOTHING;
