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
>   GitLab; each snapshot is pushed here as a new commit. Issues are welcome, pull requests cannot be
>   merged yet — see [CONTRIBUTING.md](CONTRIBUTING.md).
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
| Workflow runner: graph definitions, branching and joins, `human_review` gates with four-eyes approval | ✅ |
| Append-only audit log — guards against errors in the application; against its own database role from M5 (CHY-85). Per-surface API (`serve`, `author`, `observe`, `admin`) behind OpenID Connect | ✅ |
| Drift detection: calibration anchors and measurement tables are in place; scheduled (nightly by default) and change-triggered runs with a trial run before approval in M6, signals from operation in M6, an embedding sensor for stale anchors in M7 | 🔧 |
| Adaptation of prompt and context per model, derived from the benchmark results (M6) | 📋 |
| One-command setup with Docker Compose: local model, development identity provider and a worked example, checked on every change ([deploy/quickstart](deploy/quickstart/)) | ✅ |

✅ built · 🔧 in progress · 📋 planned · M5–M8: milestones on the way to 1.0, listed in the
[whitepaper](https://github.com/cohereny/cohereny/blob/main/docs/cohereny-whitepaper.md), section 10

What changed from one snapshot of this repository to the next: [CHANGELOG.md](CHANGELOG.md)
(auf Deutsch: [CHANGELOG.de.md](CHANGELOG.de.md)).

## Tests

1,382 unit tests need no services; another 70 run against a real PostgreSQL.
The unit tests alone cover **91 %** of the lines outside the two pilots:

| Directory | What it holds | Line coverage |
|---|---|---|
| `router/` | Routing rules | 98 % |
| `kernel/` | Identity, roles, permissions, audit | 97 % |
| `api/` | HTTP transport | 94 % |
| `contexts/` | Context repository, orchestration, learning, pilot runs | 91 % |
| `core/` | Database pool, Redis, tracing | 90 % |
| `cli/` | Command line | 49 % |
| **All of the above** | | **91 %** |
| `pilots/banking/`, `pilots/study/` | The two original pilots | 19 %, 10 % |
| **Including the pilots** | | **70 %** |

The pilots were written before the workflow runner and are being moved onto it; they are
what pulls the overall figure down. The `quality` job on the self-hosted GitLab measures
coverage on every push and fails when it drops by more than half a point against
[`quality-baseline.json`](quality-baseline.json).

Measured on 2026-10-30 with Python 3.14, pytest 9.1 and coverage 7.15:
`pytest tests/ pilots/banking/tests pilots/study/tests --cov`

## Documentation

- [Whitepaper](https://github.com/cohereny/cohereny/blob/main/docs/cohereny-whitepaper.md) — what Cohereny is and why (German)
- [Architecture (arc42)](https://github.com/cohereny/cohereny/blob/main/docs/cohereny-arc42.md) — decisions and structure, with every ADR (German)
- [DEPENDENCIES.md](DEPENDENCIES.md) — the services Cohereny needs and how to configure them
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
cp .env.example .env            # set DATABASE_URL, REDIS_URL, OIDC_BASE_URL, LITELLM_BASE_URL
python -m migrations.runner
uvicorn api.main:app --port 8000
```

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
   (organization, team, workspace) approving needs the `reviewer` role. Author and
   approver are both recorded; that they are different people is not enforced yet — see
   the known limitations in [SECURITY.md](SECURITY.md).

The quickstart takes these steps for its example, through the API ([deploy/quickstart/seed.py](deploy/quickstart/seed.py)).

### Running the tests

```bash
pytest tests/ pilots/banking/tests pilots/study/tests    # unit tests, no services needed
python -m migrations.runner && pytest -m integration     # against a real PostgreSQL
```

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
