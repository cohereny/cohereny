# Tokens and roles

Every request to Cohereny carries a token from an OpenID Connect provider. In the
quickstart that provider is a development stand-in, `scripts/dev_issuer.py`, running as
the `issuer` service. It behaves like the real thing in everything the API checks —
signatures, issuers, audiences, expiry — and differs in one way: **it does not check
passwords**. Anyone who can reach it gets any token. That is why it listens on
127.0.0.1 only and why the API accepts an issuer over plain http only with
`ENVIRONMENT=dev`.

## Users

The username decides the role. Each is its own person: the same name always gets the
same identity, two names are two people in the audit log.

| Username | Role | Can, in addition to the role below it |
|---|---|---|
| `consumer` | consumer | read blocks, compose, ask a model, read runs |
| `contributor` | contributor | write and submit blocks, build workflows, start runs |
| `reviewer` | reviewer | approve or reject blocks, activate workflows, decide at a review, read the audit log of one item |
| `architect` | architect | create scopes |
| `admin` | admin | read the whole audit log, anonymise people |

Roles come from the token's `groups` claim (`chy-<role>s`) on every request, so a
real provider grants them by group membership. Roles apply to the whole installation
for now; memberships per scope are recorded but not enforced yet.

## Surfaces

The API is split into four *surfaces*, and a token opens exactly one. A dashboard
that reads the audit log cannot write blocks with its token, even if its user could.

| Surface | Client id | Paths |
|---|---|---|
| `author` | `cohereny.author.ci` | `/v1/scopes`, `/v1/blocks`, `/v1/workflows`, `/v1/pilots` |
| `serve` | `cohereny.serve.ci` | `/v1/compose`, `/v1/complete`, `/v1/route` |
| `observe` | `cohereny.observe.ci` | `/v1/audit-log`, `/v1/benchmarks`, `/v1/questions` |
| `admin` | `cohereny.admin.ci` | `/v1/admin/...` |

A token for the wrong surface gets `403` with a message naming both; no token or an
expired one gets `401`. The contract of each surface is in `contracts/` at the
repository root.

## Getting a token

```bash
curl -s http://127.0.0.1:9010/application/o/token/ \
  -d grant_type=client_credentials -d client_id=cohereny.author.ci \
  -d username=reviewer -d password=x
```

The answer holds `access_token`, valid for ten minutes (`expires_in`). The README's
`token` function wraps this.

In the interactive documentation at <http://127.0.0.1:8000/docs>, choose **Authorize**
and paste a token. Requests to another surface then fail with `403` — paste a token for
that surface instead.

## With a real provider

Cohereny is tested with Authentik. There each surface is an application with its own
issuer and audience, roles are groups, and a person signs in with a password or a
passkey. [DEPENDENCIES.md](../../../DEPENDENCIES.md) lists what the provider has to
offer.
