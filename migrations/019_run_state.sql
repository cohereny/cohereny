-- Migration 019: what a run consumed (CHY-127)
--
-- The state machine works on tokens: completing a node places one on each
-- edge it takes, and a node fires by consuming the tokens that armed it. The
-- run state is rebuilt from the database on every advance rather than held in
-- memory, because a run that lives only in a process cannot be resumed by
-- another instance and cannot be resumed at all after a restart (ADR-028).
--
-- Rebuilding needs both halves of the sum:
--
--     tokens on edge E  =  times E was traversed  −  times E was consumed
--
-- Migration 018 stores the first half in `loop_counters.traversals`. The second
-- half was missing, so the tokens could not be reconstructed at all.
--
-- Worth naming plainly: this is the risk the scope decision for CHY-82 wrote
-- down. The state tables were built one ticket ahead of the code that reads
-- them, and the note warned that a schema designed without its reader would
-- cost one migration more rather than one less. One migration more, as
-- predicted.
--
-- The tables hold no rows anywhere — nothing writes them yet, the runner is
-- being built now.

ALTER TABLE node_executions
    ADD COLUMN IF NOT EXISTS consumed_edges UUID[] NOT NULL DEFAULT '{}';

COMMENT ON COLUMN node_executions.consumed_edges IS
    'The connections whose tokens this execution used up. Together with '
    'loop_counters.traversals this is what the token state is rebuilt from '
    '(CHY-127).';

-- Why an array on the execution and not a table of its own:
--
-- A token is not an entity with a life of its own. It exists between one
-- node completing and the next one firing, and the only question ever asked
-- about it is "how many are on this edge right now". Answering that from a sum
-- over two columns needs no extra join, no extra write path, and — the reason
-- that decides it — no way for the two to disagree. A separate table would be
-- a second place where the run's position is recorded, and the position of a
-- run is exactly the thing that must have one.
--
-- The array also carries the `first_wins` case without a special rule. That
-- policy consumes incoming edges that were never traversed, so the count can
-- exceed the traversals and the token count goes to zero rather than negative.
-- When the straggler finally arrives, its traversal is cancelled by the
-- consumption that was already recorded — which is precisely what "the
-- gateway has already decided" means.

CREATE INDEX IF NOT EXISTS idx_node_executions_consumed
    ON node_executions USING gin (consumed_edges);
