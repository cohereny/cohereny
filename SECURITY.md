# Security Policy

## Supported versions

Cohereny is pre-release (see the notice in the [README](README.md)). Security fixes go
into the current development line only; there are no maintained release branches yet.

## Reporting a vulnerability

**Please do not report security vulnerabilities in public GitHub issues.**

Report them by email to **security@cohereny.com**.

Please include:

- a description of the vulnerability
- steps to reproduce, if you have them
- the impact you expect
- a suggested fix (optional)

You will receive an acknowledgement within **72 hours** and a status update within
**7 days**.

## Responsible disclosure

- Vulnerabilities are fixed before they are disclosed publicly
- Reporters are credited in the commit message of the fix, unless they ask not to be
- No legal action is taken against good-faith security research

## Scope notes

Cohereny processes prompts and routes them to model backends; it decides which data may
reach which model (`router/routing-config.yaml`, ADR-041 in the architecture document).
Findings that let data of a scope reach a model above that scope's data class, or let a
block become active without the review its scope requires, are treated as security issues.
In a `direct` scope (project, sub-project, personal) a block goes live on write by design;
that is not a finding.

## Known limitations

Cohereny is pre-release. The following gaps are known, tracked, and not treated as
vulnerabilities; reports that show a consequence beyond what is described here are still
welcome.

- **No authorization per scope yet.** Roles apply to the whole installation. Scope
  memberships are recorded but not enforced, so a contributor can write blocks in any
  scope, and a consumer can read all blocks and all runs.
- **Four eyes are not enforced for blocks.** Approving a block in a strict scope needs the
  `reviewer` role, and author and approver are both recorded, but a reviewer can approve
  their own block. Workflow approval gates with the `four_eyes` review role do require two
  different people.
- **The audit log is protected against application faults, not against its database role.**
  Triggers refuse `UPDATE`, `DELETE` and `TRUNCATE` on the audit table. With the bundled
  setup the application connects as the role the official PostgreSQL image creates from
  `POSTGRES_USER`, and that role is a superuser: it can disable or bypass the triggers, read
  every database in the cluster and run operating-system commands via `COPY … FROM PROGRAM`.
  Treat the application's database credentials as full control of the installation. A
  separate, non-owning role without superuser rights is planned.
- **`python-jose` verifies the tokens.** Dependency scanners report advisories for it.
  Cohereny accepts only `RS256` signatures and does not use JWE; replacing the library is
  planned.
- **`DEV_MODE` disables authentication.** The service refuses to start with it unless
  `ENVIRONMENT=dev` is set and the database name does not end in `_prod` or `_test`. It is
  meant for local development only and is scheduled for removal.

Regulatory positioning — EU Cyber Resilience Act (Regulation 2024/2847) and the EU AI Act —
is described in the [whitepaper](https://github.com/cohereny/cohereny/blob/main/docs/cohereny-whitepaper.md).
