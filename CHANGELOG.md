# Changelog

What changed from one snapshot of this repository to the next, written for people who run
Cohereny or build on it. Newest first. Auf Deutsch: [CHANGELOG.de.md](CHANGELOG.de.md).

Cohereny is pre-release. A snapshot is a published state of the development repository, not
a versioned release; each one has a pre-release on GitHub named after its date.

## 2026-11-08

### Added
- Workflow runs are stored with placeholders. Identifiers in a run's input, in every step's input and output and in the reasons given at a gate are replaced before they are stored; the mapping back to the values is encrypted per run under a key the database never sees. Whoever started a run, and from `reviewer` up whoever sees it, can have the values put back with `POST /v1/workflows/runs/{run_id}/resolve`, and every time is audited. The API needs `PII_MAPPING_KEYS` and does not start without it ([CRYPTOGRAPHY.md](CRYPTOGRAPHY.md)). Runs started before this update keep their values until their content period ends ([DATA-PROTECTION.md](DATA-PROTECTION.md)).
- Identifiers are replaced before every model call of `/v1/complete` and of a workflow: e-mail addresses, phone numbers, IBANs, German tax ids and health insurance numbers, labelled dates of birth, the values a caller lists in `identifiers_to_redact`, and a scope's own rules as reviewed `redaction_rule` blocks. The model, the gateway and the trace see placeholders; `/v1/complete` returns the answer with the values. A name nobody lists is not recognised yet.
- `POST /v1/workflows/runs/{run_id}/cancel` stops a run that has not finished (AI Act Art. 14(4)(e)), for whoever started it and, from `reviewer` up, whoever sees it. The run ends as `cancelled`, not as a rejection at the gate.
- Retention periods, installation-wide or per scope: `GET /v1/admin/retention`, `PUT /v1/admin/retention/{retention_class}`. `cc retention purge` clears a run's content when its period ends (default 30 days) and deletes its trace later (default 183 days). Run it on a schedule; nothing is deleted otherwise.
- Four eyes where data changes place: a routing rule that names a model below `critical`, and a block that loosens a privacy rule, are approved by someone who wrote nothing in that version. Accounts in the identity-provider group `chy-service-accounts` never approve them ([SECURITY.md](SECURITY.md)).
- For operators: [DATA-PROTECTION.md](DATA-PROTECTION.md), what Cohereny contributes to a data protection impact assessment, each measure with the test that shows it failing; a filled-in example for a hospital ([DATA-PROTECTION-EXAMPLE.md](DATA-PROTECTION-EXAMPLE.md), deutsch: [DATA-PROTECTION-EXAMPLE.de.md](DATA-PROTECTION-EXAMPLE.de.md)); and the cryptography concept ([CRYPTOGRAPHY.md](CRYPTOGRAPHY.md)).

### Changed
- Migrations only go forward. There are no down migrations; the way back from an update is the backup taken before it ([DEPENDENCIES.md](DEPENDENCIES.md)).
- The two LangGraph pilots, `/v1/pilots` and `/v1/questions`, are deprecated. Unlike a workflow they call the model with the values and store them; the API contract marks each of their operations `deprecated`. They are rebuilt as workflows on the runner. Until then, personal data belongs in workflows or `/v1/complete` ([DATA-PROTECTION.md](DATA-PROTECTION.md)).
- A quickstart updates with `git pull` and `docker compose up`: every start builds the image from the source, and the database keeps its data and is migrated. On a quickstart from 2026-11-04 the seed makes `reviewer` a member of Example Corp, without which `reviewer` would no longer see the runs (see Security) ([deploy/quickstart](deploy/quickstart/README.md#update)).

### Fixed
- A released workflow could be set back to `draft`, and its graph then changed underneath the runs that used it. That is refused with `409` now; a released workflow changes by a new version.
- A pilot run cancelled while it waited at its gate was booked as a rejection and stayed `running` without a result. It now ends as `failed`, with the cancellation as its message.

### Security
- Runs are readable only by whoever started them, by the members of the workflow's scope and of the scopes above, and by `admin`; row-level security enforces it in the database. Until now every signed-in user could read every run. Add the people who review runs to the scopes they work in (`POST /v1/scopes/{scope_id}/members`), or they no longer see them.
- The API runs with a database role of its own, `<database>_runtime`; `<database>_migrator` owns the tables and runs the migrations, and the API refuses to start as a superuser or an owner. Until now it could switch off the triggers that keep the audit log append-only. Existing installations create both roles once with `python -m migrations.roles` and point the API's `DATABASE_URL` at the runtime role ([DEPENDENCIES.md](DEPENDENCIES.md)).

## 2026-11-04

### Added
- A one-command quickstart in [deploy/quickstart](deploy/quickstart/): the API, a database, a cache, a local model (Ollama with `qwen2.5:1.5b`) and a development identity provider on one machine, with a worked example — scopes, blocks, routing rules and a workflow in which a model drafts a reply and a person approves it. A README and five guides; the walkthrough runs as a test on every change.
- `GET /v1/workflows/runs/{run_id}/nodes` shows what each node of a run produced. Whoever decides at a `human_review` gate can read the draft first; until now they decided without seeing it.
- `ROUTING_CONFIG` names the file that declares your models (`routing-config.yaml`). Until now that meant editing the file in the source tree.
- An `llm_call` node can take its system message through `input_mapping`, so a workflow hands a model the composed context and the run's input together, as `/v1/complete` does.
- `API_DOCS` serves `/docs`, `/redoc` and `/openapi.json` (off by default). `PILOT_NODE_METRICS` switches the banking pilot's token metrics.
- `scripts/dev_issuer.py`, a local stand-in for the identity provider in development and CI. It issues a token to anyone who asks; never run it where real data is.

### Changed
- Tokens are verified with PyJWT instead of python-jose. `exp`, `iss`, `aud` and `sub` are required; a token without one of them is refused.

### Removed
- `DEV_MODE`. If an `.env` still sets it, take it out: the API refuses to start with a key it does not know. To develop without an identity provider, use `scripts/dev_issuer.py` ([DEPENDENCIES.md](DEPENDENCIES.md)).

### Security
- No setting turns authentication off any more. Every route except `/v1/health` answers `401` without a token; a test walks all routes.
- The surface gate refuses a caller without a token audience instead of letting it through.
- An identity provider over plain `http://` is accepted only with `ENVIRONMENT=dev`.

## 2026-11-03

### Added
- Every change produces a software bill of materials (CycloneDX), checked against the declared dependencies, with a supplier for each component. Available on request ([SECURITY.md](SECURITY.md)).
- Architecture documentation: drift detection is a goal for 1.0 (ADR-044) — runs on a schedule and on every change, signals from operation, a sensor for stale anchors; a person judges every finding.

### Fixed
- A file name containing a NUL byte is answered with `404`, not `500`.

## 2026-11-01

### Added
- First publication: the middleware, its tests, the API contracts per surface, the architecture documentation (in German) and the dual licence (AGPL-3.0-only or commercial).
