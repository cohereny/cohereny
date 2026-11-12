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
| `llm_call` | asks a model; which one is decided by the routing rule for its `task_type`. With an `output_schema` its answer is checked ([below](#a-models-answer-as-data)) |
| `human_review` | stops the run until a person decides |
| `fork_gateway`, `join_gateway` | run branches in parallel and wait for them |
| `compute` | runs a calculation registered in the code, by name and version ([below](#computed-steps)) |
| `document` | hands one document of the run to the steps it has an edge to ([below](#documents)) |
| `sub_workflow` | names another workflow; checked for cycles when stored, but not run yet — a run that reaches one ends as `failed` |

A workflow cannot be activated without a `context_compose`, an `llm_call` and a
`human_review`. A model's output never leaves a workflow without a person having seen
it.

## Data between nodes

A node sees the run's input under `run` and the output of each node directly before
it under that node's `node_ref` — a document only that way, never under `run`. Its
`input_mapping` picks what it needs:

```json
{"system": "compose.prompt", "prompt": "run.message"}
```

For an `llm_call`, `system` becomes the model's instructions and `prompt` the
request. Here the composed context instructs and the customer's message is what gets
answered — the same split `/v1/complete` makes.

### Several inputs

A step that reads from several steps should say that it waits for all of them —
`"join_policy": "wait_all"`. Without it, it starts as soon as the first one has finished
and reads the others as missing. A dotted target collects several values into one
object:

```json
{"node_ref": "risk", "node_type": "llm_call", "task_type": "rate_risk",
 "join_policy": "wait_all",
 "input_mapping": {"system": "prompt_risk.prompt",
                   "prompt.figures": "ratios.data",
                   "prompt.findings": "anomalies.data.anomalies"}}
```

An object as the `prompt` reaches the model in sections, one heading per key: text as it
came — a document keeps its markers —, everything else as JSON.

## A prompt per step

A `context_compose` node can ask for blocks by tag. Only blocks that carry every tag it
names are composed:

```json
{"node_ref": "prompt_risk", "node_type": "context_compose",
 "context_requirements": {"block_types": ["system_prompt"], "tags": ["banking:risiko"]}}
```

So each model step gets its own instruction from the repository, reviewed like any
block, instead of one context for the whole workflow. The tag counts on the version that
would be composed, the newest active one: a version that dropped the tag is not composed
for it, even though an older one carried it. Without `tags` a node composes what it did
before; an empty list is refused.

[deploy/examples/banking](../../examples/banking/) is a whole workflow built this way: a
credit analysis with two documents, five model steps with a prompt each, two computed
steps and the vote of front and back office.

## A model's answer as data

An `llm_call` returns text under `content`. Give it an `output_schema` — a JSON Schema
of type `object` — and the answer is read as one JSON object, checked, and added to
the node's output as `data`:

```json
{"node_ref": "classify", "node_type": "llm_call", "task_type": "classify",
 "output_schema": {"type": "object", "required": ["risk_level"],
                   "properties": {"risk_level": {"enum": ["low", "high"]}}}}
```

A successor reads `classify.data.risk_level` through its `input_mapping`; an edge from
`classify` reads `data.risk_level` (see Branches). The gateway is asked for JSON, and
an answer wrapped in one ```` ```json ```` block is read; text around it is not.

An answer that is not one JSON object, or does not match, **fails the node and the
run**. There is no default to fall back to. The reason names where and which rule —
`at $.risk_level: enum ["low", "high"]` — never what the model wrote, since that can
identify someone.

Check structure, not identifiers: the answer is checked with placeholders in place
(`{{EMAIL_1}}`), so a pattern for an e-mail address or an IBAN would refuse it.

## Computed steps

Some steps need no model and must not have one: a ratio is computed, not estimated. A
`compute` node runs a pure function that is registered in the code, named by `function`
and a pinned `function_version`:

```json
{"node_ref": "ratios", "node_type": "compute",
 "function": "banking.ratios", "function_version": 1,
 "input_mapping": {"kpis": "extract_kpis.data"}}
```

The result is the node's output as `data`, like a checked answer, so an edge reads
`data.…` and a successor `ratios.data.…`. Registered today: `banking.ratios@1` (reads
`kpis`) and `banking.anomalies@1` (reads `ratios`).

A missing figure, a value that is not a number, or a denominator of 0 **fails the
step** — there is no 0 for a figure that was not there. A function the code does not
have refuses the activation (`unknown_function`). A changed calculation is a new
version, so a released workflow keeps computing what it was reviewed against.

## Documents

A run can take documents: a PDF or a text file. A `document` node names one, and may
accept less than the installation does:

```json
{"node_ref": "statement", "node_type": "document", "document_name": "kontoauszug",
 "document_max_pages": 20}
```

A workflow with document nodes starts as `awaiting_documents` — nothing runs yet. Whoever
started the run sends each document, the file as the body, its type as `Content-Type`:

```bash
curl -s -X PUT $API/v1/workflows/runs/$RUN/documents/kontoauszug \
  -H "Authorization: Bearer $AUTHOR" -H 'Content-Type: application/pdf' -T kontoauszug.pdf
```

The last one starts the run in that call, and the answer says where it got to.
`GET /v1/workflows/runs/{run_id}/documents` lists which have arrived.

- **Only the steps the node has an edge to see the document.** Its output is `text` —
  between markers that say it is foreign data —, `pages` and `findings`; a successor reads
  `statement.text`. Documents are not in the run input, so a step without such an edge
  does not get one. Give a bank statement a single edge, to the draft, and the score
  never sees it.
- **Read once, when it arrives.** A PDF becomes its text; the file is not kept. A PDF
  without a text layer — a scanned page — is refused: recognise the text first and send
  that. Encrypted PDFs are refused too.
- **Stored with the run's placeholders.** Names listed under `identifiers_to_redact` at
  the start are replaced in the documents as well.
- **Scanned for instructions.** What looks like an instruction to a model is counted by
  kind and rule, in the answer and in the audit entry `workflow.run_document_received`.
  It does not stop the run; the text reaches every step fenced.
- **Limits.** The installation takes at most `DOCUMENT_MAX_BYTES` (10 MB by default) and
  `DOCUMENT_MAX_PAGES` (200). A node may set lower limits with `document_max_bytes` and
  `document_max_pages`, never higher ones: activation refuses that
  (`document_limit_above_installation`).
- **Each document once, and only while the run waits.** To send another file, cancel the
  run and start a new one. A run whose documents never come can be cancelled; its content
  period clears it either way.

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
`complete`, or `failed` with the reason — or `awaiting_documents`, before any step, when
it has document nodes ([above](#documents)). Then:

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

- `sample` — nobody decides before the output goes on; a share of the passes is checked
  afterwards ([below](#checked-afterwards)). Only where the minimum allows it.
- `approver` — one reviewer.
- `four_eyes` — two different reviewers; the same person approving twice is refused.

### How strict a gate must be

A gate asks for at least what its workflow's data and risk ask for. The workflow carries a
`risk_class` (`POST /v1/workflows` with `"risk_class": "limited"`), its scope a data class,
and the stricter of the two is the minimum of every gate:

| Data class of the scope | Risk class of the workflow | Minimum |
|---|---|---|
| `public`, `internal` | `minimal` | `sample` — the gate as drawn |
| `confidential` | `limited` | `approver` |
| `critical`, or none | `high`, or none | `four_eyes` |

Nothing left out loosens anything: a scope without a data class counts as `critical`, a
workflow without a risk class as `high` — so an unclassified workflow asks for four eyes at
every gate. Two or more [areas](#areas) count as `four_eyes`.

- **Activation** refuses a gate below the minimum (`review_below_minimum`).
- **At run time** the level is fixed when the run reaches the gate: the stricter of the gate
  and the minimum *as it is then*. A scope classified stricter after the release makes its
  gates stricter at once; a run already waiting keeps the level it reached the gate with.
  Each time, `workflow.run_gate_reached` in the audit log says which level applied and why.
- **The higher authority after differing votes** decides as drawn — a credit committee with
  one vote under `critical`: the areas before it have looked. Not so after a timeout, where
  nobody did; a gate a timeout can reach has to meet the minimum itself.
- A gate with areas that falls below the minimum through a reclassification is not decided
  until the workflow is released again with a stricter gate: adding a vote nobody drew would
  decide who decides.

`risk_classification`, a free object, is read by nothing and deprecated; `risk_class`
replaces it.

### Checked afterwards

A gate with `"review_role": "sample"` and `"review_sample_rate": 0.1` lets the run go on and
draws one pass in ten into a review queue. Which passes is decided by a hash of the pass,
not by chance: the audit entry `workflow.run_gate_reached` says `"drawn": true` or `false`
with the share, and the same pass gives the same answer every time. A sample gate has no
areas and no time limit — it decides nothing and waits for nothing. Where the minimum asks
for more, it waits as an `approver` gate; a scope classified stricter makes it wait at once.

```bash
GET  /v1/samples                          # open ones; ?open_only=false for all
GET  /v1/samples/{sample_id}              # one, with what there is to judge
POST /v1/samples/{sample_id}/verdict      {"verdict": "objected", "reason": "…"}
```

The queue holds direct calls too (`"source": "direct_call"`): where a scope lets
`/v1/complete` answer, a share of its calls is drawn the same way
([Scopes and blocks](scopes-and-blocks.md)). For a workflow pass (`"source": "workflow"`)
the sample id is the gate's execution id.

Whoever may see the run sees its sample; from `reviewer` up a pass is judged, once:
`confirmed` or `objected`, with a reason stored with the run's placeholders and audited as
`workflow.sample_reviewed`. The output to judge is the run's, under
`GET /v1/workflows/runs/{run_id}/nodes`; for a direct call it is in the sample itself, and
the verdict is audited as `direct_call.sample_reviewed`. An objection recalls nothing — the
output went on — it is counted. A sample nobody judged goes with the content period.

### Areas

Where it matters *who* decides, not only how many — front office and back office for a
credit — a gate names its areas instead (`review_areas`). Each area casts one vote:

```json
{"node_ref": "votum", "node_type": "human_review", "review_areas": ["markt", "marktfolge"]}
```

An area is defined on a scope and applies there and below, with its members, by `admin`:

```bash
POST   /v1/scopes/{scope_id}/review-areas                            {"name": "marktfolge"}
POST   /v1/scopes/{scope_id}/review-areas/marktfolge/members         {"actor_id": "…"}
DELETE /v1/scopes/{scope_id}/review-areas/marktfolge/members/{actor_id}
GET    /v1/scopes/{scope_id}/review-areas
```

A member votes with the usual `POST /v1/workflows/runs/{run_id}/oversight`; whoever belongs
to more than one of the gate's areas adds `"area": "marktfolge"`. A member also needs to see
the run — a member of the workflow's scope or above. Then:

- every area approves → released; every area rejects → `rejected`;
- **the votes differ → escalated**: neither area overrules the other, and the run goes along
  the gate's escalation connection ([below](#escalation)) to a higher authority;
- until every area has voted, the gate waits — a rejection included.

One person casts one vote at a gate, so nobody votes for two areas. A vote for an area needs
a current membership there; the database refuses it otherwise, also past the API. Removing a
member revokes the membership with a date instead of deleting it: votes cast before stay
and count, new ones are refused. Activation refuses an area defined nowhere above the
workflow, one without members, `approver` with two areas and `four_eyes` with one (one vote
per area says how many people decide), and two areas without an escalation connection.

### Gates nobody decides

A gate nobody decides does not wait for good. `cc oversight expire`, started by the
operator's timer ([README](../../../README.md#retention)), ends or escalates it:

- **By its own time limit.** `review_timeout` (for example `"P2D"`) and `on_timeout` go
  together; activation refuses one without the other, and an `on_timeout` that approves.
  Past the limit, `reject` ends the run as `expired` — nobody decided, so it is neither
  `rejected` nor `cancelled` — and `escalate` hands it to the next gate.
- **By the content period of its scope.** A gate without a time limit waits until the
  content period has passed since the run reached it. Then the run ends as `expired` and its
  content is cleared at once. This holds for a gate that would escalate as well: an
  escalation would only keep the content waiting longer.

Both are audited (`workflow.run_expired`, `workflow.run_escalated`) without a person — none
acted. A decision made in the meantime stands.

### Escalation

An escalation goes along a connection from the gate to another `human_review`, guarded by
exactly this condition:

```json
{"source_node_ref": "check", "target_node_ref": "head_of_unit",
 "condition": {"op": "eq", "field": "decision", "value": "escalated"}}
```

The run takes it only when the gate escalates — on a timeout, or when its areas disagree.
On an approval it takes the gate's other connections, and with none the run is complete.
The higher authority is a gate with an area of its own that the escalating gate does not
name — a credit committee may decide with one vote — or else one that asks for at least as
many people. Activation refuses `on_timeout: "escalate"` without such a connection, one that
leads anywhere but to a gate, and one to a gate that is no higher (`four_eyes` → `approver`,
or `markt` + `marktfolge` → `marktfolge`). A waiting run can still be stopped
(`POST /v1/workflows/runs/{run_id}/cancel`).

## Branches

A connection can carry a condition on the output of the node it starts from, so a run
takes one path or another. The field is a path into that output — `data.…` for an
answer checked against a schema, `content` for the text as it came:

```json
{"source_node_ref": "classify", "target_node_ref": "escalate",
 "condition": {"field": "data.risk_level", "op": "eq", "value": "high"}}
```

Operators: `eq`, `ne`, `lt`, `lte`, `gt`, `gte`, `in`, `contains`, `exists`; combined
with `all`, `any` and `not`. An edge back to an earlier node is a loop and needs a
`loop_limit`.

### A loop that runs out

A check that sends a draft back while it is not good enough, at most twice, and on to the
review otherwise:

```json
{"source_node_ref": "check", "target_node_ref": "draft", "loop_limit": 2,
 "condition": {"field": "data.ok", "op": "eq", "value": false}}
{"source_node_ref": "check", "target_node_ref": "review", "label": "default"}
```

Once the loop has used up its limit, its edge counts as a condition that did not hold: the
run goes on along the `default` edge, here to the review. The criterion was not met, only
the budget spent — so the run says so:

- the audit log has an entry `workflow.loop_exhausted` with the node, the edge, the limit
  and where the run went on to
- `GET /v1/workflows/runs/{run_id}/nodes` lists the loop under `exhausted_loops`, next to
  the outputs whoever decides at the gate reads

A loop that runs out with no other way on — no `default`, nothing else running — ends the
run as `failed`, naming the edge and its limit. It used to end as `complete`.
