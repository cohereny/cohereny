"""
contexts/orchestration/ — workflow definitions and, later, the runner
(ADR-030, CHY-107).

Owns: workflow_definitions, workflow_nodes, workflow_connections
Its own language: workflow, node, run

    schema.py    transport shapes — from domain/workflow.py

Started as a skeleton: when the context was cut, all ten routes in
api/v1/workflows.py answered 501 and no code touched the three tables. The
workflow runner of ADR-028 has filled it since.

Why this is a context of its own, even though a workflow definition is
versioned, release-gated content and could have lived in `repository`: the
runner is the most plausible future standalone service, and a context that will
later be pulled out is better one from the start (ADR-030 point 4).

Release and versioning are not rebuilt here. Orchestration uses the published
interface of the repository context — two release mechanisms would be two
truths about what is released, which is the same reason `governance` does not
build its own.
"""
