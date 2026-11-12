"""
tests/test_unprocessable.py

Every 422 is documented in the shape that comes back (CHY-168).

FastAPI documents a 422 on every operation that takes input, as a list of field
errors. Until CHY-168 that was the contract on 58 of 65 such operations, while
a domain rule or the NUL handler answered with a sentence. A client generated
from it failed to parse exactly the message it had to show.

Three layers: the rule on small schemas, the rule's result in the real
contract, and the answers of the real application against the documented
model. The second layer checks the outcome, not the mechanism, so it fails
whether the rule is switched off or finds nothing to replace.
"""
from __future__ import annotations

import copy
import json

import pytest
from fastapi.openapi.utils import get_openapi

from api.main import app
from api.v1._responses import UNPROCESSABLE_ANYWHERE, UnprocessableDetail, document_unprocessable
from tests.conftest import client_for, make_pool

_LIST_ONLY = {
    "content": {"application/json": {"schema": {"$ref": "#/components/schemas/HTTPValidationError"}}},
    "description": "Validation Error",
}
_FASTAPI_SCHEMAS = {
    "HTTPValidationError": {"properties": {"detail": {
        "type": "array", "items": {"$ref": "#/components/schemas/ValidationError"}}}},
    "ValidationError": {"properties": {"loc": {}, "msg": {}, "type": {}}},
}


def _spec(*responses: dict, schemas: dict | None = None) -> dict:
    """A schema with one operation per 422 response given."""
    return {
        "paths": {f"/op{i}": {"post": {"responses": {"422": copy.deepcopy(r)}}}
                  for i, r in enumerate(responses)},
        "components": {"schemas": copy.deepcopy(schemas if schemas is not None else _FASTAPI_SCHEMAS)},
    }


def _ref(response: dict) -> str:
    return response["content"]["application/json"]["schema"]["$ref"]


def _operations(spec: dict):
    for path, item in spec["paths"].items():
        for method, op in item.items():
            yield method.upper(), path, op


# ── the rule ──────────────────────────────────────────────────────────────────

class TestTheRule:
    def test_fastapis_list_only_422_becomes_either_shape(self):
        spec = document_unprocessable(_spec(_LIST_ONLY))
        response = spec["paths"]["/op0"]["post"]["responses"]["422"]
        assert _ref(response) == "#/components/schemas/UnprocessableDetail"
        assert response["description"] == UNPROCESSABLE_ANYWHERE

    def test_a_routes_own_422_keeps_its_model_and_its_words(self):
        own = {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/Own"}}},
               "description": "Only this route's rule."}
        spec = document_unprocessable(_spec(own))
        assert spec["paths"]["/op0"]["post"]["responses"]["422"] == own

    def test_a_422_with_words_and_no_shape_keeps_the_words_and_gets_the_shape(self):
        """The document upload declared its 422 that way (CHY-174)."""
        spec = document_unprocessable(_spec({"description": "Too many pages."}))
        response = spec["paths"]["/op0"]["post"]["responses"]["422"]
        assert response["description"] == "Too many pages."
        assert _ref(response) == "#/components/schemas/UnprocessableDetail"

    def test_fastapis_schemas_go_once_nothing_points_at_them(self):
        spec = document_unprocessable(_spec(_LIST_ONLY))
        assert not {"HTTPValidationError", "ValidationError"} & set(spec["components"]["schemas"])

    def test_a_schema_something_still_points_at_stays(self):
        """Counter-probe to the line above: removing by name alone would leave
        a dangling reference behind."""
        own = {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/ValidationError"}}},
               "description": "A route that uses FastAPI's entry model itself."}
        spec = document_unprocessable(_spec(_LIST_ONLY, own))
        schemas = spec["components"]["schemas"]
        assert "HTTPValidationError" not in schemas
        assert "ValidationError" in schemas

    def test_the_model_is_added_when_no_route_brought_it(self):
        spec = document_unprocessable(_spec(_LIST_ONLY))
        schemas = spec["components"]["schemas"]
        assert schemas["UnprocessableDetail"]["title"] == "UnprocessableDetail"
        # its entry model comes with it, or the reference would dangle
        assert "#/components/schemas/FieldError" in json.dumps(schemas["UnprocessableDetail"])
        assert "FieldError" in schemas

    def test_a_second_call_changes_nothing(self):
        """FastAPI caches the schema and hands back the same dict."""
        once = document_unprocessable(_spec(_LIST_ONLY, {"description": "Too many pages."}))
        before = copy.deepcopy(once)
        assert document_unprocessable(once) == before


# ── the contract ──────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def spec() -> dict:
    return app.openapi()


class TestTheContract:
    def test_no_operation_promises_the_list_alone(self, spec):
        text = json.dumps(spec)
        assert "HTTPValidationError" not in text
        assert '"#/components/schemas/ValidationError"' not in text

    def test_every_operation_with_input_documents_either_shape(self, spec):
        missing = []
        for method, path, op in _operations(spec):
            if not (op.get("parameters") or "requestBody" in op):
                continue
            response = op.get("responses", {}).get("422")
            if response is None or _ref(response) != "#/components/schemas/UnprocessableDetail":
                missing.append(f"{method} {path}")
        assert missing == []

    def test_an_operation_without_input_documents_no_422(self, spec):
        """Nothing to validate and no text to carry a NUL: a 422 there would be
        a promise the operation cannot keep."""
        documented = [f"{m} {p}" for m, p, op in _operations(spec)
                      if not (op.get("parameters") or "requestBody" in op)
                      and "422" in op.get("responses", {})]
        assert documented == []

    def test_without_the_rule_the_contract_is_what_it_was(self):
        """Counter-probe: FastAPI's own schema of the same routes promises the
        list alone. The two tests above pass because of the rule, not because
        FastAPI changed."""
        raw = get_openapi(title=app.title, version=app.version, routes=app.routes)
        list_only = [f"{m} {p}" for m, p, op in _operations(raw)
                     if "HTTPValidationError" in json.dumps(op.get("responses", {}).get("422", {}))]
        assert len(list_only) > 50

    @pytest.mark.parametrize("path,method,words", [
        ("/v1/complete", "post", "max_tokens"),
        ("/v1/workflows/runs/{run_id}/documents/{name}", "put", "Too many pages"),
    ])
    def test_a_routes_own_words_survive(self, spec, path, method, words):
        assert words in spec["paths"][path][method]["responses"]["422"]["description"]


# ── what comes back ───────────────────────────────────────────────────────────

class TestWhatComesBack:
    """Both shapes from one operation that, until CHY-168, promised the list."""

    def test_a_rule_answers_with_a_sentence_the_contract_describes(self):
        with client_for(make_pool()) as client:
            response = client.get("/v1/blocks", params={"status": "nonsense"})
        assert response.status_code == 422, response.text
        body = UnprocessableDetail.model_validate(response.json())
        assert isinstance(body.detail, str)

    def test_validation_answers_with_a_list_the_contract_describes(self):
        with client_for(make_pool()) as client:
            response = client.get("/v1/blocks", params={"page": 0})
        assert response.status_code == 422, response.text
        body = UnprocessableDetail.model_validate(response.json())
        assert isinstance(body.detail, list)
        # the two fields FastAPI's own model documented, still described
        assert body.detail[0].input == "0"

    def test_the_operation_documents_the_model_both_answers_match(self):
        response = app.openapi()["paths"]["/v1/blocks"]["get"]["responses"]["422"]
        assert _ref(response) == "#/components/schemas/UnprocessableDetail"
