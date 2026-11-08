# Scopes and blocks

Cohereny keeps the context a model receives — instructions, facts, examples — as
**blocks** in a tree of **scopes**. A scope inherits every block above it, so an
organisation says something once and every team and project below it says it too.

The examples use the README's `token` function and variables.

## Scopes

| Type | Review | Typical use |
|---|---|---|
| `org`, `team`, `workspace` | **strict**: a block goes live only after a reviewer approved it | rules and knowledge many people rely on |
| `project`, `sub_project`, `personal` | **direct**: a block is live as soon as it is written | work in progress, one case, your own drafts |

One kind of block waits for a second person in every scope: a routing rule that names a
model below `critical`, and a block that loosens a privacy rule. Those decide where data
may go, and whoever wrote them cannot approve them — unless the installation has only one
person who may approve ([SECURITY.md](../../../SECURITY.md)). In a direct scope such a
block comes back `pending_review` instead of live.

Creating a scope needs the `architect` role. The parent decides where it hangs; the
path is derived, never given.

```bash
ARCHITECT=$(token admin author)
curl -s $API/v1/scopes -H "Authorization: Bearer $ARCHITECT" -H 'Content-Type: application/json' \
  -d "{\"name\": \"Returns\", \"node_type\": \"team\", \"parent_id\": \"<id of Example Corp>\"}"
```

A scope may declare the **class of data** it holds — `public`, `internal`,
`confidential` or `critical` — as `privacy_level`. Omitted, it inherits the strictest
class above it (and `critical` when nobody declared one). It may never be looser than
its parent's. The class decides which models may serve the scope: see
[Routing rules](routing-rules.md). *Example Corp* is `confidential`.

## Blocks

| `block_type` | Ends up as |
|---|---|
| `system_prompt` | instructions to the model |
| `user_context` | facts and rules the model works with |
| `example_io` | an example of a good answer |
| `tool_schema` | the description of a tool |
| `routing_rule` | not prompt material — a decision about models ([Routing rules](routing-rules.md)) |

### In a strict scope: write, submit, approve

```bash
CONTRIBUTOR=$(token contributor author)
curl -s $API/v1/blocks -H "Authorization: Bearer $CONTRIBUTOR" -H 'Content-Type: application/json' \
  -d "{\"scope_id\": \"<id of Customer Support>\", \"block_type\": \"user_context\",
       \"content\": \"Gift cards cannot be refunded.\"}"
BLOCK=<block_id from the answer>
curl -s $API/v1/blocks/$BLOCK/submit -H "Authorization: Bearer $CONTRIBUTOR" \
  -H 'Content-Type: application/json' -d '{"version": 1}'
curl -s $API/v1/blocks/$BLOCK/approve -H "Authorization: Bearer $REVIEW" \
  -H 'Content-Type: application/json' -d '{"version": 1}'
```

Instead of approving, a reviewer can reject with a reason:
`/reject` with `{"version": 1, "reason": "Contradicts the returns policy."}`. The
block is then `rejected`. Its author can submit it again as it is, or change it: the
change becomes version 2, a draft, submitted with `{"version": 2}`.

`version` names the version you mean. If someone changed the block in between, the
request is refused with `409` rather than approving something you have not seen.

### In a direct scope: just write

In *Order inquiries* the same `POST /v1/blocks` answers with `"status": "active"`. No
submit, no approval. When it proves itself, **promote** it into a strict scope, where it
arrives as a draft and goes through review:

```bash
curl -s $API/v1/blocks/$BLOCK/promote -H "Authorization: Bearer $CONTRIBUTOR" \
  -H 'Content-Type: application/json' -d "{\"target_scope_id\": \"<id of Customer Support>\"}"
```

### Changing a block

`PATCH /v1/blocks/{block_id}` with the new `content`. A live block in a strict scope
is not changed in place: the patch creates the next version as a draft, the current one
stays live until the new one is approved. `GET /v1/blocks/{block_id}/history` shows every
version, `GET /v1/audit-log?target=block:<id>` who did what.

## What a model receives

`POST /v1/compose` with a scope returns its blocks and those of every scope above it,
organisation first. That is exactly what `/v1/complete` and a workflow's
`context_compose` node hand to the model as instructions.

## Rules that cannot be loosened below

Some blocks state a governance rule — whether prompts may go to a cloud model
(`cloud_routing`), whether a shipped redaction rule may be switched off
(`pii_redaction`, see below). Such a block declares the rule when it is created:

```json
{"governance_key": "cloud_routing", "privacy_impact": "restrictive", "locked": true}
```

A scope below may make a `restrictive` rule stricter, never loosen it with a
`permissive` one; `locked` forbids restating it at all. *Example Corp*'s data rule is
such a block. Write a `cloud_routing` block into *Customer Support* and submit it: the
draft is accepted, but approving it is refused with `409` — *"Governance key
'cloud_routing' is locked by block … in scope …"* — whether it loosens the rule or
tightens it. The rule is changed where it was made, or not at all.

## What the model never sees

Before every model call of `/v1/complete` and of a workflow Cohereny replaces identifiers
with placeholders: e-mail addresses, phone numbers, IBANs, German tax ids and health insurance numbers, labelled
dates of birth, and every name a caller lists in `identifiers_to_redact`. `/v1/complete`
puts the values back into its answer; a workflow run keeps the placeholders
([workflows](workflows.md)). A scope adds its own as a `redaction_rule` block — reviewed like
any block, inherited by every scope below, never composed into a prompt:

```json
{"scope_id": "<id of Example Corp>", "block_type": "redaction_rule",
 "content": "Our staff's names stay in the house.",
 "metadata": {"category": "STAFF", "texts": ["Dr. Schmidt", "Dr. Weber"]}}
```

`"metadata": {"category": "CASE_NUMBER", "pattern": "\\b\\d+ C \\d+/\\d{2}\\b"}` replaces
a pattern instead. A pattern runs over every prompt, so one that could backtrack without
end — a repeated group that varies inside, `(a+)+` — is refused at approval.

Rules add up down the tree; no scope removes one that a scope above added. Switching a
shipped rule off is the one exception: `"metadata": {"disable": ["PHONE"]}`, only in a
scope without a parent, declared as `"governance_key": "pii_redaction", "privacy_impact":
"permissive"`, and approved by someone who did not write it. A `restrictive` claim on
`pii_redaction` in that scope forbids it.
