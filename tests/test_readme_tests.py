"""The README's test numbers, generated from a run (CHY-230).

The numbers went out stale once because somebody typed them in. These tests
hold scripts/readme_tests.py to what makes a generated number trustworthy: a
red run writes nothing, a directory without a row stops it rather than
vanishing into the total, and only the part between the markers is replaced.
"""
from __future__ import annotations

import datetime

import pytest

from scripts import readme_tests as rt


def _file(covered: int, statements: int) -> dict:
    return {"summary": {"covered_lines": covered, "num_statements": statements}}


#: Small enough to add up by hand: outside the pilots 180 of 200 lines (90 %),
#: with the pilots 200 of 300 (66.7 %).
COVERAGE = {
    "files": {
        "router/rules.py": _file(10, 10),
        "kernel/audit.py": _file(40, 40),
        "api/main.py": _file(30, 40),
        "contexts/orchestration/service.py": _file(80, 90),
        "core/db.py": _file(15, 15),
        "cli/main.py": _file(5, 5),
        "pilots/banking/banking/nodes.py": _file(15, 50),
        "pilots/study/study/graph.py": _file(4, 40),
        "pilots/study/scripts/ingest_questions.py": _file(1, 10),
    },
    "totals": {"covered_lines": 200, "num_statements": 300},
}
VERSIONS = {"python": "3.14", "pytest": "9.2", "coverage": "7.17"}


class TestPercent:
    def test_half_rounds_up_as_a_reader_would(self):
        # Python's round() and format() give 74 here (half to even).
        assert rt.percent(149, 200) == 75

    def test_no_statements_is_zero_not_a_division_error(self):
        assert rt.percent(0, 0) == 0


class TestByDirectory:
    def test_pilots_are_kept_apart_and_a_pilot_keeps_its_scripts(self):
        dirs = rt.by_directory(COVERAGE)
        assert dirs["pilots/study"] == (5, 50)
        assert dirs["pilots/banking"] == (15, 50)
        assert dirs["api"] == (30, 40)

    def test_a_directory_without_a_row_stops_it(self):
        coverage = {"files": {**COVERAGE["files"], "newzone/x.py": _file(1, 1)},
                    "totals": COVERAGE["totals"]}
        with pytest.raises(ValueError, match="newzone"):
            rt.by_directory(coverage)


class TestCounts:
    def test_passed_from_the_summary_line(self):
        assert rt.passed_count("...\n2020 passed, 169 deselected, 1 warning in 20.11s\n") == 2020

    @pytest.mark.parametrize("summary", [
        "1 failed, 2019 passed in 20.0s",
        "2019 passed, 1 error in 20.0s",
        "2018 passed, 2 errors in 20.0s",
    ])
    def test_a_red_run_is_refused(self, summary):
        with pytest.raises(ValueError, match="did not pass"):
            rt.passed_count(f"...\n{summary}\n")

    def test_no_summary_is_refused(self):
        with pytest.raises(ValueError, match="no pytest summary"):
            rt.passed_count("")

    @pytest.mark.parametrize("output", [
        "169 tests collected in 0.12s",
        "169/2189 tests collected (2020 deselected) in 0.30s",
    ])
    def test_collected(self, output):
        assert rt.collected_count(f"...\n{output}\n") == 169


class TestRender:
    block = rt.render(2020, 169, COVERAGE, datetime.date(2026, 11, 8), VERSIONS)

    def test_counts_and_the_figure_outside_the_pilots(self):
        assert "2,020 unit tests need no services; another 169 run against" in self.block
        assert "cover **90 %** of the lines outside the two pilots" in self.block
        assert "| **All of the above** | | **90 %** |" in self.block

    def test_the_figure_with_the_pilots_comes_from_the_totals(self):
        assert "| **Including the pilots** | | **67 %** |" in self.block
        assert "| The two original pilots | 30 %, 10 % |" in self.block

    def test_rows_best_covered_first(self):
        rows = [line.split("`")[1] for line in self.block.splitlines()
                if line.startswith("| `") and "pilots" not in line]
        # 100 % four times (by name), then contexts 89 %, api 75 %
        assert rows == ["cli/", "core/", "kernel/", "router/", "contexts/", "api/"]

    def test_date_tools_and_command(self):
        assert ("Measured on 2026-11-08 with Python 3.14, pytest 9.2 and coverage 7.17:"
                in self.block)
        assert "`pytest tests/ pilots/banking/tests pilots/study/tests --cov`" in self.block

    def test_framed_by_the_markers(self):
        assert self.block.startswith(rt.BEGIN)
        assert self.block.endswith(rt.END)


class TestReplaceSection:
    def test_only_the_part_between_the_markers_changes(self):
        text = f"## Tests\n\n{rt.BEGIN}\nold numbers\n{rt.END}\n\nBy hand.\n"
        out = rt.replace_section(text, f"{rt.BEGIN}\nnew\n{rt.END}")
        assert out == f"## Tests\n\n{rt.BEGIN}\nnew\n{rt.END}\n\nBy hand.\n"

    @pytest.mark.parametrize("text", [
        "no markers at all",
        f"{rt.BEGIN}\n{rt.END}\n{rt.BEGIN}\n{rt.END}",
        f"{rt.END}\n{rt.BEGIN}",
    ])
    def test_markers_missing_twice_or_reversed_are_refused(self, text):
        with pytest.raises(ValueError, match="exactly one pair"):
            rt.replace_section(text, "x")

    def test_the_committed_readme_can_be_generated(self):
        text = rt.README.read_text(encoding="utf-8")
        assert rt.replace_section(text, "x").count("x") >= 1
