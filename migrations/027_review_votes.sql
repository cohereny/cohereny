-- 027_review_votes.sql — every decision at a gate is a row, and four eyes are two people (CHY-176)
--
-- review_role has accepted 'four_eyes' since migration 018, and nothing read
-- it: record_oversight closed the gate on the first approval whatever the node
-- asked for. A value the system stores and does not act on sits in the
-- definition like a requirement that is met — the point ADR-032 makes about
-- evidence, one layer down.
--
-- One row per person and gate execution. The primary key is the rule: the same
-- person cannot decide twice at one gate, so a four-eyes gate cannot be passed
-- by one reviewer approving twice. Enforced here and not only in Python,
-- because two requests of one person in flight at once would otherwise both
-- find "no vote yet" and both count.
--
-- Keyed on the execution, not the run: a gate reached again on a later lap of
-- a loop is a new decision and starts with no votes.
--
-- No ON DELETE CASCADE. A vote is evidence that a person looked (Art. 14 AI
-- Act); deleting a run must not take it along silently. Same stance as
-- transparency_records in migration 015. The audit log keeps the history of
-- each decision as well — this table answers the one question the service asks
-- inside the transaction: how many different people have approved this gate?

CREATE TABLE IF NOT EXISTS review_votes (
    execution_id UUID        NOT NULL REFERENCES node_executions (id),
    actor_id     UUID        NOT NULL REFERENCES actors (id),
    decision     TEXT        NOT NULL CHECK (decision IN ('approved', 'rejected')),
    reason       TEXT,
    cast_at      TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (execution_id, actor_id)
);

COMMENT ON TABLE review_votes IS
    'One decision per person and gate execution (CHY-176). The primary key '
    'makes a second decision by the same person impossible, which is what '
    'lets a four_eyes gate count people rather than clicks.';
