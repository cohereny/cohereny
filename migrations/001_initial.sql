-- Extension: ltree für scope_path (optional, alternativ text[])
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Actors (Identity: verweist auf Authentik IDP)
CREATE TABLE actors (
    id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    idp_sub       TEXT UNIQUE,           -- Authentik subject (NULL wenn anonymisiert)
    display_name  TEXT NOT NULL,
    role          TEXT NOT NULL CHECK (role IN ('admin', 'architect', 'reviewer', 'contributor', 'consumer')),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Scope-Hierarchie (dynamisch via scope_path)
CREATE TABLE scope_nodes (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    scope_path  TEXT[] NOT NULL,         -- z.B. ['org:codecrafter', 'team:ki', 'project:study']
    node_type   TEXT NOT NULL CHECK (node_type IN ('org', 'team', 'workspace', 'project', 'sub_project', 'personal')),
    name        TEXT NOT NULL,
    description TEXT,
    parent_id   UUID REFERENCES scope_nodes(id),
    created_by  UUID REFERENCES actors(id),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_scope_nodes_path ON scope_nodes USING gin(scope_path);

-- Scope-Mitgliedschaften
CREATE TABLE scope_memberships (
    id         UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    scope_id   UUID NOT NULL REFERENCES scope_nodes(id),
    actor_id   UUID NOT NULL REFERENCES actors(id),
    role       TEXT NOT NULL CHECK (role IN ('admin', 'architect', 'reviewer', 'contributor', 'consumer')),
    granted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    granted_by UUID REFERENCES actors(id),
    UNIQUE (scope_id, actor_id)
);

-- Kontext-Bausteine (Append-Only, versioniert)
CREATE TABLE context_blocks (
    id                    UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    block_id              UUID NOT NULL,                -- stabiler Bezeichner über alle Versionen
    version               INTEGER NOT NULL DEFAULT 1,
    superseded_by_version INTEGER,                      -- NULL = aktuell | n = ersetzt durch Version n
    scope_id              UUID NOT NULL REFERENCES scope_nodes(id),
    block_type            TEXT NOT NULL CHECK (block_type IN (
                              'system_prompt', 'user_context', 'tool_schema',
                              'example_io', 'calibration_anchor'
                          )),
    content               TEXT NOT NULL,
    metadata              JSONB NOT NULL DEFAULT '{}',
    tags                  TEXT[] NOT NULL DEFAULT '{}',
    author_id             UUID REFERENCES actors(id),
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX idx_blocks_one_active ON context_blocks (block_id) WHERE superseded_by_version IS NULL;
CREATE INDEX idx_blocks_scope ON context_blocks(scope_id);
CREATE INDEX idx_blocks_block_id ON context_blocks(block_id);
CREATE INDEX idx_blocks_tags ON context_blocks USING gin(tags);

-- Verhindert UPDATE/DELETE auf context_blocks (Append-Only Enforcement)
CREATE OR REPLACE RULE no_update_context_blocks AS
    ON UPDATE TO context_blocks DO INSTEAD NOTHING;

CREATE OR REPLACE RULE no_delete_context_blocks AS
    ON DELETE TO context_blocks DO INSTEAD NOTHING;

-- Workflow-Definitionen (versioniert)
CREATE TABLE workflow_definitions (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    scope_id    UUID NOT NULL REFERENCES scope_nodes(id),
    name        TEXT NOT NULL,
    version     INTEGER NOT NULL DEFAULT 1,
    status      TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'active', 'archived')),
    created_by  UUID REFERENCES actors(id),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Workflow-Nodes (DAG-Knoten)
CREATE TABLE workflow_nodes (
    id             UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    workflow_id    UUID NOT NULL REFERENCES workflow_definitions(id),
    node_ref       TEXT NOT NULL,          -- eindeutig pro Workflow
    node_type      TEXT NOT NULL CHECK (node_type IN (
                       'context_compose', 'llm_call', 'tool_call',
                       'fork_gateway', 'join_gateway', 'human_review'
                   )),
    join_policy    TEXT CHECK (join_policy IN ('wait_all', 'wait_any', 'first_wins')),
    config         JSONB NOT NULL DEFAULT '{}',
    UNIQUE (workflow_id, node_ref)
);

-- Workflow-Verbindungen (DAG-Kanten)
CREATE TABLE workflow_connections (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    workflow_id     UUID NOT NULL REFERENCES workflow_definitions(id),
    source_node_id  UUID NOT NULL REFERENCES workflow_nodes(id),
    target_node_id  UUID NOT NULL REFERENCES workflow_nodes(id),
    condition       TEXT,                  -- optionaler Guard-Ausdruck
    execution_mode  TEXT NOT NULL DEFAULT 'sequential' CHECK (execution_mode IN ('sequential', 'parallel'))
);

-- Feedback-Events
CREATE TABLE feedback_events (
    id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    execution_id  TEXT NOT NULL,           -- Langfuse Trace ID
    node_ref      TEXT NOT NULL,
    workflow_id   UUID REFERENCES workflow_definitions(id),
    actor_id      UUID REFERENCES actors(id),
    rating        SMALLINT CHECK (rating BETWEEN 1 AND 5),
    comment       TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Learning-Proposals (aus Feedback oder Benchmark generiert, immer Human-Approval)
CREATE TABLE learning_proposals (
    id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    source_type   TEXT NOT NULL CHECK (source_type IN ('feedback', 'benchmark')),
    source_id     UUID NOT NULL,
    proposed_diff JSONB NOT NULL,
    status        TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected')),
    reviewed_by   UUID REFERENCES actors(id),
    reviewed_at   TIMESTAMPTZ,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Nightly Benchmark Reports (aggregiert, Details in Langfuse)
CREATE TABLE benchmark_reports (
    id                UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    workflow_id       UUID REFERENCES workflow_definitions(id),
    run_date          DATE NOT NULL,
    status            TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'regression_flagged')),
    summary           JSONB NOT NULL DEFAULT '{}',
    langfuse_trace_id TEXT,
    approved_by       UUID REFERENCES actors(id),
    approved_at       TIMESTAMPTZ,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Merge Requests (Context Blocks + Workflow Changes)
CREATE TABLE merge_requests (
    id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    title        TEXT NOT NULL,
    description  TEXT,
    status       TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'approved', 'rejected', 'merged')),
    author_id    UUID REFERENCES actors(id),
    scope_id     UUID REFERENCES scope_nodes(id),
    diff         JSONB NOT NULL DEFAULT '{}',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at  TIMESTAMPTZ
);

CREATE TABLE merge_request_comments (
    id               UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    merge_request_id UUID NOT NULL REFERENCES merge_requests(id),
    actor_id         UUID REFERENCES actors(id),
    body             TEXT NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Audit Log (unveränderlich)
CREATE TABLE audit_log (
    id         UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    actor_id   UUID REFERENCES actors(id),
    action     TEXT NOT NULL,
    target_ref TEXT,
    payload    JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE OR REPLACE RULE no_update_audit_log AS
    ON UPDATE TO audit_log DO INSTEAD NOTHING;

CREATE OR REPLACE RULE no_delete_audit_log AS
    ON DELETE TO audit_log DO INSTEAD NOTHING;
