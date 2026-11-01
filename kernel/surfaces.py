"""
kernel/surfaces.py

The four published surfaces, their audiences and their machine clients
(ADR-031, CHY-109, INFRA-122).

cohereny is middleware: the reference UI is one consumer, an MCP server is to
become another, a customer integration a third. Interface segregation says none
of them should depend on operations it does not use — and measured on
19.08.2026, all of them depended on all of them:

  one audience, "cohereny", for 55 operations · cohereny-ui uses 8 of them ·
  its token may call all 55

Roles and audiences answer different questions. A role says *who may do what*;
an audience says *what this token was issued for*. One mechanism was answering
both, which meant a UI token reached an admin endpoint and was turned away only
if a role check happened to be there.

Why the vocabulary sits here and the gate does not:
  This is the same shape as kernel/roles.py — one declaration, everything else
  derived, no second list to forget. It also breaks a cycle: api/deps.py needs
  the audience set to validate a token, and the gate in api/surfaces.py needs
  api/deps.py to know who is calling. With the words in the kernel both
  import downward and neither imports the other.

Called by:
  - api.deps      (which audiences a token may carry)
  - api.surfaces  (the per-router gate)
  - api.v1.router (surface assignment)
  - scripts/export_openapi.py (one contract per surface)
"""
from enum import StrEnum

#: Prefix for every audience. Kept separate so the whole set moves together if
#: the naming ever changes -- four strings edited by hand is four chances to
#: mistype one, and a mistyped audience fails closed and silently.
AUDIENCE_PREFIX = "cohereny."


class Surface(StrEnum):
    """What a consumer needs, not what a resource is called.

    The resource grouping (/v1/blocks, /v1/workflows …) stays as it is. It was
    right as an outline and wrong only as a contract: "here is everything" is
    not a contract.
    """

    SERVE = "serve"        # machine consumers: complete, compose, route
    AUTHOR = "author"      # humans and the UI: blocks, reviews, scopes, pilots
    OBSERVE = "observe"    # dashboards and evidence: audit log, benchmarks
    ADMIN = "admin"        # operations: actors, anonymisation


#: surface → audience. Derived, never restated.
AUDIENCES: dict[str, str] = {s.value: f"{AUDIENCE_PREFIX}{s.value}" for s in Surface}

#: The Authentik application slug per surface. Dash, not dot: the audience is a
#: bare string and may carry one, a slug appears in a URL path.
#:
#: The reference installation's Terraform (infrastructure repository) builds
#: the same names.
#: That boundary cannot be collapsed — Terraform cannot import Python — so it is
#: stated in both places and named in both, the way kernel/roles.py and the
#: CHECK constraint in 001_initial.sql already are.
SLUGS: dict[str, str] = {s.value: f"cohereny-{s.value}" for s in Surface}


#: The machine client of each surface (INFRA-122, ADR-031 amended 24.09.2026).
#:
#: One per surface, next to the UI's, and never the UI's own provider with a
#: grant bolted on. Authentik's `client_credentials` has a mode in which the
#: client secret alone mints a token for an account Authentik creates itself
#: ("ak-<provider>-client_credentials") — enabled on the UI's provider, the
#: UI's secret would become a way to produce tokens with nobody behind them,
#: which the reference installation's Terraform ruled out on 21.08. A separate client
#: keeps that capability with a secret that lives only in SOPS and the CI.
#:
#: Still one surface per token: `cohereny.author.ci` opens the author surface
#: and no other, exactly as `cohereny.author` does.
CI_AUDIENCES: dict[str, str] = {s.value: f"{AUDIENCE_PREFIX}{s.value}.ci" for s in Surface}

#: Slugs of the machine clients. Mirrored in the reference installation's Terraform,
#: stated in both places for the reason SLUGS is.
CI_SLUGS: dict[str, str] = {s.value: f"cohereny-{s.value}-ci" for s in Surface}


def accepted_audiences(surface: str) -> frozenset[str]:
    """The audiences that open one surface: its UI client's and its machine
    client's, and no other surface's."""
    return frozenset({AUDIENCES[surface], CI_AUDIENCES[surface]})


#: Every audience this service issues, for the check that a token is ours at all.
ALL_AUDIENCES: frozenset[str] = frozenset(AUDIENCES.values()) | frozenset(CI_AUDIENCES.values())


def issuers(base_url: str) -> dict[str, str]:
    """issuer URL → surface, for every surface and both of its clients.

    Authentik issues one `iss` per application (issuer_mode per_provider), so
    there are eight, not one: four surfaces, each with the UI's application and
    the machine client's (INFRA-122). Verified against the running instance on
    21.08.2026 — the alternative, `global`, would have collapsed them to a
    single issuer at the price of a JWKS URL belonging to one arbitrary
    application and taking all of them down with it.
    """
    base = base_url.rstrip("/")
    return {
        **{f"{base}/{slug}/": surface for surface, slug in SLUGS.items()},
        **{f"{base}/{slug}/": surface for surface, slug in CI_SLUGS.items()},
    }
