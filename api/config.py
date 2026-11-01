"""
api/config.py

Central configuration via Pydantic Settings — reads values from the .env file
or environment variables. Instantiated on import and exported as the `settings` singleton.

Three stages each use their own .env file (DEV_MODE, DB index, etc.):
  .env.dev   → DEV_MODE=true, Redis DB 1
  .env.test  → DEV_MODE=false, Redis DB 2
  .env.prod  → DEV_MODE=false, Redis DB 0

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

    # PostgreSQL connection string including DB name (dev/test/prod use separate DBs)
    database_url: str = "postgresql://cohereny:cohereny@localhost:5432/cohereny"

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
    # DEV_MODE=true skips validation on dev (CHY-91). Three defects holding each
    # other up.
    #
    # The default is a placeholder (example.com is reserved for documentation):
    # every deployment names its own identity provider, and one that forgot to
    # fails on the first token instead of trusting somebody else's issuer.
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

    # True = skip JWT validation entirely and return a static admin
    # (api/deps.py). Only legitimate in local development without Authentik.
    #
    # Guarded by _refuse_dev_mode_outside_dev below. Before CHY-91 the only
    # thing standing between this and production was the right .env file being
    # in the right place, plus a comment saying "never use in prod or test" —
    # and the internal CRA assessment certified "Secure by Default" on the strength of it.
    dev_mode: bool = False

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

    # Directory where pilot input PDFs are stored (RAID mount, read-only in container).
    # Must match the volume mount in compose.yaml.
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
    def _refuse_dev_mode_outside_dev(self) -> "Settings":
        """Two independent conditions, because one of them is self-declaring.

        `environment` alone would not help against the realistic accident:
        copying .env.dev over .env.prod flips both values together. So the
        database is asked as well — a name ending in _prod or _test is evidence
        about the data at hand that the flag cannot fake.

        Blocklist rather than allowlist on purpose. Requiring the name to end in
        _dev would break a local checkout whose database is simply called
        `cohereny`, and a guard that blocks legitimate work gets switched off.

        Raised at construction, so a misconfigured process does not start.
        Running without authentication is worse than not running.
        """
        if not self.dev_mode:
            return self

        if self.environment != "dev":
            msg = (
                f"DEV_MODE=true with ENVIRONMENT={self.environment!r}. "
                f"Dev mode disables authentication entirely and returns a static "
                f"admin for every request — it is only valid when ENVIRONMENT=dev."
            )
            raise ValueError(msg)

        db_name = self.database_url.rsplit("/", 1)[-1].split("?")[0]
        if db_name.endswith(("_prod", "_test")):
            msg = (
                f"DEV_MODE=true against database {db_name!r}. Dev mode disables "
                f"authentication; it must never point at production or staging "
                f"data, whatever ENVIRONMENT claims."
            )
            raise ValueError(msg)

        return self

    model_small: str = "gemma3:4b"
    model_medium: str = "gemma4:e4b"


settings = Settings()
