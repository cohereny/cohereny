# Cohereny — Platform Dependencies

Cohereny is a **headless service** that relies on externally provided infrastructure,
configured through environment variables. It does not care where these services run;
it needs their addresses and credentials, nothing else.

All addresses below use `example.com`, the domain reserved for documentation. The
defaults in `api/config.py` are the same placeholders on purpose: a deployment that
forgets to set a value fails visibly instead of talking to somebody else's service.

---

## Required services

### PostgreSQL — state and context repository

PostgreSQL 16 or later, with the extensions `uuid-ossp` and `ltree` (the migrations
create them; the database user needs the right to do so, or an administrator creates
them beforehand). Migrations run from `migrations/` with `python -m migrations.runner`.

```env
DATABASE_URL=postgresql://cohereny:<password>@db.example.com:5432/cohereny
```

### Redis — compiled-context cache

Redis 7 or later. One database per deployment stage. Redis holds a cache, not state:
if it is unreachable, an approval still commits, and a composition may be served stale
for at most an hour (`CACHE_TTL_SECONDS`, ADR-010).

```env
REDIS_URL=redis://cache.example.com:6379/0
```

### OpenID Connect provider — authentication and roles

Tested with [Authentik](https://goauthentik.io). The API has four consumer surfaces —
`serve`, `author`, `observe`, `admin` — and each is its own OIDC application with its own
issuer and audience (ADR-031):

| Surface | Application slug | Audience |
|---|---|---|
| serve | `cohereny-serve` | `cohereny.serve` |
| author | `cohereny-author` | `cohereny.author` |
| observe | `cohereny-observe` | `cohereny.observe` |
| admin | `cohereny-admin` | `cohereny.admin` |

Machine clients for automated checks use the slug `cohereny-<surface>-ci` and the
audience `cohereny.<surface>.ci`. Only the base URL is configured; every issuer is derived
from it as `<base>/<slug>/` (`kernel/surfaces.py`).

```env
OIDC_BASE_URL=https://auth.example.com/application/o
```

Roles come from group membership: `chy-consumers`, `chy-contributors`, `chy-reviewers`,
`chy-architects`, `chy-admins` (`kernel/roles.py`).

### LiteLLM — model gateway

All model calls go through an OpenAI-compatible [LiteLLM](https://github.com/BerriAI/litellm)
gateway. Which model handles which task is decided by approved routing rules in the
context repository (ADR-038), not by this setting.

```env
LITELLM_BASE_URL=https://litellm.example.com/v1
LITELLM_API_KEY=            # only if the gateway has authentication enabled
```

The model names the router knows, and the data class each may receive, are listed in
`router/routing-config.yaml` (ADR-041).

---

## Optional services

### Langfuse — tracing

Self-hosted [Langfuse](https://langfuse.com) traces every model call. Without keys,
tracing stays off.

```env
LANGFUSE_HOST=https://langfuse.example.com     # https: see tests/test_internal_urls.py
LANGFUSE_PUBLIC_KEY=
LANGFUSE_SECRET_KEY=
```

---

## Topology

```
cohereny-api
  ├── → PostgreSQL     state, context repository, audit log
  ├── → Redis          compiled-context cache
  ├── → OIDC provider  issuers and signing keys (JWKS)
  ├── → LiteLLM        model calls
  └── → Langfuse       traces (optional)
```

The complete list of settings, with defaults and the reasoning behind each, is in
`api/config.py`; `.env.example` is the template.

---

## Architecture boundary

Cohereny **uses** these services but does not **manage** them. Secrets belong in the
deployment's secret store, never in this repository.
