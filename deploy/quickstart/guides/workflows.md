# Workflows

A workflow is a graph of steps stored in Cohereny — not code, not a deployment. It
belongs to a scope, composes that scope's context, has models do the work by task type
and stops wherever a person has to decide. Every step it takes is recorded.

The quickstart's workflow *Draft a reply to a customer* has the three steps every
workflow needs:

```
compose (context_compose) → draft (llm_call) → review (human_review)
```

## Node types

| `node_type` | Does |
|---|---|
| `context_compose` | assembles the context of the workflow's scope ([Scopes and blocks](scopes-and-blocks.md)) |
| `llm_call` | asks a model; which one is decided by the routing rule for its `task_type` |
| `human_review` | stops the run until a person decides |
| `fork_gateway`, `join_gateway` | run branches in parallel and wait for them |
| `sub_workflow` | names another workflow; checked for cycles when stored, but not run yet — a run that reaches one ends as `failed` |

A workflow cannot be activated without a `context_compose`, an `llm_call` and a
`human_review`. A model's output never leaves a workflow without a person having seen
it.

## Data between nodes

A node sees the run's input under `run` and the output of each node directly before
it under that node's `node_ref`. Its `input_mapping` picks what it needs:

```json
{"system": "compose.prompt", "prompt": "run.message"}
```

For an `llm_call`, `system` becomes the model's instructions and `prompt` the
request. Here the composed context instructs and the customer's message is what gets
answered — the same split `/v1/complete` makes.

## Building one

```bash
WF=$(curl -s $API/v1/workflows -H "Authorization: Bearer $AUTHOR" -H 'Content-Type: application/json' \
  -d "{\"scope_id\": \"$PROJECT\", \"name\": \"Summarise a complaint\"}" |
  python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')

node() { curl -s $API/v1/workflows/$WF/nodes -H "Authorization: Bearer $AUTHOR" \
  -H 'Content-Type: application/json' -d "$1"; }
node '{"node_ref": "compose", "node_type": "context_compose"}'
node '{"node_ref": "summary", "node_type": "llm_call", "task_type": "summarize",
       "input_mapping": {"system": "compose.prompt", "prompt": "run.message"}}'
node '{"node_ref": "review", "node_type": "human_review", "review_role": "approver"}'

edge() { curl -s $API/v1/workflows/$WF/connections -H "Authorization: Bearer $AUTHOR" \
  -H 'Content-Type: application/json' -d "{\"source_node_ref\": \"$1\", \"target_node_ref\": \"$2\"}"; }
edge compose summary
edge summary review
```

Check it, then have a reviewer activate it:

```bash
curl -s -X POST $API/v1/workflows/$WF/validate -H "Authorization: Bearer $AUTHOR"
curl -s -X PUT $API/v1/workflows/$WF -H "Authorization: Bearer $REVIEW" \
  -H 'Content-Type: application/json' -d '{"status": "active"}'
```

`validate` lists what is wrong — a missing mandatory node, a loop without a limit, a
path from a model call that bypasses the review — and activation refuses the same list. A draft cannot be
run.

## Running one

`POST /v1/workflows/{id}/runs` with `{"input_data": {...}}` runs the workflow as far
as it goes and answers with where it stopped: `awaiting_oversight` at a review,
`complete`, or `failed` with the reason. Then:

- Who sees a run: whoever started it, members of the workflow's scope and of the
  scopes above (`POST /v1/scopes/{id}/members`), and `admin`. Everyone else gets 404.
- `GET /v1/workflows/runs/{run_id}/nodes` — what each step produced, in order; for an
  `llm_call` the answer under `output.content`. **Read this before deciding.**
- `POST /v1/workflows/runs/{run_id}/resolve` — the same with the names in place of the
  placeholders (below), for whoever started the run and, from `reviewer` up, whoever sees
  it. Every call is audited.
- `POST /v1/workflows/runs/{run_id}/oversight` with `{"decision": "approved"}` or
  `"rejected"`, and a `reason` — needs the `reviewer` role. A rejection ends the run.
- `POST /v1/workflows/runs/{run_id}/cancel`, optionally with `{"reason": "…"}` — stops a
  run that has not finished, for whoever started it and, from `reviewer` up, whoever sees
  it. The run ends as `cancelled`, not as a rejection.
- `GET /v1/audit-log?target=workflow_run:<run_id>` — the record of it.

Names that must not reach the model go into the input under `identifiers_to_redact`,
by category: `{"input_data": {"message": "…", "identifiers_to_redact": {"PERSON":
["Erika Mustermann"]}}}`. E-mail addresses, phone numbers, IBANs and similar patterns
are replaced without being listed. The run is stored with placeholders — its input,
every answer, the reason given at the gate — so reading it shows `{{PERSON_1}}`; the
list itself is not stored. `output.redactions` on each `llm_call` counts what was
replaced, and `resolve` puts the names back for whoever may see them.

## Reviews

`review_role` on a `human_review` node says how many people must agree:

- `approver` — one reviewer.
- `four_eyes` — two different reviewers; the same person approving twice is refused.

`review_timeout` (for example `"P2D"`) and `on_timeout` (`reject` or `escalate`) are stored
with the gate, and an `on_timeout` that approves is refused. Nothing acts on them yet: a run
nobody decides waits at the gate until someone does, or stops it
(`POST /v1/workflows/runs/{run_id}/cancel`).

## Branches

A connection can carry a condition on the data the node before it produced, so a run
takes one path or another:

```json
{"source_node_ref": "classify", "target_node_ref": "escalate",
 "condition": {"field": "classify.content", "op": "contains", "value": "urgent"}}
```

Operators: `eq`, `ne`, `lt`, `lte`, `gt`, `gte`, `in`, `contains`, `exists`; combined
with `all`, `any` and `not`. An edge back to an earlier node is a loop and needs a
`loop_limit`.
