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

## Software bill of materials

Every pipeline of the development repository writes a CycloneDX SBOM of the Python
dependencies (CRA Annex I Part II (1)). It covers everything the Dockerfile installs (the
service and both pilots), transitive dependencies included, each with version, package URL,
license and supplier. The pipeline fails if a declared top-level dependency or a supplier
is missing from it, or if the file does not validate against the CycloneDX 1.6 schema
(`scripts/check_sbom.py`).

It describes the resolution at pipeline time. Runtime dependencies are declared with lower
bounds only and there is no lock file yet, so an image built later may contain newer
releases. If you build Cohereny yourself, generate the SBOM from your own environment the
same way:

```bash
python -m venv /tmp/product && /tmp/product/bin/pip install . ./pilots/study ./pilots/banking
python -m venv /tmp/generator && /tmp/generator/bin/pip install -r scripts/requirements-sbom.txt
/tmp/generator/bin/cyclonedx-py environment --pyproject pyproject.toml -o sbom.cdx.json /tmp/product/bin/python
/tmp/product/bin/python scripts/sbom_supplier.py sbom.cdx.json
/tmp/generator/bin/python scripts/check_sbom.py --schema sbom.cdx.json . ./pilots/study ./pilots/banking
```

The generator gets a virtual environment of its own; installed next to the product, its
dependencies would be listed as Cohereny's. The generator leaves the supplier empty;
`sbom_supplier.py` takes it from the package metadata of the product environment, so it runs
with that environment's interpreter. The SBOM for a given commit is available on
request at security@cohereny.com.

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
- **`scripts/dev_issuer.py` issues a token to anyone who asks.** It is the local stand-in
  for the identity provider in development, CI and the quickstart, listens on loopback by
  default — the quickstart runs it inside its Compose network and publishes it on
  127.0.0.1 only — and does not check passwords. The service accepts an issuer over plain
  http only with `ENVIRONMENT=dev`, which keeps it out of other stages; do not run it where
  a deployment holding real data could reach it. There is no switch that turns
  authentication off.

Regulatory positioning — EU Cyber Resilience Act (Regulation 2024/2847) and the EU AI Act —
is described in the [whitepaper](https://github.com/cohereny/cohereny/blob/main/docs/cohereny-whitepaper.md).
