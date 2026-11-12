# Credit analysis — the banking pilot as a workflow

The credit analysis of the LangGraph banking pilot (`pilots/banking/`), rebuilt as a
workflow definition on Cohereny's own runner (CHY-178, CHY-172). It reads an annual
statement, computes the ratios, has models assess them and rate the risk, drafts the vote
with the bank statement, and stops at the vote of front office and back office.

| File | Holds |
|---|---|
| `workflow.json` | the definition: 16 nodes, 23 connections, risk class `high` |
| `blocks.yaml` | the prompts as blocks, one per model step by tag, and the legal frame of the vote |
| `routing.yaml` | which model serves each step, as routing rules |

One source for both the seed and the tests: `scripts/seed_banking_workflow.py` writes
these files into an installation; `tests/test_banking_example.py` holds them to their
promises, and `tests/integration/test_banking_example_db.py` runs them on a real database.

## The flow

| Step | Type | Reads |
|---|---|---|
| `jahresabschluss`, `kontoauszug` | `document` | the two documents of the run |
| `einordnung` | `llm_call` | the annual statement: type of document, company, year |
| `kennzahlen_lesen` | `llm_call` | the annual statement: ten figures as integers |
| `kennzahlen` | `compute` `banking.ratios@1` | the figures |
| `auffaelligkeiten` | `compute` `banking.anomalies@1` | the ratios |
| `bewertung` | `llm_call` | ratios and findings |
| `risiko` | `llm_call` | company, ratios, findings, assessment: GRÜN, GELB or ROT |
| `votum` | `llm_call` | all of that, the rating, and the bank statement |
| `votum_freigabe` | `human_review` | the draft — areas `markt` and `marktfolge` |
| `kreditkommission` | `human_review` | reached only if the two votes differ — area `kreditkommission` |

Each model step reads its instruction from a `context_compose` node of its own
(`prompt_<step>`), which composes only the blocks tagged `banking:<step>`. One block serves
three steps: the definitions of the ratios, tagged for assessment, rating and vote. A step with
several inputs waits for all of them (`wait_all`).

- **Two documents.** The annual statement feeds classification and extraction. The bank
  statement has a single edge, to the draft of the vote: the risk rating neither receives
  it nor reads a step that did (§ 37a BDSG, CHY-177).
- **Computed, not estimated.** `banking.ratios@1` and `banking.anomalies@1` compute what
  the pilot computed in Python, with the pilot's thresholds (ADR-048).
- **The gate always holds.** Front office (`markt`) and back office (`marktfolge`) vote
  once each (MaRisk BTO 1.1). If they differ, the run goes to the credit committee
  (`kreditkommission`), which decides with one vote. There is no fast track.

## Setting it up

```bash
python3 scripts/seed_banking_workflow.py            # what it would write, offline
COHERENY_API_URL=https://… COHERENY_TOKEN=… \
  python3 scripts/seed_banking_workflow.py --apply \
  --markt <actor> --marktfolge <actor> --kreditkommission <actor>
```

The token is that of an `admin` on the `author` surface. The seed creates the scope
*Musterbank* (`confidential`), the three review areas with their members, the blocks and
routing rules as drafts, and the workflow as a draft. What exists already is left as it
is. Then, by people:

1. approve the seven blocks and the five routing rules (`reviewer`)
2. check the members of the areas
3. keep the run trace and the run content of *Musterbank* for at least 183 days (`admin`,
   admin surface): `PUT /v1/admin/retention/run_trace` and `…/run_content` with
   `{"scope_id": "<Musterbank>", "keep_days": 183}`. A credit assessment is a high-risk AI
   system, and the AI Act asks its deployer to keep the logs six months (Art. 26(6));
   activation is refused until both periods are that long
4. validate and activate the workflow (`reviewer`)

A run is started like any workflow with documents: it waits as `awaiting_documents`
until `jahresabschluss` and `kontoauszug` have arrived ([Documents](../../quickstart/guides/workflows.md#documents)).
The PDFs of the pilot's fixtures serve as test material:
`python3 pilots/banking/fixtures/generate_fixtures.py`.

## What differs from the pilot

| The pilot | Here | Why |
|---|---|---|
| Prompts as literals in `nodes.py` | Blocks in the repository, one per step by tag, reviewed before any run uses them | ADR-042; a changed prompt reaches the next run only after approval |
| Free text for the risk class, parsed: `"BEGRÜNDUNG"` contains `"GRÜN"`, and any `"ROT"` won | A JSON answer with `risikoklasse` one of three values, checked against a schema | CHY-175; nothing left to parse |
| The anomaly assessment assumed mechanical engineering for every company | No industry the document does not name | the prompt asked for a guess |
| `compliance_check` generated a compliance statement per run | Left out; the legal frame is one reviewed block for the vote | decided 09.11.2026: compliance is a property of the process, not text a model writes |
| Fast track when no rule fired: no risk rating, no review, GRÜN by default | Every run is rated and stops at the gate | decided 08.11.2026 |
| The bank statement in the run state, readable by every node | One edge, to the draft of the vote | CHY-177 way 1 |
| A missing figure or a denominator of 0 became 0 or 1.0 | The step refuses, the run fails with the reason | ADR-048 |
| Figures as German number strings, coerced | Integers, checked against the schema; a model that writes `15.250.000`, or a negative depreciation, fails the step | a coercion that guesses is a second parser of model output; the profit and loss account prints expenses with a minus |
| The classification read the first 3,000 characters, on phi3-mini | The whole statement, on gemma3-4b | a definition hands a step what its edge carries, and phi3-mini on the Raspberry Pi took longer than the 300 s a call may take (comparison run 09.11.2026) |
| The report written after the decision | The draft of the vote is what front and back office decide on | the people decide on the text, not after it |
| One gate, one approver, a timeout of two hours | Two areas, one vote each, escalation to the committee; time limits and expiry per ADR-050 | MaRisk BTO 1.1 |

The fixtures no longer print the expected result in the PDF. Until 09.11.2026 the cover
page said `Erwartet: ROT`, and the notes listed the expected anomalies with the bank's
thresholds — text a model read before it rated.

Fixture 05 (no short-term liabilities in either year) now ends at `kennzahlen`: the
current ratio and the change year on year have a denominator of 0. The pilot computed no
anomaly there and expected GRÜN.

## The pilot's tests

The 59 tests of `pilots/banking/tests/` go with the pilot (CHY-179). What each group
checked, and where that is held now:

| Pilot tests | Count | Now |
|---|---|---|
| `TestCoerceNumber` — German number strings to float | 10 | No counterpart, on purpose: figures arrive as integers and are checked against the schema (`tests/test_output_schema.py`) |
| `TestExtractJson` — JSON from text, code fences, embedded | 6 | `tests/test_output_schema.py`: one fenced block is read, text around it is refused — the pilot read JSON out of prose |
| `TestNodeCalculateRatios` — ratios of two fixtures, zero denominators | 4 | `tests/test_steps.py::TestRatios`; a zero denominator is refused instead of giving 0 (ADR-048) |
| `TestNodeRiskGateway`, `TestRouteAfterGateway` — fast track or full analysis | 7 | Gone with the fast track; `tests/test_banking_example.py::TestTheGraph::test_the_gate_always_holds` |
| `TestNodeDetectAnomaliesRules` — the three rules and their thresholds | 7 | `tests/test_steps.py::TestAnomalies`, the thresholds at their edges taken over on 09.11.2026 |
| `test_ingress_wiring.py` — the document scanned, audited, fenced, called material | 4 | Scan, audit and fence at the upload: `tests/test_run_documents.py`, `tests/integration/test_run_documents_db.py`; called material by each step's own prompt: `tests/test_banking_example.py::TestTheSteps::test_every_step_that_reads_a_document_is_told_it_is_material` |
| `TestMissingGate` — no gate never approves | 3 | No workflow without `human_review` is activated: `tests/test_workflow_graph.py::test_each_mandatory_node_is_required` |
| `TestTimeout` — a timeout is not an approval | 6 | `on_timeout` is never `approve` (migration 018); escalation and expiry: `tests/test_gate_expiry.py`, `tests/integration/test_gate_expiry_db.py` |
| `TestDecisions` — approved, rejected, cancelled, an unusable value | 7 | `tests/test_workflow_advance.py::TestOversight`, `tests/integration/test_run_cancel_db.py`; a decision outside the vocabulary is refused by the request model |
| `TestPrompt` — a decision typed at the console | 5 | No counterpart: a decision is made through the API by a person with a role and an area, and audited |
