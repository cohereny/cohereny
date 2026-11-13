# Cryptography

What Cohereny does with cryptography itself, what it expects from the installation around
it, and every secret involved. Written for whoever runs Cohereny, and structured after the
cryptography concept of BSI IT-Grundschutz (CON.1).

The division of labour is decided in ADR-014 (arc42): **Cohereny does not encrypt data at
rest; the operator chooses the measure.** Disk encryption, TLS termination and the secret
store are properties of an installation, and the right choice differs between a laptop, a
data centre and a cloud account. What Cohereny owes the operator is this document — what it
relies on, and where it stops.

One exception, narrow on purpose (CHY-234): the mapping from a workflow run's placeholders
back to its values. It is the part of a run that is nothing but personal data, and disk
encryption does not help against a dump taken by someone with database access.

## Who does what

| Concern | Cohereny | The operator |
|---|---|---|
| Authentication | Verifies tokens (below) | Runs the identity provider, issues the tokens, holds the signing key |
| Traffic to the API | Speaks HTTP behind a proxy | Terminates TLS in front of it |
| Traffic from the API | Uses the URLs it is given | Gives `https://`, `sslmode=verify-full` and `rediss://` URLs |
| Data at rest | Encrypts one thing: each workflow run's placeholder mapping (below) | Encrypts the volumes of the database, the cache and the backups |
| Secrets | Reads them from the environment | Fills the environment from a secret store, rotates the secrets |
| Which model sees which data | Enforces the data class of the scope (ADR-041) | Classifies the scopes, declares each model's ceiling |

## What Cohereny does itself

- **Token verification.** Every request except `GET /v1/health` carries a bearer token —
  and, where a deployment switches on the interactive documentation with `API_DOCS=true`,
  `/docs`, `/redoc` and `/openapi.json`, which show the contract and no data.
  Cohereny accepts RS256 only — the algorithm is pinned, so a token cannot choose `none` or
  HS256 — and requires `exp`, `iss`, `aud` and `sub`. The issuer must be one of the
  installation's known issuers and the audience must be the surface called (ADR-031). The
  public keys come from that issuer's JWKS over `https`; plain `http` is accepted only with
  `ENVIRONMENT=dev`. Keys are cached for 30 minutes per issuer. An RSA key shorter than
  2048 bits is never used: a token signed with one is refused with `503` and the key id in
  the application log — the identity provider has to rotate, not the caller (CHY-273).
- **One key of its own: the run mappings** (CHY-234, `kernel/mapping_cipher.py`). A workflow
  run is stored with placeholders, and the mapping back to the values is sealed per run with
  AES-256-GCM (`cryptography`), under a key from `PII_MAPPING_KEYS` that the database never
  sees. The run's id is associated data, so a mapping copied onto another run's row does not
  open there; a changed byte fails the decryption instead of yielding another mapping; every
  sealing draws a fresh 96-bit nonce. Each row names the key that sealed it, which makes
  rotation possible (see the inventory). The API does not start without a valid key.
- **Nothing else.** Cohereny signs nothing and stores no passwords. Who a person is, and how
  they prove it, belongs to the identity provider.
- **Identifiers are not secrets.** IDs are random UUIDs (version 4). Access is decided by
  the token and the role, never by whether someone can guess an ID.
- **The audit log is protected by the database, not by cryptography.** Triggers refuse
  `UPDATE`, `DELETE` and `TRUNCATE` on it, and the application's database role holds none
  of these rights, does not own the table and cannot switch a trigger off (CHY-85). There
  is no hash chain: whoever holds the migration role or the superuser can still change
  history, so those two credentials never reach the API (see below).
- **Where data may go is decided by classification, not by encryption.** Each scope has a
  data class (`public`, `internal`, `confidential`, `critical`; the strictest on the chain
  applies), each model a ceiling, and a model below the class is never called (ADR-041).
  A routing rule that sends data to a model below `critical` needs a second person to
  approve it (ADR-045).

## Protection need

The protection need follows the **data class of the scope** the data belongs to: data of a
`critical` scope needs the protection the operator gives critical data, wherever it is
stored. Where it is stored is listed by retention class (ADR-046);
`GET /v1/admin/retention` shows every table and its period.

| Retention class | What it holds | Personal data |
|---|---|---|
| `run_content` | Inputs and outputs of runs, free-text reasons and comments — a workflow run's with placeholders, its mapping sealed (above); a pilot run's with the values; a direct call drawn for review, with placeholders and no mapping (CHY-269) | Likely — this is what the models read and write |
| `run_trace` | Who started a run, which model answered, who decided at the gate | Pseudonymous actor references |
| `audit` | Every governance action | Pseudonymous actor references, decision reasons |
| `accounts` | Actors and scope memberships | Display name and identity-provider subject, until anonymized |
| `context` | Context blocks, governance keys, learning proposals | Curated material; the author as an actor reference |
| `measurements` | Benchmark runs and measurements | Curated test cases, not user data |
| `structure` | Scopes, workflow definitions, configuration | No |

Outside the database: Langfuse traces hold the same content as `run_content`; the Redis
cache holds composed contexts for a short time; the pilots' input files are read from
`PILOT_INPUT_DIR`.

## What the operator provides

### In transit

- **In front of the API:** a reverse proxy that terminates TLS (1.2 at least, 1.3
  preferred). The API itself speaks plain HTTP and must not be reachable around the proxy.
- **To the database:** `DATABASE_URL` with `?sslmode=verify-full` whenever PostgreSQL is
  not on the same host.
- **To the cache:** `REDIS_URL` as `rediss://` with a password whenever Redis is not on the
  same host.
- **To the gateway, the identity provider and Langfuse:** `https://`. The configuration
  templates use it. Outside `ENVIRONMENT=dev` the API refuses `http://` for the identity
  provider; for the gateway and Langfuse it still accepts it, and nothing yet refuses an
  unencrypted database or cache connection — a mode that does is planned (see below).

### At rest

- **Database and cache:** encrypt the volumes of PostgreSQL and of Redis persistence —
  LUKS/dm-crypt, or the platform's disk encryption.
- **Backups:** encrypted (restic, for instance, encrypts its repository) and kept off the
  host they protect.
- **Langfuse:** its storage holds prompts and outputs; encrypt it the same way and set its
  retention there.
- **What this does not cover:** volume encryption protects a stolen disk and discarded
  hardware, not the running system. Whoever holds the database credentials reads
  everything, and so does a database dump — except a workflow run's values, which need the
  mapping key as well. Keep that key apart from the database and its backups, or it protects
  nothing.

### Secrets

- From the environment, filled from a secret store (the reference installation uses SOPS
  with age). Never in the database, never in the image, never in a repository unencrypted.
- **The identity provider's signing key:** Cohereny caches the public keys for 30 minutes
  and does not yet fetch them again when a token names an unknown key. After a rotation,
  new tokens fail for up to 30 minutes — restart the API after rotating, which empties the
  cache, until the fetch-on-unknown-key is built.

## Secrets and keys

| Secret | Used for | Held by | Read by the API | Rotation |
|---|---|---|---|---|
| Signing key of the identity provider | Signing tokens | Identity provider | Never; the public key via JWKS | At the identity provider; restart the API afterwards (see above) |
| Client secret of each UI client | The UI's login | Identity provider, the UI's environment | No | At the identity provider, then the UI's environment |
| Tokens of a signed-in UI user (access, refresh, ID token) | The UI's calls to the API | The UI's session store: files in the UI container, not encrypted | No | Dropped after eight hours without a request and on every redeploy of the UI; sign out ends them at the identity provider |
| Credentials of machine clients | CI and automation (`client_credentials`) | Identity provider, the CI's secret store | No | Before their expiry |
| Password of the runtime role (`<database>_runtime`, in `DATABASE_URL`) | The API, the purge, `cc oversight expire`, `cc actor` | Operator | Environment | `python -m migrations.roles` sets it again from the environment; then restart |
| Password of the migration role (`<database>_migrator`) | Migrations; owns every table | Operator | Never; only the one-shot migration run | Like the runtime role |
| Password of the database superuser | Creating the two roles above, nothing else | Operator | Never; only `python -m migrations.roles` | In PostgreSQL |
| Password in `REDIS_URL` | Cache access | Operator | Environment | Redis first, then the environment; restart |
| `LITELLM_API_KEY` | Calling the gateway | Gateway | Environment | At the gateway, then the environment; restart |
| `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` | Tracing | Langfuse | Environment | In Langfuse, then the environment; restart |
| `PII_MAPPING_KEYS` — `<id>:<32 bytes base64url>`, comma separated | Sealing each workflow run's placeholder mapping (CHY-234) | Operator, apart from the database and its backups | Environment, at start; the API does not start without it | Put a new key first — it seals from then on — and keep the old ones, which still open what they sealed, until those runs are past their content period; then drop them. A lost key takes the values of its runs with it; the placeholders stay. Generate one: `python -c 'from kernel.mapping_cipher import generate_key; print(generate_key("k1"))'` |
| API keys of model providers | Hosted models | Gateway only | Never | At the gateway |

Cohereny never sees a model provider's key: the gateway holds it, and a hosted model is
reached only through the gateway.

## Known gaps

| Gap | Effect | Planned |
|---|---|---|
| JWKS not fetched again on an unknown key id | New tokens fail for up to 30 minutes after the identity provider rotates its key | CHY-232 |
| Secrets held as plain strings in the settings | Printing the settings object would show them; nothing prints it today | CHY-233 |
| No mode that refuses unencrypted connections | An `http://` gateway or a database without TLS is accepted | CHY-195 |
| Pilot runs stored with the real values | Workflow runs keep placeholders (CHY-234); the LangGraph pilots do not, and neither do workflow runs started before migration 036 until their content period ends. A copy of the database shows those values | CHY-172 with CHY-179 for the banking pilot; the study pilot later (ADR-028 point 5) |

## Changes

**2026-11-07** — Workflow runs stored with placeholders (CHY-234). The first thing Cohereny
encrypts itself: each run's mapping, AES-256-GCM, bound to its run, under `PII_MAPPING_KEYS` —
a new row in the inventory, and an exception to ADR-014 named at the top. The gap row narrows
to the pilots and to runs started before migration 036.

**2026-11-06** — Three database roles instead of one (CHY-85): the superuser only creates
the migration role, which owns every table, and the runtime role the API connects as. The
API refuses to start with more than the runtime role's rights. Two new passwords in the
inventory; the gap row on the audit log's own database role is closed.

**2026-11-05** — Identifiers replaced before every model call (CHY-63). No new key: the
mapping is not stored. The gap row on the pseudonymisation mapping now names what is still
open, run content in the database, and where it is closed (CHY-234).

**2026-11-05** — First version (CHY-15): CON.1 concept for operators, after ADR-014.
