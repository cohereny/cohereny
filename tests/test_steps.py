"""The compute steps (ADR-048, CHY-173).

The figures below were checked once against the pilot's own `node_calculate_ratios`
and rule part of `detect_anomalies` (2026-11-08, three cases, ratios equal to
1e-12, the same findings). Written out here rather than imported from the pilot:
the `test` job installs the core package, not the pilots, and the pilot is on
its way out (CHY-179). Where the pilot put 0.0 or 1.0 for a figure that was
missing, a step refuses — that is the decision of ADR-048, shown below.
"""
from __future__ import annotations

import pytest

import steps
from steps import banking

HEALTHY = {
    "bilanzsumme_current": 10_000_000, "eigenkapital_current": 3_500_000,
    "umlaufvermoegen_current": 4_000_000, "kurzfristige_verbindlichkeiten_current": 2_000_000,
    "kurzfristige_verbindlichkeiten_prior": 1_800_000, "umsatz_current": 12_000_000,
    "ebit_current": 900_000, "abschreibungen_current": 400_000,
}
ILLIQUID = {
    "bilanzsumme_current": 5_000_000, "eigenkapital_current": 1_250_000,
    "umlaufvermoegen_current": 1_500_000, "kurzfristige_verbindlichkeiten_current": 2_200_000,
    "kurzfristige_verbindlichkeiten_prior": 1_400_000, "umsatz_current": 6_000_000,
    "ebit_current": 150_000, "abschreibungen_current": 200_000,
}


def ratios(kpis):
    return banking.RATIOS.run({"kpis": kpis})


def anomalies(r):
    return banking.ANOMALIES.run({"ratios": r})


class TestRatios:
    def test_the_pilots_figures_for_a_healthy_company(self):
        r = ratios(HEALTHY)
        assert r["ek_quote"] == pytest.approx(0.35)
        assert r["current_ratio"] == pytest.approx(2.0)
        assert r["verschuldungsgrad"] == pytest.approx(6_500_000 / 3_500_000)
        assert r["ebitda_marge"] == pytest.approx(1_300_000 / 12_000_000)
        assert r["kv_veraenderung_yoy"] == pytest.approx(200_000 / 1_800_000)
        assert (r["eigenkapital_abs"], r["fremdkapital_abs"], r["ebitda_abs"]) == (
            3_500_000, 6_500_000, 1_300_000)

    def test_the_same_input_gives_the_same_output(self):
        assert ratios(HEALTHY) == ratios(dict(HEALTHY))

    def test_a_missing_figure_is_refused_where_the_pilot_put_zero(self):
        kpis = {k: v for k, v in HEALTHY.items() if k != "eigenkapital_current"}
        with pytest.raises(steps.StepInputError, match=r"^kpis\.eigenkapital_current is missing$"):
            ratios(kpis)

    def test_a_zero_denominator_is_a_reason_not_a_ratio(self):
        with pytest.raises(steps.StepInputError,
                           match=r"kpis\.kurzfristige_verbindlichkeiten_prior is 0, so "
                                 r"kv_veraenderung_yoy cannot be computed"):
            ratios({**HEALTHY, "kurzfristige_verbindlichkeiten_prior": 0})

    @pytest.mark.parametrize("bad", ["1.234.567", True, None, float("nan"), float("inf")])
    def test_what_is_not_a_finite_number_is_refused(self, bad):
        with pytest.raises(steps.StepInputError, match=r"kpis\.umsatz_current is not a finite number"):
            ratios({**HEALTHY, "umsatz_current": bad})

    def test_the_reason_never_carries_the_value(self):
        with pytest.raises(steps.StepInputError) as caught:
            ratios({**HEALTHY, "umsatz_current": "Erika Mustermann"})
        assert "Erika" not in str(caught.value)

    def test_without_the_mapped_input_the_step_says_which_key_it_needs(self):
        with pytest.raises(steps.StepInputError, match="^kpis is missing$"):
            banking.RATIOS.run({"run": {}})


class TestAnomalies:
    def test_no_finding_for_a_healthy_company(self):
        assert anomalies(ratios(HEALTHY)) == {"anomalies": [], "count": 0}

    def test_the_pilots_findings_for_an_illiquid_one(self):
        found = anomalies(ratios(ILLIQUID))
        assert found["count"] == 2
        assert found["anomalies"][0].startswith("LIQUIDITÄT: Current Ratio = 0.68 (< 1.0)")
        assert found["anomalies"][1].startswith("VERBINDLICHKEITEN: Kurzfristige Verbindlichkeiten YoY +57.1%")

    def test_low_equity_is_a_finding(self):
        found = anomalies({"current_ratio": 1.5, "kv_veraenderung_yoy": 0.0, "ek_quote": 0.125})
        assert found["anomalies"] == [
            "EIGENKAPITAL: EK-Quote = 12.5% (< 20%) — sehr niedrige Eigenkapitalbasis."]

    def test_a_missing_ratio_is_refused_where_the_pilot_assumed_one(self):
        """The pilot read a missing current ratio as 1.0 — no finding, silently."""
        with pytest.raises(steps.StepInputError, match=r"ratios\.current_ratio is missing"):
            anomalies({"kv_veraenderung_yoy": 0.0, "ek_quote": 0.3})

    # Taken over from the pilot's tests (CHY-178), which go with the pilot
    # (CHY-179): the thresholds of version 1 at their edges.

    def test_falling_liabilities_are_a_finding_too(self):
        found = anomalies({"current_ratio": 1.5, "kv_veraenderung_yoy": -0.4, "ek_quote": 0.35})
        [finding] = found["anomalies"]
        assert finding.startswith("VERBINDLICHKEITEN") and "gesunken" in finding  # codespell:ignore

    def test_a_value_at_a_threshold_is_no_finding(self):
        """Strictly below 1.0 and 20 %, strictly beyond ±30 %."""
        assert anomalies({
            "current_ratio": banking.CURRENT_RATIO_BELOW,
            "kv_veraenderung_yoy": banking.LIABILITIES_YOY_ABOVE,
            "ek_quote": banking.EQUITY_RATIO_BELOW,
        }) == {"anomalies": [], "count": 0}

    def test_every_rule_that_holds_is_reported(self):
        found = anomalies({"current_ratio": 0.65, "kv_veraenderung_yoy": 0.5, "ek_quote": 0.15})
        assert [a.split(":")[0] for a in found["anomalies"]] == [
            "LIQUIDITÄT", "VERBINDLICHKEITEN", "EIGENKAPITAL"]
        assert found["count"] == len(found["anomalies"])


class TestRegistry:
    def test_a_step_is_found_by_name_and_version(self):
        assert steps.lookup("banking.ratios", 1) is banking.RATIOS

    @pytest.mark.parametrize(("name", "version"), [
        ("banking.ratios", 2), ("banking.unknown", 1), (None, 1), ("banking.ratios", None)])
    def test_anything_else_is_not_a_step(self, name, version):
        assert steps.lookup(name, version) is None

    def test_known_lists_every_step_with_its_version(self):
        assert steps.known() == ["banking.anomalies@1", "banking.ratios@1"]
