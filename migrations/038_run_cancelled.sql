-- 038_run_cancelled.sql — a person can stop a workflow run (CHY-200)
--
-- The AI Act asks that whoever oversees a system can interrupt it "through a
-- 'stop' button or a similar procedure that allows the system to come to a
-- halt in a safe state" (Art. 14(4)(e)). Until now a workflow run could only
-- end by finishing, failing or being rejected at a gate; nothing let a person
-- stop it.
--
-- `cancelled` is a state of its own, for the run and for the node it stopped
-- at. It is neither a failure — nothing went wrong — nor a rejection: nobody
-- judged an output, and recording one would manufacture an Art. 14 decision
-- nobody took. The same separation that keeps `rejected` apart from `failed`
-- (CHY-83).
--
-- The constraints from 018 are unnamed in the DDL, so PostgreSQL generated
-- `<table>_status_check`. Dropped without IF EXISTS on purpose: if that name
-- were wrong, IF EXISTS would skip the drop, the old CHECK would stay beside
-- the new one and still refuse `cancelled` — a migration that reports success
-- and changes nothing. Failing here is the honest outcome.

ALTER TABLE workflow_runs DROP CONSTRAINT workflow_runs_status_check;
ALTER TABLE workflow_runs ADD CONSTRAINT workflow_runs_status_check
    CHECK (status IN ('pending', 'running', 'awaiting_oversight',
                      'complete', 'failed', 'rejected', 'cancelled'));

ALTER TABLE node_executions DROP CONSTRAINT node_executions_status_check;
ALTER TABLE node_executions ADD CONSTRAINT node_executions_status_check
    CHECK (status IN ('pending', 'running', 'complete', 'failed', 'skipped',
                      'cancelled'));

COMMENT ON COLUMN workflow_runs.status IS
    'pending → running ⇄ awaiting_oversight → complete | failed | rejected | '
    'cancelled. cancelled: a person stopped the run (Art. 14(4)(e), CHY-200) — '
    'neither a failure nor a rejection at a gate.';
