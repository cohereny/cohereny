"""
Generates synthetic Banking Pilot fixture PDFs for testing and demos.

Every company, register number and auditor is invented, seats are a city
only, and each title page says the document is synthetic.

Fixtures
--------
01_solide_betrieb_gmbh.pdf       GRÜN  — solid KPIs, 0 anomalies, ~12 pages
02_monitoring_bedarf_gmbh.pdf    GELB  — low EK-Quote + KV spike (2 anomalies), ~10 pages
03_krisenfall_gmbh.pdf           ROT   — current ratio, EK-Quote, KV spike (3 anomalies), ~10 pages
04_klein_startup_gmbh.pdf        GRÜN  — minimal document, fast CI, ~4 pages
05_fehlende_kpis_gmbh.pdf        EDGE  — no revenue/EBIT disclosed, tests fallback to 0
06_english_annual_report_ltd.pdf EDGE  — English document, tests language detection

Anomaly thresholds (from config.py):
    ANOMALY_THRESHOLD_CR  = 1.0   (current_ratio < 1.0)
    ANOMALY_THRESHOLD_YOY = 0.30  (abs KV change > 30%)
    ANOMALY_THRESHOLD_EQ  = 0.20  (ek_quote < 20%)

Run:
    python generate_fixtures.py [--output-dir pdfs/]
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    HRFlowable,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

# ── Shared styles ─────────────────────────────────────────────────────────────

_base = getSampleStyleSheet()
BLUE = colors.HexColor("#003366")
LIGHT_BLUE = colors.HexColor("#dce6f0")
GREY = colors.HexColor("#666666")
RED_WARN = colors.HexColor("#8b0000")

H1 = ParagraphStyle("H1", parent=_base["Heading1"], textColor=BLUE, fontSize=16, spaceAfter=12)
H2 = ParagraphStyle("H2", parent=_base["Heading2"], textColor=BLUE, fontSize=13, spaceAfter=8, spaceBefore=16)
H3 = ParagraphStyle("H3", parent=_base["Heading3"], textColor=BLUE, fontSize=11, spaceAfter=6, spaceBefore=10)
BODY = ParagraphStyle("Body", parent=_base["Normal"], fontSize=9.5, leading=14, spaceAfter=6)
SMALL = ParagraphStyle("Small", parent=_base["Normal"], fontSize=8.5, leading=12, textColor=GREY)
RIGHT = ParagraphStyle("Right", parent=BODY, alignment=TA_RIGHT)
CENTER = ParagraphStyle("Center", parent=BODY, alignment=TA_CENTER)


def fmt(n: float) -> str:
    """Format number as German EUR string: 1.234.567"""
    return f"{n:,.0f}".replace(",", ".")


def tbl(has_header: bool = True) -> TableStyle:
    base = [
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#cccccc")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]
    if has_header:
        base += [
            ("BACKGROUND", (0, 0), (-1, 0), BLUE),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ]
    return TableStyle(base)


def bold_last(rows: int) -> TableStyle:
    """Table style that bolds and highlights the last row (totals)."""
    s = tbl()
    s.add("FONTNAME", (0, rows - 1), (-1, rows - 1), "Helvetica-Bold")
    s.add("BACKGROUND", (0, rows - 1), (-1, rows - 1), LIGHT_BLUE)
    return s


# ── Fixture config ────────────────────────────────────────────────────────────

@dataclass
class F:
    """All parameters needed to generate one fixture PDF."""

    # Identity
    filename: str
    company: str
    city: str
    hrb: str
    sitz: str
    activity: str
    year: str = "2024"
    year_prior: str = "2023"
    language: Literal["de", "en"] = "de"
    size: Literal["klein", "mittel"] = "mittel"  # klein = no Lagebericht/CF

    # Expected test outcome (documented in script, not enforced programmatically)
    expected_risk: str = "GRÜN"
    expected_anomalies: list[str] = field(default_factory=list)

    # ── Balance sheet (all EUR integers) ─────────────────────────────────────
    bilanzsumme: int = 0
    bilanzsumme_prior: int = 0
    eigenkapital: int = 0
    eigenkapital_prior: int = 0
    stammkapital: int = 0
    kapitalruecklage: int = 0
    gewinnruecklagen: int = 0
    jahresueberschuss: int = 0          # also used in GuV
    jahresueberschuss_prior: int = 0

    # Umlaufvermögen (current assets)
    umlaufvermoegen: int = 0
    umlaufvermoegen_prior: int = 0
    vorraete: int = 0
    forderungen: int = 0
    kassenbestand: int = 0

    # Liabilities
    kv_current: int = 0                 # kurzfristige Verbindlichkeiten (key KPI)
    kv_prior: int = 0
    lv_current: int = 0                 # langfristige Verbindlichkeiten
    rueckstellungen: int = 0

    # ── P&L ──────────────────────────────────────────────────────────────────
    umsatz: int = 0
    umsatz_prior: int = 0
    materialaufwand: int = 0
    personalaufwand: int = 0
    abschreibungen: int = 0
    sonstige_aufwendungen: int = 0
    ebit: int = 0
    zinsen_aufwand: int = 0

    # ── Narrative ─────────────────────────────────────────────────────────────
    anhang_text: str = ""
    lagebericht_lage: str = ""
    lagebericht_risiken: str = ""
    auditor_note: str = ""              # optional qualification in Bestätigungsvermerk


# ── Fixture definitions ───────────────────────────────────────────────────────

FIXTURES: list[F] = [

    # ── 01: GRÜN — solid company, 0 anomalies ────────────────────────────────
    # EK-Quote = 3.570.000 / 8.500.000 = 42%    ✓ (> 20%)
    # Current Ratio = 2.100.000 / 1.000.000 = 2.10  ✓ (> 1.0)
    # KV-YoY = (1.000.000 - 950.000) / 950.000 = +5.3%  ✓ (< 30%)
    # EBITDA-Marge = (960.000 + 720.000) / 12.000.000 = 14.0%
    F(
        filename="01_solide_betrieb_gmbh.pdf",
        company="Solide Betrieb GmbH",
        city="Mainz",
        hrb="HRB 00001 Mainz",
        sitz="Mainz",
        activity="Herstellung von Industriearmaturen und Ventiltechnik",
        size="mittel",
        expected_risk="GRÜN",
        expected_anomalies=[],
        bilanzsumme=8_500_000, bilanzsumme_prior=8_100_000,
        eigenkapital=3_570_000, eigenkapital_prior=3_250_000,
        stammkapital=2_000_000, kapitalruecklage=300_000,
        gewinnruecklagen=750_000, jahresueberschuss=520_000,
        jahresueberschuss_prior=480_000,
        umlaufvermoegen=2_100_000, umlaufvermoegen_prior=2_000_000,
        vorraete=850_000, forderungen=1_100_000, kassenbestand=150_000,
        kv_current=1_000_000, kv_prior=950_000,
        lv_current=3_650_000, rueckstellungen=280_000,
        umsatz=12_000_000, umsatz_prior=11_200_000,
        materialaufwand=6_600_000, personalaufwand=2_880_000,
        abschreibungen=720_000, sonstige_aufwendungen=840_000,
        ebit=960_000, zinsen_aufwand=220_000,
        anhang_text=(
            "Die Gesellschaft weist eine stabile Eigenkapitalbasis von 42,1% auf. "
            "Die Current Ratio von 2,10 zeigt ausreichende kurzfristige Liquidität. "
            "Alle Verbindlichkeiten werden fristgerecht bedient."
        ),
        lagebericht_lage=(
            "Die Solide Betrieb GmbH erzielte im Geschäftsjahr 2024 einen Umsatz von "
            "EUR 12,0 Mio. (+7,1%). Das EBIT stieg auf EUR 960.000, die EBITDA-Marge "
            "beträgt 14,0%. Die Liquiditätslage ist stabil."
        ),
        lagebericht_risiken=(
            "Wesentliche Risiken: Rohstoffpreisvolatilität (gering), Fachkräftemangel (mittel). "
            "Keine existenzgefährdenden Risiken erkennbar."
        ),
    ),

    # ── 02: GELB — 2 anomalies: low EK-Quote + KV spike ─────────────────────
    # EK-Quote = 2.040.000 / 12.000.000 = 17.0%   ✗ anomaly (< 20%)
    # Current Ratio = 2.800.000 / 2.000.000 = 1.40  ✓ (> 1.0)
    # KV-YoY = (2.000.000 - 1.450.000) / 1.450.000 = +37.9%  ✗ anomaly (> 30%)
    # EBITDA-Marge = (570.000 + 190.000) / 9.500.000 = 8.0%
    F(
        filename="02_monitoring_bedarf_gmbh.pdf",
        company="Monitoring Bedarf GmbH",
        city="Darmstadt",
        hrb="HRB 00002 Darmstadt",
        sitz="Darmstadt",
        activity="Großhandel mit elektronischen Bauelementen und Industriekomponenten",
        size="mittel",
        expected_risk="GELB",
        expected_anomalies=["EK-Quote 17% < 20%", "KV-YoY +38% > 30%"],
        bilanzsumme=12_000_000, bilanzsumme_prior=11_200_000,
        eigenkapital=2_040_000, eigenkapital_prior=1_940_000,
        stammkapital=1_500_000, kapitalruecklage=200_000,
        gewinnruecklagen=130_000, jahresueberschuss=210_000,
        jahresueberschuss_prior=190_000,
        umlaufvermoegen=2_800_000, umlaufvermoegen_prior=2_600_000,
        vorraete=1_200_000, forderungen=1_400_000, kassenbestand=200_000,
        kv_current=2_000_000, kv_prior=1_450_000,
        lv_current=7_600_000, rueckstellungen=360_000,
        umsatz=9_500_000, umsatz_prior=9_100_000,
        materialaufwand=6_080_000, personalaufwand=1_900_000,
        abschreibungen=190_000, sonstige_aufwendungen=760_000,
        ebit=570_000, zinsen_aufwand=290_000,
        anhang_text=(
            "Die Eigenkapitalquote beträgt 17,0% und liegt damit unterhalb des branchenüblichen "
            "Referenzwerts von 20%. Die kurzfristigen Verbindlichkeiten stiegen um 37,9% infolge "
            "der Inanspruchnahme einer erweiterten Kreditlinie zur Finanzierung von Lageraufbau. "
            "Die Gesellschaft führt Gespräche mit Banken über eine Umschuldung."
        ),
        lagebericht_lage=(
            "Umsatz: EUR 9,5 Mio. (+4,4%). EBIT: EUR 570.000. EBITDA-Marge: 8,0%. "
            "Erhöhter Lagerbestand aufgrund vorgezogener Bestellungen bei Schlüssellieferanten."
        ),
        lagebericht_risiken=(
            "Risiken: Erhöhter Verschuldungsgrad (FK/EK 4,88x), Abhängigkeit von drei "
            "Hauptlieferanten (62% des Einkaufsvolumens). Monitoring durch Kreditkomitee "
            "vierteljährlich."
        ),
        auditor_note=(
            "Wir weisen darauf hin, dass die Eigenkapitalquote (17,0%) und der Anstieg der "
            "kurzfristigen Verbindlichkeiten (+37,9%) besonderer Beobachtung bedürfen. "
            "Dies begründet keine Einschränkung unseres Prüfungsurteils."
        ),
    ),

    # ── 03: ROT — 3 anomalies: current ratio + EK-Quote + KV spike ──────────
    # EK-Quote = 360.000 / 7.200.000 = 5.0%    ✗ anomaly (< 20%)
    # Current Ratio = 1.440.000 / 2.210.000 = 0.65  ✗ anomaly (< 1.0)
    # KV-YoY = (2.210.000 - 1.450.000) / 1.450.000 = +52.4%  ✗ anomaly (> 30%)
    # EBITDA-Marge = (-400.000 + 180.000) / 6.800.000 = -3.2%
    F(
        filename="03_krisenfall_gmbh.pdf",
        company="Krisenfall GmbH",
        city="Offenbach",
        hrb="HRB 00003 Offenbach",
        sitz="Offenbach am Main",
        activity="Herstellung und Vertrieb von Kunststoffverpackungen",
        size="mittel",
        expected_risk="ROT",
        expected_anomalies=["Current Ratio 0.65 < 1.0", "EK-Quote 5% < 20%", "KV-YoY +52% > 30%"],
        bilanzsumme=7_200_000, bilanzsumme_prior=7_800_000,
        eigenkapital=360_000, eigenkapital_prior=880_000,
        stammkapital=500_000, kapitalruecklage=0,
        gewinnruecklagen=380_000, jahresueberschuss=-520_000,  # Jahresfehlbetrag
        jahresueberschuss_prior=-140_000,
        umlaufvermoegen=1_440_000, umlaufvermoegen_prior=2_100_000,
        vorraete=680_000, forderungen=620_000, kassenbestand=140_000,
        kv_current=2_210_000, kv_prior=1_450_000,
        lv_current=4_450_000, rueckstellungen=180_000,
        umsatz=6_800_000, umsatz_prior=8_200_000,
        materialaufwand=4_420_000, personalaufwand=2_040_000,
        abschreibungen=180_000, sonstige_aufwendungen=940_000,
        ebit=-400_000, zinsen_aufwand=195_000,
        anhang_text=(
            "Die Gesellschaft weist zum 31. Dezember 2024 einen Jahresfehlbetrag von "
            "EUR 520.000 aus (Vorjahr: EUR -140.000). Das Eigenkapital ist auf EUR 360.000 "
            "gesunken (Vorjahr: EUR 880.000). Die Eigenkapitalquote beträgt 5,0%. "
            "Die kurzfristigen Verbindlichkeiten überschreiten das Umlaufvermögen "
            "(Current Ratio: 0,65). Es wurden Gespräche mit dem Insolvenzberater aufgenommen. "
            "Die Geschäftsführung prüft Restrukturierungsoptionen einschließlich Kapitalerhöhung "
            "und Verkauf von Sachanlagen."
        ),
        lagebericht_lage=(
            "Umsatz: EUR 6,8 Mio. (-17,1%). Jahresfehlbetrag: EUR -520.000. "
            "Der Umsatzrückgang ist auf den Verlust eines Großkunden (ca. 22% des Umsatzes) "
            "sowie auf den gestiegenen Energie- und Rohstoffaufwand zurückzuführen."
        ),
        lagebericht_risiken=(
            "KRITISCH: Akutes Liquiditätsrisiko — kurzfristige Verbindlichkeiten übersteigen "
            "Umlaufvermögen. Negative EK-Entwicklung. Umsatzrückgang -17,1%. "
            "Zwei Banken haben Kreditlinien zum 31.03.2025 gekündigt. "
            "Die Fortführung des Unternehmens ist nur unter Voraussetzung einer "
            "Kapitalerhöhung oder Gesellschafterdarlehen gewährleistet."
        ),
        auditor_note=(
            "HINWEIS ZUR UNTERNEHMENSFORTFÜHRUNG: Wir weisen auf den Abschnitt 6.5 "
            "(Risikobericht) hin, der wesentliche Unsicherheiten im Zusammenhang mit "
            "der Unternehmensfortführung beschreibt. Die aktuelle Liquiditätslage "
            "(Current Ratio 0,65) und der Eigenkapitalschwund begründen erhebliche "
            "Zweifel an der Fortführungsfähigkeit. Unser Prüfungsurteil ist dahingehend "
            "eingeschränkt, dass wir keine hinreichende Sicherheit über die "
            "Unternehmensfortführung erhalten konnten."
        ),
    ),

    # ── 04: GRÜN klein — fast CI fixture, minimal document ──────────────────
    # EK-Quote = 425.000 / 850.000 = 50.0%   ✓
    # Current Ratio = 510.000 / 170.000 = 3.0  ✓
    # KV-YoY = (170.000 - 160.000) / 160.000 = +6.3%  ✓
    F(
        filename="04_klein_startup_gmbh.pdf",
        company="Klein Startup GmbH",
        city="Wiesbaden",
        hrb="HRB 00004 Wiesbaden",
        sitz="Wiesbaden",
        activity="IT-Dienstleistungen und Softwareentwicklung",
        size="klein",
        expected_risk="GRÜN",
        expected_anomalies=[],
        bilanzsumme=850_000, bilanzsumme_prior=720_000,
        eigenkapital=425_000, eigenkapital_prior=370_000,
        stammkapital=25_000, kapitalruecklage=0,
        gewinnruecklagen=345_000, jahresueberschuss=55_000,
        jahresueberschuss_prior=45_000,
        umlaufvermoegen=510_000, umlaufvermoegen_prior=420_000,
        vorraete=0, forderungen=380_000, kassenbestand=130_000,
        kv_current=170_000, kv_prior=160_000,
        lv_current=230_000, rueckstellungen=25_000,
        umsatz=1_200_000, umsatz_prior=980_000,
        materialaufwand=120_000, personalaufwand=840_000,
        abschreibungen=48_000, sonstige_aufwendungen=96_000,
        ebit=96_000, zinsen_aufwand=18_000,
        anhang_text=(
            "Junges IT-Dienstleistungsunternehmen. Eigenkapitalquote 50,0%. "
            "Current Ratio 3,00. Keine wesentlichen finanziellen Risiken."
        ),
        lagebericht_lage="",  # not rendered for "klein"
        lagebericht_risiken="",
    ),

    # ── 05: EDGE — fehlende KPIs (kein Umsatz, kein EBIT) ───────────────────
    # Purpose: test LLM fallback when key figures are missing from the document.
    # The document is a Vorratsgesellschaft (shelf company, no operations yet).
    # LLM should return 0 for umsatz and ebit → ratios are undefined / 0.
    # EK-Quote = 25.000 / 25.000 = 100%   ✓ (no debt at all)
    # Current Ratio: UV=25.000, KV=0 → safe_div returns 0 → anomaly (< 1.0)
    # KV-YoY: kv_prior=0 → code returns 0 → no anomaly
    # Expected: EK anomaly = none (100%), Current Ratio anomaly (0 < 1.0) — edge behavior
    F(
        filename="05_fehlende_kpis_gmbh.pdf",
        company="Fehlende KPIs Verwaltungs GmbH",
        city="Frankfurt am Main",
        hrb="HRB 00005 Frankfurt am Main",
        sitz="Frankfurt am Main",
        activity="Verwaltung eigener Beteiligungen (Vorratsgesellschaft)",
        size="klein",
        expected_risk="GRÜN",  # LLM should see 100% EK, no debt — but may differ
        expected_anomalies=["Current Ratio ergibt 0 wegen KV=0 (edge: Fallback-Logik)"],
        bilanzsumme=25_000, bilanzsumme_prior=25_000,
        eigenkapital=25_000, eigenkapital_prior=25_000,
        stammkapital=25_000, kapitalruecklage=0,
        gewinnruecklagen=0, jahresueberschuss=0,
        jahresueberschuss_prior=0,
        umlaufvermoegen=25_000, umlaufvermoegen_prior=25_000,
        vorraete=0, forderungen=0, kassenbestand=25_000,
        kv_current=0, kv_prior=0,
        lv_current=0, rueckstellungen=0,
        umsatz=0, umsatz_prior=0,
        materialaufwand=0, personalaufwand=0,
        abschreibungen=0, sonstige_aufwendungen=0,
        ebit=0, zinsen_aufwand=0,
        anhang_text=(
            "Die Gesellschaft wurde im Berichtsjahr gegründet und hat noch keine "
            "Geschäftstätigkeit aufgenommen. Es werden keine Umsatzerlöse ausgewiesen. "
            "Ein EBIT ist nicht ausweisbar. Das Stammkapital von EUR 25.000 ist vollständig eingezahlt. "
            "Verbindlichkeiten bestehen nicht."
        ),
        lagebericht_lage="",
        lagebericht_risiken="",
    ),

    # ── 06: EDGE — English document ──────────────────────────────────────────
    # EK-Quote = 3.200.000 / 8.000.000 = 40.0%   ✓
    # Current Ratio = 2.000.000 / 1.100.000 = 1.82  ✓
    # KV-YoY = (1.100.000 - 1.020.000) / 1.020.000 = +7.8%  ✓
    F(
        filename="06_english_annual_report_ltd.pdf",
        company="Sonnenschein Software Ltd",
        city="London",
        hrb="Company No. 00000006",
        sitz="London",
        activity="Software development and IT consulting services",
        size="klein",
        language="en",
        expected_risk="GRÜN",
        expected_anomalies=[],
        bilanzsumme=8_000_000, bilanzsumme_prior=7_200_000,
        eigenkapital=3_200_000, eigenkapital_prior=2_900_000,
        stammkapital=1_000_000, kapitalruecklage=500_000,
        gewinnruecklagen=1_400_000, jahresueberschuss=300_000,
        jahresueberschuss_prior=260_000,
        umlaufvermoegen=2_000_000, umlaufvermoegen_prior=1_800_000,
        vorraete=0, forderungen=1_600_000, kassenbestand=400_000,
        kv_current=1_100_000, kv_prior=1_020_000,
        lv_current=3_500_000, rueckstellungen=200_000,
        umsatz=6_500_000, umsatz_prior=5_900_000,
        materialaufwand=1_300_000, personalaufwand=3_250_000,
        abschreibungen=260_000, sonstige_aufwendungen=650_000,
        ebit=1_040_000, zinsen_aufwand=175_000,
        anhang_text=(
            "The company maintains a strong equity position (40.0% equity ratio). "
            "Current ratio of 1.82 indicates adequate short-term liquidity. "
            "No material financial risks identified."
        ),
        lagebericht_lage="",
        lagebericht_risiken="",
    ),
]


# ── PDF builder ───────────────────────────────────────────────────────────────

def _cover(f: F) -> list:
    s = []
    s.append(Spacer(1, 3 * cm))
    s.append(Paragraph(f.company, H1))
    s.append(Paragraph(f.city, BODY))
    s.append(Spacer(1, 0.5 * cm))
    s.append(HRFlowable(width="100%", thickness=2, color=BLUE))
    s.append(Spacer(1, 0.5 * cm))
    title = "Annual Report" if f.language == "en" else "Jahresabschluss"
    subtitle = f"for the year ended 31 December {f.year}" if f.language == "en" else f"für das Geschäftsjahr {f.year}"
    big = ParagraphStyle("BigCenter", parent=H1, fontSize=22, alignment=TA_CENTER)
    sub = ParagraphStyle("SubCenter", parent=BODY, alignment=TA_CENTER, fontSize=12)
    s.append(Paragraph(title, big))
    s.append(Paragraph(subtitle, sub))
    s.append(Spacer(1, 1 * cm))
    if f.language == "en":
        s.append(Paragraph(f"Company registration: {f.hrb}", SMALL))
        s.append(Paragraph(f"Registered office: {f.sitz}", SMALL))
        s.append(Paragraph(f"Prepared: {f.city}, March {int(f.year) + 1}", SMALL))
        s.append(Paragraph("Auditors: Example & Partners Auditing LLP, London", SMALL))
    else:
        s.append(Paragraph(f"Handelsregister: {f.hrb}", SMALL))
        s.append(Paragraph(f"Sitz: {f.sitz}", SMALL))
        s.append(Paragraph(f"Gezeichnetes Kapital: EUR {fmt(f.stammkapital)}", SMALL))
        s.append(Paragraph(f"Erstellt: {f.city}, März {int(f.year) + 1}", SMALL))
        s.append(Paragraph("Geprüft durch: Muster & Partner Wirtschaftsprüfungsgesellschaft mbH, Frankfurt", SMALL))
    s.append(Spacer(1, 1.5 * cm))
    # Fixture metadata box
    warn_style = ParagraphStyle("Warn", parent=SMALL, textColor=GREY)
    s.append(Paragraph(
        # Without the expected risk level (CHY-178): this line is part of the
        # document text, and a model that reads "Erwartet: ROT" is not rating.
        f"[Fiktives Unternehmen \N{EN DASH} synthetische Testdaten — {f.filename}]",
        warn_style,
    ))
    s.append(PageBreak())
    return s


def _bilanz_de(f: F) -> list:
    s = []
    s.append(Paragraph(f"1. Bilanz zum 31. Dezember {f.year}", H1))
    s.append(Paragraph("Alle Angaben in Euro (EUR).", SMALL))
    s.append(Spacer(1, 0.3 * cm))

    anlagevermögen = f.bilanzsumme - f.umlaufvermoegen
    anlagevermögen_prior = f.bilanzsumme_prior - f.umlaufvermoegen_prior
    verbindlichkeiten = f.kv_current + f.lv_current

    aktiva = [
        ["Position", f"{f.year}\nEUR", f"{f.year_prior}\nEUR"],
        ["A. ANLAGEVERMÖGEN", "", ""],
        ["  Sachanlagen und immaterielle Vermögensgegenstände", fmt(anlagevermögen), fmt(anlagevermögen_prior)],
        ["  Summe Anlagevermögen", fmt(anlagevermögen), fmt(anlagevermögen_prior)],
        ["", "", ""],
        ["B. UMLAUFVERMÖGEN", "", ""],
        ["  I. Vorräte", fmt(f.vorraete), ""],
        ["  II. Forderungen aus Lieferungen und Leistungen", fmt(f.forderungen), ""],
        ["  III. Kassenbestand und Bankguthaben", fmt(f.kassenbestand), ""],
        ["  Summe Umlaufvermögen", fmt(f.umlaufvermoegen), fmt(f.umlaufvermoegen_prior)],
        ["", "", ""],
        ["BILANZSUMME AKTIVA", fmt(f.bilanzsumme), fmt(f.bilanzsumme_prior)],
    ]
    at = Table(
        [[Paragraph(r[0], BODY), Paragraph(r[1], RIGHT), Paragraph(r[2], RIGHT)] for r in aktiva],
        colWidths=[10 * cm, 3.5 * cm, 3.5 * cm],
    )
    style = TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), BLUE),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, 3), (-1, 3), "Helvetica-Bold"),
        ("FONTNAME", (0, 9), (-1, 9), "Helvetica-Bold"),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("BACKGROUND", (0, -1), (-1, -1), LIGHT_BLUE),
        ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#cccccc")),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ])
    at.setStyle(style)
    s.append(at)
    s.append(Spacer(1, 0.5 * cm))

    passiva = [
        ["Position", f"{f.year}\nEUR", f"{f.year_prior}\nEUR"],
        ["A. EIGENKAPITAL", "", ""],
        ["  I. Gezeichnetes Kapital (Stammkapital)", fmt(f.stammkapital), fmt(f.stammkapital)],
        ["  II. Kapitalrücklage", fmt(f.kapitalruecklage), fmt(f.kapitalruecklage)],
        ["  III. Gewinnrücklagen", fmt(f.gewinnruecklagen), ""],
        [f"  IV. Jahresüberschuss / Jahresfehlbetrag {f.year}", fmt(f.jahresueberschuss), ""],
        ["  Summe Eigenkapital", fmt(f.eigenkapital), fmt(f.eigenkapital_prior)],
        ["", "", ""],
        ["B. RÜCKSTELLUNGEN", fmt(f.rueckstellungen), ""],
        ["", "", ""],
        ["C. VERBINDLICHKEITEN", "", ""],
        ["  I. Langfristige Verbindlichkeiten (> 1 Jahr)", fmt(f.lv_current), ""],
        ["  II. Kurzfristige Verbindlichkeiten (≤ 1 Jahr)", fmt(f.kv_current), fmt(f.kv_prior)],
        ["  Summe Verbindlichkeiten", fmt(verbindlichkeiten), ""],
        ["", "", ""],
        ["BILANZSUMME PASSIVA", fmt(f.bilanzsumme), fmt(f.bilanzsumme_prior)],
    ]
    pt = Table(
        [[Paragraph(r[0], BODY), Paragraph(r[1], RIGHT), Paragraph(r[2], RIGHT)] for r in passiva],
        colWidths=[10 * cm, 3.5 * cm, 3.5 * cm],
    )
    pt.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), BLUE),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, 6), (-1, 6), "Helvetica-Bold"),
        ("FONTNAME", (0, 13), (-1, 13), "Helvetica-Bold"),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("BACKGROUND", (0, -1), (-1, -1), LIGHT_BLUE),
        ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#cccccc")),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    s.append(pt)
    s.append(PageBreak())
    return s


def _bilanz_en(f: F) -> list:
    s = []
    s.append(Paragraph(f"1. Balance Sheet as at 31 December {f.year}", H1))
    s.append(Paragraph("All figures in EUR.", SMALL))
    s.append(Spacer(1, 0.3 * cm))

    fixed_assets = f.bilanzsumme - f.umlaufvermoegen
    fixed_assets_prior = f.bilanzsumme_prior - f.umlaufvermoegen_prior
    total_liabilities = f.kv_current + f.lv_current

    assets = [
        ["Line item", f"{f.year}\nEUR", f"{f.year_prior}\nEUR"],
        ["A. NON-CURRENT ASSETS", "", ""],
        ["  Property, plant and equipment / intangibles", fmt(fixed_assets), fmt(fixed_assets_prior)],
        ["  Total non-current assets", fmt(fixed_assets), fmt(fixed_assets_prior)],
        ["", "", ""],
        ["B. CURRENT ASSETS", "", ""],
        ["  Inventories", fmt(f.vorraete), ""],
        ["  Trade receivables", fmt(f.forderungen), ""],
        ["  Cash and cash equivalents", fmt(f.kassenbestand), ""],
        ["  Total current assets", fmt(f.umlaufvermoegen), fmt(f.umlaufvermoegen_prior)],
        ["", "", ""],
        ["TOTAL ASSETS", fmt(f.bilanzsumme), fmt(f.bilanzsumme_prior)],
    ]
    at = Table(
        [[Paragraph(r[0], BODY), Paragraph(r[1], RIGHT), Paragraph(r[2], RIGHT)] for r in assets],
        colWidths=[10 * cm, 3.5 * cm, 3.5 * cm],
    )
    at.setStyle(bold_last(len(assets)))
    s.append(at)
    s.append(Spacer(1, 0.5 * cm))

    equity = [
        ["Line item", f"{f.year}\nEUR", f"{f.year_prior}\nEUR"],
        ["A. EQUITY", "", ""],
        ["  Share capital", fmt(f.stammkapital), fmt(f.stammkapital)],
        ["  Share premium", fmt(f.kapitalruecklage), fmt(f.kapitalruecklage)],
        ["  Retained earnings", fmt(f.gewinnruecklagen), ""],
        [f"  Profit / (Loss) for the year {f.year}", fmt(f.jahresueberschuss), ""],
        ["  Total equity", fmt(f.eigenkapital), fmt(f.eigenkapital_prior)],
        ["", "", ""],
        ["B. PROVISIONS", fmt(f.rueckstellungen), ""],
        ["", "", ""],
        ["C. LIABILITIES", "", ""],
        ["  Non-current liabilities (> 1 year)", fmt(f.lv_current), ""],
        ["  Current liabilities (≤ 1 year)", fmt(f.kv_current), fmt(f.kv_prior)],
        ["  Total liabilities", fmt(total_liabilities), ""],
        ["", "", ""],
        ["TOTAL EQUITY AND LIABILITIES", fmt(f.bilanzsumme), fmt(f.bilanzsumme_prior)],
    ]
    et = Table(
        [[Paragraph(r[0], BODY), Paragraph(r[1], RIGHT), Paragraph(r[2], RIGHT)] for r in equity],
        colWidths=[10 * cm, 3.5 * cm, 3.5 * cm],
    )
    et.setStyle(bold_last(len(equity)))
    s.append(et)
    s.append(PageBreak())
    return s


def _guv_de(f: F) -> list:
    s = []
    s.append(Paragraph("2. Gewinn- und Verlustrechnung", H1))
    s.append(Paragraph(f"für die Zeit vom 1. Januar bis 31. Dezember {f.year}", BODY))
    s.append(Spacer(1, 0.3 * cm))

    ebitda = f.ebit + f.abschreibungen
    ebt = f.ebit - f.zinsen_aufwand
    steuer = max(0, int(ebt * 0.3)) if ebt > 0 else 0
    guv = [
        ["Position", f"{f.year}\nEUR", f"{f.year_prior}\nEUR"],
        ["1.  Umsatzerlöse", fmt(f.umsatz), fmt(f.umsatz_prior)],
        ["2.  Materialaufwand", f"-{fmt(f.materialaufwand)}", ""],
        ["3.  Personalaufwand", f"-{fmt(f.personalaufwand)}", ""],
        ["4.  Abschreibungen auf Sachanlagen", f"-{fmt(f.abschreibungen)}", ""],
        ["5.  Sonstige betriebliche Aufwendungen", f"-{fmt(f.sonstige_aufwendungen)}", ""],
        ["", "", ""],
        ["    EBIT (Ergebnis vor Zinsen und Steuern)", fmt(f.ebit), ""],
        ["", "", ""],
        ["6.  Zinsen und ähnliche Aufwendungen", f"-{fmt(f.zinsen_aufwand)}", ""],
        ["", "", ""],
        ["    Ergebnis vor Steuern (EBT)", fmt(ebt), ""],
        ["", "", ""],
        ["7.  Steuern vom Einkommen und vom Ertrag", f"-{fmt(steuer)}", ""],
        ["", "", ""],
        ["    JAHRESÜBERSCHUSS / JAHRESFEHLBETRAG", fmt(f.jahresueberschuss), fmt(f.jahresueberschuss_prior)],
    ]
    gt = Table(
        [[Paragraph(r[0], BODY), Paragraph(r[1], RIGHT), Paragraph(r[2], RIGHT)] for r in guv],
        colWidths=[10 * cm, 3.5 * cm, 3.5 * cm],
    )
    gt.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), BLUE),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, 7), (-1, 7), "Helvetica-Bold"),
        ("BACKGROUND", (0, 7), (-1, 7), LIGHT_BLUE),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("BACKGROUND", (0, -1), (-1, -1), LIGHT_BLUE),
        ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#cccccc")),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    s.append(gt)

    # Key ratios summary box (makes LLM extraction easier)
    s.append(Spacer(1, 0.5 * cm))
    s.append(Paragraph("Kennzahlen-Überblick", H3))
    ebitda_marge = (ebitda / f.umsatz * 100) if f.umsatz else 0
    ek_quote = (f.eigenkapital / f.bilanzsumme * 100) if f.bilanzsumme else 0
    cr = (f.umlaufvermoegen / f.kv_current) if f.kv_current else 0
    kv_yoy = ((f.kv_current - f.kv_prior) / f.kv_prior * 100) if f.kv_prior else 0
    ratios = [
        ["Kennzahl", "Wert", "Vorjahr"],
        ["Eigenkapitalquote (EK / Bilanzsumme)", f"{ek_quote:.1f}%", ""],
        ["Current Ratio (UV / KV)", f"{cr:.2f}", ""],
        ["KV-Veränderung YoY", f"{kv_yoy:+.1f}%", ""],
        ["EBITDA", fmt(ebitda), ""],
        ["EBITDA-Marge (EBITDA / Umsatz)", f"{ebitda_marge:.1f}%", ""],
        ["Bilanzsumme", fmt(f.bilanzsumme), fmt(f.bilanzsumme_prior)],
    ]
    rt = Table(
        [[Paragraph(r[0], BODY), Paragraph(r[1], RIGHT), Paragraph(r[2], RIGHT)] for r in ratios],
        colWidths=[8 * cm, 3.5 * cm, 3.5 * cm],
    )
    rt.setStyle(tbl())
    s.append(rt)
    s.append(PageBreak())
    return s


def _guv_en(f: F) -> list:
    s = []
    s.append(Paragraph("2. Profit and Loss Account", H1))
    s.append(Paragraph(f"for the year ended 31 December {f.year}", BODY))
    s.append(Spacer(1, 0.3 * cm))

    ebitda = f.ebit + f.abschreibungen
    ebt = f.ebit - f.zinsen_aufwand
    tax = max(0, int(ebt * 0.25)) if ebt > 0 else 0
    pnl = [
        ["Line item", f"{f.year}\nEUR", f"{f.year_prior}\nEUR"],
        ["Revenue", fmt(f.umsatz), fmt(f.umsatz_prior)],
        ["Cost of materials", f"-{fmt(f.materialaufwand)}", ""],
        ["Staff costs", f"-{fmt(f.personalaufwand)}", ""],
        ["Depreciation and amortisation", f"-{fmt(f.abschreibungen)}", ""],
        ["Other operating expenses", f"-{fmt(f.sonstige_aufwendungen)}", ""],
        ["", "", ""],
        ["EBIT (Earnings before interest and tax)", fmt(f.ebit), ""],
        ["", "", ""],
        ["Finance costs", f"-{fmt(f.zinsen_aufwand)}", ""],
        ["", "", ""],
        ["Profit / (Loss) before tax", fmt(ebt), ""],
        ["Income tax expense", f"-{fmt(tax)}", ""],
        ["", "", ""],
        ["PROFIT / (LOSS) FOR THE YEAR", fmt(f.jahresueberschuss), fmt(f.jahresueberschuss_prior)],
    ]
    pt = Table(
        [[Paragraph(r[0], BODY), Paragraph(r[1], RIGHT), Paragraph(r[2], RIGHT)] for r in pnl],
        colWidths=[10 * cm, 3.5 * cm, 3.5 * cm],
    )
    pt.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), BLUE),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, 7), (-1, 7), "Helvetica-Bold"),
        ("BACKGROUND", (0, 7), (-1, 7), LIGHT_BLUE),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("BACKGROUND", (0, -1), (-1, -1), LIGHT_BLUE),
        ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#cccccc")),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    s.append(pt)

    # Key ratios
    s.append(Spacer(1, 0.5 * cm))
    s.append(Paragraph("Key Financial Ratios", H3))
    ebitda_margin = (ebitda / f.umsatz * 100) if f.umsatz else 0
    equity_ratio = (f.eigenkapital / f.bilanzsumme * 100) if f.bilanzsumme else 0
    curr_ratio = (f.umlaufvermoegen / f.kv_current) if f.kv_current else 0
    kv_change = ((f.kv_current - f.kv_prior) / f.kv_prior * 100) if f.kv_prior else 0
    ratios = [
        ["Ratio", "Value", "Prior year"],
        ["Equity ratio (equity / total assets)", f"{equity_ratio:.1f}%", ""],
        ["Current ratio (current assets / current liabilities)", f"{curr_ratio:.2f}", ""],
        ["Current liabilities change YoY", f"{kv_change:+.1f}%", ""],
        ["EBITDA", fmt(ebitda), ""],
        ["EBITDA margin (EBITDA / revenue)", f"{ebitda_margin:.1f}%", ""],
        ["Total assets", fmt(f.bilanzsumme), fmt(f.bilanzsumme_prior)],
    ]
    rt = Table(
        [[Paragraph(r[0], BODY), Paragraph(r[1], RIGHT), Paragraph(r[2], RIGHT)] for r in ratios],
        colWidths=[9 * cm, 3.5 * cm, 3.5 * cm],
    )
    rt.setStyle(tbl())
    s.append(rt)
    s.append(PageBreak())
    return s


def _anhang_de(f: F) -> list:
    s = []
    section = "3." if f.size == "klein" else "5."
    s.append(Paragraph(f"{section} Anhang", H1))
    s.append(Paragraph(f"{section}1 Allgemeine Angaben", H2))
    s.append(Paragraph(
        f"Die {f.company} hat ihren Sitz in {f.city} und ist im {f.hrb} eingetragen. "
        f"Die Gesellschaft ist tätig im Bereich: {f.activity}. "
        f"Der Jahresabschluss wurde nach den Vorschriften des HGB aufgestellt.",
        BODY,
    ))
    s.append(Paragraph(f"{section}2 Finanzielle Lage und Kennzahlen", H2))
    if f.anhang_text:
        s.append(Paragraph(f.anhang_text, BODY))
    # The expected anomalies stay out of the document (CHY-178). They were the
    # findings of the bank's own rules, with their thresholds — "EK-Quote 17% <
    # 20%" — written into the annual statement the rules are to be run on.
    s.append(PageBreak())
    return s


def _anhang_en(f: F) -> list:
    s = []
    s.append(Paragraph("3. Notes to the Financial Statements", H1))
    s.append(Paragraph("3.1 General information", H2))
    s.append(Paragraph(
        f"{f.company} is registered in {f.city} ({f.hrb}). "
        f"Principal activity: {f.activity}. "
        f"These financial statements have been prepared in accordance with UK GAAP / IFRS.",
        BODY,
    ))
    s.append(Paragraph("3.2 Financial position and key ratios", H2))
    if f.anhang_text:
        s.append(Paragraph(f.anhang_text, BODY))
    s.append(PageBreak())
    return s


def _lagebericht_de(f: F) -> list:
    if not f.lagebericht_lage:
        return []
    s = []
    s.append(Paragraph("6. Lagebericht", H1))
    s.append(Paragraph(f"für das Geschäftsjahr vom 1. Januar bis 31. Dezember {f.year}", BODY))
    s.append(Paragraph("6.1 Wirtschaftsbericht", H2))
    s.append(Paragraph(f.lagebericht_lage, BODY))
    if f.lagebericht_risiken:
        s.append(Paragraph("6.2 Risikobericht", H2))
        s.append(Paragraph(f.lagebericht_risiken, BODY))
    s.append(PageBreak())
    return s


def _bestaetigung_de(f: F) -> list:
    if f.size == "klein":
        return []
    s = []
    s.append(Paragraph("7. Bestätigungsvermerk des Abschlussprüfers", H1))
    s.append(Paragraph(f"An die {f.company}, {f.city}", BODY))
    s.append(Spacer(1, 0.3 * cm))
    s.append(Paragraph(
        f"Wir haben den Jahresabschluss der {f.company} für das Geschäftsjahr "
        f"vom 1. Januar bis 31. Dezember {f.year} geprüft.",
        BODY,
    ))
    opinion = (
        "Nach unserer Beurteilung vermittelt der Jahresabschluss in Übereinstimmung mit den "
        "deutschen handelsrechtlichen Vorschriften ein den tatsächlichen Verhältnissen "
        "entsprechendes Bild der Vermögens-, Finanz- und Ertragslage."
    )
    s.append(Paragraph(f"<b>Prüfungsurteil:</b> {opinion}", BODY))
    if f.auditor_note:
        s.append(Paragraph(f"<b>Hinweis:</b> {f.auditor_note}", BODY))
    s.append(Spacer(1, 0.5 * cm))
    s.append(Paragraph(f"{f.city}, März {int(f.year) + 1}", BODY))
    s.append(Paragraph("Muster & Partner Wirtschaftsprüfungsgesellschaft mbH", BODY))
    return s


def build_pdf(f: F, output_dir: Path) -> Path:
    """Build one fixture PDF and return its path."""
    out = output_dir / f.filename
    doc = SimpleDocTemplate(
        str(out),
        pagesize=A4,
        rightMargin=2.5 * cm,
        leftMargin=2.5 * cm,
        topMargin=2.5 * cm,
        bottomMargin=2.5 * cm,
    )
    story: list = []
    story.extend(_cover(f))
    if f.language == "en":
        story.extend(_bilanz_en(f))
        story.extend(_guv_en(f))
        story.extend(_anhang_en(f))
    else:
        story.extend(_bilanz_de(f))
        story.extend(_guv_de(f))
        if f.size == "mittel":
            story.extend(_anhang_de(f))
            story.extend(_lagebericht_de(f))
            story.extend(_bestaetigung_de(f))
        else:
            story.extend(_anhang_de(f))
    doc.build(story)
    return out


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Generate Banking Pilot fixture PDFs")
    parser.add_argument(
        "--output-dir",
        default=str(Path(__file__).parent / "pdfs"),
        help="Output directory for generated PDFs (default: ./pdfs/)",
    )
    args = parser.parse_args()

    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)

    print(f"Generating {len(FIXTURES)} fixture PDFs → {out}/")
    for f in FIXTURES:
        path = build_pdf(f, out)
        anomalies_str = ", ".join(f.expected_anomalies) if f.expected_anomalies else "none"
        print(f"  ✓  {path.name:45s}  expected={f.expected_risk:5s}  anomalies=[{anomalies_str}]")

    print(f"\nDone. {len(FIXTURES)} PDFs written to {out}/")
    print("\nExpected banking pilot outcomes:")
    print("  01 Solide Betrieb    → GRÜN  (fast_track)")
    print("  02 Monitoring Bedarf → GELB  (full_analysis, 2 anomalies)")
    print("  03 Krisenfall        → ROT   (full_analysis, 3 anomalies + qualified auditor)")
    print("  04 Klein Startup     → GRÜN  (fast_track, minimal document)")
    print("  05 Fehlende KPIs     → edge  (umsatz=0, ebit=0 fallback)")
    print("  06 English Ltd       → GRÜN  (language detection test)")


if __name__ == "__main__":
    main()
