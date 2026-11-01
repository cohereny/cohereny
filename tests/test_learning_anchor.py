"""
tests/test_learning_anchor.py

A calibration anchor as a test case: what reads, what is refused, and how an
output is scored (ADR-043, ADR-006 layers 1 and 2, CHY-67).

Each validator has a case it passes and one it fails. A validator that scores
1.0 for everything passes every "it accepts" test and protects nothing — the
failing case is the one that shows it looks at the output at all.
"""
from __future__ import annotations

import json
from uuid import uuid4

import pytest

from contexts.learning import validators as v
from contexts.learning.anchor import Validators, parse
from contexts.learning.errors import MalformedAnchor

_BLOCK, _SCOPE = uuid4(), uuid4()


def _row(content: str = "The loan is approved.", **metadata) -> dict:
    case = {"task_type": "summarize", "input": "A credit file.",
            "raw_prompt": "Summarize the credit file.", **metadata}
    return {"id": uuid4(), "block_id": _BLOCK, "version": 3, "scope_id": _SCOPE,
            "content": content, "metadata": case, "block_type": "calibration_anchor"}


# ── Reading ───────────────────────────────────────────────────────────────────

class TestWhatReads:
    def test_a_complete_anchor(self):
        anchor = parse(_row(validators={"required_terms": ["loan"]}, models=["phi3-mini"]))
        assert (anchor.block_id, anchor.version, anchor.scope_id) == (_BLOCK, 3, _SCOPE)
        assert anchor.reference == "The loan is approved."
        assert anchor.validators.required_terms == ["loan"]
        assert anchor.validators.rouge_l is True, "rouge_l is on unless switched off"
        assert anchor.models == ["phi3-mini"]

    def test_metadata_written_as_json_text(self):
        """Rows from before CHY-164 carry metadata as a JSON string."""
        row = _row()
        row["metadata"] = json.dumps(row["metadata"])
        assert parse(row).task_type == "summarize"

    def test_without_rouge_l_the_reference_may_be_empty(self):
        """A structure-only case: JSON shape, no prose to compare against."""
        anchor = parse(_row(content="", validators={
            "rouge_l": False, "json_schema": {"type": "object"}}))
        assert anchor.reference == ""


class TestWhatIsRefused:
    @pytest.mark.parametrize(("change", "says"), [
        ({"raw_prompt": None}, "raw_prompt"),
        ({"raw_prompt": "   "}, "raw_prompt: must not be blank"),
        ({"task_type": ""}, "task_type"),
        ({"models": []}, "models"),
        ({"surprise": 1}, "unknown metadata key(s) surprise"),
        ({"validators": {"requird_terms": ["x"]}}, "validators.requird_terms"),
        ({"validators": {"regex": ["(unclosed"]}}, "does not compile"),
        ({"validators": {"json_schema": {"type": "nonsense"}}}, "not a valid JSON Schema"),
        ({"validators": {"length": {}}}, "min_chars, max_chars or both"),
        ({"validators": {"length": {"min_chars": 9, "max_chars": 3}}}, "greater than"),
        ({"validators": {"required_terms": [" "]}}, "a term is empty"),
    ])
    def test_a_defect_named_in_words_an_author_can_act_on(self, change, says):
        metadata = {k: val for k, val in change.items() if val is not None}
        row = _row(**metadata)
        for key, val in change.items():
            if val is None:
                del row["metadata"][key]
        with pytest.raises(MalformedAnchor) as refused:
            parse(row)
        assert says in str(refused.value)
        assert str(_BLOCK) in str(refused.value)

    def test_a_misspelt_validator_is_not_dropped(self):
        """The reason unknown keys are refused: dropped silently, the anchor
        would pass a check it never ran."""
        with pytest.raises(MalformedAnchor, match="forbiden_terms"):
            parse(_row(validators={"forbiden_terms": ["guarantee"]}))

    def test_rouge_l_against_nothing(self):
        with pytest.raises(MalformedAnchor, match="reference answer"):
            parse(_row(content="  "))

    def test_metadata_that_is_not_an_object(self):
        row = _row()
        row["metadata"] = ["a", "list"]
        with pytest.raises(MalformedAnchor, match="not an object"):
            parse(row)


# ── Scoring ───────────────────────────────────────────────────────────────────

class TestEachValidator:
    def test_json_schema(self):
        schema = {"type": "object", "required": ["decision"]}
        assert v.json_schema('{"decision": "approve"}', schema) == 1.0
        assert v.json_schema('{"verdict": "approve"}', schema) == 0.0
        assert v.json_schema("approve", schema) == 0.0, "not JSON at all"

    def test_regex_counts_the_share_found(self):
        assert v.regex("Risk: low. Score 7.", [r"Risk: \w+", r"Score \d+"]) == 1.0
        assert v.regex("Risk: low.", [r"Risk: \w+", r"Score \d+"]) == 0.5
        assert v.regex("nothing", [r"Risk: \w+"]) == 0.0

    def test_length_in_characters_of_the_stripped_output(self):
        assert v.length("12345", 3, 5) == 1.0
        assert v.length("123456", 3, 5) == 0.0
        assert v.length("12", 3, None) == 0.0
        assert v.length("   12   ", 3, None) == 0.0, "padding is not content"

    def test_required_terms_share_case_insensitive(self):
        assert v.required_terms("The LOAN and the Collateral", ["loan", "collateral"]) == 1.0
        assert v.required_terms("The loan", ["loan", "collateral"]) == 0.5
        assert v.required_terms("Nothing", ["loan"]) == 0.0

    def test_forbidden_terms_all_or_nothing(self):
        assert v.forbidden_terms("A careful assessment.", ["guarantee", "certain"]) == 1.0
        assert v.forbidden_terms("Approval is CERTAIN.", ["guarantee", "certain"]) == 0.0

    def test_rouge_l(self):
        reference = "the loan is approved with conditions"
        assert v.rouge_l(reference, reference) == 1.0
        # LCS "is" = 1 word; precision 1/4, recall 1/6, F1 = 2·(1/24)/(5/12) = 1/5
        assert v.rouge_l("weather is sunny today", reference) == pytest.approx(1 / 5)
        assert v.rouge_l("", reference) == 0.0
        assert v.rouge_l("banana", reference) == 0.0

    def test_rouge_l_respects_order(self):
        """A subsequence, not a bag of words: the same words reversed share
        only one word in order."""
        assert v.rouge_l("c b a", "a b c") == pytest.approx(1 / 3)


class TestTheScore:
    def test_the_mean_of_what_applies(self):
        validators = Validators(required_terms=["loan"], forbidden_terms=["certain"])
        result = v.score(validators, "the loan is approved", "the loan is approved")
        assert result.parts == {"required_terms": 1.0, "forbidden_terms": 1.0, "rouge_l": 1.0}
        assert result.total == 1.0

    def test_a_failing_part_lowers_it(self):
        validators = Validators(required_terms=["loan"], rouge_l=False)
        assert v.score(validators, "", "no match here").total == 0.0
        assert v.score(validators, "", "the loan").total == 1.0

    def test_rounded_to_what_the_column_stores(self):
        validators = Validators(regex=["a", "b", "c"], rouge_l=False)
        assert v.score(validators, "", "a").total == 0.3333

    def test_nothing_to_score_is_refused_not_invented(self):
        with pytest.raises(ValueError, match="no validator applies"):
            v.score(Validators(rouge_l=False), "", "anything")
