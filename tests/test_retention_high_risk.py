"""
tests/test_retention_high_risk.py

The six-month rule for high-risk logs, without a database (CHY-266, AI Act Art. 26(6)).

The integration suite shows it at work (tests/integration/
test_high_risk_retention_db.py). Held here: which change counts as shortening a
high-risk scope below the minimum — the edges of the rule, where a number off by
one or a None read as zero would slip through.
"""
from __future__ import annotations

from uuid import uuid4

import pytest

from kernel.errors import HighRiskPeriodTooShort
from kernel.retention import HIGH_RISK_MIN_KEEP_DAYS, Periods, shortened_below_minimum

RISKY, CALM = uuid4(), uuid4()


def _periods(risky: int | None, calm: int | None = 30) -> Periods:
    return Periods(installation={"run_trace": 183, "run_content": 30},
                   by_scope={RISKY: {"run_trace": 183, "run_content": risky},
                             CALM: {"run_trace": 183, "run_content": calm}})


@pytest.mark.parametrize(("before", "after", "refused"), [
    (200, 182, True),     # one day under the minimum
    (200, 183, False),    # the minimum itself is enough
    (None, 90, True),     # from "without limit" to under the minimum
    (200, None, False),   # to "without limit" — longer, never shorter
    (30, 60, False),      # under the minimum, but longer than before
    (60, 30, True),       # under the minimum and shorter
])
def test_only_a_change_that_shortens_below_six_months_is_refused(before, after, refused):
    found = shortened_below_minimum("run_content", _periods(before), _periods(after), {RISKY})
    assert (found == {RISKY: {"run_content": after}}) is refused


def test_a_scope_without_a_high_risk_workflow_is_not_looked_at():
    found = shortened_below_minimum("run_content", _periods(200, calm=200),
                                    _periods(200, calm=7), {RISKY})
    assert found == {}


def test_a_class_the_rule_does_not_cover_is_not_looked_at():
    assert shortened_below_minimum("benchmark", _periods(200), _periods(7), {RISKY}) == {}


def test_the_minimum_is_six_months():
    assert HIGH_RISK_MIN_KEEP_DAYS == 183


def test_the_refusal_says_what_to_do():
    activating = HighRiskPeriodTooShort({RISKY: {"run_content": 30}}, 183, activating=True)
    changing = HighRiskPeriodTooShort({RISKY: {"run_trace": 90}}, 183, activating=False)
    assert "run_content 30 days" in str(activating) and "then activate" in str(activating)
    assert "run_trace 90 days" in str(changing) and "retire the workflow" in str(changing)
    assert "Art. 26(6)" in str(activating) and "Art. 26(6)" in str(changing)
