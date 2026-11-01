"""
Generates a synthetic annual report (Jahresabschluss) for
"Rhein-Main Industrie GmbH" as a realistic PDF for the banking pilot.

Every name in it is invented: the company, its register number, its banks,
auditors, managing directors and business partners. Seats are given as a city
only, and the title page says the document is synthetic, so a generated PDF
that travels on its own cannot be mistaken for a real company's accounts.

Three deliberate anomalies embedded in the financial data:
  1. OBVIOUS:  Short-term liabilities +40% YoY (2023: 2,750,000 → 2024: 3,850,000)
  2. HIDDEN:   Current Ratio < 1.0 (UV 3,450,000 / KV 3,850,000 = 0.896)
  3. POSITIVE: Equity ratio > 30% (EK 4,900,000 / BS 15,250,000 = 32.1%)

Run:  python generate_jahresabschluss.py
Output: rhein-main-industrie-jahresabschluss-2024.pdf
"""

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    PageBreak, HRFlowable,
)
from reportlab.lib.enums import TA_CENTER, TA_RIGHT

# ── Styles ────────────────────────────────────────────────────────────────────

styles = getSampleStyleSheet()
BLUE = colors.HexColor("#003366")
LIGHT_BLUE = colors.HexColor("#dce6f0")
GREY = colors.HexColor("#666666")

h1 = ParagraphStyle("H1", parent=styles["Heading1"], textColor=BLUE, fontSize=16, spaceAfter=12)
h2 = ParagraphStyle("H2", parent=styles["Heading2"], textColor=BLUE, fontSize=13, spaceAfter=8, spaceBefore=16)
h3 = ParagraphStyle("H3", parent=styles["Heading3"], textColor=BLUE, fontSize=11, spaceAfter=6, spaceBefore=10)
body = ParagraphStyle("Body", parent=styles["Normal"], fontSize=9.5, leading=14, spaceAfter=6)
small = ParagraphStyle("Small", parent=styles["Normal"], fontSize=8.5, leading=12, textColor=GREY)
center = ParagraphStyle("Center", parent=body, alignment=TA_CENTER)
bold_center = ParagraphStyle("BoldCenter", parent=center, fontName="Helvetica-Bold")
right = ParagraphStyle("Right", parent=body, alignment=TA_RIGHT)


def fmt(n):
    """Format number as German EUR: 1.234.567"""
    return f"{n:,.0f}".replace(",", ".")


def tbl_style(has_header=True):
    base = [
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#bbbbbb")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]
    if has_header:
        base += [
            ("BACKGROUND", (0, 0), (-1, 0), BLUE),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ]
    return TableStyle(base)


# ── Content builder ───────────────────────────────────────────────────────────

def build_content():
    story = []

    # ── Cover page ────────────────────────────────────────────────────────────
    story.append(Spacer(1, 3 * cm))
    story.append(Paragraph("Rhein-Main Industrie GmbH", h1))
    story.append(Paragraph("Frankfurt am Main", body))
    story.append(Spacer(1, 0.5 * cm))
    story.append(HRFlowable(width="100%", thickness=2, color=BLUE))
    story.append(Spacer(1, 0.5 * cm))
    story.append(Paragraph("Jahresabschluss", ParagraphStyle("BigCenter", parent=h1, fontSize=22, alignment=TA_CENTER)))
    story.append(Paragraph("für das Geschäftsjahr 2024", ParagraphStyle("SubCenter", parent=body, alignment=TA_CENTER, fontSize=12)))
    story.append(Spacer(1, 1 * cm))
    story.append(Paragraph("(1. Januar 2024 bis 31. Dezember 2024)", center))
    story.append(Spacer(1, 3 * cm))
    story.append(Paragraph("Handelsregister: HRB 00000 Frankfurt am Main", small))
    story.append(Paragraph("Sitz: Frankfurt am Main", small))
    story.append(Paragraph("Gezeichnetes Kapital: EUR 2.500.000", small))
    story.append(Spacer(1, 1 * cm))
    story.append(Paragraph("Erstellt: Frankfurt am Main, März 2025", small))
    story.append(Paragraph("Geprüft durch: Muster & Partner Wirtschaftsprüfungsgesellschaft mbH, Frankfurt", small))
    story.append(Spacer(1, 0.5 * cm))
    story.append(Paragraph("Fiktives Unternehmen \N{EN DASH} synthetische Testdaten", small))
    story.append(PageBreak())

    # ── Table of contents ─────────────────────────────────────────────────────
    story.append(Paragraph("Inhaltsverzeichnis", h1))
    toc_data = [
        ["1.", "Bilanz", "3"],
        ["2.", "Gewinn- und Verlustrechnung", "5"],
        ["3.", "Kapitalflussrechnung", "7"],
        ["4.", "Eigenkapitalspiegel", "9"],
        ["5.", "Anhang", "10"],
        ["6.", "Lagebericht", "16"],
        ["7.", "Bestätigungsvermerk des Abschlussprüfers", "22"],
    ]
    toc = Table([[Paragraph(r[0], body), Paragraph(r[1], body), Paragraph(r[2], right)] for r in toc_data],
                colWidths=[1.5 * cm, 13 * cm, 2 * cm])
    toc.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0, colors.white),
        ("LINEBELOW", (0, 0), (-1, -1), 0.3, colors.HexColor("#cccccc")),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(toc)
    story.append(PageBreak())

    # ── 1. Bilanz ─────────────────────────────────────────────────────────────
    story.append(Paragraph("1. Bilanz zum 31. Dezember 2024", h1))
    story.append(Paragraph(
        "Alle Angaben in Euro (EUR). Vorjahreszahlen (VJ) beziehen sich auf den 31. Dezember 2023.",
        small))
    story.append(Spacer(1, 0.3 * cm))

    story.append(Paragraph("AKTIVA", h2))
    aktiva_data = [
        ["Position", "2024\nEUR", "2023\nEUR"],
        ["A. ANLAGEVERMÖGEN", "", ""],
        ["  I. Immaterielle Vermögensgegenstände", "", ""],
        ["    1. Lizenzen und ähnliche Rechte", fmt(850_000), fmt(950_000)],
        ["  II. Sachanlagen", "", ""],
        ["    1. Grundstücke und Gebäude", fmt(4_500_000), fmt(4_200_000)],
        ["    2. Technische Anlagen und Maschinen", fmt(4_800_000), fmt(4_600_000)],
        ["    3. Andere Anlagen, Betriebs- und Geschäftsausstattung", fmt(900_000), fmt(850_000)],
        ["  III. Finanzanlagen", "", ""],
        ["    1. Beteiligungen", fmt(750_000), fmt(750_000)],
        ["  Summe Anlagevermögen", fmt(11_800_000), fmt(11_350_000)],
        ["", "", ""],
        ["B. UMLAUFVERMÖGEN", "", ""],
        ["  I. Vorräte", "", ""],
        ["    1. Roh-, Hilfs- und Betriebsstoffe", fmt(900_000), fmt(820_000)],
        ["    2. Unfertige Erzeugnisse", fmt(550_000), fmt(490_000)],
        ["    3. Fertige Erzeugnisse und Waren", fmt(350_000), fmt(340_000)],
        ["  II. Forderungen und sonstige Vermögensgegenstände", "", ""],
        ["    1. Forderungen aus Lieferungen und Leistungen", fmt(1_200_000), fmt(980_000)],
        ["    2. Sonstige Vermögensgegenstände", fmt(150_000), fmt(120_000)],
        ["  III. Kassenbestand und Bankguthaben", fmt(300_000), fmt(1_040_000)],
        ["  Summe Umlaufvermögen", fmt(3_450_000), fmt(3_790_000)],
        ["", "", ""],
        ["C. RECHNUNGSABGRENZUNGSPOSTEN", fmt(0), fmt(0)],
        ["", "", ""],
        ["BILANZSUMME AKTIVA", fmt(15_250_000), fmt(15_140_000)],
    ]
    aktiva_tbl = Table(
        [[Paragraph(r[0], body), Paragraph(r[1], right), Paragraph(r[2], right)] for r in aktiva_data],
        colWidths=[10 * cm, 3.5 * cm, 3.5 * cm],
    )
    aktiva_tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), BLUE),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("BACKGROUND", (0, -1), (-1, -1), LIGHT_BLUE),
        ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#cccccc")),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("FONTNAME", (0, 10), (-1, 10), "Helvetica-Bold"),
        ("FONTNAME", (0, 21), (-1, 21), "Helvetica-Bold"),
    ]))
    story.append(aktiva_tbl)
    story.append(PageBreak())

    story.append(Paragraph("PASSIVA", h2))
    passiva_data = [
        ["Position", "2024\nEUR", "2023\nEUR"],
        ["A. EIGENKAPITAL", "", ""],
        ["  I. Gezeichnetes Kapital (Stammkapital)", fmt(2_500_000), fmt(2_500_000)],
        ["  II. Kapitalrücklage", fmt(500_000), fmt(500_000)],
        ["  III. Gewinnrücklagen", fmt(1_200_000), fmt(1_000_000)],
        ["  IV. Jahresüberschuss", fmt(700_000), fmt(650_000)],
        ["  Summe Eigenkapital", fmt(4_900_000), fmt(4_650_000)],
        ["", "", ""],
        ["B. RÜCKSTELLUNGEN", "", ""],
        ["  1. Steuerrückstellungen", fmt(120_000), fmt(100_000)],
        ["  2. Sonstige Rückstellungen", fmt(380_000), fmt(380_000)],
        ["  Summe Rückstellungen", fmt(500_000), fmt(480_000)],
        ["", "", ""],
        ["C. VERBINDLICHKEITEN", "", ""],
        ["  I. Langfristige Verbindlichkeiten (> 1 Jahr)", "", ""],
        ["    1. Verbindlichkeiten ggü. Kreditinstituten", fmt(5_700_000), fmt(7_060_000)],
        ["    2. Sonstige langfristige Verbindlichkeiten", fmt(300_000), fmt(200_000)],
        ["    Summe langfristige Verbindlichkeiten", fmt(6_000_000), fmt(7_260_000)],
        ["  II. Kurzfristige Verbindlichkeiten (≤ 1 Jahr)", "", ""],
        ["    1. Verbindlichkeiten ggü. Kreditinstituten", fmt(1_500_000), fmt(600_000)],
        ["    2. Verbindlichkeiten aus Lieferungen und Leistungen", fmt(1_650_000), fmt(1_450_000)],
        ["    3. Erhaltene Anzahlungen auf Bestellungen", fmt(300_000), fmt(350_000)],
        ["    4. Sonstige kurzfristige Verbindlichkeiten", fmt(400_000), fmt(350_000)],
        ["    Summe kurzfristige Verbindlichkeiten", fmt(3_850_000), fmt(2_750_000)],
        ["  Summe Verbindlichkeiten", fmt(9_850_000), fmt(10_010_000)],
        ["", "", ""],
        ["BILANZSUMME PASSIVA", fmt(15_250_000), fmt(15_140_000)],
    ]
    passiva_tbl = Table(
        [[Paragraph(r[0], body), Paragraph(r[1], right), Paragraph(r[2], right)] for r in passiva_data],
        colWidths=[10 * cm, 3.5 * cm, 3.5 * cm],
    )
    passiva_tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), BLUE),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("BACKGROUND", (0, -1), (-1, -1), LIGHT_BLUE),
        ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#cccccc")),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("FONTNAME", (0, 6), (-1, 6), "Helvetica-Bold"),
        ("FONTNAME", (0, 11), (-1, 11), "Helvetica-Bold"),
        ("FONTNAME", (0, 17), (-1, 17), "Helvetica-Bold"),
        ("FONTNAME", (0, 23), (-1, 23), "Helvetica-Bold"),
        ("FONTNAME", (0, 24), (-1, 24), "Helvetica-Bold"),
    ]))
    story.append(passiva_tbl)
    story.append(PageBreak())

    # ── 2. GuV ────────────────────────────────────────────────────────────────
    story.append(Paragraph("2. Gewinn- und Verlustrechnung", h1))
    story.append(Paragraph("für die Zeit vom 1. Januar bis 31. Dezember 2024", body))
    story.append(Spacer(1, 0.3 * cm))
    guv_data = [
        ["Position", "2024\nEUR", "2023\nEUR"],
        ["1.  Umsatzerlöse", fmt(18_500_000), fmt(17_200_000)],
        ["2.  Bestandsveränderungen fertiger und unfertiger Erzeugnisse", fmt(20_000), fmt(-50_000)],
        ["3.  Andere aktivierte Eigenleistungen", fmt(150_000), fmt(120_000)],
        ["4.  Sonstige betriebliche Erträge", fmt(180_000), fmt(220_000)],
        ["5.  Materialaufwand", f"-{fmt(10_200_000)}", f"-{fmt(9_700_000)}"],
        ["    a) Aufwendungen für Roh-, Hilfs- und Betriebsstoffe", f"-{fmt(7_800_000)}", f"-{fmt(7_400_000)}"],
        ["    b) Aufwendungen für bezogene Leistungen", f"-{fmt(2_400_000)}", f"-{fmt(2_300_000)}"],
        ["6.  Personalaufwand", f"-{fmt(4_800_000)}", f"-{fmt(4_500_000)}"],
        ["    a) Löhne und Gehälter", f"-{fmt(3_900_000)}", f"-{fmt(3_650_000)}"],
        ["    b) Soziale Abgaben und Altersversorgung", f"-{fmt(900_000)}", f"-{fmt(850_000)}"],
        ["7.  Abschreibungen auf Sachanlagen", f"-{fmt(1_200_000)}", f"-{fmt(1_100_000)}"],
        ["8.  Sonstige betriebliche Aufwendungen", f"-{fmt(1_450_000)}", f"-{fmt(1_340_000)}"],
        ["", "", ""],
        ["    EBIT (Ergebnis vor Zinsen und Steuern)", fmt(1_200_000), fmt(850_000)],
        ["", "", ""],
        ["9.  Zinsen und ähnliche Erträge", fmt(15_000), fmt(12_000)],
        ["10. Zinsen und ähnliche Aufwendungen", f"-{fmt(365_000)}", f"-{fmt(380_000)}"],
        ["", "", ""],
        ["    Ergebnis vor Steuern (EBT)", fmt(850_000), fmt(482_000)],
        ["", "", ""],
        ["11. Steuern vom Einkommen und vom Ertrag", f"-{fmt(150_000)}", f"-{fmt(82_000)}"],
        ["12. Sonstige Steuern", f"-{fmt(0)}", f"-{fmt(0)}"],
        ["", "", ""],
        ["    JAHRESÜBERSCHUSS", fmt(700_000), fmt(400_000)],
    ]
    guv_tbl = Table(
        [[Paragraph(r[0], body), Paragraph(r[1], right), Paragraph(r[2], right)] for r in guv_data],
        colWidths=[10 * cm, 3.5 * cm, 3.5 * cm],
    )
    guv_tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), BLUE),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, 14), (-1, 14), "Helvetica-Bold"),
        ("BACKGROUND", (0, 14), (-1, 14), LIGHT_BLUE),
        ("FONTNAME", (0, 19), (-1, 19), "Helvetica-Bold"),
        ("FONTNAME", (0, 23), (-1, 23), "Helvetica-Bold"),
        ("BACKGROUND", (0, 23), (-1, 23), LIGHT_BLUE),
        ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#cccccc")),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    story.append(guv_tbl)
    story.append(PageBreak())

    # ── 3. Kapitalflussrechnung ───────────────────────────────────────────────
    story.append(Paragraph("3. Kapitalflussrechnung", h1))
    story.append(Paragraph("für das Geschäftsjahr 2024 (nach DRS 21)", body))
    story.append(Spacer(1, 0.3 * cm))
    cf_data = [
        ["Position", "2024\nEUR", "2023\nEUR"],
        ["Jahresüberschuss", fmt(700_000), fmt(400_000)],
        ["+ Abschreibungen auf das Anlagevermögen", fmt(1_200_000), fmt(1_100_000)],
        ["+ Zunahme der Rückstellungen", fmt(20_000), fmt(30_000)],
        ["- Zunahme der Forderungen aus LuL", f"-{fmt(220_000)}", fmt(50_000)],
        ["- Zunahme der Vorräte", f"-{fmt(150_000)}", fmt(20_000)],
        ["+ Zunahme der Verbindlichkeiten aus LuL", fmt(200_000), fmt(80_000)],
        ["Cashflow aus laufender Geschäftstätigkeit", fmt(1_750_000), fmt(1_680_000)],
        ["", "", ""],
        ["- Investitionen in Sachanlagen", f"-{fmt(1_750_000)}", f"-{fmt(1_900_000)}"],
        ["- Investitionen in immaterielle Vermögensgegenstände", f"-{fmt(0)}", f"-{fmt(50_000)}"],
        ["Cashflow aus Investitionstätigkeit", f"-{fmt(1_750_000)}", f"-{fmt(1_950_000)}"],
        ["", "", ""],
        ["+ Aufnahme kurzfristiger Bankverbindlichkeiten", fmt(900_000), fmt(0)],
        ["- Tilgung langfristiger Bankverbindlichkeiten", f"-{fmt(1_360_000)}", f"-{fmt(500_000)}"],
        ["- Dividendenausschüttung", f"-{fmt(130_000)}", f"-{fmt(120_000)}"],
        ["Cashflow aus Finanzierungstätigkeit", f"-{fmt(590_000)}", f"-{fmt(620_000)}"],
        ["", "", ""],
        ["Veränderung des Finanzmittelfonds", f"-{fmt(590_000)}", f"-{fmt(890_000)}"],
        ["Anfangsbestand Finanzmittelfonds", fmt(890_000), fmt(1_780_000)],
        ["Endbestand Finanzmittelfonds", fmt(300_000), fmt(890_000)],
    ]
    cf_tbl = Table(
        [[Paragraph(r[0], body), Paragraph(r[1], right), Paragraph(r[2], right)] for r in cf_data],
        colWidths=[10 * cm, 3.5 * cm, 3.5 * cm],
    )
    cf_tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), BLUE),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, 7), (-1, 7), "Helvetica-Bold"),
        ("BACKGROUND", (0, 7), (-1, 7), LIGHT_BLUE),
        ("FONTNAME", (0, 11), (-1, 11), "Helvetica-Bold"),
        ("BACKGROUND", (0, 11), (-1, 11), LIGHT_BLUE),
        ("FONTNAME", (0, 16), (-1, 16), "Helvetica-Bold"),
        ("BACKGROUND", (0, 16), (-1, 16), LIGHT_BLUE),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("BACKGROUND", (0, -1), (-1, -1), LIGHT_BLUE),
        ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#cccccc")),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    story.append(cf_tbl)
    story.append(PageBreak())

    # ── 4. Eigenkapitalspiegel ────────────────────────────────────────────────
    story.append(Paragraph("4. Eigenkapitalspiegel", h1))
    ek_data = [
        ["", "Gezeichnetes\nKapital\nEUR", "Kapital-\nrücklage\nEUR", "Gewinn-\nrücklagen\nEUR", "Jahres-\nüberschuss\nEUR", "Gesamt\nEUR"],
        ["Stand 01.01.2023", fmt(2_500_000), fmt(500_000), fmt(870_000), fmt(400_000), fmt(4_270_000)],
        ["Jahresüberschuss 2023", "—", "—", "—", fmt(400_000), fmt(400_000)],
        ["Einstellungen in Gewinnrücklagen", "—", "—", fmt(130_000), f"-{fmt(130_000)}", "—"],
        ["Dividendenausschüttung", "—", "—", "—", f"-{fmt(120_000)}", f"-{fmt(120_000)}"],
        ["Stand 31.12.2023", fmt(2_500_000), fmt(500_000), fmt(1_000_000), fmt(650_000), fmt(4_650_000)],
        ["Jahresüberschuss 2024", "—", "—", "—", fmt(700_000), fmt(700_000)],
        ["Einstellungen in Gewinnrücklagen", "—", "—", fmt(200_000), f"-{fmt(200_000)}", "—"],
        ["Dividendenausschüttung", "—", "—", "—", f"-{fmt(130_000)}", f"-{fmt(130_000)}"],
        ["Stand 31.12.2024", fmt(2_500_000), fmt(500_000), fmt(1_200_000), fmt(700_000), fmt(4_900_000)],
    ]
    ek_tbl = Table(
        [[Paragraph(c, small if i > 0 else body) for c in r] for i, r in enumerate(ek_data)],
        colWidths=[4 * cm, 2.5 * cm, 2.5 * cm, 2.5 * cm, 2.5 * cm, 2.5 * cm],
    )
    ek_tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), BLUE),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, 5), (-1, 5), "Helvetica-Bold"),
        ("BACKGROUND", (0, 5), (-1, 5), LIGHT_BLUE),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("BACKGROUND", (0, -1), (-1, -1), LIGHT_BLUE),
        ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#cccccc")),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
    ]))
    story.append(ek_tbl)
    story.append(PageBreak())

    # ── 5. Anhang ─────────────────────────────────────────────────────────────
    story.append(Paragraph("5. Anhang", h1))

    story.append(Paragraph("5.1 Allgemeine Angaben", h2))
    story.append(Paragraph(
        "Die Rhein-Main Industrie GmbH hat ihren Sitz in Frankfurt am Main und ist im Handelsregister "
        "des Amtsgerichts Frankfurt am Main unter HRB 00000 eingetragen. Die Gesellschaft ist im "
        "Bereich der Herstellung von Präzisionsteilen für den Maschinenbau tätig.",
        body))
    story.append(Paragraph(
        "Der Jahresabschluss wurde nach den Vorschriften des Handelsgesetzbuches (HGB) sowie den "
        "ergänzenden Bestimmungen des GmbH-Gesetzes aufgestellt. Er vermittelt unter Beachtung der "
        "Grundsätze ordnungsmäßiger Buchführung ein den tatsächlichen Verhältnissen entsprechendes "
        "Bild der Vermögens-, Finanz- und Ertragslage der Gesellschaft.",
        body))

    story.append(Paragraph("5.2 Bilanzierungs- und Bewertungsmethoden", h2))
    methods = [
        ("Immaterielle Vermögensgegenstände",
         "Entgeltlich erworbene immaterielle Vermögensgegenstände werden zu Anschaffungskosten "
         "abzüglich planmäßiger linearer Abschreibungen bewertet (3–5 Jahre)."),
        ("Sachanlagen",
         "Sachanlagen werden zu Anschaffungs- oder Herstellungskosten, vermindert um planmäßige "
         "Abschreibungen, bewertet. Die Nutzungsdauer beträgt 20–50 Jahre für Gebäude, "
         "5–15 Jahre für Maschinen und 3–10 Jahre für die Betriebs- und Geschäftsausstattung."),
        ("Vorräte",
         "Roh-, Hilfs- und Betriebsstoffe werden zu durchschnittlichen Einstandspreisen bewertet. "
         "Unfertige und fertige Erzeugnisse werden zu Herstellungskosten unter Berücksichtigung des "
         "Niederstwertprinzips angesetzt."),
        ("Forderungen",
         "Forderungen und sonstige Vermögensgegenstände sind zum Nennwert angesetzt. "
         "Erkennbare Einzelrisiken werden durch Einzelwertberichtigungen berücksichtigt."),
        ("Verbindlichkeiten",
         "Verbindlichkeiten sind mit ihrem Erfüllungsbetrag angesetzt."),
    ]
    for title, text in methods:
        story.append(Paragraph(f"<b>{title}:</b> {text}", body))

    story.append(Paragraph("5.3 Erläuterungen zur Bilanz — Anlagevermögen", h2))
    story.append(Paragraph(
        "Die Investitionen in Sachanlagen betrugen im Geschäftsjahr 2024 insgesamt EUR 1.750.000 "
        "(Vorjahr: EUR 1.950.000) und entfallen im Wesentlichen auf die Erweiterung der "
        "Fertigungskapazitäten am Standort Frankfurt sowie die Erneuerung von CNC-Bearbeitungszentren.",
        body))

    story.append(Paragraph("5.4 Erläuterungen zur Bilanz — Verbindlichkeiten", h2))
    story.append(Paragraph(
        "Die kurzfristigen Verbindlichkeiten gegenüber Kreditinstituten haben sich im "
        "Berichtsjahr von EUR 600.000 auf EUR 1.500.000 erhöht (+150%). Hintergrund ist die "
        "Refinanzierung eines zum 31.03.2024 fällig gestellten Darlehens der Musterbank eG "
        "in Höhe von EUR 800.000 sowie die Inanspruchnahme der Kontokorrentlinie zur Überbrückung "
        "von Zahlungsverzögerungen bei Großkunden.",
        body))
    story.append(Paragraph(
        "Insgesamt erhöhten sich die kurzfristigen Verbindlichkeiten von EUR 2.750.000 "
        "auf EUR 3.850.000 (+40,0%). Die Gesellschaft ist mit der Musterbank eG sowie der "
        "Beispielbank AG in Verhandlungen über die Umstrukturierung des kurzfristigen "
        "Darlehensportfolios. Ein Term Sheet für ein Refinanzierungspaket liegt seit Februar 2025 vor.",
        body))

    story.append(Paragraph("5.5 Angaben zu Haftungsverhältnissen", h2))
    story.append(Paragraph(
        "Zum Bilanzstichtag bestehen keine Haftungsverhältnisse gegenüber Tochterunternehmen "
        "oder Dritten, die nicht in der Bilanz ausgewiesen sind. Es bestehen Bankbürgschaften "
        "gegenüber Lieferanten in Höhe von insgesamt EUR 250.000.",
        body))

    story.append(Paragraph("5.6 Angaben zu nahe stehenden Personen und Unternehmen", h2))
    story.append(Paragraph(
        "Mit der Muster Engineering GmbH, Frankfurt, bestehen laufende Werkverträge für "
        "Konstruktionsdienstleistungen. Das Volumen betrug im Geschäftsjahr 2024 EUR 320.000 "
        "(Vorjahr: EUR 290.000). Die Verträge wurden zu marktüblichen Konditionen abgeschlossen. "
        "An der Muster Engineering GmbH ist kein Gesellschafter der Rhein-Main Industrie GmbH beteiligt.",
        body))

    story.append(Paragraph("5.7 Finanzinstrumente und Währungsrisiken", h2))
    story.append(Paragraph(
        "Die Gesellschaft ist Währungsrisiken in geringem Umfang ausgesetzt. Etwa 8% der "
        "Umsatzerlöse werden in USD fakturiert. Zur Absicherung werden kurzfristige "
        "Devisentermingeschäfte eingesetzt. Zum Bilanzstichtag bestehen offene Positionen "
        "mit einem Nominalwert von USD 520.000.",
        body))

    story.append(Paragraph("5.8 Mitarbeiter", h2))
    emp_data = [
        ["Kategorie", "2024", "2023"],
        ["Gewerbliche Arbeitnehmer", "148", "142"],
        ["Angestellte", "52", "49"],
        ["Auszubildende", "12", "11"],
        ["Gesamt", "212", "202"],
    ]
    emp_tbl = Table(
        [[Paragraph(c, body) for c in r] for r in emp_data],
        colWidths=[8 * cm, 3.5 * cm, 3.5 * cm],
    )
    emp_tbl.setStyle(tbl_style())
    story.append(emp_tbl)
    story.append(PageBreak())

    story.append(Paragraph("5.9 Angaben zu Geschäftsführung und Gesellschaftern", h2))
    story.append(Paragraph(
        "Geschäftsführer: Dipl.-Ing. Max Mustermann (Vorsitzender), Dr. Erika Mustermann (Finanzen). "
        "Gesellschafter: Familie Mustermann (60%), Rhein-Main Beteiligungsgesellschaft mbH (40%).",
        body))
    story.append(PageBreak())

    # ── 5a. Anlagenspiegel ────────────────────────────────────────────────────
    story.append(Paragraph("Anlage zum Anhang: Anlagenspiegel", h1))
    story.append(Paragraph("Entwicklung des Anlagevermögens im Geschäftsjahr 2024", body))
    story.append(Spacer(1, 0.3 * cm))
    anlagen_data = [
        ["Position", "Stand\n01.01.2024\nEUR", "Zugänge\nEUR", "Abgänge\nEUR", "Umbuchungen\nEUR", "Stand\n31.12.2024\nEUR"],
        ["I. Immaterielle Vermögensgegenstände", "", "", "", "", ""],
        ["  Lizenzen, Schutzrechte", fmt(1_850_000), fmt(0), fmt(0), fmt(0), fmt(1_850_000)],
        ["  Kumulierte Abschreibungen", f"-{fmt(900_000)}", f"-{fmt(100_000)}", fmt(0), fmt(0), f"-{fmt(1_000_000)}"],
        ["  Buchwert", fmt(950_000), "", "", "", fmt(850_000)],
        ["", "", "", "", "", ""],
        ["II. Sachanlagen", "", "", "", "", ""],
        ["  Grundstücke und Gebäude (AK)", fmt(6_200_000), fmt(300_000), fmt(0), fmt(0), fmt(6_500_000)],
        ["  Kumulierte Abschreibungen", f"-{fmt(2_000_000)}", f"-{fmt(300_000)}", fmt(0), fmt(0), f"-{fmt(2_000_000)}"],
        ["  Buchwert Grundstücke/Gebäude", fmt(4_200_000), "", "", "", fmt(4_500_000)],
        ["", "", "", "", "", ""],
        ["  Maschinen und Anlagen (AK)", fmt(8_900_000), fmt(1_200_000), fmt(200_000), fmt(0), fmt(9_900_000)],
        ["  Kumulierte Abschreibungen", f"-{fmt(4_300_000)}", f"-{fmt(700_000)}", fmt(200_000), fmt(0), f"-{fmt(4_800_000)}"],
        ["  Buchwert Maschinen", fmt(4_600_000), "", "", "", fmt(4_800_000) + "  ← Hauptinvestition"],
        ["", "", "", "", "", ""],
        ["  BGA (AK)", fmt(1_600_000), fmt(250_000), fmt(0), fmt(0), fmt(1_850_000)],
        ["  Kumulierte Abschreibungen", f"-{fmt(750_000)}", f"-{fmt(200_000)}", fmt(0), fmt(0), f"-{fmt(950_000)}"],
        ["  Buchwert BGA", fmt(850_000), "", "", "", fmt(900_000)],
        ["", "", "", "", "", ""],
        ["III. Finanzanlagen", "", "", "", "", ""],
        ["  Beteiligungen (AK)", fmt(750_000), fmt(0), fmt(0), fmt(0), fmt(750_000)],
        ["  Buchwert Beteiligungen", fmt(750_000), "", "", "", fmt(750_000)],
        ["", "", "", "", "", ""],
        ["GESAMT ANLAGEVERMÖGEN (Buchwert)", fmt(11_350_000), fmt(1_750_000), fmt(200_000), fmt(0), fmt(11_800_000)],
    ]
    anlagen_tbl = Table(
        [[Paragraph(r[0], small), Paragraph(r[1], small), Paragraph(r[2], small),
          Paragraph(r[3], small), Paragraph(r[4], small), Paragraph(r[5], small)] for r in anlagen_data],
        colWidths=[5.5 * cm, 2.3 * cm, 2.3 * cm, 2.3 * cm, 2.3 * cm, 2.3 * cm],
    )
    anlagen_tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), BLUE),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("BACKGROUND", (0, -1), (-1, -1), LIGHT_BLUE),
        ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#cccccc")),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    story.append(anlagen_tbl)
    story.append(Spacer(1, 0.5 * cm))
    story.append(Paragraph(
        "Die Investitionen in Höhe von EUR 1.750.000 (Vorjahr: EUR 1.950.000) entfallen "
        "überwiegend auf die Erweiterung der CNC-Bearbeitungskapazitäten (EUR 1.200.000) "
        "sowie den Aus- und Umbau von Büro- und Lagerflächen am Standort Frankfurt (EUR 300.000). "
        "Abgänge betreffen ausgemusterte Altmaschinen mit einem Restbuchwert von EUR 0.",
        body))
    story.append(PageBreak())

    # ── 5b. Verbindlichkeitenspiegel ──────────────────────────────────────────
    story.append(Paragraph("Anlage zum Anhang: Verbindlichkeitenspiegel", h1))
    story.append(Paragraph("Fälligkeitsstruktur der Verbindlichkeiten zum 31. Dezember 2024", body))
    story.append(Spacer(1, 0.3 * cm))
    verb_data = [
        ["Verbindlichkeitsart", "Gesamt\n2024\nEUR", "davon\n≤ 1 Jahr\nEUR", "davon\n1–5 Jahre\nEUR", "davon\n> 5 Jahre\nEUR", "Gesamt\n2023\nEUR"],
        ["Ggü. Kreditinstituten", fmt(7_200_000), fmt(1_500_000), fmt(4_200_000), fmt(1_500_000), fmt(7_660_000)],
        ["Aus LuL", fmt(1_650_000), fmt(1_650_000), fmt(0), fmt(0), fmt(1_450_000)],
        ["Erhaltene Anzahlungen", fmt(300_000), fmt(300_000), fmt(0), fmt(0), fmt(350_000)],
        ["Sonstige Verbindlichkeiten", fmt(700_000), fmt(400_000), fmt(300_000), fmt(0), fmt(550_000)],
        ["GESAMT", fmt(9_850_000), fmt(3_850_000), fmt(4_500_000), fmt(1_500_000), fmt(10_010_000)],
    ]
    verb_tbl = Table(
        [[Paragraph(c, body) for c in r] for r in verb_data],
        colWidths=[4.5 * cm, 2.5 * cm, 2.5 * cm, 2.5 * cm, 2.5 * cm, 2.5 * cm],
    )
    verb_tbl.setStyle(tbl_style())
    story.append(verb_tbl)
    story.append(Spacer(1, 0.4 * cm))
    story.append(Paragraph(
        "Die kurzfristigen Bankverbindlichkeiten (EUR 1.500.000) beinhalten einen per "
        "31.03.2024 fällig gestellten Kredit der Musterbank eG (EUR 800.000) "
        "sowie die vollständige Inanspruchnahme der Kontokorrentlinie (EUR 700.000). "
        "Die Gesellschaft befindet sich in fortgeschrittenen Refinanzierungsverhandlungen "
        "(vgl. Anhang 5.10).",
        body))
    story.append(Spacer(1, 0.3 * cm))

    # Forderungsspiegel
    story.append(Paragraph("Anlage zum Anhang: Forderungsspiegel", h2))
    story.append(Paragraph("Fälligkeitsstruktur der Forderungen zum 31. Dezember 2024", body))
    ford_data = [
        ["Forderungsart", "Gesamt 2024\nEUR", "davon ≤ 1 Jahr\nEUR", "davon > 1 Jahr\nEUR", "Gesamt 2023\nEUR"],
        ["Aus LuL", fmt(1_200_000), fmt(1_180_000), fmt(20_000), fmt(980_000)],
        ["Sonstige", fmt(150_000), fmt(150_000), fmt(0), fmt(120_000)],
        ["GESAMT", fmt(1_350_000), fmt(1_330_000), fmt(20_000), fmt(1_100_000)],
    ]
    ford_tbl = Table(
        [[Paragraph(c, body) for c in r] for r in ford_data],
        colWidths=[5 * cm, 3 * cm, 3 * cm, 3 * cm, 3 * cm],
    )
    ford_tbl.setStyle(tbl_style())
    story.append(ford_tbl)
    story.append(Spacer(1, 0.3 * cm))
    story.append(Paragraph(
        "Auf Forderungen aus Lieferungen und Leistungen wurden Einzelwertberichtigungen "
        "in Höhe von EUR 35.000 (Vorjahr: EUR 28.000) gebildet. Überfällige Forderungen "
        "über 90 Tage betragen EUR 85.000 (Vorjahr: EUR 62.000).",
        body))
    story.append(PageBreak())

    # ── 5c. Rückstellungsspiegel ──────────────────────────────────────────────
    story.append(Paragraph("Anlage zum Anhang: Rückstellungsspiegel", h1))
    rueck_data = [
        ["Rückstellungsart", "Stand\n01.01.2024\nEUR", "Zuführung\nEUR", "Verbrauch\nEUR", "Auflösung\nEUR", "Stand\n31.12.2024\nEUR"],
        ["Steuerrückstellungen", fmt(100_000), fmt(150_000), fmt(110_000), fmt(20_000), fmt(120_000)],
        ["Personalrückstellungen (Urlaub, Überstunden)", fmt(180_000), fmt(190_000), fmt(175_000), fmt(5_000), fmt(190_000)],
        ["Gewährleistungsrückstellungen", fmt(120_000), fmt(85_000), fmt(70_000), fmt(15_000), fmt(120_000)],
        ["Sonstige Rückstellungen", fmt(80_000), fmt(65_000), fmt(55_000), fmt(20_000), fmt(70_000)],
        ["GESAMT", fmt(480_000), fmt(490_000), fmt(410_000), fmt(60_000), fmt(500_000)],
    ]
    rueck_tbl = Table(
        [[Paragraph(c, body) for c in r] for r in rueck_data],
        colWidths=[5.5 * cm, 2.2 * cm, 2.2 * cm, 2.2 * cm, 2.2 * cm, 2.2 * cm],
    )
    rueck_tbl.setStyle(tbl_style())
    story.append(rueck_tbl)
    story.append(Spacer(1, 0.4 * cm))

    story.append(Paragraph("Sonstige finanzielle Verpflichtungen", h2))
    verpfl_data = [
        ["Art der Verpflichtung", "Gesamt\nEUR", "2025\nEUR", "2026–2029\nEUR"],
        ["Mietverträge (Lagerhallen)", fmt(1_200_000), fmt(300_000), fmt(900_000)],
        ["Leasingverträge (Fahrzeuge, Stapler)", fmt(480_000), fmt(160_000), fmt(320_000)],
        ["Langfristige Lieferverträge (Stahl)", fmt(2_400_000), fmt(800_000), fmt(1_600_000)],
        ["GESAMT", fmt(4_080_000), fmt(1_260_000), fmt(2_820_000)],
    ]
    verpfl_tbl = Table(
        [[Paragraph(c, body) for c in r] for r in verpfl_data],
        colWidths=[7 * cm, 3 * cm, 3 * cm, 4 * cm],
    )
    verpfl_tbl.setStyle(tbl_style())
    story.append(verpfl_tbl)
    story.append(PageBreak())

    story.append(Paragraph("5.10 Ereignisse nach dem Bilanzstichtag", h2))
    story.append(Paragraph(
        "Am 15. Februar 2025 wurde mit der Musterbank eG ein Term Sheet über ein "
        "Refinanzierungspaket unterzeichnet. Die endgültige Kreditvereinbarung soll bis zum "
        "31. März 2025 abgeschlossen werden. Das Paket umfasst die Umwandlung kurzfristiger "
        "Verbindlichkeiten von EUR 1.200.000 in ein mittelfristiges Darlehen mit einer Laufzeit "
        "von 5 Jahren. Weitere wesentliche Ereignisse nach dem Bilanzstichtag liegen nicht vor.",
        body))
    story.append(PageBreak())

    # ── 6. Lagebericht ────────────────────────────────────────────────────────
    story.append(Paragraph("6. Lagebericht", h1))
    story.append(Paragraph("für das Geschäftsjahr vom 1. Januar bis 31. Dezember 2024", body))

    story.append(Paragraph("6.1 Wirtschaftsbericht — Gesamtwirtschaftliche Lage", h2))
    story.append(Paragraph(
        "Das Jahr 2024 war von anhaltender Unsicherheit in der deutschen Industrie geprägt. "
        "Die gesamtwirtschaftliche Produktion stagnierte, während die Auftragslage im "
        "Maschinenbau selektiv blieb. Rhein-Main Industrie GmbH konnte sich durch ihre "
        "Spezialisierung auf Präzisionsteile für die Automobilzulieferung und den "
        "Sonderanlagenbau dem allgemeinen Trend widersetzen.",
        body))

    story.append(Paragraph("6.2 Geschäftsverlauf und Ergebnisse", h2))
    story.append(Paragraph(
        "Der Umsatz stieg um 7,6% auf EUR 18,5 Mio. (Vorjahr: EUR 17,2 Mio.). "
        "Das Ergebnis vor Zinsen und Steuern (EBIT) verbesserte sich auf EUR 1,2 Mio. "
        "(Vorjahr: EUR 0,85 Mio.). Die EBITDA-Marge beträgt 13,0%.",
        body))

    story.append(Paragraph("6.3 Finanzlage", h2))
    story.append(Paragraph(
        "Die Finanzlage der Gesellschaft war im Geschäftsjahr 2024 angespannt. "
        "Der Cashflow aus laufender Geschäftstätigkeit von EUR 1,75 Mio. deckte die "
        "Investitionsauszahlungen vollständig. Gleichzeitig führte die Fälligkeit eines "
        "Bankdarlehens und die Inanspruchnahme der Kontokorrentlinie zu einem Anstieg "
        "der kurzfristigen Verbindlichkeiten auf EUR 3,85 Mio. (Vorjahr: EUR 2,75 Mio.).",
        body))
    story.append(Paragraph(
        "Der Kassenbestand sank auf EUR 300.000 (Vorjahr: EUR 1.040.000). "
        "Die Gesellschaft plant eine Refinanzierung der kurzfristigen Verbindlichkeiten "
        "durch ein mittelfristiges Darlehen (Term Sheet unterzeichnet, Februar 2025). "
        "Die Current Ratio (Umlaufvermögen / kurzfristige Verbindlichkeiten) beträgt 0,90.",
        body))

    story.append(Paragraph("6.4 Vermögenslage", h2))
    kpi_data = [
        ["Kennzahl", "2024", "2023", "Veränderung"],
        ["Eigenkapitalquote", "32,1%", "30,7%", "+1,4 PP"],
        ["Current Ratio", "0,90", "1,38", "-0,48"],
        ["Verschuldungsgrad (FK/EK)", "2,11x", "2,26x", "-0,15x"],
        ["EBITDA-Marge", "13,0%", "11,3%", "+1,7 PP"],
        ["Kurzfristige Verbindlichkeiten YoY", "+40,0%", "—", "—"],
        ["Bilanzsumme", "EUR 15,25 Mio.", "EUR 15,14 Mio.", "+0,7%"],
    ]
    kpi_tbl = Table(
        [[Paragraph(c, body) for c in r] for r in kpi_data],
        colWidths=[6 * cm, 3 * cm, 3 * cm, 3 * cm],
    )
    kpi_tbl.setStyle(tbl_style())
    story.append(kpi_tbl)
    story.append(Spacer(1, 0.3 * cm))

    story.append(Paragraph("6.5 Risikobericht", h2))
    story.append(Paragraph(
        "Die wesentlichen Risiken für das Geschäftsjahr 2025 sind:",
        body))
    risks = [
        ("Liquiditätsrisiko", "mittel",
         "Die erhöhten kurzfristigen Verbindlichkeiten und der gesunkene Kassenbestand "
         "erfordern eine zeitnahe Refinanzierung. Das Term Sheet mit der Musterbank eG "
         "reduziert dieses Risiko, sofern die Kreditvereinbarung planmäßig abgeschlossen wird."),
        ("Marktrisiko", "gering",
         "Rückgang der Automobilproduktion könnte Auftragsvolumen belasten. "
         "Diversifikation in Sonderanlagenbau (aktuell 35% des Umsatzes) mindert Abhängigkeit."),
        ("Kreditrisiko", "gering",
         "Top-10-Kunden stellen 62% des Umsatzes. Bonitätsprüfungen werden halbjährlich durchgeführt."),
        ("Operationelles Risiko", "gering",
         "Abhängigkeit von Schlüsselmitarbeitern in der CNC-Fertigung. "
         "Nachfolgeplanung wird im Geschäftsjahr 2025 adressiert."),
    ]
    risk_data = [["Risikoart", "Einstufung", "Beschreibung"]]
    for name, level, desc in risks:
        risk_data.append([
            Paragraph(f"<b>{name}</b>", body),
            Paragraph(level, body),
            Paragraph(desc, body),
        ])
    risk_tbl = Table(
        [r if isinstance(r[0], str) else r for r in [[Paragraph(c, body) for c in r] if isinstance(r[0], str) else r for r in risk_data]],
        colWidths=[4 * cm, 2.5 * cm, 10.5 * cm],
    )
    risk_tbl.setStyle(tbl_style())
    story.append(risk_tbl)

    story.append(Paragraph("6.6 Digitalisierungsbericht", h2))
    story.append(Paragraph(
        "Im Geschäftsjahr 2024 hat die Rhein-Main Industrie GmbH ihre digitale Infrastruktur "
        "weiter ausgebaut. Im Mittelpunkt standen der Rollout eines ERP-Systems (SAP S/4HANA "
        "Public Cloud, Go-Live Oktober 2024) sowie die Anbindung der CNC-Maschinen an das "
        "Produktionsmanagementsystem (MES). Die Investitionen in digitale Infrastruktur "
        "betrugen EUR 320.000 (in den Sachanlagen und immateriellen Vermögensgegenständen enthalten).",
        body))
    story.append(Paragraph(
        "KI-gestützte Qualitätskontrolle: Seit September 2024 wird ein KI-System zur "
        "automatisierten Sichtprüfung von Präzisionsteilen eingesetzt (Pilotlinie L7). "
        "Die Ausschussquote auf dieser Linie hat sich um 28% reduziert. Der Rollout auf "
        "weitere Linien ist für 2025 geplant.",
        body))

    story.append(Paragraph("6.7 Nachtragsbericht", h2))
    story.append(Paragraph(
        "Zum Zeitpunkt der Erstellung dieses Lageberichts (März 2025) sind folgende "
        "wesentliche Ereignisse nach dem Bilanzstichtag eingetreten:",
        body))
    story.append(Paragraph(
        "1. Refinanzierungsverhandlungen: Am 15. Februar 2025 wurde mit der Musterbank "
        "eG ein Term Sheet unterzeichnet (vgl. Anhang 5.10). Die Kreditvereinbarung "
        "zur Umwandlung von EUR 1.200.000 kurzfristiger Verbindlichkeiten in ein "
        "5-Jahres-Darlehen soll bis 31. März 2025 abgeschlossen werden.",
        body))
    story.append(Paragraph(
        "2. Großauftrag Sonderanlagenbau: Im Januar 2025 wurde ein Rahmenvertrag mit der "
        "Beispiel Maschinenbau AG, Kassel, über die Lieferung von Präzisionsteilen im "
        "Gesamtvolumen von EUR 2,8 Mio. für die Jahre 2025–2027 unterzeichnet.",
        body))

    story.append(Paragraph("6.8 Chancenbericht", h2))
    story.append(Paragraph(
        "Die Rhein-Main Industrie GmbH sieht folgende wesentliche Chancen für die "
        "Geschäftsentwicklung in den Jahren 2025–2027:",
        body))
    chances = [
        ("Nearshoring-Trend",
         "Die Rückverlagerung von Fertigungskapazitäten nach Europa bietet Chancen "
         "für hochwertige Zulieferer. Mehrere Anfragen aus der Elektromobilitätsbranche "
         "befinden sich in der Qualifikationsphase."),
        ("Sonderanlagenbau",
         "Die Diversifikation in den Sonderanlagenbau (aktuell 35% des Umsatzes) "
         "reduziert die Abhängigkeit von der Automobilindustrie und erschließt "
         "wachstumsstarke Märkte (Logistikautomatisierung, Halbleiter)."),
        ("KI-gestützte Fertigung",
         "Der erfolgreiche Pilot auf Linie L7 zeigt das Potenzial für weitere "
         "Effizienzgewinne. Bei vollständigem Rollout wird eine Reduzierung der "
         "Ausschussquote um ca. EUR 280.000 p.a. erwartet."),
    ]
    for title, text in chances:
        story.append(Paragraph(f"<b>{title}:</b> {text}", body))

    story.append(Paragraph("6.9 Prognosebericht", h2))
    story.append(Paragraph(
        "Für das Geschäftsjahr 2025 erwartet die Geschäftsführung ein Umsatzwachstum "
        "von 5–8% auf ca. EUR 19,5–20,0 Mio. Das EBIT soll auf EUR 1,3–1,5 Mio. steigen. "
        "Voraussetzung ist der erfolgreiche Abschluss der Refinanzierung bis Ende März 2025 "
        "sowie eine stabile Auftragslage im Automobilzuliefererbereich.",
        body))
    story.append(Paragraph(
        "Die Liquiditätssituation wird sich nach erfolgreichem Abschluss der Refinanzierung "
        "deutlich verbessern. Für Ende 2025 wird eine Current Ratio von mindestens 1,1 "
        "angestrebt. Der Kassenbestand soll auf EUR 600.000–800.000 aufgebaut werden.",
        body))

    prog_data = [
        ["Kennzahl", "Prognose 2025", "Prognose 2026", "Basis: Ist 2024"],
        ["Umsatz", "EUR 19,5–20,0 Mio.", "EUR 20,5–21,5 Mio.", "EUR 18,5 Mio."],
        ["EBIT", "EUR 1,3–1,5 Mio.", "EUR 1,5–1,8 Mio.", "EUR 1,2 Mio."],
        ["EBITDA-Marge", "13,5–14,5%", "14,0–15,0%", "13,0%"],
        ["Current Ratio", "> 1,10", "> 1,25", "0,90"],
        ["EK-Quote", "> 33%", "> 35%", "32,1%"],
        ["Mitarbeiter", "ca. 220", "ca. 230", "212"],
    ]
    prog_tbl = Table(
        [[Paragraph(c, body) for c in r] for r in prog_data],
        colWidths=[5 * cm, 3.5 * cm, 3.5 * cm, 5 * cm],
    )
    prog_tbl.setStyle(tbl_style())
    story.append(prog_tbl)
    story.append(PageBreak())

    # ── 7. Bestätigungsvermerk ────────────────────────────────────────────────
    story.append(Paragraph("7. Bestätigungsvermerk des Abschlussprüfers", h1))
    story.append(Paragraph(
        "An die Rhein-Main Industrie GmbH, Frankfurt am Main",
        body))
    story.append(Spacer(1, 0.3 * cm))
    story.append(Paragraph("<b>Vermerk über die Prüfung des Jahresabschlusses</b>", body))
    story.append(Paragraph(
        "Wir haben den Jahresabschluss der Rhein-Main Industrie GmbH — bestehend aus der Bilanz "
        "zum 31. Dezember 2024 und der Gewinn- und Verlustrechnung für das Geschäftsjahr vom "
        "1. Januar bis zum 31. Dezember 2024 sowie dem Anhang, einschließlich der Darstellung "
        "der Bilanzierungs- und Bewertungsmethoden — geprüft.",
        body))
    story.append(Paragraph(
        "<b>Prüfungsurteil</b>: Nach unserer Beurteilung aufgrund der bei der Prüfung gewonnenen "
        "Erkenntnisse vermittelt der beigefügte Jahresabschluss in Übereinstimmung mit den deutschen "
        "handelsrechtlichen Vorschriften ein den tatsächlichen Verhältnissen entsprechendes Bild der "
        "Vermögens- und Finanzlage der Gesellschaft zum 31. Dezember 2024 sowie ihrer Ertragslage "
        "für das Geschäftsjahr vom 1. Januar bis zum 31. Dezember 2024.",
        body))
    story.append(Paragraph(
        "<b>Hinweis:</b> Wir weisen auf die im Anhang (Abschnitt 5.10) dargestellten Ereignisse "
        "nach dem Bilanzstichtag hin, insbesondere auf die laufenden Verhandlungen zur Refinanzierung "
        "kurzfristiger Verbindlichkeiten. Zum Zeitpunkt der Berichtserstellung (März 2025) ist das "
        "Refinanzierungspaket noch nicht final abgeschlossen. Die dargestellte Situation begründet "
        "keine Einschränkung unseres Prüfungsurteils.",
        body))
    story.append(Spacer(1, 0.5 * cm))
    story.append(Paragraph("Frankfurt am Main, März 2025", body))
    story.append(Spacer(1, 0.3 * cm))
    story.append(Paragraph("Muster & Partner Wirtschaftsprüfungsgesellschaft mbH", body))
    story.append(Paragraph("Dr. Moritz Muster — Wirtschaftsprüfer", small))
    story.append(Paragraph("Petra Beispiel — Wirtschaftsprüferin", small))

    return story


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    output = "rhein-main-industrie-jahresabschluss-2024.pdf"
    doc = SimpleDocTemplate(
        output,
        pagesize=A4,
        rightMargin=2.5 * cm,
        leftMargin=2.5 * cm,
        topMargin=2.5 * cm,
        bottomMargin=2.5 * cm,
    )
    doc.build(build_content())
    print(f"PDF created: {output}")
