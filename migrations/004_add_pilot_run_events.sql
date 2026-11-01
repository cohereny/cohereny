-- Persist the SSE event log per pilot run for replay after run completion or
-- server restart. Previously events were kept in-memory only (ADR-013) and
-- discarded once the first SSE client drained the stream to EOF.
--
-- After this migration, _bg_run bulk-writes all events (with ts timestamps)
-- to this column in its finally block. stream_run replays from here for
-- non-live runs instead of synthesising a single synthetic done event.

ALTER TABLE pilot_runs ADD COLUMN IF NOT EXISTS events_json JSONB DEFAULT '[]';
