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
that is not a finding — except for a block that decides where data goes (a routing rule
naming a model below `critical`, a loosened privacy rule), which waits for a second person
in every scope (ADR-045). One of those going live without that is a finding.

Before every model call of `/v1/complete` and of a workflow Cohereny replaces identifiers
with placeholders: what the shipped patterns find, what a scope's `redaction_rule` blocks add, and every value the caller
names in `identifiers_to_redact` (ADR-018). An identifier of any of these kinds that
reaches a model, the gateway or a trace unreplaced is a security issue. So is a shipped
pattern switched off other than by a block in a scope without a parent, declared as a
loosening of `pii_redaction` and approved by a second person.

A workflow run is stored with placeholders, and the mapping back to its values is sealed per
run (CHY-234, [CRYPTOGRAPHY.md](CRYPTOGRAPHY.md)). A value of such a run stored as written, a
mapping that opens for another run, values put back for anyone but whoever started the run
and, from `reviewer` up, whoever sees it, or a resolution that leaves no audit entry: each is
a security issue.

What Cohereny encrypts and verifies itself, what it expects the installation to provide,
and every secret involved: [CRYPTOGRAPHY.md](CRYPTOGRAPHY.md).

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

- **Authorization per scope covers reading runs, not yet blocks or writing.** A run is
  visible only to whoever started it, to the members of its workflow's scope and of the
  scopes above, and to admin; row-level security enforces it in the database, so a query
  that does not say for whom sees nothing. It holds against application faults, not
  against someone holding the application's database credentials, who can claim to act
  for anyone. Blocks are still readable by every signed-in user, and roles still apply to
  the whole installation, so a contributor can write blocks in any scope.
- **Four eyes cover the blocks that decide where data goes, not every block.** A routing
  rule that names a model below `critical`, and a block that loosens a privacy rule
  (`privacy_impact: permissive`), are approved by someone who wrote nothing in that version
  — in every scope; in a project or personal scope they wait in review instead of going
  live. Other blocks, prompt material and tightening rules, can still be approved by their
  author; author and approver are both recorded. Workflow approval gates with the
  `four_eyes` review role require two different people.
- **A single operator approves their own data-flow blocks.** If only one person may
  approve in the installation — role `reviewer` or above, technical accounts not counted —
  that person may approve a routing rule or privacy loosening they wrote. Then nobody
  second checks whether data may leave the installation. The approval is marked in the
  audit log (`four_eyes: self_approved`), logged as a warning and answered with a
  `Cohereny-Notice` header. It is not four eyes: for regulated use — banking, health data,
  human oversight under Art. 14 of the EU AI Act — do not run with a single approver. A
  second person with the role `reviewer` ends it; there is no setting for it.
- **Technical accounts must be marked.** Put CI and automation accounts in the
  identity-provider group `chy-service-accounts`. A marked account never approves a block
  that decides where data goes and does not count as a person. An unmarked one with an
  approving role counts as a second person, and whoever can use its credentials can approve
  as it.
- **Identifiers are found by pattern or by name, nothing else.** E-mail addresses, phone
  numbers, IBANs, German tax ids and health insurance numbers and labelled dates of birth
  are found on their own. A name, a postal address or any other identifier that nobody
  lists — in `identifiers_to_redact` or in a `redaction_rule` block of the scope —
  reaches the model unchanged — and the gateway and the trace with it, and is stored with
  the run as written. What the model writes on its own is checked against the same patterns and
  names before the trace records it. Name what you know; send data you cannot list completely only to a scope
  with data class `critical`, whose models stay inside the installation. Recognising names
  without a list is planned.
- **The pilots send and keep the real values.** They are deprecated. A workflow run
  replaces identifiers before the model and is stored with placeholders. The LangGraph
  pilots do neither: they call the model with the values (the banking pilot traces them
  to Langfuse as well), and what they took in and gave back is stored with the values,
  protected by roles, by the retention periods and by the encryption the installation
  provides ([CRYPTOGRAPHY.md](CRYPTOGRAPHY.md)). So are workflow runs started before
  migration 036, until their content period ends. The banking pilot moves onto the workflow
  runner; the study pilot follows later.
- **`/v1/route` only names a model.** A client that takes the name and calls the gateway
  itself passes no replacement; Cohereny never sees what it sends. Personal data goes
  through `/v1/complete` or a workflow.
- **The mapping key decides.** Whoever holds `PII_MAPPING_KEYS` and the database credentials
  reads every value of every run; whoever loses the key loses the values of the runs it
  sealed. Keep it in the secret store, apart from the database and its backups.
- **The audit log holds against the application, not against the owner of its table.**
  Triggers refuse `UPDATE`, `DELETE` and `TRUNCATE` on the audit table, and the API's
  database role (`<database>_runtime`) holds none of these rights, owns no table and may
  create nothing; the API refuses to start as anything more. Whoever holds the migration
  role (`<database>_migrator`, the owner) or the superuser can still switch a trigger off.
  Keep those two credentials away from the API — `python -m migrations.roles` and the
  migration run are the only places that need them — and there is no hash chain that would
  show a change made with them.
- **`scripts/dev_issuer.py` issues a token to anyone who asks.** It is the local stand-in
  for the identity provider in development, CI and the quickstart, listens on loopback by
  default — the quickstart runs it inside its Compose network and publishes it on
  127.0.0.1 only — and does not check passwords. The service accepts an issuer over plain
  http only with `ENVIRONMENT=dev`, which keeps it out of other stages; do not run it where
  a deployment holding real data could reach it. There is no switch that turns
  authentication off.

Regulatory positioning — EU Cyber Resilience Act (Regulation 2024/2847) and the EU AI Act —
is described in the [whitepaper](https://github.com/cohereny/cohereny/blob/main/docs/cohereny-whitepaper.md).
