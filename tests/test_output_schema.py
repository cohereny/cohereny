"""An llm_call's answer, read and checked against its schema (CHY-175).

What the runner does with a model's answer when its node names a shape: read
exactly one JSON object, check it, refuse with where and which rule — never with
what the model wrote, because that can be an identifier and the reason is stored
with the run. The runner side (that a mismatch fails the run, that an edge reads
the checked object) is in test_workflow_advance.py.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from contexts.orchestration import output_schema as os_
from contexts.orchestration.errors import NodeExecutionFailed
from contexts.orchestration.execution import NodeResult
from contexts.orchestration.schema import NodeCreate

RISK = {
    "type": "object",
    "required": ["risk_level", "reasoning"],
    "properties": {
        "risk_level": {"enum": ["low", "medium", "high"]},
        "reasoning": {"type": "string"},
    },
}
#: Stands for anything a model might write that identifies someone.
IDENTIFIER = "erika.mustermann@example.de"


class TestReadingTheAnswer:
    def test_a_bare_object(self):
        assert os_.read_answer('{"risk_level": "low"}') == {"risk_level": "low"}

    @pytest.mark.parametrize("fence", ["```json", "```"])
    def test_one_fenced_block_that_is_the_whole_answer(self, fence):
        text = f'{fence}\n{{"risk_level": "low"}}\n```\n'
        assert os_.read_answer(text) == {"risk_level": "low"}

    def test_text_around_a_fence_is_refused(self):
        with pytest.raises(os_.AnswerShapeError, match="whole answer"):
            os_.read_answer('Here you go:\n```json\n{"risk_level": "low"}\n```')

    def test_two_fenced_blocks_are_refused(self):
        with pytest.raises(os_.AnswerShapeError, match="is not JSON"):
            os_.read_answer('```json\n{"a": 1}\n```\n```json\n{"b": 2}\n```')

    def test_prose_is_refused(self):
        with pytest.raises(os_.AnswerShapeError, match="is not JSON"):
            os_.read_answer("The risk is low.")

    def test_json_that_is_not_an_object_is_refused(self):
        with pytest.raises(os_.AnswerShapeError, match=r"not an object \(list\)"):
            os_.read_answer('["low"]')


class TestCheckingIt:
    def test_a_matching_answer_passes(self):
        os_.check({"risk_level": "high", "reasoning": "two anomalies"}, RISK)

    def test_a_missing_field_is_named_by_the_rule(self):
        with pytest.raises(os_.AnswerShapeError, match=r"at \$: required"):
            os_.check({"risk_level": "high"}, RISK)

    def test_a_wrong_value_names_where_and_which_rule(self):
        with pytest.raises(os_.AnswerShapeError, match=r"at \$\.risk_level: enum"):
            os_.check({"risk_level": "severe", "reasoning": "x"}, RISK)

    def test_the_value_the_model_wrote_is_never_in_the_reason(self):
        with pytest.raises(os_.AnswerShapeError) as caught:
            os_.check({"risk_level": IDENTIFIER, "reasoning": "x"}, RISK)
        assert IDENTIFIER not in str(caught.value)
        assert "erika" not in str(caught.value)

    def test_a_long_rule_is_cut_in_the_reason(self):
        schema = {"type": "object", "properties": {"x": {"enum": [f"v{i}" for i in range(80)]}}}
        with pytest.raises(os_.AnswerShapeError) as caught:
            os_.check({"x": "nope"}, schema)
        assert str(caught.value).endswith("…")


class TestTheResult:
    def test_the_checked_object_is_added_as_data_and_the_answer_stays(self):
        text = '{"risk_level": "low", "reasoning": "none"}'
        out = os_.checked(NodeResult(output={"content": text}), RISK, "classify")
        assert out.output["data"] == {"risk_level": "low", "reasoning": "none"}
        assert out.output["content"] == text

    def test_a_mismatch_is_a_failed_node_that_names_itself(self):
        with pytest.raises(NodeExecutionFailed, match="Node classify failed: the model's answer"):
            os_.checked(NodeResult(output={"content": '{"risk_level": "low"}'}), RISK, "classify")

    def test_an_output_without_text_cannot_be_checked(self):
        with pytest.raises(NodeExecutionFailed, match="no text to check"):
            os_.checked(NodeResult(output={}), RISK, "classify")


class TestWritingTheNode:
    def _node(self, **kw):
        return NodeCreate(node_ref="classify", node_type="llm_call", task_type="classify", **kw)

    def test_a_schema_of_an_object_is_accepted(self):
        assert self._node(output_schema=RISK).output_schema == RISK

    def test_a_schema_of_anything_but_an_object_is_refused(self):
        with pytest.raises(ValidationError, match='"type": "object"'):
            self._node(output_schema={"type": "array"})

    def test_a_schema_that_is_not_a_json_schema_is_refused(self):
        with pytest.raises(ValidationError, match="not a valid JSON Schema"):
            self._node(output_schema={"type": "object", "properties": 5})

    def test_a_schema_in_the_config_is_refused_not_ignored(self):
        with pytest.raises(ValidationError, match=r"config\['output_schema'\] is not read"):
            self._node(config={"output_schema": RISK})

    def test_without_a_schema_nothing_changes(self):
        assert self._node().output_schema is None
