"""
api/config.py

Central configuration via Pydantic Settings — reads values from the .env file
or environment variables. Instantiated on import and exported as the `settings` singleton.

Three stages each use their own .env file (ENVIRONMENT, DB index, etc.):
  .env.dev   → ENVIRONMENT=dev,  Redis DB 1
  .env.test  → ENVIRONMENT=test, Redis DB 2
  .env.prod  → ENVIRONMENT=prod, Redis DB 0

Every stage authenticates. There is no switch that turns authentication off
(CHY-192); local development and CI get their tokens from scripts/dev_issuer.py.

Called by:
  - api.main, api.deps, core.db, core.redis (all import `settings`)

Dependencies:
  - pydantic-settings (reads .env file, validates types)
"""
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All configurable API parameters, loaded from the .env file.

    Defaults are set for local development without Docker. In container stages
    all values are provided via the respective .env.* file.
    """

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # PostgreSQL connection string including DB name (dev/test/prod use separate DBs).
    # The runtime role, never the superuser or the owner: core.db refuses both (CHY-85).
    database_url: str = "postgresql://cohereny_runtime:cohereny_runtime@localhost:5432/cohereny"

    # Redis URL with DB index: DB0=prod, DB1=dev, DB2=test — prevents cross-stage collisions
    redis_url: str = "redis://localhost:6379/0"

    # Where Authentik publishes its applications. Each cohereny surface has its
    # own application below this, so this is a base and not the issuer itself:
    # `iss` is https://…/application/o/cohereny-author/ and its three siblings
    # (INFRA-92, ADR-031). The map is built by kernel.surfaces.issuers().
    #
    # https, not http. The issuer is where the JWKS comes from, so every token
    # this service accepts is only as trustworthy as the channel that delivered
    # the signing key. Over plain HTTP anyone on the path substitutes it.
    #
    # Until 21.08.2026 this named a single application, `cohereny`, that had
    # never been created — both schemes answered 404, and nobody noticed because
    # DEV_MODE=true skipped validation on dev (CHY-91). Three defects holding each
    # other up.
    #
    # The default is a placeholder (example.com is reserved for documentation):
    # every deployment names its own identity provider, and one that forgot to
    # fails on the first token instead of trusting somebody else's issuer.
    #
    # Plain http is accepted on dev only (_refuse_plain_http_issuer_outside_dev).
    oidc_base_url: str = "https://auth.example.com/application/o"

    # Which stage this process believes it is. Defaults to the strictest value:
    # a deployment that forgot to say gets treated as production, because the
    # cost of being wrong is asymmetric.
    #
    # Added for CHY-91. Until now the application had no idea which stage it
    # ran in — DEV_MODE was the only signal, and it was its own justification.
    # The switch that says "I am dev" was the same switch that turned off
    # authentication, so there was nothing to check it against.
    environment: Literal["dev", "test", "prod"] = "prod"

    # There used to be a `dev_mode` here: skip JWT validation and answer every
    # request as a static admin. CHY-91 fenced it in with this `environment`
    # and the database name; CHY-192 removed it. A switch that turns off
    # authentication is one misplaced .env file away from production, however
    # well it is guarded, and it hid three broken issuer configurations on dev
    # for weeks (see oidc_base_url). Development now authenticates like every
    # other stage, against scripts/dev_issuer.py where no Authentik is at hand.

    # Serve /docs, /redoc and /openapi.json. Off by default: the published
    # contracts in contracts/ are the documentation, and a deployment should not
    # serve its schema to anyone who asks unless it decided to.
    #
    # These three used to hang on dev_mode, so the only way to read the API docs
    # on a running instance was to turn authentication off. This switch
    # authenticates nothing and disables nothing (CHY-192).
    api_docs: bool = False

    # Emit a node_metrics event with token usage and latency per model call in
    # pilot runs (banking pilot). Read by the UI's telemetry panel. Was
    # dev_mode as well — a measurement switch that came bundled with turning
    # authentication off (CHY-192).
    pilot_node_metrics: bool = False

    # Authentik group → actors.role used to be mirrored here as two settings,
    # oidc_group_role_map and default_actor_role. Both were plain passthroughs
    # of GROUP_ROLE_MAP and DEFAULT_ROLE, wrapped so a deployment could override
    # them — and the override was never set, on any stage. CHY-101 removed them
    # when kernel/access.py stopped importing this module: an unused
    # indirection is not how you keep an option open. If an override is ever
    # needed it becomes a parameter of role_from_groups().

    api_version: str = "0.1.0"

    # LiteLLM gateway URL — OpenAI-compatible API endpoint.
    # Returned in /v1/route responses so clients do not need to hardcode infra URLs.
    # A deployment may point this at the gateway directly over plain http to
    # bypass a reverse proxy with a short streaming timeout (the reference
    # installation's dev stage does, INFRA-16). That trade is real and not
    # settled here: TLS against a known streaming problem. The *default* is
    # https either way — a default is what applies when nobody thought about it,
    # and unencrypted should never be that. It is a placeholder, like the
    # issuer above: each deployment sets its own.
    litellm_base_url: str = "https://litellm.example.com/v1"

    # LiteLLM API key (LITELLM_MASTER_KEY). Only needed when LiteLLM auth is enabled.
    # Set to empty string for local dev without an active LiteLLM instance.
    litellm_api_key: str = ""

    # Keys that seal each workflow run's placeholder mapping (CHY-234,
    # kernel/mapping_cipher.py): `<id>:<32 bytes base64url>`, comma separated.
    # The first one seals, every one listed opens — to rotate, put a new key in
    # front and drop the old one once the runs it sealed are past their content
    # period. Required: the API does not start without one (E5, 06.11.2026).
    # A lost key takes the values of its runs with it; the placeholders stay.
    pii_mapping_keys: str = ""

    # Path to the deployment's model declaration (routing-config.yaml): which
    # models this installation can reach and the strictest data class each may
    # see. Empty = the file bundled with the code, which declares the reference
    # installation's models.
    #
    # A setting since CHY-189. Before, whoever ran Cohereny against their own
    # gateway had to edit a file inside the source tree — or the image — to
    # name their models, and the quickstart could not declare its one local
    # model without rebuilding the API. Which model serves which task is not
    # here: that is a reviewed routing rule in the context repository (ADR-037).
    routing_config: str = ""

    # Directory where pilot input PDFs are stored (read-only in the container).
    # Must match the volume mount of the API container.
    pilot_input_dir: str = "/data/examples"

    # LLM model identifiers — used in EU AI Act Art. 50 transparency disclosure.
    # Must match the model names configured in LiteLLM config.yaml.
    # --- Langfuse (CHY-29) ---
    #
    # https, not http. CHY-78 lost three weeks of traces to exactly one missing
    # character here: Traefik answers plain http with 404 instead of redirecting,
    # and the SDK swallows transport errors by design. tests/test_internal_urls.py
    # guards this file for that reason.
    # Placeholder; without keys tracing stays off regardless.
    langfuse_host: str = "https://langfuse.example.com"
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""

    # Master switch. Without keys tracing stays off anyway; this exists so a
    # deployment can say so on purpose rather than by omission.
    langfuse_enabled: bool = True

    # Prompt and response into the trace. On by default — a trace without them
    # records that something happened, not what. Off for deployments where even
    # a self-hosted trace store is too much. Field-level redaction is CHY-63.
    langfuse_capture_io: bool = True

    # Separates dev/test/prod inside one Langfuse project.
    langfuse_environment: str = "default"


    @model_validator(mode="after")
    def _refuse_plain_http_issuer_outside_dev(self) -> "Settings":
        """An issuer over plain http is accepted on dev only.

        The issuer is where the signing keys come from. Over http anyone on the
        path can replace them, and then every token they sign is valid here.

        The case this exists for is scripts/dev_issuer.py (CHY-192). It speaks
        http and hands a token to whoever asks, so a deployment that trusts it
        has no authentication — the same exposure DEV_MODE had, by another
        route. CHY-91 kept DEV_MODE to ENVIRONMENT=dev; this keeps its
        replacement there too, by the property it cannot hide: the scheme.

        ENVIRONMENT defaults to prod, so a deployment that forgot to say which
        stage it is gets the strict reading. Raised at construction, so a
        misconfigured process does not start.
        """
        if self.environment != "dev" and self.oidc_base_url.lower().startswith("http://"):
            msg = (
                f"OIDC_BASE_URL is plain http with ENVIRONMENT={self.environment!r}. "
                f"The signing keys would travel unencrypted, and an http issuer is "
                f"most likely scripts/dev_issuer.py, which issues a token to anyone. "
                f"Use https, or ENVIRONMENT=dev for local development."
            )
            raise ValueError(msg)
        return self

    model_small: str = "gemma3:4b"
    model_medium: str = "gemma4:e4b"


settings = Settings()
