# Cohereny

<img src="assets/branding/cohereny-logo.png" alt="cohereny" width="360"/>

**SHAPE · ADAPT · LEARN · REPEAT**

Self-hosted context middleware for multi-LLM workflows: a shared, versioned and
reviewed context repository, composed per workflow step and routed per model.

---

> ### ⚠️ Work in progress — use at your own risk
>
> Cohereny is under active development and has not had a 1.0 release.
> It is not ready for production use.
>
> - **No warranty.** Cohereny is provided as is, without warranty of any kind —
>   see sections 15 and 16 of the [AGPLv3](LICENSE.md).
> - **The API will change.** Endpoints, request and response shapes are not stable yet,
>   and changes may land without a deprecation period until 1.0.
> - **There is no support commitment.** No SLA and no backports. Security reports are
>   handled as described in [SECURITY.md](SECURITY.md).
> - **This repository is a published snapshot.** Development happens on a self-hosted
>   GitLab. `main` holds the releases, each pushed here as a new commit; `next` holds the tested
>   state between releases, updated after every deployment to the test stage — not a release, not
>   supported. Issues are welcome, pull requests cannot be merged yet — see
>   [CONTRIBUTING.md](CONTRIBUTING.md).
> - **One person, best effort.** Cohereny is built by one person, carefully and to the best of
>   my knowledge. In a system this complex I will still miss things. If you find one — a bug, a
>   gap in the security model, a claim in the documentation the code doesn't keep — please tell
>   me: open an issue, or for vulnerabilities follow [SECURITY.md](SECURITY.md). Feedback like
>   this is not just very welcome, it is essential.

---

## The problem

A prompt tuned for one model often does worse on another — not because the other model
is weaker, but because it expects a different structure, tone and format. Three effects
add up, and every model switch pays for them again:

- **Model variance:** the same prompt behaves differently across models
- **Model drift:** the same model behaves differently after an update
- **Prompt sensitivity:** small changes in wording change the result

In a workflow with several steps and several models, every step has its own context to
get right — and each one is exposed to all three at once.

## The approach

Cohereny keeps context outside the application code, in a repository every team shares:
system prompts, domain knowledge, examples, tool schemas, routing rules. Each piece is a
versioned block that goes live through review, scoped to an organization, a team, a
project or a single person. At runtime Cohereny composes what a workflow step needs and
hands it to the model the routing rules choose.

What one person learns about a model ends up in the repository, where everyone benefits
from it — after someone has looked at it.

## Why Cohereny exists

Cohereny grew out of experience from a private project and from comparing notes with
people I know: an AI system can run flawlessly, be fully logged and still quietly get worse
over weeks, because no control measures what actually comes out. I wanted to know whether
that could be done better, so I tried — because I could, because I enjoy it, and because I
wanted to find out in practice what works and what doesn't.

— Thomas Roth

## What is built

| Area | State |
|---|---|
| Context repository: versioned blocks, review lifecycle, scope-dependent approval (strict and direct scopes, promotion) | ✅ |
| Composition along the scope tree, with a privacy ratchet: a deeper scope may tighten an inherited privacy rule, never loosen it | ✅ |
| Routing rules as reviewed blocks; data classes per scope, model ceilings per model | ✅ |
| Four eyes where data changes place: a routing rule naming a model below `critical` and a loosened privacy rule need a second person in every scope; technical accounts never approve them | ✅ |
| Retention: every table assigned to a retention class; how long run content and run traces are kept is configured per scope through the admin API and audited, and `cc retention purge` applies it | ✅ |
| Identifiers replaced before every model call of `/v1/complete` and of a workflow: e-mail addresses, phone numbers, IBANs, German tax ids and health insurance numbers, labelled dates of birth, every value the caller names, and a scope's own rules as reviewed blocks. Model, gateway and trace see placeholders. Workflow runs are stored with placeholders, the mapping back to the values encrypted per run; whoever started a run, and from `reviewer` up whoever sees it, can have the values put back, audited. Names nobody lists in M7 | ✅ |
| Workflow runner: graph definitions, branching and joins, loops with a limit per edge — one that runs out goes on along its default or fails the run, and is recorded —, `human_review` gates with four-eyes approval; a prompt per step, composed from the blocks tagged for it; a model's answer checked against a JSON Schema per step, a mismatch stops the run; calculations as pure steps registered in the code, by name and version; documents of a run (PDF, text) uploaded by its starter, read once, scanned for instructions and stored with placeholders — a step sees one only through an edge from its node | ✅ |
| Stopping a run (AI Act Art. 14(4)(e)): whoever started it, and from `reviewer` up whoever sees it, ends it as `cancelled` — neither a failure nor a rejection at the gate, audited, a reason optional and stored with placeholders | ✅ |
| Gates nobody decides: past a gate's own time limit the run escalates along a connection to a gate that asks for at least as many people, or ends as `expired`; past the content period of its scope it ends as `expired` and its content is cleared. `cc oversight expire` applies it, audited without a person | ✅ |
| Review depth by risk (ADR-050): every gate asks for at least what the scope's data class and the workflow's risk class ask — a scope or workflow left unclassified asks for four eyes. Activation refuses a gate below it; at run time a stricter classification applies at once, and the audit log says per gate which level applied and why. Where both are low, a gate may be `sample`: the run goes on, a share of the passes is drawn into a review queue and judged afterwards | ✅ |
| Areas at the gate (MaRisk BTO 1.1): a gate names areas — front office, back office — and each casts one vote; differing votes escalate to a higher authority, which may be one area of its own such as a credit committee. Who belongs to which area is kept per scope by `admin`, revoked with a date, never deleted; the database refuses a vote without a current membership | ✅ |
| Runs visible only to whoever started them, to the members of the workflow's scope and of the scopes above, and to admin — enforced by row-level security, so a query that does not say for whom sees nothing. Blocks follow in M6 | ✅ |
| Append-only audit log, out of the application's reach: the API runs as a database role that owns no table and may not switch a trigger off, and refuses to start as more. Per-surface API (`serve`, `author`, `observe`, `admin`) behind OpenID Connect | ✅ |
| Application log: one JSON line per request on stderr — request id, route, status, duration, actor UUID; never a prompt, an answer or a document — and an `X-Request-ID` on every response. An error that leaves the installation, in an answer or a stored run, names no internal address; it carries the request id, and the details are in the log | ✅ |
| Drift detection: calibration anchors and measurement tables are in place; scheduled (nightly by default) and change-triggered runs with a trial run before approval in M6, signals from operation in M6, an embedding sensor for stale anchors in M7 | 🔧 |
| Adaptation of prompt and context per model, derived from the benchmark results (M6) | 📋 |
| One-command setup with Docker Compose: local model, development identity provider and a worked example, checked on every change ([deploy/quickstart](deploy/quickstart/)) | ✅ |
| The two LangGraph pilots (banking, study), `/v1/pilots` and `/v1/questions`: they call the model with the values and store them, unlike a workflow. Deprecated; they are rebuilt as workflows on the runner — banking as the credit analysis in [deploy/examples/banking](deploy/examples/banking/), study later. Personal data belongs in workflows or `/v1/complete` | ⚠️ |

✅ built · 🔧 in progress · 📋 planned · ⚠️ deprecated · M5–M8: milestones on the way to 1.0, listed in the
[whitepaper](https://github.com/cohereny/cohereny/blob/main/docs/cohereny-whitepaper.md), section 10

What changed from one snapshot of this repository to the next: [CHANGELOG.md](CHANGELOG.md)
(auf Deutsch: [CHANGELOG.de.md](CHANGELOG.de.md)).

## Tests

<!-- BEGIN generated by scripts/readme_tests.py -->
2,526 unit tests need no services; another 340 run against a real PostgreSQL.
The unit tests alone cover **91 %** of the lines:

| Directory | What it holds | Line coverage |
|---|---|---|
| `router/` | Routing rules | 98 % |
| `steps/` | Compute steps of workflows, pure functions | 98 % |
| `api/` | HTTP transport | 97 % |
| `kernel/` | Identity, roles, permissions, audit | 94 % |
| `core/` | Database pool, Redis, tracing | 92 % |
| `contexts/` | Context repository, orchestration, learning, pilot runs | 89 % |
| `cli/` | Command line | 60 % |
| **All of the above** | | **91 %** |

Measured on 2026-11-12 with Python 3.14, pytest 9.2 and coverage 7.17:
`pytest tests/ --cov`
<!-- END generated by scripts/readme_tests.py -->

The two LangGraph pilots are not measured: they are deprecated, being moved onto the
workflow runner, and go when that is done. The `quality` job on the self-hosted GitLab measures
coverage on every push and fails when it drops by more than half a point against
[`quality-baseline.json`](quality-baseline.json).

## Documentation

- [Whitepaper](https://github.com/cohereny/cohereny/blob/main/docs/cohereny-whitepaper.md) — what Cohereny is and why (German)
- [Architecture (arc42)](https://github.com/cohereny/cohereny/blob/main/docs/cohereny-arc42.md) — decisions and structure, with every ADR (German)
- [DEPENDENCIES.md](DEPENDENCIES.md) — the services Cohereny needs and how to configure them
- [CRYPTOGRAPHY.md](CRYPTOGRAPHY.md) — what Cohereny encrypts and verifies itself, what the operator provides, every secret involved
- [MODEL-DRIFT.md](MODEL-DRIFT.md) — the phenomenon in more detail (German)
- The API contract: [`openapi.yaml`](openapi.yaml), one contract per surface under [`contracts/`](contracts/)

The whitepaper and the arc42 documentation are in German; an English version is planned for v1.0 (CHY-66).

Comments and documents refer to internal tracker IDs such as `CHY-123` or `INFRA-45`.
The tracker is not public; the references stay because they tie each decision to its
record.

## Getting started

The quickest way is the one-command setup in [deploy/quickstart](deploy/quickstart/):
the API, a database, a cache, a local model and a development identity provider on one
machine, with a worked example already set up — scopes, blocks, routing rules and a
workflow in which a model drafts and a person approves.

```bash
git clone https://github.com/cohereny/cohereny.git
cd cohereny/deploy/quickstart
docker compose up
```

Then follow [its README](deploy/quickstart/README.md). It is for trying Cohereny out:
its identity provider issues a token to anyone who asks.

### Running it yourself

Requirements: Python 3.12 or newer, PostgreSQL 16+ and Redis 7+, an OpenID Connect
provider and a LiteLLM gateway — see [DEPENDENCIES.md](DEPENDENCIES.md). The provider is
tested with Authentik; issuers and signing keys are expected where Authentik publishes
them, one application per API surface.

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env            # set DATABASE_URL, REDIS_URL, OIDC_BASE_URL, LITELLM_BASE_URL,
                                # PII_MAPPING_KEYS (DEPENDENCIES.md)

# The database, then two roles in it — once, with a superuser connection (DEPENDENCIES.md)
createdb -h localhost -U postgres cohereny
DB_SUPERUSER_URL=postgresql://postgres:<password>@localhost:5432/postgres \
  COHERENY_MIGRATOR_PASSWORD=<password> COHERENY_RUNTIME_PASSWORD=<password> \
  python -m migrations.roles --database cohereny
DATABASE_URL=postgresql://cohereny_migrator:<password>@localhost:5432/cohereny \
  python -m migrations.runner     # migrations run as the owner of the tables
uvicorn api.main:app --port 8000  # the API as cohereny_runtime, from .env
```

The API refuses to start when `DATABASE_URL` names the superuser or the owner of the
tables: the triggers that keep the audit log append-only hold only against a role that is
neither.

#### First steps

An empty database has no admin, no scope and no routing rule, so nothing can be routed
yet.

1. Make yourself admin. Roles come from the token's groups claim on every request, so
   with Authentik add your account to the group `chy-admins`. Only for a token without a
   groups claim does `cc actor set-role <subject> admin` set the role directly.
2. Create a scope (`POST /v1/scopes` on the `author` surface).
3. Write the default routing rules into it:
   `python scripts/seed_routing_rules.py --scope <scope-id> --author <actor-id> --apply`.
   Without `--apply` it prints what it would write. It reads `DATABASE_URL` from the
   environment, not from `.env`, so export it first. `<actor-id>` is your own actor, for
   example the `created_by` of the scope from step 2. The default rules name models of
   the reference installation; adapt `router/routing-config.yaml` and the drafts to the
   model IDs your LiteLLM gateway serves.
4. The rules are written as drafts. Submit and approve them
   (`POST /v1/blocks/{block_id}/submit`, then `/approve`). In a strict scope
   (organization, team, workspace) approving needs the `reviewer` role. A rule that
   names a model below `critical` is approved by someone other than its writer; while
   you are the only person who may approve, you may approve it yourself, and the
   response tells you what that means — see the known limitations in
   [SECURITY.md](SECURITY.md).
5. Add the people who review runs to the scope: each reads their id from `GET /v1/me`,
   and you add it with `POST /v1/scopes/{scope_id}/members`. A run is visible to whoever
   started it, to the members of its workflow's scope and of every scope above, and to
   admin — a reviewer who is not a member does not find the run they are to decide.

The quickstart takes these steps for its example, through the API ([deploy/quickstart/seed.py](deploy/quickstart/seed.py)).

#### Retention

What a run took in and gave back is cleared after 30 days; the run itself —
who started it, which model answered, who decided at the gate — is deleted
after 183 days. Both are defaults, set per scope where a scenario needs
otherwise (`PUT /v1/admin/retention/{run_content|run_trace}` on the `admin`
surface; `GET /v1/admin/retention` shows every table, every period and where
it comes from). Nothing applies them until the purge runs:

```bash
cc retention purge --dry-run   # what would go, nothing changes
cc retention purge             # clears and deletes, writes retention.purged
```

The purge leaves a run waiting at a gate alone: its content is what the
reviewer has to read. A run nobody decides is ended by a second command —
once the content period has passed since it reached the gate, or earlier by
the gate's own time limit ([guide](deploy/quickstart/guides/workflows.md#reviews)):

```bash
cc oversight expire --dry-run  # what would expire or escalate, nothing changes
cc oversight expire            # ends or escalates them, each audited
```

Both are in the API image; with the quickstart, `docker compose exec api cc
oversight expire`. Start them from a timer, not by hand — a systemd timer, a
cron job or a Kubernetes CronJob, once a day. Every purge writes an audit
entry, also when nothing expired, and `GET /v1/admin/retention` reports the
newest as `last_purge_at`: alarm on its age, not on the job's exit code.

#### Identifiers before the model

Before every model call — `/v1/complete` and every `llm_call` of a run —
Cohereny replaces identifiers with placeholders. The model, the LiteLLM gateway
and the Langfuse trace see `{{EMAIL_1}}`, never the address. `/v1/complete`
puts the values back into the answer it returns. Nothing else is covered: the
deprecated LangGraph pilots call the model with the values, and `/v1/route` only
names a model — a client that then calls the gateway itself sends what it sends.

A workflow run keeps its placeholders: its input is replaced before the run is
stored, and every step reads and writes placeholders, the reasons given at the
gate included. What is not recognised — a name nobody lists — stays as written,
in the run as on the way to the model. The mapping back to the values is encrypted per run, with a key
the database never sees (`PII_MAPPING_KEYS`, [CRYPTOGRAPHY.md](CRYPTOGRAPHY.md)),
and cleared with the run's content. Whoever started a run, and from `reviewer`
up whoever sees it, gets the values back with
`POST /v1/workflows/runs/{id}/resolve`; every call is audited, never with a
value.

Found without being named: e-mail addresses, phone numbers, IBANs, German tax
ids and health insurance numbers, and a date of birth when it is labelled as
one ("geb.", "Geburtsdatum", "date of birth"). A name is found only when it is
named:

```json
{"scope_id": "…", "task_type": "summarize", "prompt": "…",
 "identifiers_to_redact": {"PERSON": ["Erika Mustermann"], "CASE_NUMBER": ["4 C 123/26"]}}
```

For a run, the same object goes into its input under `identifiers_to_redact`
and applies to every step of the run. It is not stored with the input: each
listed value goes into the run's encrypted mapping and is searched for in every
step, whether the input contained it or not. What a scope always replaces — the
names of its staff, the format of its case numbers — it states once, as a
`redaction_rule` block that every scope below inherits
([guide](deploy/quickstart/guides/scopes-and-blocks.md#what-the-model-never-sees)). The response counts what was
replaced (`redactions`) and how many placeholders the model changed so that
they could not be put back (`unresolved_placeholders`); those stay as the model
wrote them. What this does not catch is listed in [SECURITY.md](SECURITY.md).

### Running the tests

```bash
pytest tests/ pilots/banking/tests pilots/study/tests    # unit tests, no services needed
pytest -m integration       # against a real PostgreSQL, as the runtime role
```

The integration tests expect the roles and migrations in place, as in the `integration`
job: `DATABASE_URL` as the runtime role, `OWNER_DATABASE_URL` as the migration role.

## Stack

| Layer | Technology |
|---|---|
| Middleware | Python 3.12+, FastAPI |
| Context store | PostgreSQL (`ltree` for the scope tree) |
| Cache | Redis |
| Identity | OpenID Connect (tested with Authentik) |
| Model access | LiteLLM — local models through a runtime such as Ollama, hosted models where a scope's data class allows |
| Tracing | Langfuse (optional) |

## How this code is written

Cohereny is built the way a proper software project should be, even though one person works
on it:

- coding conventions that are checked automatically ([CONVENTIONS.md](CONVENTIONS.md), German)
- tests with every change, and for each safeguard a check that its test fails once the
  safeguard is removed
- no commit without a ticket, no merge without review
- architecture decisions recorded as ADRs in the
  [arc42 documentation](https://github.com/cohereny/cohereny/blob/main/docs/cohereny-arc42.md)

Much of this code is written together with [Claude Code](https://claude.com/claude-code),
Anthropic's coding assistant. The maintainer reviews every change before it is merged and
approves or rejects it, and is responsible for all of it: the code, the tests and the
decisions recorded in the architecture documentation.

In the self-hosted repository, commits record the assistant's part in a `Co-Authored-By`
trailer. This repository holds snapshots of that work, not its history.

## Licensing

Cohereny uses **dual licensing**:

| License | File | Who it covers |
|---|---|---|
| **GNU AGPL v3.0** | [LICENSE.md](LICENSE.md) | Everyone — open source; copyleft applies, also to a modified version run as a network service |
| **Commercial license** | [COMMERCIAL-LICENSE.md](COMMERCIAL-LICENSE.md) | Use without the copyleft obligations — on request |

The open-source license is version 3 of the AGPL only (SPDX: `AGPL-3.0-only`).

## Contributing and security

- How to contribute, and why pull requests cannot be merged yet: [CONTRIBUTING.md](CONTRIBUTING.md)
- Coding conventions (German): [CONVENTIONS.md](CONVENTIONS.md)
- Reporting a vulnerability: [SECURITY.md](SECURITY.md) — please don't open a public issue

## Maintainer

Cohereny is a private project of Thomas Roth, built in his own time, on his own hardware
and with his own accounts and licenses. It is not owned, funded or developed by any company.

Thomas Roth — [cohereny.com](https://cohereny.com) · contact@cohereny.com
