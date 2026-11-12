# Cohereny quickstart

Everything on one machine, with one command: the API, a database, a cache, a local
model and a development identity provider — and an example to try, already set up.

The example is a customer service. An organisation, *Example Corp*, sets the tone of
voice and a data rule; its *Customer Support* team adds product facts and decides
which model drafts replies; a project, *Order inquiries*, adds this week's carrier
delay. A workflow lets the model draft a reply to a customer and stops for a person to
approve it.

Next to it, a *Help Center* holds texts written for customers anyway. There the model
may be asked directly, without a workflow; in Example Corp, which holds customer data,
it may not.

This is for trying Cohereny out, not for running it: the identity provider issues a
token to anyone who asks. For a real installation see
[DEPENDENCIES.md](../../DEPENDENCIES.md).

## Start

You need Docker with Compose, about **4 GB of memory** for Docker and about **4 GB of
disk** (images and the model).

```bash
git clone https://github.com/cohereny/cohereny.git
cd cohereny/deploy/quickstart
cp .env.example .env       # optional — every value has a default
docker compose up
```

The first start builds the image and downloads the model `qwen2.5:1.5b` (about 1 GB);
allow five to ten minutes. When the `seed` container prints `seed: done`, the example
is there. Later starts take seconds and leave your data as it was.

| What | Where |
|---|---|
| API with interactive documentation | <http://127.0.0.1:8000/docs> |
| Development identity provider (tokens) | <http://127.0.0.1:9010> |
| Database, cache, model | inside the Compose network only |

## Walk through the example

Every request needs a token, and a token opens exactly one *surface* of the API:
`author` (scopes, blocks, workflows), `serve` (compose, complete), `observe` (audit log)
and `admin`. The development provider issues one to any of its users without a
password. This shell function fetches one; tokens last ten minutes, so fetch new ones
when you get a `401`:

```bash
token() {   # token <user> <surface>
  curl -s http://127.0.0.1:9010/application/o/token/ \
    -d grant_type=client_credentials -d client_id="cohereny.$2.ci" \
    -d username="$1" -d password=x |
  python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])'
}
API=http://127.0.0.1:8000
AUTHOR=$(token admin author); SERVE=$(token admin serve)
REVIEW=$(token reviewer author); OBSERVE=$(token reviewer observe)
```

Two users matter here: `admin` writes, `reviewer` approves. Nothing in the
organisation and the team went live on the say of its author alone — the seed had
`reviewer` approve every block, as you will do below.

`reviewer` is also a member of *Example Corp*. A run is visible only to whoever
started it, to the members of its scope and of the scopes above, and to `admin`;
without the membership `reviewer` would not find the run you start in step 4.
`GET /v1/me` shows a user's id and memberships — the id is what an admin passes
to `POST /v1/scopes/{id}/members`.

**1. Find the example.** Note the id of the project *Order inquiries*, of its
workflow and of the *Help Center*.

```bash
curl -s $API/v1/scopes -H "Authorization: Bearer $AUTHOR"
PROJECT=<id of "Order inquiries">
HELP=<id of "Help Center">
curl -s "$API/v1/workflows?scope_id=$PROJECT" -H "Authorization: Bearer $AUTHOR"
WORKFLOW=<id of "Draft a reply to a customer">
```

**2. See what the project inherits.** Five blocks from three scopes, organisation
first: the tone of voice, the data rule, the product facts, an example reply and the
carrier delay. That is what a model receives as instructions in this project.

```bash
curl -s $API/v1/compose -H "Authorization: Bearer $SERVE" \
  -H 'Content-Type: application/json' -d "{\"scope_id\": \"$PROJECT\"}"
```

**3. Ask the model directly.** You name a *task type*, not a model. A routing rule of
the help center decides which model serves `summarize` — here the local one — and how
long the answer may be.

```bash
curl -s $API/v1/complete -H "Authorization: Bearer $SERVE" \
  -H 'Content-Type: application/json' -d "{\"scope_id\": \"$HELP\",
  \"task_type\": \"summarize\",
  \"prompt\": \"Summarise our shipping and returns policy in two sentences.\"}"
```

A `502` right after the first start means the model is still downloading:
`docker compose logs model-pull`.

Now the same question in the project, where customer data lives:

```bash
curl -s $API/v1/complete -H "Authorization: Bearer $SERVE" \
  -H 'Content-Type: application/json' -d "{\"scope_id\": \"$PROJECT\",
  \"task_type\": \"summarize\",
  \"prompt\": \"Summarise our shipping and returns policy in two sentences.\"}"
```

`409`: Example Corp's data class is `confidential`, so an answer there needs a person
before it is used, and a direct call returns it unchecked. That is what the workflow
in the next step is for. Where a direct call answers is decided per scope, from its
data class and from how risky direct calls in it are — the help center's were
classified `minimal`; `GET /v1/scopes/$HELP/direct-call-risk-class` shows it. There one
call in ten is drawn for a check afterwards: its answer carries a `sample_id`, and the
call waits in `GET /v1/samples` for a reviewer.

**4. Start the workflow** with a customer's message. The run composes the context,
lets the model draft a reply and stops at the review: `"status": "awaiting_oversight"`.

```bash
curl -s $API/v1/workflows/$WORKFLOW/runs -H "Authorization: Bearer $AUTHOR" \
  -H 'Content-Type: application/json' -d '{"input_data": {"message":
  "Hi, I ordered a kettle on Tuesday (order 4711) and it still has not arrived. Where is it?"}}'
RUN=<id of the run>
```

**5. Read the draft before deciding.** The `draft` node's `output.content` is the
model's reply. Read it closely: a model of 1.5 billion parameters writes a passable
draft and gets details wrong — it may miss the delay or invent a date. That is the
case a review exists for. Larger models write better drafts
([Your own model](guides/your-own-model.md)); none makes the review unnecessary.

```bash
curl -s $API/v1/workflows/runs/$RUN/nodes -H "Authorization: Bearer $REVIEW"
```

**6. Approve it** — or reject it with `"decision": "rejected"`. The reason goes into
the record.

```bash
curl -s $API/v1/workflows/runs/$RUN/oversight -H "Authorization: Bearer $REVIEW" \
  -H 'Content-Type: application/json' \
  -d '{"decision": "approved", "reason": "Accurate and polite; mentions the delay."}'
```

**7. Look at the record.** Who started the run, who decided, and when.

```bash
curl -s "$API/v1/audit-log?target=workflow_run:$RUN" -H "Authorization: Bearer $OBSERVE"
```

The same steps run as a script — `docker compose run --rm seed python
deploy/quickstart/check.py` — and on every change to Cohereny, so this walkthrough
cannot go stale unnoticed.

Prefer clicking? Open <http://127.0.0.1:8000/docs>, choose **Authorize** and paste a
token for the surface of the request you want to try.

## Make it your own

| Guide | You learn to |
|---|---|
| [Tokens and roles](guides/tokens-and-roles.md) | act as different users, understand surfaces and roles |
| [Scopes and blocks](guides/scopes-and-blocks.md) | build your own scope tree, write and approve context |
| [Routing rules](guides/routing-rules.md) | decide which model serves which task |
| [Workflows](guides/workflows.md) | build a workflow and run it with a human review |
| [Your own model](guides/your-own-model.md) | use Ollama on your machine, or any OpenAI-compatible gateway |

## Update

```bash
git pull
docker compose up
```

Every start builds the image from the source you have, so after `git pull` the new
version runs. The database keeps its data, and the migrations bring it up to date. Your
`.env` stays as it is: a new setting has a default, and [.env.example](.env.example)
shows it. What changed is in [CHANGELOG.md](../../CHANGELOG.md).

## Stop and clean up

```bash
docker compose down          # stop; data and model stay
docker compose down -v       # stop and delete the database and the downloaded model
```

`QUICKSTART_SEED=false` in `.env` starts with an empty database instead of the example.

## When something does not work

| You see | Because | Do |
|---|---|---|
| `502` from `/v1/complete` or a failed run | the model is not downloaded yet, or Docker ran out of memory | `docker compose logs model-pull`; give Docker at least 4 GB |
| `401` | the token expired after ten minutes, or it is for another surface (then `403`) | fetch a new one with `token` |
| `409 no routing rule for task type …` | no approved routing rule for that task type in the scope or above | [Routing rules](guides/routing-rules.md) |
| a port is already in use | another service on 8000 or 9010 | set `API_PORT` / `ISSUER_PORT` in `.env` |
| slow answers | Docker on a Mac cannot use the GPU | [Your own model](guides/your-own-model.md): Ollama on the host |
