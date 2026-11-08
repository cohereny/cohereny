"""
tests/test_openapi_contract.py

The published contract must match the running application (CHY-28, ADR-029).

Everything else in this suite asks "does this function behave as intended". This
file asks a different question: "did the contract change, and did anyone mean
it". A renamed response field or a removed endpoint breaks cohereny-ui, which
lives in a separate repository with its own pipeline and will not notice until
runtime. Here it becomes a diff in the merge request.

The last two assertions guard the two properties that make the rest of the API
readable from outside — no operation that claims success while doing nothing,
and no "planned" marker without a ticket behind it.
"""
from __future__ import annotations

import json
import re

import pytest

import yaml

from api.main import app
from api.v1.router import SURFACE_PATHS
from kernel.surfaces import Surface
from scripts.export_openapi import _targets, render


@pytest.mark.parametrize("path,text", _targets(), ids=lambda v: getattr(v, "name", ""))
def test_every_committed_contract_matches_the_application(path, text):
    """Five files now: the whole API and one per surface (CHY-109)."""
    assert path.exists(), f"{path.name} is missing — run scripts/export_openapi.py"
    assert path.read_text(encoding="utf-8") == text, (
        f"{path.name} is out of date. If the change was intended, run\n"
        "    python scripts/export_openapi.py\n"
        "and commit the result — the diff is the point (ADR-029)."
    )


def test_export_is_deterministic():
    """Two renders must be byte-identical.

    If they are not, the CI check fails at random and gets disabled within a
    week — which is how a guard becomes decoration.
    """
    assert render() == render()
    assert render(Surface.SERVE) == render(Surface.SERVE)


def _operations():
    for path, ops in app.openapi()["paths"].items():
        for method, op in ops.items():
            if isinstance(op, dict) and method in ("get", "post", "put", "patch", "delete"):
                yield path, method, op


def test_no_operation_answers_200_while_being_unimplemented():
    """The defect this ticket started from.

    Twenty-nine of fifty-four operations answered HTTP 200 with
    {"status": "not_implemented"}. 200 means "here is your result"; a generated
    client parses that body as success. They answer 501 now, and this asserts
    that nobody reintroduces the cheerful version.
    """
    offenders = [
        f"{m.upper()} {p}"
        for p, m, op in _operations()
        if op.get("x-cohereny-status") == "planned" and "200" in op.get("responses", {})
    ]
    assert not offenders, f"planned operations must not declare 200: {offenders}"


_PLANNED = [
    (p, m, op) for p, m, op in _operations() if op.get("x-cohereny-status") == "planned"
]


@pytest.mark.parametrize("path,method,op", _PLANNED, ids=[f"{m}:{p}" for p, m, _ in _PLANNED])
def test_planned_operations_name_a_ticket(path, method, op):
    """A 501 without a ticket is an apology; with one it is a plan."""
    ticket = op.get("x-cohereny-ticket", "")
    assert re.fullmatch(r"CHY-\d+", ticket), (
        f"{method.upper()} {path} is marked planned but names no ticket"
    )
    assert "501" in op["responses"]


def test_exactly_the_pilots_are_deprecated():
    """The LangGraph pilots and their question bank, and nothing else (2026-11-08).

    They call the model with the values and store them, which a workflow run
    does not. Until CHY-172 rebuilds them on the runner the contract says so on
    every operation; a deprecation that spreads to another router, or quietly
    disappears, shows up here.
    """
    deprecated = {(p, m) for p, m, op in _operations() if op.get("deprecated")}
    pilots = {(p, m) for p, m, _ in _operations()
              if p.startswith(("/v1/pilots", "/v1/questions"))}
    assert pilots, "no pilot operations found — the prefix changed"
    assert deprecated == pilots


# ── The per-surface contracts (CHY-109) ──────────────────────────────────────
#
# A contract that lists an operation the gate will refuse, or omits one it
# allows, is worse than no contract: it sends a consumer to build against
# something that does not work. These tests tie the two together.

_CONTRACTS = {s: yaml.safe_load(render(s)) for s in Surface}


@pytest.mark.parametrize("surface", list(Surface))
def test_a_contract_lists_exactly_what_its_surface_gates(surface):
    assert set(_CONTRACTS[surface]["paths"]) == SURFACE_PATHS[surface.value]


@pytest.mark.parametrize("surface", list(Surface))
def test_a_contract_has_no_dangling_schema_reference(surface):
    """Pruning is where this goes wrong quietly.

    A $ref to a schema that was pruned away still generates a client — one that
    fails on the first response it tries to parse.
    """
    spec = _CONTRACTS[surface]
    declared = set(spec.get("components", {}).get("schemas", {}))
    referenced = {
        m.rsplit("/", 1)[1]
        for m in re.findall(r"#/components/schemas/[A-Za-z0-9_.-]+", json.dumps(spec))
    }
    assert referenced <= declared, f"dangling: {sorted(referenced - declared)}"


@pytest.mark.parametrize("surface", list(Surface))
def test_a_contract_carries_fewer_schemas_than_the_whole_api(surface):
    """The point of splitting is that a consumer sees less, not the same.

    Without pruning each file would carry all ~31 models and interface
    segregation would be a claim in a document rather than a property of a file.
    """
    whole = len(yaml.safe_load(render())["components"]["schemas"])
    mine = len(_CONTRACTS[surface].get("components", {}).get("schemas", {}))
    assert mine < whole, f"{surface.value} carries every schema — pruning did nothing"


def test_the_surfaces_partition_the_api():
    """Every path is on exactly one surface, or deliberately on none."""
    published = set(app.openapi()["paths"])
    assigned = [p for paths in SURFACE_PATHS.values() for p in paths]

    assert len(assigned) == len(set(assigned)), "a path is gated by two surfaces"
    assert set(assigned) <= published, f"gated but unpublished: {set(assigned) - published}"
    # /v1/health takes no token by design; anything else appearing here is a
    # route that slipped past the gate.
    assert published - set(assigned) == {"/v1/health"}


def test_health_is_in_no_surface_contract():
    """It answers before authentication and belongs to no consumer group."""
    for surface, spec in _CONTRACTS.items():
        assert "/v1/health" not in spec["paths"], surface.value
