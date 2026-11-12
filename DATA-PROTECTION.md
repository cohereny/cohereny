# Data protection

What an operator needs from Cohereny to assess the impact of processing personal data with
it (GDPR Art. 35): what Cohereny holds and where it goes, what it does to protect it, what it
leaves open, and a checklist for the assessment itself. Written for whoever runs Cohereny;
the companion to [CRYPTOGRAPHY.md](CRYPTOGRAPHY.md), which covers keys and encryption.

**Cohereny is software, not a controller.** Whoever runs it decides which data goes in and
for which purpose, and the data protection impact assessment (DPIA) is theirs to carry out
— before the first real personal data, and certainly before special categories such as
health data (Art. 9). This document is the part the software can contribute. For high-risk
AI systems the AI Act says so explicitly: deployers use the provider's information for their
DPIA (Art. 26(9)). The reference installation that develops Cohereny processes no special
categories of data.

**Every measure names the test that shows it failing.** A description of a safeguard is a
claim; a test that turns red when the safeguard is removed is evidence. Where a measure has
no such test, the table says so. `tests/test_data_protection_doc.py` checks that every test
named here exists, so a renamed test cannot leave this document pointing at nothing.

A filled-in example — a hospital structuring discharge letters for the electronic patient
record, with risks rated and an outcome — is in [DATA-PROTECTION-EXAMPLE.md](DATA-PROTECTION-EXAMPLE.md)
(deutsch: [DATA-PROTECTION-EXAMPLE.de.md](DATA-PROTECTION-EXAMPLE.de.md)). It tested this template
against a case; what it found is corrected here.

The structure follows Art. 35(7): the processing (a), necessity and proportionality (b), the
risks (c), and the measures (d). The measures are grouped by the seven protection goals of the
Standard Data Protection Model (SDM) of the German data protection authorities, the method
they use to assess technical and organisational measures.

## Who does what

| Concern | Cohereny | The operator |
|---|---|---|
| Purpose and legal basis (Art. 5, 6, 9(2)) | Records which scope and workflow a run belongs to | Decides them and documents them per scope and workflow |
| Record of processing (Art. 30) | Lists every table and its retention class (`GET /v1/admin/retention`) | Keeps the record |
| DPIA (Art. 35) | This document | Carries it out before personal data enters |
| Prior consultation (Art. 36) | — | Consults the supervisory authority if a high risk remains |
| Data subject rights (Art. 15–22) | Anonymization of an account; content expires with its period | Receives and answers requests |
| Security of processing (Art. 32) | The measures below | Network, encryption at rest, backups, secrets ([CRYPTOGRAPHY.md](CRYPTOGRAPHY.md)) |
| Processors and transfers (Art. 28, Chapter V) | Calls models only through the operator's gateway | Contracts with hosted model providers; where they process |
| Which model may see which data | Enforces the data class of the scope (ADR-041) | Classifies scopes, declares each model's ceiling |

## 1 · The processing — Art. 35(7)(a)

### Whose data

- **Users** — the people who sign in. Cohereny keeps their identity-provider subject, display
  name and role in one table (`actors`); everything else refers to them by a random UUID.
- **People in the content** — patients, customers, applicants: whoever the documents and
  prompts a workflow processes are about. Cohereny cannot know who they are; their data is
  whatever the operator sends in.

### What is kept where

Every table belongs to a retention class, listed in `kernel/retention.py` and readable at
`GET /v1/admin/retention`; the table in [CRYPTOGRAPHY.md](CRYPTOGRAPHY.md#protection-need) says
which classes carry personal data. In short:

| Kind | Where | Personal data |
|---|---|---|
| Run content | Inputs and outputs of runs and their steps, a workflow run's documents as text, free-text reasons at the gate, comments — a workflow run's with placeholders, the mapping back to the values encrypted (CHY-234); a pilot run's with the values. Also the direct calls drawn for review: prompt, answer and the reason of the verdict, with placeholders and without any mapping (CHY-269) | Likely, including special categories — this is what the models read and write |
| Run trace | Who started a run, which model answered, who decided at the gate | Pseudonymous: actor UUIDs |
| Audit log | Every governance action | Pseudonymous: actor UUIDs, plus decision reasons |
| Accounts | `actors`, scope memberships | Subject and display name, until anonymized |
| Context | Context blocks, curated by people | Whatever authors write into them |
| Outside the database | Langfuse traces (optional), the Redis cache of composed contexts, the cooldown of models that answered 429 (CHY-159), pilot input files | Traces and cache: placeholders and context, except the banking pilot's traces, which carry the values; cooldown: a model name and a time, nothing personal; input files: as delivered |
| Application log | The API's output on stderr, kept by whatever collects it — the container runtime, a log shipper; Cohereny itself keeps no file (CHY-97) | Pseudonymous: a request by route, status and actor UUID. No content; but the traceback of an unexpected error carries what a library put in its message, which can be a value from the database |

### Where it flows

1. A caller sends a request to the API, with a token from the operator's identity provider.
2. Cohereny composes the context from the approved blocks of the scope and the scopes above it.
3. **Before every model call** of `/v1/complete` and of a workflow, identifiers are replaced by placeholders: e-mail addresses,
   phone numbers, IBANs, German tax ids and health insurance numbers, labelled dates of birth,
   every value the caller names, and the scope's own redaction rules (ADR-018). The model, the
   gateway and the trace see the placeholders — the trace also in place of what the model
   writes on its own. A caller of `/v1/complete` gets the answer with the values back; a
   workflow run keeps it with placeholders (step 6). The deprecated LangGraph pilots call the
   model with the values (§5).
4. **The model is chosen within the data class of the scope** (ADR-041). A model whose
   ceiling is below the class is never called; a scope nobody classified counts as `critical`.
5. In a workflow, **a person reviews the output at a gate** before it leaves the workflow.
   The decision is recorded with the person who made it. Whoever started a run, and from
   `reviewer` up whoever sees it, can **stop it** at any point before it ends (AI Act
   Art. 14(4)(e), CHY-200); the run ends as `cancelled`, which is not a decision at the gate.
   A run nobody decides does not wait for good (CHY-254, ADR-050): past the gate's own time
   limit it escalates to a further gate or ends as `expired`; past the content period of its
   scope, counted from when it reached the gate, it ends as `expired` and its content is
   cleared. `cc oversight expire` applies this and has to run on a schedule (§6, item 5).
   Outside a workflow, `/v1/complete` returns the answer without a gate, so it answers
   only where the scope's minimum review is `sample`: data `public` or `internal`, and
   direct calls classified `minimal` by an architect (CHY-199, ADR-050). Everywhere else,
   unclassified scopes included, it refuses before a model is asked. Where it answers, a
   share set with the classification is **drawn for a check afterwards** (CHY-269): the
   call keeps what the model was asked and what it answered, with placeholders and no
   mapping, in the same review queue as a workflow's sample gates; the caller gets the
   answer as always, and its `sample_id`. Content cleared after the scope's content period,
   the verdict deleted with the trace period. A scope classified `minimal` without a share
   — possible only before migration 048 — refuses too.
6. **A workflow run is stored with placeholders** (CHY-234): its input as it arrives, every
   step's input and output, the reasons given at the gate — in place of every identifier step 3
   recognises. A name nobody lists is stored as written, as it reaches the model. The mapping back to the values is
   encrypted in the application, under a key the database never sees (`PII_MAPPING_KEYS`).
   Whoever started the run, and from `reviewer` up whoever sees it, can have the values put
   back; each time is recorded. The content is cleared when its period ends (default 30 days),
   the mapping with it; the run's trace is deleted later (default 183 days). Pilot runs, and
   workflow runs started before migration 036, keep their values until their period ends.
7. **Documents of a workflow run** (ADR-049) arrive on an endpoint of their own while the run
   waits for them, from whoever started it and nobody else. A PDF is read to text when it
   arrives; the file is not kept. The text is scanned for instruction-shaped content and stored
   with the run's placeholders, and it reaches only the steps an edge from its `document` node
   leads to — documents are not part of the run input. A run that waits for documents in vain
   is cleared with its period like a finished one.

### Who can read what

| Who | What |
|---|---|
| The model, the gateway, the trace | Placeholders instead of the identifiers Cohereny recognises (step 3) |
| A signed-in user | The runs they started, the runs of the scopes they are a member of and of the scopes below, every run as `admin` (CHY-92), with placeholders — and **every block of the installation**, which is still open (CHY-237; see residual risks) |
| Whoever started a run; from `reviewer` up whoever sees it | The run's values, through `POST /v1/workflows/runs/{id}/resolve` — each time audited, the record never holding a value (CHY-234) |
| The API's database role | What each table grants it; no ownership, no DDL, cannot switch a trigger off (CHY-85) |
| Whoever holds the database's owner or superuser credentials | Everything, including the history — except a workflow run's values, which need the mapping key as well |
| Whoever reads the API's output (container logs, the log collector) | The application log: which actor UUID called which route when, what the model gateway answered when it failed, tracebacks of unexpected errors |

## 2 · Necessity and proportionality — Art. 35(7)(b)

Whether a processing is necessary for its purpose is the operator's judgement. Cohereny gives
it these levers, each set per scope:

- **Data class** — which models may see the data at all, down to local models only.
- **Redaction** — which identifiers never leave the installation: the shipped patterns, the
  values a caller names, and the scope's own rules as reviewed blocks.
- **Retention** — how long run content and run traces are kept.
- **Scope tree** — separate contexts for separate purposes; a deeper scope may tighten an
  inherited privacy rule, never loosen it.
- **Human oversight** — a gate with one reviewer or four eyes before an output leaves a workflow,
  or with one vote from each area that has to agree, such as front office and back office. How
  many at least follows the scope's data class and the workflow's risk class.

## 3 · Risks — Art. 35(7)(c)

How likely and how severe each risk is depends on the data and the purpose, so the operator
rates them. What the software contributes is the list, and what limits each risk:

| Risk to the people concerned | How it could happen | What limits it |
|---|---|---|
| Disclosure to a model provider | A hosted model receives health or financial data | Data class ceiling; placeholders before every call; a routing rule to a model below `critical` needs a second person |
| Disclosure to other users of the installation | A signed-in user reads runs or blocks that are not theirs | Runs: row-level security by starter, scope membership and `admin` (CHY-92). Blocks: open until CHY-237 |
| Disclosure through a copy of the database | A dump or backup is lost; it holds run content | Workflow runs hold placeholders, their values only encrypted under a key kept outside the database (CHY-234); encryption at rest by the operator; retention periods. Pilot runs keep their values |
| The values of runs are lost | The mapping key is lost or replaced without keeping the old one | The key in the secret store, backed up apart from the database; rotation keeps an old key until its runs have expired |
| A decision about a person without a person | A model's output becomes a decision unreviewed (Art. 22) | Mandatory gate in every workflow; rejection ends the run; four eyes where configured; a run can be stopped at any point |
| A record that is changed afterwards | Someone rewrites the audit log or a block's history | Append-only triggers that hold against the application's own role |
| Data kept longer than needed | Content and traces accumulate | Periods per scope and a purge that applies them |
| A request for erasure that cannot be met | The person's data sits in run content | Accounts are anonymized; content expires with its period — erasure per person in content is planned (CHY-239) |
| A name nobody listed reaches the model and is stored | Free text mentions someone the patterns do not catch | Caller-named values and scope rules; the content period; recognising names without a list is planned (CHY-235) |
| Credentials leak from a container | Someone gains code execution in the API | The API's role cannot bypass the triggers; no credentials are built into the image |

## 4 · Measures — Art. 35(7)(d)

Grouped by the SDM protection goals. "Shown failing by" names the test that fails if the
measure is removed; pytest node ids, run them to see.

### Data minimisation

| Measure | Where | Shown failing by |
|---|---|---|
| Identifiers replaced before every model call of `/v1/complete` and of a workflow, in every attempt of a fallback chain | ADR-018, `core/completion.py` | `tests/test_completion.py::TestRedactionAtTheBoundary::test_the_gateway_sees_placeholders_only`, `tests/test_completion.py::TestRedactionAtTheBoundary::test_every_attempt_of_a_fallback_chain_is_redacted` |
| The trace records placeholders and counts, never values | ADR-018 | `tests/test_completion.py::TestRedactionAtTheBoundary::test_the_trace_records_placeholders_and_counts_never_values` |
| What the model writes on its own reaches the trace only as placeholders | ADR-018, CHY-244 | `tests/test_completion.py::TestRedactionAtTheBoundary::test_what_the_model_wrote_on_its_own_reaches_the_trace_as_a_placeholder` |
| A workflow run is stored with placeholders — its input, every step's input and output, the reasons at the gate; a search of every table finds none of its values | CHY-234, migration 036 | `tests/integration/test_run_placeholders_db.py::test_no_value_of_the_run_is_stored_as_written`, `tests/integration/test_run_placeholders_db.py::TestResolving::test_a_reason_at_the_gate_is_stored_with_placeholders` |
| The caller's list of names is not stored; each listed name is searched for in every step, found in the input or not | CHY-234 | `tests/test_run_placeholders.py::TestTheStart::test_the_list_goes_into_the_mapping_not_into_the_input` |
| A document reaches only the steps an edge from its node leads to; documents are not in the run input, so a step that has no such edge does not see one | ADR-049, CHY-177 | `tests/integration/test_run_documents_db.py::test_the_bank_statement_reaches_the_draft_and_nothing_else`, `tests/test_run_documents.py::TestSection37a::test_the_score_does_not_see_the_bank_statement` |
| In the credit analysis shipped as an example, the bank statement has a single edge, to the draft of the vote: the risk rating neither receives it nor reads a step that did (§ 37a BDSG). Each model step composes only the instruction tagged for it | CHY-178, `deploy/examples/banking` | `tests/test_banking_example.py::TestTheGraph::test_the_bank_statement_reaches_the_draft_and_nothing_before_the_rating`, `tests/integration/test_banking_example_db.py::test_every_step_gets_its_own_prompt_and_the_run_stops_at_four_eyes` |
| A document is kept as text with the run's placeholders, never as the file; a name listed at the start is replaced in it too | ADR-049, migration 041 | `tests/test_run_documents.py::TestWhatIsKept::test_the_text_is_kept_with_the_runs_placeholders` |
| A step takes no larger a document than its node allows, and a node no more than the installation | ADR-049 | `tests/test_run_documents.py::TestLimits::test_the_node_narrows_the_ceiling`, `tests/test_run_documents.py::TestActivationRule::test_a_node_above_the_ceiling_is_refused_per_limit` |
| A run that waited for documents in vain is cleared with its period, its documents included | ADR-049, ADR-046 | `tests/integration/test_retention_purge_db.py::test_a_run_that_waited_for_documents_in_vain_expires` |
| A run nobody decides at its gate is cleared with the content period, counted from when it reached the gate — also where the gate would escalate | ADR-050, CHY-254, `cc oversight expire` | `tests/integration/test_gate_expiry_db.py::test_past_the_content_period_the_run_expires_and_its_content_goes`, `tests/integration/test_gate_expiry_db.py::test_the_content_period_wins_over_an_escalation` |
| A run that ended as `expired` is cleared and deleted with its periods like every other ended run | ADR-050, ADR-046 | `tests/integration/test_gate_expiry_db.py::test_an_expired_run_is_purged_like_a_finished_one` |
| Run content is cleared before the run's trace is deleted | ADR-046, `cc retention purge` | `tests/integration/test_retention_purge_db.py::test_content_expires_before_the_trace_and_unfinished_runs_are_left_alone` |
| Every table has a retention class — none escapes the periods | ADR-046, `kernel/retention.py` | `tests/integration/test_retention_db.py::test_every_table_in_the_database_is_in_the_inventory` |
| The application log writes a request as its route template, status and actor UUID — no value from the path, no query string, no client address; the server's own access line, which wrote the last two, is off | CHY-97, `api/request_log.py` | `tests/test_request_log.py::TestTheRequestLine::test_without_a_token_the_line_has_route_status_and_no_query`, `tests/test_request_log.py::TestTheRequestLine::test_a_signed_in_request_names_its_actor_by_uuid`, `tests/test_request_log.py::TestInstall::test_the_server_writes_through_the_app_log_and_its_access_line_is_off` |

### Confidentiality

| Measure | Where | Shown failing by |
|---|---|---|
| Every route except `/v1/health` needs a token; no setting turns this off | CHY-192 | `tests/test_no_auth_bypass.py::TestNoRouteAnswersWithoutAToken::test_without_a_token_the_answer_is_401` |
| A model below the data class of the scope is never called | ADR-041 | `tests/test_scope_data_class.py::TestCeiling::test_a_cloud_model_may_not_see_critical_data` |
| A scope nobody classified counts as `critical`; the strictest class on the chain wins | ADR-041 | `tests/test_scope_data_class.py::TestEffectiveClass::test_nobody_declared_means_critical`, `tests/test_scope_data_class.py::TestEffectiveClass::test_the_strictest_on_the_chain_wins_not_the_deepest` |
| Only whoever started a run uploads its documents; whoever does not see the run does not see them | ADR-049, migration 041 | `tests/test_run_documents.py::TestWhoMayUpload::test_only_the_starter`, `tests/integration/test_run_isolation_db.py::TestWhoSeesAWorkflowRun::test_the_documents_follow_the_run` |
| A run is visible only to whoever started it, to the members of its workflow's scope and of the scopes above, and to `admin`; a pilot run to its starter and `admin` | CHY-92, migration 035 | `tests/integration/test_run_isolation_db.py::TestWhoSeesAWorkflowRun::test_a_member_of_another_scope_does_not`, `tests/integration/test_run_isolation_db.py::TestWhoSeesAWorkflowRun::test_a_stranger_does_not`, `tests/integration/test_run_isolation_db.py::TestWhoSeesAPilotRun::test_a_member_of_any_scope_does_not` |
| A run's audit trail goes to the people who see the run, and so does that of a pilot run and of a drawn direct call; anyone else gets 404, as from the run itself | CHY-303, `api/v1/audit.py` | `tests/test_audit_log.py::TestHistoryFollowsTheObject::test_a_hidden_object_answers_404_and_the_log_is_not_read`, `tests/integration/test_run_isolation_db.py::TestWhoSeesAWorkflowRun::test_its_history_follows_the_run`, `tests/integration/test_run_isolation_db.py::TestWhoSeesAPilotRun::test_its_history_follows_the_run`, `tests/integration/test_direct_call_samples_db.py::test_its_history_goes_to_the_same_people` |
| A query that does not say for whom it reads sees no run and writes none — a forgotten filter stays closed | CHY-92, `kernel/acting.py` | `tests/integration/test_run_isolation_db.py::TestWithoutSayingForWhom::test_a_query_sees_no_rows`, `tests/integration/test_run_isolation_db.py::TestWithoutSayingForWhom::test_nothing_is_written` |
| Every table that holds run content is under row-level security | CHY-92, `kernel/retention.py` | `tests/integration/test_run_isolation_db.py::test_every_table_with_content_has_row_security` |
| The API refuses to start as a superuser, an owner, or with `BYPASSRLS`, `CREATE` or `TEMP` | CHY-85, `kernel/least_privilege.py` | `tests/test_app_privileges.py::TestEveryEntryChecks::test_the_api_refuses_to_start_and_closes_its_pool`, `tests/integration/test_app_role_db.py::test_the_owner_is_refused_at_start` |
| No credentials in the image | CHY-85, `.dockerignore` | `tests/test_image_contents.py::test_environment_files_never_reach_the_build_context` |
| A run's mapping is encrypted in the application, bound to its run, under a key the database never sees | CHY-234, `kernel/mapping_cipher.py` | `tests/test_mapping_cipher.py::TestWhatDoesNotOpen::test_not_for_another_run`, `tests/integration/test_run_placeholders_db.py::test_the_values_are_kept_in_the_sealed_mapping` |
| The values are put back only for whoever started the run and, from `reviewer` up, whoever sees it | CHY-234 | `tests/integration/test_run_placeholders_db.py::TestResolving::test_a_colleague_without_the_role_reads_placeholders_only`, `tests/integration/test_run_placeholders_db.py::TestResolving::test_someone_who_does_not_see_the_run_learns_nothing` |
| The API does not start without the mapping key | CHY-234 | `tests/test_run_placeholders.py::TestTheKey::test_the_api_does_not_start_without_one` |
| An error that leaves the installation — an answer, a failed run, the audit log — names no internal address and no text of a gateway or library; it carries the request id, and the details stay in the application log | CHY-97, `core/errors.py` | `tests/test_complete.py::test_a_gateway_failure_leaves_without_its_host`, `tests/test_complete.py::test_a_fallback_is_written_to_the_audit_log`, `tests/test_workflow_advance.py::test_a_foreign_exception_ends_the_run_instead_of_leaving_it_running`, `tests/test_request_log.py::TestAnUnexpectedError::test_the_answer_is_a_500_with_its_id_and_nothing_else` |

### Integrity

| Measure | Where | Shown failing by |
|---|---|---|
| The audit log refuses UPDATE, DELETE and TRUNCATE, even from the owner of the table | ADR-009, migration 011 | `tests/integration/test_governance_triggers.py::TestAuditLogIsAppendOnly::test_rewriting_the_log_is_refused` |
| The application's role cannot bypass the triggers — no grant, no ownership, no replication role | CHY-85, migration 033 | `tests/integration/test_app_role_db.py::test_what_the_triggers_guard_is_out_of_reach` |
| A block gets a new version only through `cc_supersede_block` | ADR-009, migration 034 | `tests/integration/test_app_role_db.py::TestSupersession::test_setting_the_flag_yourself_is_refused` |
| A deeper scope cannot loosen an inherited privacy rule | ADR-017 | `tests/test_privacy_ratchet.py::TestTheRatchet::test_the_case_adr_017_demanded` |

### Intervenability

| Measure | Where | Shown failing by |
|---|---|---|
| No path from a model call leaves a workflow without a human gate | ADR-028, workflow validation | `tests/test_workflow_graph.py::TestReviewCoverage::test_a_branch_that_bypasses_the_review_is_refused` |
| A rejection at the gate ends the run | Art. 14(4)(d) AI Act | `tests/test_workflow_advance.py::TestOversight::test_rejecting_ends_the_run_as_rejected_not_failed` |
| A run can be stopped — by whoever started it, and from `reviewer` up by whoever sees it; run and open nodes end as `cancelled`, recorded as no decision at the gate; a node still working when the stop arrives does not move the run on | Art. 14(4)(e) AI Act, CHY-200, migration 038 | `tests/integration/test_run_cancel_db.py::test_at_the_gate_run_and_gate_end_cancelled_and_nobody_decided`, `tests/integration/test_run_cancel_db.py::test_without_the_role_refused_without_seeing_the_run_not_found`, `tests/integration/test_run_cancel_db.py::test_a_node_still_working_when_the_stop_arrives_does_not_move_the_run` |
| A gate's time limit acts: past it the run ends as `expired` or escalates to a further gate — never recorded as a decision, audited without a person; a decision made in the meantime stands | ADR-050, CHY-254, migration 042 | `tests/integration/test_gate_expiry_db.py::test_past_its_time_limit_a_rejecting_gate_ends_the_run_expired`, `tests/integration/test_gate_expiry_db.py::test_an_escalating_gate_hands_the_run_to_the_higher_gate`, `tests/integration/test_gate_expiry_db.py::test_a_decision_that_came_first_wins` |
| A gate asks for at least what the scope's data class and the workflow's risk class ask; left unclassified, both count as the strictest; a stricter classification applies to the next gate a run reaches, and the audit log says which level applied and why | CHY-223, ADR-050, migration 044 | `tests/integration/test_review_levels_db.py::test_activation_refuses_one_approver_in_an_unclassified_scope`, `tests/integration/test_review_levels_db.py::test_a_workflow_nobody_classified_is_refused_in_a_low_scope_too`, `tests/integration/test_review_levels_db.py::test_a_scope_classified_stricter_after_the_release_asks_for_more` |
| At `sample`, which only the lowest data class and risk class allow, a share of the passes is judged afterwards: the queue is visible to whoever may see the run and to nobody else, a verdict is given once from `reviewer` up, its reason is stored with placeholders and cleared with the content, and a stricter classification makes the gate wait for a person | CHY-223, ADR-050, migration 045 | `tests/integration/test_review_samples_db.py::test_the_table_itself_hides_the_sample`, `tests/integration/test_review_samples_db.py::test_a_verdict_once_from_reviewer_up_audited`, `tests/integration/test_review_samples_db.py::test_the_reason_is_stored_with_placeholders`, `tests/integration/test_review_samples_db.py::test_the_reason_goes_with_the_content_and_the_row_with_the_run`, `tests/integration/test_review_samples_db.py::test_a_stricter_scope_makes_the_sample_gate_wait` |
| At a gate with areas each area casts one vote, and only a current member of it: the database refuses a vote without membership, a second vote for an area, and one person for two areas; a revoked membership keeps its votes and takes no new one | CHY-180, ADR-050, migration 043 | `tests/integration/test_review_areas_db.py::test_the_database_refuses_a_vote_without_membership`, `tests/integration/test_review_areas_db.py::test_the_database_refuses_a_second_vote_for_an_area`, `tests/integration/test_review_areas_db.py::test_the_database_refuses_one_person_for_two_areas`, `tests/integration/test_review_areas_db.py::test_a_revoked_member_votes_no_more` |
| Differing votes of the areas are neither approval nor rejection: the decision goes to a higher authority, and the gate waits until every area has voted | CHY-180, MaRisk BTO 1.1 Tz. 6 | `tests/integration/test_review_areas_db.py::test_differing_votes_go_up_to_the_credit_committee`, `tests/integration/test_review_areas_db.py::test_a_rejection_alone_does_not_close_the_gate` |
| An escalation leads only to a gate that asks for at least as many people; a gate that escalates on timeout without such a connection is refused | ADR-050 | `tests/test_gate_expiry.py::TestGateActivationRules::test_the_higher_authority_may_not_ask_for_fewer_people`, `tests/integration/test_gate_expiry_db.py::test_activation_refuses_an_escalation_without_an_edge` |
| The reason for stopping a run is stored with placeholders | CHY-200 | `tests/integration/test_run_cancel_db.py::test_the_reason_is_stored_with_placeholders` |
| Cancelling a pilot run at its gate is not recorded as a rejection | CHY-153 | `tests/integration/test_pilot_lifecycle.py::test_a_run_cancelled_at_the_gate_ends_as_cancelled_not_as_a_rejection` |
| Only someone who may see a run decides at its gate; the role alone is not enough | CHY-92 | `tests/integration/test_run_isolation_db.py::TestDecidingNeedsToSeeTheRun::test_a_reviewer_outside_the_scope_cannot_decide` |
| A technical account (group `chy-service-accounts`) never decides at a gate and never judges a sample, whatever its role and membership; the decision stays open for a person | CHY-275, `contexts/orchestration/service.py` | `tests/integration/test_run_isolation_db.py::TestTechnicalAccountsDoNotDecide::test_not_even_as_a_member_of_the_scope`, `tests/integration/test_direct_call_samples_db.py::test_a_technical_account_does_not_judge`, `tests/test_workflow_advance.py::TestTechnicalAccountsDoNotDecide::test_it_is_refused_and_the_gate_stays_as_it_was` |
| Four eyes: two different people; whoever edited a draft does not approve it; technical accounts never approve a rule that sends data to a hosted model | ADR-045, CHY-176 | `tests/test_workflow_advance.py::TestFourEyes::test_the_same_person_cannot_approve_twice`, `tests/integration/test_four_eyes_db.py::test_whoever_edited_the_draft_cannot_approve_it_and_a_third_person_can`, `tests/integration/test_four_eyes_db.py::test_a_technical_account_is_marked_by_its_group_and_approves_no_cloud_rule` |
| Switching off a shipped redaction rule needs a second person | ADR-018 | `tests/integration/test_redaction_rules_db.py::test_switching_off_at_the_top_needs_a_second_person` |
| A direct call (`/v1/complete`) answers only where an answer may be used unchecked — data `public` or `internal`, direct calls classified `minimal` — and refuses elsewhere before any model is asked; unclassified counts as `high` | CHY-199, ADR-050, migration 047 | `tests/test_direct_calls.py::TestTheRule::test_direct_calls_answer_only_for_low_data_and_minimal_risk`, `tests/test_direct_calls.py::TestTheEndpointThatAnswers::test_an_unclassified_scope_is_refused_before_any_model_is_asked` |
| Where a direct call answers, a share is judged afterwards: set with `minimal` and only there, the highest along the chain; a drawn call is kept with placeholders only, visible to its caller, its scope's members and admin, judged once from `reviewer` up with its reason replaced too, and cleared and deleted by the periods; without a share no answer | CHY-269, ADR-050, migration 048 | `tests/test_complete.py::test_a_drawn_call_is_kept_with_placeholders_and_named_in_the_answer`, `tests/test_complete.py::test_minimal_without_a_share_is_refused_before_any_model_is_asked`, `tests/integration/test_direct_call_samples_db.py::test_the_caller_the_scope_and_admin_see_it_and_nobody_else`, `tests/integration/test_direct_call_samples_db.py::test_the_reason_is_kept_with_placeholders`, `tests/integration/test_direct_call_samples_db.py::test_the_periods_clear_the_content_then_delete_the_row` |
| Where a workflow counts as high-risk — `risk_class = high`, or none, which counts as `high` — its scope keeps the run trace and the run content at least six months (AI Act Art. 26(6)): activating it below is refused, and so is a change of a period that shortens it below six months there, set in the scope, further up, or removed so a shorter one is inherited; lengthening is always taken | CHY-266, ADR-046 | `tests/integration/test_high_risk_retention_db.py::test_a_high_risk_workflow_is_not_activated_where_logs_go_sooner`, `tests/integration/test_high_risk_retention_db.py::test_shortening_below_six_months_is_refused_and_kept_nowhere`, `tests/integration/test_high_risk_retention_db.py::test_a_parent_shortened_below_six_months_is_refused_for_the_child`, `tests/integration/test_high_risk_retention_db.py::test_a_limited_workflow_is_not_held_to_it` |
| Anonymization removes the person and keeps the record (Art. 17) | ADR-016, `POST /v1/admin/actors/{actor_ref}/anonymize` | `tests/integration/test_governance_triggers.py::test_anonymization_removes_the_person_and_keeps_the_record` |

### Transparency

| Measure | Where | Shown failing by |
|---|---|---|
| The periods in force can be read per scope, with where each comes from (Art. 5(2)) | ADR-046, `GET /v1/admin/retention` | `tests/test_retention.py::TestReading::test_for_a_scope_it_says_which_period_applies_and_where_it_comes_from` |
| Changing a period is audited | ADR-046 | `tests/test_retention.py::TestChanging::test_an_admin_sets_a_scope_period_and_the_change_is_audited` |
| A run's transparency record cannot be deleted with the run | migration 015 | `tests/integration/test_governance_triggers.py::test_a_run_with_a_transparency_record_cannot_be_deleted` |
| Every document a run receives is audited with what the scan found, counted and never quoted — also when it found nothing | ADR-049 | `tests/test_run_documents.py::TestWhatIsKept::test_the_audit_counts_and_never_quotes` |
| Every time a run's values are put back, it is audited: who, which run, which steps — never a value | CHY-234 | `tests/integration/test_run_placeholders_db.py::TestResolving::test_every_resolution_is_audited_without_a_value` |

### Unlinkability

| Measure | Where | Shown failing by |
|---|---|---|
| Traces carry the internal actor id, not the identity provider's subject | ADR-016 | `tests/test_tracing.py::test_span_carries_the_internal_actor_id_not_the_oidc_subject` |
| Separate purposes in separate scopes, each with its own data class, rules and periods | ADR-041, ADR-046 | By configuration; purpose binding per workflow step is planned (CHY-238) |

### Availability

| Measure | Where | Shown failing by |
|---|---|---|
| Backups, restore and their tests | The operator ([CRYPTOGRAPHY.md](CRYPTOGRAPHY.md)) | — |
| Migrations only forward; the way back is the backup taken before an update | CHY-113 | — |
| The mapping key, kept and backed up apart from the database — a lost key takes the values of its runs with it | The operator ([CRYPTOGRAPHY.md](CRYPTOGRAPHY.md#secrets-and-keys)) | — |

## 5 · Residual risks

What Cohereny does not yet do, with the ticket that changes it. Until then, each is a risk
for the operator to rate and, where needed, to cover by organisation.

| Residual risk | Until then | Planned |
|---|---|---|
| Every signed-in user reads every block of the installation | Do not write personal data into blocks; keep the users of an installation to those with a need to know | CHY-237 — blocks, compose and route per scope membership (M6) |
| The deprecated LangGraph pilots send the values to the model and keep them, the banking pilot in its traces as well; workflow runs started before migration 036 keep theirs | Do not run the pilots with personal data — use workflows or `/v1/complete`; encrypt the database volume and the backups; keep the content period short. The older workflow runs expire with their period | CHY-172 with CHY-179 — the banking pilot is rebuilt on the runner (`deploy/examples/banking`, CHY-178) and goes with CHY-179; the study pilot later (ADR-028 point 5) |
| `/v1/route` only names a model; a client that then calls the gateway itself sends identifiers as written | Personal data only through `/v1/complete` or a workflow; give `/v1/route` to clients that send none | — |
| Whoever holds both the mapping key and the database credentials reads every value | Keep the key in the secret store, apart from the database, its hosts and its backups | — |
| A name nobody lists reaches the model and is stored with the run as written | Name the people you know (caller identifiers, scope rules); send data you cannot list completely only to a `critical` scope with local models; keep the content period short | CHY-235 — names recognised without a list (M7) |
| A technical account that starts runs may resolve them — whoever can use it reads the values of every run it started | Give such an account to an integration only where it needs the values back; keep its credentials like the data | — |
| Erasure per person in run content | The content expires with its period (default 30 days): choose the period with Art. 12(3) in mind — one month to answer a request. Not so in a scope with a high-risk workflow: there the content is kept at least six months (CHY-266), and a request meets a legal duty to keep (GDPR Art. 17(3)(b) with AI Act Art. 26(6)) — clear for the trace; whether it covers the content as well is the controller's judgement | CHY-239 (M7) |
| Text in context blocks has no period | Do not write personal data into blocks; blocks are curated material | CHY-242 (M7) |
| The owner and the superuser can still change the history; there is no hash chain | Keep those two credentials away from the API and its hosts' everyday access | — |
| Row-level security holds against faults of the application, not against its own credentials: whoever holds them can claim to act for anyone | Protect the application's database credentials like the data itself ([CRYPTOGRAPHY.md](CRYPTOGRAPHY.md)) | — |
| The traceback of an unexpected error goes to the application log as it is, including what a library wrote into its message — a key value the database refused, an address | Keep the application log short (§6, item 5) and readable only by whoever runs the installation, not by its users | — |

## 6 · Checklist for the operator

Before personal data enters an installation, and again when the processing changes
(Art. 35(11)) — a new model, a new scope, a new workflow:

1. **Purpose and legal basis** per scope and workflow, documented (Art. 5(1)(b), 6, for health
   data 9(2)).
2. **Is a DPIA required?** Art. 35(1) and (3), and the list your supervisory authority
   publishes under Art. 35(4). Health data, and AI used to assess people, are typical triggers.
3. **Classify every scope** with a data class and declare each model's ceiling. Know which
   models are hosted, by whom and where; the contract with that provider (Art. 28) and any
   transfer outside the EU (Chapter V).
4. **Name what you know**: the identifiers callers send along, and scope redaction rules for
   names and numbers of your domain (staff, case numbers). Send personal data only through
   workflows or `/v1/complete` — not through the deprecated pilots, nor through a client that
   calls the gateway itself after `/v1/route`.
5. **Retention**: set the content and trace periods per scope; run `cc oversight expire` and
   `cc retention purge` on a schedule — the purge leaves runs waiting at a gate alone, the
   first command ends those nobody decides — and alert on the age of the last purge; set the
   retention of Langfuse traces there. Keep the **application log** for 30 days, recommended:
   long enough to find the request behind a report, short enough that the actor UUIDs in it do
   not pile up. Cohereny writes it to stderr and does not rotate it; the container runtime or
   the log collector does.
   **Where a workflow is a high-risk AI system** (AI Act Annex III, `risk_class = high`), you
   as its deployer keep the logs it generates automatically for at least six months, unless
   other law, data protection law in particular, says otherwise (Art. 26(6)). In Cohereny
   those are the run trace (default 183 days) and the audit log (no period); the run content
   is held to the same six months (decided 10.11.2026). Cohereny refuses to activate such a
   workflow — or one nobody classified, which counts as `high` — where either period is
   shorter, and refuses to shorten either below six months there (CHY-266). With the shipped
   content period of 30 days that means: set it to at least 183 days in the scope first. The
   application log is the service's operating record, not these logs; its 30 days do not
   stand in for them.
6. **Erasure requests**: anonymize the account (`POST /v1/admin/actors/{actor_ref}/anonymize`);
   decide which content period applies in scopes with personal data and how a request is met
   within it. Anonymizing does not lock anyone out: the next request with a valid token for
   the same identity gets a new account, with the role its groups carry, and nothing links it
   to the old one. That is right for a person who keeps working and asked for what was
   recorded to be erased. For a person who **leaves**, access ends at the identity provider:
   **first** lock or remove the account there, **then** wait until the access tokens it issued
   before have expired (their lifetime is set there), **then** anonymize in Cohereny. In the
   other order, a request in between creates the new account. Every new account is in the
   audit log (`actor.provisioned`).
7. **Who reads what**: add the people who review runs to the scopes they work in (each
   reads their id from `GET /v1/me`; `POST /v1/scopes/{id}/members`). Blocks are readable by
   everyone signed in until CHY-237 — limit the users accordingly.
8. **Security**: TLS, encryption at rest, the secret store ([CRYPTOGRAPHY.md](CRYPTOGRAPHY.md));
   the migration and superuser credentials stay away from the API; the mapping key
   (`PII_MAPPING_KEYS`) generated for this installation, kept and backed up apart from the
   database.
9. **Human oversight**: who reviews at which gate; four eyes where a wrong output would harm,
   areas where two parts of the organisation must agree. Give every workflow a `risk_class`
   and every scope a data class — left out, each counts as the strictest, and every gate asks
   for four eyes. Where callers use `/v1/complete`, classify direct calls in that scope
   (`/v1/scopes/{id}/direct-call-risk-class`) — left out, it counts as `high`, and
   `/v1/complete` does not answer there (CHY-199). With `minimal` goes the share judged
   afterwards (`sample_rate`); someone has to work the queue (`GET /v1/samples`), or the
   check exists only on paper (CHY-269). Keep the areas' members current
   (`/v1/scopes/{id}/review-areas`): a person who moves on is revoked there, not only at the
   identity provider.
10. **The views of the people concerned** or their representatives, where appropriate (Art. 35(9)).
11. **A high risk that remains** after the measures: consult the supervisory authority before
    starting (Art. 36).
12. **The record of processing** (Art. 30): the tables and periods from
    `GET /v1/admin/retention` are a starting point, not the record.

## Changes

**2026-11-10** — Six months for high-risk logs (CHY-266, AI Act Art. 26(6)): where a workflow
counts as high-risk, the run trace and the run content are kept at least six months — activating
below and shortening below are refused. A measure with its tests; the residual risk of a free
trace period closed; the erasure risk restated for such scopes (GDPR Art. 17(3)(b)); the
checklist (item 5). The example (EXAMPLE) names its assumption: the workflow is `limited`.

**2026-11-10** — A spot check of direct calls (CHY-269, ADR-050): `sample` means a share is
judged afterwards for `/v1/complete` too. A new store of content — drawn calls with
placeholders, no mapping, under the content and trace periods — in the flow and the table
above, one measure with its tests, the residual risk of unchecked direct calls closed, and
the checklist (item 9): someone works the queue.

**2026-11-10** — Erasure and leaving (CHY-270): the checklist (item 6) says what anonymizing
does not do — it locks nobody out; the next login gets a new account — and, for a person who
leaves, the order: lock at the identity provider, let the tokens expire, then anonymize.

**2026-11-10** — Direct calls by review level (CHY-199, ADR-050): `/v1/complete` answers
only where an answer may be used unchecked; an architect classifies direct calls per
scope. A step of the flow, a measure with its tests, and the residual risk restated: a
direct call allowed at `sample` is not spot-checked (CHY-269).

**2026-11-10** — Model cooldown (CHY-159): Redis keeps, per stage, which model answered 429
and until when it is skipped. Listed under "Outside the database"; it holds no personal data.

**2026-11-09** — Application log (CHY-97): a new kind of record and who reads it, two measures
with their tests — the request line holds no value and no client address, and an error that
leaves names no internal address —, two residual risks, and the checklist (item 5) with the
log's period and the six months of the AI Act for high-risk workflows (Art. 26(6), CHY-6).

**2026-11-09** — Checked afterwards (CHY-223, ADR-050, step 3b): where data and risk are
lowest, a gate may be `sample` — the output goes on, a share is judged later. One measure with
its tests: the queue's visibility, the verdict and its reason follow the run.

**2026-11-09** — Review depth by risk (CHY-223, ADR-050, step 3a): a gate asks for at least
what the data class of its scope and the risk class of its workflow ask, and a forgotten
classification counts as the strictest. One measure with its tests; the checklist (item 9)
asks for both classifications.

**2026-11-09** — Areas at the gate (CHY-180, ADR-050): a gate may ask one vote from each of
several areas — front office and back office — and differing votes go to a higher authority.
Two measures with their tests; the checklist (item 9) asks for the areas' members to be kept
current, because a membership ends only where it is revoked.

**2026-11-09** — The credit analysis as a workflow (CHY-178): the measure for the bank statement,
shown until now on a reduced shape, now holds on the definition that is shipped, and a run of
it on a real database shows the rating's request without the statement and the draft's with
it. The banking pilot's residual risk ends when CHY-179 removes it.

**2026-11-09** — Gates nobody decides (CHY-254, ADR-050): the residual risk of 08.11. is
closed. A gate's time limit now acts — the run escalates to a further gate or ends as
`expired` —, and a run nobody decides is cleared with the content period, counted from when
it reached the gate. Four measures with their tests; `cc oversight expire` joins the
checklist (item 5), because it acts only when it runs.

**2026-11-08** — Documents of a workflow run (CHY-174, ADR-049): a new way in (step 7) and
five measures with their tests — a document reaches only the steps its node leads to, is kept
as text with placeholders, within its node's limit, uploaded by the starter alone, audited
with what its scan found; a run waiting for documents in vain is cleared with its period.
Writing that down showed the gap at the gate: a run nobody decides keeps its content past the
period — a residual risk until CHY-254.

**2026-11-08** — Exact about which model calls are covered: those of `/v1/complete` and of
workflows. The LangGraph pilots are deprecated; that they send the values to the model and
keep them is a residual risk of its own, and `/v1/route`, which leaves the call to the
client, joins the residual risks and the checklist.

**2026-11-07** — A run can be stopped (CHY-200): three measures under intervenability. Cancelling a
pilot run at its gate is no longer booked as a rejection (CHY-153).

**2026-11-07** — A filled-in example for a hospital (CHY-18) tested this template; it showed
that a name nobody lists is stored as well as sent, and that a technical account may resolve the
runs it starts — both corrected above. Workflow runs stored with placeholders (CHY-234): the
residual risk of run content with real values became six measures with their tests. Pilot runs and runs started
before migration 036 remain; the mapping key joins the risks, the availability measures and the
checklist.

**2026-11-06** — Runs are read per scope membership (CHY-92): the risk of every user reading
every run became four measures with their tests; blocks stay a residual risk (CHY-237), and
the limit of row-level security against the application's own credentials is named.

**2026-11-06** — First version (CHY-18): the processing, risks, measures by SDM goal with the
test that shows each failing, residual risks with their tickets, the operator's checklist.
