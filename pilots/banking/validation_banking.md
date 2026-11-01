# Banking Pilot — Validierungstabelle

Eingabedatei: `rhein-main-industrie-jahresabschluss-2024.pdf`
Unternehmen: Rhein-Main Industrie GmbH, Frankfurt am Main
Geschäftsjahr: 2024

## Bewusst eingebaute Auffälligkeiten

| # | Art | Wert | Erwartung |
|---|---|---|---|
| 1 | **Offensichtlich** | KV 2024: 3.850.000 EUR / KV 2023: 2.750.000 EUR | +40,0% YoY — muss als Anomalie erkannt werden |
| 2 | **Versteckt** | UV 3.450.000 / KV 3.850.000 | Current Ratio = 0,896 (< 1,0) — Liquiditätsproblem |
| 3 | **Positiv** | EK 4.900.000 / BS 15.250.000 | EK-Quote = 32,1% (> 30%) — solide Kapitalbasis |

---

## Erwartete Outputs pro Node

| Node | ID | Erwarteter Output | Akzeptanzkriterium | Toleranz |
|---|---|---|---|---|
| 1 Ingest | `ingest` | `document_type`: "Jahresabschluss" | Korrekte Klassifikation | Synonyme OK: "Jahresabschluss 2024", "Abschluss" |
| | | `company_name`: enthält "Rhein-Main" | Unternehmensname erkannt | Teilstring genügt |
| | | `fiscal_year`: "2024" | Geschäftsjahr extrahiert | Auch "31.12.2024" OK |
| 2 KPI-Extraktion | `extract_kpis` | `bilanzsumme_current`: 15.250.000 | ±5% Toleranz | 14.487.500–16.012.500 |
| | | `eigenkapital_current`: 4.900.000 | ±5% | 4.655.000–5.145.000 |
| | | `umlaufvermoegen_current`: 3.450.000 | ±5% | 3.277.500–3.622.500 |
| | | `kurzfristige_verbindlichkeiten_current`: 3.850.000 | ±5% | 3.657.500–4.042.500 |
| | | `kurzfristige_verbindlichkeiten_prior`: 2.750.000 | ±5% | 2.612.500–2.887.500 |
| | | `umsatz_current`: 18.500.000 | ±5% | 17.575.000–19.425.000 |
| | | `ebit_current`: 1.200.000 | ±10% | 1.080.000–1.320.000 |
| | | `abschreibungen_current`: 1.200.000 | ±10% | 1.080.000–1.320.000 |
| 3 Kennzahlen | `calculate_ratios` | `ek_quote`: 0,321 | Deterministische Berechnung | exakt (±0,001 Rundung) |
| | | `current_ratio`: 0,896 | Muss < 1,0 sein | exakt (±0,01 Rundung) |
| | | `verschuldungsgrad`: 2,11 | Fremdkapital/EK | ±0,1 |
| | | `ebitda_marge`: 0,130 | EBITDA/Umsatz | ±0,01 |
| | | `kv_veraenderung_yoy`: +0,400 | +40,0% | ±0,02 |
| 4 Anomalien | `detect_anomalies` | ≥ 2 Anomalien erkannt | Current Ratio + KV YoY | Beide müssen im Output enthalten sein |
| | | Anomalie 1: Current Ratio < 1,0 | Im Text erwähnt | "Current Ratio", "Liquidität" oder äquivalent |
| | | Anomalie 2: KV +40% YoY | Im Text erwähnt | "Verbindlichkeiten", "+40%", "kurzfristig" |
| 5 Risk Gateway | `risk_gateway` | `routing_decision`: "full_analysis" | Anomalien → kein Fast Track | Bei 0 Anomalien: "fast_track" |
| 6 Risikoklasse | `classify_risk` | `risk_level`: "GELB" oder "ROT" | NICHT "GRÜN" | Beide akzeptabel; ROT wenn Current Ratio dominant |
| 7 Compliance | `compliance_check` | ≥ 1 Compliance-Flag | DORA oder EU AI Act erwähnt | Mindestens 1 konkreter Flag im Output |
| 8 Human Oversight | `human_oversight` | `oversight_required`: True | Immer True — hardcoded | Niemals False |
| | | `oversight_note`: nicht leer | Text mit Zeitstempel | Muss "EU AI Act Art. 14" enthalten |
| 9 Summary | `summarize` | `summary`: nicht leer | ≥ 200 Zeichen | Executive Summary vorhanden |
| | | Erwähnt Liquiditätsproblem | Current Ratio oder kurzfristige Verbindlichkeiten | Pflicht-Keyword |
| | | Erwähnt Risikoklasse | "GELB" oder "ROT" im Text | Pflicht-Keyword |
| | | Erwähnt Human Oversight | "EU AI Act" oder "menschliche Freigabe" | Pflicht-Keyword |

---

## Erwartete Kennzahlen (Referenz)

Alle aus dem Quellcode der Bilanz berechnet — deterministische Sollwerte:

| Kennzahl | Formel | Sollwert |
|---|---|---|
| EK-Quote | 4.900.000 / 15.250.000 | **32,1%** |
| Current Ratio | 3.450.000 / 3.850.000 | **0,896** |
| Verschuldungsgrad | 10.350.000 / 4.900.000 | **2,11x** |
| EBITDA-Marge | 2.400.000 / 18.500.000 | **13,0%** |
| KV-Änderung YoY | (3.850.000 - 2.750.000) / 2.750.000 | **+40,0%** |
| Fremdkapital | 15.250.000 - 4.900.000 | **10.350.000 EUR** |
| EBITDA | 1.200.000 + 1.200.000 | **2.400.000 EUR** |

---

## Routing-Erwartung

Mit dem Testdokument wird immer der **full_analysis**-Pfad genommen:

```
ingest → extract_kpis → calculate_ratios → detect_anomalies → risk_gateway
        ↓ (full_analysis — 2 Anomalien erkannt)
classify_risk → compliance_check → human_oversight → summarize → END
```

Der **fast_track**-Pfad (direkt zu `summarize`) kann mit einem synthetischen Dokument
ohne Auffälligkeiten getestet werden.
