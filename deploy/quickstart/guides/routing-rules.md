# Routing rules

Nobody calling Cohereny names a model. A request names a **task type** — `summarize`,
`draft_reply`, whatever your workflows need — and a **routing rule** decides which
model serves it, in which order to fall back, and how long an answer may be.

A routing rule is a block, so it is reviewed like one: in a strict scope it takes
effect only after a reviewer approved it, and the audit log records who decided. A rule
that names a model below `critical` needs a second person in every scope: whoever wrote
it cannot approve it ([Scopes and blocks](scopes-and-blocks.md)).
Which model may see which data is a decision with data-protection consequences; it is
not a setting a deployment flips.

## What a rule says

```json
{
  "scope_id": "<id of Customer Support>",
  "block_type": "routing_rule",
  "content": "Why: customer messages contain personal data, so only the local model.",
  "metadata": {
    "task_type": "draft_reply",
    "candidates": ["qwen2.5:1.5b"],
    "max_tokens": 300
  }
}
```

- `task_type` — what the rule decides.
- `candidates` — models, best first. When one fails (unreachable, rate-limited, a
  server error) the next one answers, and the fallback is written to the audit log.
- `max_tokens` — required. The longest answer this rule allows; a caller may ask for
  less, never more.
- `content` — the reason. It is what a reviewer judges and what an auditor reads later.

The quickstart's team *Customer Support* has two: `draft_reply` and `summarize`, both
on the local model.

## Which rule applies

The rule of the **nearest** scope wins: a project's rule overrides its team's, a team's
its organisation's. An organisation sets a default, and a project sharpens it. A scope
with no rule for a task type, and none above it, cannot run that task at all —
`/v1/complete` answers `409`. There is no installation-wide default model, on purpose:
an installation that has not decided should say so rather than pick a model that then
looks chosen.

## Models and data classes

A rule can only name models the installation declares, in
[routing-config.yaml](../routing-config.yaml). Each declaration says the strictest
class of data that model may see:

```yaml
models:
  qwen2.5:1.5b:
    runs_on: local-ollama
    max_privacy_level: critical
```

A rule is refused — at approval and again at every request — when a candidate may not
see the class of data its scope holds. *Example Corp* holds `confidential` data, so a
hosted model declared `public` could never serve it. And a chain may not fall back
from a stricter model to a looser one: a fallback fires when something already went
wrong, which is the worst moment to move data somewhere less protected.

## Adding a rule

```bash
curl -s $API/v1/blocks -H "Authorization: Bearer $AUTHOR" -H 'Content-Type: application/json' -d "{
  \"scope_id\": \"$PROJECT\", \"block_type\": \"routing_rule\",
  \"content\": \"Short answers for quick questions in this project.\",
  \"metadata\": {\"task_type\": \"summarize\", \"candidates\": [\"qwen2.5:1.5b\"], \"max_tokens\": 80}}"
```

*Order inquiries* is a direct scope, so the rule is live at once and from now on
overrides the team's `summarize` rule for this project — ask step 3 of the README again
and the answer is shorter. In a strict scope, submit and approve it like any block
([Scopes and blocks](scopes-and-blocks.md)).

To use another model, see [Your own model](your-own-model.md).
