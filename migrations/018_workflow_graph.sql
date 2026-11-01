-- Migration 018: the workflow graph, and the tables a runner needs (CHY-82)
--
-- Implements the model of arc42 §5.6 (CHY-81, ADR-026, ADR-028). The three
-- workflow tables have existed since 001_initial and describe a *DAG* — which
-- ADR-026 corrected: a workflow is a directed graph, cycles are allowed, and
-- every back edge carries an iteration limit.
--
-- On the number: arc42 §5.6 reserves 011 for this and notes that 010 is taken
-- by CHY-42. Both are taken by now — 010 by actor anonymisation, 011 by the
-- immutable audit log. Third time a number reserved in prose was not reserved.
-- Written here as 018.
--
-- (The §5.6 sentence is paraphrased rather than quoted. Comments are English
-- here, and a quoted German clause is both a language exception and a
-- codespell finding for a word that is spelled correctly.)
--
-- Checked at the host before touching a column type, rather than assumed:
-- workflow_definitions, workflow_nodes and workflow_connections hold **zero
-- rows** in dev, test and prod (2026-09-04). Nothing writes them — all ten
-- endpoints answer 501. The same check turned up a finding of its own: test is
-- nine migrations behind and prod fifteen (CHY-161 — first noted here as
-- CHY-128, a different ticket; both stages were rebuilt on 2026-09-24).

-- ── 1. workflow_definitions ───────────────────────────────────────────────────

ALTER TABLE workflow_definitions
    ADD COLUMN IF NOT EXISTS description           TEXT,
    ADD COLUMN IF NOT EXISTS superseded_by_version INTEGER,
    ADD COLUMN IF NOT EXISTS risk_classification   JSONB NOT NULL DEFAULT '{}';

-- `archived` becomes `deprecated` (CHY-95, finding B3).
--
-- context_blocks calls retirement `deprecated`, workflow_definitions called it
-- `archived`: two words for one thing, which cost a morning on 2026-08-13.
-- CHY-95 routed the unification here because this ticket rewrites the table
-- anyway — the moment a rename costs nothing.
--
-- `deprecated` wins and not the other way round: it is the older and the more
-- widely used spelling. The DELETE route, the default list filter, the trigger
-- HINT of migration 013 and the fixtures all say `deprecated`.
UPDATE workflow_definitions SET status = 'deprecated' WHERE status = 'archived';

ALTER TABLE workflow_definitions DROP CONSTRAINT IF EXISTS workflow_definitions_status_check;
ALTER TABLE workflow_definitions ADD CONSTRAINT workflow_definitions_status_check
    CHECK (status IN ('draft', 'active', 'deprecated'));

-- risk_classification is an object or nothing. Without this a caller could
-- store a list and every reader would fail somewhere far from the write —
-- the same guard migration 017 puts on model_map and params.
ALTER TABLE workflow_definitions DROP CONSTRAINT IF EXISTS workflow_definitions_risk_object;
ALTER TABLE workflow_definitions ADD CONSTRAINT workflow_definitions_risk_object
    CHECK (jsonb_typeof(risk_classification) = 'object');

COMMENT ON TABLE workflow_definitions IS
    'One row per workflow *version*. A workflow is a directed graph, not a '
    'DAG and not an ordered list (ADR-026).';
COMMENT ON COLUMN workflow_definitions.status IS
    'draft → active → deprecated. `deprecated`, not `archived`: one word per '
    'concept (CHY-95).';
COMMENT ON COLUMN workflow_definitions.risk_classification IS
    'risk_category, requires_human_oversight, data_categories, privacy_level '
    '(arc42 §5.6). Free-form on purpose — the EU AI Act categories are still '
    'moving, and a CHECK here would have to be migrated with them.';

-- ── 2. workflow_nodes ─────────────────────────────────────────────────────────

ALTER TABLE workflow_nodes
    ADD COLUMN IF NOT EXISTS name                    TEXT,
    ADD COLUMN IF NOT EXISTS description             TEXT,
    -- sub_workflow (arc42 §5.6, new in v2.7)
    ADD COLUMN IF NOT EXISTS target_workflow_id      UUID REFERENCES workflow_definitions(id),
    ADD COLUMN IF NOT EXISTS target_workflow_version INTEGER,
    ADD COLUMN IF NOT EXISTS input_mapping           JSONB NOT NULL DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS output_mapping          JSONB NOT NULL DEFAULT '{}',
    -- human_review (arc42 §5.6, new in v2.7)
    ADD COLUMN IF NOT EXISTS review_role             TEXT,
    ADD COLUMN IF NOT EXISTS review_timeout          INTERVAL,
    ADD COLUMN IF NOT EXISTS on_timeout              TEXT;

ALTER TABLE workflow_nodes DROP CONSTRAINT IF EXISTS workflow_nodes_node_type_check;
ALTER TABLE workflow_nodes ADD CONSTRAINT workflow_nodes_node_type_check
    CHECK (node_type IN (
        'context_compose', 'llm_call', 'tool_call',
        'fork_gateway', 'join_gateway', 'human_review',
        'sub_workflow'
    ));

ALTER TABLE workflow_nodes DROP CONSTRAINT IF EXISTS workflow_nodes_review_role_check;
ALTER TABLE workflow_nodes ADD CONSTRAINT workflow_nodes_review_role_check
    CHECK (review_role IS NULL OR review_role IN ('approver', 'four_eyes'));

-- `approve` is deliberately absent from this list, and that is the whole point
-- of the column.
--
-- A human oversight gate that approves on silence is not a gate (ADR-004). The
-- banking pilot fell back to `approved` twice — on timeout and on a missing
-- gate — which is CHY-83. Here the database refuses to store the value, so the
-- same mistake cannot be made by configuration.
ALTER TABLE workflow_nodes DROP CONSTRAINT IF EXISTS workflow_nodes_on_timeout_check;
ALTER TABLE workflow_nodes ADD CONSTRAINT workflow_nodes_on_timeout_check
    CHECK (on_timeout IS NULL OR on_timeout IN ('reject', 'escalate'));

-- A sub_workflow node without a target is a node that cannot run, and a target
-- without a pinned version is "always the newest" — which arc42 §5.6 rules out
-- explicitly: a parent must keep calling the child it was reviewed against.
ALTER TABLE workflow_nodes DROP CONSTRAINT IF EXISTS workflow_nodes_sub_workflow_complete;
ALTER TABLE workflow_nodes ADD CONSTRAINT workflow_nodes_sub_workflow_complete
    CHECK (
        node_type <> 'sub_workflow'
        OR (target_workflow_id IS NOT NULL AND target_workflow_version IS NOT NULL)
    );

-- The other direction: a target on a node that is not a sub_workflow reads as
-- configuration and does nothing. Refused rather than ignored — the same rule
-- as ADR-032 one layer down.
ALTER TABLE workflow_nodes DROP CONSTRAINT IF EXISTS workflow_nodes_target_only_on_sub;
ALTER TABLE workflow_nodes ADD CONSTRAINT workflow_nodes_target_only_on_sub
    CHECK (
        node_type = 'sub_workflow'
        OR (target_workflow_id IS NULL AND target_workflow_version IS NULL)
    );

COMMENT ON TABLE workflow_nodes IS
    'Nodes of the graph. 001_initial called them "DAG-Knoten"; that has been '
    'wrong since ADR-026 — cycles are allowed and carry a loop_limit.';
COMMENT ON COLUMN workflow_nodes.on_timeout IS
    'reject or escalate. Never approve: a gate that approves on silence is not '
    'a gate (ADR-004, CHY-83). The CHECK is what makes that unconfigurable.';
COMMENT ON COLUMN workflow_nodes.target_workflow_version IS
    'Pinned. A parent keeps calling the child version it was reviewed against '
    '(arc42 §5.6) — "always the newest" would change behaviour without review.';

-- ── 3. workflow_connections ───────────────────────────────────────────────────

ALTER TABLE workflow_connections
    ADD COLUMN IF NOT EXISTS label      TEXT,
    ADD COLUMN IF NOT EXISTS loop_limit INTEGER;

ALTER TABLE workflow_connections DROP CONSTRAINT IF EXISTS workflow_connections_loop_limit_check;
ALTER TABLE workflow_connections ADD CONSTRAINT workflow_connections_loop_limit_check
    CHECK (loop_limit IS NULL OR loop_limit > 0);

-- The obligation itself — every *back* edge carries a limit — is graph
-- analysis and cannot be a CHECK: whether an edge points backwards depends on
-- every other edge. It lives in the validator (contexts/orchestration/graph.py)
-- and is enforced when a workflow is activated.

COMMENT ON TABLE workflow_connections IS
    'Edges of the graph. Cycles are allowed (ADR-026); a back edge must carry '
    'loop_limit, which the validator enforces because a CHECK cannot see the '
    'rest of the graph.';
COMMENT ON COLUMN workflow_connections.loop_limit IS
    'Iterations allowed on this edge per run (ADR-026). Per edge and per run, '
    'not per run: two independent loops must not limit one another, which is '
    'the reason a global recursion limit does not do.';
COMMENT ON COLUMN workflow_connections.condition IS
    'Guard expression; NULL runs unconditionally. Several outgoing conditional '
    'edges are an XOR branch — if none matches and there is no default edge, '
    'the run fails rather than quietly going nowhere (arc42 §5.6).';

-- ── 4. What the runner will write ─────────────────────────────────────────────
--
-- ADR-028 decided an own runner: the wait state of a human review is a row,
-- not a blocked thread, so it survives a restart and is visible to every API
-- instance. These are the tables that hold it. The runner itself is CHY-127.
--
-- The vocabulary is deliberately the same as pilot_runs.status: pending,
-- running, awaiting_oversight, complete, failed, rejected. One concept, one
-- word — a second name for "waiting for a human" would be the duplication
-- CHY-95 spent a ticket removing.

CREATE TABLE IF NOT EXISTS workflow_runs (
    id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    workflow_id  UUID NOT NULL REFERENCES workflow_definitions(id),
    -- Pinned like a sub_workflow target: which version actually ran is the
    -- question an audit asks, and workflow_id already names one version row —
    -- carrying the number as well means a reader need not resolve the FK to
    -- answer it.
    workflow_version INTEGER NOT NULL,
    status       TEXT NOT NULL DEFAULT 'pending'
                 CHECK (status IN ('pending', 'running', 'awaiting_oversight',
                                   'complete', 'failed', 'rejected')),
    actor_id     UUID REFERENCES actors(id),
    input_data   JSONB NOT NULL DEFAULT '{}',
    result       JSONB,
    error_message TEXT,
    started_at   TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- A sub-workflow run is a run of its own with its own record, not a
    -- subgraph of the parent (ADR-028 point 2). This is what links the two
    -- without merging them.
    parent_run_id UUID REFERENCES workflow_runs(id),
    parent_node_id UUID REFERENCES workflow_nodes(id)
);

CREATE INDEX IF NOT EXISTS idx_workflow_runs_status ON workflow_runs (status, created_at);
CREATE INDEX IF NOT EXISTS idx_workflow_runs_parent ON workflow_runs (parent_run_id)
    WHERE parent_run_id IS NOT NULL;

COMMENT ON TABLE workflow_runs IS
    'One row per execution of a workflow version (ADR-028). A sub-workflow '
    'call produces its own row with parent_run_id set — an independent run '
    'with its own audit trail, not a subgraph.';

CREATE TABLE IF NOT EXISTS node_executions (
    id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    run_id       UUID NOT NULL REFERENCES workflow_runs(id) ON DELETE CASCADE,
    node_id      UUID NOT NULL REFERENCES workflow_nodes(id),
    -- A node inside a loop is executed more than once, and each pass is its
    -- own record. Without this the second pass would either overwrite the
    -- first or violate a unique constraint — and the audit trail of an
    -- iterating workflow is exactly the sequence of passes.
    iteration    INTEGER NOT NULL DEFAULT 1 CHECK (iteration > 0),
    status       TEXT NOT NULL DEFAULT 'pending'
                 CHECK (status IN ('pending', 'running', 'complete',
                                   'failed', 'skipped')),
    input_data   JSONB,
    output_data  JSONB,
    -- Which model answered, per node and per pass. The transparency record
    -- needs it at this granularity: "the run used gemma4-e4b" is not an answer
    -- when a workflow routes different steps to different models.
    model_used   TEXT,
    error_message TEXT,
    started_at   TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    UNIQUE (run_id, node_id, iteration)
);

CREATE INDEX IF NOT EXISTS idx_node_executions_run ON node_executions (run_id, started_at);

COMMENT ON TABLE node_executions IS
    'One row per node per pass (ADR-028). `skipped` is a real outcome: the '
    'branch an XOR did not take is part of the record, and leaving it out '
    'would make the trace look like the branch never existed.';
COMMENT ON COLUMN node_executions.iteration IS
    'Which pass through this node, starting at 1. A loop produces several.';

CREATE TABLE IF NOT EXISTS loop_counters (
    run_id        UUID NOT NULL REFERENCES workflow_runs(id) ON DELETE CASCADE,
    connection_id UUID NOT NULL REFERENCES workflow_connections(id),
    traversals    INTEGER NOT NULL DEFAULT 0 CHECK (traversals >= 0),
    PRIMARY KEY (run_id, connection_id)
);

COMMENT ON TABLE loop_counters IS
    'Traversals of one edge in one run (ADR-026). Per edge, not per run: two '
    'independent loops must not limit one another, which is the reason '
    'LangGraph''s global recursion_limit was not enough (ADR-028 reason 1). '
    'A row exists only for edges that have actually been traversed.';
