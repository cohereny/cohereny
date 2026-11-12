-- 026_audit_log_statement_time.sql — entries of one transaction keep their order (CHY-65)
--
-- audit_log.created_at defaulted to now(), which in PostgreSQL is the start of
-- the transaction, not the moment of the insert. Every entry a transaction
-- writes therefore carried the same timestamp, and the audit endpoint, which
-- orders by created_at DESC, id DESC, fell back to the id — a random UUID.
--
-- That did not matter while a transaction wrote one lifecycle entry. The
-- direct path of ADR-042 writes several: block.submitted and block.approved on
-- create, and block.superseded before both on an edit. On a scratch database
-- on 2026-09-29 the test for acceptance criterion 1 of CHY-65 read
-- "approved" before "submitted" — the trail was complete and its order was a
-- coin toss.
--
-- clock_timestamp() is the time of the statement. Entries of one transaction
-- are now ordered as they were written, including the one cc_supersede_block
-- inserts through this default. Entries of different transactions are ordered
-- as before; for them the two functions differ by the length of the
-- transaction, milliseconds.
--
-- Existing rows keep their timestamps. Only the default changes, which is DDL
-- and does not touch the rows the rules and triggers of 011 protect.
--
-- Not reversible in the sense that matters (CHY-113): setting the default back
-- is one statement, but entries written in between would keep their order.

ALTER TABLE audit_log ALTER COLUMN created_at SET DEFAULT clock_timestamp();

COMMENT ON COLUMN audit_log.created_at IS
    'When the entry was written — clock_timestamp(), not now(): entries of one '
    'transaction keep the order they were written in (CHY-65, migration 026).';
