# Your own model

The quickstart runs `qwen2.5:1.5b` in an Ollama container: small, local, licensed
Apache-2.0, and slow on a Mac, because Docker there cannot use the GPU. Cohereny talks
to any OpenAI-compatible endpoint, so you can point it elsewhere. Three things have to
agree, and the order matters:

1. **The gateway** serves the model — `MODEL_GATEWAY_URL` in `.env`.
2. **The installation declares it** — [routing-config.yaml](../routing-config.yaml),
   with the strictest class of data it may see.
3. **A routing rule names it** — reviewed like any block
   ([Routing rules](routing-rules.md)).

A model the gateway serves but nobody declared cannot be named in a rule; a declared
model no rule names is never asked.

## Ollama on your machine, with the GPU

Install Ollama on the host, then:

```bash
ollama pull qwen2.5:7b
```

In `.env`:

```bash
MODEL_GATEWAY_URL=http://host.docker.internal:11434/v1
```

Declare the model in `routing-config.yaml` — it runs on your machine, so it may see
everything:

```yaml
models:
  qwen2.5:7b:
    runs_on: local-ollama
    max_privacy_level: critical
```

Rebuild and restart, without the Ollama container you no longer need:

```bash
docker compose build
docker compose up -d --scale ollama=0 --scale model-pull=0
```

Then write a routing rule with `"candidates": ["qwen2.5:7b"]` and have it approved.
Until then every request keeps using the rules that exist.

## A hosted model

Any service that speaks the OpenAI chat-completions API works — a LiteLLM gateway in
front of several providers is what Cohereny is tested with. In `.env`:

```bash
MODEL_GATEWAY_URL=https://gateway.example.com/v1
MODEL_GATEWAY_KEY=<your key>
```

Declare it with the class of data you are willing to send there. A model run by a
third party without a data-processing agreement should be `public`:

```yaml
models:
  hosted-model-name:
    runs_on: some-provider
    max_privacy_level: public
```

Cohereny then refuses to route a `confidential` scope like *Example Corp* to it — at
approval of the rule and again at every request. That is the point: which data may
leave the house is decided once, reviewed, and enforced, not remembered. A rule naming
such a model is approved by someone other than its writer, in every scope.

A chain may fall back from a hosted model to a local one, never the other way round.

## Checking what a request would do

`POST /v1/route` (surface `serve`) with a `scope_id` and a `task_type` answers which
model would serve it, without asking the model.
