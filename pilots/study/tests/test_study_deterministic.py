"""Unit tests for deterministic (LLM-free) routing functions in the study pilot.

All heavy dependencies are mocked in conftest.py. Only stdlib + pytest required.
Tests cover: route_after_quality, route_after_evaluate.
"""
from study.nodes import route_after_quality, route_after_evaluate
from study.config import MAX_QUALITY_ITERATIONS, MAX_QUIZ_ITERATIONS


# ── route_after_quality ───────────────────────────────────────────────────────
# Signature: route_after_quality(state) -> "quiz" | "gap_analysis"
# Logic: "quiz" if quality_complete OR iteration_count >= MAX_QUALITY_ITERATIONS
#        else "gap_analysis"

class TestRouteAfterQuality:
    def test_quality_complete_routes_to_quiz(self):
        state = {"quality_complete": True, "iteration_count": 0, "quality_gaps": []}
        assert route_after_quality(state) == "quiz"

    def test_quality_complete_overrides_gaps(self):
        """quality_complete=True short-circuits even when gaps are present."""
        state = {"quality_complete": True, "iteration_count": 0, "quality_gaps": ["gap1"]}
        assert route_after_quality(state) == "quiz"

    def test_not_complete_routes_to_gap_analysis(self):
        state = {"quality_complete": False, "iteration_count": 0, "quality_gaps": ["gap1"]}
        assert route_after_quality(state) == "gap_analysis"

    def test_max_iterations_reached_routes_to_quiz(self):
        """Safeguard: stop the quality loop once MAX_QUALITY_ITERATIONS is reached."""
        state = {
            "quality_complete": False,
            "iteration_count": MAX_QUALITY_ITERATIONS,
            "quality_gaps": ["still gaps"],
        }
        assert route_after_quality(state) == "quiz"

    def test_beyond_max_iterations_routes_to_quiz(self):
        """Any count >= MAX_QUALITY_ITERATIONS exits the loop."""
        state = {
            "quality_complete": False,
            "iteration_count": MAX_QUALITY_ITERATIONS + 5,
            "quality_gaps": ["still gaps"],
        }
        assert route_after_quality(state) == "quiz"

    def test_iteration_one_below_max_routes_to_gap_analysis(self):
        """One iteration below MAX must still loop."""
        state = {
            "quality_complete": False,
            "iteration_count": MAX_QUALITY_ITERATIONS - 1,
            "quality_gaps": ["gap"],
        }
        assert route_after_quality(state) == "gap_analysis"

    def test_missing_iteration_count_defaults_to_zero(self):
        """state without iteration_count key must not raise."""
        state = {"quality_complete": False, "quality_gaps": ["gap"]}
        assert route_after_quality(state) == "gap_analysis"


# ── route_after_evaluate ──────────────────────────────────────────────────────
# Signature: route_after_evaluate(state) -> "__end__" | "quiz"
# Logic: "__end__" if lernziel_erreicht OR
#        iteration_count >= MAX_QUIZ_ITERATIONS + MAX_QUALITY_ITERATIONS
#        else "quiz"
# MAX_QUIZ_ITERATIONS + MAX_QUALITY_ITERATIONS = 2 + 3 = 5

_TOTAL_MAX = MAX_QUIZ_ITERATIONS + MAX_QUALITY_ITERATIONS


class TestRouteAfterEvaluate:
    def test_lernziel_erreicht_routes_to_end(self):
        state = {"lernziel_erreicht": True, "iteration_count": 1}
        assert route_after_evaluate(state) == "__end__"

    def test_lernziel_not_reached_low_iterations_routes_to_quiz(self):
        state = {"lernziel_erreicht": False, "iteration_count": 1}
        assert route_after_evaluate(state) == "quiz"

    def test_total_max_iterations_reached_routes_to_end(self):
        """Safeguard: stop the full loop once total max is reached."""
        state = {"lernziel_erreicht": False, "iteration_count": _TOTAL_MAX}
        assert route_after_evaluate(state) == "__end__"

    def test_beyond_total_max_routes_to_end(self):
        state = {"lernziel_erreicht": False, "iteration_count": 99}
        assert route_after_evaluate(state) == "__end__"

    def test_one_below_total_max_routes_to_quiz(self):
        """One iteration below total max must still allow another quiz round."""
        state = {"lernziel_erreicht": False, "iteration_count": _TOTAL_MAX - 1}
        assert route_after_evaluate(state) == "quiz"

    def test_lernziel_erreicht_overrides_max_check(self):
        """lernziel_erreicht=True must exit even if iteration count is low."""
        state = {"lernziel_erreicht": True, "iteration_count": 0}
        assert route_after_evaluate(state) == "__end__"

    def test_total_max_constant_is_correct(self):
        """Verify the combined cap matches expected value (regression guard)."""
        assert _TOTAL_MAX == 5
