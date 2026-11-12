-- 041_run_documents.sql — documents of a run, and the node that hands one on (CHY-174, ADR-049)
--
-- A run took JSON only. The banking workflow needs documents — an annual
-- statement, a bank statement — and the bank statement must reach the draft of
-- the vote and nothing else (§ 37a BDSG from 20.11.2026, CHY-177 way 1). In the
-- run input it would reach every node: `build_input` hands each one the whole
-- input under `run` (CHY-131).
--
-- So documents do not go into the input. A run whose definition has `document`
-- nodes is created as `awaiting_documents`; each document arrives on its own
-- endpoint, is read once — PDF to text — scanned (core/ingress) and stored here
-- with the run's placeholders (CHY-234). With the last one the run starts. A
-- `document` node hands its document to its successors, fenced, and only to
-- them: who sees a document is decided by the edges.
--
-- Three typed columns on the node, by the rule of ADR-040 and held to the node
-- type in both directions as `function` is in 040. The limits narrow the
-- installation's ceiling (DOCUMENT_MAX_BYTES, DOCUMENT_MAX_PAGES); whether they
-- stay below it is checked at activation (rule
-- `document_limit_above_installation`) — the database does not see the
-- configuration.
--
-- Free of data migration: the columns and the table are new, no node is of the
-- new type and no run is in the new state.

ALTER TABLE workflow_nodes
    ADD COLUMN IF NOT EXISTS document_name      TEXT,
    ADD COLUMN IF NOT EXISTS document_max_bytes INTEGER,
    ADD COLUMN IF NOT EXISTS document_max_pages INTEGER;

-- Without IF EXISTS, as in 038 and 040: a different name would have to fail
-- here rather than leave the old list in place beside the new one.
ALTER TABLE workflow_nodes DROP CONSTRAINT workflow_nodes_node_type_check;
ALTER TABLE workflow_nodes ADD CONSTRAINT workflow_nodes_node_type_check
    CHECK (node_type IN (
        'context_compose', 'llm_call', 'tool_call',
        'fork_gateway', 'join_gateway', 'human_review',
        'sub_workflow', 'compute', 'document'
    ));

-- On any other node type they would be stored and never read.
ALTER TABLE workflow_nodes DROP CONSTRAINT IF EXISTS workflow_nodes_document_only_on_document;
ALTER TABLE workflow_nodes ADD CONSTRAINT workflow_nodes_document_only_on_document
    CHECK (node_type = 'document'
           OR (document_name IS NULL AND document_max_bytes IS NULL
               AND document_max_pages IS NULL));

-- A document node names its document, and the name is a path segment of the
-- upload endpoint: lower case, digits, underscores. IS NOT NULL spelled out: a
-- NULL name matches no pattern, the comparison is NULL, and a CHECK that is
-- NULL passes — the node would be stored without a document to wait for.
ALTER TABLE workflow_nodes DROP CONSTRAINT IF EXISTS workflow_nodes_document_names_document;
ALTER TABLE workflow_nodes ADD CONSTRAINT workflow_nodes_document_names_document
    CHECK (node_type <> 'document'
           OR (document_name IS NOT NULL AND document_name ~ '^[a-z][a-z0-9_]{0,62}$'));

-- A limit of 0 accepts nothing; NULL is how "the installation's" is spelled.
ALTER TABLE workflow_nodes DROP CONSTRAINT IF EXISTS workflow_nodes_document_limits_positive;
ALTER TABLE workflow_nodes ADD CONSTRAINT workflow_nodes_document_limits_positive
    CHECK ((document_max_bytes IS NULL OR document_max_bytes > 0)
           AND (document_max_pages IS NULL OR document_max_pages > 0));

-- Written into `config` they would be stored and ignored. Refused, as in 023, 039, 040.
ALTER TABLE workflow_nodes DROP CONSTRAINT IF EXISTS workflow_nodes_config_without_document;
ALTER TABLE workflow_nodes ADD CONSTRAINT workflow_nodes_config_without_document
    CHECK (NOT (config ? 'document_name') AND NOT (config ? 'document_max_bytes')
           AND NOT (config ? 'document_max_pages'));

-- One node per document and workflow. The node *is* the document, with one
-- limit; two steps that need it get two edges from it.
CREATE UNIQUE INDEX IF NOT EXISTS workflow_nodes_one_node_per_document
    ON workflow_nodes (workflow_id, document_name) WHERE document_name IS NOT NULL;

COMMENT ON COLUMN workflow_nodes.document_name IS
    'Only for document, and required there: the name the document is uploaded '
    'under (PUT /v1/workflows/runs/{run_id}/documents/{name}), unique per workflow.';
COMMENT ON COLUMN workflow_nodes.document_max_bytes IS
    'Only for document: the largest file this node accepts, in bytes. NULL is the '
    'installation''s ceiling; above it activation is refused (ADR-049).';
COMMENT ON COLUMN workflow_nodes.document_max_pages IS
    'Only for document: the most pages a PDF for this node may have. NULL is the '
    'installation''s ceiling.';

-- The run waits for its documents. Without IF EXISTS, as in 038.
ALTER TABLE workflow_runs DROP CONSTRAINT workflow_runs_status_check;
ALTER TABLE workflow_runs ADD CONSTRAINT workflow_runs_status_check
    CHECK (status IN ('pending', 'running', 'awaiting_oversight', 'awaiting_documents',
                      'complete', 'failed', 'rejected', 'cancelled'));

COMMENT ON COLUMN workflow_runs.status IS
    '[awaiting_documents →] running ⇄ awaiting_oversight → complete | failed | '
    'rejected | cancelled. awaiting_documents: the definition has document nodes '
    'and not every document has arrived; no node runs (ADR-049). cancelled: a '
    'person stopped the run (Art. 14(4)(e), CHY-200).';

-- What arrived, as text with the run's placeholders — never the file. The
-- findings are kinds and rules, never an excerpt: an excerpt is document text.
CREATE TABLE IF NOT EXISTS workflow_run_documents (
    run_id      UUID        NOT NULL REFERENCES workflow_runs (id) ON DELETE CASCADE,
    name        TEXT        NOT NULL,
    media_type  TEXT        NOT NULL
                CHECK (media_type IN ('application/pdf', 'text/plain')),
    bytes       INTEGER     NOT NULL CHECK (bytes > 0),
    -- PDF only; a text file has no pages.
    pages       INTEGER     CHECK (pages > 0),
    characters  INTEGER     NOT NULL CHECK (characters > 0),
    -- NULL once the run's content period has passed (ADR-046).
    text        TEXT,
    findings    JSONB       NOT NULL DEFAULT '[]',
    received_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (run_id, name),
    CONSTRAINT workflow_run_documents_pages_only_for_pdf
        CHECK ((media_type = 'application/pdf') = (pages IS NOT NULL)),
    CONSTRAINT workflow_run_documents_findings_is_array
        CHECK (jsonb_typeof(findings) = 'array')
);

COMMENT ON TABLE workflow_run_documents IS
    'Documents of a workflow run, as text with the run''s placeholders (CHY-174, '
    'ADR-049). Uploaded by the run''s starter while it awaits them; read only by '
    'the run''s document nodes. Text cleared with the run''s content, rows deleted '
    'with the run.';

GRANT SELECT, INSERT, UPDATE ON workflow_run_documents TO cohereny_app;

-- Visible to whoever sees the run, through the same policy as its steps (035)
-- and its mapping (036).
ALTER TABLE workflow_run_documents ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS workflow_run_documents_visible ON workflow_run_documents;
CREATE POLICY workflow_run_documents_visible ON workflow_run_documents FOR ALL TO cohereny_app
    USING (EXISTS (SELECT 1 FROM workflow_runs r WHERE r.id = workflow_run_documents.run_id))
    WITH CHECK (EXISTS (SELECT 1 FROM workflow_runs r WHERE r.id = workflow_run_documents.run_id));
