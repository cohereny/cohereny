"""
tests/test_node_dataflow.py

What one node hands to the next (CHY-131).

The module under test decides; it touches nothing. That is what makes these
tests arithmetic rather than integration — and it is why the wiring gets its
own test in `test_workflow_advance.py`: a correct `build_input` that nobody
calls is exactly the shape the bug had.
"""
from __future__ import annotations

import pytest

from contexts.orchestration import dataflow
from contexts.orchestration.dataflow import MISSING, RUN_INPUT


class TestResolvePath:
    def test_a_dotted_path_walks_nested_mappings(self):
        assert dataflow.resolve_path("a.b.c", {"a": {"b": {"c": 7}}}) == 7

    def test_a_missing_key_is_MISSING_and_not_None(self):
        """`None` is a value a node may legitimately produce.

        If absence were reported as None, a mapping could not tell "produced
        nothing" from "produced null", and the second would silently take the
        place of the first.
        """
        assert dataflow.resolve_path("a.b", {"a": {"b": None}}) is None
        assert dataflow.resolve_path("a.x", {"a": {"b": None}}) is MISSING

    def test_walking_into_a_non_mapping_is_a_miss_not_a_crash(self):
        assert dataflow.resolve_path("a.b.c", {"a": {"b": 5}}) is MISSING


class TestSources:
    def test_run_input_and_each_predecessor_keep_their_own_namespace(self):
        """The collision this design exists to prevent.

        Both the run and a node produce `content`. Merged, one would win and
        the loser would be whichever row came back second — a bug that depends
        on row order and therefore appears and disappears on its own.
        """
        available = dataflow.sources(
            run_input={"content": "from the run"},
            upstream={"summarise": {"content": "from the node"}},
        )
        assert available[RUN_INPUT]["content"] == "from the run"
        assert available["summarise"]["content"] == "from the node"


class TestApplyMapping:
    def test_a_path_that_leads_nowhere_is_left_out(self):
        """Not written as None.

        A node reading `input_data["prompt"]` should get a KeyError when
        nothing produced a prompt — not an empty string that reaches a model
        and comes back with an answer to no question.
        """
        mapped = dataflow.apply_mapping(
            {"prompt": "summarise.text"}, {"summarise": {}}
        )
        assert "prompt" not in mapped

    def test_a_non_string_passes_through_as_a_constant(self):
        mapped = dataflow.apply_mapping({"temperature": 0.2}, {})
        assert mapped == {"temperature": 0.2}


class TestBuildInput:
    def test_a_node_sees_the_run_and_its_predecessors_without_a_mapping(self):
        received = dataflow.build_input(
            run_input={"topic": "ZFS"},
            upstream={"fetch": {"text": "…"}},
            input_mapping=None,
        )
        assert received == {RUN_INPUT: {"topic": "ZFS"}, "fetch": {"text": "…"}}

    def test_mapped_keys_sit_on_top_rather_than_replacing(self):
        """An executor written against the raw shape keeps working.

        The alternative — the mapping *replacing* the structure — means adding
        a mapping to one node silently breaks every executor that reads the
        raw shape, and it breaks them at runtime, in a workflow, in production.
        """
        received = dataflow.build_input(
            run_input={"topic": "ZFS"},
            upstream={"fetch": {"text": "…"}},
            input_mapping={"prompt": "fetch.text"},
        )
        assert received["prompt"] == "…"
        assert received["fetch"] == {"text": "…"}
        assert received[RUN_INPUT] == {"topic": "ZFS"}

    def test_a_mapping_may_shadow_an_origin_and_that_is_a_decision(self):
        """The one case where overwriting is intent rather than accident."""
        received = dataflow.build_input(
            run_input={"topic": "ZFS"}, upstream={"fetch": {"text": "…"}},
            input_mapping={RUN_INPUT: "fetch.text"},
        )
        assert received[RUN_INPUT] == "…"

    def test_a_mapping_whose_path_misses_leaves_the_origin_standing(self):
        """Shadowing is not attempted-shadowing.

        A typo in a mapping must not blank out the run input — the node would
        then run on nothing while every row still says it ran.
        """
        received = dataflow.build_input(
            run_input={"topic": "ZFS"}, upstream={},
            input_mapping={RUN_INPUT: "nothing.here"},
        )
        assert received[RUN_INPUT] == {"topic": "ZFS"}


class TestOnePathResolver:
    """The guard and the mapping must read one address the same way.

    They were two implementations until CHY-131. Two implementations of "what
    does `a.b.c` mean" drift, and the day they disagree a condition and an
    input mapping read the same field differently — a bug nobody looks for,
    because the two features feel unrelated.
    """

    def test_conditions_uses_the_dataflow_resolver(self):
        from contexts.orchestration import conditions

        assert conditions._MISSING is MISSING

    @pytest.mark.parametrize("path", ["a", "a.b", "a.b.c", "missing", "a.missing"])
    def test_both_agree_on_every_shape(self, path):
        from contexts.orchestration import conditions

        data = {"a": {"b": {"c": 1}}}
        # `exists` compares against `value`, so this asks "is it there".
        found = dataflow.resolve_path(path, data) is not MISSING
        verdict = conditions.evaluate(
            {"op": "exists", "field": path, "value": True}, data
        )
        assert verdict is found
