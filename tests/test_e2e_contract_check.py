"""
tests/test_e2e_contract_check.py

The E2E's contract check catches a vanished field (CHY-124, acceptance 2).

`scripts/e2e_context_lifecycle.py` validates every response against
openapi.yaml through an httpx hook. Before, a field could disappear from a
response and all checks stayed green, because each read the one value it
cared about. These drive `ContractCheck.validate` directly — the part that
decides — with a response shaped like the real one and the same response with
a field taken away.

The limit, stated rather than discovered later: the contract marks a field as
required only when the model gives it no default. `status` or `content`
vanishing is caught; `governance_key` (default None) vanishing is not.
"""
from __future__ import annotations

from uuid import uuid4

import pytest

from scripts.e2e_context_lifecycle import ContractCheck


def _block() -> dict:
    return {
        "id": str(uuid4()), "block_id": str(uuid4()), "version": 1,
        "superseded_by_version": None, "scope_id": str(uuid4()),
        "block_type": "system_prompt", "content": "Antworte knapp.",
        "metadata": {}, "tags": [], "author_id": str(uuid4()),
        "status": "draft", "created_at": "2026-09-24T12:00:00+00:00",
        "governance_key": None, "privacy_impact": None, "locked": False,
    }


@pytest.fixture(scope="module")
def contract() -> ContractCheck:
    return ContractCheck()


def test_a_real_response_passes(contract):
    """Counter-probe: without it, every test below could pass because the
    validator rejects everything."""
    assert contract.validate("POST", "/v1/blocks", 201, _block()) is None


@pytest.mark.parametrize("field", ["status", "content", "version", "block_id"])
def test_a_vanished_field_is_reported(contract, field):
    body = _block()
    del body[field]
    mismatch = contract.validate("POST", "/v1/blocks", 201, body)
    assert mismatch is not None, f"{field} disappeared and nothing noticed"
    assert field in mismatch


def test_a_field_of_the_wrong_type_is_reported(contract):
    body = {**_block(), "version": "1"}
    assert contract.validate("POST", "/v1/blocks", 201, body) is not None


def test_an_undocumented_status_is_reported(contract):
    """What the E2E found on its first run: PATCH answering 201 and
    /v1/complete answering 409 while the contract listed neither."""
    mismatch = contract.validate("GET", "/v1/blocks", 418, {})
    assert mismatch is not None and "undocumented status" in mismatch


def test_a_path_the_contract_does_not_know_is_not_its_business(contract):
    assert contract.validate("GET", "/metrics", 200, {"x": 1}) is None


def test_path_parameters_are_matched(contract):
    """`/v1/blocks/{block_id}/history` must match a concrete id, or every
    response under a path parameter would go unchecked."""
    body = {"block_id": str(uuid4()), "versions": [], "total": 0}
    assert contract.validate("GET", f"/v1/blocks/{uuid4()}/history", 200, body) is None
    assert contract.validate("GET", f"/v1/blocks/{uuid4()}/history", 200, {"total": 0}) is not None
