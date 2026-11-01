"""
contexts/learning/anchor.py

What a calibration anchor means (ADR-043, CHY-67).

An anchor is a benchmark test case kept as a block: versioned, scoped and
approved like any other content (ADR-006). The repository context owns the
row; what the row *means* is read here — the same division routing_rule has
with the router (CHY-121). Nothing in this module touches a database.

The block's fields, as an anchor uses them:

    content   the reference answer — what a good output looks like
    metadata  the case itself:
                task_type    which kind of task this measures
                input        the document or question the prompt is applied to
                raw_prompt   the unadapted prompt, frozen: the baseline the
                             composed context is measured against (ADR-019)
                validators   ADR-006 layers 1 and 2 (see Validators)
                models       optional — measure only against these

Unknown keys are refused, in the case and in the validators alike. A misspelt
validator would otherwise be dropped without a word, and the anchor would pass
a rule it never checked.
"""
from __future__ import annotations

import json
import re
from typing import Any
from uuid import UUID

from jsonschema import exceptions as jsonschema_exceptions
from jsonschema.validators import validator_for
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from contexts.learning.errors import MalformedAnchor


class LengthBounds(BaseModel):
    """Characters, not tokens: tokens depend on the model being measured."""

    model_config = ConfigDict(extra="forbid")

    min_chars: int | None = Field(default=None, ge=0)
    max_chars: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _a_bound_that_bounds(self) -> LengthBounds:
        if self.min_chars is None and self.max_chars is None:
            msg = "length needs min_chars, max_chars or both"
            raise ValueError(msg)
        if (self.min_chars is not None and self.max_chars is not None
                and self.min_chars > self.max_chars):
            msg = "length: min_chars is greater than max_chars"
            raise ValueError(msg)
        return self


class Validators(BaseModel):
    """ADR-006 layers 1 and 2. Deterministic, no model involved.

    Each one that is set contributes a value in [0, 1] to the score:

        json_schema      the output parses as JSON and conforms
        regex            share of the patterns found in the output
        length           output length within the bounds
        required_terms   share of the terms present (case-insensitive)
        forbidden_terms  1 if none of them occurs, else 0
        rouge_l          ROUGE-L F1 against the reference answer (on by default)

    Layer 3, the LLM judge, is not here: ADR-006 calls it the very last
    resort, and a model grading models is exactly the bias the pyramid is
    built to keep off the critical path.
    """

    model_config = ConfigDict(extra="forbid")

    json_schema: dict[str, Any] | None = None
    regex: list[str] = []
    length: LengthBounds | None = None
    required_terms: list[str] = []
    forbidden_terms: list[str] = []
    rouge_l: bool = True

    @field_validator("json_schema")
    @classmethod
    def _schema_is_a_schema(cls, schema: dict[str, Any] | None) -> dict[str, Any] | None:
        if schema is None:
            return None
        try:
            validator_for(schema).check_schema(schema)
        except jsonschema_exceptions.SchemaError as exc:
            msg = f"json_schema is not a valid JSON Schema: {exc.message}"
            raise ValueError(msg) from exc
        return schema

    @field_validator("regex")
    @classmethod
    def _patterns_compile(cls, patterns: list[str]) -> list[str]:
        for pattern in patterns:
            try:
                re.compile(pattern)
            except re.error as exc:
                msg = f"regex {pattern!r} does not compile: {exc}"
                raise ValueError(msg) from exc
        return patterns

    @field_validator("required_terms", "forbidden_terms")
    @classmethod
    def _terms_are_words(cls, terms: list[str]) -> list[str]:
        if any(not t.strip() for t in terms):
            msg = "a term is empty — it would be found in every output"
            raise ValueError(msg)
        return terms


class Anchor(BaseModel):
    """One calibration anchor version, read as a test case."""

    model_config = ConfigDict(extra="forbid")

    #: Line and version: a measurement names the anchor by both (migration 028).
    block_id: UUID
    version: int
    scope_id: UUID
    reference: str
    task_type: str = Field(min_length=1)
    input: str = Field(min_length=1)
    raw_prompt: str = Field(min_length=1)
    validators: Validators = Validators()
    models: list[str] | None = None

    @field_validator("task_type", "input", "raw_prompt")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            msg = "must not be blank"
            raise ValueError(msg)
        return value

    @field_validator("models")
    @classmethod
    def _models_name_something(cls, models: list[str] | None) -> list[str] | None:
        if models is not None and (not models or any(not m.strip() for m in models)):
            msg = "models, if given, is a non-empty list of model names"
            raise ValueError(msg)
        return models

    @model_validator(mode="after")
    def _something_to_compare_against(self) -> Anchor:
        if self.validators.rouge_l and not self.reference.strip():
            msg = "content — the reference answer — is empty, and rouge_l compares against it"
            raise ValueError(msg)
        return self


#: The metadata keys an anchor reads. Everything else in `metadata` is refused.
_CASE_KEYS = ("task_type", "input", "raw_prompt", "validators", "models")


def parse(row: Any) -> Anchor:
    """One stored calibration_anchor block as a test case.

    Takes a row as the repository hands it over (mapping access). Raises
    MalformedAnchor naming the first defect, in words an author can act on.
    """
    metadata = row["metadata"]
    if isinstance(metadata, str):
        # Rows written before CHY-164 carry the metadata as a JSON string.
        metadata = json.loads(metadata)
    if not isinstance(metadata, dict):
        raise MalformedAnchor(row["block_id"], "metadata is not an object")

    unknown = sorted(set(metadata) - set(_CASE_KEYS))
    if unknown:
        raise MalformedAnchor(
            row["block_id"],
            f"unknown metadata key(s) {', '.join(unknown)}; an anchor reads {', '.join(_CASE_KEYS)}",
        )

    try:
        return Anchor(
            block_id=row["block_id"], version=row["version"],
            scope_id=row["scope_id"], reference=row["content"], **metadata,
        )
    except ValidationError as exc:
        raise MalformedAnchor(row["block_id"], _first_defect(exc)) from exc


def _first_defect(exc: ValidationError) -> str:
    """The first error as `where: what` — one sentence an author can fix.

    pydantic's own rendering lists every error with URLs to its documentation,
    and the 422 that carries this is read by a person approving a block.
    """
    error = exc.errors()[0]
    where = ".".join(str(part) for part in error["loc"]) or "anchor"
    message = error["msg"].removeprefix("Value error, ")
    return f"{where}: {message}"
