"""
router/config.py

What this deployment can reach, and what each model may see (CHY-87).

The task_type -> model table lived here until CHY-87 step 2b. It is gone: which
model serves which task is a governance decision, made by a person and reviewed
by another, and it now lives in `context_blocks` as `routing_rule` blocks —
versioned, approval-bound, inherited along the scope chain (ADR-037).

What stays is the half that is not a decision: which models this installation
can reach and what data each may process. That is infrastructure, in the same
category as `litellm_base_url`, and it follows the split CHY-42 already made
between `workflow_configs` and ENV/SOPS.

The privacy check moved with the rules, because it judges a chain and chains
are no longer declared here. It reads the ceilings below.

Called by:
  - router.rules  (turns stored rules into candidates, applies the ratchet)
  - api/v1/route.py, api/v1/complete.py
"""
from pathlib import Path

import yaml

from kernel.schema import PRIVACY_LEVELS as KERNEL_PRIVACY_LEVELS

_DEFAULT_CONFIG = Path(__file__).parent / "routing-config.yaml"

#: Strictness order, low to high. A chain may not descend it: falling back from
#: a model that may see confidential data to one that may not would move that
#: data somewhere less protected, and it would happen at the moment nobody is
#: watching, because a fallback only fires when something is already broken.
#:
#: Defined in the kernel since CHY-160, because a scope declares its data class
#: in the same vocabulary a model's ceiling is written in. Re-exported here so
#: callers of the router keep one import.
PRIVACY_LEVELS = KERNEL_PRIVACY_LEVELS


class RoutingConfigError(ValueError):
    """The deployment's model declaration cannot be trusted, so it does not start.

    Every problem this raises is one a redeploy fixes and a running system
    cannot: an empty declaration, an unknown privacy level, a rule naming a
    model nobody declared. Starting anyway would mean discovering it at the
    moment a routing decision is made.

    The constructors carry the wording, following `MalformedCondition`
    (CHY-130). A message assembled at the raise site reads fine once and drifts
    the second time the same condition is raised somewhere else.
    """

    @classmethod
    def no_models(cls, path: Path) -> "RoutingConfigError":
        return cls(
            f"no models declared in {path} — this deployment cannot route "
            f"anywhere, and a routing rule would have nothing to name"
        )

    @classmethod
    def undeclared_model(cls, model: str) -> "RoutingConfigError":
        return cls(
            f"model '{model}' is not declared in routing-config.yaml — its "
            f"privacy ceiling is unknown, so no chain containing it can be judged"
        )

    @classmethod
    def unknown_level(cls, model: str, level: str) -> "RoutingConfigError":
        return cls(
            f"model '{model}' has max_privacy_level '{level}', "
            f"not one of {list(PRIVACY_LEVELS)}"
        )


class RouterConfig:
    """The declared models, checked once on load.

    config_path: a routing-config.yaml. Defaults to the bundled file; tests
                 pass their own.
    """

    def __init__(self, config_path: Path = _DEFAULT_CONFIG) -> None:
        with config_path.open() as f:
            data = yaml.safe_load(f) or {}
        self._models: dict[str, dict] = data.get("models", {})
        self._check(config_path)

    def _check(self, path: Path) -> None:
        """Everything that must hold before the first request, not during it."""
        if not self._models:
            raise RoutingConfigError.no_models(path)
        for model in sorted(self._models):
            # Raises on an unknown level. Called here so a typo stops the
            # process rather than surfacing when a chain is judged, which is
            # the moment something is already going wrong.
            self.privacy_rank(model)

    def privacy_rank(self, model: str) -> int:
        """Position of a model's ceiling on PRIVACY_LEVELS, higher is stricter.

        An undeclared model raises rather than sorting last. A rule naming a
        model this deployment cannot reach is a defect in the rule, and
        treating it as "least privileged" would let it through a ratchet it
        was never measured against.
        """
        declared = self._models.get(model)
        if declared is None:
            raise RoutingConfigError.undeclared_model(model)
        level = declared["max_privacy_level"]
        if level not in PRIVACY_LEVELS:
            raise RoutingConfigError.unknown_level(model, level)
        return PRIVACY_LEVELS.index(level)

    def knows(self, model: str) -> bool:
        return model in self._models

    def models(self) -> dict[str, dict]:
        """What is declared, for callers that need to explain a decision."""
        return dict(self._models)
