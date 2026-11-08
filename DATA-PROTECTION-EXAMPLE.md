# Example DPIA: structuring discharge letters for the electronic patient record

> **A filled-in example, not legal advice.** The hospital, its workflow, its periods and its
> ratings are invented. The example tests the template [DATA-PROTECTION.md](DATA-PROTECTION.md)
> against a case: is it enough to carry a data protection impact assessment under GDPR Art. 35
> for processing health data? Whoever runs Cohereny in a similar way may start from it — the
> assessment is the controller's, not this document's. Deutsche Fassung:
> [DATA-PROTECTION-EXAMPLE.de.md](DATA-PROTECTION-EXAMPLE.de.md).
>
> Software state: Cohereny with placeholders in stored runs (CHY-234, November 2026).

## 0 · Key facts

| | |
|---|---|
| Controller | Klinikum Beispielstadt, department of internal medicine (invented) |
| Processing | A language model drafts a structured version of a discharge letter — diagnoses, medication, recommendations — for the electronic patient record (ePA); a physician reviews and approves it |
| Why a DPIA | Large-scale processing of health data (Art. 35(3)(b), Art. 9(1)) with a new technology; the supervisory authority's list under Art. 35(4) is to be checked as well |
| Involved | Data protection officer (Art. 35(2)), IT security, head of department, patient advocate (Art. 35(9)) |
| Out of scope | Whether the system is a medical device (MDR) and therefore a high-risk AI system under the AI Act — the controller settles that separately. The hospital information system (HIS) and the ePA have their own assessments |
| Review | On every material change (a new model, a new scope, a changed workflow, a Cohereny release that touches data protection), otherwise yearly (Art. 35(11)) |

## 1 · The processing — Art. 35(7)(a)

### Purpose and legal basis

Physicians spend a good part of their time splitting discharge letters into the sections the
ePA expects. The model provides a draft; the decision stays with the physician. No draft reaches
the HIS without her approval.

The legal basis is Art. 9(2)(h) GDPR, provision of health care, together with the controller's
national law (in Germany, among others, Section 22(1)(1)(b) BDSG, and the ePA provisions of the
Social Code Book V). The example assumes this has been checked; in a real deployment the legal
department does it.

### Whose data, which data

| Data subjects | Data | Source |
|---|---|---|
| Patients | Name, date of birth, health insurance number, diagnoses, findings, medication, course of treatment — a special category under Art. 9 | The discharge letter, from the HIS |
| Third parties in the letter | Relatives, referring physicians, other hospitals | The letter's free text |
| Staff | Account (display name, identity provider subject), approvals and reasons given at the gate | The hospital's identity provider, Cohereny |

### How the data flows

1. **The HIS starts the run.** It sends the letter's text to `POST /v1/workflows/{id}/runs`
   using a technical account in the group `chy-service-accounts`. Under
   `identifiers_to_redact` it names what it knows about the case: the patient's name and date of
   birth, the relatives from her contact data, the referring physician. There is no PDF intake
   yet (CHY-174); the HIS sends text.
2. **Cohereny replaces before it stores.** Every text of the input is searched: the shipped
   patterns (among them the health insurance number and a labelled date of birth), the names
   given, and the scope's rules — here the staff's names and the format of case numbers, as a
   `redaction_rule` block. The run is stored with placeholders (`{{PERSON_1}}`). The mapping back
   to the values is encrypted per run; the hospital keeps the key outside the database.
3. **The context is composed** from the approved blocks of the scope: structure, instructions on
   medication, style. Blocks hold no patient data.
4. **The model drafts.** The scope's data class is `critical`, so Cohereny calls only models whose
   ceiling is `critical` — here a model on the hospital's own servers. It sees placeholders only.
   Identifiers it writes on its own are replaced before the answer is stored or traced.
5. **The physician reviews at the gate.** The workflow stops before the result (`human_review`).
   The ward physician reads the draft with placeholders and has the values put back to check that
   the right patient is meant; every such request is in the audit log. She approves or rejects; a
   rejection ends the run. Her reason, too, is stored with placeholders.
6. **The HIS takes the result.** After approval it resolves the run, as the account that started
   it, and writes the structured letter to the ePA. From here the HIS's and the ePA's assessments
   apply.
7. **The content expires.** The scope keeps content for seven days — by then the result is in the
   HIS. The nightly purge then clears input, outputs, reasons and the mapping. Who started the
   run, which model answered and who approved stay 183 days (Art. 26(6) AI Act as the lower
   bound); then the run is deleted.

### What lies where, and who reads it

| Where | What | Who reads it |
|---|---|---|
| Cohereny's database | Runs with placeholders; the mapping, encrypted; the audit log | Whoever started the run, the members of the scope "internal medicine" and of the scopes above it, `admin` — enforced by row-level security |
| A run's values | Through `POST /v1/workflows/runs/{id}/resolve` | The account that started it (here the HIS) and, from role `reviewer` up, whoever sees the run; each time audited |
| Model server | Prompt and answer, with placeholders | Hospital IT; the server stores no requests (checked by IT operations) |
| Langfuse | One trace per model call, with placeholders | Hospital IT; retention set to seven days there |
| Backups | The database, encrypted | Hospital IT; the key for the mappings is not in the same backup |

## 2 · Necessity and proportionality — Art. 35(7)(b)

- **Suitable and necessary:** Without a model, physicians structure every letter by hand. The
  draft saves that time; the medical decision stays theirs. A hosted model would need a processing
  agreement and would take the data out of the house — the data class `critical` rules that out.
- **Data minimisation:** The model sees no identifier Cohereny recognises. Runs are stored with
  placeholders, the values only encrypted. A name nobody names slipping through is a residual
  risk (section 5).
- **Storage limitation:** Seven days for content are enough because the approved result lives in
  the HIS. 183 days for the trace because the AI Act asks for at least six months of logs; the
  trace holds no health data.
- **No automated decision (Art. 22):** Every draft passes the gate; the workflow validator allows
  no path around it.
- **Data subject rights:** The patient information under Art. 13 names the use. Access (Art. 15)
  to the content of the last seven days is answered by resolving; after that it is cleared.
  Erasure per person before the period ends is not built yet (CHY-239) — at seven days the period
  is shorter than the month Art. 12(3) allows for an answer.

## 3 · Risks — Art. 35(7)(c)

Rated by likelihood and severity, each negligible, limited, significant or high. "Before" without
the measures of section 4, "after" with them.

| # | Risk to the data subjects | How it could happen | Severity | Likelihood before | Likelihood after |
|---|---|---|---|---|---|
| R1 | Disclosure to a model provider | A draft goes to a hosted model | high | significant | negligible — only models with ceiling `critical`; placeholders before every call |
| R2 | Disclosure to other staff | Someone outside internal medicine reads runs | significant | significant | negligible — row-level security by scope |
| R3 | Disclosure through a copy of the database | A backup or a dump is lost | high | limited | negligible — placeholders, the mapping encrypted, the key kept apart, volumes encrypted |
| R4 | A wrong draft reaches the ePA | The model invents a diagnosis or a dose | high | significant | limited — the physician's approval is mandatory; what remains is overlooking an error |
| R5 | People mixed up | A placeholder is matched to the wrong value | high | limited | negligible — one numbering per run, merged under a lock, tested; the physician resolves at the gate |
| R6 | A name nobody names is stored | The letter mentions a neighbour the HIS does not know | limited | significant | limited — it stays in the house (local model) but is stored as written for seven days |
| R7 | Resolving without cause | Someone with access reads values without a reason | significant | limited | negligible — starter and `reviewer` only; every request audited and reviewed monthly |
| R8 | Misuse of privileged access | Whoever holds both the key and database access reads everything | high | negligible | negligible — kept apart; no service account holds both |
| R9 | An erasure request that cannot be met | A patient asks for erasure | limited | limited | negligible — a seven-day period |

## 4 · Measures — Art. 35(7)(d)

**What Cohereny brings**, each with the test that shows it failing, is in
[DATA-PROTECTION.md, section 4](DATA-PROTECTION.md#4--measures--art-357d). For this case these
carry most of the weight:

- Placeholders before every model call of the workflow and in its stored runs; the mapping encrypted per run and bound
  to it (R1, R3, R5)
- The scope's data class and a ceiling per model; a scope nobody classified counts as `critical` (R1)
- Row-level security for runs; resolving only for the starter and from `reviewer` up, each time
  audited (R2, R7)
- A mandatory gate in every workflow; a rejection ends the run (R4)
- Periods per scope and a purge that applies them (R6, R9)

**What the hospital decides** (organisation and configuration):

| Measure | Against |
|---|---|
| Scope "internal medicine" with data class `critical`; only the model on the hospital's servers, ceiling `critical` | R1 |
| The HIS names every name it knows about the case under `identifiers_to_redact`; a `redaction_rule` block names the staff and the format of case numbers | R1, R6 |
| The scope's members are the department's physicians; approving from role `reviewer`; the HIS account in `chy-service-accounts` | R2, R7 |
| Content period seven days, trace 183 days; `cc retention purge` nightly, an alert when the last purge is older than 36 hours | R6, R9 |
| The key for the mappings in the secret store, an offline copy with the data protection office; never in the database backup | R3, R8 |
| A monthly review of the audit entries `workflow.run_content_resolved` by the data protection officer | R7 |
| Training: a draft is not a finding; whoever approves checks diagnoses and doses against the letter | R4 |
| No patient data in blocks — every signed-in user can read blocks (CHY-237) | R2 |

## 5 · Residual risks and outcome

| Residual risk | Rating | How it is handled |
|---|---|---|
| Names nobody names are stored as written for up to seven days (R6) | limited | Accepted: they do not leave the house, the period is short, and the HIS names what it knows. Recognising names without a list comes with CHY-235 |
| An error overlooked in an approved draft (R4) | limited | Accepted, with training and spot checks of approvals by the head of department |
| Whoever holds database and key access at once reads everything (R8) | negligible | Kept apart; the holders are named |
| Blocks readable by every signed-in user (CHY-237) | negligible | No personal data in blocks |

**Outcome (in this example):** After the measures no high risk remains. Prior consultation of the
supervisory authority under Art. 36 is not required. That decision is the controller's; if the
use changes — a hosted model, a longer period, another scope — the assessment is made again.

| | |
|---|---|
| Opinion of the data protection officer (Art. 35(2)) | Agreed, on condition that the monthly review of resolve entries is documented |
| Views of the data subjects (Art. 35(9)) | The patient advocate was heard; her request to name the use in the patient information is done |
| Approval | (name, role, date) |
| Next review | (date, at the latest a year after approval) |

## 6 · What the example tested in the template

The template carries the case: every section of this DPIA could be filled from
[DATA-PROTECTION.md](DATA-PROTECTION.md), its measures and their tests taken over, its checklist
worked through. The example showed three things:

1. **A name nobody names is stored, not only sent to the model.** The template said so for the
   model only. Corrected in DATA-PROTECTION.md, SECURITY.md and README.md together with this
   example.
2. **A technical account that starts a run may resolve it.** That is intended — the HIS needs the
   values for the ePA — and it means: whoever can use that account can read the values of every
   run it started. The template now lists it among the residual risks.
3. **Rating likelihood and severity** stays with the controller; the template supplies the risks,
   not the ratings. As intended: the same software carries a different risk in a study aid than in
   a hospital.
