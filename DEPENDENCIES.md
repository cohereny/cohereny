# Cohereny — Platform Dependencies

Cohereny is a **headless service** that relies on externally provided infrastructure,
configured through environment variables. It does not care where these services run;
it needs their addresses and credentials, nothing else.

To try Cohereny without providing any of this, use the one-command setup in
[deploy/quickstart](deploy/quickstart/): it brings all of it along on one machine.

All addresses below use `example.com`, the domain reserved for documentation. The
defaults in `api/config.py` for the identity provider, the model gateway and the trace
are the same placeholders on purpose: a deployment that forgets to set one fails visibly
instead of talking to somebody else's service. Database and cache default to `localhost`.

---

## Required services

### PostgreSQL — state and context repository

PostgreSQL 16 or later, with the extensions `uuid-ossp` and `ltree` (the migrations
create them; both are trusted extensions, so the database owner may).

Cohereny uses two roles per database and never connects as a superuser (CHY-85):

| Role | Does | Used by |
|---|---|---|
| `<database>_migrator` | Owns the database and every table; runs the migrations | `python -m migrations.runner`, nothing else |
| `<database>_runtime` | What the application may do, table by table — no ownership, no DDL, no `BYPASSRLS` | The API, `cc retention purge`, `cc oversight expire`, `cc actor` |

`python -m migrations.roles` creates both, once, with a superuser connection, and hands
an existing database over to the migration role. The database itself must exist: create
it empty first (`createdb cohereny`); the script refuses a name it does not find. It reads the passwords from the
environment (`<DATABASE>_MIGRATOR_PASSWORD`, `<DATABASE>_RUNTIME_PASSWORD`); running it
again sets them again, which is how they are rotated. Then the migrations run as the
migration role, and the API as the runtime role:

```sh
DB_SUPERUSER_URL=postgresql://postgres:<password>@db.example.com:5432/postgres \
  COHERENY_MIGRATOR_PASSWORD=<password> COHERENY_RUNTIME_PASSWORD=<password> \
  python -m migrations.roles --database cohereny
DATABASE_URL=postgresql://cohereny_migrator:<password>@db.example.com:5432/cohereny \
  python -m migrations.runner
```

```env
DATABASE_URL=postgresql://cohereny_runtime:<password>@db.example.com:5432/cohereny
```

The API refuses to start when `DATABASE_URL` names a role with more rights: a superuser,
the owner of the tables, a role with `BYPASSRLS` or with `CREATE` on the schema. Run the
migrations before starting a new version, not from inside the API's container: the
owner's credentials do not belong there.

**Back up before every update; there is no way back down.** Migrations only go forward. None
has a down step, and the image of an older version does not undo a migration — its code may
not run against the newer schema. The way back is the backup taken before the update: the
database (`pg_dump`), the configuration, and the key for run mappings (`PII_MAPPING_KEYS`,
[CRYPTOGRAPHY.md](CRYPTOGRAPHY.md)), kept apart from the database backup. A mistake in a
released migration is corrected by a later one, never by changing or reverting the old one. `CONNECT` on the database is withheld from every
other role; revoke it on the server's maintenance database (`postgres`) too if other
roles share the server.

### Redis — compiled-context cache

Redis 7 or later. One database per deployment stage. Redis holds a cache, not state:
if it is unreachable, an approval still commits, and a composition may be served stale
for at most an hour (fixed: `CACHE_TTL_SECONDS` in `contexts/repository/compose.py`, ADR-010).

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

The provider is required on every stage; there is no setting that runs the API without
authentication. For local development and CI, `scripts/dev_issuer.py` stands in for it:
it serves the eight issuers and issues tokens in the shape above, without checking
passwords. The API accepts an issuer over plain http only with `ENVIRONMENT=dev`.

```env
ENVIRONMENT=dev
OIDC_BASE_URL=http://127.0.0.1:9010/application/o
```

### LiteLLM — model gateway

All model calls go through an OpenAI-compatible endpoint: a
[LiteLLM](https://github.com/BerriAI/litellm) gateway in the tested setup, Ollama's own
`/v1` in the quickstart. Which model handles which task is decided by approved routing
rules in the context repository (ADR-038), not by this setting.

```env
LITELLM_BASE_URL=https://litellm.example.com/v1
LITELLM_API_KEY=            # only if the gateway has authentication enabled
```

The model names the router knows, and the data class each may receive, are declared in
a `routing-config.yaml` (ADR-041). `ROUTING_CONFIG` names the file; empty uses
`router/routing-config.yaml`, which declares the reference installation's models.

```env
ROUTING_CONFIG=/etc/cohereny/routing-config.yaml
```

### The key for run mappings

A workflow run is stored with placeholders, and the mapping back to its values is
encrypted per run (CHY-234). The key is the installation's, and Cohereny does not start
without one. Generate it once, keep it in the secret store, and back it up apart from the
database: whoever holds both reads every value, and whoever loses the key loses the values
of the runs it sealed. Rotation and the details: [CRYPTOGRAPHY.md](CRYPTOGRAPHY.md#secrets-and-keys).

```bash
python -c 'from kernel.mapping_cipher import generate_key; print(generate_key("k1"))'
```

```env
PII_MAPPING_KEYS=k1:<32 bytes, base64url>   # newest first; every listed key opens
```

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
