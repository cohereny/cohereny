# Changelog

What changed from one snapshot of this repository to the next, written for people who run
Cohereny or build on it. Newest first. Auf Deutsch: [CHANGELOG.de.md](CHANGELOG.de.md).

Cohereny is pre-release. A snapshot is a published state of the development repository, not
a versioned release; each one has a pre-release on GitHub named after its date.

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
