"""
contexts/pilots/workflow_config.py

Composing the configuration a pilot run uses (CHY-42, ADR-030).

Named `workflow_config` and not `config`: `api/config.py` holds the
application's settings — where the database is, which issuer to trust. This is
workflow tuning, which is *data* and editable at runtime. Two different things
deserve two names, or the next reader has to open both to tell them apart.

What may be configured, and what may not
----------------------------------------
The pilots' `config.py` files mix two kinds of value. Infrastructure — the
LiteLLM base URL, Langfuse keys, POSTGRES_URI — stays in the environment and in
SOPS: a base URL a PUT can change is an SSRF vector, and a secret in a row is a
secret in every backup. Only tuning is data, and only tuning is here.

Why the snapshot is narrower than the table
-------------------------------------------
`effective_config` is meant to be the record of what a run actually used (EU AI
Act Art. 12). A value recorded there that no node reads makes the record
*false*, and unnoticeably so, because it looks complete.

ADR-028 point 4 keeps Study and Banking out of scope for rebuilding — the
runner of CHY-82 replaces them. So `model_map` is wired through and recorded;
`params` is stored and served but takes no effect yet, and is therefore left
out of the snapshot.

That has a consequence worth stating, because the alternative is the failure
mode this project keeps meeting: **an override of a section that does not take
effect is refused, not ignored.** Accepting `params` in a run request and
quietly doing nothing with it would be an API that reports success without
looking.

The aggregate of ADR-030: decides, does not execute. No IO here.
"""
from __future__ import annotations

from typing import Any

from contexts.pilots.errors import ConfigSectionNotEffective, UnknownPilotConfig

#: Sections of a workflow config that actually reach the pilots today.
#:
#: The one place this is decided. The snapshot is built from it, run overrides
#: are validated against it, and a test asserts the snapshot never carries
#: anything else — so widening it is a deliberate act with a visible diff, not
#: something that happens because somebody added a key.
EFFECTIVE_SECTIONS: tuple[str, ...] = ("model_map",)

#: Sections that exist in the table and are served by the API, but do not yet
#: change what a run does. Listed rather than implied: "not effective" and
#: "not known" produce very different, and very differently urgent, errors.
STORED_ONLY_SECTIONS: tuple[str, ...] = ("params",)

ALL_SECTIONS: tuple[str, ...] = EFFECTIVE_SECTIONS + STORED_ONLY_SECTIONS


def merge_section(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """One level of override, not a deep merge.

    A model map is tier → model id; there is no nesting to descend into, and a
    recursive merge would invent semantics for a shape that cannot occur. When
    `params` becomes effective and grows nested groups, this is the function to
    revisit — deliberately, and with the case in front of us rather than
    guessed at now.
    """
    return {**base, **override}


def check_override(run_config: dict[str, Any]) -> None:
    """Refuse a run override the pilots cannot honour.

    Two refusals, and they are different: a section that exists but takes no
    effect is a "not yet" with a ticket behind it, and an unknown section is a
    typo. Collapsing them into one message would send someone looking for a
    feature when they mistyped `model_maps`.

    Silently accepting either is the option this does not take. An API that
    takes an instruction and does nothing with it is worse than one that
    refuses: the caller believes their setting is in force.
    """
    for section in run_config:
        if section in EFFECTIVE_SECTIONS:
            continue
        if section in STORED_ONLY_SECTIONS:
            raise ConfigSectionNotEffective(section)
        raise UnknownPilotConfig(section, ALL_SECTIONS)


def compose_effective(
    *, pilot_name: str, version: int, stored: dict[str, Any], run_config: dict[str, Any]
) -> dict[str, Any]:
    """The record of what this run will use.

    Carries `pilot_name` and `version` alongside the values: without them a
    snapshot answers "what" but not "against which configuration", and telling
    "the config changed since" from "this run differed" is exactly what an
    audit six months later needs.

    Only effective sections are included. `check_override` has already refused
    anything else, so filtering here is not a second gate — it is what keeps
    the snapshot honest if `stored` grows a section before it is wired through.
    """
    return {
        "pilot_name": pilot_name,
        "config_version": version,
        **{
            section: merge_section(
                stored.get(section) or {}, run_config.get(section) or {}
            )
            for section in EFFECTIVE_SECTIONS
        },
    }
