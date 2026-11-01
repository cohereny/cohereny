"""Unit tests for deterministic (LLM-free) functions in the banking pilot.

All heavy dependencies are mocked in conftest.py. Only stdlib + pytest required.
Tests cover: _coerce_number, _extract_json, node_calculate_ratios,
             node_risk_gateway, route_after_gateway, node_detect_anomalies (rules only).
"""
import math
from unittest.mock import patch

from banking.nodes import (
    _coerce_number,
    _extract_json,
    node_calculate_ratios,
    node_risk_gateway,
    route_after_gateway,
    node_detect_anomalies,
)
from banking.config import (
    ANOMALY_THRESHOLD_CR,
    ANOMALY_THRESHOLD_YOY,
    ANOMALY_THRESHOLD_EQ,
)


# ── _coerce_number ────────────────────────────────────────────────────────────

class TestCoerceNumber:
    def test_none_returns_default_zero(self):
        assert _coerce_number(None) == 0.0

    def test_none_returns_custom_default(self):
        assert _coerce_number(None, default=5.0) == 5.0

    def test_int_becomes_float(self):
        assert _coerce_number(42) == 42.0

    def test_float_passthrough(self):
        assert math.isclose(_coerce_number(3.14), 3.14)

    def test_negative_number(self):
        assert _coerce_number(-100) == -100.0

    def test_german_thousand_separators(self):
        # "1.234.567" → dots stripped → "1234567" → 1234567.0
        assert _coerce_number("1.234.567") == 1_234_567.0

    def test_plain_integer_string(self):
        assert _coerce_number("8500000") == 8_500_000.0

    def test_invalid_string_returns_default(self):
        assert _coerce_number("abc") == 0.0

    def test_invalid_string_returns_custom_default(self):
        assert _coerce_number("abc", default=99.0) == 99.0

    def test_german_decimal_comma(self):
        # "3,14" → comma replaced with dot → 3.14
        assert math.isclose(_coerce_number("3,14"), 3.14)


# ── _extract_json ─────────────────────────────────────────────────────────────

class TestExtractJson:
    def test_valid_json_string(self):
        result = _extract_json('{"a": 1, "b": "foo"}')
        assert result == {"a": 1, "b": "foo"}

    def test_markdown_wrapped_json(self):
        text = '```json\n{"risk": "GRÜN", "score": 42}\n```'
        result = _extract_json(text)
        assert result["risk"] == "GRÜN"
        assert result["score"] == 42

    def test_json_embedded_in_text(self):
        text = 'Here is the result: {"level": "ROT"} — end.'
        result = _extract_json(text)
        assert result["level"] == "ROT"

    def test_no_json_returns_empty_dict(self):
        assert _extract_json("no json here at all") == {}

    def test_nested_json_object(self):
        text = '{"outer": {"inner": true}, "count": 3}'
        result = _extract_json(text)
        assert result["outer"] == {"inner": True}
        assert result["count"] == 3

    def test_malformed_json_returns_empty_dict(self):
        assert _extract_json("{invalid json}") == {}


# ── node_calculate_ratios ─────────────────────────────────────────────────────

def _make_ratio_state(**kpis):
    return {"kpis": kpis, "audit_log": []}


class TestNodeCalculateRatios:
    def test_gruen_fixture_values(self):
        """Fixture 01 — GRÜN: healthy mid-market manufacturer."""
        state = _make_ratio_state(
            bilanzsumme_current=8_500_000,
            bilanzsumme_prior=0,
            eigenkapital_current=3_570_000,
            umlaufvermoegen_current=4_200_000,
            kurzfristige_verbindlichkeiten_current=2_000_000,
            kurzfristige_verbindlichkeiten_prior=1_900_000,
            umsatz_current=12_000_000,
            ebit_current=1_800_000,
            abschreibungen_current=240_000,
            jahresueberschuss_current=1_200_000,
        )
        result = node_calculate_ratios(state)
        r = result["ratios"]

        assert math.isclose(r["ek_quote"], 3_570_000 / 8_500_000, rel_tol=1e-9)
        assert math.isclose(r["current_ratio"], 4_200_000 / 2_000_000, rel_tol=1e-9)
        assert math.isclose(r["kv_veraenderung_yoy"], (2_000_000 - 1_900_000) / 1_900_000, rel_tol=1e-9)
        assert math.isclose(r["ebitda_marge"], (1_800_000 + 240_000) / 12_000_000, rel_tol=1e-9)
        assert "audit_log" in result
        assert len(result["audit_log"]) == 1

    def test_rot_fixture_values(self):
        """Fixture 03 — ROT: distressed company with critical liquidity."""
        state = _make_ratio_state(
            bilanzsumme_current=7_200_000,
            bilanzsumme_prior=0,
            eigenkapital_current=360_000,
            umlaufvermoegen_current=1_800_000,
            kurzfristige_verbindlichkeiten_current=2_760_000,
            kurzfristige_verbindlichkeiten_prior=1_811_000,
            umsatz_current=9_800_000,
            ebit_current=-400_000,
            abschreibungen_current=180_000,
            jahresueberschuss_current=-560_000,
        )
        result = node_calculate_ratios(state)
        r = result["ratios"]

        assert math.isclose(r["ek_quote"], 360_000 / 7_200_000, rel_tol=1e-9)
        # CR below threshold → anomaly will fire
        assert r["current_ratio"] < ANOMALY_THRESHOLD_CR
        # KV-YoY above threshold → anomaly will fire
        assert r["kv_veraenderung_yoy"] > ANOMALY_THRESHOLD_YOY
        # EK-Quote below threshold → anomaly will fire
        assert r["ek_quote"] < ANOMALY_THRESHOLD_EQ

    def test_zero_bilanzsumme_no_division_error(self):
        """Zero Bilanzsumme must not raise; ratios default to 0.0."""
        state = _make_ratio_state(
            bilanzsumme_current=0,
            eigenkapital_current=1_000_000,
            umlaufvermoegen_current=500_000,
            kurzfristige_verbindlichkeiten_current=0,
            kurzfristige_verbindlichkeiten_prior=0,
            umsatz_current=0,
            ebit_current=0,
            abschreibungen_current=0,
        )
        result = node_calculate_ratios(state)
        r = result["ratios"]
        assert r["ek_quote"] == 0.0

    def test_zero_kv_prior_yoy_is_zero(self):
        """If kv_prior is 0, kv_veraenderung_yoy must be 0.0 (no division by zero)."""
        state = _make_ratio_state(
            bilanzsumme_current=5_000_000,
            eigenkapital_current=2_000_000,
            umlaufvermoegen_current=1_500_000,
            kurzfristige_verbindlichkeiten_current=800_000,
            kurzfristige_verbindlichkeiten_prior=0,
            umsatz_current=8_000_000,
            ebit_current=600_000,
            abschreibungen_current=100_000,
        )
        result = node_calculate_ratios(state)
        assert result["ratios"]["kv_veraenderung_yoy"] == 0.0


# ── node_risk_gateway ─────────────────────────────────────────────────────────

class TestNodeRiskGateway:
    def test_no_anomalies_routes_fast_track(self):
        state = {"anomalies": [], "audit_log": []}
        result = node_risk_gateway(state)
        assert result["routing_decision"] == "fast_track"

    def test_with_anomalies_routes_full_analysis(self):
        state = {"anomalies": ["LIQUIDITÄT: ..."], "audit_log": []}
        result = node_risk_gateway(state)
        assert result["routing_decision"] == "full_analysis"

    def test_audit_log_appended(self):
        state = {"anomalies": [], "audit_log": ["prev"]}
        result = node_risk_gateway(state)
        assert "prev" in result["audit_log"]
        assert len(result["audit_log"]) == 2

    def test_missing_anomalies_key_routes_fast_track(self):
        state = {"audit_log": []}
        result = node_risk_gateway(state)
        assert result["routing_decision"] == "fast_track"


# ── route_after_gateway ───────────────────────────────────────────────────────

class TestRouteAfterGateway:
    def test_routes_fast_track(self):
        assert route_after_gateway({"routing_decision": "fast_track"}) == "fast_track"

    def test_routes_full_analysis(self):
        assert route_after_gateway({"routing_decision": "full_analysis"}) == "full_analysis"

    def test_missing_key_defaults_to_full_analysis(self):
        assert route_after_gateway({}) == "full_analysis"


# ── node_detect_anomalies — rule-based only ───────────────────────────────────

def _ratio_state(cr, eq, kv_yoy, ebitda_marge=0.15):
    return {
        "ratios": {
            "current_ratio": cr,
            "ek_quote": eq,
            "kv_veraenderung_yoy": kv_yoy,
            "ebitda_marge": ebitda_marge,
        },
        "audit_log": [],
    }


class TestNodeDetectAnomaliesRules:
    def test_low_current_ratio_triggers_liquidity_flag(self):
        state = _ratio_state(cr=0.65, eq=0.35, kv_yoy=0.05)
        with patch("banking.nodes._call", return_value="LLM commentary"):
            result = node_detect_anomalies(state)
        assert any("LIQUIDITÄT" in a for a in result["anomalies"])
        assert not any("EIGENKAPITAL" in a for a in result["anomalies"])

    def test_low_ek_quote_triggers_equity_flag(self):
        state = _ratio_state(cr=1.5, eq=0.15, kv_yoy=0.05)
        with patch("banking.nodes._call", return_value="LLM commentary"):
            result = node_detect_anomalies(state)
        assert any("EIGENKAPITAL" in a for a in result["anomalies"])
        assert not any("LIQUIDITÄT" in a for a in result["anomalies"])

    def test_high_kv_yoy_increase_triggers_flag(self):
        state = _ratio_state(cr=1.5, eq=0.35, kv_yoy=0.40)
        with patch("banking.nodes._call", return_value="LLM commentary"):
            result = node_detect_anomalies(state)
        matching = [a for a in result["anomalies"] if "VERBINDLICHKEITEN" in a]
        assert matching
        assert "gestiegen" in matching[0]

    def test_high_kv_yoy_decrease_triggers_flag(self):
        state = _ratio_state(cr=1.5, eq=0.35, kv_yoy=-0.40)
        with patch("banking.nodes._call", return_value="LLM commentary"):
            result = node_detect_anomalies(state)
        matching = [a for a in result["anomalies"] if "VERBINDLICHKEITEN" in a]
        assert matching
        assert "gesunken" in matching[0]

    def test_all_within_thresholds_no_anomalies(self):
        state = _ratio_state(cr=2.1, eq=0.42, kv_yoy=0.05)
        # No _call mock needed: LLM is not invoked when anomalies list is empty
        result = node_detect_anomalies(state)
        assert result["anomalies"] == []

    def test_multiple_triggers_all_flags_present(self):
        """CR low + EK low + KV-YoY high — three rule triggers, LLM adds BEWERTUNG."""
        state = _ratio_state(cr=0.65, eq=0.15, kv_yoy=0.50)
        with patch("banking.nodes._call", return_value="Kritisch"):
            result = node_detect_anomalies(state)
        anomaly_text = " ".join(result["anomalies"])
        assert "LIQUIDITÄT" in anomaly_text
        assert "EIGENKAPITAL" in anomaly_text
        assert "VERBINDLICHKEITEN" in anomaly_text
        assert "BEWERTUNG" in anomaly_text

    def test_thresholds_are_exclusive_boundaries(self):
        """Values exactly at the threshold must NOT trigger (uses strict < / >)."""
        state = _ratio_state(
            cr=ANOMALY_THRESHOLD_CR,       # exactly 1.0 → no trigger
            eq=ANOMALY_THRESHOLD_EQ,       # exactly 0.20 → no trigger
            kv_yoy=ANOMALY_THRESHOLD_YOY,  # exactly 0.30 → no trigger
        )
        result = node_detect_anomalies(state)
        assert result["anomalies"] == []
