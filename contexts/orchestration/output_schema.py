"""
contexts/orchestration/output_schema.py

The shape a model's answer must have, checked inside the runner (CHY-175).

An `llm_call` may carry a JSON Schema (`workflow_nodes.output_schema`, migration
039). The runner then reads the answer as one JSON object, checks it against the
schema, and adds the checked object to the node's output as `data` — the place
an edge condition (`data.risk_level`) and a successor's input mapping
(`extract_kpis.data.revenue`) read from. A mismatch fails the node. There is no
default to fall back to: the banking pilot did that, and a misread figure went
on looking like a read one (ADR-004 — a gate that passes on silence is none).

Layer 1 of ADR-006, the deterministic check, inside the runner. The benchmark's
own `json_schema` validator scores an answer; this one stops a run. They share
the library and not the code: `contexts/learning` is another context, and a
score is a different thing from a refusal (ADR-030).

Two rules about what the check may say:

- **Never the value.** A refusal names where in the answer and which rule of
  the schema, nothing the model wrote. The answer can hold an identifier, and
  the reason is stored in `node_executions.error_message` and shown to whoever
  reads the run.
- **One fence, nothing else.** Models put JSON into a ```json block even when
  told not to. Exactly one such block is read, and only when it is the whole
  answer; text before or after it is refused, since that text is something the
  schema would not have seen.

Schemas check structure, not identifiers. The answer arrives with placeholders
(CHY-234), so a pattern for an IBAN would see `{{IBAN_1}}` and fail.
"""
from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from jsonschema import exceptions as jsonschema_exceptions
from jsonschema.validators import validator_for

from contexts.orchestration.errors import NodeExecutionFailed

if TYPE_CHECKING:
    # Only a type here: execution imports schema, and schema imports this
    # module to check a schema when a node is written.
    from contexts.orchestration.execution import NodeResult

#: An answer that is exactly one fenced block. `json` after the opening fence
#: is optional; anything outside the fence is not.
_FENCED = re.compile(r"\A\s*```(?:json)?[ \t]*\n(?P<body>.*?)\n?```\s*\Z", re.DOTALL)

#: How much of a rule's own value a refusal quotes. The value comes from the
#: schema, not from the answer, so it is safe to show — but an `enum` of fifty
#: entries is not a reason anybody reads.
_RULE_CHARS = 120


class AnswerShapeError(ValueError):
    """The answer does not have the shape its node asked for. The text is the reason.

    Each reason is built here, by name, so the sentence a run ends with is in
    one place — and none of them can quote the answer, only where and which rule.
    """

    @classmethod
    def not_json(cls, *, fenced_with_text: bool) -> AnswerShapeError:
        reason = "is not JSON"
        if fenced_with_text:
            reason += ": a fenced block is read only when it is the whole answer"
        return cls(reason)

    @classmethod
    def not_an_object(cls, found: str) -> AnswerShapeError:
        return cls(f"is JSON but not an object ({found})")

    @classmethod
    def mismatch(cls, path: str, rule: str, rule_value: str) -> AnswerShapeError:
        return cls(f"does not match its output schema at {path}: {rule} {rule_value}")


def schema_problem(schema: Any) -> str | None:
    """Why this cannot be a node's output schema, or None if it can.

    Checked when the node is written, so a broken schema is refused by the API
    and not discovered by the first run that reaches it.
    """
    if not isinstance(schema, Mapping):
        return "output_schema must be a JSON object"
    if schema.get("type") != "object":
        return ('output_schema must describe an object ("type": "object"): the answer '
                "is read as one JSON object, and its fields are what edges and "
                "successors address")
    try:
        validator_for(schema).check_schema(schema)
    except jsonschema_exceptions.SchemaError as exc:
        return f"output_schema is not a valid JSON Schema: {exc.message}"
    return None


def read_answer(content: str) -> dict[str, Any]:
    """The answer as one JSON object, or AnswerShapeError."""
    fenced = _FENCED.match(content)
    text = fenced["body"] if fenced else content
    try:
        answer = json.loads(text)
    except json.JSONDecodeError as exc:
        raise AnswerShapeError.not_json(
            fenced_with_text="```" in content and not fenced) from exc
    if not isinstance(answer, dict):
        raise AnswerShapeError.not_an_object(type(answer).__name__)
    return answer


def check(answer: dict[str, Any], schema: Mapping[str, Any]) -> None:
    """AnswerShapeError if the answer does not match the schema.

    The reason names the first problem jsonschema ranks highest: where in the
    answer (a JSON path) and which rule, with the rule's value from the schema.
    The offending value from the answer is never part of it.
    """
    validator = validator_for(schema)(schema)
    error = jsonschema_exceptions.best_match(validator.iter_errors(answer))
    if error is None:
        return
    rule = json.dumps(error.validator_value, ensure_ascii=False)
    if len(rule) > _RULE_CHARS:
        rule = rule[:_RULE_CHARS] + "…"
    raise AnswerShapeError.mismatch(error.json_path, str(error.validator), rule)


def checked(result: NodeResult, schema: Mapping[str, Any], node_ref: str) -> NodeResult:
    """The executor's result with the checked answer under `data`.

    The answer stays in `content` as it came; `data` is what was read from it.
    Raises NodeExecutionFailed, which the runner records as the node failing
    with this reason — the run stops and says why.
    """
    content = result.output.get("content")
    if not isinstance(content, str):
        raise NodeExecutionFailed(node_ref, "the model's answer has no text to check")
    try:
        answer = read_answer(content)
        check(answer, schema)
    except AnswerShapeError as exc:
        raise NodeExecutionFailed(node_ref, f"the model's answer {exc}") from exc
    return replace(result, output={**result.output, "data": answer})
