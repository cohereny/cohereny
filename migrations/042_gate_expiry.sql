-- 042_gate_expiry.sql — a gate nobody decides ends as `expired` (CHY-254, ADR-050)
--
-- `review_timeout` and `on_timeout` have been stored since 018 and read by
-- nothing: a run at a gate nobody decided waited for good, and kept its content
-- past every period, because the purge touches only runs that are over.
--
-- From here on a command started by the operator's timer (`cc oversight
-- expire`) looks at waiting gates. A gate past its `review_timeout` either
-- escalates — along an edge to a further gate, if `on_timeout` says so — or
-- ends the run as `expired`; a gate without a time limit ends it as `expired`
-- once the content period has passed since the run reached it.
--
-- `expired` is its own word for its own fact: nobody decided. Not `rejected`,
-- which records a person saying no, and not `cancelled`, which records a person
-- stopping the run (CHY-200). The audit entry for it names no person, because
-- none acted.
--
-- Free of data migration: no run and no execution is in the new state.

-- Without IF EXISTS, as in 038 and 041: a different name would have to fail
-- here rather than leave the old list in place beside the new one.
ALTER TABLE workflow_runs DROP CONSTRAINT workflow_runs_status_check;
ALTER TABLE workflow_runs ADD CONSTRAINT workflow_runs_status_check
    CHECK (status IN ('pending', 'running', 'awaiting_oversight', 'awaiting_documents',
                      'complete', 'failed', 'rejected', 'cancelled', 'expired'));

ALTER TABLE node_executions DROP CONSTRAINT node_executions_status_check;
ALTER TABLE node_executions ADD CONSTRAINT node_executions_status_check
    CHECK (status IN ('pending', 'running', 'complete', 'failed', 'skipped',
                      'cancelled', 'expired'));

COMMENT ON COLUMN workflow_runs.status IS
    '[awaiting_documents →] running ⇄ awaiting_oversight → complete | failed | '
    'rejected | cancelled | expired. cancelled: a person stopped the run (Art. '
    '14(4)(e), CHY-200). expired: nobody decided at a gate before its time '
    'limit or the content period ran out (CHY-254, ADR-050).';
