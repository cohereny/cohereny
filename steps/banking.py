"""
steps/banking.py

The computed steps of the banking workflow (ADR-048, CHY-173, CHY-178).

Taken from the LangGraph pilot (`pilots/banking/banking/nodes.py`), with one
deliberate difference: where the pilot put 0.0 or 1.0 for a figure that was
missing or a ratio that could not be divided, these refuse (Thomas,
2026-11-08). For a statement where every figure is read, the results are the
pilot's.

The thresholds belong to the version. Changing one changes what a run finds,
and that is a new version, not an edit — a run recorded with
`banking.anomalies@1` has to mean the same thresholds a year from now.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from steps.base import Step, number, ratio, section

#: The pilot's thresholds (pilots/banking/banking/config.py), fixed for version 1.
CURRENT_RATIO_BELOW = 1.0
LIABILITIES_YOY_ABOVE = 0.30
EQUITY_RATIO_BELOW = 0.20


def _ratios_v1(data: Mapping[str, Any]) -> dict[str, Any]:
    """Key ratios from the figures `extract_kpis` read (input key `kpis`)."""
    kpis = section(data, "kpis")

    def read(key: str) -> float:
        return number(kpis, key, within="kpis")

    total_assets = read("bilanzsumme_current")
    equity = read("eigenkapital_current")
    current_assets = read("umlaufvermoegen_current")
    short_term_now = read("kurzfristige_verbindlichkeiten_current")
    short_term_prior = read("kurzfristige_verbindlichkeiten_prior")
    revenue = read("umsatz_current")
    ebit = read("ebit_current")
    depreciation = read("abschreibungen_current")

    debt = total_assets - equity
    ebitda = ebit + depreciation
    return {
        "ek_quote": ratio(equity, total_assets,
                          denominator_field="kpis.bilanzsumme_current", result="ek_quote"),
        "current_ratio": ratio(current_assets, short_term_now,
                               denominator_field="kpis.kurzfristige_verbindlichkeiten_current",
                               result="current_ratio"),
        "verschuldungsgrad": ratio(debt, equity,
                                   denominator_field="kpis.eigenkapital_current",
                                   result="verschuldungsgrad"),
        "ebitda_marge": ratio(ebitda, revenue,
                              denominator_field="kpis.umsatz_current", result="ebitda_marge"),
        "kv_veraenderung_yoy": ratio(short_term_now - short_term_prior, short_term_prior,
                                     denominator_field="kpis.kurzfristige_verbindlichkeiten_prior",
                                     result="kv_veraenderung_yoy"),
        "eigenkapital_abs": equity,
        "fremdkapital_abs": debt,
        "ebitda_abs": ebitda,
    }


def _anomalies_v1(data: Mapping[str, Any]) -> dict[str, Any]:
    """The rule-based findings of `detect_anomalies` (input key `ratios`).

    The texts are the pilot's: they go on to the model that comments on them.
    """
    ratios = section(data, "ratios")
    current_ratio = number(ratios, "current_ratio", within="ratios")
    liabilities_yoy = number(ratios, "kv_veraenderung_yoy", within="ratios")
    equity_ratio = number(ratios, "ek_quote", within="ratios")

    found: list[str] = []
    if current_ratio < CURRENT_RATIO_BELOW:
        found.append(
            f"LIQUIDITÄT: Current Ratio = {current_ratio:.2f} (< 1.0) — kurzfristige "
            f"Verbindlichkeiten übersteigen Umlaufvermögen. Akutes Liquiditätsrisiko."
        )
    if abs(liabilities_yoy) > LIABILITIES_YOY_ABOVE:
        direction = "gestiegen" if liabilities_yoy > 0 else "gesunken"
        found.append(
            f"VERBINDLICHKEITEN: Kurzfristige Verbindlichkeiten YoY {liabilities_yoy:+.1%} "
            f"({direction}). Schwellenwert: ±{LIABILITIES_YOY_ABOVE:.0%}."
        )
    if equity_ratio < EQUITY_RATIO_BELOW:
        found.append(
            f"EIGENKAPITAL: EK-Quote = {equity_ratio:.1%} (< {EQUITY_RATIO_BELOW:.0%}) — "
            f"sehr niedrige Eigenkapitalbasis."
        )
    return {"anomalies": found, "count": len(found)}


RATIOS = Step("banking.ratios", 1, _ratios_v1, reads=("kpis",))
ANOMALIES = Step("banking.anomalies", 1, _anomalies_v1, reads=("ratios",))
