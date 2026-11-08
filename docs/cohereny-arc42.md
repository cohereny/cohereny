# Cohereny — Arc42 Architekturdokumentation

<img src="assets/branding/cohereny-logo-horizontal.svg" alt="cohereny Logo" width="360"/>

**SHAPE · ADAPT · LEARN · REPEAT**

> **Version:** 2.63 (basiert auf Whitepaper v4.40)
> **Autor:** Thomas Roth
> **Datum:** 2026-11-08
> **Status:** Pre-Release (vor 1.0)
> **Template:** Arc42 v8.2 (arc42.org)

> **Zum Stand dieses Dokuments:** Die Architekturdokumentation ist seit Juni 2026 mit dem Projekt
> gewachsen. Maßgeblich für den Implementierungsstand ist die Tabelle „What is built“ in der
> [README](https://github.com/cohereny/cohereny#what-is-built); §1.4 folgt ihr. Einzelne Abschnitte
> beschreiben die Ziel-Architektur oder einen früheren Stand und können hinter dem Code
> zurückliegen; die Überarbeitung läuft. ADRs bleiben als Entscheidungen ihrer Zeit stehen,
> Überholtes ist markiert. Das Änderungsprotokoll ist Historie und bleibt, wie es war; ersetzt sind
> darin nur interne Verweise, die ein Leser nicht auflösen kann.
>
> **Ziel und Stand:** Cohereny ist ein Pre-Release. Das Dokument beschreibt das Ziel 1.0; was
> heute fehlt, trägt den Meilenstein, der es bringt (→ M5 … M8, Übersicht im Whitepaper §10).

**Änderungen v2.62 → v2.63 (2026-11-08) — CHY-250, CHY-230: Piloten abgekündigt, Ersetzen genau benannt:**
- **ADR-018, dritter Nachtrag:** „Vor jedem Modellaufruf“ gilt für `/v1/complete` und die `llm_call`-Knoten der Workflows. Die LangGraph-Piloten rufen das Modell mit den Werten auf, der Bank-Pilot schreibt sie auch in seinen Langfuse-Trace; `/v1/route` überlässt den Aufruf dem Client. Gefunden bei der Prüfung der öffentlichen Dateien vor dem Schnappschuss vom 08.11.
- **ADR-028, Nachtrag 08.11.:** Beide Piloten sind abgekündigt; `/v1/pilots` und `/v1/questions` tragen im API-Vertrag `deprecated`. Sie werden als Workflows auf den Runner umgestellt (CHY-172).
- **Nachgezogen:** §1.1, §1.4, §6.7, §8.3, §13.1 (Art. 9, Art. 25).

**Änderungen v2.61 → v2.62 (2026-11-07) — CHY-200, CHY-153: Läufe abbrechen:**
- **Abbruch von Workflow-Läufen ✅ (EU AI Act Art. 14(4)(e)):** `POST /v1/workflows/runs/{run_id}/cancel` für die Person, die den Lauf gestartet hat, und ab `reviewer` für alle, die ihn sehen. Lauf und offene Knoten enden als `cancelled` (Migration 038): weder Fehler noch Ablehnung, am Gate wird keine Entscheidung verbucht. Die Begründung ist freiwillig und wird mit Platzhaltern gespeichert; der Audit-Eintrag `workflow.run_cancelled` nennt Ausgangszustand und gestoppte Knoten, nie die Begründung. Ein Knoten, der beim Abbruch noch arbeitet, läuft zu Ende, sein Ergebnis wird verworfen.
- **Abbruch am Pilot-Gate (CHY-153):** löst das Gate mit dem dritten Wert `cancelled` statt mit `rejected`. Der Lauf endet als `failed` mit der Meldung des Abbruchs, statt als Art.-14-Ablehnung verbucht zu werden, die der Trigger aus 014 abwies. Nachtrag an ADR-013.
- **Nachgezogen:** §4.1 (E6), §6.9, §8.7, §13.2, §13.5.

**Änderungen v2.60 → v2.61 (2026-11-07) — CHY-113, CHY-152: Migrationen nur vorwärts, mit Wächter:**
- **§8.11:** Die Regel „nur vorwärts“ hat einen Wächter. `migrations/CHECKSUMS` verzeichnet jede Migration, und ein Test fällt, sobald eine gemergte Datei sich ändert, fehlt oder eine Nummer doppelt vergeben ist. Regel und Hinweis für Betreiber stehen in `CONVENTIONS.md` und `DEPENDENCIES.md`.
- **Erste Berichtigung nach der Regel:** Migration 037 begründet die Aufzeichnung einer Freigabe mit Art. 12 statt Art. 14 (CHY-152); 014 bleibt unverändert.

**Änderungen v2.59 → v2.60 (2026-11-07) — CHY-18: Muster-DSFA für das Arztbrief-Szenario:**
- **Muster-DSFA ✅:** `DATA-PROTECTION-EXAMPLE.md` (deutsch `DATA-PROTECTION-EXAMPLE.de.md`) im Code-Repository, eine ausgefüllte DSFA für ein erfundenes Klinikum, das Entlassbriefe für die ePA strukturieren lässt — nach Art. 35(7), mit neun Risiken nach Eintrittswahrscheinlichkeit und Schwere, Maßnahmen, Restrisiken und Ergebnis. Sie prüft die Vorlage `DATA-PROTECTION.md` am Fall und hat zwei Ungenauigkeiten gefunden, die dort berichtigt sind: Ein Name, den niemand nennt, wird auch gespeichert; ein technisches Konto, das Läufe startet, darf sie auflösen.
- **Nachgezogen:** §6.7, §8.7, §11.3, §13.1, §13.6.

**Änderungen v2.58 → v2.59 (2026-11-07) — CHY-234: Läufe mit Platzhaltern, Zuordnung verschlüsselt, Auflösen mit Audit:**
- **ADR-018, zweiter Nachtrag:** Ersetzen am Eingang ist gebaut (Entscheidungen E1–E9 vom 06.11.). Ein Workflow-Lauf wird nur mit Platzhaltern gespeichert: die Eingabe, jeder Schritt, die Begründungen am Gate. Die Zuordnung liegt je Lauf verschlüsselt in `pii_mappings` (Migration 036). Aufgelöst wird nur für die Person, die den Lauf gestartet hat, und ab `reviewer` für alle, die ihn sehen, jedes Mal im Audit. Läufe von vor 036 behalten ihre Werte bis zum Ende ihrer Frist, Pilotläufe bis zu ihrer Ablösung.
- **ADR-014, Nachtrag:** Die erste eigene Verschlüsselung — eine benannte, enge Ausnahme: die Zuordnung je Lauf, AES-256-GCM in der Anwendung, Schlüssel `PII_MAPPING_KEYS` außerhalb der Datenbank. Alles andere bleibt beim Betreiber.
- **Gefunden und behoben (CHY-244):** Der Trace zeichnete bei `/v1/complete` die Antwort des Modells ungeprüft auf; was das Modell von sich aus schrieb, erreichte Langfuse im Klartext.
- **Nachgezogen:** §1.4, §6.7, §8.3, §8.7, §11.3, §13.1 (Art. 17, 25, 32), §13.5 (OPS.1.1.5).

**Änderungen v2.57 → v2.58 (2026-11-06) — CHY-85, CHY-92: eigene Datenbankrollen, Lese-Isolation für Laufinhalte; Entscheidungen vom 06.11.:**
- **ADR-009, Nachtrag:** Punkt 2 ist umgesetzt (CHY-85), mit zwei Rollen je Datenbank statt der im Text genannten: Die Eigentümerin (`<db>_migrator`) führt die Migrationen aus, als Laufzeitrolle (`<db>_runtime`) verbinden sich API, CLI und Löschlauf. Die Rechte je Tabelle stehen in `kernel/retention.py` (Migration 033), nur die Eigentümerin setzt `superseded_by_version` (Migration 034), und die API startet nicht mit mehr Rechten. Die Trigger halten gegen die Anwendung, nicht gegen die Eigentümerin.
- **ADR-047 neu:** Wer einen Lauf sieht, setzt die Datenbank per Row-Level Security durch (CHY-92): wer ihn gestartet hat, die Mitglieder des Workflow-Bereichs und der Bereiche darüber, `admin`. Ohne gesetzten Akteur sieht eine Abfrage nichts; ein Systemkontext nur für Runner, Pilot-Threads und Löschlauf; `GET /v1/me`. Blöcke, Compose und Route → M6 (CHY-237). Schreiben je Geltungsbereich ist als eigene Lücke benannt (CHY-243).
- **§8.11 neu:** Rollen, Reihenfolge beim Update, Migrationen nur vorwärts mit dem Backup als Rückweg (CHY-113, entschieden 06.11.).
- **Entscheidungen vom 06.11.:** ADR-004 mit angekündigter Abstufung der Prüftiefe nach Risiko (CHY-223 → M6), `/v1/complete` folgt diesen Stufen (CHY-199 → M6); ADR-018 Ersetzen am Eingang im Kern nach M5 vorgezogen (CHY-234), Art.-17-Suche und UI → M7 (CHY-239); ADR-028 Zweckbindung je Knoten im Ablauf gelöst (CHY-177), strikte Eingaben je Workflow → M7 (CHY-238); ADR-042 Zurückziehen in direkten Bereichen symmetrisch (CHY-170); ADR-046 Nachtrag, Fristen für Audit-Log, Blöcke und Messungen → M7 (CHY-242).
- **DPIA:** Die Grundlage für die DSFA des Betreibers steht als `DATA-PROTECTION.md` im Code-Repository (CHY-18); die Muster-DSFA für das Arztbrief-Szenario folgt.
- **§1.4 an die README angeglichen:** Die Zeilen zu vier Augen, Aufbewahrung und Platzhaltern fehlten seit dem 05.11.
- **Nachgezogen:** §1.4, §4.1 (E6), §5.6, §5.7, §6.1, §6.7, §7.2, §8.1, §8.2, §8.3, §8.7, ADR-017, ADR-030 (Punkt 3), §11.2, §11.3, §12 (Glossar: „Append-Only“, neu „Laufzeitrolle“, „Row-Level Security“, „Systemkontext“), §13.1–§13.6.

**Änderungen v2.56 → v2.57 (2026-11-05) — CHY-63: PII-Redaction an der Grenze:**
- **ADR-018 entschieden, Nachtrag:** Ersetzt wird an der Grenze, unmittelbar vor jedem Modellaufruf und bei jedem Modell. Modell, Gateway und Trace sehen Platzhalter, die Antwort bekommt die Werte zurück, die Zuordnung wird nicht gespeichert. Erkennung Stufe 1 ohne Modell: mitgelieferte Muster, Bezeichner des Aufrufers, Regeln des Bereichs als Blocktyp `redaction_rule` (Migration 032, nie komponiert). Eine mitgelieferte Regel abschalten nur oben im Baum, als Lockerung von `pii_redaction` erklärt, mit vier Augen (ADR-045) und unter der Ratsche (ADR-017). Ersetzen am Eingang fest eingeplant (CHY-234, M7), NER (CHY-235, M7).
- **Nachgezogen:** §1.1, §6.7 (Szenario Healthcare), §8.3 (Payload-Daten, Löschantrag), §8.7 (Regulatorik-Übersicht, Architektur-Lücken), §11.3 (Risiko), §13.1 (Art. 9, 17, 25).
- **Berichtigt:** In §1.1 und §8.7 standen die Aufbewahrungsfristen noch auf → M5 (beim Durchgang zu CHY-17 übersehen). §6.7 versprach, Prompts, Traces und Ergebnisse seien „von vornherein pseudonym“ — für gespeicherte Ergebnisse gilt das erst mit CHY-234.

**Änderungen v2.55 → v2.56 (2026-11-05) — CHY-19: Restarbeiten Art. 17 erledigt:**
- **§13.1 (Art. 17):** `UserPreferences` gibt es nicht; wer die Tabelle anlegt, muss sie einer Aufbewahrungsklasse zuordnen (Test aus CHY-17). Das Zusammenspiel mit den Fristen ist durch ADR-046 geklärt: Die Anonymisierung löscht nichts, Klarnamen stehen nur in `actors`, Freitext der Person läuft mit `run_content` ab. Die Grenze — eine Inhaltsfrist länger als ein Monat — geht als Prüfpunkt in die DPIA (CHY-18).

**Änderungen v2.54 → v2.55 (2026-11-05) — CHY-15: Kryptokonzept:**
- **ADR-014 entschieden:** Cohereny verschlüsselt ruhende Daten nicht selbst, der Betreiber wählt die Maßnahme. Das Kryptokonzept für Betreiber steht als `CRYPTOGRAPHY.md` im Repository (englisch, öffentlich, mit dem Code versioniert): Zuständigkeit, Token-Prüfung, Schutzbedarf nach Datenklasse, Anforderungen an Transport, Ruhezustand und Geheimnisse, Schlüsselverzeichnis, bekannte Lücken (CHY-232, CHY-233, CHY-195, CHY-85, CHY-63). Die Referenzinstallation verschlüsselt mit INFRA-163.
- **Nachgezogen:** §8.7 (Regulatorik-Tabelle), §11.3 (Risiko), §13.1 (Art. 32), §13.5 (BSI CON.1).

**Änderungen v2.53 → v2.54 (2026-11-05) — CHY-17: Aufbewahrung:**
- **ADR-046 neu:** Die Inhalte eines Laufs werden nach Ablauf geleert, seine Spur später gelöscht. Fristen sind Konfiguration je Aufbewahrungsklasse und Geltungsbereich (die nächstgelegene Angabe gilt), gepflegt über die Admin-API und auditiert; was es an Daten gibt, ist Code mit Test. Der Löschlauf `cc retention purge` wird vom Betrieb angestoßen und hinterlässt bei jedem Lauf einen Audit-Eintrag.
- **Nachgezogen:** §8.7 (Regulatorik-Tabelle), §8.9 (Konsequenz Langfuse), §11.3 (Risiko Payloads), §13.1 (Art. 17), §13.5 (BSI OPS.1.1.5), Glossar (neu „Aufbewahrungsklasse“).

**Änderungen v2.52 → v2.53 (2026-11-05) — CHY-163, CHY-171: vier Augen, wo Daten den Ort wechseln:**
- **ADR-045 neu:** Eine Routing-Regel mit einem Modell unterhalb `critical` und ein Baustein, der eine Datenschutzregel lockert, sind Datenfluss-Bausteine. Freigeben darf sie nur, wer in dieser Runde keine Version geschrieben hat, in jedem Geltungsbereich; im direkten Bereich werden sie eingereicht statt aktiv. Der Einzelbetrieb wird gezählt, nicht geschaltet, und ist an vier Stellen gekennzeichnet. Technische Konten (Gruppe `chy-service-accounts`, Migration 029) zählen nicht als Person.
- **Audit:** `block.created` wird geschrieben (CHY-171), neu sind `block.edited` für das Bearbeiten eines Entwurfs und `actor.service_account_changed`. Erst damit ist für jede Version bekannt, wer sie geschrieben hat.
- **ADR-011, ADR-042:** Nachtrag zu ADR-045 — die Freiheit der direkten Bereiche gilt für Prompt-Material, nicht für Datenflüsse. Die Entscheidungstexte bleiben stehen.
- **Nachgezogen:** §5.7 (Audit-Katalog), §6.2, §8.7, §11.2, §13.5 (BSI ORP.4), Glossar (neu „Datenfluss-Baustein“, „Einzelbetrieb“, „Technisches Konto“; „Contributor“).

**Änderungen v2.51 → v2.52 (2026-11-04) — CHY-189, CHY-229: Schnellstart, Einsicht am Gate:**
- **§1.4, §3.2, §7, §13.5 (BSI SYS.1.6):** Installation mit einem Befehl gebaut ✅. `deploy/quickstart/` bringt API, Datenbank, Cache, ein lokales Modell (Ollama) und den Entwickler-Issuer auf einen Rechner, mit einem Beispiel: Kundenservice mit Vererbung, Datenschutzregel, Routing-Regeln und einem Workflow mit Freigabe. Der Durchlauf der Anleitung läuft je MR in der CI.
- **§3.2 Konfiguration:** Die Modellliste (`routing-config.yaml`) liegt an einem Pfad aus `ROUTING_CONFIG`; ohne Angabe gilt `router/routing-config.yaml`. Vorher musste ändern, wer eigene Modelle anbinden wollte, Quellcode.
- **ADR-034 (Folge für den Datenweg):** `llm_call` nimmt auch `system` über `input_mapping`, damit Kontext (Anweisung) und Eingabe des Laufs (Anfrage) gemeinsam ein Modell erreichen, wie bei `/v1/complete`.
- **§1.4, §8.7 (Art. 14), ADR-004 (Umsetzungsstand):** Wer am `human_review`-Gate entscheidet, liest vorher, was die Knoten erzeugt haben (`GET /v1/workflows/runs/{id}/nodes`, CHY-229). Bis dahin entschied der Prüfer, ohne den Entwurf zu sehen.

**Änderungen v2.50 → v2.51 (2026-11-04) — CHY-192: `DEV_MODE` entfernt:**
- **§8.1, §11.2, §13 (CRA Anhang I Teil I Nr. 2 Buchst. b):** `DEV_MODE` ist entfernt. Jede Stufe prüft Tokens; einen Schalter, der die Authentifizierung abschaltet, gibt es nicht mehr. Entwicklung und CI holen Tokens beim lokalen Entwickler-Issuer (`scripts/dev_issuer.py`); einen Issuer über http nimmt die API nur unter `ENVIRONMENT=dev` an. Die API-Doku hängt an einem eigenen Schalter (`API_DOCS`), und das Oberflächen-Gate weist einen Aufrufer ohne Audience ab, statt beiseitezutreten.

**Änderungen v2.49 → v2.50 (2026-11-03) — CHY-225: Meilensteine neu geordnet:**
- **§1.4, §3.2, §7, §11.2, §13 (CRA Anhang I, BSI SYS.1.6):** `DEV_MODE` entfernen (CHY-192) von M7b nach M5, die Installation mit einem Befehl (CHY-189) von M8 nach M5. Grund: Die Meilensteine laufen jetzt nacheinander, M5 zuerst; der Entwickler-Issuer, den CHY-192 bringt, trägt auch den Schnellstart (Whitepaper v4.28 §10).

**Änderungen v2.48 → v2.49 (2026-11-03) — CHYUI-9: Anmeldung der Referenz-UI:**
- **§6.5 BFF-Pattern, §7.2:** Die Referenz-UI meldet Nutzer selbst über den OIDC-Client der Oberfläche `author` an (Authorization Code mit PKCE). Zugriffs- und Refresh-Token liegen in der serverseitigen Sitzung, der Browser hält nur ein httpOnly-Cookie. Vorher trug sie einen statischen Token, der leer war; ihre Aufrufe gingen nur durch, weil die dev-Stufe ohne Authentifizierung lief (CHY-192). `.env.ui` enthält jetzt Adresse der API und OIDC-Client.

**Änderungen v2.47 → v2.48 (2026-11-03) — CHY-202, CHY-93: python-jose ersetzt:**
- **§11.2 Technische Schulden:** `python-jose` erledigt. PyJWT prüft die Tokens; die Audience wird in der Dekodierung gegen die acht Audiences der Oberflächen geprüft, `exp`, `iss`, `aud` und `sub` sind Pflicht. Die Token-Prüfung ist gegen selbst signierte Tokens getestet (21 Fälle durch `require_auth`, CHY-93) und mit echten Tokens von Authentik nachgewiesen, für einen UI- und einen Maschinen-Client.

**Änderungen v2.46 → v2.47 (2026-11-03) — CHY-181, CHY-220: Drift-Erkennung (ADR-044):**
- **ADR-044 neu:** Drift wird automatisch erkannt und von einem Menschen beurteilt. Drei Ursachen (Modell, eigener Kontext, veränderte Eingaben), vier Wege: Läufe bei Änderungen mit Probelauf vor der Freigabe, Nachtlauf nach Zeitplan, Signale aus dem Betrieb, ab M7 ein Messfühler mit Einbettungen. Wiederholungen statt Einzelmessung, Kontextstand an jeder Messung, vorgeführter Fehlerfall je Ursache als Bedingung für M8. Neu angelegt: CHY-217 bis CHY-220.
- **ADR-043, zweiter Nachtrag:** Punkt 6 („auf Abruf“) beschrieb die Referenzinstallation, deren lokale Inferenz-Rechner nachts aus sind, nicht das Produkt; abgelöst durch ADR-044. CHY-181 (Zeitplan) von „Nach 1.0“ nach M6. Der Entscheidungstext bleibt stehen.
- **ADR-036:** Kasten „Präzisiert durch ADR-044“ — „Einbettungen“ in der Non-Goal-Zeile meint Einbettungen für Retrieval. Die Entscheidung bleibt unverändert.
- **Nachgezogen:** §1.4, §4.2, §4.3, §5.1, §5.5, §6.3, §6.6, §8.7, §8.9, §13.2, §13.4, die Kästen an ADR-006, ADR-019, ADR-020 und ADR-024, die Anmerkung Prompt-Injection in ADR-019, Glossar („Nightly Benchmark“, neu „Drift-Erkennung“, „Messfühler“, „Probelauf“).

**Änderungen v2.45 → v2.46 (2026-11-02) — CHY-22: SBOM gebaut:**
- **§13.3 und Regulatorik-Übersicht:** Anhang I Teil II Nr. 1 auf ✅. Jede Pipeline erzeugt eine CycloneDX-SBOM der Python-Abhängigkeiten, mit Lieferant je Komponente und gegen das Schema geprüft; `scripts/check_sbom.py` erzwingt alle Top-Level-Abhängigkeiten. Grenze: Auflösung zur Pipeline-Zeit, Lock-Datei → M8 (CHY-211). EVB-IT-Zeilen und Maßnahmenliste nachgezogen; CVE-Prüfung blockierend bleibt offen (CHY-198).

**Änderungen v2.44 → v2.45 (2026-11-01) — CHY-197: Entscheidungen aus der Durchsicht:**
- **Ziel und heutiger Stand:** Das Dokument beschreibt weiter das Ziel 1.0. Lücken werden nicht herabgestuft, sondern bekommen den Meilenstein, der sie schließt (→ M5 … M8, Übersicht im Whitepaper §10 „Weg zu 1.0“), immer mit Ticket und Beschreibung. Das Zeichen zeigt den heutigen Stand. Neu angelegt: CHY-199 bis CHY-209, CHYUI-11.
- **Regulatorik (§8.7, §13):** Legende „✅ liefert die technische Grundlage … kein Eintrag bedeutet Konformität“. Wo SECURITY.md eine Lücke nennt, steht ⚠️ mit Meilenstein: Secure by Default, Zugriffsschutz, Least Privilege, Sicherheitsupdates, Art. 32, Art. 14, MaRisk-Modellgovernance, BaFin-Stilllegung, BSI ORP.4 und KI-Aufsicht. CRA-Fundstellen nach Anhang I Teil I Nr. 2 Buchst. a–d und Teil II Nr. 1/5; Herstellerpflichten gelten ab der Veröffentlichung (Meldeprozess → M7b). Digital Omnibus mit Fundstelle VO (EU) 2026/1744; EVB-IT 2026 (03/2026); „wahrscheinlich kein KI-System“ auch in §8.7.
- **Human Oversight (E6, §8.7, §13):** gebaut ist das Pflicht-Gate im Workflow-Weg; `/v1/complete` ohne Gate (→ M5, CHY-199), Abbruch von Workflow-Läufen (→ M5, CHY-200), Freigabe nach einer Regression (→ M6, CHY-64).
- **Laufzeitsicht:** 6.1 auf 🔧 (Routing im Diagramm, Adaption → M6); 6.2 neu: der gebaute Freigabeweg statt „Neue Übersetzungsregel“ mit Moderator; neues 6.9 „Workflow-Lauf mit Freigabe-Gate“ (✅); 6.6 und 6.7 mit Stand-Hinweis nach ADR-041 und ADR-043.
- **Bausteinsicht:** Statuszeichen in den Boxen von §5.1, „Governance Engine“ → „Freigabeweg“ (ADR-042); Typ-Strukturen der Adaption als 📋 → M6; Bootstrap-Templates nach 1.0 (CHY-209); §5.6 als maßgebliche Fassung des Datenmodells.
- **Referenzinstallation als Beispiel** (§3, §7, §8.1, ADR-014, ADR-015, ADR-031): Stack, Stufen, CI und Netzsegmentierung gekennzeichnet bzw. als Empfehlung für den Betrieb gefasst; SOPS, Terraform und `verify:test` neutral. Referenz-UI: Veröffentlichung → M8 (CHYUI-11). Merge-Request-Nummern und INFRA-Kennungen entfernt, auch im Änderungsprotokoll — dort nur die Verweise, nicht der Inhalt.
- **Qualitätsziele Q3/Q9 und §8.8** an ADR-038 angeglichen: Ein neues Modell braucht eine neue Fassung von `router/routing-config.yaml`. §11.2 führt die bekannten Lücken aus SECURITY.md mit Meilenstein. Das Buch „Architekturmuster für Enterprise-KI“ ist in §1.4 eingeführt.
- **Schlussprüfung vor der Veröffentlichung:** Konformitätswörter in §6.7, §8.2, ADR-014 und §13.3 ersetzt; Meilenstein für CHY-70 einheitlich M6; Vier Augen bei Blöcken einheitlich CHY-163; Evaluations-Engine 🔧; „veröffentlichtes Image“ → Dockerfile; Lizenzlage der Gemma-Modelle berichtigt (Gemma 3 nicht Apache 2.0, Wechsel auf Gemma 4 → M7b).

**Änderungen v2.43 → v2.44 (2026-10-31) — CHY-197: Abgleich mit dem Code:**
- **Kopf bis §4, §10 bis §13** gegen den Code und die README geprüft. Berichtigt: Rolle „Moderator" heißt `reviewer`; Blocktypen nach Migration 021 statt nach dem Entwurfsmodell; Komposition „überschreibt" nicht, sie fügt flach nach tief an (wie §5.3); Benchmark auf Abruf statt nächtlich, `review_needed` am Lauf statt am Block, Läufe noch nicht gebaut; Human Oversight: gebaut ist der Pflichtknoten im Workflow-Runner, die Freigabe nach einer Regression ist geplant; Identity Provider: getestet mit Authentik, dessen Aufbau der Code erwartet; Konfiguration auch über `routing-config.yaml`; Workflow-Logik im eigenen Runner (ADR-028).
- **Als gebaut nachgetragen:** SSE-Persistenz (CHY-58), Anonymisierung nach Art. 17 (`POST /v1/admin/actors/{actor_ref}/anonymize`, CHY-34), CVE-Prüfung in CI (pip-audit, Container Scanning — nur meldend, CHY-198).
- **Regulatorik-Anhang:** Eigenschaften der Referenzinstallation (Backup, TLS „über gesamten Stack") durch das ersetzt, was Cohereny mitbringt; `privacy_safe` durch die Datenklasse je Geltungsbereich (ADR-041); Basis-Image 3.14; `pyproject.toml` statt `requirements.txt`.
- **§5 bis §8** gegen den Code geprüft. Der Workflow-Runner ist gebaut (CHY-127) und nicht mehr als fehlend geführt; `loop_limit_reached` und die Ausführung von Sub-Workflows als noch nicht gebaut markiert; eine ungültige Aktivierung antwortet 409, nicht 422, und es sind vier Regeln. Domänenmodell §5.6 nach dem Schema: Rollen, Geltungsbereiche mit Datenklasse, Blöcke nach Migration 013/016/021, Routing-Regel mit `candidates`, Kalibrieranker nach ADR-043, Benchmark-Tabellen nach Migration 028; Merge Requests als entfallen markiert (Migration 025); Feedback-Domäne als geplant (CHY-40).
- **Löschweg §8.3** nach dem Gebauten (`[anonym]`, Actor-ID bleibt, Payloads nicht erfasst) statt nach dem Vorschlag aus ADR-016; **Cache §8.5** mit dem echten Schlüssel, fester TTL und dem Hinweis, dass auch ein Treffer PostgreSQL liest; Autorisierung §8.1 mit der Ausnahme `DEV_MODE` und ohne Autorisierung je Geltungsbereich (SECURITY.md).
- **§9 ADRs:** Die Entscheidungen bleiben, wie sie getroffen wurden; wo der Code abweicht oder nichts gebaut ist, steht unter „Status" ein Umsetzungsstand (ADR-004, -006, -008, -010, -011, -012, -016, -017, -019, -020, -024, -026, -027, -036, -039, -043; ADR-013 ein Status-Update). Berichtigt: `domain/` → `kernel/`, ltree-Operator in ADR-008, Migration 018 statt 011 für den Workflow-Graphen, „fünf Kontexte" mit dem Hinweis auf vier seit ADR-042, Eigentum der Kontexte in ADR-030, `merge-requests` in ADR-031 als entfallen, „Vier Augen erzwingt CHY-163" als offen (SECURITY.md), falscher Verweis auf ADR-043 in ADR-028, Betriebsangaben in ADR-024 und ADR-043.
- **Aus der Whitepaper-Durchsicht nachgezogen:** Verzweigung in §5.6 — genommen wird jede zutreffende Kante, `execution_mode` wird nicht ausgewertet; §13.5 SYS.1.6 und §13.6 IEC 62304 ohne Betriebsangaben der Referenzinstallation.
- **Referenzinstallation** aus §5.5, §6.3, §6.5 und §8.9 genommen (systemd-Dienst, Cron 03:30, Stufenname, ntfy, Prometheus/Grafana als Cohereny-Metriken); **AdaptVault §8.9** als geplant markiert, Testfälle nach ADR-043.
- **Verweise:** Anker auf §8.8 repariert, 11.3 wieder im Inhaltsverzeichnis eingerückt, alte Tracker-Nummern in §11.3 durch CHY-Kennungen ersetzt; „Datenschutz-Ratsche" → „Privacy-Ratchet"; veralteter Betriebsstand in §3.2 ersetzt.

**Änderungen v2.42 → v2.43 (2026-10-31) — CHY-188: Umsetzungsstand ADR-009:**
- **ADR-009:** Hinweis unter „Status". Die Rollentrennung aus Punkt 2 ist nicht gebaut (CHY-85); die einzige Datenbankrolle ist Superuser und Eigentümerin aller Tabellen. Die Konsequenz „nicht umgehbar" gilt erst mit CHY-85. Gleichlautend in `SECURITY.md` unter „Known limitations".

**Änderungen v2.41 → v2.42 (2026-10-30) — CHY-188: dritter Lauf der Durchsicht:**
- **Hinweis zum Stand** oben eingefügt; §1.4 neu nach der README-Tabelle (Stand 2026-10-30) statt vom 09.07.
- **Gebautes als gebaut markiert:** §5.3 Komposition, §5.4 Freigabeweg (als Block-Lebenszyklus in `repository`, ADR-042), §5.4b Workflow-Runner. §5.4 sagte, kein Contributor könne einen Block direkt aktiv setzen — in direkten Geltungsbereichen geschieht genau das beim Schreiben (ADR-011).
- **`governance` als Kontext** im Zonenbild und in der ADR-030-Tabelle als entfernt markiert (ADR-042).
- **ADR-023** bereinigt: Ersetzungsrest im Namen, Positionierungsaussagen und Behauptungen über das Fremdprodukt entfernt, Abgrenzung sachlich wie im Whitepaper §11.4.
- **ADR-031:** Der Absatz „Noch offen“ beschrieb das Audience-Gate als wirkungslos. Die Übergangs-Audience ist entfernt; Nachtrag ergänzt.
- **Regulatorik:** C5 als „n/a“ statt ✅; Art. 14(4)(a) statt Art. 14(3)(c); Art. 9(2)(c) und Art. 72 statt Art. 9(9); MaRisk AT 4.3.4 nach RS 06/2026 (in Kraft seit 30.06.2026); Betrieb der Referenzinstallation (Prometheus/Grafana, restic) nicht mehr als Cohereny-Eigenschaft.

**Änderungen v2.40 → v2.41 (2026-10-30) — CHY-188: Durchsicht vor der Veröffentlichung:**
- **Aussagen an den Code angeglichen:** Nightly Benchmark und Adaption in den Regulatorik-Tabellen (Art. 14(3)(c), Art. 72) von ✅ auf 📋 bzw. ⚠️ — sie sind geplant, nicht gebaut. `SECURITY.md` als vorhanden geführt; der Art.-50-Hinweis ist in der UI gebaut (CHYUI-4), offen ist Art. 50(2) (CHY-118). Die C5-Aussage folgt der Berichtigung im Whitepaper (v4.13): C5:2026 enthält keine KI-Anforderungen
- **CRA:** Die Einordnung „kein Markteintritt, nicht anwendbar" trug nach der Veröffentlichung nicht mehr. Cohereny behandelt sich als Hersteller; die Pflichten sind eingeplant
- **Interna entfernt:** ein Hostname im Benchmark-Diagramm, Pfade und Gerätedaten der Referenzinstallation, Verweise auf interne Repositories, Dokumente und das Arbeitsprotokoll, Formulierungen zur Positionierung (ADR-023), interne Termine. §7.1 zeigt die API so, wie sie erreichbar ist: nur intern
- **Berichtigt:** „CC" (alter Produktname) → „Cohereny", „Open-Source-Projekt" mit AGPLv3 statt ohne Lizenzangabe; zwei Zeilen zu „offline bis zur Veröffentlichung" und „Release erst nach PoC" entfernt
- **Zweiter Lauf am selben Tag:** CRA auch in Vorbemerkung, Stärken- und Lückentabellen auf Herstellerpflicht umgestellt, Art. 50 und Nightly Benchmark in den übrigen Zeilen angeglichen, „Cohereny" im Fließtext durchgehend groß

**Änderungen v2.39 → v2.40 (2026-10-29) — CHY-188: Schreibweise:**
- Der Produktname heißt im Text durchgehend „Cohereny". Klein bleibt er nur, wo er technisch ist (Domain `cohereny.com`, Repository, Dateinamen, Bezeichner wie `cohereny-ui`, der Compose-Stack `cohereny`) oder zum Logo gehört.

**Änderungen v2.38 → v2.39 (2026-10-29) — CHY-188: Veröffentlichung vorbereitet:**

- **§2.2 Lizenz AGPLv3 statt GPLv3** (Nachtrag zu CHY-55). Unter der GPLv3 muss, wer Cohereny verändert und nur als Dienst betreibt, nichts offenlegen; die AGPLv3 verlangt es (Abschnitt 13). Erst damit hat die kommerzielle Lizenz für Anbieter gehosteter Dienste einen Gegenstand
- **Für die Veröffentlichung bereinigt (§3.2, §5.5, §7, §8.1, §8.9, ADR-014, ADR-016, ADR-024, ADR-038, ADR-043 und Änderungseinträge):** Hostnamen, Adressen, interne Domains und die Tunnel-Kennung durch Rollen ersetzt (Application-Server, DMZ-Host, Compute lokal, Compute Cloud). Ältere Änderungseinträge, die Hostnamen nannten, sind sinngleich umformuliert
- **Rechner, die Modelle ausführen, heißen nach ihrer Rolle, nicht nach dem Gerät:** *Compute lokal* (Ollama, Stufen `local-small` und `local-medium`) und *Compute Cloud* (Modelle per API: zwei über Ollama Cloud eingerichtet und nur für die Datenklasse `public` zugelassen, CHY-157; Claude vorgesehen, in den Diagrammen gestrichelt). Das Gerät wechselt, die Rolle bleibt. Messwerte nennen weiter das Gerät, auf dem gemessen wurde — ohne die Messbedingung sagt die Zahl nichts
- **§8.1: eine überholte Aussage entfernt statt umbenannt** — Secrets lägen in einem eigenen privaten Repository. Dort steht jetzt nur, dass sie in den Secret-Store des Betreibers gehören und nicht ins Repository
- **§7.2:** LiteLLM wird direkt oder über den Reverse Proxy erreicht; beides kommt vor. Die alte Fassung nannte nur den direkten Weg
- **§3.2 Konfigurationsangaben an den Code angeglichen**, weil Leser ab der Veröffentlichung danach konfigurieren: `DATABASE_URL` statt `POSTGRES_URI`, `OIDC_BASE_URL` statt `OIDC_ISSUER`/`OIDC_CLIENT_ID` (ADR-031), Gruppen `chy-*` statt `cc-*`, der Redis-Cache ist aktiv (ADR-010). Der Verweis auf `DEPENDENCIES.md` zeigt auf das Code-Repository

**Änderungen v2.37 → v2.38 (2026-10-06) — CHY-67 umgesetzt:**

- **ADR-043, Nachtrag:** Schritt 1 gebaut (CHY-67). `context_blocks` hat jetzt `UNIQUE (block_id, version)`; eine Messung nennt die Anker-Version per Fremdschlüssel, weil `learning` die Tabelle nicht lesen darf. Die gesperrte Zeile einer Freigabe trägt `content`

**Änderungen v2.36 → v2.37 (2026-10-06) — CHY-67/68/64: Benchmark auf Ankern:**

- **ADR-043 neu:** Testfälle des Benchmarks sind Kalibrieranker im Kontext-Repository (Block-Typ `calibration_anchor`, versioniert, freigegeben); nur Messergebnisse bekommen eine eigene Tabelle. Anker sind **kein Prompt-Material** mehr. Bewertet wird deterministisch (ADR-006 Stufe 1+2). Der Lauf geschieht **auf Abruf** über CLI und systemd-Dienst auf dem Application-Server, ein Zeitplan ist geparkt (CHY-181). Drei Schritte: CHY-67 → CHY-68 → CHY-64
- **ADR-019 entschieden** (A/B-Modus), §5.5 und Szenario 6.3 auf „auf Abruf" nachgezogen

**Änderungen v2.35 → v2.36 (2026-09-30) — CHY-172, CHY-176: Ablösung des Banking-Piloten, vier Augen:**

- **ADR-028, Nachtrag:** Die Ablösung des Banking-Piloten auf den eigenen Runner ist beschlossen (CHY-172, Thomas am 30.09.). Punkt 4 schützt den Piloten nur noch, bis der Ablauf im Runner steht; Study bleibt vorerst auf LangGraph
- **§5.6 `human_review`:** `four_eyes` wird durchgesetzt (CHY-176, Migration 027). Bis heute war der Wert speicherbar und ungelesen, und die erste Freigabe schloss jedes Gate — dieselbe Zusage ohne Deckung, die ADR-040 für `config` beschreibt

**Änderungen v2.34 → v2.35 (2026-09-29) — CHY-65 Schritt 3: Promote:**

- **ADR-042, zweiter Nachtrag:** Promote ist gebaut (CHY-65). Kopiert wird die neueste *aktive* Version samt Governance-Angabe, als neue Linie und als Entwurf im Ziel-Scope; das Original bleibt unverändert. Die Herkunft steht im ersten Audit-Eintrag der Kopie, `block.promoted`. Anders als der Wortlaut von Punkt 3 muss der Ausgangs-Scope nicht direkt sein
- Umsetzungsstand: alle drei Wege gebaut. Audit-Katalog: 24 Konstanten; `block.created` wird nie geschrieben (CHY-171)

**Änderungen v2.33 → v2.34 (2026-09-29) — CHY-65 Schritt 2: der direkte Weg:**

- **ADR-042 mit Nachtrag:** umgesetzt mit CHY-65. `review_mode(node_type)` im Kernel; im direkten Scope folgen auf Anlegen und Bearbeiten `submit` und `approve` in derselben Transaktion. Ein abgelehnter Block hinterlässt keinen Entwurf. Zwei Folgen, die die Entscheidung nicht vorhergesehen hatte: Governance-Angaben dürfen im direkten Scope bei einer Bearbeitung mitkommen, und `audit_log.created_at` steht auf `clock_timestamp()` (Migration 026)
- Umsetzungsstand nachgezogen; offen bleibt Promote (Schritt 3), die Rolle fürs Zurückziehen in direkten Scopes ist CHY-170

**Änderungen v2.32 → v2.33 (2026-09-29) — CHY-65: ein Freigabeweg:**

- **ADR-042 neu:** ADR-011 wird auf dem Block-Lebenszyklus gebaut, nicht mit eigenen Merge Requests. Strenge Scopes nutzen die vorhandene Freigabe, direkte aktivieren sofort, Promote kopiert als Entwurf nach oben. `merge_requests`, `merge_request_comments`, die sieben 501-Routen und der Kontext `governance` entfallen — **vier Kontexte** statt fünf
- **ADR-005 überholt**, ADR-011 mit Nachtrag; §5.7, Umsetzungsstand, Audit-Katalog, Szenario 6.4 und Glossar nachgezogen

**Änderungen v2.31 → v2.32 (2026-09-25) — CHY-166: die Regel begrenzt die Antwortlänge:**

- **ADR-038 um einen Nachtrag ergänzt:** `max_tokens` ist Pflicht in jeder Routing-Regel, Voreinstellung und Obergrenze zugleich; ein Aufrufer darf nur weniger verlangen (sonst 422). `/v1/route` nennt die Grenze mit, der Runner reicht sie durch. Nebenbei: eine unbrauchbare Regel bei der Freigabe ist 422 statt 500

**Änderungen v2.30 → v2.31 (2026-09-24) — CHY-124: Maschinen-Clients:**

- **ADR-031 um einen zweiten Nachtrag ergänzt:** Je Oberfläche ein Maschinen-Client
  `cohereny.<oberfläche>.ci`, nur `client_credentials`, Secret nur in SOPS und CI. Die API
  akzeptiert je Oberfläche genau zwei Audiences. Verworfen wurde `client_credentials` am
  Provider der UI, weil deren Secret dort allein Tokens ohne Person dahinter prägen könnte

**Änderungen v2.29 → v2.30 (2026-09-24) — CHY-160, CHY-162: die Datenklasse:**

- **ADR-006 präzisiert (CHY-39):** Das Gate vor der Cloud ist strukturell (ADR-041), nicht je Anfrage — ein Mensch, der dieselbe Frage hundertmal am Tag bestätigt, bestätigt sie irgendwann ungelesen
- **ADR-041 neu:** Die Datenklasse gehört dem Geltungsbereich. Wirksam ist die strengste Klasse auf der Kette, unklassifiziert gilt `critical`, und jeder Kandidat einer Routing-Regel muss sie sehen dürfen — geprüft bei Freigabe und beim Auflösen
- **`verify:dev` war seit CHY-87 rot** und niemand sah es: das E2E rief `/v1/complete` ohne Routing-Regel auf. Es läuft nach dem Merge, wo keiner hinsieht (CHY-162)

**Änderungen v2.28 → v2.29 (2026-09-24) — CHY-145, CHY-157: der Runner routet:**

- **§5.6 `llm_call` korrigiert.** `classification` und `model_assignment` standen im Modell und existierten nirgends — dieselbe Abweichung wie `context_requirements` vor CHY-134. Übrig bleibt `task_type` als Pflichtspalte (Migration 023): der Schlüssel, nach dem die Routing-Regeln aufgelöst werden. Ein Knoten nennt sein Modell nicht mehr selbst
- **ADR-040 Schritt 2 erledigt.** `config["model"]` umging Freigabe, Vererbung, Ratchet und Fallback-Kette; seit CHY-145 löst der Runner wie `/v1/complete` auf, und ein Rückfall steht im Audit
- **Ein fremder Fehler ließ Läufe hängen.** Der Runner fing nur seine eigene Fehlerfamilie; ein `ModelRefused` aus dem Modellaufruf hinterließ Lauf und Ausführung dauerhaft auf `running`. Jetzt wird jede Ausnahme festgehalten, unerwartete danach weitergeworfen
- **Erstes gehostetes Modell im Router (CHY-157):** Ollama Cloud als `public`. Damit kann die Kettenprüfung erstmals ablehnen — und es entsteht eine Lücke, die vorher unerreichbar war: nichts vergleicht die Datenklasse mit der Obergrenze des Modells (CHY-160, entschieden: die Klasse kommt vom Geltungsbereich)

**Änderungen v2.27 → v2.28 (2026-09-08) — CHY-148: gegen den Quellbaum geprüft:**

- **§8.8 Multimodalität trägt jetzt „📋 Referenz-Design".** Der Abschnitt stand im Präsens, und `vision|multimodal|transcription` findet im Quellbaum nichts — `ExecutionLog` und `TranslationRule` ebenso wenig. §6.8 nebenan war seit jeher gekennzeichnet: dieselbe Sache, einmal markiert und einmal nicht
- **Die Konvention aus §1.4 gilt, sie wird nur nicht durchgehalten** — 16 von 160 Überschriften tragen ein Zeichen. Wo ein Statuszeichen fehlt, liest sich ein Entwurf wie ein Betriebszustand
- Das Whitepaper hat die Legende in v4.13 zum ersten Mal überhaupt bekommen (94 Überschriften, null Zeichen)

**Änderungen v2.26 → v2.27 (2026-09-07) — CHY-134: `context_requirements`:**

- **ADR-040 neu:** Was einen Knoten steuert, bekommt eine Spalte. `config` ist ein JSONB ohne CHECK, aus dem fünf Schlüssel gelesen werden — drei davon scheitern still
- **§5.6 korrigiert:** `context_requirements` ist jetzt eine Spalte mit Kohärenz-CHECK und trägt `block_types`. `required_tags` und `exclude_tags` standen im Modell und filtern nirgends — als CHY-147 aufgenommen statt weiter zugesagt
- **Das Vokabular steht in SQL:** Die Blocktypen gehören dem repository-Kontext, den orchestration nicht importieren darf (ADR-030). Die Datenbank kennt beide Tabellen
- **CHECK-Verletzungen sind keine 500 mehr:** `NodeNotStorable` macht aus sieben Constraints der Tabelle einen 422 mit dem Satz, den die Regel meint

**Änderungen v2.25 → v2.26 (2026-09-07) — CHY-135: Ingress-Guardrail:**

- **ADR-039 neu:** Der Zaun ist der Mechanismus, die Musterliste ist der Alarm. Dem Modell zu sagen, es solle Anweisungen im Inhalt ignorieren, greift auf derselben Ebene an wie der Angriff
- **Nur eine Hälfte ist eine Garantie:** Aus einem Zaun auszubrechen heißt, ihn zu reproduzieren — ob die Marker-Bytes im Inhalt stehen, ist keine Heuristik. Der Ausbruch ist feststellbar, auch wenn er nicht verhindert wird
- **Phrasen statt Wörter**, weil „Anweisung" im Bankdeutsch eine Zahlungsanweisung ist. Eine Wortliste brächte allen bei, den Befund zu ignorieren — schlechter als kein Befund
- **Die Grenze ist ein Test:** ein umformulierter Angriff kommt durch, und der Test behauptet das ausdrücklich

**Änderungen v2.24 → v2.25 (2026-09-07) — CHY-87 abgeschlossen:**

- **ADR-038 neu:** Was gewählt werden *darf*, ist Infrastruktur; was gewählt *wird*, ist Governance. Dieselbe Trennung wie CHY-42 zwischen `workflow_configs` und ENV/SOPS — eine Routing-Tabelle, die ein `PUT` ändern kann, ist eine Umgehung der Freigabe
- **Ohne Regel wird nicht geroutet.** Ein unbekannter Task-Typ fiel still auf `default`; danach nennt die Spur ein Modell, und nichts unterscheidet „jemand hat das gewählt" von „niemand hat"
- **Routing hat einen Geltungsbereich** — `/v1/route` bekommt ihn als Pflichtfeld, `/v1/complete` auch auf dem Weg mit ausdrücklichen Block-Kennungen: die Komposition braucht dort keinen, die Modellwahl schon
- **Der Ratchet aus ADR-017 greift ohne neuen Code.** `ratchet.py` prüft keinen Blocktyp; sein Docstring sagte bis heute „There is no routing_rule"

**Änderungen v2.23 → v2.24 (2026-09-07) — CHY-87: die Routing-Regeln ziehen in die Datenbank:**

- **ADR-037 neu:** Nicht jeder Kontextbaustein darf in einen Prompt. `compose` nahm ohne Typfilter jeden aktiven Block im Geltungsbereich — richtig, solange jeder Typ Prompt-Material war, und ein Leck, sobald einer es nicht ist
- **Migration 021** nimmt `routing_rule` in `context_blocks` auf. Damit greifen Versionen, Freigabepfad, Privacy-Ratchet und Scope-Vererbung ohne eine zweite Umsetzung: ein tieferer Geltungsbereich darf verschärfen und nicht lockern, und das fällt aus ADR-017 heraus
- **Positivliste, nicht Ausschlussliste** — ein siebter Typ ist morgen nicht komponierbar, bis jemand etwas anderes sagt

**Änderungen v2.22 → v2.23 (2026-09-07) — Rückfluss aus dem Buch:**

- **ADR-036 neu:** Nachweispflichtig ist, was in den **Prompt** gelangt — nicht, was gefunden wurde. Der Vektorspeicher bleibt Non-Goal; die alte Zeile sagte etwas über die *Speicherung* und wurde als Aussage über das *Verfahren* gelesen
- **Der Schwerpunkt liegt auf der Augmentierung, nicht auf dem Retrieval.** Art. 26 Abs. 4 spricht von **Eingabedaten** — das ist der fertige Prompt. Ob sie „ausreichend repräsentativ" sind, entscheidet der Schritt, der zwanzig Treffer auf vier kürzt, weil das Fenster voll ist. Ein Nachweis, der bei der Treffermenge endet, belegt eine Repräsentativität, die der Prompt nicht mehr hat
- **Die Kürzung ist stumm** und selbst dem Retriever unbekannt. Deshalb wird jeder Bestandteil, der den Zusammenbau nicht überlebt, mit seinem Grund festgehalten — den fertigen Prompt mitzuspeichern beantwortet „was stand drin", nicht „was fehlt und warum"
- **Art. 10 greift ausgerechnet nicht** — ein Retrieval-Korpus ist weder Trainings- noch Validierungs- noch Testdatensatz. Der Bestand, der die Antwort im Betrieb bestimmt, fällt aus dem Wortlaut und kommt nur über Art. 26 Abs. 4 beim Betreiber wieder herein
- **`block_versions` war schon die halbe Antwort:** Cohereny weist für die eigenen Bausteine längst nach, welche Fassung in den Prompt ging (ADR-010). Zugeliefertes Material war die einzige Quelle ohne Herkunftsangabe
- **„Ratsche" war zurück:** neun Fundstellen in arc42, beide Schreibweisen nebeneinander. Bereinigt bis auf die Stellen, die den falschen Begriff **zitieren**, um ihn zu verbieten
- **§6.6-Hinweis** zum Referenzdesign ergänzt (siehe v2.22)

**Änderungen v2.21 → v2.22 (2026-09-07) — CHY-87: die Modellwahl ist eine Kandidatenliste:**

- **ADR-035 neu:** Cohereny wählt die Fähigkeit, LiteLLM verteilt die Last. Zwei Schichten, die dasselbe entscheiden, tun es mit unterschiedlichem Wissen — nur Cohereny kennt die Datenschutzstufe
- **Die Fallback-Regel läuft anders herum als die naheliegende Lesart.** Alle Kandidaten gehen an dasselbe Gateway: ein *stummes* Gateway macht die Kette nutzlos, ein *antwortendes* mit 5xx ist genau ihr Fall. „Bei Unerreichbarkeit weiterschalten" hätte den 13.08. nicht verhindert
- **§6.6 trug einen Audit-Eintrag `routing_decision`, den es nicht gab.** Umgesetzt ist `routing.fell_back`, und **nur beim Fallback** — das Referenzdesign nimmt einen Eintrag je Knoten an, die Umsetzung einen je Ausnahme. Der Unterschied steht jetzt am Diagramm, statt als stille Abweichung
- **Das Privacy-Ratchet misst gegen den ersten Kandidaten**, nicht gegen den vorherigen — paarweise lehnt sie eine zulässige Kette ab

**Änderungen v2.20 → v2.21 (2026-09-04) — CHY-133: der Pflichtkern läuft:**

- **ADR-034 neu:** Was der Validator verlangt, muss das Deployment ausführen können. `context_compose` hatte keinen Executor — jeder gültige Workflow wäre am ersten Knoten gescheitert
- **Der Fehler war strukturell:** Was ein Workflow enthalten *muss*, steht im Validator; was dieses Deployment ausführen *kann*, in der Kompositionswurzel. Beide für sich vollständig, **nichts verglich sie**
- **ADR-033 ergänzt um `workflow_id`** — `node_ref` ist nur je Workflow eindeutig. Ohne die Id teilen sich zwei gleichnamige Knoten einen Cache-Eintrag, und es sieht aus wie ein Treffer

**Änderungen v2.19 → v2.20 (2026-09-04) — CHY-127/129/130/131: der Runner bekommt einen Träger:**

- **ADR-033 neu:** Ein Knoten sieht die Ausgabe seiner unmittelbaren Vorgänger, nach Herkunft getrennt. Bis dahin trug `NodeContext.input_data` die Laufeingabe für **jeden** Knoten, und `node_executions.output_data` wurde geschrieben und von niemandem gelesen — der Pflichtkern von ADR-004 war nicht verbunden
- **Der Fehler machte nichts kaputt.** Läufe liefen durch, jeder Knoten meldete Erfolg, die Ablaufverfolgung war lückenlos; nur die Arbeit passierte nicht. Dasselbe Muster wie bei Sicherungsjobs, die Erfolg melden und nichts tun, und bei ADR-032
- **`NodeContext` bekam vier Felder nie übergeben** (`config`, `input_data`, `scope_id`, `actor_id`). Alle vier haben Vorgabewerte, deshalb war es keine Ausnahme, sondern eine leere Konfiguration — der `llm_call`-Executor aus CHY-129 hätte nie ein Modell gefunden
- **CHY-130: Kantenbedingungen sind Daten**, kein Ausdruck (Migration 020, `condition` TEXT → JSONB). Die Pfadauflösung teilen sich Bedingungen und Eingabe-Mappings seit CHY-131 — zwei Auflösungen von „was bedeutet `a.b.c`" driften
- **CHY-129: Der Modellaufruf liegt nicht mehr hinter HTTP** (`core/completion.py`). `base_url` und `api_key` sind **Parameter**, keine `api.config`-Zugriffe, und ein Test hält das offen
- **Migration 019:** `node_executions.consumed_edges`. Ein Token ist eine Differenz — `traversed − consumed`; 018 speicherte nur die eine Hälfte

**Änderungen v2.18 → v2.19 (2026-09-04) — CHY-82: Schema, Validator, und was die Bilder verschwiegen:**

- **§5.1 Bausteinsicht:** Der **Workflow-Runner** steht jetzt im Kern. ADR-028 hat ihn am 11.08. beschlossen, und die Bausteinsicht kannte ihn drei Wochen lang nicht — genauso wenig wie das Backlog (jetzt CHY-127)
- **Die LangGraph-Box heißt „Fremde Engine".** Sie stand immer schon richtig im *Client Layer*: ADR-028 nimmt LangGraph aus dem **Kern**, §8.2.2 verlangt Einsetzbarkeit *in* einer fremden Engine. Der neue Name macht aus einer Abhängigkeit ein Beispiel
- **§5.4b neu:** Whitebox Workflow-Runner — Verantwortung, Schnittstellen, Zustandstabellen, und warum `human_review` ein Wartezustand der Status-Maschine ist und kein blockierter Thread
- **§5.3 Konfliktstrategie korrigiert.** Dort stand „`force_override: true` gewinnt **immer**" — überholt durch ADR-017 und seit CHY-62 auch im Code falsch. ADR-017 nennt genau diesen Satz als Anlass seiner Entscheidung; er stand hier trotzdem unverändert weiter
- **§5.6 Implementierungsstand:** Migration **018** statt der zweimal veralteten Nummer, Validator ergänzt, fehlender Runner benannt

**Änderungen v2.17 → v2.18 (2026-09-04) — CHY-42: Workflow-Konfiguration als Daten:**

- **ADR-032 neu:** Ein Nachweis enthält nur, was gewirkt hat. `effective_config` führt `model_map` und **nicht** `params`, weil die Piloten das zweite nicht lesen (ADR-028 Punkt 4). Ein Nachweis, der einen ungelesenen Wert nennt, ist nicht unvollständig, sondern falsch
- **Ein Override auf eine unwirksame Sektion wird abgelehnt, nicht ignoriert** — sonst meldet die API Erfolg, ohne hingesehen zu haben
- `workflow_configs` trägt nur **Tuning**. Infrastruktur (LiteLLM-URL, Langfuse-Schlüssel, `POSTGRES_URI`) bleibt in ENV/SOPS: eine Basis-URL, die ein `PUT` ändern kann, ist ein SSRF-Vektor
- Endpunkte unter `/v1/pilots/configs`, nicht `/v1/pilots/workflows` — `/v1/workflows` ist bereits die Workflow-**Definition** (CHY-82)

**Änderungen v2.16 → v2.17 (2026-09-03) — CHY-62: Privacy-Ratchet umgesetzt:**

- **ADR-017 fortgeschrieben.** Das Feld sitzt am Block, nicht an `routing_rule.action` — letzteres existiert nicht. Es wird **erklärt statt abgeleitet**, weil die Komposition technisch nichts überschreibt: `as_prompt` fügt alle Blöcke aneinander, und ein Widerspruch zwischen zwei Prosatexten ist maschinell nicht feststellbar
- **`governance_key` ist ein Fremdschlüssel**, kein Freitext. Ein Tippfehler höbe den Schutz sonst lautlos auf
- **Zwei Durchsetzungspunkte:** bei der Freigabe (dort ist die Korrektur billig) und bei der Komposition (Auffangnetz — mehrere Versionen können gleichzeitig `active` sein)
- **`locked` ist stärker als das Ratchet:** kein tieferer Scope darf den Schlüssel nennen, auch nicht strenger. Zwei Quellen für eine Wahrheit laufen auseinander
- Der **Governance-MR** ist bis CHY-65 der bestehende Review-Workflow: Governance ist nur im `draft` änderbar, und die Freigabe verlangt die Rolle im Scope des Blocks

**Änderungen v2.15 → v2.16 (2026-08-27) — CHY-121, CHY-96: M3b abgeschlossen:**

- **Kriterium 3 aus ADR-030 ist erfüllt.** `api_db_calls` **43 → 0** über fünf
  Schnitte; SQL liegt ausschließlich in `contexts/*/store.py` und `kernel/`.
  Als Test festgehalten, nicht nur als Zahl — ein Ratchet, das null erreicht,
  sollte eine Wand werden
- **`domain/` ist aufgelöst** (ADR-030 Punkt 1), das leere Paket mit entfernt
- **Der Scope-Baum gehört dem Kernel.** Die JOIN gegen `scope_nodes` im
  Repository-Kontext ist nicht umgezogen, sondern **weggefallen**: `scope_path`
  und `depth` stehen in der Ahnenkette, die der Aufrufer ohnehin lädt
- **Konfiguration wird in Konfigurationsmodulen gelesen** (CHY-96). Die
  Prozessumgebung wird aus keinem Anfragepfad mehr mutiert
- **Neuer Abschnitt „Was M3b tatsächlich verändert hat"** mit den gemessenen
  Werten und den drei Kriterien, die noch offen sind

**Änderungen v2.14 → v2.15 (2026-08-27) — CHY-123:**

- **Der Kernel hat jetzt eine Verwaltungsoberfläche.** `anonymize_actor`,
  `set_role`, `read_entries` — drei Transportmodule hielten sie vorher selbst,
  darunter `cli/main.py` mit einer dritten Kopie der Rollennamen
- **Eine Grenze, die im Transport bleibt, und warum:** Wer welchen Log-Eintrag
  lesen darf, hängt von der Autorschaft eines Blocks ab. Die gehört
  `repository`, der Kernel benutzt niemanden — also gehört die Zusammensetzung
  in die einzige Schicht, die beides sieht. Steht als Absatz in §5.7, damit es
  nicht beim nächsten Durchsehen als Rest gelesen wird
- **`api_db_calls` 12 → 8.** Der Rest liegt vollständig in `api/v1/scopes.py`;
  Kriterium 3 ist mit CHY-121 erreichbar

**Änderungen v2.13 → v2.14 (2026-08-27) — CHY-106…108, CHY-122:**

- **Alle fünf Kontexte existieren.** Zwei tragen Code, drei sind Gerüste — und
  der Unterschied gehört benannt: `governance`, `orchestration` und `learning`
  hatten *nichts* zu extrahieren. Ihre Tabellen gibt es seit Migration 001, ihre
  25 Routen antworten alle 501, kein Code fasst sie an
- **`domain/` ist bis auf `actor.py` und `scope.py` leer** — beide kernel-eigen
- **`AuditLogEntry` liegt jetzt im Kernel.** Es beschreibt `audit_log` und lag
  unter `governance` nur, weil MR-Freigaben das Erste waren, das jemand
  auditiert hat
- **Kriterium 3 ist in Sichtweite:** In `api/` bleiben 12 SQL-Anweisungen, alle
  in `scopes.py`, `admin.py` und `audit.py` — alle drei gegen kernel-eigene
  Tabellen, erfasst als CHY-121 und CHY-123

**Änderungen v2.12 → v2.13 (2026-08-27) — CHY-105:**

- **Zweiter Kontext: `repository`.** `api/v1/blocks.py` 621 → 202 Zeilen. Der
  Freigabe-Lebenszyklus war ausschließlich über HTTP erreichbar — die zentrale
  Tatsache dieses Produkts hatte genau eine Tür. ADR-030 Punkt 4 hing daran
- **Die Zustandsmaschine ist jetzt eine Tabelle** und dadurch erstmals *gegen
  Migration 013 prüfbar*. Vorher steckte sie als Argumente in vier Endpunkten
- **Kriterium 6 für `context_blocks` erreicht:** vier Dateien mit SQL auf eine
- **Neuer Abschnitt „Wenn die Kennzahl mitschrumpft"** unter ADR-030: Zwei
  Quelllisten hatten `kernel/` und `contexts/` nie aufgenommen, weshalb jeder
  Schnitt seine Anweisungen aus dem Nenner nahm — ein Umbau verbesserte die
  Zahl, indem er weniger messbar machte

**Änderungen v2.11 → v2.12 (2026-08-27) — CHY-104:**

- **§5.7 „Aufbau eines Kontexts" ist nicht mehr Absicht.** `contexts/pilots/` existiert; der
  Abschnitt beschreibt jetzt, was drinsteht, samt der beiden Module, die die Vorlage nicht
  nennt (`registry.py`, `execution.py`) und der Regel, warum sie trotzdem erlaubt sind
- **Neuer Unterabschnitt „Der erste Schnitt: `pilots`"** mit den gemessenen Zahlen:
  846 → 209 Zeilen, C90 3 → 0, Abdeckung 54 % → 69 %, 57 Regeltests ohne Datenbank.
  Und dem Punkt, der in keiner Zahl steht: Der Art.-14-Aufsichtsentscheid ist ohne HTTP
  auslösbar — vorher führte genau ein Weg dorthin, und der brauchte einen HTTP-Client
- **Die Kriterientabelle in ADR-030 hat eine gemessene Spalte „27.08."** statt einer Spalte
  „Heute", die vom 16.08. stammte. Vier von sechs Kriterien sind erfüllt; bei 3 und 6 steht,
  was noch offen ist
- **„Vertragstest" → „Contract-Test"** und „Vertrag" → „Contract", wo die OpenAPI-Spec
  gemeint ist. Lehnübersetzung wie „Ratsche". Juristisches „Vertrag" in den Compliance-Unterlagen
  bleibt — neun Fundstellen, die ein pauschales Ersetzen falsch gemacht hätte

**Änderungen v2.10 → v2.11 (2026-08-27) — CHY-120:**

- **§5.7 Kernel neu gefasst.** Der Abschnitt beschrieb eine Aufteilung, die CHY-101 nicht
  umgesetzt hat: `access.py` sollte SQL-frei sein und hatte zehn SQL-Anweisungen, `identity.py`
  sollte den Lebenszyklus tragen und enthielt nur ein Protokoll. **Der Code folgt jetzt dem
  Dokument**, nicht umgekehrt — die Begründung darin war richtig: eine Rollenprüfung ohne
  Datenbank ist prüfbar, eine mit nicht
- **Ein Selbstwiderspruch aufgelöst:** die Tabelle verortete `role_from_groups` in `access.py`,
  der Fließtext zwei Absätze weiter in `identity.py`. Der Schnitt verläuft jetzt zwischen
  *wer* (Identität, mit SQL) und *darf* (Erlaubnis, ohne)
- **Neu beschrieben:** das `Caller`-Protokoll, `kernel/errors.py` samt Zuordnung auf
  Statuscodes, und `kernel/surfaces.py` in der Zonentabelle
- **„Die Grenze wird erzwungen" auf den Ist-Stand:** es ist `scripts/architecture.py` mit
  sechs Regeln, nicht `quality_gate.py` im Futur. Festgehalten, dass der Wächter `api/` bis
  CHY-101 gar nicht gelesen hat — die Regel dafür stand da und konnte nicht greifen
- **Die Messtabelle in ADR-030 ist als Befund vom 16.08. gekennzeichnet**, mit dem heutigen
  Stand daneben. Zwei Zeilen darin sind schlechter geworden, und das steht auch so da
- **„Grenzverletzung" → „Zonenverstoß"** — Lehnübersetzung von *boundary violation*, und
  ADR-030 spricht ohnehin von Zonen

**Änderungen v2.9 → v2.10 (2026-08-21) — CHY-86, CHY-109:**

- **ADR-031 mit einem Nachtrag aus der Umsetzung versehen:** Drei Punkte des Beschlusses
  haben nicht gehalten — Tags können die Zuordnung Route → Oberfläche nicht tragen (`governance`
  trägt zwei), der Beschluss hatte drei von dreizehn Routern übersehen (`pilots`, `questions`,
  `feedback` — ohne Ergänzung wären 40 von 44 Pfaden ungeschützt geblieben), und das Vokabular
  musste nach `domain/surfaces.py` wandern, um einen Importzyklus aufzulösen. Neu entstanden:
  vier beschnittene Contracts unter `contracts/`
- **§5.7 ergänzt:** Die Scope-API existiert (CHY-86). Bis dahin ließ sich eine Scope-Hierarchie
  nur per SQL anlegen — das System konnte über seine eigene API nicht in Betrieb genommen werden

**Änderungen v2.7 → v2.8 (2026-08-13) — CHY-24, CHY-28:**

- **ADR-010 fortgeschrieben:** Der Write-Through-`BackgroundTask` entfällt. Er ist mit den Generationszählern aus der Fortschreibung v2.0 unvereinbar — jede Freigabe macht sämtliche vorberechneten Schlüssel unerreichbar, und der Schlüsselraum enthält `model_id`, das unter `auto_route` erst zur Anfragezeit feststeht
- **ADR-029 neu:** Die OpenAPI-Spezifikation entsteht aus dem Code; `openapi.yaml` ist eine durch CI erzwungene Momentaufnahme. Kehrt die Richtung von CHY-28 (Contract-First) um, nachdem dessen Prämisse abgelaufen ist. Auslöser war der Befund, dass 29 von 54 Operationen HTTP 200 mit `{"status": "not_implemented"}` beantworteten

**Änderungen v2.6 → v2.7 (2026-08-11) — CHY-81:**

- **§5.6 Domäne 2 neu gefasst:** Der Workflow ist ein **gerichteter Graph**, nicht länger ein DAG. Die Azyklizität war weder begründet (kein ADR) noch implementiert — und konnte den seit 2026-06 laufenden Study-Pilot mit seinen zwei Rückschleifen nicht abbilden
- **Knotentypen an die Implementierung angeglichen:** `context_compose | llm_call | tool_call | fork_gateway | join_gateway | human_review` statt der abweichenden Doku-Fassung `start | end | task | branch_gateway | join_gateway`. `human_review` war seit `001_initial` im Schema, in der Doku aber kein Knotentyp
- **`sub_workflow` neu:** Knotentyp mit gepinnter Zielversion und Feldabbildung. Trägt die Einbindung als Schritt in fremde Workflow-Engines (Camunda, SAP), ohne die Freigabepflicht an deren Modellierung abzugeben
- **`loop_limit` neu** an `WorkflowConnection`; `human_review` um `review_role`, `timeout`, `on_timeout` ergänzt
- **Fünf Diagramme neu:** Knotentypen mit Pflichtkern, Parallelisierung mit Join, Schleife mit Grenze, Sub-Workflow-Aufruf, Validator-Regeln
- **ADR-026 neu:** Zyklen erlaubt, Iterationsgrenze verpflichtend, vom Validator erzwungen
- **ADR-027 neu:** Serialisierung — JSON nativ, veröffentlichtes JSON Schema als Prüfartefakt, XML nur an der BPMN-Grenze. Abwägung gegen den XML/XSD/XSLT-Vorschlag
- **ADR-028 neu (entschieden):** Eigener Workflow-Runner statt LangGraph im Kern. Grundlage ist der Codebefund, dass `interrupt` nirgends verwendet wird, der Freigabe-Wartezustand auf einem prozesslokalen `threading.Event` hängt und die geschriebenen Checkpoints **nie gelesen** werden. Ausschlaggebend sind aber vier Modellgründe: `loop_limit` je Kante, versionsgepinnte Sub-Workflows, Definitionen aus der Datenbank, eigener Audit-Trail

**Änderungen v2.5 → v2.6 (2026-08-11):**

- **ADR-025 neu:** Actor-Beziehungen — typsichere Tabellen statt polymorpher `ActorReference`. Elf bestehende Fremdschlüssel auf `actors(id)` folgen drei Mustern; eine generische Zwischentabelle mit `entity_type`/`entity_id` ließe sich nicht per Fremdschlüssel absichern. Mehrfach-Freigaben werden als Schritte der Status-Maschine modelliert, die Historie liegt im Audit-Log. Verkleinert CHY-34, betrifft CHY-31/32/65
- **ADR-008 präzisiert:** Die Begründung der Unterstrich-Konvention („ltree-Labels sind nur `[A-Za-z0-9_]`") gilt seit PostgreSQL 16 nicht mehr — Bindestriche parsen dort. Die Konvention bleibt, wird jetzt aber in der Anwendung durchgesetzt statt von der Datenbank (CHY-59)

**Änderungen v2.4 → v2.5 (2026-08-01):**
- **§8.9 neu:** AdaptVault als Querschnittskonzept — versioniertes Adaption-Repository (RevisionedEntity), Benchmark-Schleife, Nightly Job, HITL, DPO-Flywheel
- **ADR-024 neu:** AdaptVault — Automatische Prompt-Adaption via Benchmark-DB + DPO (CHY-67–70)

**Änderungen v2.3 → v2.4 (2026-07-31):**
- **Infra-Migration:** Stack vom Raspberry Pi 5 auf einen eigenen Application-Server umgezogen; Compute-Host umbenannt; alle Adressen und das Mermaid-Diagramm aktualisiert
- **DNS:** interne Namen auf eine neue interne Domain umgestellt (alle Referenzen)
- **CI/CD:** Gitea Actions → GitLab CI
- **Lizenz:** MIT (geplant) → GPLv3 + Commercial License (CHY-55 ✅)
- **Domain:** cohereny.cloud → cohereny.com
- **Logo:** cohereny-brand SVG eingebunden
- **ADR-023 neu:** Abgrenzung zu Databricks AI Gateway / Mosaic AI (CHY-56)

**Änderungen v2.2 → v2.3 (Peer Review 2026-07-12):**

- **§1.1:** "Middleware-Schicht" → "Context Middleware" als kanonischer Begriff
- **ADR-006:** L3-Cloud-Pfad um bidirektionalen Human-Checkpoint erweitert — PRE (Privacy Gate) + POST (Quality Gate + Korrektur-Freitext → ADR-021)
- **ADR-020:** GIN-Index konkretisiert — `CREATE INDEX ON translation_rules USING GIN ((content->>'target_model_id'))` als zwingend, nicht optional

**Änderungen v2.1 → v2.2 (CTX-39):**

- **Pilot umbenannt:** jetzt „Study" — alle Abschnitte (§1.4, §6.5, §8.3) aktualisiert
- **§7.1:** Veraltete Pi-5-RAM-Warnung entfernt (Raspberry Pi 5 = 16 GB bestätigt; Stack im Umzug auf den Application-Server)
- **ADR-008:** ltree-Encoding konkretisiert — `org_uuid.team_uuid`, `node_type` separat; Technische Schuld als erledigt markiert
- **ADR-018:** Phase-2-NER präzisiert — spaCy/GliNER on-premise als Pflicht vor Kompositions-Engine

**Änderungen v2.0 → v2.1:**

- **Kapitel 1.1:** Problem 6 (Modalitäts-Isolation) ergänzt; "sechs Probleme" in der Lösungsbeschreibung
- **Kapitel 6.8:** Neues Laufzeitszenario — kamerabasiertes Aktivitäts-Monitoring (Referenz-Design)
- **Kapitel 8.8:** Multimodalität als Architekturprinzip (neues querschnittliches Konzept)
- **Kapitel 10.1:** Qualitätsbaum um "Erweiterbarkeit" erweitert
- **Kapitel 10.2:** Q9 Multimodale Erweiterbarkeit hinzugefügt
- **Inhaltsverzeichnis:** Abschnitt 8.8 ergänzt

**Änderungen v1.1 → v2.0 (Major):**

- **Ist/Soll-Trennung:** Neuer Abschnitt 1.4 Implementierungsstand; Status-Marker (✅ live / 🔧 teilweise / 📋 geplant) in Bausteinsicht, API-Übersicht und Laufzeitszenarien — die Ziel-Architektur ist jetzt klar vom implementierten Stand unterschieden.
- **Regulatorik-Update (Faktencheck 07/2026):** Digital-Omnibus-Fristen (Anhang III → 02.12.2027, Anhang I → 02.08.2028, Art. 50 unverändert 02.08.2026); TransparencyRecord auf Art. 50 korrigiert; MaRisk-9.-Novelle/BAIT-Staffelung; BaFin-Orientierungshilfe KI (18.12.2025) aufgenommen; Grundschutz++; C5:2026; CRA-Meldepflichten 11.09.2026; Art.-25-Wertschöpfungskettenrisiko.
- **DSGVO-Konzept (8.3) erweitert:** dritte Datenkategorie „Payload-Daten" (Prompts/Outputs/Traces), PII-Redaction-Baustein, Retention.
- **Caching-Konzept (8.5)** an ADR-010 angeglichen; ADR-010 auf Generation-Counter-Invalidierung fortgeschrieben.
- **ADRs:** ADR-002 superseded; ADR-009 um SECURITY-DEFINER-Mechanismus ergänzt; ADR-013 Status aktualisiert; Entscheidungsvorschläge für ADR-014/015/016; **neu:** ADR-017 Governance-Ratchet, ADR-018 PII-Redaction, ADR-019 A/B-Benchmark (adaptation_gain), ADR-020 Model-Sunset-Workflow.
- **Konsistenz:** Banking-Szenario auf DORA Art. 9–11 / MaRisk AT 4.3.5 umgestellt (BAIT/AT 7.2 entfernt); Technische-Schulden-Tabelle bereinigt; Glossar (Scope-Hierarchie) aktualisiert; Begriffstrennung Study Workflow (Studienhefte) vs. Arztbrief-Szenario (Patientendaten).

---

## Inhaltsverzeichnis

1. [Einführung und Ziele](#1-einführung-und-ziele)
2. [Randbedingungen](#2-randbedingungen)
3. [Kontextabgrenzung](#3-kontextabgrenzung)
4. [Lösungsstrategie](#4-lösungsstrategie)
5. [Bausteinsicht](#5-bausteinsicht)
6. [Laufzeitsicht](#6-laufzeitsicht)
7. [Verteilungssicht](#7-verteilungssicht)
8. [Querschnittliche Konzepte](#8-querschnittliche-konzepte)
   - 8.7 [Regulatorische Einordnung (Übersicht)](#87-regulatorische-einordnung)
   - 8.8 [Multimodalität als Architekturprinzip](#88-multimodalität-als-architekturprinzip--referenz-design)
9. [Architekturentscheidungen](#9-architekturentscheidungen)
10. [Qualitätsanforderungen](#10-qualitätsanforderungen)
11. [Risiken und technische Schulden](#11-risiken-und-technische-schulden)
   - 11.3 [Compliance-Risiken](#113-compliance-risiken)
12. [Glossar](#12-glossar)
13. [Anhang — Vollständige Regulatorische Einordnung](#13-anhang--vollständige-regulatorische-einordnung)

---

## 1. Einführung und Ziele

### 1.1 Aufgabenstellung

Cohereny ist eine selbst-gehostete **Context Middleware** für Multi-LLM-Workflows — headless by default, mit optionaler Referenz-UI (eigenes Repository; Veröffentlichung → M8, CHYUI-11). Sie adressiert systematisch sechs miteinander verbundene Probleme — darunter drei grundlegende LLM-Qualitätsphänomene:

**Problem 1 — Modell-Varianz (Modell-Differenz):** Verschiedene Modelle verhalten sich unterschiedlich — das ist normal und gewollt (verschiedene Architekturen, verschiedene "Persönlichkeiten"). Für Multi-LLM-Workflows bedeutet das: ein Prompt muss modell-spezifisch formuliert werden, damit jedes Modell im Workflow optimal arbeitet.

**Problem 2 — Model Drift (LLM Drift):** Dasselbe Modell verhält sich nach einem Provider-Update, Patch oder Fine-Tuning anders als zuvor — ungewollt aus Nutzersicht. Bestehende Übersetzungsregeln und Kalibrierungs-Ankerpunkte können dadurch ungültig werden.

**Problem 3 — Prompt Sensitivity:** Minimale Formulierungsänderungen im Wortlaut eines Prompts führen zu anderen Ergebnissen — beim gleichen Modell, gleicher Version. Diese Eigenschaft ist inhärent, aber durch kuratierte Formulierungsbausteine systematisch managebar.

**Problem 4 — Agentic Context Explosion:** In Workflows mit mehreren Schritten und mehreren Modellen multiplizieren sich alle drei Phänomene. Jeder Step braucht einen anderen, modell-optimierten Kontext. Ohne Werkzeug führt das zu Prompt-Varianten-Proliferation.

**Problem 5 — Wissens-Isolation:** Gewonnenes Wissen über "welcher Prompt funktioniert bei welchem Modell" bleibt bei einzelnen Personen und wird nicht organisatorisch geteilt.

**Problem 6 — Modalitäts-Isolation:** Bestehende LLM-Middleware-Tools sind auf Text-Prompts ausgelegt. Multimodale Workflows (Vision, Audio, kombiniert) erfordern separate Infrastruktur, separate Prompt-Strategien und separate Governance — obwohl die Kernprobleme (Modell-Varianz, Drift, Sensitivity) für alle Modalitäten gleich sind. Cohereny soll alle Modalitäten durch dieselbe Adaptionsschicht adressieren (📋, §8.8; nach 1.0, CHY-206).

**Lösung:** Ein trainierbares Kontext-Repository mit Scope-Hierarchie (Global → Team → Projekt), das zur Laufzeit step-spezifische, modell-optimierte Kontexte komponiert — mit Drift-Erkennung (→ M6), Governance, Revisionssicherheit und einer auf die DSGVO ausgelegten Datenhaltung (Löschweg nach Art. 17, Aufbewahrungsfristen und Platzhalter vor jedem Modellaufruf von `/v1/complete` und in Workflows gebaut). Cohereny adressiert systematisch alle sechs Probleme.

### 1.2 Qualitätsziele

Die folgende Tabelle zeigt die Top-5 Qualitätsziele (sortiert nach Priorität):

| Prio | Qualitätsziel                | Szenario                                                                                   |
| ---- | ----------------------------- | ------------------------------------------------------------------------------------------ |
| 1    | **Korrektheit**         | Kontext-Adaption verbessert Modell-Output messbar gegenüber unadaptiertem Prompt          |
| 2    | **Datenschutz**         | Was das eigene Netz nicht verlassen darf, geht an kein Cloud-Modell (Privacy-by-Design)               |
| 3    | **Wartbarkeit**         | Neue Modell-Profile und Übersetzungsregeln können ohne Code-Änderung eingebracht werden |
| 4    | **Nachvollziehbarkeit** | Jede Kontext-Entscheidung ist auditierbar und versioniert                                  |
| 5    | **Erweiterbarkeit**     | Neue LLM-Provider und Workflow-Typen können ohne Kern-Änderungen integriert werden       |

### 1.3 Stakeholder

| Rolle                                        | Erwartung                                                            |
| -------------------------------------------- | -------------------------------------------------------------------- |
| **Thomas Roth (Betreiber/Entwickler)** | Saubere, wartungsarme Architektur; Self-hosting; kein Vendor Lock-in |
| **Contributor (Community)**            | Klare API; gute Dokumentation; Review-Workflow für eigene Beiträge |
| **Team-Mitglieder**                    | Geteilter Kontext; einfache Nutzung ohne Prompt-Expertise            |
| **Reviewer**                           | Übersichtliche Review-Queue; nachvollziehbare Diffs                 |
| **Admin**                              | Volle Kontrolle über Konfiguration und Governance                   |

### 1.4 Implementierungsstand (Stand 2026-11-08)

> **Legende (gilt dokumentweit):** ✅ implementiert · 🔧 teilweise (Skeleton/Stub vorhanden) · 📋 geplant (Ziel-Architektur) · **→ M5** Meilenstein auf dem Weg zu 1.0, in dem das Fehlende kommt (Übersicht: Whitepaper §10 „Weg zu 1.0“). Cohereny ist ein Pre-Release; das Zeichen zeigt immer den heutigen Stand.

Diese Übersicht folgt der Tabelle „What is built“ in der README; die Zeilen zu den Pilot-Endpunkten und zum Tracing kommen hinzu.

| Baustein | Status | Beleg |
|---|---|---|
| Kontext-Repository: versionierte Blöcke, Prüf-Lebenszyklus, Freigabe je Geltungsbereich (streng, direkt, Promote) | ✅ | ADR-011, ADR-042, CHY-65 |
| Komposition entlang des Geltungsbereichsbaums mit Privacy-Ratchet und Cache | ✅ | ADR-008/009/010, ADR-017, CHY-62 |
| Routing-Regeln als geprüfte Blöcke; Datenklassen je Geltungsbereich, Obergrenzen je Modell | ✅ | ADR-037, ADR-041 |
| Vier Augen, wo Daten den Ort wechseln: Eine Routing-Regel mit einem Modell unterhalb `critical` und eine gelockerte Datenschutzregel brauchen in jedem Geltungsbereich eine zweite Person; technische Konten geben sie nie frei | ✅ | ADR-045, CHY-163 |
| Aufbewahrung: jede Tabelle einer Aufbewahrungsklasse zugeordnet; wie lange Inhalte und Spur eines Laufs bleiben, steht je Geltungsbereich in der Admin-API, auditiert, angewandt von `cc retention purge` | ✅ | ADR-046, CHY-17 |
| Bezeichner vor jedem Modellaufruf von `/v1/complete` und in Workflows ersetzt: E-Mail-Adressen, Telefonnummern, IBANs, Steuer-IDs, Krankenversichertennummern, gekennzeichnete Geburtsdaten, jeder Wert, den der Aufrufer nennt, und die Regeln des Bereichs als geprüfte Blöcke. Modell, Gateway und Trace sehen Platzhalter. Workflow-Läufe werden nur mit Platzhaltern gespeichert, die Zuordnung je Lauf verschlüsselt; wer den Lauf gestartet hat, und ab `reviewer` wer ihn sieht, lässt die Werte auflösen, protokolliert. Namen ohne Liste → M7 (CHY-235). Die LangGraph-Piloten ersetzen nicht; sie sind abgekündigt (CHY-172) | ✅ | ADR-018, CHY-63, CHY-234 |
| Workflow-Runner: Verzweigungen, Zusammenführungen, `human_review`-Gates mit Vier-Augen-Freigabe; wer entscheidet, liest vorher die Ausgaben der Knoten | ✅ | ADR-028, CHY-127, CHY-176, CHY-229 |
| Läufe sieht nur, wer sie gestartet hat, wer Mitglied im Bereich des Workflows oder eines Bereichs darüber ist, und `admin` — durchgesetzt per Row-Level Security, eine Abfrage, die nicht sagt, für wen, sieht nichts. Blöcke folgen in M6 (CHY-237) | ✅ | ADR-047, CHY-92 |
| Append-only Audit-Log außer Reichweite der Anwendung: Die API läuft als Datenbankrolle, die keine Tabelle besitzt und keinen Trigger abschalten darf, und startet nicht mit mehr Rechten. API je Oberfläche (`serve`, `author`, `observe`, `admin`) hinter OpenID Connect | ✅ | Migrationen 011, 033, 034, ADR-009, ADR-029, ADR-031, CHY-85 |
| Pilot-Endpunkte `/v1/pilots/*` mit SSE und gespeicherten Ereignissen (`events_json`); Altbestand auf LangGraph, wird auf den Workflow-Runner gezogen | ✅ | ADR-013, ADR-028, CHY-58 |
| Langfuse-Tracing (optional) | ✅ | ADR-012 |
| Drift-Erkennung: Kalibrieranker und Messtabellen gespeichert; Läufe, Zeitplan, Läufe bei Änderungen und Signale aus dem Betrieb → M6, Messfühler → M7 | 🔧 | ADR-043, ADR-044, CHY-67/68/64 |
| Adaption von Prompt und Kontext je Modell | 📋 → M6 | CHY-204 |
| Installation mit einem Befehl (Docker Compose): lokales Modell, Entwickler-Issuer, Beispiel mit Freigabe; der Durchlauf der Anleitung läuft je MR in der CI | ✅ | CHY-189 |

Die Bausteinsicht (§5) und die Laufzeitsicht (§6) beschreiben die **Ziel-Architektur**; implementierte Teile sind markiert. Diese Trennung ist bewusst: Das Dokument dient zugleich als Referenzarchitektur — für das Whitepaper und für das Buch „Architekturmuster für Enterprise-KI“ (Carl Hanser, erscheint 2027) — und als Bautagebuch. Was bis 1.0 fehlt, steht je Meilenstein im Whitepaper §10.

---

## 2. Randbedingungen

### 2.1 Technische Randbedingungen

| Randbedingung                             | Begründung                                                        |
| ----------------------------------------- | ------------------------------------------------------------------ |
| Self-hosted, Docker-basiert               | Datenschutz, Kontrolle, kein Cloud-Vendor-Lock-in                  |
| API-First / Headless Core                 | Keine Geschäftslogik exklusiv im UI; Testbarkeit; Erweiterbarkeit |
| PostgreSQL als primäre Datenbank         | Revisionssicherheit, Transaktionen, gut verstanden                 |
| OIDC für Authentifizierung               | Kein eigenes User-Management; getestet mit Authentik, dessen Aufbau von Issuer und Schlüsseln der Code erwartet (eine Anwendung je Oberfläche, ADR-031) |
| LiteLLM als Model-Gateway                 | Abstrahiert unterschiedliche LLM-Provider hinter einer API         |
| Python (FastAPI) als Backend-Sprache      | Ökosystem-Fit für LLM-Tooling; Pydantic für Datenmodelle        |
| Keine proprietären Cloud-Dienste im Kern | Community-Betreibbarkeit                                           |

### 2.2 Organisatorische Randbedingungen

| Randbedingung                                      | Begründung                                                             |
| -------------------------------------------------- | ----------------------------------------------------------------------- |
| AGPLv3 + Commercial License (CHY-55 ✅, AGPLv3 seit CHY-188) | Veröffentlicht unter AGPLv3 (11/2026) |
| Keine Werbung, kein Tracking                       | Philosophie: sauber, wartungsarm, privatsphäre-respektierend           |

### 2.3 Rechtliche Randbedingungen

| Randbedingung                                   | Begründung                                                                                                                                                                                                                                          |
| ----------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| DSGVO-Konformität                              | Personenbezogene Daten müssen löschbar / anonymisierbar sein — gilt auch für Payload-Daten (Prompts/Traces), siehe §8.3                                                                                                                         |
| EU AI Act beachten (i. d. F. der VO (EU) 2026/1744, Digital Omnibus) | Anhang III Hochrisiko ab 02.12.2027; Anhang I ab 02.08.2028;**Art.-50-Transparenzpflichten unverändert ab 02.08.2026** (betrifft die Referenz-UI und jede andere Oberfläche, über die Dritte mit KI-Ausgaben interagieren); Art.-25-Risiko: Gesamtsystem Cohereny+LLM ist ein KI-System |
| EU CRA: Herstellerpflichten                    | Mit der Veröffentlichung unter AGPLv3 und dem Angebot einer kommerziellen Lizenz behandelt sich Cohereny als Hersteller. Art.-14-Meldepflichten (24h/72h/14d) gelten seit 11.09.2026, die volle Anwendung ab 11.12.2027. Die Herstellerpflichten gelten ab der Veröffentlichung: `SECURITY.md` mit Meldeadresse und Richtlinie zur koordinierten Offenlegung steht; ein SBOM entsteht in jeder Pipeline (CHY-22); Meldeprozess (CHY-16), Unterstützungszeitraum (CHY-149) und Patch-SLA (CHY-12) → M7b |

---

## 3. Kontextabgrenzung

### 3.1 Fachlicher Kontext

```mermaid
graph LR
    subgraph Nutzer["Nutzer und Clients"]
        DEV[Entwickler\nLangGraph Workflow]
        USER[Team-Mitglied\nReferenz-UI]
        CLI[CLI-Nutzer\nAutomation]
    end

    subgraph CC["Cohereny"]
        CORE[Cohereny Core\nHeadless API]
    end

    subgraph Modelle["LLM-Provider"]
        LOCAL[Lokale Modelle\nOllama]
        CLOUD[Cloud-Modelle\nOllama Cloud, Claude vorgesehen]
    end

    subgraph Identity["Identity"]
        IDP[OIDC Provider\nAuthentik]
    end

    DEV -->|REST API| CORE
    USER -->|REST API via UI| CORE
    CLI -->|REST API| CORE
    CORE -->|LiteLLM| LOCAL
    CORE -->|LiteLLM| CLOUD
    IDP -->|JWT Token| CORE

    classDef cloud fill:#1a3a4f,color:#38bdf8
    class CC cloud
```

**Systemgrenzen:**

- Cohereny verwaltet Kontext und Adaption — nicht die Modelle selbst
- Modell-Verwaltung (Download, Deployment) ist Aufgabe von Ollama / LiteLLM
- Benutzer- und Gruppen-Verwaltung ist Aufgabe des OIDC-Providers
- Workflow-Logik (Sequenz der Steps) führt der eigene Workflow-Runner aus (ADR-028); ein Client kann sie ebenso in einer fremden Engine halten (z. B. LangGraph) und Cohereny nur für Komposition und Routing aufrufen

### 3.2 Technischer Kontext

Cohereny nutzt ausschließlich Services, die von der **Infrastruktur des Betreibers** bereitgestellt werden.
Die Konfiguration erfolgt über Umgebungsvariablen und eine `routing-config.yaml`, deren Pfad `ROUTING_CONFIG` nennt (ohne Angabe gilt `router/routing-config.yaml`): Dort steht, welche Modelle erreichbar sind und welche Datenklasse jedes sehen darf (ADR-038).
Vollständige Konfigurationsreferenz: [`DEPENDENCIES.md`](https://github.com/cohereny/cohereny/blob/main/DEPENDENCIES.md)

#### Abhängigkeits-Tabelle

| Nachbarsystem              | Endpunkt im Referenz-Deployment                                               | Env-Variable                                                        | Verantwortung                                                                |
| -------------------------- | ----------------------------------------------------------------- | ------------------------------------------------------------------- | ---------------------------------------------------------------------------- |
| **LiteLLM Gateway**  | Application-Server, Port 4000 | `LITELLM_BASE_URL`                                                | Model-Routing, Abstraktionsschicht zu lokalen Laufzeiten wie Ollama          |
| Compute lokal, kleine Modelle | via LiteLLM (nicht direkt)                                        | —                                                                  | phi3:mini, gemma3:1b, nomic-embed-text                                       |
| Compute lokal, mittlere Modelle | via LiteLLM (nicht direkt)                                        | —                                                                  | gemma3:4b (schnell), gemma4:e4b (Reasoning)                                  |
| **PostgreSQL**       | Application-Server, Port 5432                                          | `DATABASE_URL`                                                    | Kontext-Repository, Audit Log, Workflow-Laufzustand. LangGraph-Checkpoints nur noch aus den Piloten — werden nirgends gelesen und entfallen mit ADR-028 |
| **Authentik / OIDC** | über den Reverse Proxy (HTTPS)                                    | `OIDC_BASE_URL`                                                 | Identität, JWT-Validierung, Rollen (Gruppen `chy-*`)                            |
| **Langfuse v3**      | über den Reverse Proxy (HTTPS)                                | `LANGFUSE_HOST`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` | Tracing aller LLM-Aufrufe via OTel (SDK v4 + Server v3), Qualitäts-Baseline |
| Redis                      | Application-Server, Port 6379                                        | `REDIS_URL`                                                       | Cache für Kontext-Komposition (ADR-010)                   |
| Compute Cloud: Ollama Cloud | `ollama.com`                                                   | via LiteLLM-Config                                                  | `gemma4-cloud`, `gpt-oss-20b` — zugelassen nur für die Datenklasse `public`, kein Auftragsverarbeitungsvertrag (CHY-157, ADR-041) |
| Compute Cloud: Claude API  | `api.anthropic.com`                                             | via LiteLLM-Config                                                  | vorgesehen, noch nicht eingerichtet; zulässige Datenklasse wird beim Einrichten festgelegt (ADR-041) |

#### Deployment-Kontext

**Beispiel: die Referenzinstallation.** Die drei Stufen der API (dev, test, prod) und die Referenz-UI laufen als ein Stack auf einem Application-Server; die gemeinsam genutzten Dienste stellt der Betreiber. Stack-Definition (`compose.yaml`) und CI liegen nicht im öffentlichen Repository; veröffentlicht ist das `Dockerfile`, aus dem sich das API-Image bauen lässt. Zum Ausprobieren liegt im Repository `deploy/quickstart/`: ein Befehl, alles auf einem Rechner, mit Beispiel und Anleitungen (CHY-189).

```
[Internes Netz]

  Stack cohereny (Application-Server)
  ┌──────────────────────────────────────────────────────────┐
  │  cohereny-api-prod  :8000                                │
  │  cohereny-api-dev   :8001    je Stufe ein interner Name, │
  │  cohereny-api-test  :8002    TLS am Reverse Proxy        │
  │  cohereny-ui        :3002                                │
  │                                                          │
  │  cohereny-postgres  :5432  (cohereny_dev/test/prod)      │
  │  cohereny-redis     :6379  (DB0=prod, DB1=dev, DB2=test) │
  └──────────────────────────────────────────────────────────┘
           │
           │  LITELLM_BASE_URL → https://litellm.example.com/v1
           │  DATABASE_URL     → postgresql://cohereny:...@cohereny-postgres:5432/...
           │  REDIS_URL        → redis://cohereny-redis:6379/0
           │  OIDC_BASE_URL    → https://auth.example.com/application/o
           ▼
  Gemeinsame Dienste (Application-Server)
  ┌──────────────────────────────┐
  │  LiteLLM      :4000          │
  │  PostgreSQL   :5432          │  ← langgraph/langfuse (separate Instanz!)
  │  Langfuse     :3010          │
  │  Authentik    :9000          │
  └──────────────────────────────┘
```

**Hinweis:** cohereny-postgres ist eine **eigene** PostgreSQL-Instanz (Stack: Cohereny),
separat vom langgraph-postgres (Stack: langgraph) mit `langgraph`- und `langfuse`-DBs.

**CI/CD der Referenzinstallation** (nicht Teil des öffentlichen Repositorys):

- `deploy:dev` — Push auf dev → baut und startet die dev-Stufe der API
- `deploy:test` — Push auf test → baut und startet die test-Stufe
- `deploy:prod` — manuell → baut und startet die prod-Stufe; die UI hat dieselben drei Jobs

#### Architektur-Grenze

Cohereny **nutzt** diese Services, **verwaltet** sie aber nicht.
Infrastrukturänderungen (z. B. PostgreSQL-Migration, LiteLLM-Update) werden im Infrastruktur-Repository
der Referenzinstallation geplant, nicht in diesem Repository.

---

## 4. Lösungsstrategie

### 4.1 Kernentwurfsentscheidungen

**E1 — Headless Core:** Alle Geschäftslogik in der API, kein exklusiver UI-Pfad. Konsequenz: Referenz-UI ist lebender API-Beweis, kein bevorzugter Zugriffspfad.

**E2 — Dynamische Scope-Hierarchie statt flachem Repository (ADR-008):** Kontext ist hierarchisch über `scope_path` organisiert (org | team | workspace | project | sub_project | personal — beliebige Tiefe ohne Schema-Änderung). Tiefere Scopes erben automatisch: Die Komposition fügt die Blöcke der Vorfahren flach nach tief an, bei Routing-Regeln gilt die des tiefsten Scopes; für Privacy-Regeln gilt das Ratchet-Prinzip (nur Verschärfung, ADR-017). Konsequenz: Cold-Start-Problem lösbar durch Bootstrap-Templates auf org-Ebene. Die frühere Kurzform „Global → Team → Projekt" bleibt als didaktisches Beispiel in Gebrauch.

**E3 — Kontext-Blöcke statt Monolith:** Kleinste versionierbare Einheit ist ein Block (z. B. ein Systemprompt, ein Beispielpaar aus Eingabe und Ausgabe oder eine Routing-Regel). Konsequenz: Granulare Wiederverwendung, step-spezifische Komposition, Konflikterkennung auf Block-Ebene.

**E4 — OIDC statt eigenem User-Management:** Identität kommt vom OIDC-Provider, die Rolle bei jeder Anfrage aus dem Gruppen-Claim des Tokens (Gruppen `chy-*`). Konsequenz: kein eigenes Benutzerverzeichnis, kein Synchronisationsbedarf. Getestet mit Authentik; der Code erwartet dessen Aufbau von Issuer und Schlüsseln, eine Anwendung je Oberfläche (ADR-031).

**E5 — Actor-Pattern für DSGVO:** Personenbezug nie direkt in Fachdaten, sondern über anonymisierbare Actor-Referenz. Konsequenz: Fachliche Revisionssicherheit und das Löschrecht nach DSGVO sind gleichzeitig erfüllbar.

**E6 — Human Oversight hardcoded:** Menschliche Freigabe ist Pflicht und nicht konfigurierbar (ADR-004). Gebaut: Jeder Workflow braucht einen `human_review`-Knoten, und kein Pfad von einem `llm_call` endet ohne ihn; ein Lauf lässt sich jederzeit abbrechen (Art. 14(4)(e), CHY-200). Noch nicht unter der Zusage: `/v1/complete` liefert Modellantworten ohne Gate (→ M6, CHY-199), und die Freigabepflicht nach einer im Benchmark erkannten Regression kommt mit den Benchmark-Läufen (→ M6, CHY-64). Entschieden ist, die Prüftiefe nach Risiko abzustufen (→ M6, CHY-223): Abschalten lässt sie sich weiterhin nicht, aber nicht jede Stufe hält die Ausgabe an. `/v1/complete` bleibt dann nur dort, wo die Stufe ohne Gate vor der Ausgabe zulässig ist. Konsequenz: AI-Act-kompatibles Sicherheitsverhalten als nicht umgehbarer Kern jedes Workflows.

### 4.2 Motivationen

```mermaid
graph TD
    PROB1[Problem:\nModell-Varianz] --> SOL1[Loesung:\nAdaptions-Engine +\nKontext-Repository]
    PROB2[Problem:\nModel Drift] --> SOL2[Loesung:\nDrift-Erkennung +\nHuman Oversight]
    PROB3[Problem:\nPrompt Sensitivity] --> SOL3[Loesung:\nKuratiertes Repository\nFormulierungsregeln]
    PROB4[Problem:\nAgentic Context\nExplosion] --> SOL4[Loesung:\nStep-spezifische\nKomposition]
    PROB5[Problem:\nWissens-Isolation] --> SOL5[Loesung:\nScope-Hierarchie +\nGovernance]
    PROB6[Problem:\nDSGVO vs.\nRevisionssicherheit] --> SOL6[Loesung:\nActor-Pattern]
    PROB7[Problem:\nModalitaets-Isolation] --> SOL7[Loesung:\nEine Adaptionsschicht\nfuer alle Modalitaeten,\ngeplant]

    classDef cloud fill:#1a3a4f,color:#38bdf8
    classDef good fill:#1a4731,color:#4ade80

    class PROB1,PROB2,PROB3,PROB4,PROB5,PROB6,PROB7 cloud
    class SOL1,SOL2,SOL3,SOL4,SOL5,SOL6,SOL7 good
```

### 4.3 Lösungsmechanismus pro LLM-Qualitätsphänomen

Die drei grundlegenden Phänomene werden durch drei getrennte Mechanismen adressiert. Gebaut ist bisher das versionierte, geprüfte Kontext-Repository; die Drift-Erkennung ist teilweise gebaut (🔧, Läufe → M6, Messfühler → M7, ADR-044), die Adaption je Modell geplant (📋 → M6, CHY-204):

| Phänomen                    | Mechanismus                                                                                                                                                                                                    | Beteiligte Komponenten                                                                    |
| ---------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------- |
| **Modell-Varianz**     | Adaptionsschicht übersetzt Kontext modell-spezifisch — jedes Modell im Workflow erhält einen auf seine Eigenheiten kalibrierten Prompt                                                                      | Adaptions-Engine (📋 → M6, CHY-204), Kontext-Repository |
| **Model Drift**        | Die Drift-Erkennung (ADR-044) misst die Modelle gegen freigegebene Kalibrieranker — nach Zeitplan, bei jeder Änderung und als Probelauf vor der Freigabe — und beobachtet den Betrieb; ab M7 meldet ein Messfühler, wenn die Anker nicht mehr zum Betrieb passen. Eine Regression setzt am Lauf das Merkmal `review_needed`, das nur ein Reviewer zurücknimmt — Human Oversight ist Pflicht (nicht abschaltbar, ADR-004). → M6/M7 (🔧, CHY-68, CHY-181, CHY-217, CHY-218, CHY-219, CHY-64) | Benchmark und Messfühler (Kontext `learning`), Kalibrieranker (`calibration_anchor`), Human Oversight |
| **Prompt Sensitivity** | Kuratiertes Kontext-Repository speichert modell-spezifische Formulierungsregeln — instabile Wortlaute werden durch erprobte, versionierte Bausteine ersetzt                                                     | Kontext-Repository (versionierte Blöcke, Prüf-Lebenszyklus, ADR-042) |

**Abgrenzung zu LiteLLM und Langfuse (ADR-012):**
LiteLLM (Routing) und Langfuse (Observability) sind Stack-Bausteine von Cohereny — sie adressieren
keines der drei Phänomene selbst. Langfuse macht Model Drift über Traces *sichtbar*, löst ihn aber nicht.
Cohereny ist die Schicht, die auf beiden aufbaut und die eigentliche Adaption übernimmt.

---

## 5. Bausteinsicht

> **Status-Legende:** ✅ live · 🔧 teilweise · 📋 geplant, → Mx Meilenstein (siehe §1.4). Die Whitebox-Sicht beschreibt die Ziel-Architektur; jede Box im Cohereny Core trägt ihren heutigen Stand.

### 5.1 Whitebox Gesamtsystem (Ebene 1)

```mermaid
graph TB
    subgraph Client["Client Layer"]
        WF[Fremde Engine\nz.B. LangGraph]
        UI[Referenz-UI\nAstro]
        SDK[Python SDK]
        CLI_C[CLI Client]
    end

    subgraph CC["Cohereny Core"]
        GW[API Gateway ✅\nAuth + Routing]
        RUN[Workflow-Runner ✅\nTraversierung + Wartezustände]
        RT[Router Engine ✅\nModell-Selektion]
        COMP[Kompositions-Engine ✅\nBlock-Hierarchie]
        AD[Adaptions-Engine 📋 M6\nPrompt-Transform]
        GOV[Freigabeweg ✅\nBlock-Lebenszyklus, ADR-042]
        EV[Evaluations-Engine 🔧\nAnker-Validatoren gebaut,\nim Runner M5]
        BM[Benchmark 🔧\nZeitplan + Aenderungen, M6]
    end

    subgraph Storage["Storage Layer"]
        PG[(PostgreSQL\nKontext-Repository\nAudit Log)]
        RD[(Redis\nCache)]
    end

    subgraph External["Externe Systeme"]
        LM[LiteLLM Gateway]
        IDP[OIDC Provider]
        LF[Langfuse\nTracing]
    end

    Client --> GW
    GW --> RUN
    GW --> RT
    GW --> GOV
    RUN --> COMP
    RUN --> RT
    RUN <--> GOV
    RUN <--> PG
    RT --> COMP
    COMP --> AD
    AD --> LM
    RT <--> PG
    COMP <--> PG
    COMP <--> RD
    GOV <--> PG
    EV --> PG
    BM --> LM
    BM --> PG
    GW --> IDP
    AD --> LF

    classDef cloud fill:#1a3a4f,color:#38bdf8
    classDef good fill:#1a4731,color:#4ade80
    classDef warn fill:#4a3500,color:#fbbf24

    class CC cloud
    class Storage good
    class External warn
```

> **Zur LangGraph-Box (korrigiert 04.09.2026, CHY-82):** Sie steht im *Client Layer* und
> gehört dorthin. ADR-028 hat LangGraph aus dem **Kern** entfernt — dort traversiert seit der
> Entscheidung ein eigener Runner —, während Whitepaper §8.2.2 verlangt, dass Cohereny *in* einer fremden
> Engine als Knoten einsetzbar bleibt. Beide Aussagen gelten gleichzeitig, und genau das macht
> die Box zu einem Beispiel statt zu einer Abhängigkeit. Sie heißt jetzt so.
>
> **Der Workflow-Runner fehlte im Bild.** ADR-028 hat ihn am 11.08. beschlossen; die
> Bausteinsicht kannte ihn drei Wochen lang nicht. Nachgetragen; gebaut mit CHY-127 (✅, §5.4b).

### 5.2 Whitebox API Gateway (Ebene 2) — Status: ✅ (OIDC-Validierung je Oberfläche, ADR-031; neun geplante Operationen antworten 501 mit `x-cohereny-status: planned`)

**Verantwortung:** Eintrittspunkt für alle Anfragen. Validiert OIDC-Token, prüft die Audience gegen die aufgerufene Oberfläche (ADR-031) und leitet die Rolle aus dem `groups`-Claim ab.

**Schnittstellen:**

- Eingehend: HTTP/REST mit Bearer Token
- Ausgehend: Token-Validierung gegen den OIDC-Provider (JWKS je Issuer); Weiterleitung an die Anwendungsfälle der Kontexte und des Kernels

**Ablehnung:** Nur authentifizierte Requests; Rolle wird aus Token-Claims abgeleitet (kein Parameter-Missbrauch möglich).

### 5.3 Whitebox Kompositions-Engine (Ebene 2) — Status: ✅

**Verantwortung:** Erstellt für einen gegebenen Workflow-Step einen Composed Context durch Zusammenführung von Blöcken aus Global → Team → Projekt. Lehnt erklärte Konflikte ab (Privacy-Ratchet).

**Schnittstellen:**

- Eingehend: `scope_id` (Blatt; die Vorfahren leitet der Server aus dem ltree-Pfad ab), optional `workflow_id`, `node_ref`, `model_id`, `block_types`, `refresh`
- Ausgehend: `ComposedContext` (Blöcke flach nach tief, `block_versions`, `scope_generations`, `cache_hit`) oder ein Fehler, wenn der Privacy-Ratchet verletzt ist (409)

**Konfliktstrategie:**

1. Komponiert wird die ganze Kette, flach nach tief; nichts wird überschrieben, ein tieferer Block steht nur später im Text
2. Konflikt ist nur, was Blöcke erklären (`governance_key` + `privacy_impact`): Ein `permissive`-Block gegen einen `restrictive`-Vorfahren und jede erneute Aussage unter einem `locked`-Vorfahren werden abgelehnt — bei der Freigabe und erneut beim Komponieren (409; ADR-017, CHY-62)
3. Aufgelöst wird über eine freigegebene Änderung im höheren Scope (ADR-042)

> **Korrektur (04.09.2026, CHY-82):** Punkt 2 lautete „`force_override: true` gewinnt immer".
> Das war schon durch ADR-017 überholt und ist seit CHY-62 auch im Code falsch: Ein Block mit
> `privacy_impact: permissive` wird gegen einen restriktiven Vorfahren **abgelehnt**, und ein
> `locked`-Block ist von unten überhaupt nicht überschreibbar — unabhängig von jedem Override.
> ADR-017 nennt genau diesen Satz als Anlass seiner Entscheidung; er stand hier trotzdem
> unverändert weiter.
>
> Ebenfalls präzisiert: **die Komposition überschreibt technisch nichts.** `as_prompt` fügt
> alle Blöcke aneinander, flach nach tief. „Tiefster Scope gewinnt" beschreibt eine Lesart des
> zusammengesetzten Textes, keinen Mechanismus — siehe ADR-017 Fortschreibung v2.17.

### 5.4 Whitebox Freigabeweg (Ebene 2, bis v2.44 „Governance Engine“) — Status: ✅ als Block-Lebenszyklus in `contexts/repository` (ADR-042)

**Verantwortung:** Implementiert den Review-Workflow für Kontext-Block-Änderungen. Prüft Rollen und verwaltet den Review-Status.

**Schnittstellen:**

- Eingehend: Vorschlag (Actor-Ref + Block-Inhalt)
- Ausgehend: Status-Änderung (`pending_review` → `active`/`rejected`) + Audit-Log-Eintrag

**Unveränderlich:** In einem strengen Geltungsbereich (Organisation, Team, Workspace) wird ein Block nur über ein Review durch die Rolle `reviewer` aktiv. In einem direkten (Projekt, Unterprojekt, persönlich) wird er beim Schreiben aktiv, weiterhin geprüft durch den Privacy-Ratchet und auditiert (ADR-011).

### 5.4b Whitebox Workflow-Runner (Ebene 2) — Status: ✅ (CHY-127)

**Verantwortung:** Führt eine Workflow-Definition aus. Traversiert `workflow_nodes` und
`workflow_connections`, führt Knoten über eine `NodeExecutor`-Schnittstelle aus, zählt
Schleifendurchläufe je Kante und Lauf und hält Freigabe-Wartezustände als Zeile in der
Datenbank.

**Warum Eigenbau statt LangGraph:** ADR-028, vier Gründe — `loop_limit` gilt je Kante und
nicht je Lauf, Sub-Workflows sind eigenständige, versionsgepinnte Objekte statt Subgraphen,
Workflow-Definitionen kommen zur Laufzeit aus der Datenbank, und der Audit-Trail entsteht
ohnehin selbst. Die verworfene Alternative (`interrupt()` plus `PostgresSaver`) war technisch
tragfähig; sie hätte die vier Punkte nur daneben zu bauen verlangt.

**Schnittstellen:**

- Eingehend: `workflow_id` + Eingabedaten; Fortsetzung nach einer Freigabe
- Ausgehend: Komposition (`context_compose`) und Modellaufruf mit Routing (`llm_call`) als Executors, die die Transportschicht einsetzt (`api/v1/workflows.py`) — ein Kontext importiert keinen anderen (ADR-030); sie rufen die veröffentlichte Schnittstelle von `repository`, den Router und `core/completion`. `human_review` hält der Runner selbst: Wartezustand am Lauf, eine Stimme je Person in `review_votes` (Migration 027), Entscheidung über `POST /v1/workflows/runs/{run_id}/oversight`. Ausführbar sind `context_compose`, `llm_call` und die beiden Gateways; `tool_call` und `sub_workflow` lehnt der Runner ab (`NodeTypeNotExecutable`)

**Zustand (Migration 018, CHY-82 ✅):** `workflow_runs`, `node_executions`, `loop_counters`.
Tabellen und Runner stehen (`contexts/orchestration/runner.py`).

**Unveränderlich:** `human_review` ist ein Wartezustand der Status-Maschine, kein blockierter
Thread. Eine Zeile in der Datenbank übersteht einen Neustart und ist von jeder API-Instanz
sichtbar — der heutige Pilot wartet mit `ev.wait(timeout=7200)` in einem Thread und verliert
den Zustand mit dem Prozess.

### 5.5 Whitebox Benchmark (Ebene 2) — Status: 🔧 (Anker, Validatoren, Messtabellen ✅ CHY-67; Läufe 📋 → M6, CHY-68; Report und Freigabe 📋 → M6, CHY-64)

**Verantwortung:** Führt Benchmark-Runs durch. Erkennt Qualitäts-Regressionen. Erzwingt Human Oversight wenn Regressionen gefunden.

> **Seit ADR-044 (03.11.2026):** Der Lauf geschieht **nach Zeitplan** (einstellbar, Vorgabe nächtlich; CHY-181), **bei Änderungen** an Blöcken, Routing-Regeln, Ankern oder Modellen, als **Probelauf vor einer Freigabe** (CHY-217) und auf Abruf über die CLI `cc benchmark run` (CHY-68; die CLI kennt den Befehl noch nicht). Alles kommt mit M6. Die Referenzinstallation misst auf Abruf, solange ihre lokalen Inferenz-Rechner nachts aus sind; das ist Betrieb, keine Produkteigenschaft. Testfälle sind Kalibrieranker im Kontext-Repository (ADR-043). Umsetzung: CHY-67 ✅, CHY-68, CHY-64, dazu CHY-181 und CHY-217.

**Schnittstellen:**

- Eingehend: Zeitplan (CHY-181), Änderungen aus Freigabeweg und Modellkonfiguration, Probelauf vor einer Freigabe (CHY-217), CLI `cc benchmark run` (CHY-68) — alle geplant
- Ausgehend: ein Lauf in `benchmark_runs` mit Merkmal `review_needed`, je Anker-Version, Modell und Variante (`composed`/`raw`) eine Messung in `benchmark_measurements` (Migration 028); Report und Freigabe nach einer Regression geplant (CHY-64)

**Vorgesehen (CHY-64):** Eine Regression setzt `review_needed` am Lauf, und das ist nicht abschaltbar.

---

### 5.6 Domänenmodell — vollständiges Entity-Modell

Erarbeitet 2026-06-29, abgeglichen mit `migrations/` 001–028 am 31.10.2026; Geplantes ist mit 📋 markiert. Maßgeblich gegenüber der Kurzreferenz im Whitepaper §8.
Sechs Domänen — eng verknüpft, aber klar abgegrenzt.

---

#### Domäne 1: Identity & Scope-Hierarchie

Statt einer hardcodierten 3-Stufen-Hierarchie (Global → Team → Projekt) gibt es eine **dynamische Scope-Hierarchie** via `scope_path` (ADR-008). Jeder Scope-Knoten wird in einer einheitlichen Tabelle geführt.

**Scope-Knoten-Typen:** `org | team | workspace | project | sub_project | personal`

Ein **Workspace** ist ein teamübergreifender Container — z.B. "Alle Angebote für Kunde XY" umfasst Projekte aus Vertrieb, Legal und IT gleichzeitig. Sub-Projekte erlauben weitere Granularität innerhalb eines Projekts.

```
ScopeNode         ← scope_nodes (Kernel)
  id (UUID), name, description
  node_type: org | team | workspace | project | sub_project | personal
  scope_path        ← ltree, aus Typ und ID abgeleitet (cc_scope_label, ADR-008)
  parent_id         ← FK auf übergeordneten ScopeNode
  privacy_level     ← public | internal | confidential | critical | NULL
                      wirksam ist die strengste Klasse der Kette, ohne Angabe critical (ADR-041)
  created_by, created_at

Actor             ← DSGVO-sichere Personenreferenz
  id
  idp_sub           ← NULL nach Anonymisierung (und bei lokalen Actors)
  display_name      ← "[anonym]" nach Anonymisierung
  role: admin | architect | reviewer | contributor | consumer
  is_anonymized, anonymized_at

ScopeMembership   ← scope_memberships: Actor ↔ ScopeNode + Rolle
  scope_id, actor_id, role, granted_by, granted_at
  ← gibt Laufinhalte frei, durchgesetzt per Row-Level Security (ADR-047);
    Blöcke → M6 (CHY-237)

UserPreferences   📋 nicht gebaut — persönlich, Hard-Delete bei DSGVO-Anfrage
```

**Vererbung:** Ein ContextBlock hängt an einem Geltungsbereich (`scope_id`) und ist für alle Geltungsbereiche darunter sichtbar, abgefragt über den ltree-Pfad (ADR-008). Komponiert wird die ganze Kette, flach nach tief; ein tieferer Block überschreibt technisch nichts (§5.3). Eine Datenschutzregel darf ein tieferer Scope verschärfen, nicht lockern (Privacy-Ratchet, ADR-017), und unter einem `locked`-Block darf er sie gar nicht neu aussprechen.

---

#### Domäne 2: Workflow

Ein Workflow ist ein **gerichteter Graph** — keine geordnete Liste. Die Ausführungsreihenfolge
ergibt sich aus der Traversierung, nicht aus einer Position im Datensatz. **Zyklen sind zulässig**
und tragen verpflichtend eine Iterationsgrenze (ADR-026).

> **Korrektur gegenüber v2.6:** Bis einschließlich v2.6 stand hier „DAG (Directed Acyclic Graph)".
> Die Aussage war gegen die *geordnete Liste* gerichtet, wurde aber als Zyklenverbot gelesen — ohne
> ADR und ohne Prüfung in der Implementierung. Sie konnte den seit 2026-06-28 laufenden Study-Pilot
> mit seinen zwei Rückschleifen nicht abbilden. Begründung der Neufassung in ADR-026.

##### Knotentypen

Drei Knotentypen sind kein beliebiger Baustein, sondern der unveränderliche Kern jedes Workflows —
sie setzen ADR-004 (Human Oversight als nicht abschaltbarer Kern) auf Graphebene durch.

```mermaid
graph TB
    subgraph PFLICHT["Pflichtkern — in jedem Workflow, vom Validator erzwungen"]
        direction LR
        CTX["context_compose\nScope auflösen,\nBlöcke komponieren"] --> LLM["llm_call\nModell-Routing\n+ Inferenz"]
        LLM --> HR["human_review\nFreigabe,\nWartezustand"]
    end

    subgraph FREI["Frei kombinierbar"]
        direction LR
        TOOL["tool_call\nexterner Dienst"] ~~~ FORK["fork_gateway\nverzweigen"]
        FORK ~~~ JOIN["join_gateway\nzusammenführen"]
        JOIN ~~~ SUB["sub_workflow\neigener Endpunkt"]
    end

    PFLICHT ~~~ FREI

    classDef good fill:#1a4731,color:#4ade80
    classDef cloud fill:#1a3a4f,color:#38bdf8
    class CTX,LLM,HR good
    class TOOL,FORK,JOIN,SUB cloud
```

`sub_workflow` ist neu in v2.7. Die übrigen sechs Typen stehen seit Migration `001_initial` im
Schema; die Doku nannte bis v2.6 abweichend `start | end | task | branch_gateway | join_gateway`
und kannte `human_review` nicht als Knotentyp.

##### Entitäten

```
WorkflowDefinition  ← versioniert (Append-Only)
  workflow_id (stabile UUID), version, superseded_by_version
  name, description
  scope_id          ← FK auf ScopeNode; Pfad hängt am ScopeNode (ltree, ADR-008)
  risk_classification:                                          📋 geplant
    risk_category:              high_risk | limited_risk | minimal_risk
    requires_human_oversight:   bool
    data_categories:            [medical, financial, personal, ...]
    privacy_level:              public | internal | confidential | critical
  status: draft | active | deprecated
  actor_ref, created_at

WorkflowNode        ← Knoten im Graph, Teil einer WorkflowDefinition-Version
  node_id (stabile UUID), workflow_id, workflow_version
  node_ref          ← sprechender Bezeichner, eindeutig je Workflow
  name, description
  node_type: context_compose | llm_call | tool_call
           | fork_gateway | join_gateway | human_review
           | sub_workflow                                       🔵 neu v2.7

  Für node_type = llm_call:
    task_type:  str, Pflicht    ✅ Spalte seit Migration 023 (CHY-145)
                ← der Schlüssel, nach dem die Routing-Regeln des
                  Geltungsbereichs aufgelöst werden (ADR-038). Ein Knoten
                  nennt sein Modell NICHT selbst

    ⚠️ Bis v2.28 stand hier ein anderes Modell, und nichts davon gab es:
       • `classification` mit complexity, privacy_level, data_sensitivity,
         requires_local — nur `task_type` wird gelesen, und das ist jetzt eine
         Spalte. `privacy_level` gehört nicht an den Knoten, sondern an den
         Geltungsbereich (entschieden 24.09.2026, CHY-160). Die übrigen drei
         liest nichts — sie aufzunehmen hieße, Felder ohne Leser anzulegen
       • `model_assignment` mit strategy, fixed_model_id, fallback_chain —
         überholt: Welches Modell eine Aufgabe bearbeitet, ist eine Routing-
         Regel mit Freigabe und Ratchet (ADR-038), und seit CHY-145 benutzt
         der Runner sie. `config["model"]` ist unspeicherbar (023)
       • `prompt_template_block_id` — Prompt und Systemnachricht werden
         Bausteine in CHY-146, nicht ein Fremdschlüssel am Knoten

  Für node_type = context_compose:
    context_requirements:                                       🔵 neu v2.27
      block_types: [system_prompt, user_context, tool_schema,
                    example_io]  ← nur komponierbare
                    Typen (ADR-037), mindestens einer. Ganz weglassen
                    heißt „alles, was der Geltungsbereich hergibt"

    ⚠️ Bis CHY-134 stand hier `required_block_types`, `required_tags` und
       `exclude_tags` — und im Schema gar nichts. Die beiden Tag-Filter sind
       weiterhin nicht umgesetzt: `compose` filtert nicht nach Tags, und ein
       Feld, das niemand auswertet, ist genau die Zusage ohne Deckung, die
       dieses Ticket entfernt hat. Aufgenommen als CHY-147. Die Beispielwerte
       `model_profile` und `translation_rule` waren zusätzlich Blocktypen, die
       es nie gab

  Für node_type = join_gateway:
    join_policy: wait_all | wait_any | first_wins

  Für node_type = human_review:                                 🔵 neu v2.7
    review_role:      approver | four_eyes    ← Mindestrolle bzw. Vier-Augen-Pflicht
                                                  four_eyes durchgesetzt seit CHY-176:
                                                  zwei verschiedene Personen, eine Zeile je
                                                  Entscheidung in review_votes (Migration 027),
                                                  der Primärschlüssel verweigert die zweite
                                                  derselben Person. Eine Ablehnung beendet sofort
    timeout:          interval | null         ← null = wartet unbegrenzt
    on_timeout:       reject | escalate       ← nie „auto-approve" (ADR-004)

  Für node_type = sub_workflow:                                 🔵 neu v2.7
    target_workflow_id:      UUID   ← FK auf WorkflowDefinition
    target_workflow_version: int    ← gepinnt, kein „immer aktuell"
    input_mapping:           JSONB  ← Feldabbildung Eltern → Kind
    output_mapping:          JSONB  ← Feldabbildung Kind → Eltern

WorkflowConnection  ← Kante im Graph
  id, workflow_id, workflow_version
  from_node_id → to_node_id
  condition:      JSONB | null    ← null = bedingungslos ausführen
  label:          str | null      ← "medical" / "legal" / "default"
  execution_mode: sequential | parallel
  loop_limit:     int | null      ← Pflicht auf jeder Rückkante   🔵 neu v2.7
                                    (ADR-026); null nur auf Vorwärtskanten

StepClassification  ← globale Taxonomie, Admin-gepflegt
  task_type (PK), description
  default_model_class, default_privacy_requirement
  typical_context_block_types: [str]
```

> **Implementierungsstand (04.09.2026, CHY-82):** Das Modell oben steht im Schema, mit Ausnahmen: `StepClassification` gibt es nicht; Versionen einer Definition (stabile `workflow_id`, `workflow_version` an Knoten und Kanten) sind geplant (CHY-127) — heute hängen Knoten und Kanten an der Zeilen-ID; die Kanten heißen `source_node_id`/`target_node_id`; `timeout` heißt `review_timeout` und wird noch nicht ausgewertet.
> **Migration 018** ergänzt `loop_limit` und `label` an den Kanten, `sub_workflow` samt
> Mapping-Feldern, die Detailfelder von `human_review` sowie `risk_classification`,
> `description` und `superseded_by_version` an der Definition. Dazu die Zustandstabellen des
> Runners: `workflow_runs`, `node_executions`, `loop_counters`.
>
> Die Nummer war zweimal veraltet: hier stand „Migration 011 (010 ist für CHY-42 reserviert)",
> und beide sind belegt — 010 durch die Actor-Anonymisierung, 011 durch das unveränderliche
> Audit-Log. **Eine in Prosa reservierte Nummer ist nicht reserviert.**
>
> Der **Validator** (`contexts/orchestration/graph.py`) setzt die vier Regeln durch und teilt
> sich die Graphanalyse mit dem Runner — ADR-028 verlangt genau das, denn zwei
> Traversierungen, die sich über den Begriff „Rückkante" uneinig sind, sind sich auch über die
> geltende Iterationsgrenze uneinig.
>
> **Der ausführende Runner kam mit CHY-127** (`contexts/orchestration/runner.py`, §5.4b). ADR-028
> hatte ihn am 11.08. beschlossen und unter „Betrifft" CHY-81, CHY-82 und CHY-83 gelistet — keines
> davon baute ihn; drei Wochen ohne Träger, bis er am 04.09. CHY-127 wurde.

##### Strukturen

**Sequenz.** Kanten ohne `condition` und mit `execution_mode: sequential`. Der Regelfall.

**Verzweigung.** Ein Knoten hat mehrere ausgehende Kanten mit `condition`. Genommen wird jede,
deren Bedingung zutrifft — ein exklusives Oder (XOR) entsteht, wenn sich die Bedingungen
ausschließen; der Runner erzwingt es nicht. Eine `default`-Kante greift nur, wenn keine zutrifft.
Trifft keine Bedingung zu und existiert keine `default`-Kante, bricht der Run mit einem Fehler ab;
stilles Versanden ist keine Option.

**Parallelisierung (AND) und Zusammenführung.** Mehrere ausgehende Kanten ohne Bedingung werden alle
genommen; ein `join_gateway` mit `join_policy` wartet auf alle Pfade (`wait_all`), auf den ersten
(`wait_any`) oder verwirft die Nachzügler (`first_wins`). Die Zweige laufen heute nacheinander;
`execution_mode` wird gespeichert, aber noch nicht ausgewertet (`contexts/orchestration/runner.py`).

```mermaid
graph LR
    C["llm_call\nklassifizieren"] --> F{{"fork_gateway"}}
    F -->|"execution_mode: parallel"| P1["llm_call\nZusammenfassung"]
    F -->|"execution_mode: parallel"| P2["tool_call\nKennzahlen ziehen"]
    P1 --> J{{"join_gateway\njoin_policy: wait_all"}}
    P2 --> J
    J --> HR["human_review"]

    classDef good fill:#1a4731,color:#4ade80
    classDef cloud fill:#1a3a4f,color:#38bdf8
    class HR good
    class C,P1,P2,F,J cloud
```

**Schleife.** Eine Kante, die auf einen bereits durchlaufenen Knoten zurückführt. Sie **muss**
`loop_limit` tragen. Ist die Grenze erreicht, wird die Rückkante nicht mehr genommen und der Run
läuft auf dem regulären Pfad weiter. Ein Vermerk `loop_limit_reached` im Audit-Trail soll im Nachhinein
unterscheidbar machen, ob ein Ergebnis überzeugt hat oder nur das Budget aufgebraucht war (ADR-026) —
gebaut ist er noch nicht: Der Runner erkennt die erschöpfte Kante, protokolliert sie aber nicht.

```mermaid
graph LR
    S["llm_call\nzusammenfassen"] --> V["llm_call\nAbdeckung prüfen"]
    V -->|"Abdeckung ausreichend"| HR["human_review"]
    V -->|"Lücken gefunden\nloop_limit: 3"| S

    classDef good fill:#1a4731,color:#4ade80
    classDef warn fill:#4a3500,color:#fbbf24
    classDef cloud fill:#1a3a4f,color:#38bdf8
    class HR good
    class V warn
    class S cloud
```

**Sub-Workflow** (Schema und Validator ✅, Ausführung 📋). Ein Knoten, der eine andere
`WorkflowDefinition` in gepinnter Version aufruft. Ausführen kann der Runner ihn noch nicht: Für
`sub_workflow` ist kein Executor registriert, ein Lauf, der ihn erreicht, endet mit `failed`.
Der aufgerufene Workflow ist ein vollwertiger Workflow: eigener Endpunkt, eigene Version, eigener
Audit-Trail — und damit **selbst an den Pflichtkern gebunden**. Eine fremde Workflow-Engine bindet
Cohereny schon heute als einzelnen Schritt ein, über `POST /v1/workflows/{workflow_id}/runs`; der Lauf
hält am `human_review` an, die Freigabepflicht bleibt bei Cohereny (ADR-004, ADR-028).

```mermaid
graph TB
    subgraph OUTER["Aufrufender Workflow — kredit_pruefung v4"]
        direction LR
        A["llm_call\nUnterlagen lesen"] --> SW["sub_workflow\ntarget: drift_check v7"]
        SW --> HRO["human_review\nKreditanalyst"]
    end

    SW -.->|"input_mapping ↓\noutput_mapping ↑"| I1

    subgraph INNER["Aufgerufener Workflow — drift_check v7 · eigener Endpunkt, eigene Version, eigener Audit-Trail"]
        direction LR
        I1["context_compose"] --> I2["llm_call"]
        I2 --> I3["human_review"]
    end

    classDef good fill:#1a4731,color:#4ade80
    classDef cloud fill:#1a3a4f,color:#38bdf8
    class HRO,I1,I2,I3 good
    class A,SW cloud
```

Rekursion ist ausgeschlossen: Der Validator prüft den Aufrufgraphen der Sub-Workflows auf
Azyklizität. Zyklen sind *innerhalb* eines Workflows erlaubt, *zwischen* Workflows nicht — dort
gäbe es keine Kante, an der eine Iterationsgrenze hängen könnte.

##### Validierung

Konfigurierbar heißt nicht beliebig. Ein Workflow erreicht `status: active` nur, wenn vier
Bedingungen erfüllt sind. Geprüft wird bei der Aktivierung (vorab auch über
`POST /v1/workflows/{workflow_id}/validate`), nicht zur Laufzeit — ein Workflow, der
seine Freigabepflicht erst mitten im Lauf verletzt, hat bis dahin bereits Kosten verursacht und
Daten bewegt.

```mermaid
graph LR
    D["PUT /v1/workflows/{id}\nstatus: active"] --> V{"Validator"}
    V -->|"Pflichtknoten fehlt"| R1["409 abgelehnt"]
    V -->|"Rückkante ohne loop_limit"| R2["409 abgelehnt"]
    V -->|"human_review nicht nach\ndem letzten llm_call"| R3["409 abgelehnt"]
    V -->|"Aufrufgraph der\nSub-Workflows zyklisch"| R4["409 abgelehnt"]
    V -->|"alle Regeln erfüllt"| OK["status: active"]

    classDef good fill:#1a4731,color:#4ade80
    classDef danger fill:#4a1942,color:#f472b6
    classDef cloud fill:#1a3a4f,color:#38bdf8
    class OK good
    class R1,R2,R3,R4 danger
    class D,V cloud
```

Die Regeln sind Code mit Tests, nicht Konvention. Das ist der Unterschied, auf den es gegenüber
einer Aufsicht ankommt: „unsere Modellierungsrichtlinie sieht einen Freigabeschritt vor" ist eine
Absichtserklärung, „der Server nimmt einen Workflow ohne Freigabeschritt nicht an" ist eine
prüfbare Eigenschaft. Warum diese Prüfung nicht in ein XSD ausgelagert werden kann, steht in
ADR-027.

---

#### Domäne 3: Context Repository

Kleinste versionierbare Einheit ist der **ContextBlock**. Typen unterscheiden sich in der Bedeutung von `content` (Text) und `metadata` (JSONB).

```
ContextBlock        ← context_blocks; Veröffentlichtes ist unveränderlich (Trigger, Migration 013/016)
  id (row UUID)           ← technischer Row-Key
  block_id (stabile UUID) ← logische Identität über alle Versionen
  version: int            ← beginnt bei 1, hochzählend; UNIQUE (block_id, version)
  superseded_by_version: int | null   ← null = aktuelle Version
  UNIQUE (block_id) WHERE superseded_by_version IS NULL  ← DB-Constraint

  block_type: system_prompt | user_context | tool_schema | example_io
            | calibration_anchor | routing_rule
              ← nur die ersten vier sind Prompt-Material (ADR-037, ADR-043)
  scope_id            ← FK auf ScopeNode
  tags: [str]         ← thematische Zuordnung, multi-value
  content: text       ← Prompt-Material; bei routing_rule die Begründung,
                        bei calibration_anchor die Referenzantwort
  metadata: JSONB     ← erweiterbar; bei routing_rule und calibration_anchor
                        die maschinenlesbare Regel bzw. der Testfall
  governance_key, privacy_impact: restrictive | permissive, locked: bool
                      ← Privacy-Ratchet (ADR-017, Migration 016)

  status: draft | pending_review | active | rejected | deprecated
  author_id, created_at
  (change_reason steht im Audit-Eintrag der neuen Version)
```

**Typ-spezifische Strukturen** — gebaut (✅) sind `routing_rule` und `calibration_anchor`, ihre Struktur steht in `metadata`; `model_profile`, `translation_rule` und `prompt_template` gehören zur Adaption und sind noch keine Blocktypen (📋 → M6, CHY-204):

```
model_profile:                          ← 📋 Adaption, → M6
  model_id, provider
  prompt_style: instruction | chat | completion | alpaca
  max_context_tokens                    ← Datenklassen-Obergrenze je Modell steht in
                                          router/routing-config.yaml (ADR-041)
  avg_tokens_per_sec, cost_per_1k_tokens
  strengths: [str], weaknesses: [str]
  system_prompt_supported: bool, json_mode_supported: bool

translation_rule:                       ← 📋 Adaption, → M6; lernfähig (s. Domäne 4)
  source_model_id, target_model_id
  task_type
  transformation_flags:
    restructure_system_prompt: bool
    add_examples: bool
    simplify_instructions: bool
    add_output_format_hint: bool
  prompt_template: str
  confidence_score: float               ← steigt durch Lern-Loop
  sample_count: int
  avg_quality_improvement: float
  learned_from_anchors: [block_id]     ← welche Anchors haben diese Regel erzeugt

routing_rule:                           ← Regel in metadata, Begründung in content (CHY-87)
  task_type: str                        ← es gilt die Regel des tiefsten Scopes
  candidates: [model_id]                ← Reihenfolge = Rückfallkette; kein Kandidat
                                          unter der Datenklasse des ersten
  max_tokens: int                       ← Obergrenze und Vorgabe der Antwortlänge (CHY-166)

calibration_anchor:                     ← Testfall des Benchmarks (ADR-043, CHY-67)
  content: Referenzantwort
  metadata:
    task_type, input
    raw_prompt                          ← eingefrorener, unadaptierter Prompt (ADR-019)
    validators                          ← ADR-006 Stufe 1 und 2: json_schema, regex, length,
                                          required_terms, forbidden_terms, rouge_l
    models: [model_id]                  ← optional: nur gegen diese messen
  (Messergebnisse stehen nicht im Anker, sondern in benchmark_measurements)

prompt_template:                        ← 📋 Adaption, → M6
  task_type
  template: str                         ← Platzhalter: {input} {context} {format_instructions}
  model_variants: { model_id → template_override }
  output_format: markdown | json | plain
```

**Bootstrap-Templates** (📋, nach 1.0, CHY-209) sind keine eigene Entität. Sie sind Sammlungen von ContextBlöcken im `org`-Scope mit Tag `template:healthcare`, `template:software-engineering` etc. Neues Projekt/Team wählt ein Template → getaggte Blöcke werden als Version 1 in den Ziel-Scope kopiert. Gebaut ist bisher nur `promote`: die Live-Version eines Blocks als Entwurf in einen strengen Geltungsbereich darüber.

---

#### Domäne 4: Feedback & Lern-Loop (📋 geplant → M6, CHY-40)

> **Stand 31.10.2026:** Nicht gebaut. `feedback_events` und `learning_proposals` stehen seit Migration 001, mit anderem Zuschnitt als unten und ohne Leser; die Endpunkte antworten 501. `ExecutionLog` gibt es nicht — ausgeführte Knoten stehen in `node_executions`, Modellaufrufe in Langfuse. Ein Vorschlag wird nach ADR-042 ein Block-Entwurf, der den Freigabeweg seines Geltungsbereichs durchläuft.

Wenn Prompt A nicht das gewünschte Ergebnis bringt, Prompt B aber schon, entsteht daraus automatisch ein Verbesserungsvorschlag — der aber immer menschliche Freigabe braucht.

```
ExecutionLog        ← ein LLM-Call (Rohdaten in Langfuse, nicht dupliziert)
  id, workflow_id, node_id
  model_id, prompt_used
  quality_score: float | null
  success: bool
  langfuse_trace_id           ← Pointer auf vollständigen Trace
  executed_at

FeedbackEvent       ← explizite Rückmeldung auf einen ExecutionLog
  id, execution_log_id
  feedback_type: positive | negative | correction
  alternative_prompt: str | null    ← Prompt B wenn A gescheitert
  alternative_result: str | null
  quality_improvement: float | null ← gemessene Verbesserung
  actor_ref, timestamp

LearningProposal    ← auto-generiert aus FeedbackEvent oder BenchmarkRun
                      braucht IMMER menschliche Freigabe (ADR-004)
  id
  triggered_by_feedback_id:   UUID | null
  triggered_by_benchmark_id:  UUID | null
  proposal_type: new_translation_rule | update_translation_rule | update_routing_rule
  current_block_id:   UUID (block_id der aktuellen Regel)
  proposed_content:   JSONB (Inhalt der neuen Version)
  confidence: float
  status: pending_review | approved | rejected
  reviewed_by_ref: actor_ref | null
  reviewed_at: datetime | null
```

**Lern-Loop:**

```
Prompt A → ExecutionLog (success: false)
  → FeedbackEvent (negative, alternative_prompt: B)
  → Prompt B → ExecutionLog (success: true, quality_improvement: 0.3)
  → FeedbackEvent (positive)
  → System generiert LearningProposal (neue TranslationRule-Version)
  → Reviewer gibt den daraus entstandenen Block-Entwurf frei (Freigabeweg, ADR-042)
  → Neue Version des betroffenen Blocks wird aktiv
  → Alter Block bekommt superseded_by_version = N+1
```

---

#### Domäne 5: Qualität & Benchmark

```
BenchmarkRun          ← benchmark_runs (Migration 028, ADR-043)
  id, mode: drift | ab_adaptation
  status: running | complete | failed
  triggered_by (Actor), review_needed: bool
  started_at, finished_at

BenchmarkMeasurement  ← benchmark_measurements, nur anfügbar
  run_id
  anchor_block_id, anchor_version   ← FK auf context_blocks (block_id, version)
  model, variant: composed | raw
  outcome: measured | unreachable | refused | error
  score (0–1, genau dann gesetzt, wenn measured), scores: JSONB
  output, detail, latency_ms, tokens_in, tokens_out

Report und Freigabe nach Regression   📋 CHY-64
```

CalibrationAnchors sind ContextBlöcke (Typ: `calibration_anchor`) — sie unterliegen damit derselben Versionierung und Governance wie alle anderen Blöcke.

---

#### Domäne 6: Governance & Compliance

```
(MergeRequest, MergeRequestItem, MergeRequestComment entfallen — ADR-042,
 Migration 025. Freigegeben wird die Block-Version selbst, §5.4.)

AuditLog            ← nur anfügbar: UPDATE, DELETE, TRUNCATE lösen einen Fehler aus (Migration 011)
  id, created_at      ← clock_timestamp(), Reihenfolge innerhalb einer Transaktion bleibt (026)
  actor_id, action    ← z. B. block.approved, actor.anonymized (kernel/audit.py)
  target_ref          ← "<typ>:<id>"
  payload: JSONB

TransparencyRecord  ← EU AI Act Art. 50; heute nur für Pilotläufe (Migration 005, 015)
  id, pilot_run_id
  user_informed: bool
  model_used, model_disclosure
  created_at
```

---

### 5.7 Modulstruktur — Zonen und Bounded Contexts (v2.9, ADR-030)

Die Abschnitte 5.1–5.6 beschreiben *logische* Bausteine. Dieser beschreibt, wie sie im
Repositorium liegen. Bis v2.8 fehlte er, und an seiner Stelle stand eine Endpunktliste —
eine von drei Fassungen derselben API, die am 16.08. nachweislich auseinandergelaufen waren.

> **Die Endpunkte stehen nicht mehr hier.** `cohereny/openapi.yaml` wird aus der Anwendung
> erzeugt und von CI gegen sie geprüft (ADR-029); jede geplante Operation trägt dort
> `x-cohereny-status: planned` samt Ticket und antwortet 501. Eine zweite Aufzählung in
> Prosa hat keine Aufgabe, die diese Datei nicht besser erfüllt — sie hatte nur die
> Eigenschaft, unbemerkt falsch werden zu können.

#### Vier Zonen, Abhängigkeiten ausschließlich nach unten

```
Zone 1  Transport      api/v1 · cli · Hintergrundjobs
                       ── gleichberechtigte Aufrufer, keiner bevorzugt ──
                                        │
Zone 2  Contexts       repository │ orchestration │ learning │ pilots
                       ── kein Kontext importiert einen anderen ──
                                        │
Zone 3  Kernel         kernel/ — Identität, Rollen, Erlaubnis, Audit, Geltungsbereiche, Oberflächen
                       ── von allen benutzt, benutzt niemanden ──
                                        │
Zone 4  Infrastruktur  PostgreSQL · Redis · LiteLLM · Langfuse
```

Der Runner (ADR-028) liegt in Zone 2, im Kontext `orchestration`; Komposition und Modellaufruf setzt ihm Zone 1 als Executors ein. `router/` ist kein Kontext: Es deutet die `routing_rule`-Blöcke, die `repository` liefert, und liegt wie `core/` unterhalb von Zone 1.

| Modul | Besitzt | Sprache |
|---|---|---|
| `contexts/repository/` ✅ | `context_blocks`; Komposition und Routing | Block, Version, Freigabestand |
| ~~`contexts/governance/`~~ | — | entfernt am 29.09.2026 (ADR-042): der Freigabeweg ist der Block-Lebenszyklus in `repository` |
| `contexts/orchestration/` ✅ | `workflow_definitions`, `-nodes`, `-connections`, `workflow_runs`, `node_executions`, `loop_counters`, `review_votes`; der Runner (CHY-127) | Workflow, Knoten, Lauf |
| `contexts/learning/` ✅ | `feedback_events`, `learning_proposals`, `benchmark_reports`, `benchmark_runs`, `benchmark_measurements`; Bedeutung der Kalibrieranker (ADR-043) | Rückmeldung, Vorschlag, Anker, Messung, `adaptation_gain` |
| `contexts/pilots/` ✅ | `pilot_runs`, `question_bank`, `run_coverage`, `transparency_records`, `workflow_configs` | Lauf, Aufsicht, Abdeckung |

Komposition und Routing liegen bei `repository`, weil sie **keine eigene Tabelle** haben und
ausschließlich Blöcke lesen. Lese- und Schreibseite über eine Kontextgrenze zu trennen kostet
eine Übersetzungsschicht und bringt nichts.

#### Aufbau eines Kontexts

```
contexts/<name>/
├── schema.py     Pydantic — Transportformen (aus dem aufgelösten domain/)
├── <aggregat>.py Zustandsmaschine und Regeln — ohne IO, ohne HTTP
├── events.py     was geschehen ist (siehe unten)
├── errors.py     Domänenfehler — kein HTTPException
├── store.py      Datenzugriff, der einzige Ort mit SQL
└── service.py    Anwendungsfälle: besitzen die Transaktion, rufen alles obige
```

Die Trennung „Aggregat entscheidet, Anwendungsfall führt aus" ist der Kern. Sie macht die
Regeln ohne Datenbank prüfbar — und genau daran scheiterte die Testabdeckung bisher: bei
54 % in `pilots.py` und 44 % in `deps.py` brauchte jeder Test den vollen Stapel, weil Regel
und Ausführung dieselbe Funktion waren.

**Veröffentlicht sind `service`, `schema`, `errors` — sonst nichts.** Die Wächterregel
`context_encapsulation` erzwingt es, und der erste Verstoß kam prompt aus der
Transportschicht: `api/v1/pilots.py` importierte die Registry des Kontexts, um sie in den
Service hineinzureichen. Richtig ist das Gegenteil — Prozesszustand eines Kontexts geht
niemanden von außen etwas an.

**Ein Kontext darf Module ergänzen, die diese Liste nicht nennt**, wenn er Zustand hat, den
sie nicht abdeckt. `pilots` hat zwei davon: `registry.py` für das, was gerade *in diesem
Prozess* läuft, und `execution.py` für einen Hintergrund-Worker mit Ablaufdatum. Was er nicht
darf, ist sie veröffentlichen.

##### Der erste Schnitt: `pilots` (CHY-104 ✅ 27.08.)

Zuerst dieser Kontext, weil dort das Schlechteste zusammentraf — und weil er die geringste
Kopplung nach außen hat.

| | vorher | nachher |
|---|---:|---:|
| `api/v1/pilots.py` | 846 Zeilen | **209** |
| Funktionen über der Komplexitätsschwelle (C90) | 3 | **0** |
| SQL-Aufrufe in `api/` (gesamtes Projekt) | 43 | **30** |
| `HTTPException` in der Datei | 12 | **0** |
| Abdeckung des Gebiets | 54 % | **69 %** (ohne `execution.py`: 78 %) |
| Tests der Regeln ohne Datenbank | — | **57**, Laufzeit 0,06 s |

Der eigentliche Gewinn steht in keiner dieser Zeilen: **der Aufsichtsentscheid nach EU AI Act
Art. 14 ist jetzt ohne HTTP auslösbar.** Vorher gab es genau einen Weg, einen Banking-Lauf
freizugeben, und der führte über einen HTTP-Client — eine Kontrolle, die man nicht
automatisieren und nur mit vollem Stapel prüfen kann.

Zwei Entscheidungen sind bewusst gegen die Regel getroffen:

- **`execution.py` bekommt kein Abdeckungsziel.** ADR-028 ersetzt die LangGraph-Piloten; der
  Hintergrundteil verschwindet mit ihnen. Er wurde verschoben, nicht überarbeitet. Aufwand in
  Tests für Code mit Ablaufdatum ist Aufwand, der mitgelöscht wird
- **`os.environ.setdefault` bleibt stehen.** Bekannter Befund, als CHY-96 erfasst. Im
  Vorbeigehen mitzuerledigen wäre genau der Nebenschauplatz, den die Projektregel „Ideen zuerst ins Backlog" ausschließt

##### Der zweite Schnitt: `repository` (CHY-105 ✅ 27.08.)

Der Produktkern. `api/v1/blocks.py` trug den gesamten Freigabe-Lebenszyklus, und
der war **ausschließlich über HTTP erreichbar** — weder CLI noch Hintergrundjob
noch der Runner aus ADR-028 konnten einen Block einreichen, freigeben oder
ablehnen. „Was ist freigegeben" ist die zentrale Tatsache dieses Produkts und
hatte genau eine Tür. **ADR-030 Punkt 4 hängt daran:** Orchestrierung soll diese
Schnittstelle mitbenutzen, statt einen zweiten Freigabemechanismus zu bauen.

| | vorher | nachher |
|---|---:|---:|
| `api/v1/blocks.py` | 621 Zeilen | **202** |
| `HTTPException` darin | 9 | **0** |
| Dateien mit SQL gegen `context_blocks` | 4 | **1** |
| Abdeckung des Gebiets | 96 % | **97 %** · `blocks.py` 100 % |
| Regeltests ohne Datenbank | — | **63**, Laufzeit 0,08 s |

**Die Zustandsmaschine gab es nirgends.** `submit`, `withdraw`, `approve` und
`reject` reichten je `to_status`, `allowed_from`, `action` und eine Rolle in
einen gemeinsamen Helfer — die Übergangstabelle existierte nur im Kopf des
Lesers, zusammengesetzt aus vier Aufrufstellen 80 Zeilen auseinander. Jetzt ist
sie eine Tabelle in `block.py`, und damit erstmals **gegen Migration 013
prüfbar**: Der Test parst den plpgsql-Trigger, statt die Kanten zu wiederholen.
Eine Kopie im Test würde mit der Tabelle mitdriften, die sie absichern soll.

Sieben Kanten im Trigger, sechs mit Codepfad. Die siebte — `deprecated → active`
— hat einen eigenen Test, der sie **benennt**: Die Datenbank erlaubt das
Reaktivieren eines stillgelegten Blocks, kein Endpunkt bietet es an. Eine
undokumentierte Lücke zwischen Schema und Code ist der Weg, auf dem der Nächste
zu dem Schluss kommt, das Schema sei falsch.

**Kein `events.py`.** Dieser Kontext hat kein eigenes Wire-Vokabular; was mit
einem Block geschah, steht im Audit-Log des Kernels. Ein Kontext füllt die
Module, die er braucht.

Zwei Abweichungen vom Ticketumfang, beide begründet:

- **Mehr:** `api/v1/audit.py` und `api/v1/complete.py` lasen `context_blocks`
  selbst. Das Ticket nennt sie nicht, aber „besitzt `context_blocks`" heißt, den
  Zugriff zu besitzen — sonst bliebe Kriterium 6 bei vier Dateien
- **Weniger:** `scope_nodes` gehört dem Kernel, wird aber weiter direkt gelesen.
  Als **CHY-121** erfasst statt im Vorbeigehen mitgenommen; die Kernel-Seite
  berührt zusätzlich `api/v1/scopes.py`

##### Die drei letzten: Gerüste statt Schnitte (CHY-106…108 ✅ 27.08.)

`governance`, `orchestration` und `learning` sind angelegt, und in keinem der
drei wurde etwas herausgelöst. Das ist der Befund, nicht eine Abkürzung:

| Kontext | Tabellen seit | Code, der sie anfasst | Routen |
|---|---|---|---|
| `governance` | Migration 001 | **keiner** | 7× 501 |
| `orchestration` | Migration 001 | **keiner** | 10× 501 |
| `learning` | Migration 001/009 | **keiner** | 8× 501 |

> **Stand 27.08.2026, seither überholt:** `governance` wurde am 29.09. entfernt (ADR-042); CHY-65 hat statt eines Merge-Request-Modells den Freigabeweg auf den Block-Lebenszyklus in `repository` gelegt. `orchestration` trägt seit CHY-127 den Runner und hält `human_review` selbst, `learning` deutet seit CHY-67 die Kalibrieranker und besitzt die Messtabellen. Ein Vorschlag aus `learning` wird ein Block-Entwurf in `repository`, kein Merge Request.

Jeder ist ein `git mv` der Schemadatei plus ein dokumentiertes `__init__.py`.
Der Nutzen liegt in der Reihenfolge: **CHY-65 baut das Merge-Request-Modell *in*
`governance`**, und der Runner aus ADR-028 füllt `orchestration`, statt beide
danach aus `api/` hineinzuschieben. Die ersten beiden Schnitte haben je einen
Tag gekostet — der Zuschnitt war der teure Teil davon, nicht das Verschieben.

In die Docstrings geschrieben, was sonst beim Bauen neu entschieden würde:

- **`orchestration`:** `context_compose` greift nach `repository`,
  `human_review` nach `governance` — beides über veröffentlichte Schnittstellen.
  Eines davon dort nachzubauen wäre eine zweite Wahrheit darüber, was komponiert
  und was freigegeben ist
- **`learning`:** Der Kontext besitzt die *Belege* — was beobachtet, was
  vorgeschlagen wurde —, nicht die Entscheidung. Ein Vorschlag wird ein Merge
  Request in `governance` und danach eine Blockversion in `repository`. Von dort
  aus einen Block zu schreiben wäre genau das automatische Überschreiben, das
  die Schleife verhindern soll

**`AuditLogEntry` ist nicht nach `governance` mitgekommen.** Es beschreibt
`audit_log`, das der Kernel besitzt, und lag dort nur, weil MR-Freigaben das
Erste waren, das überhaupt jemand auditiert hat. Ein Schema im falschen Modul
ist der Weg, auf dem eine Tabelle einen zweiten Eigentümer bekommt.

##### Was `api/` an SQL noch hat

Nach CHY-122 (dem Nachzug für `question_bank`, bei CHY-104 übersehen) sind es
**zwölf** Anweisungen, und sie liegen sämtlich in drei Dateien:

| Datei | Tabellen | SQL |
|---|---|---|
| `api/v1/scopes.py` | `scope_nodes`, `scope_memberships` | 8 |
| `api/v1/admin.py` | `actors` | 3 |
| `api/v1/audit.py` | `audit_log` | 1 |

Alle drei Tabellen gehören dem **Kernel**. Kriterium 3 aus ADR-030 — „SQL nur
in `contexts/*/store.py` und `kernel/`" — war damit nicht mehr weit.

> **Erledigt am selben Tag** (CHY-123, CHY-121): **null**. Siehe unten.

##### Was M3b tatsächlich verändert hat

Dreizehn Tickets, an einem Tag abgeschlossen — 36 Tage vor Termin. Die Zahlen
sind gemessen, beide Seiten mit demselben Werkzeugstand:

| | 24.08. | 27.08. |
|---|---:|---:|
| Tests | 626 | **800** |
| SQL-Anweisungen in `api/` | 43 | **0** |
| Zeilen in den drei größten Transportdateien | 846 · 621 · 204 | **209 · 202 · 70** |
| `HTTPException` unterhalb Zone 1 | 24 | **0** |
| Zonenverstöße | 3 | **3** |
| Abdeckung | 44,7 % *(zu klein gemessen)* | **52,4 %** |

**Drei der sechs Kriterien aus ADR-030 sind erfüllt:**

| # | Kriterium | Stand |
|---|---|---|
| 1 | Unabhängigkeit von Frameworks | ✅ 0 |
| 2 | Austauschbare UI | ✅ 0 |
| **3** | **Austauschbare Datenbank** | ✅ **0** — als Test festgehalten, nicht nur als Zahl |
| 4 | Strenge Abhängigkeitsregel | **3** — alle `core/` → `api.config` (CHY-94) |
| 5 | Testbarkeit der Fachlogik | teilweise — `pilots` 57, `repository` 63 Tests ohne Stack |
| 6 | Lose Kopplung | teilweise — `pilot_runs`, `context_blocks`, `question_bank`, `audit_log` je **1** Datei; `actors` **2**, beide im Kernel |

Kriterium 3 hat einen eigenen Test bekommen, und die Begründung gilt allgemein:
**ein Ratchet, das null erreicht, sollte eine Wand werden.** Eine Zahl, die
niemand mehr unterschreiten kann, ist keine Kennzahl mehr, sondern eine Regel.

###### Was der Umbau über die Messung gelernt hat

Zwei Quelllisten — `[tool.coverage.run] source` und `SRC` in
`scripts/quality_gate.py` — enthielten weder `kernel/` noch `contexts/`. Seit
CHY-101 nahm damit **jeder herausgelöste Kontext seine Anweisungen aus dem
Nenner mit**: Ein Umbau, der Code an eine ungemessene Stelle schob, verbesserte
die Zahl.

Das ist derselbe Fehlermodus wie in ADR-017 (ungepinnte Linter tauschen das
Lineal mitten in der Pipeline aus) — dort ändert sich das Messgerät, hier der
Messbereich. Beide Male wird die Differenz dem Commit zugeschrieben, der
zufällig als Nächstes kam.

Gehalten hat den Umbau deshalb nicht die Abdeckungszahl, sondern das Verfahren:
**jede Absicherung mit einer Gegenprobe belegt**, die sie entfernt und Rot
erwartet. Zweimal war eine Gegenprobe grün, obwohl sie es nicht hätte sein
dürfen — beide Male wegen eines Fehlers in der Probe, nicht im Test. Eine Probe,
die aus dem falschen Grund grün ist, ist genauso wertlos wie ein Test.

##### Die Verwaltungsoberfläche des Kernels (CHY-123, 27.08.)

Mit CHY-123 sind `actors` und `audit_log` beim Kernel angekommen.
`api/v1/admin.py` fällt von 132 auf **50** Zeilen, `api_db_calls` von 12 auf
**8** — und was bleibt, liegt vollständig in `api/v1/scopes.py`.

| neu im Kernel | war in |
|---|---|
| `identity.anonymize_actor` | `api/v1/admin.py` (DSGVO Art. 17, vollständig) |
| `identity.set_role` | `cli/main.py` (Bootstrap des ersten Admins) |
| `audit.read_entries` | `api/v1/audit.py` (Abfrage selbst zusammengebaut) |
| `kernel/schema.py` | `domain/actor.py` |

Der Einwand ist derselbe wie beim Freigabe-Lebenszyklus in CHY-105: **Eine
Rechtspflicht, die nur ein HTTP-Client erfüllen kann, ist eine Pflicht mit genau
einer Tür.** Eine Löschung nach Art. 17 muss aus einem Skript auslösbar sein.

Die CLI trug außerdem eine **handgeschriebene Kopie der fünf Rollennamen** —
die dritte Liste im Baum, obwohl `kernel/roles.py` `ROLE_NAMES` veröffentlicht.
Ein Test hält per AST fest, dass niemand außerhalb des Kernels sie noch einmal
aufzählt.

###### Eine Entscheidung, die absichtlich nicht in den Kernel gewandert ist

Wer einen Audit-Eintrag lesen darf, lautet: *reviewer oder höher — oder Autor
des betreffenden Blocks*. Die zweite Hälfte ist nötig, weil der Grund einer
Ablehnung nur im Audit-Payload steht; ohne sie wäre eine Review für den
abgelehnten Autor unbeantwortbar (CHY-32).

Die Autorschaft gehört dem Kontext `repository`. Der Kernel ist Zone 3 und
**benutzt niemanden** — er kann also nicht fragen. Die Zusammensetzung gehört
damit in den Transport, die einzige Schicht, die zugleich `kernel/` und
`contexts/` sehen darf.

Das ist kein Rest, den man später noch aufräumt, sondern die Regel: **Zone 1 ist
der Ort, an dem über Kontextgrenzen hinweg zusammengesetzt wird.** Wo eine
Entscheidung zwei Zonen braucht, gehört sie nach oben, nicht nach innen.

#### Der Kernel — der Schnitt zwischen *wer* und *darf*

`core/authz.py` hieß nach seinem kleinsten Teil. Von 219 Zeilen entfielen 13 auf
Autorisierung; der Rest war Identitätslebenszyklus — der Docstring sagte es selbst:
*„Role checks, **and the one place an `actors` row comes into existence**"*. Unverändert
verschoben, wäre der Fehlname in die neue Struktur gewandert.

> **Und genau das ist bei CHY-101 passiert.** Der Umzug schob die Datei am Stück nach
> `kernel/access.py` und vergab den Namen `identity.py` an etwas anderes. Aufgefallen ist es
> erst bei der Durchsicht am 27.08., als Dokument und Code nebeneinander lagen: `access.py`
> hatte 222 Zeilen und zehn SQL-Anweisungen, wo hier „kein SQL" stand. Korrigiert mit
> **CHY-120**.
>
> Diese Fassung hat außerdem einen Selbstwiderspruch der vorigen aufgelöst. Die Tabelle legte
> `role_from_groups` nach `access.py`, zwei Absätze weiter stand, die Regel über den fehlenden
> `groups`-Claim gehöre zu `identity.py` — und diese Regel *ist* `role_from_groups`. Tabelle
> und Fließtext meinten dieselbe Funktion und verorteten sie verschieden.

| Datei | Inhalt | SQL |
|---|---|---|
| `kernel/roles.py` | Das Rollenvokabular — einmalig, aus `domain/roles.py` | nein |
| `kernel/access.py` | `ResolvedActor`, `require(actor, minimum)` — „darf dieser Akteur das?" | **nein** |
| `kernel/identity.py` | `Caller`, `role_from_groups`, `resolve_actor`, Bereitstellung, Rollenprojektion | ja |
| `kernel/audit.py` | Append-only Nachweis (ADR-016) | ja |
| `kernel/errors.py` | Domänenfehler — u. a. `AccessDenied`, `ActorGone`, `ActorStateInconsistent`, `ScopeNotFound`, `ScopeLoosensPrivacy`, `LastAdminProtected` | nein |
| `kernel/scopes.py` | Der Geltungsbereichsbaum: `scope_nodes`, `scope_memberships`, Vorfahrenkette (CHY-121) | ja |
| `kernel/schema.py` | Transportformen für Actors und Geltungsbereiche (CHY-121, CHY-123) | nein |
| `kernel/surfaces.py` | Die vier Oberflächen, ihre Audiences und Issuer (ADR-031) | nein |

Dass `access.py` SQL-frei ist, ist kein Zufall, sondern die Bedingung: Rollenlogik wird damit
ohne Datenbank testbar. `tests/test_access.py` führt das vor — jeder Fall dort baut einen
`ResolvedActor` von Hand, ohne Pool, ohne `await`. Ein Test in derselben Datei prüft, dass
das so bleibt.

`ResolvedActor` liegt in `access.py` und nicht bei der Identität, weil sein einziges Verhalten
`has_at_least` eine Erlaubnisfrage ist. Die Abhängigkeit zeigt damit in eine Richtung:
**identity → access**, nie zurück.

An der Aufrufstelle sind es zwei Schritte statt einem:

```python
actor = await resolve_actor(pool, caller)
require(actor, "reviewer")
```

Bewusst nicht hinter einem Bequemlichkeits-Wrapper versteckt. Die beiden Fragen sind
verschieden, und wer den Code liest, soll das sehen.

Die Unterscheidung, die dabei nicht verlorengehen darf: ein Token **ohne** `groups`-Claim
bedeutet „der IdP hat nichts zu sagen", nicht „der Aufrufer gehört zu nichts" — die
gespeicherte Rolle bleibt stehen. Sie gehört zu `identity.py`, weil sie über die *Projektion*
entscheidet, nicht über die Erlaubnis, und sie wird von `role_from_groups` getragen.

#### `Caller` — ein Protokoll, keine Klasse

Bis CHY-101 importierte die Rollenprüfung `api.deps.CurrentUser` direkt. Das war einer der
neun Zonenverstöße und der Grund, warum eine Frage nach einer Erlaubnis die gesamte
Transportschicht hereinzog — über eine einzige Typannotation.

`kernel/identity.py` sagt jetzt nur noch, *was* es braucht: `sub`, `username`, `groups`. Wer
aufruft, bringt etwas mit, das passt. `CurrentUser` erfüllt das, **ohne davon zu wissen** —
`api/deps.py` wurde für den Umzug nicht angefasst. Das ist der praktische Wert struktureller
Typisierung: die Umkehr der Abhängigkeit kostet zur Laufzeit nichts.

Eine CLI, ein Hintergrundjob oder der Runner (ADR-028) erfüllen dasselbe Protokoll. Keiner
von ihnen sollte einen HTTP-Begriff konstruieren müssen, um zu fragen, ob jemand etwas darf.

#### Domänenfehler statt Statuscodes

`core/authz.py` warf dreimal `HTTPException`. Damit steckte die Wahl eines Statuscodes in den
Regeln, und die Regeln waren außerhalb einer HTTP-Anfrage nicht benutzbar: eine CLI, die eine
Rolle prüfen wollte, bekam ein 403-Objekt zurück, mit dem sie nichts anfangen konnte.

`kernel/errors.py` trägt die Fälle als Domänenfehler. Die Zuordnung auf Statuscodes liegt in
`api/main.py`, in einem Handler — **an der einzigen Stelle, an der HTTP existiert**:

| Fehler | Code | wann |
|---|---|---|
| `AccessDenied` | 403 | Rolle reicht nicht |
| `ActorGone` | 403 | Zeile verschwand zwischen Auflösen und Sperren |
| `ActorStateInconsistent` | 409 | anonymisierte Zeile trägt noch `idp_sub` — verboten durch Migration 010 |

Ein unbekannter Kernelfehler wird **500**, nicht stillschweigend 403. Die Vorgabe ist bewusst
die pessimistische: ein neuer Fehlertyp, den niemand eingetragen hat, ist ein
Programmierfehler und darf nicht als plausible Ablehnung durchgehen.
`tests/test_kernel_errors.py` zählt die Familie ab, damit das nicht unbemerkt eintritt.

#### Erweiterbarkeit des Rechtemodells — was trägt und was nicht

| | |
|---|---|
| **Trägt** | Eine neue Rolle ist eine Zeile im `StrEnum`. `ROLE_ORDER` und `GROUP_ROLE_MAP` leiten sich ab; `actors.role` speichert den Namen, nicht den Rang — Einfügen zwischen bestehende Stufen ist gefahrlos |
| **Trägt nicht** | `access.require(actor, minimum)` kennt **keinen Geltungsbereich**. „Reviewer in Scope A, Consumer in Scope B" ist nicht ausdrückbar |
| **Trägt nicht** | Die Rangfolge ist linear. „Darf anonymisieren, aber nicht freigeben" passt nicht hinein — Admin kann per Konstruktion alles, was Reviewer kann |

Der Bedarf ist älter als die Lücke: `scope_memberships` steht **mit `role`-Spalte in
Migration 001**, und inzwischen werden Mitgliedschaften gespeichert (`kernel/scopes.py`, `/v1/scopes/{scope_id}/members`) und für Laufinhalte von der Datenbank durchgesetzt (ADR-047). Dazu ADR-011
(scope-abhängige Governance) und `review_role: approver | four_eyes` in der
Workflow-Konfiguration.

**Entschieden:** Das Modell wird hier *nicht* gebaut — das ist CHY-92, eine Entscheidung und
keine Umsetzung. Vorgesehen war, nur die Naht zu legen — `access.require(actor, minimum, *, scope=None)`
sollte den Geltungsbereich entgegennehmen und vorerst ignorieren. Gebaut ist sie nicht: `require`
nimmt `(actor, minimum)`; kommt das Modell, ändern sich die Aufrufstellen. *Stand 06.11.2026:* Für
Laufinhalte ist das Modell gekommen, nicht über `require`, sondern in der Datenbank (ADR-047);
`require` nimmt weiter `(actor, minimum)`.

#### Domain Events — als Rückgabewert, nicht als Bus

Die erste Fassung von ADR-030 strich Domain Events mit dem Argument, ein Ereignisbus ohne
Abonnenten sei Zeremonie. Das Argument gilt für den Bus und ist für die Ereignisse falsch:
**beide gibt es längst, nur implizit.**

- `kernel/audit.py` führt **26 Konstanten in Vergangenheitsform** (Stand 05.11., mit `block.edited` und
  `actor.service_account_changed`) — `block.approved`, `block.submitted`, `block.rejected`,
  `pilot.oversight_decided` … Das *ist* der Katalog. `block.created` wird seit CHY-171 geschrieben;
  vorher stand es im Katalog, und nichts schrieb es
- `bump_scope_generation` reagiert auf den Übergang nach und aus `active` — seit CHY-105 an einer
  Stelle (`_invalidate_if_composed`), aber abgeleitet aus einer Regel über Statuspaare statt aus einem Ereignis
- **CHYUI-2** ist geplant: ntfy-Push bei Freigabe-Gates
- Vor allem: veröffentlichte Schnittstellen tragen *Abfragen*. Sie tragen keine
  *Benachrichtigung* — das Repository soll nicht wissen, wer sich für eine Freigabe interessiert

Vorgesehen ist deshalb, dass das Aggregat zurückgibt, **was geschehen ist**, nicht was zu tun ist (Entwurf, im Code noch nicht umgesetzt):

```python
@dataclass(frozen=True)
class BlockApproved:
    block_id: UUID; scope_id: UUID; frm: str; to: str; version: int
```

Der Anwendungsfall leitet daraus die Audit-Nutzlast ab, die Cache-Entscheidung und später die
Benachrichtigung. Ein
Verbraucher kommt dann *hinzu*, statt dass sich eine Signatur ändert.

**Draußen bleiben** Registrierung, asynchroner Versand, Outbox und Eventual Consistency
zwischen Kontexten. Die braucht es erst, wenn ein Verbraucher außerhalb des Requests läuft.
Eine Verlockung sei benannt: `audit_log` ist append-only und trägt jede Nutzlast, ist also
technisch eine Outbox — aber es ist ein **Compliance-Nachweis**. Als Integrationskanal
mitbenutzt, entschiede die Aufbewahrungsfrist über die Zustellgarantie. Wenn asynchron
nötig wird: eigene Tabelle.

#### Die Grenze wird erzwungen

`scripts/architecture.py` prüft sie, nicht `quality_gate.py` — der Wächter ist mit CHY-103
ein eigenes Werkzeug geworden, weil er Datei und Zeile nennen muss und nicht nur eine Zahl.
Die Zählung geht zusätzlich in die Grundlinie, damit ein Rückschritt auffällt.

Er entstand **vor** dem ersten Umzug. Ein Wächter, der danach geschrieben wird, findet nur,
was man ohnehin gerade gebaut hat.

| Regel | verlangt |
|---|---|
| `inward_only` | Nichts unterhalb von Zone 1 importiert `api` |
| `framework_free` | Kein FastAPI, kein Starlette unterhalb von Zone 1 |
| `http_below_transport` | Kein `HTTPException` unterhalb von Zone 1 — als Symbol geprüft, nicht als Import |
| `context_isolation` | Kein Kontext importiert einen anderen |
| `kernel_uses_nothing` | `kernel/` importiert keinen Kontext |
| `context_encapsulation` | Zone 1 erreicht nur `service`, `schema`, `errors` eines Kontexts |

**Die letzte Regel liest `api/`** — und bis CHY-101 tat der Wächter das nicht. Er lief
ausschließlich unterhalb der Transportschicht, weshalb ein Adapter mit
`from contexts.pilots.store import …` unbemerkt geblieben wäre. Die Regel stand da und
konnte nicht greifen. Ein eigener Test sichert seither ab, dass die Schleife nicht wieder
verschwindet.

Jede Regel wird gegengeprüft: Verstoß herstellen, Meldung erwarten — und für
`context_encapsulation` zusätzlich der erlaubte Fall, der **nicht** melden darf. Eine Regel,
die 0 meldet, sagt sonst nicht, ob sie sauber ist oder wirkungslos.

**Stand 27.08.2026: drei Zonenverstöße.** Alle drei sind `core/db`, `core/redis` und
`core/tracing`, die `settings` für Verbindungsdaten lesen — Zone-4-Belange, die noch keinen
Platz haben. Vor CHY-101 waren es neun.


---

## 6. Laufzeitsicht

> **Status:** 6.2 und 6.9 sind implementiert (✅), ebenso 6.5 (Altbestand: die LangGraph-Piloten werden auf den eigenen Runner gezogen, ADR-028); 6.1 ist bis auf die Adaption gebaut (🔧 → M6); 6.3 ist **Ziel-Szenario** (📋 → M6); 6.4 ist überholt (ADR-042); 6.6–6.8 sind Referenz-Designs — nicht als Live-PoC umgesetzt (siehe Whitepaper §13).

### 6.1 Szenario (🔧): Agentic Workflow Step mit step-spezifischem Kontext

> **Stand 01.11.2026:** Gebaut ist alles bis auf die Adaption: `/v1/complete` löst die Routing-Regel des Geltungsbereichs auf, komponiert mit Cache, ruft das Modell über LiteLLM mit Rückfallkette und schreibt einen Langfuse-Trace. Die Transformation je Modell kommt mit der Adaptions-Engine (📋 → M6, CHY-204). `/v1/complete` liefert ohne Freigabe-Gate (→ M6, CHY-199); für Ergebnisse, die freigegeben werden müssen, ist der Workflow-Lauf in §6.9 der Weg.

```mermaid
sequenceDiagram
    participant WF as Aufrufer\nz.B. fremde Workflow-Engine
    participant GW as API Gateway
    participant RT as Router
    participant COMP as Kompositions-\nEngine
    participant PG as PostgreSQL
    participant RD as Redis
    participant LM as LiteLLM
    participant LF as Langfuse

    WF->>GW: POST /v1/complete\ntask_type + scope_id + Prompt
    GW->>GW: OIDC Token validieren\nRolle aus dem Gruppen-Claim
    GW->>RT: Routing-Regel fuer task_type\nim Geltungsbereich aufloesen
    RT->>PG: Regel des tiefsten Scopes laden
    RT->>RT: Kandidaten gegen die\nDatenklasse pruefen
    GW->>COMP: Kontext komponieren
    COMP->>PG: Scope-Kette laden\n(auch bei Cache-Hit)
    COMP->>RD: Cache-Lookup Composed Context
    alt Cache-Hit
        RD-->>COMP: Cached Context
    else Cache-Miss
        COMP->>PG: Bloecke laden\nflach nach tief
        PG-->>COMP: Kontext-Bloecke
        COMP->>COMP: Komponieren\nPrivacy-Ratchet pruefen
        COMP->>RD: Context cachen
    end
    COMP-->>GW: Composed Context
    Note over GW,LM: 📋 → M6: Prompt je Modell transformieren (CHY-204)
    GW->>LM: Prompt + erstes Modell\nder Rueckfallkette
    alt Modell nicht erreichbar
        GW->>LM: naechster Kandidat
        GW->>PG: AuditLog routing.fell_back
    end
    LM-->>GW: Response
    GW->>LF: Trace mit Bausteinen\nsamt Version (optional)
    GW-->>WF: Response + Metadaten
```

### 6.2 Szenario (✅): Freigabeweg — Block einreichen und freigeben

> **Neu gefasst am 01.11.2026 (CHY-197):** Bis v2.44 stand hier „Neue Übersetzungsregel“ mit Endpunkten, einer Rolle „Moderator“ und Merge-Request-Nummern, die es nicht gibt. Übersetzungsregeln gehören zur Adaption (📋 → M6). Gezeigt ist jetzt der gebaute Freigabeweg nach ADR-011 und ADR-042, der für jeden Blocktyp gilt.

```mermaid
sequenceDiagram
    participant C as Contributor
    participant GW as API Gateway
    participant REP as Kontext-Repository\n(Freigabeweg)
    participant PG as PostgreSQL
    participant RD as Redis
    participant R as Reviewer

    C->>GW: POST /v1/blocks\nEntwurf + Bearer Token
    GW->>REP: Block anlegen
    REP->>PG: INSERT status=draft, version=1
    C->>GW: POST /v1/blocks/{block_id}/submit
    GW->>REP: Rolle pruefen, Uebergang submit
    REP->>PG: status=pending_review\nAuditLog block.submitted
    R->>GW: POST /v1/blocks/{block_id}/approve
    GW->>REP: Rolle reviewer pruefen
    REP->>PG: Zeile sperren (FOR UPDATE)\nVersion pruefen
    REP->>REP: Privacy-Ratchet pruefen\nRouting-Regel gegen Modell-Obergrenzen
    REP->>PG: status=active\nAuditLog block.approved\n(dieselbe Transaktion)
    REP->>RD: Scope-Generation erhoehen\n(Composed Context verfaellt)
    GW-->>R: 200 + Block
```

- **Direkte Geltungsbereiche** (Projekt, Unterprojekt, persönlich): Ein Block ist beim Schreiben aktiv, ohne Einreichen und Freigabe (ADR-011) — außer einem Datenfluss-Baustein, der eingereicht wird und auf eine zweite Person wartet (ADR-045). Höher hinaus führt `promote` — die Live-Version als Entwurf in einen strengen Geltungsbereich darüber.
- **Vier Augen:** Einen Datenfluss-Baustein — Routing-Regel mit einem Modell unterhalb `critical`, gelockerte Datenschutzregel — gibt nur frei, wer in dieser Runde nichts geschrieben hat; abgewiesen wird mit `403`. Ausnahme ist der Einzelbetrieb, gekennzeichnet als `four_eyes: self_approved` (ADR-045). Für anderes Material werden Autor und Freigebende aufgezeichnet, aber nicht verglichen.
- **Benachrichtigung der Reviewer:** noch nicht gebaut (→ M7, CHYUI-2). Ausstehende Freigaben zeigt `GET /v1/blocks` mit Filter auf `pending_review`.

### 6.3 Szenario (📋 → M6): Benchmark-Lauf mit Regression und Human Oversight

> **Seit ADR-044 (03.11.2026):** Der Cron-Auslöser im Diagramm gilt wieder — als Zeitplan, einstellbar, Vorgabe nächtlich (CHY-181); daneben starten Änderungen und die CLI denselben Lauf (CHY-217, CHY-68). Gebaut ist davon noch nichts, ebenso der Ablauf danach (→ M6, CHY-68, CHY-64).

```mermaid
sequenceDiagram
    participant CRON as Cron\n02:00 Uhr
    participant BM as Benchmark\nScheduler
    participant LM as LiteLLM
    participant PG as PostgreSQL
    participant ADMIN as Admin\nHuman Oversight

    CRON->>BM: Benchmark-Run starten
    BM->>PG: Aktive Benchmarks laden
    loop Fuer jeden Benchmark
        BM->>PG: Test-Prompts + CalibrationAnchors laden
        loop Fuer jedes registrierte Modell
            BM->>LM: Prompt senden
            LM-->>BM: Response + Latenz
            BM->>BM: Qualitaet bewerten
        end
    end
    BM->>PG: Vorperiode laden
    BM->>BM: Regression pruefen
    alt Keine Regression
        BM->>PG: benchmark_runs\nreview_needed=false
    else Regression erkannt
        BM->>PG: benchmark_runs\nreview_needed=true
        BM->>ADMIN: Benachrichtigung\nRegression gefunden
        ADMIN->>BM: POST /v1/benchmarks/{run_id}/approve\nFreigabe oder Ablehnung
        BM->>PG: Freigabe auditiert
    end
```

### 6.4 Szenario (überholt): Merge Request für Kontext-Block

> **Überholt durch ADR-042 (29.09.2026).** Einen eigenen Merge-Request-Datensatz gibt es nicht. Freigegeben wird die Block-Version selbst: `draft → pending_review → active` in strengen Scopes, sofort aktiv in direkten Scopes (ADR-011). Das Diagramm zeigt den früheren Entwurf und bleibt als Beleg dafür stehen, warum die Entscheidung nötig war.

```mermaid
sequenceDiagram
    participant C as Contributor
    participant GW as API Gateway
    participant GOV as Governance\nEngine
    participant PG as PostgreSQL
    participant MOD as Moderator

    C->>GW: POST /v1/context/blocks/merge-requests\nGeaenderte Bloecke + Ziel-Scope
    GW->>GOV: MR erstellen
    GOV->>PG: Basis-Bloecke laden
    GOV->>GOV: Block-Level Diff berechnen
    GOV->>GOV: Konflikte pruefen
    GOV->>PG: MR speichern\nstatus=pending_review
    GOV-->>C: MR-ID + Diff-URL
    MOD->>GW: GET /v1/context/blocks/merge-requests/mr_id
    GW-->>MOD: Diff-Ansicht
    MOD->>GW: POST review\napprove
    GW->>GOV: MR mergen
    GOV->>PG: Bloecke in Ziel-Scope\nneu versionieren
    GOV->>PG: AuditLog + MR status=merged
    GOV-->>MOD: 200 OK
```

### 6.5 Szenario (✅ implementiert): Pilot Run mit SSE-Streaming (Study PoC)

Der Study-Pilot war der erste Agentic Workflow, den Cohereny ausgeführt hat. Er ist Altbestand: Die LangGraph-Piloten entstanden vor dem eigenen Runner und werden auf ihn gezogen (ADR-028).
Er läuft als LangGraph-Pipeline in einem Background-Thread und streamt Fortschrittsereignisse
per SSE an die Referenz-UI. Das folgende Szenario zeigt den vollständigen Ablauf.

```mermaid
sequenceDiagram
    participant UI as Referenz-UI\n(Browser + Astro BFF)
    participant API as cohereny-api\nFastAPI
    participant BG as Background Thread\nThreadPoolExecutor
    participant LG as LangGraph\nStudy Pilot
    participant LM as LiteLLM

    UI->>API: POST /v1/pilots/runs\n{ pilot_name, input_file }
    API->>API: INSERT pilot_runs (status=pending)
    API->>BG: run_in_executor(_bg_run, cancel_event)
    API-->>UI: 201 { id, status=pending }

    UI->>API: GET /v1/pilots/runs/{id}/stream
    Note over UI,API: SSE-Verbindung offen (EventSource)

    BG->>API: UPDATE status=running
    loop 9 LangGraph Nodes
        BG->>LG: graph.stream(updates)
        loop Pro Batch (node_summarize / node_extract_facts)
            LG->>LM: llm.invoke(prompt)
            LM-->>LG: response
            LG->>BG: try_emit(node_progress)
            BG->>API: call_soon_threadsafe(_push_event)
            API-->>UI: data: {type:node_progress, node, details}
        end
        LG-->>BG: node_complete event
        BG->>API: call_soon_threadsafe(_push_event)
        API-->>UI: data: {type:node_complete, node, elapsed_ms, details}
    end

    BG->>API: UPDATE status=complete, result_json
    BG->>API: _push_event(done)
    API-->>UI: data: {type:done, lernziel_erreicht, facts_count}
    API-->>UI: data: {type:eof}

    alt User klickt Cancel
        UI->>API: DELETE /v1/pilots/runs/{id}
        API->>BG: cancel_event.set()
        BG->>LG: check_cancel() → WorkflowCancelledError
        LG->>LM: httpx-Socket schliesst → TCP-Reset → Ollama stoppt
        BG->>API: UPDATE status=failed
        API-->>UI: data: {type:error, message}
    end
```

**Besonderheiten dieser Architektur:**

- **Reconnect-Safety:** Events liegen je Lauf als Liste im Prozess-Register (`RunRegistry`), nicht in einer Queue. Jede neue SSE-Verbindung (Page Reload) spielt die History ab cursor=0 ab; erst nach `eof` wird der Speicher freigegeben. Am Laufende werden die Events in `pilot_runs.events_json` geschrieben; einen Lauf, den der Prozess nicht kennt (Neustart, später verbundener Client), spielt der Server von dort ab (CHY-58).
- **Thread-local Callbacks:** `set_emit` / `set_cancel_event` speichern Callbacks in thread-local Storage, damit LangGraph-Nodes ohne Signaturänderung `try_emit()` und `check_cancel()` aufrufen können.
- **BFF-Pattern:** Die Referenz-UI (Astro SSR) proxiert alle API-Calls — der Browser spricht nie direkt mit der FastAPI. Angemeldet wird über den OIDC-Client der Oberfläche `author` (Authorization Code mit PKCE, ADR-031); Zugriffs- und Refresh-Token liegen in der serverseitigen Sitzung, der Browser hält nur ein httpOnly-Cookie. Jeder Aufruf trägt damit `aud = cohereny.author` und den angemeldeten Nutzer (CHYUI-9).

### 6.6 Szenario (📋 Referenz-Design): Banking Due Diligence mit hybridem Routing (Ausrichtung DORA)

> **Stand 01.11.2026:** Ablauf und Aufsichts-Gate gibt es als LangGraph-Pilot (Altbestand, `pilots/banking/`, `POST /v1/pilots/runs/{run_id}/oversight`), mit fest konfigurierten Modellen je Knoten; die Ablösung auf den eigenen Runner läuft (M5/M6, CHY-172, CHY-178). Das hybride Routing ist nicht gebaut, und das Diagramm stammt aus der Zeit vor ADR-041: Heute gehört die Datenklasse dem Geltungsbereich, nicht dem Knoten. Der Weg dorthin sind zwei Workflows in zwei Geltungsbereichen (siehe „Hybrides Routing“ unten); ausführbare Sub-Workflows dafür kommen nach 1.0 (CHY-205).

Jahresabschlüsse und Due-Diligence-Dokumente enthalten hochsensible Rohdaten, die das Unternehmen
nicht verlassen dürfen. Gleichzeitig erfordert komplexes Risk-Reasoning Modellstärke, die lokal
nicht verfügbar ist. Der Privacy Router entscheidet dynamisch: Rohdaten-nahe Verarbeitung bleibt
lokal, Reasoning-intensive Steps können auf Cloud-Modelle geroutet werden — die Routing-Entscheidung
ist vollständig auditiert (DORA Art. 9–11; MaRisk AT 4.3.4 Modell-Governance). Hinweis: BAIT ist für
DORA-pflichtige Institute seit 17.01.2025 aufgehoben; MaRisk AT 7.2 enthält seit der 9. Novelle
(RS 06/2026, in Kraft seit 30.06.2026) keine IT-Anforderungen mehr — primäre Referenz ist DORA, ergänzt um die
BaFin-Orientierungshilfe KI (18.12.2025).

```mermaid
sequenceDiagram
    participant UI as Referenz-UI
    participant API as cohereny-api
    participant ROUTER as Privacy Router\n(Scope: org/bank)
    participant LM_L as LiteLLM\nCompute lokal
    participant LM_C as LiteLLM\nCompute Cloud (Claude API)
    participant PG as PostgreSQL\nAudit + Checkpoint
    participant ADMIN as Human Oversight\n(Kreditanalyst)

    UI->>API: POST /v1/pilots/runs\n{ pilot_name: banking, input_file }
    API->>PG: INSERT pilot_runs (status=pending)
    API->>API: Background Thread starten

    Note over API,ROUTER: Scope org/bank erbt globale Risiko-\nund Compliance-Vorgaben

    loop 5 lokale Analyse-Nodes
        API->>ROUTER: Kontext anfordern\n(ingest / extract_kpis / calculate_ratios\n / detect_anomalies / risk_gateway)
        ROUTER->>ROUTER: privacy_safe=true\n→ immer lokal
        ROUTER-->>API: Kontext + Modell: local-medium
        API->>LM_L: Prompt (Rohdaten bleiben On-Premise)
        LM_L-->>API: Ergebnis
        API->>PG: AuditLog: node, modell, routing_decision
    end

    API->>ROUTER: Kontext anfordern\n(classify_risk / compliance_check)
    ROUTER->>ROUTER: Rohdaten extrahiert\n→ Cloud zulässig\nprivacy_safe=false
    ROUTER-->>API: Kontext + Modell: cloud (Claude)
    API->>LM_C: Risk-Reasoning Prompt
    LM_C-->>API: Risikoklasse + Begründung

    Note over API,ADMIN: human_oversight_required=true\nhardcoded — keine Ausnahme

    API->>ADMIN: Benachrichtigung\nFreigabe erforderlich (EU AI Act Art. 14)
    ADMIN->>API: POST /v1/pilots/runs/{id}/oversight\ndecision: approved/rejected
    API->>PG: AuditLog: pilot.oversight_decided (Actor, Entscheidung, Zeitpunkt)
    API->>API: summarize Node
    API->>PG: UPDATE status=complete, result_json
    API-->>UI: SSE done event + Routing-Statistik
```

> **`routing_decision` je Knoten ist Entwurf, nicht Umsetzung** (Stand 07.09.2026,
> CHY-87). Umgesetzt ist `routing.fell_back`, und **nur beim Fallback**: ein Eintrag
> je Aufruf ersäufte das Protokoll und versteckte die Ausnahme, die er zeigen soll.
> Sollte der Banking-Ablauf gebaut werden, ist zu entscheiden, ob eine
> Routing-Entscheidung *ohne* Ausweichen einen eigenen Nachweis braucht — bei einem
> Übergang lokal/Cloud spricht mehr dafür als bei der Wahl zwischen zwei lokalen
> Modellen (ADR-035).


**Besonderheiten:**

- **Hybrides Routing nach ADR-041:** Die Datenklasse gehört dem Geltungsbereich, und ein Workflow hat genau einen. Die Rohdaten verarbeitet ein Workflow in einem Geltungsbereich der Klasse `confidential` (nur lokale Modelle); die abstrahierten Kennzahlen gehen als Eingabe an einen zweiten Workflow in einem Bereich, in dem ein gehostetes Modell zugelassen ist. Die Herabstufung ist eine ausdrückliche Entscheidung des Betreibers, keine Eigenschaft eines Knotens.
- **Audit-Trail für DORA:** Freigabeentscheidungen und Rückfälle im Routing stehen mit Zeitstempel und Actor-Ref im Audit-Log, das verwendete Modell je Knoten und Durchlauf in `node_executions`; beides wird in PostgreSQL persistiert.
- **Drift-Erkennung (ADR-044):** Der Benchmark nach Zeitplan und bei Änderungen prüft, ob Provider-Updates die Präzision der Risikoanalysen beeinflussen → `review_needed` bei Regression, kein automatisches Rollout (→ M6, CHY-68/CHY-181/CHY-64).

### 6.7 Szenario (📋 Referenz-Design): Healthcare Arztbrief-Strukturierung (Datenklasse critical, Human Oversight)

> **Stand 01.11.2026 (ADR-043):** Die Fakten-Validierung gegen Kalibrieranker im Diagramm ist überholt. Kalibrieranker sind Testfälle des Benchmarks: Sie messen jedes Modell, bevor es eingesetzt wird, und prüfen keine Ergebnisse im laufenden Workflow. Angehalten wird jedes Ergebnis am `human_review`-Knoten.

Arztbriefe und Epikrisen für die elektronische Patientenakte (ePA) enthalten besonders
schutzwürdige Daten (DSGVO Art. 9). Jede KI-generierte Ausgabe erfordert Arzt-Freigabe
bevor sie in die ePA eingetragen wird (MDR, EU AI Act Art. 14).

```mermaid
sequenceDiagram
    participant ARZT as Arzt\n(Contributor)
    participant API as cohereny-api
    participant ROUTER as Privacy Router\n(Bootstrap-Template: healthcare)
    participant LM_L as LiteLLM\nlokal (Datenklasse critical)
    participant CA as CalibrationAnchors\nMedizinisches Faktenset
    participant PG as PostgreSQL
    participant ARZT2 as Arzt\n(Human Oversight)

    ARZT->>API: POST /v1/pilots/runs\n{ pilot_name: healthcare, input_file: arztbrief.pdf }
    API->>PG: INSERT pilot_runs (status=pending)

    Note over API,ROUTER: Bootstrap-Template healthcare:\nDatenklasse critical\n→ nur lokale Modelle

    loop Strukturierungs-Nodes
        API->>ROUTER: Kontext anfordern
        ROUTER->>ROUTER: healthcare scope:\nDatenklasse critical
        ROUTER-->>API: Kontext + Modell: local-medium
        API->>LM_L: Strukturierungs-Prompt\n(Daten verlassen nie das System)
        LM_L-->>API: Strukturierter Arztbrief-Abschnitt
        API->>PG: AuditLog: node, modell=local
    end

    API->>CA: Fakten-Validierung\ngegen medizinisches Faktenset
    CA-->>API: Validierungsergebnis\n(CalibrationScore)

    alt CalibrationScore < Schwellwert
        API->>PG: UPDATE status=failed\nerror: Halluzinationsverdacht
        API-->>ARZT: SSE error event\nmanueller Review erforderlich
    else CalibrationScore OK
        Note over API,ARZT2: requires_human_oversight=true\nhardcoded — kein Bericht ohne Arzt-Freigabe

        API->>ARZT2: Benachrichtigung\nFreigabe für ePA-Eintrag
        ARZT2->>API: POST approve\nÄrztliche Freigabe + Kommentar
        API->>PG: AuditLog: arzt_ref, Entscheidung, zeitstempel
        API->>API: Actor-Refs der Freigebenden\nPatientendaten: Platzhalter vor dem Modell
        API->>PG: UPDATE status=complete\nepa_ready=true
        API-->>ARZT: SSE done event
    end
```

**Besonderheiten:**

- **Privacy-by-Design:** Der Geltungsbereich `healthcare` ist als `critical` eingestuft (oder gar nicht, was als `critical` gilt, ADR-041) — kein gehostetes Modell bekommt seine Daten, auch nicht als Rückfall, und ein tieferer Bereich kann die Einstufung nicht lockern.
- **Kalibrieranker:** Medizinische Faktensets (ICD-10, Leitlinien-Aussagen) als Testfälle des Benchmarks (ADR-043). Sie werden unabhängig vom LLM aktuell gehalten; der Benchmark misst jedes Modell gegen sie, bevor es eingesetzt wird (Läufe → M6). Im laufenden Workflow hält der `human_review`-Knoten jedes Ergebnis an.
- **Actor-Pattern für DSGVO Art. 17:** Es gilt für die Handelnden — Ärztinnen und Ärzte als Autoren und Freigebende lassen sich anonymisieren, ohne dass die fachliche Revision verloren geht. Patientendaten in Prompt, Ergebnis und Trace erfasst es nicht; dafür ersetzt Cohereny Bezeichner vor jedem Modellaufruf von `/v1/complete` und in Workflows durch Platzhalter (ADR-018, CHY-63), und die Inhalte eines Laufs vergehen nach ihrer Frist (ADR-046, CHY-17).
- **PII-Redaction (ADR-018):** Identifizierende Angaben werden **vor** jedem Modellaufruf durch Platzhalter ersetzt — Modell, Gateway und Trace sehen sie nicht. Die Namen der Patientinnen und Patienten nennt der Aufrufer oder eine Regel des Bereichs; Namen, die niemand nennt, findet erst NER (→ M7, CHY-235). Gespeichert wird der Lauf nur mit Platzhaltern (CHY-234): Wer am Gate prüft, ob die richtige Patientin gemeint ist, lässt sich die Werte auflösen, und das Audit hält es fest.
- **Abgrenzung:** Dieses Szenario (Arztbriefe = Art.-9-Daten) ist nicht der laufende Study-**Lern**workflow, der Studienhefte ohne Patientendaten verarbeitet. Die DPIA-Pflicht greift erst für dieses Szenario. Die Grundlage für die DSFA des Betreibers steht in `DATA-PROTECTION.md`, eine ausgefüllte Muster-DSFA für genau dieses Szenario in `DATA-PROTECTION-EXAMPLE.md` (deutsch `DATA-PROTECTION-EXAMPLE.de.md`, CHY-18).

---

### 6.8 Szenario (📋 Referenz-Design, nach 1.0): Kamerabasiertes Aktivitäts-Monitoring

**Beschreibung:** Lokale Vision-Inferenz mit Human-in-the-Loop Feedback-Schleife. Kameradaten werden ausschließlich lokal verarbeitet (kein Cloud-Call). Bei Healthcare-Einsatz: Human Oversight Gate hardcoded, DPIA (CHY-18) erforderlich.

```mermaid
sequenceDiagram
    participant CAM as Kamera-Event
    participant FE as Frame-Extraktion
    participant OL as Ollama Vision
    participant CC as Cohereny
    participant HR as Human Review Gate
    participant AL as AuditLog

    CAM->>FE: Bewegungsereignis
    FE->>OL: Frames via ffmpeg (lokal)
    OL->>CC: Klassifikation + Konfidenz-Score
    CC->>CC: CalibrationAnchor-Lookup
    alt Konfidenz >= Schwellwert
        CC->>AL: Klassifikation + Actor-Ref + Timestamp
    else Unbekanntes Muster oder Healthcare
        CC->>HR: Human Review Pflicht
        HR->>CC: Klassifikation bestätigt oder korrigiert
        CC->>CC: Neuer CalibrationAnchor
        CC->>AL: Review-Aktion + Actor-Ref + Timestamp
    end
```

**Besonderheiten:**

- **Datenklasse:** Der Geltungsbereich der Vision-Workflows trägt die Klasse `critical` (ADR-041) — keine Frames an externe APIs
- **CalibrationAnchors:** Visuelle Referenz-Frames statt Referenz-Texten — dieselbe Mechanik, andere Payload-Modalität
- **Übersetzungsregel Vision** (📋, setzt die Adaption voraus → M6, CHY-204; Multimodalität nach 1.0, CHY-206): Unterschiedliche Prompt-Formate für moondream vs. llava werden als Übersetzungsregel mit `task_type=vision_classification` im Kontext-Repository hinterlegt
- **Human Oversight (hardcoded):** Nicht deaktivierbar — der Validator aktiviert keinen Workflow ohne `human_review`-Knoten
- **Audit-Trail modalitäts-übergreifend:** Jede Vision-Inference erzeugt `ExecutionLog` + `AuditLogEntry` — gleiche Revisionssicherheit wie Text-Workflows

### 6.9 Szenario (✅): Workflow-Lauf mit Freigabe-Gate

Der gebaute Kern: ein Lauf durch den eigenen Workflow-Runner (ADR-028) mit Komposition, Routing, Rückfallkette und einem Freigabe-Gate mit Vier-Augen-Prinzip (CHY-127, CHY-176). Darauf beruht die Freigabepflicht des Produkts — der Validator aktiviert keinen Workflow, in dem ein Pfad von einem `llm_call` ohne `human_review` endet.

```mermaid
sequenceDiagram
    participant A as Aufrufer
    participant GW as API Gateway
    participant RUN as Workflow-Runner
    participant COMP as Kompositions-\nEngine
    participant RT as Router
    participant LM as LiteLLM
    participant PG as PostgreSQL
    participant R1 as Reviewer 1
    participant R2 as Reviewer 2

    A->>GW: POST /v1/workflows/{workflow_id}/runs
    GW->>RUN: Lauf eines aktiven Workflows starten
    RUN->>PG: Lauf status=running\nAuditLog workflow.run_started
    loop je Knoten entlang der Kanten
        RUN->>COMP: context_compose
        RUN->>RT: llm_call: Routing-Regel des Scopes
        RT->>LM: Modell der Rueckfallkette
        LM-->>RUN: Antwort
        RUN->>PG: node_executions\n(Modell, Zeitstempel)
    end
    RUN->>PG: human_review erreicht\nstatus=awaiting_oversight
    R1->>GW: POST /v1/workflows/runs/{run_id}/oversight\napproved
    GW->>RUN: Rolle reviewer pruefen
    RUN->>PG: review_votes (1 von 2)\nAuditLog workflow.run_oversight_decided
    R2->>GW: POST /v1/workflows/runs/{run_id}/oversight\napproved
    RUN->>PG: review_votes (2 von 2)\nAuditLog workflow.run_oversight_decided
    RUN->>RUN: Lauf weiter bis zum Ende
    RUN->>PG: status=complete
```

- **Wartezustand statt blockiertem Thread:** `awaiting_oversight` ist ein Zustand in PostgreSQL. Weiterlaufen nach einer Entscheidung ist dieselbe Operation wie der Start (ADR-028 Punkt 2).
- **Vier Augen:** Bei `four_eyes` schließt das Gate erst mit zwei Freigaben verschiedener Personen; der Primärschlüssel von `review_votes` weist eine zweite Entscheidung derselben Person ab.
- **Ablehnung** beendet den Lauf als `rejected`, nicht als `failed` — eine Entscheidung, kein Fehler.
- **Abbruch** beendet den Lauf und den Knoten, an dem er steht, als `cancelled` — weder Fehler noch Ablehnung, am Gate wird nichts entschieden (CHY-200, Art. 14(4)(e)). Ein Knoten, der noch arbeitet, läuft zu Ende; sein Ergebnis wird verworfen.
- **Noch nicht gebaut:** TransparencyRecord je Lauf (→ M7b, CHY-201).

---

## 7. Verteilungssicht

> **Beispiel: die Referenzinstallation.** Ihre Stack-Definition (`compose.yaml`) und die Stufen-Vorlagen liegen nicht im öffentlichen Repository; veröffentlicht ist das `Dockerfile` des API-Images (Port 8000). Zum Ausprobieren liegt im Repository `deploy/quickstart/`: ein Befehl, alles auf einem Rechner, mit Beispiel und Anleitungen (CHY-189).

### 7.1 Deployment-Topologie

```mermaid
graph TD
    subgraph Users["Internes Netz oder VPN"]
        USER_EXT[Nutzer\nBrowser]
    end

    subgraph DMZ["DMZ-Host"]
        NPM[Reverse Proxy\nPort 80/443]
    end

    subgraph Internal["Internes Netzwerk"]
        subgraph App["Application-Server"]
            CC_C[Cohereny Core\nFastAPI Port 8000]
            UI_C[Referenz-UI\ncohereny-ui]
            PG_C[(PostgreSQL\nKontext-Repository)]
            RD_C[(Redis\nCache)]
            LF_C[Langfuse\nTracing]
            LLM_C[LiteLLM\nGateway]
            IDP_C[Authentik\nOIDC Port 9000]
        end

        subgraph Local["Compute lokal"]
            OLS[Ollama\nKleine Modelle\nphi3:mini gemma3:1b]
            OLM[Ollama\nMittlere Modelle\ngemma3:4b gemma4:e4b]
        end

        subgraph Dev["Entwicklungsrechner"]
            DEV_C[Development\nFastAPI dev-server]
        end
    end

    subgraph CloudExt["Compute Cloud, per API"]
        CLA[Claude API\nvorgesehen]
        OCL[Ollama Cloud\ngemma4-cloud gpt-oss-20b]
    end

    USER_EXT --> NPM
    NPM --> CC_C
    NPM --> UI_C
    UI_C -->|SSE + REST| CC_C
    CC_C --> PG_C
    CC_C --> RD_C
    CC_C --> LF_C
    CC_C --> LLM_C
    CC_C --> IDP_C
    LLM_C --> OLS
    LLM_C --> OLM
    LLM_C -->|nur public| OCL
    LLM_C -.->|vorgesehen| CLA

    classDef cloud fill:#1a3a4f,color:#38bdf8
    classDef good fill:#1a4731,color:#4ade80
    classDef warn fill:#4a3500,color:#fbbf24

    class App cloud
    class Local good
    class CloudExt warn
```

### 7.2 Docker Compose Struktur

In der Referenzinstallation laufen alle Komponenten als Docker-Container auf dem Application-Server:

```
<stack>/cohereny/
├── compose.yaml          ← API (3 Stages) + Migrationslauf je Stage + PostgreSQL + Redis + UI
├── .env                  ← POSTGRES_PASSWORD des Superusers (Datenbank-Container, Rollen anlegen)
├── .env.dev / .env.test / .env.prod  ← Stage-spezifische Configs
├── .env.<stage>.db       ← Zugangsdaten der Laufzeitrolle (API, CLI, Löschlauf)
├── .env.<stage>.migrator ← Zugangsdaten der Eigentümerin (nur Migrationslauf)
└── .env.ui               ← API_URL + OIDC-Client der UI (Secret, nie in Git)

<stack>/cohereny-ui/
└── (geklontes UI-Repository — Build-Context für compose.yaml oben)

<stack>/langfuse/
└── compose.yaml          ← Langfuse Tracing

<stack>/litellm/
└── compose.yaml          ← LiteLLM Gateway + config.yaml
```

**Netzwerk-Konvention:** API + UI im Docker-Netzwerk `cohereny`; die Referenz-UI erreicht die API intern über den Container-Namen der jeweiligen Stufe (`cohereny-api-<stufe>:8000`). Die API erreicht LiteLLM über `LITELLM_BASE_URL` — direkt (Port 4000) oder über den Reverse Proxy; dann muss dessen Timeout lange, gestreamte Antworten zulassen.

### 7.3 Kommunikationspfade

| Von                | Nach           | Protokoll                  | Port  | Bemerkung        |
| ------------------ | -------------- | -------------------------- | ----- | ---------------- |
| Referenz-UI        | Cohereny Core        | HTTP intern / HTTPS extern | 8000  | via Reverse Proxy |
| LangGraph Workflow | Cohereny Core        | HTTP intern                | 8000  | direkt, ohne Proxy |
| Cohereny Core            | PostgreSQL     | TCP/SQL                    | 5432  | gleicher Stack   |
| Cohereny Core            | Redis          | TCP                        | 6379  | gleicher Stack   |
| Cohereny Core            | LiteLLM        | HTTP                       | 4000  | gleicher Host    |
| Cohereny Core            | Authentik      | OIDC/HTTPS                 | 9000  | gleicher Host    |
| LiteLLM            | Compute lokal  | HTTP (lokale Laufzeit, z. B. Ollama) | 11434 | intern, LAN      |
| LiteLLM            | Compute Cloud  | HTTPS                      | 443   | extern; Ollama Cloud nur `public`, Claude vorgesehen |

---

## 8. Querschnittliche Konzepte

### 8.1 Sicherheitskonzept

**Authentifizierung:** Ausschließlich OIDC Bearer Token, auf jeder Stufe. Kein API-Key-System, keine Basic Auth und kein Schalter, der die Prüfung abschaltet; bis CHY-192 gab es dafür `DEV_MODE`. Ohne Token antwortet jede Route außer `/v1/health` mit 401, ein Test über alle Routen hält das fest. Für Entwicklung und CI steht `scripts/dev_issuer.py` an Stelle des Identity Providers: Er stellt Tokens für die acht Issuer aus, ohne Passwörter zu prüfen. Einen Issuer über http nimmt die API deshalb nur unter `ENVIRONMENT=dev` an (SECURITY.md, „Known limitations“).

**Autorisierung:** Die Rolle wird aus dem `groups`-Claim des Tokens abgeleitet — nie aus Request-Parametern; fehlt der Claim, bleibt die gespeicherte Rolle stehen. Die Audience des Tokens muss zur aufgerufenen Oberfläche passen (ADR-031). Den Geltungsbereich nennt die Anfrage (`scope_id`). Wer einen Lauf sieht, setzt die Datenbank durch (ADR-047, CHY-92): wer ihn gestartet hat, die Mitglieder des Workflow-Bereichs und der Bereiche darüber, `admin`. Blöcke liest weiter jeder Angemeldete (→ M6, CHY-237), und geschrieben wird mit der installationsweiten Rolle in jedem Bereich (CHY-243; SECURITY.md). Verhindert Privilege Escalation durch manipulierte Anfragen.

**Netzwerk-Segmentierung (Empfehlung für den Betrieb):** Cohereny erzwingt sie nicht; die Referenzinstallation ist so aufgebaut.

- Cohereny Core ohne direkten Internet-Zugang betreiben
- Aus der DMZ (Reverse Proxy) nur Wege zur Core API (und zur UI, falls eingesetzt)
- Keine direkten Datenbankverbindungen aus der DMZ

**Secrets-Handling:** Alle Secrets in `.env` Dateien, niemals in compose.yaml oder Git. Verwaltung im Secret-Store des Betreibers, nicht im Repository.

### 8.2 Revisionssicherheitskonzept

Fachdaten — Kontext-Blöcke aller Typen, also auch Routing-Regeln und Kalibrieranker — sind versioniert:

- **Veröffentlichtes ändert sich nicht:** Eine Änderung an einem eingereichten oder aktiven Block ist eine neue Version mit `version + 1` (`cc_supersede_block`). Ein Entwurf wird an Ort und Stelle bearbeitet; Statuswechsel sind Updates derselben Zeile, erlaubt nur nach der Übergangstabelle im Trigger (Migration 013/016). Gelöscht wird nicht, nur auf `deprecated` gesetzt
- **Vorgänger-Verlinkung:** `superseded_by_version` zeigt auf die nachfolgende Version
- **Actor-Entkopplung:** Personenbezug nur über die Actor-ID, nie direkt

Die Trigger schützen gegen Fehler der Anwendung und gegen ihre Datenbankrolle, die keine Tabelle besitzt und keinen Trigger abschalten darf (ADR-009, CHY-85). Gegen die Zugangsdaten der Eigentümerin schützen sie nicht (SECURITY.md).

Konsequenz: Fachliche Geschichte ist vollständig rekonstruierbar; der Personenbezug der Handelnden lässt sich durch Actor-Anonymisierung entfernen, ohne die Fachhistorie zu beschädigen (Löschweg nach Art. 17; Payload-Daten → M5, §8.3).

### 8.3 DSGVO-Konzept (v2.0 — drei Datenkategorien)

**Drei Datenkategorien** — v1.1 kannte nur die ersten beiden; die dritte ist die eigentliche Art.-17-Baustelle:

| Kategorie               | Typ                                                                                                                                                | Behandlung                                                                                                                                                                                                                                                    |
| ----------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Fachdaten               | Translation Rules, Model Profiles, Anchors                                                                                                         | Niemals löschen, nur versionieren; Personenbezug ausschließlich via`actor_ref`                                                                                                                                                                            |
| Personendaten           | Actor-Stammdaten (`idp_sub`, `display_name`)                                                                                          | Pseudonymisierung (ADR-016)                                                                                                                                                                                         |
| **Payload-Daten** | `pilot_runs.result_json`/`events_json`, `workflow_runs.input_data`/`result`, `node_executions.output`, `benchmark_measurements.output`, `audit_log.payload`, Langfuse-Traces | Können personenbezogene**Inhalte** tragen (z. B. Patientendaten im Prompt), die keine Actor-Anonymisierung erreicht. Maßnahmen: **Platzhalter vor jedem Modellaufruf von `/v1/complete` und in Workflows** ✅ (ADR-018, CHY-63: Modell, Gateway und Trace sehen die Bezeichner nicht) + **Aufbewahrungsfristen** ✅ (ADR-046, CHY-17; Langfuse-Retention beim Betreiber, INFRA-167) + **Workflow-Läufe nur mit Platzhaltern gespeichert** ✅ (ADR-018, CHY-234: Zuordnung je Lauf verschlüsselt, Auflösen protokolliert); die abgekündigten Piloten senden die Werte an das Modell und tragen sie bis zu ihrer Ablösung (CHY-172) |

**Löschantrag-Workflow (v2.0):**

1. Actor → Pseudonymisierung über `POST /v1/admin/actors/{actor_ref}/anonymize` (Admin; CHY-34, ADR-016): `idp_sub = NULL`, `display_name = "[anonym]"`, `is_anonymized`, `anonymized_at`; die Actor-ID bleibt, damit Audit-Log und Fachhistorie konsistent bleiben. Der Vorgang wird auditiert (`actor.anonymized`)
2. Fachliche Historie bleibt vollständig erhalten
3. Payload-Daten: Das Löschersuchen erfasst sie nicht; sie vergehen nach ihrer Frist (ADR-046, CHY-17). Was die Installation verlässt — Modell, Gateway, Trace —, trägt seit CHY-63 Platzhalter statt der Bezeichner, die Stufe 1 findet (ADR-018). Workflow-Läufe speichert Cohereny seit CHY-234 nur mit Platzhaltern; die Werte stehen verschlüsselt in `pii_mappings` und vergehen mit der Inhaltsfrist. Löschersuchen je Person in Lauf-Inhalten → M7 (CHY-239). Langfuse-Traces tragen die interne Actor-ID statt des OIDC-Subjects, der Bezug bricht also an der Quelle

### 8.4 Observability-Konzept

| Layer          | Werkzeug               | Zweck                                                      |
| -------------- | ---------------------- | ---------------------------------------------------------- |
| Tracing        | Langfuse               | Jeden LLM-Call mit Eingabe, Ausgabe, Latenz, Kosten tracen |
| Metriken       | — (Betreiber)          | Cohereny exportiert keine Metriken; System-Metriken liefert die Infrastruktur des Betreibers |
| Audit Log      | PostgreSQL             | Alle Governance-Aktionen (wer hat was wann geändert)      |
| Benchmark      | `benchmark_runs`, `benchmark_measurements` | Messwerte je Anker, Modell und Variante (🔧: Tabellen ✅, Läufe CHY-68, Report CHY-64) |

### 8.5 Caching-Konzept (v2.0 — an ADR-010 angeglichen)

**Compiled Context Pattern (ADR-010):** Redis ist der vorkompilierte Lese-Spiegel, PostgreSQL bleibt die Quelle der Wahrheit.

- Cache-Key: `cc:compiled:{workflow_id}:{node_ref}:{scope_id}:{model_id}:{block_types}:{gen}` — `model_id` ist Teil des Keys, weil die Rückfallkette einer Routing-Regel für denselben Knoten unterschiedliche Modelle liefern kann; `{block_types}` ist die angeforderte Typauswahl; `{gen}` ist der Generation-Stand aller Scopes der Kette
- **Generation-Counter-Invalidierung:** pro Scope ein `cc:scope_gen:{scope_id}` (INCR bei jedem Statuswechsel in oder aus `active`, nach dem Commit). Alte Keys werden nie mehr angefragt und laufen per TTL aus — kein DEL-Fanout, kein Sekundärindex, und ein fehlgeschlagener Cache-Schreibvorgang erzeugt höchstens einen Cache-Miss statt veralteter Daten; scheitert dagegen der INCR, bleibt der alte Eintrag bis zum Ablauf der TTL erreichbar (§8.10)
- TTL: 3600 s, fest im Code (`CACHE_TTL_SECONDS` in `contexts/repository/compose.py`)
- Cold Path (Cache-Miss): Komposition aus PostgreSQL, dann Cache-Write. Auch ein Treffer liest PostgreSQL — die Vorfahrenkette des Geltungsbereichs, weil die Generationen aller Scopes der Kette in den Key eingehen; `/v1/complete` liest außerdem die Routing-Regeln. Der Cache spart Blockabfrage und Komposition.

**Vorteil:** Agentic Workflows, die denselben Step mehrfach aufrufen (z. B. in Schleifen), profitieren sofort vom Cache; Invalidierung ist implizit korrekt.

### 8.6 Fehlerbehandlung

| Fehlerszenario                  | Verhalten                                                                    |
| ------------------------------- | ---------------------------------------------------------------------------- |
| Unauflösbarer Kontext-Konflikt | `409 Conflict` mit Konflikt-Detail; kein Fallback auf schlechteren Kontext |
| Modell nicht verfügbar         | Routing Engine wählt nächstbestes Modell (Fallback-Chain konfigurierbar)   |
| OIDC Provider nicht erreichbar  | `503 Service Unavailable`; kein Fallback auf unauth. Anfragen              |
| Benchmark: Modell nicht erreichbar oder verweigert | Messung mit `outcome` `unreachable`, `refused` oder `error`, nie als Wert 0 (ADR-043, Migration 028); Läufe geplant (CHY-68) |
| PostgreSQL nicht erreichbar     | Die Anfrage scheitert (500); kein Circuit Breaker, kein degraded Mode ohne Persistenz |

### 8.7 Regulatorische Einordnung

> **Hinweis:** Diese Einordnung ist eine technisch-konzeptionelle Einschätzung und erhebt **keinen Anspruch auf Vollständigkeit**. Sie bildet den Ausgangspunkt für die Abstimmung mit dem Betreiber — je nach Branche, Deployment-Kontext und Anwendungsfall können weitere Regulierungen gelten. Vollständige Einzelanalysen mit Artikel-Mapping: **Kapitel 13 (Anhang)**.

#### Regulatoriken-Übersicht

| Regulierung                                                                                                | Geltungsbereich für Cohereny                                                                                                                                  | Status | Kritische Lücken                                                                                           |
| ---------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------- | ------ | ----------------------------------------------------------------------------------------------------------- |
| **DSGVO** (EU 2016/679)                                                                              | ✅ Voll anwendbar — personenbezogene Daten via Actor-Pattern**und in Payloads** (§8.3)                                                           | ⚠️   | Löschung über `POST /v1/admin/actors/{actor_ref}/anonymize` gebaut (CHY-34); Payloads vergehen nach ihrer Frist (CHY-17 ✅) und erreichen Modell, Gateway und Trace nur mit Platzhaltern (CHY-63 ✅, ADR-018); Workflow-Läufe nur mit Platzhaltern gespeichert (CHY-234 ✅), Namen ohne Liste → M7 (CHY-235); DPIA-Grundlage für Betreiber ✅ (`DATA-PROTECTION.md`), Muster-DSFA für das Arztbrief-Szenario ✅ (`DATA-PROTECTION-EXAMPLE.md`, CHY-18) |
| **EU AI Act** (2024/1689, i. d. F. VO (EU) 2026/1744)                                                | ⚠️ Cohereny selbst wahrscheinlich kein KI-System (Komm.-Leitlinien C(2025) 924); Deployer-Pflichten beim Betreiber;**Art.-25-Risiko:** Gesamtsystem Cohereny+LLM ist ein KI-System | ⚠️   | Art.-50-Hinweis in der Referenz-UI (Veröffentlichung → M8, CHYUI-11); Kennzeichnung nach Art. 50(2) → M7b (CHY-118); Anhang III ab 02.12.2027, Anhang I ab 02.08.2028 |
| **CRA** (2024/2847)                                                                                  | ✅ Anwendbar — Cohereny behandelt sich mit der Veröffentlichung als Hersteller; Art.-14-Meldepflichten seit 11.09.2026, volle Anwendung ab 11.12.2027 | ⚠️     | `SECURITY.md` ✅; CVE-Prüfung in CI (pip-audit, Container Scanning; meldend, CHY-75); SBOM ✅ (CHY-22); Unterstützungszeitraum (CHY-149), Patch-SLA (CHY-12), Meldeprozess (CHY-16) → M7b; die Pflichten gelten ab der Veröffentlichung |
| **DORA** (EU 2022/2554)                                                                              | ⚠️ Kapitel II bei Finanzinstitut-Betreibern vollständig                                                                                               | ⚠️   | Incident-Handling-Konzept → M7b (CHY-16); BCP als Betreiber-Checkliste nach 1.0 (CHY-8); **Restore-Tests dokumentieren (Art. 12)** beim Betreiber                                     |
| **MaRisk AT 4.3.4** (RS 06/2026, 9. Novelle, in Kraft seit 30.06.2026 — AT 7.2 ohne IT-Anforderungen) | ⚠️ Finanzinstitut-Betreiber mit automatisierten Modellen                                                                                               | ⚠️     | Regelmäßige Analyse der Modellergebnisse über Benchmark-Läufe → M6 (CHY-68); formales Pre-Deployment-Assessment-Dokument (Betreiber-Checkliste nach 1.0, CHY-9) |
| **BaFin-Orientierungshilfe KI** (18.12.2025)                                                         | ⚠️ Kreditinstitute/Versicherungen (unverbindlich) — adressiert**explizit Model Drift + Lebenszyklus inkl. Stilllegung**                         | ⚠️     | Decommissioning → Model-Sunset-Workflow vorgeschlagen (ADR-020), Umsetzung → M6 (CHY-158); Prozessbeschreibung nach 1.0 (CHY-11) |
| **BSI IT-Grundschutz** (Edition 2023; Übergang zu **Grundschutz++** seit 04/2026, OSCAL/JSON) | ⚠️ Behörden-Betreiber                                                                                                                                 | ⚠️   | Kryptokonzept CON.1 (ADR-014); Mapping künftig gegen Grundschutz++-Bibliothek automatisierbar              |
| **BSI Kriterienkatalog KI** (24.06.2025)                                                             | ⚠️ Bundesverwaltung (empfehlend)                                                                                                                       | ⚠️     | Prompt-Injection-Schutz aus Dokumenten (ADR-039; verdrahtet nur im Banking-Piloten) → M7b (CHY-5); `/v1/complete` ohne Gate → M6 (CHY-199) |
| **BSI C5:2026** (07.04.2026 — keine KI-Anforderungen; einschlägig ist der freiwillige Katalog AIC4) | n/a On-Premise → C5 nicht anwendbar                                                                                                                   | n/a     | Vorteil ggü. Cloud-Lösungen                                                                              |
| **EVB-IT 2026** (Open Source als Standard, veröffentlicht 03/2026) | ⚠️ Öffentliche Beschaffung | ✅ | SBOM, das die Muster vorsehen: CycloneDX in jeder Pipeline (CHY-22) |
| **MDR** + **IEC 62304**                                                                        | ❌ Nur bei SaMD-Klassifizierung                                                                                                                          | ❌     | SaMD-Entscheidung erforderlich (nach 1.0, CHY-4)                                                                        |
| **Gematik ePA**                                                                                      | ❌ Nur bei ePA-Integration                                                                                                                               | ❌     | FHIR R4, VAU, gematik IDP                                                                                   |
| **PCI-DSS v4.0.1** (bestätigt aktuell)                                                              | ❌ Nur bei CHD-Verarbeitung                                                                                                                              | n/a    | —                                                                                                          |


#### Architekturelle Stärken (regulatorisch relevant)

| Architekturmerkmal                   | Regulatorische Wirkung                                                    |
| ------------------------------------ | ------------------------------------------------------------------------- |
| Pflicht-Gate `human_review` in jedem Workflow (Validator, Runner), Vier-Augen-Gates; Ausgaben vor der Entscheidung einsehbar (CHY-229) | EU AI Act Art. 14(3)(a), Art. 14(4)(a) — im Workflow-Weg nicht abschaltbar; `/v1/complete` liefert ohne Gate (→ M6, CHY-199) |
| Drift-Erkennung + `review_needed` (Läufe, Zeitplan, Läufe bei Änderungen → M6, Messfühler → M7; ADR-044) | EU AI Act Art. 9(2)(c), Art. 14(4)(a), Art. 72; MaRisk AT 4.3.4 Tz. 5; DORA Art. 10 |
| Actor-Pattern (Append-Only)          | DSGVO Art. 25 Privacy by Design; Art. 17 Löschrecht ohne Datenverlust    |
| OIDC mandatory in prod               | DORA Art. 9; DSGVO Art. 32; BSI ORP.4                                     |
| Self-hosted, kein Call-Home          | BSI C5 nicht erforderlich; Datensouveränität                            |

#### Architektur-Lücken — Abstimmungsbedarf

> **Legende:** 🔵 Issue angelegt, technische Umsetzung geplant | 🔘 Organisatorische Aufgabe — Betreiber-Verantwortung, nicht durch Cohereny lösbar

| Lücke                                                                        | Betrifft                   | Status           |
| ----------------------------------------------------------------------------- | -------------------------- | ---------------- |
| SBOM (`cyclonedx-bom`) in CI; CVE-Prüfung blockierend machen (pip-audit und Container Scanning laufen, melden nur) | CRA, DORA, BSI | SBOM ✅ CHY-22; CVE-Prüfung meldend ✅ CHY-75, blockierend 🔵 CHY-198 |
| `SECURITY.md` + CVD-Prozess                                                 | CRA Herstellerpflicht      | ✅ `SECURITY.md` |
| Löschung nach DSGVO Art. 17: `POST /v1/admin/actors/{actor_ref}/anonymize` | DSGVO | ✅ CHY-34; Payloads: Frist (CHY-17), Platzhalter vor dem Modell (CHY-63) und in gespeicherten Workflow-Läufen (CHY-234); Löschersuchen je Person in Lauf-Inhalten → M7 (CHY-239) |
| DPIA vor einem Einsatz mit Patientendaten (Arztbrief-Szenario)               | DSGVO Art. 35              | 🔘 Betreiber; Grundlage `DATA-PROTECTION.md` ✅, Muster-DSFA ✅ (CHY-18) |
| Retention Policy (Aufbewahrungsfristen)                                       | DSGVO Art. 5, DORA Art. 12 | ✅ CHY-17, ADR-046 (Audit-Log, Blöcke, Messungen: → M7, CHY-242) |
| Incident-Reporting-Workflow                                                   | DORA Art. 17, CRA Art. 14  | 🔵 → M7b, CHY-16 (Herstellerpflicht nach CRA) |
| Kryptokonzept + at-rest-Verschlüsselung                                      | BSI CON.1, CRA Anhang I    | ✅ CHY-15 (`CRYPTOGRAPHY.md`); Verschlüsselung ruhender Daten beim Betreiber |
| Prompt-Injection-Schutz aus Dokumenten: Zaun und Musterprüfung gebaut (`core/ingress.py`, ADR-039), verdrahtet nur im Banking-Piloten; `/v1/complete` und Workflow-Runner prüfen zugelieferten Text nicht | BSI Kriterienkatalog KI | 🔧 CHY-135 ✅; CHY-5 → M7b |
| SaMD-Entscheidung (Arztbrief-Szenario, §6.7)                                  | MDR, IEC 62304             | 🔘 nach 1.0, CHY-4 |
| **PII-Redaction vor LLM** (Payload-Datenkategorie, §8.3)               | DSGVO Art. 17/25           | ✅ CHY-63 und CHY-234 (ADR-018): an der Grenze und am Eingang; NER → M7 (CHY-235) |
| **Art.-50-Hinweis + TransparencyRecord** | EU AI Act Art. 50 | 🔧 Hinweis in der Referenz-UI (Veröffentlichung → M8, CHYUI-11); TransparencyRecord nur für Pilotläufe, für Workflow-Läufe und `/v1/complete` → M7b (CHY-201); Art. 50(2) → M7b (CHY-118) |
| `/v1/complete` ohne Freigabe-Gate — entschieden: erlaubt nur, wo die Prüfstufe des Bereichs keine Freigabe vor der Ausgabe verlangt | EU AI Act Art. 14 | 🔵 → M6, CHY-199 über CHY-223 |
| Abbruch von Workflow-Läufen | EU AI Act Art. 14(4)(e) | ✅ CHY-200 (Migration 038); am Pilot-Gate ✅ CHY-153 |
| Autorisierung je Geltungsbereich: Laufinhalte lesen, Blöcke lesen, schreiben | DORA Art. 9, BSI ORP.4, CRA | Laufinhalte ✅ CHY-92 (ADR-047); Blöcke 🔵 → M6, CHY-237; Schreiben 🔵 CHY-243 |
| Vier Augen bei Datenfluss-Bausteinen (Routing-Regeln unterhalb `critical`, gelockerte Datenschutzregeln) | DORA Art. 9, BSI ORP.4, CRA | ✅ CHY-163, ADR-045 |
| Eigene Datenbankrolle statt Superuser (Audit-Log auch gegen die Rolle geschützt) | DSGVO Art. 32, CRA | ✅ CHY-85 (ADR-009) |

---

### 8.8 Multimodalität als Architekturprinzip (📋 Referenz-Design)

> **Nichts davon existiert im Quellbaum** (geprüft 08.09.2026, CHY-148):
> `grep -rlwiE "vision|multimodal|transcription" contexts kernel api router cli` findet nichts,
> `ExecutionLog` und `TranslationRule` ebenso wenig. Der Abschnitt trug bis v2.27 **kein**
> Statuszeichen und stand im Präsens, während §6.8 nebenan seit jeher „📋 Referenz-Design"
> trägt — dieselbe Sache, einmal gekennzeichnet und einmal nicht.
>
> Er bleibt stehen, weil das Prinzip trägt: Wenn Modalität eine Eigenschaft des
> `ModelProfile` ist, kostet eine neue Modalität kein Deploy. Das ist ein Entwurf, kein
> Bauzustand.

Die Adaptionsschicht von Cohereny soll modalitäts-agnostisch sein (Adaption 📋 → M6, CHY-204; Multimodalität nach 1.0, CHY-206). Übersetzungsregeln und Modell-Profile im Kontext-Repository sollen sich für Vision-, Audio- und Text-Modelle gleichermaßen definieren lassen — ohne Änderung am Kern-Framework.

**Erweiterung auf neue Modalitäten (Entwurf):**

1. Neues `ModelProfile` mit modalitätsspezifischen Stärken und bevorzugtem Prompt-Format anlegen
2. `TranslationRule` mit modalitätsspezifischer Prompt-Struktur anlegen (z. B. `task_type=vision_classification`)
3. Optional: `CalibrationAnchor`-Set mit Referenz-Eingaben für die neue Modalität

Für die Adaption ändert sich kein Deployment-Artefakt — nur Repository-Einträge. Das Modell selbst muss erreichbar und mit seiner Datenklasse in `router/routing-config.yaml` eingetragen sein (ADR-038); das ist eine neue Fassung des Pakets. Dasselbe gilt für Text-Modelle (Qualitätsziel Q9, §10.2).

**Datenschutz-Routing bei sensitiven Modalitäten:**

Kameradaten, Sprachaufnahmen und biometrische Merkmale unterliegen erhöhtem Schutzbedarf (DSGVO Art. 9). Der Datenschutz-Router behandelt sie wie medizinische Textdaten:

| Modalität                 | Datenkategorie   | Standard-Routing          |
| -------------------------- | ---------------- | ------------------------- |
| Text (medizinisch)         | `confidential` | `local_only`            |
| Video / Frames             | `confidential` | `local_only`            |
| Audio (Patientengespräch) | `confidential` | `local_only`            |
| Biometrische Merkmale      | `critical`     | `local_only`, hardcoded |

**Audit-Trail modalitäts-übergreifend:** Jede Vision-Inferenz, jede Audio-Transkription und jede Text-Completion erzeugt einen `ExecutionLog` und `AuditLogEntry` mit Actor-Ref, Modell-ID, Timestamp und Konfidenz — derselbe Mechanismus, gleiche Revisionssicherheit für alle Modalitäten.

### 8.9 AdaptVault: Versioniertes Adaption-Repository + DPO-Flywheel (📋 geplant; Erkennung teilweise gebaut)

> **Seit ADR-043 (06.10.2026):** Testfälle sind Kalibrieranker im Kontext-Repository statt einer Tabelle `prompt_benchmarks`. Gemessen wird nach Zeitplan, bei Änderungen und auf Abruf (ADR-044). Gebaut sind Anker, Validatoren und Messtabellen (CHY-67); Läufe (CHY-68), Zeitplan (CHY-181), Läufe bei Änderungen (CHY-217), Varianten-Generierung (CHY-69), DPO (CHY-70) und der Export-Endpunkt sind nicht gebaut.

**Kontext:** Die Adaptionsschicht kann nur so gut sein wie die Übersetzungsregeln im Kontext-Repository. Manuelle Pflege skaliert nicht bei wachsender Modell- und Domänenanzahl.

**AdaptVault** ist das benannte Sub-System für das versionierte, benchmark-getriebene Adaptions-Wissen von Cohereny. `PromptBenchmark`- und `PromptAdaptation`-Einträge folgen dem `RevisionedEntity`-Pattern (ADR-009): append-only, auditierbar, rollback-fähig. Die Frage "Welche Adaption hat wann für welches Modell wie gut funktioniert?" ist damit jederzeit beantwortbar.

**Architekturprinzip:** Messbarkeit vor Intuition. Jede behauptete Adaption muss quantitativ validiert sein (`adaptation_gain` > 0); nur HITL-validierte oder automatisch high-confidence Ergebnisse fließen ins AdaptVault.

**Die drei Schichten:**

| Schicht | Mechanismus | Status |
|---|---|---|
| **Erkennung** | Benchmark nach Zeitplan und bei Änderungen über alle aktiven Kalibrieranker × Modelle (ADR-043, ADR-044) | Anker und Messtabellen ✅ CHY-67; Läufe CHY-68; Zeitplan CHY-181; Änderungen CHY-217 |
| **Adaption** | Varianten-Generierung (CoT, Format, Kontext, Meta-Prompt) + HITL-Queue bei Konfidenz < Schwellwert | CHY-69, M6 |
| **Lernen** | DPO-Training aus ranked pairs → intern: `cohereny-phi3-dpo:v1` (Ollama, via LiteLLM) / extern: `GET /v1/adaptvault/export/dpo` JSONL | 📋 → M6, CHY-70 |

**Laufzeit-Ablauf (Entwurf nach ADR-024; Auslöser und Testfälle nach ADR-043):**

```mermaid
flowchart TD
    RUN[Lauf nach Zeitplan,\nbei Aenderung oder Abruf] --> ITER[Iteriere alle\naktiven Kalibrieranker]
    ITER --> EVAL[Sende an alle\nLiteLLM-Modelle]
    EVAL --> SCORE[Evaluiere Output\nschema / contains / llm_judge]
    SCORE --> |Score ok| DB[benchmark_results\nschreiben]
    SCORE --> |Score lt Schwellwert| VAR[Varianten-Generierung]
    VAR --> |gain > Threshold| AUTO[auto_approved\nprompt_adaptations]
    VAR --> |gain unsicher| HITL[HITL-Queue\nCHYUI-3]
    DB --> ALERT[Alarm\nbei Score-Drop gt 10%]

    classDef cloud fill:#1a3a4f,color:#38bdf8
    classDef warn fill:#4a3500,color:#fbbf24
    class RUN,ITER,EVAL,SCORE,DB,VAR,AUTO cloud
    class ALERT,HITL warn
```

**Konsequenzen:**

- (+) `adaptation_gain` ist ein messbarer Qualitätsnachweis — erfüllt das Qualitätsziel QK1 ("Adaption verbessert Output messbar", §10.1)
- (+) DPO-Flywheel: Produktionsbetrieb erzeugt kontinuierlich Trainingsdaten; Modell verbessert sich inkrementell
- (+) Human Oversight bleibt nicht abschaltbar für semantisch unsichere Adaptionen (konsistent mit ADR-004)
- (-) Jeder Lauf erzeugt Langfuse-Traces; ihre Frist stellt der Betreiber in Langfuse ein (ADR-046)
- (-) DPO-Training ist compute-intensiv; initiales Training läuft auf einem lokalen Compute-Host, nicht in Docker

**Verweis:** ADR-024 enthält die vollständige Entscheidungsdokumentation.

---

### 8.10 Reihenfolge der übergreifenden Funktionen (v2.9)

Vier Dinge betreffen jeden Kontext: Rechteprüfung, Audit, Cache-Invalidierung und Tracing.
Sie werden nicht je Kontext nachgebaut — sie liegen im Kernel bzw. in der Infrastruktur
(§5.7). Verbindlich ist aber nicht ihr Ort, sondern **ihre Reihenfolge**:

```
prüfen  →  sperren  →  ändern  →  protokollieren  →  committen  →  invalidieren
  ①          FOR         │            ②                 │             ③
 access     UPDATE       │        audit_log             │        Redis-Bump
(vor der Transaktion)    └────── eine Transaktion ──────┘   (nach dem Commit)
```

Jede Abweichung hat eine benennbare Folge:

| Abweichung | Folge |
|---|---|
| Rechteprüfung **innerhalb** der Transaktion | Eine Sperre wird für einen Aufrufer gehalten, der gar nicht darf |
| Audit **außerhalb** der Transaktion | Die Änderung überlebt, der Nachweis nicht — der Fall, den ADR-016 ausschließt |
| Invalidierung **innerhalb** der Transaktion | Ein Redis-Ausfall nimmt eine bereits korrekte Freigabe zurück und meldet dem Aufrufer, sie sei nicht geschehen |
| Invalidierung ganz weggelassen | Ein veralteter Cache für die Dauer von `CACHE_TTL_SECONDS` — der einzige der vier Fehler, den man tragen kann |

Der letzte Punkt ist der Grund für die Asymmetrie: Audit wird mit der Änderung zusammen
committet, weil beides zusammen gelten muss; die Invalidierung darf danach scheitern, weil
ihr Ausfall begrenzt und heilbar ist. `contexts/repository/service.py` hält die Reihenfolge ein
(`transition`: Rolle prüfen, dann `FOR UPDATE`, Änderung und Audit in einer Transaktion, Invalidierung
nach dem Commit) — die Regel hat den Umzug aus `api/v1/blocks.py` (CHY-105) überstanden.

Tracing (Langfuse, ADR-024) umschließt das Ganze und darf nichts davon beeinflussen: ein
nicht erreichbarer Kollektor bricht keine Anfrage ab (CHY-29).

### 8.11 Datenbank: Rollen, Migrationen, Rückweg (v2.58)

Drei Regeln gehören zusammen: mit welchen Rechten sich die Anwendung verbindet, wann sich das
Schema ändert und wie eine Installation zurückkommt.

- **Rollen** (ADR-009, CHY-85): Die Anwendung verbindet sich als Laufzeitrolle, die keine Tabelle
  besitzt, kein DDL ausführt und keinen Trigger abschalten darf; mit mehr Rechten startet sie nicht.
  Die Eigentümerin der Tabellen führt nur die Migrationen aus. Wer einen Lauf sieht, setzt die
  Datenbank per Row-Level Security durch (ADR-047)
- **Reihenfolge beim Update:** erst das Backup, dann die Migrationen in einem eigenen Lauf als
  Eigentümerin, dann die neue Version der API. Nie im API-Container: Er hat die Rechte dafür nicht
- **Nur vorwärts** (entschieden 2026-11-06, Thomas; CHY-113): Eine Migration hat keinen Rückweg.
  Ein Fehler in einer Migration wird durch eine neue Migration korrigiert, nie durch eine
  rückwärtige. Der Rückweg einer Installation ist das Backup vor dem Update. Ein älteres Image allein
  ist kein Rollback, denn es findet ein Schema vor, das es nicht kennt

Verworfen: Rückwärts-Migrationen je Datei. Ein Rückweg, der nur im Ernstfall läuft, ist ungeprüft,
und was eine Migration gelöscht hat, holt nur das Backup zurück.

Durchgesetzt wird „nur vorwärts“ durch einen Wächter, nicht durch den Runner, der nur Nummern
prüft: `migrations/CHECKSUMS` verzeichnet jede Migration mit ihrer Prüfsumme, und
`tests/test_migrations_forward_only.py` fällt, sobald eine gemergte Datei sich ändert, fehlt oder
eine Nummer doppelt vergeben ist (CHY-113, 07.11.2026). Eine geänderte Zeile in der Liste bleibt
möglich, steht dann aber im MR und muss begründet werden. Als erste Berichtigung nach dieser Regel
ersetzt Migration 037 die falsche Begründung aus 014 (CHY-152).


## 9. Architekturentscheidungen

### ADR-001: API-First / Headless Core

**Status:** Entschieden

**Kontext:** Wie stellen wir sicher, dass Cohereny für beliebige Clients nutzbar ist (Web-UI, CLI, LangGraph, Drittanbieter)?

**Entscheidung:** Alle Geschäftslogik in der REST API; kein privilegierter Zugriffspfad am UI vorbei. Die Referenz-UI (Astro) ist lebender API-Beweis, kein exklusives Frontend.

**Konsequenzen:**

- (+) Maximale Flexibilität für Clients
- (+) Referenz-UI beweist API-Vollständigkeit
- (-) Mehr Entwicklungsaufwand (API + UI getrennt)

---

### ADR-002: Scope-Hierarchie (Global → Team → Projekt)

**Status:** ⚠️ Superseded durch ADR-008 (dynamische Scope-Hierarchie, 2026-06-29) — hier nur noch als Historie

**Kontext:** Wie lösen wir das Cold-Start-Problem bei neuen Teams ohne Wissens-Silos zu erzeugen?

**Entscheidung:** Dreistufige Hierarchie mit automatischer Vererbung. Tiefere Scopes können Blöcke überschreiben, nicht löschen.

**Konsequenzen:**

- (+) Teams starten mit Basis-Wissen (Global)
- (+) Spezialisierung ohne Duplikation (Team/Projekt-Overrides)
- (-) Komplexere Kompositions-Logik

---

### ADR-003: Actor-Pattern für DSGVO vs. Revisionssicherheit

**Status:** Entschieden

**Kontext:** DSGVO fordert Löschbarkeit von Personendaten; Revisionssicherheit fordert Unveränderlichkeit der Fachhistorie. Direkter Widerspruch.

**Entscheidung:** Personenbezug nie direkt in Fachdaten, sondern über anonymisierbare `actor_ref`. Anonymisierung lässt Fachhistorie intakt.

**Konsequenzen:**

- (+) Beide Anforderungen gleichzeitig erfüllbar
- (+) Klare Datentrennung von Anfang an
- (-) Kein direkter User-Name in Fachhistorie (nur Actor-Ref)

---

### ADR-004: Human Oversight als nicht abschaltbarer Kern

**Status:** Entschieden

> **Umsetzungsstand (31.10.2026):** Der Fall, für den entschieden wurde — Freigabepflicht nach einer im Benchmark erkannten Regression —, ist nicht gebaut. Ein Feld `human_oversight_required` gibt es nicht; `benchmark_runs.review_needed` steht im Schema (Migration 028), setzen wird es der Lauf (CHY-68), die Freigabe ist CHY-64. Gebaut und nicht abschaltbar ist die Pflicht in Workflows: Der Validator aktiviert keinen Workflow ohne `human_review`, keine Ausgabe eines `llm_call` erreicht das Ende ohne ein `human_review`, und der Runner wartet dort. Was dort entschieden wird, liest der Prüfer seit CHY-229 vorher über `GET /v1/workflows/runs/{id}/nodes`. `/v1/complete` liefert Modellantworten ohne Gate.

> **Angekündigt (2026-11-06, Thomas; CHY-223 → M6):** Die Prüftiefe wird nach Risiko abgestuft, in drei Stufen: `sample` (die Ausgabe geht sofort hinaus, ein einstellbarer Anteil wird nachträglich geprüft), `approver` und `four_eyes`. Es gilt das Strengere aus der Datenklasse des Geltungsbereichs und der Risikoklasse des Workflows; tiefere Bereiche dürfen nur verschärfen, wie bei der Modellwahl (ADR-041). Nie abgestuft werden der Fall dieser Entscheidung — die Freigabe nach einer Regression —, Meldungen der Drift-Erkennung (ADR-044) und die Datenklasse `critical`. Die angewandte Stufe steht je Lauf im Audit. `/v1/complete` ist danach die Stufe ohne Gate vor der Ausgabe und nur dort erlaubt, wo sie zulässig ist (CHY-199). Die ADR entsteht mit der Umsetzung.

**Kontext:** Soll die Pflicht zum Human Review bei Qualitäts-Regressionen konfigurierbar sein?

**Entscheidung:** Nein. `human_oversight_required = True` bei erkannter Regression ist hardcoded, nicht konfigurierbar. Rationale: AI Act Art. 14; Sicherheits-Anker der nicht durch Konfigurationsfehler umgangen werden kann.

**Konsequenzen:**

- (+) AI-Act-kompatibel by default
- (+) Kein "accidental bypass" durch falsche Konfiguration
- (-) Weniger Flexibilität für Betreiber die anders abwägen wollen

---

### ADR-005: Git-Backend vs. eigene Datenbank für MR-Workflow

**Status:** Entschieden (2026-06-30) — Option B · **überholt durch ADR-042 (2026-09-29)**

> **Überholt.** Die Tabellen wurden nie beschrieben; was sie speichern sollten — Versionen und Freigabestatus —, trägt inzwischen `context_blocks` selbst. Migration 025 entfernt beide (CHY-65).

**Kontext:** Der Merge Request Workflow für Kontext-Blöcke braucht eine Storage-Lösung für Diffs, Versionen und Review-State.

**Optionen:**

- A: Echtes Git als Backend (Kontext-Blöcke als JSON-Dateien in Git)
- B: Eigene PostgreSQL-basierte Versionierung mit Git-ähnlicher UX
- C: Hybrid

**Entscheidung:** Option B (eigene PostgreSQL-Tabellen: `merge_requests`, `merge_request_comments`). Begründung: Kontext-Blöcke sind strukturierte Daten (JSONB), schema-aware Diffs sind direkter abzubilden als Text-Diffs. Git als Backend würde zusätzliche Abhängigkeit und JSON→Text→JSON-Roundtrips erzeugen. Migration zu A/C möglich wenn Community-Workflows wichtig werden.

**Konsequenzen:**

- (+) Schema-aware Diffs, typsichere Konflikt-Erkennung
- (+) Direkter API-Zugriff, keine externe Abhängigkeit
- (-) Kein Free-Rebase vom Git-Tooling — Review-Workflow muss vollständig eigengebaut werden

---

### ADR-006: Dreistufige Validator-Pyramide für Nightly Benchmark

**Status:** Entschieden (2026-06-30)

> **Präzisiert durch ADR-043 (06.10.2026):** Der Benchmark bewertet nur mit Stufe 1 und 2; der LLM-Judge (Stufe 3) gehört nicht dazu. Er läuft nach Zeitplan, bei Änderungen und auf Abruf (ADR-044, 03.11.2026). **Umsetzungsstand (31.10.2026):** Stufe 1 und 2 sind als Validatoren eines Kalibrierankers gebaut (`contexts/learning/validators.py`, CHY-67): JSON Schema, Regex, Länge, Pflicht- und Verbotsbegriffe, ROUGE-L. TF-IDF, Jaccard und die Abweichung zur Baseline sind nicht gebaut, ebenso Stufe 3. Läufe gegen die Modelle kommen mit CHY-68. ADR-021 ist nicht geschrieben; das Korrektur-Protokoll nach Stufe 3b kommt mit CHY-40 (→ M6). **Abgrenzung (03.11.2026):** Einbettungen gehören nicht in die Pyramide. Ein Embedding-Modell im Score wäre ein Lineal, das selbst driftet; als Messfühler außerhalb des Scores sind sie vorgesehen (ADR-044, → M7).

**Kontext:** Lokale Modelle als Judge für andere lokale Modelle sind ungeeignet — sie bevorzugen strukturierte Formatierung gegenüber semantischer Korrektheit (Bias für gut formatierte aber inhaltlich falsche Artefakte). Cloud-Modelle als Judge verursachen bei großen Dokumenten prohibitive nächtliche API-Kosten. Rein heuristische Qualitätsmessung ist nicht verlässlich.

**Entscheidung: Option B (CalibrationAnchor) mit deterministischem Validator-Stack**

Qualität wird **nie heuristisch** gemessen. Deterministische Validatoren sind der primäre Mechanismus — LLM-Judge ist die allerletzte Instanz.

```
Layer 1 — Deterministisch (kostenlos, immer):
  JSON Schema Validation    → Strukturkorrektheit strukturierter Artefakte
  Regex                     → Formatvorgaben (Heading-Tiefe, Listenstruktur)
  Längen-Checks             → Mindestlänge, ±N% Abweichung zur Baseline
  Keyword-Coverage          → Set-Overlap / TF-IDF gegen Input-Dokument

Layer 2 — CalibrationAnchor (selten, kein LLM):
  ROUGE-L / Jaccard         → Ähnlichkeit zur Gold-Referenz
  CalibrationAnchors        → manuell gepflegt, versioniert im Kontext-Repository

Layer 3 — LLM-Judge (nur bei Ambiguität aus 1+2):
  Lokales Modell (L3a)      → Routine-Grenzfälle, kein Human Gate erforderlich
  Cloud-Modell (L3b, Claude):
    1. Human Gate PRE        → Privacy-Prüfung: keine sensiblen Daten in die Cloud (ADR-004)
                               ⚠️ präzisiert 24.09.2026 (CHY-39), siehe unten
    2. Cloud LLM Call        → Claude bewertet Ambiguität
    3. Human Gate POST       → Quality-Gate: Antwort korrekt? Freitext-Korrektur möglich (→ ADR-021)
```

**Präzisiert am 24.09.2026 (Thomas, CHY-39): Das Gate *vor* der Cloud ist strukturell, nicht je Anfrage.**

Als ADR-006 entstand, gab es keinen Mechanismus, der die Klasse der Daten mit der Obergrenze eines Modells vergleicht — ein Mensch musste es vor jedem Cloud-Aufruf tun. Seit ADR-041 gibt es ihn: Kein Modell bekommt Daten über seiner Obergrenze, auch nicht als Rückfall, und ein unklassifizierter Bereich gilt als `critical`. Damit erreichen nur Daten, deren Bereich ausdrücklich als `public` eingestuft ist, ein gehostetes Modell überhaupt.

Die Privacy-Prüfung aus Schritt 1 findet deshalb **zweimal statt, aber nicht je Anfrage**: wenn jemand den Bereich einstuft, und wenn jemand die Routing-Regel freigibt, die ein Cloud-Modell nennt. Beides sind menschliche Entscheidungen, beide auditiert, und beide werden **maschinell durchgesetzt** — beim Auflösen jeder Anfrage, ohne Umweg.

**Kein menschliches Gate je Anfrage.** Es prüfte eine Frage, die schon beantwortet ist, und wer eine Frage hundertmal am Tag bestätigt, bestätigt sie irgendwann ungelesen — das Gegenteil von Aufsicht.

**Schritt 3 bleibt unverändert.** Das Gate *nach* dem Aufruf prüft etwas anderes: nicht, ob die Daten hinaus durften, sondern ob die Antwort stimmt. In Workflows erzwingt der Validator dafür ohnehin `human_review` (ADR-004).

**Wo diese Präzisierung nicht reicht:** wenn der Inhalt einer Anfrage nicht zur Einstufung seines Bereichs passt — ein Upload mit personenbezogenen Daten in einem `public`-Bereich. Das ist keine Frage der Modellwahl, sondern der Eingangsprüfung (ADR-039) und der PII-Schwärzung (ADR-018).

**Konsequenzen:**

- (+) Kein nächtlicher Cloud-API-Verbrauch im Normalfall
- (+) Deterministisch und reproduzierbar — kein Modell-Bias auf dem kritischen Pfad
- (+) CalibrationAnchors sind scope-spezifisch und versioniert
- (-) Layer-1-Validatoren müssen pro Artefakt-Typ definiert und gepflegt werden
- (-) CalibrationAnchors brauchen initiale manuelle Erstellung (einmalig pro Scope/Task)

---

### ADR-008: Dynamische Scope-Hierarchie via `scope_path`

**Status:** Entschieden (2026-06-29)

> **Umsetzungsstand (31.10.2026):** ltree, Label-Kodierung und Scope-Typen sind umgesetzt (Migration 009, CHY-59). Die Konfliktregel im Absatz „Vererbung“ ist es nicht: Die Komposition überschreibt nichts, sie reiht die Blöcke aller Vorfahren flach nach tief aneinander, und `force_override` gibt es nicht (ADR-017, Fortschreibung v2.17).

**Kontext:** Die ursprüngliche 3-stufige Hierarchie (Global → Team → Projekt) ist zu starr. Use Cases erfordern Workspaces (teamübergreifend, z.B. alle Projekte für einen Kunden) und Sub-Projekte. Eine hardcodierte Hierarchie würde Schema-Änderungen erfordern wenn eine neue Ebene nötig wird.

**Entscheidung:** Statt `scope_type + scope_id` wird `scope_path` als dynamischer hierarchischer Pfad verwendet. Alle Scope-Knoten (Org, Team, Workspace, Projekt, Sub-Projekt, Personal) leben in einer einheitlichen `scope_nodes`-Tabelle.

Scope-Typen: `org | team | workspace | project | sub_project | personal`

Ein **Workspace** ist ein teamübergreifender Container auf Org-Ebene — z.B. `org_acme → workspace_kundexy → project_angebotq3`. Derselbe Workspace kann Projekte aus verschiedenen Teams enthalten.

Vererbung: Ein Block mit `scope_path = 'org_acme.team_it'` (ltree) ist sichtbar für alle Kontexte deren Pfad mit `org_acme.team_it` beginnt. Tiefster Scope im Pfad gewinnt; `force_override: true` überschreibt immer.

**Implementierung (entschieden 2026-07-11):** PostgreSQL `ltree`-Extension.

- Encoding: `{type}_{uuid}` als einzelnes Label, Punkte als Hierarchietrenner — z.B. `org_acme.team_it.project_study`
- `node_type` ist ein separates Feld in `scope_nodes` (nicht in den Pfad kodiert)
- Nur `[A-Za-z0-9_]` als ltree-Labels; Bindestriche in UUIDs durch Unterstriche ersetzen
  > **Präzisierung 2026-08-07 (CHY-59, bei der Umsetzung geprüft):** Die ursprüngliche Begründung — ltree erlaube ausschließlich `[A-Za-z0-9_]` — trifft seit **PostgreSQL 16 nicht mehr zu**; Bindestriche in Labels werden dort akzeptiert (`'org_acme.team-it'::ltree` parst fehlerfrei, verifiziert auf PG 16.14). Die Konvention **bleibt unverändert bestehen**, jetzt aber aus Konsistenzgründen statt aus technischem Zwang: Zwei zulässige Schreibweisen desselben Scopes würden auf verschiedene Zeilen zeigen, und ein solcher Fehler bleibt unsichtbar — die Abfrage liefert einfach kein Ergebnis. Durchgesetzt wird die Regel deshalb in der Anwendung (`kernel/schema.py`: `scope_label()` baut das Label, ein Validator prüft `scope_path`), nicht von der Datenbank.
- Prefix-Matching via `scope_path <@ 'org_acme.team_it'::ltree` (ltree Operator)

**Konsequenzen:**

- (+) Beliebige Tiefe und Topologie ohne Schema-Änderung
- (+) Workspace als eigenständiger Scope-Typ (teamübergreifend)
- (+) Sub-Projekte, zukünftige Scope-Typen ohne Migration
- (-) Komplexere Kompositions-Abfragen (Prefix-Matching statt einfacher IN-Klausel)
- (-) Cache-Keys müssen scope_path-aware sein

---

### ADR-009: Append-Only auf Datenbankebene erzwungen

**Status:** Entschieden (2026-06-29)

> **Umsetzungsstand (06.11.2026):** Alle vier Punkte sind umgesetzt: 1 (Migration 001), 3 und 4 (Migration 012), 2 seit CHY-85 (Migrationen 033, 034), anders als im Text benannt — siehe Nachtrag. Punkt 3 mit einer Abweichung: Ab dem INSERT fest sind `block_id`, `version`, `scope_id`, `block_type`, `author_id` und `created_at`; `content`, `metadata`, `tags` und seit Migration 016 die Governance-Spalten erst außerhalb von `draft` — ein Entwurf wird an Ort und Stelle bearbeitet. Die erste Konsequenz gilt gegen die Anwendung und ihre Datenbankrolle, nicht gegen die Eigentümerin der Tabellen: Wer deren Zugangsdaten besitzt, kann einen Trigger abschalten.

**Kontext:** Das Append-Only-Prinzip war bisher nur in der Anwendungsschicht erzwungen. Ein direktes SQL UPDATE auf versionierte Tabellen wäre technisch möglich und würde die Revisionssicherheit zerstören.

**Entscheidung:** Drei Mechanismen kombiniert:

1. **Partieller Unique Index** — verhindert zwei gleichzeitig aktuelle Versionen:

   ```sql
   CREATE UNIQUE INDEX ON context_blocks (block_id)
     WHERE superseded_by_version IS NULL;
   ```
2. **DB-Rollen-Trennung** — die API-Rolle hat kein UPDATE-Recht auf versionierte Tabellen:

   - `cohereny_app`: INSERT auf `context_blocks`, `workflow_definitions`, `workflow_nodes`; kein UPDATE, kein DELETE
   - `cohereny_version_manager`: darf `superseded_by_version` setzen (nur für den Versionierungs-Service)
3. **Trigger** — verhindert nachträgliche Änderung unveränderlicher Felder (`block_id`, `version`, `content`, `actor_ref`, `created_at`) nach INSERT.
4. **SECURITY-DEFINER-Funktion (Ergänzung v2.0)** — Der Versionswechsel wird als PostgreSQL-Funktion gekapselt:

   ```sql
   SELECT cc_supersede_block(block_id, new_content, actor_ref, change_reason);
   ```

   Die Funktion läuft mit den Rechten ihres Owners (`cohereny_version_manager`) und erledigt „superseded setzen + neue Version einfügen" **atomar in einer Transaktion**. Damit braucht die Anwendung **keinen zweiten Connection-Pool** und es gibt kein Race gegen den partiellen Unique-Index (kurzzeitig zwei „aktuelle" Versionen sind unmöglich).

**Konsequenzen:**

- (+) Revisionssicherheit ist nicht umgehbar — auch nicht durch Bugs in der Anwendung
- (+) DSGVO-Prüfbarkeit: vollständige Geschichte garantiert auf DB-Ebene
- (-) Etwas aufwändigere DB-Verwaltung (zwei Rollen, Trigger-Pflege)
- (-) Migrations-Skripte müssen die privilegierte Rolle nutzen

**Nachtrag (2026-11-06, CHY-85): So ist Punkt 2 umgesetzt.**

- **Zwei Rollen je Datenbank.** `<db>_migrator` besitzt alle Objekte und führt die Migrationen aus; als `<db>_runtime` verbinden sich API, CLI und Löschlauf. Die Rechte hält die Gruppenrolle `cohereny_app`, deren Mitglied die Laufzeitrolle ist. Die Stufen einer Installation teilen sich so keine Zugangsdaten. Angelegt und bei Bedarf neu gesetzt werden beide von `python -m migrations.roles` mit den Rechten eines Superusers, den danach nichts mehr braucht
- **Die Rechte je Tabelle sind Code:** in `kernel/retention.py`, neben der Aufbewahrungsklasse (Feld `access`). Migration 033 vergibt sie, ein Test hält die Migration an der Liste fest, der Integrationstest die Datenbank. Kein TRUNCATE, kein TRIGGER, kein CREATE: Die Anwendung führt kein DDL aus. Auf `context_blocks` hat sie UPDATE, weil Bearbeiten eines Entwurfs und Statuswechsel Updates derselben Zeile sind (Abweichung zu Punkt 3); was sich dabei ändern darf, begrenzen die Trigger
- **`cohereny_version_manager` gibt es nicht.** `cc_supersede_block` gehört der Eigentümerin und läuft mit ihren Rechten. Seit Migration 034 darf nur sie `superseded_by_version` setzen; vorher genügte `SET cc.superseding = 'on'`, um am Trigger aus Migration 012 vorbeizukommen. Damit überbrückt die SECURITY-DEFINER-Funktion zum ersten Mal eine echte Rechtelücke
- **Die API verweigert den Start mit mehr Rechten als nötig:** als Superuser, als Eigentümerin, mit BYPASSRLS, mit CREATE auf dem Schema oder mit TEMP (`kernel/least_privilege.py`, ohne Ausnahme und ohne Schalter). Dasselbe prüfen der Löschlauf und `cc actor set-role`
- **Migrationen laufen getrennt von der API**, als Eigentümerin, in einem eigenen Lauf vor dem Start der neuen Version (§8.11)
- **Grenze, offen dokumentiert** (`SECURITY.md`): Wer die Zugangsdaten der Eigentümerin oder des Superusers besitzt, kann einen Trigger abschalten, und keine Hash-Kette würde die Änderung zeigen. Beide Zugangsdaten gehören nicht in die Reichweite der API

---

### ADR-010: Compiled Context Pattern — Redis als vorkompilierter Lese-Spiegel

**Status:** Entschieden (2026-06-30, konkretisiert aus v1/v2-Entwurf)

> **Umsetzungsstand (31.10.2026):** Gebaut ist die Fortschreibung v2.0 ohne Write-Through (v2.7): Generation-Counter je Scope, der Schlüssel enthält die Generationen der ganzen Vorfahrenkette (`contexts/repository/compose.py`, CHY-61). Abweichend vom Text: Die TTL ist die Konstante `CACHE_TTL_SECONDS`, keine Umgebungsvariable; der Schlüssel trägt zusätzlich die angeforderten Blocktypen; auch ein Treffer liest vorher die Vorfahrenkette aus `scope_nodes` — `/v1/compose` und `/v1/complete` sind nicht PostgreSQL-frei. `auto_route` gibt es als Mechanismus nicht; verschiedene Modelle für denselben Knoten entstehen durch die Rückfallkette.

**Kontext:** Die Kompositions-Engine ist der kritische Pfad (jeder LLM-Call in einer 9-Node-Pipeline). Scope-Path-Traversal und Konflikt-Evaluierung zur Laufzeit wären zu langsam. PostgreSQL ist der revisionssichere Append-Only-Datenspeicher — nie der Lese-Pfad für Laufzeit-Requests.

**Entscheidung: Write-Through Compiled Context Pattern**

Sobald ein `context_block` als `active` markiert wird, löst FastAPI einen `BackgroundTask` aus, der den Composed Context für alle betroffenen Workflow-Nodes vorausberechnet und als flaches Key-Value in Redis schreibt.

```
Key:   cc:compiled:{workflow_id}:{node_ref}:{scope_leaf_id}
Value: JSON { compiled_prompt, model, block_versions, compiled_at }
TTL:   3600s (ENV: CC_CACHE_TTL)

Sekundärindex:
Key:   cc:scope_idx:{scope_id}
Value: Redis Set aller compiled Keys die Blöcke aus diesem Scope einschließen
```

Laufzeit-Logik:

- Cache-Hit → ein Redis-GET, kein PostgreSQL-Query, direkt weiter an LiteLLM
- Cache-Miss → PostgreSQL-Lookup + Compile + Cache-Write (selten: Neustart, erste Aktivierung)

Cache-Invalidierung:

- Globaler Block ändert sich → `cc:scope_idx:{global_scope_id}` → DEL alle enthaltenen Keys
- Nur Projekt-Block → `cc:scope_idx:{project_scope_id}` → DEL nur exakte Projekt-Keys
- Kein SCAN in prod — Sekundärindex ermöglicht gezieltes DEL

**Konsequenzen:**

- (+) `/v1/compose` und `/v1/complete` sind im Normalfall PG-frei — skaliert auch bei 9-Node-Pipelines
- (+) PostgreSQL bleibt reiner Append-Only-Datenspeicher, nie Read-Last auf dem kritischen Pfad
- (+) Scope-basierte Invalidierung ist präzise — kein Cache-Flush bei unrelatierten Änderungen
- (-) ~~BackgroundTask nach Block-Aktivierung muss zuverlässig laufen (Fehler = stale Cache)~~ — entfällt mit v2.7, es gibt keinen
- (-) ~~Sekundärindex muss bei jeder Cache-Schreiboperation mitgepflegt werden~~ — entfällt mit v2.0

**Fortschreibung v2.7 (2026-08-13, CHY-24) — Write-Through entfällt:**

Der Absatz oben („Sobald ein `context_block` als `active` markiert wird, löst FastAPI einen `BackgroundTask` aus…") beschreibt eine Vorausberechnung, die mit der v2.0-Fortschreibung unvereinbar geworden ist, ohne dass es jemandem auffiel. Mit Generation-Countern enthält jeder Schlüssel die Generation **aller** beteiligten Scopes; eine Aktivierung erhöht sie und macht damit *jeden* vorausberechneten Schlüssel unerreichbar. Um den Cache warm zu halten, müsste der BackgroundTask den gesamten Schlüsselraum für die neue Generation neu berechnen — genau der DEL-Fanout, den v2.0 abgeschafft hat, nur teurer.

Hinzu kommt, dass sich der Schlüsselraum nicht aufzählen lässt: Er enthält `model_id`, und bei `auto_route` steht das Modell erst zur Anfragezeit fest.

**Entscheidung:** Kein Write-Through. Die erste Anfrage nach einer Aktivierung ist ein Cache-Miss, kompiliert und schreibt zurück. Das kostet einen PostgreSQL-Lookup pro Aktivierung und Schlüssel — gegen einen LLM-Aufruf vernachlässigbar, und der Fehlerfall ist ein Miss statt veralteter Daten.

Der Sekundärindex (`cc:scope_idx:{scope_id}`) entfällt damit ebenfalls; er war nur für den DEL-Fanout nötig und ist nie gebaut worden.

**Fortschreibung v2.0 — Generation-Counter statt DEL-Fanout (gewählte Variante):**

Statt Sekundärindex + gezieltem DEL wird pro Scope ein Generation-Counter geführt (`cc:scope_gen:{scope_id}`, INCR bei jeder Block-Aktivierung). Der Cache-Key enthält die Generationen der beteiligten Scopes sowie die `model_id` (nötig für `auto_route`/Fallback-Chains):

```
Key: cc:compiled:{workflow_id}:{node_ref}:{scope_leaf_id}:{model_id}:{gen}
```

Konsequenzen der Fortschreibung:

- (+) Invalidierung ist **implizit korrekt** — alte Keys werden nie mehr angefragt, TTL räumt auf
- (+) Sekundärindex und DEL-Logik entfallen; ein verlorener Compile-Task erzeugt höchstens einen Cache-Miss statt stale Daten
- (+) Kosten: 1–2 zusätzliche, pipelinebare Redis-GETs pro Compose — vernachlässigbar gegen einen LLM-Call
- (-) Verwaiste Keys leben bis TTL-Ablauf im Speicher (bei 3600 s unkritisch)

---

### ADR-011: Scope-abhängige Governance — Direkt-Write vs. MR-Pflicht

**Status:** Entschieden (2026-06-30)

> **Umsetzungsstand (31.10.2026):** Gebaut mit CHY-65 als `review_mode(node_type)` in `kernel/scopes.py`, Promote als `POST /v1/blocks/{id}/promote` (ADR-042). `workspace` ist fest `strict` — die Konfigurierbarkeit ist nicht gebaut. Die Rolle, die hier „Moderator“ heißt, ist im Code `reviewer`.

**Kontext:** Formale Merge Requests für jeden Prompt-Edit sind in der Praxis nicht zumutbar. Prompt-Engineering ist hochgradig explorativ — kein Fachexperte wartet auf einen Moderator-Review für die Änderung eines Adjektivs. Das führt dazu, dass Teams die Governance-Schicht umgehen und Prompts direkt in den App-Code hardcoden.

**Entscheidung: Governance-Intensität ergibt sich aus dem `node_type` des Scopes**

```
node_type = org           → strict   (MR + Moderator-Review, Pflicht)
node_type = team          → strict   (MR + Moderator-Review, Pflicht)
node_type = workspace     → strict   (default, konfigurierbar)
node_type = project       → direct   (Direkt-Commit, Block wird sofort active)
node_type = sub_project   → direct
node_type = personal      → direct
```

Implementierung: `requires_review(scope_node: ScopeNode) → bool` — die API prüft beim Block-Write ob ein MR geöffnet werden muss oder der Block direkt `active` wird.

**Promote-Pfad (project → team/global):**
Ein Fachexperte prototypt frei im `project`-Scope. Ist ein Prompt bewährt, löst ein expliziter "Promote"-Schritt einen MR in den höheren Scope aus. Das Governance-Prinzip bleibt gewahrt, ohne den explorativen Workflow zu blockieren.

**Konsequenzen:**

- (+) Fachexperten können frei im project/personal-Scope experimentieren
- (+) Governance bleibt für geteilten Kontext (global/team) vollständig erhalten
- (+) Promote-Pfad erzwingt bewusste Entscheidung beim Hochstufen eines Prompts
- (-) `node_type = workspace` braucht Konfigurierbarkeit (default: strict) — zusätzliche Komplexität
- (-) Direktschreiber im project-Scope können keine Governance-Auditpflicht erfüllen (gewollt)

**Nachtrag (2026-09-29, ADR-042):** „MR" in dieser Entscheidung heißt die Freigabe einer Block-Version — einen eigenen Merge-Request-Datensatz gibt es nicht. `requires_review(scope_node)` entsteht in CHY-65 als `review_mode(node_type)`. Auch der direkte Weg ist auditiert (`block.submitted` und `block.approved` mit `mode: direct`); der letzte Minuspunkt oben gilt damit nur noch für die fehlende *Zweitperson*, nicht für die Spur.

**Nachtrag (2026-11-05, ADR-045):** Die Freiheit der direkten Bereiche gilt für Prompt-Material. Ein Datenfluss-Baustein — Routing-Regel mit einem Modell unterhalb `critical`, gelockerte Datenschutzregel — wird auch dort eingereicht und von einer zweiten Person freigegeben. Der Minuspunkt gilt für ihn nicht mehr.

---

### ADR-012: LiteLLM und Langfuse als Stack-Bausteine (nicht Konkurrenten)

**Status:** Entschieden

> **Umsetzungsstand (31.10.2026):** Pflicht ist nur LiteLLM. Langfuse ist optional — ohne Schlüssel bleibt Tracing aus (`DEPENDENCIES.md`). Einen `ExecutionLog` gibt es nicht: Die Trace-ID steht in der Antwort von `/v1/complete` und in der Knotenausgabe eines Workflow-Laufs. Dass Langfuse-Daten in die Benchmark-Auswertung einfließen, ist nicht gebaut.

**Kontext:** LiteLLM (API-Routing) und Langfuse (Observability/Tracing) sind in der Cohereny-Infrastruktur
im Einsatz. In Positionierungstexten und Vergleichstabellen könnten sie als Alternativen oder Konkurrenten
zu Cohereny wahrgenommen werden. Diese Einordnung wäre falsch und muss in allen technischen
Dokumenten explizit klargestellt werden.

**Kontext der drei Phänomene:** Weder LiteLLM noch Langfuse adressieren eines der drei
LLM-Qualitätsphänomene (Modell-Varianz, Model Drift, Prompt Sensitivity):

- LiteLLM routet Anfragen an Modelle — kennt aber keine Übersetzungsregeln, keine CalibrationAnchors,
  keinen Kontext-Repository-Lookup.
- Langfuse macht Model Drift durch Traces *sichtbar* — erkennt aber keine Regressionen automatisch,
  löst keinen Human Oversight aus, und enthält keine kuratierten Formulierungsregeln.

**Entscheidung:** Cohereny baut auf LiteLLM und Langfuse auf, konkurriert nicht mit ihnen.

- LiteLLM ist der Model-Gateway-Layer: Cohereny gibt ihm Modell-ID + transformierten Prompt.
- Langfuse ist der Observability-Layer: Cohereny schreibt Trace-IDs in ExecutionLogs.
- Die eigentliche Adaption — alle drei Phänomene systematisch adressieren — ist ausschließlich
  Coherenys Aufgabe.

Diese Architekturentscheidung muss in Whitepaper (Abschnitt 11.3), Arc42 (Abschnitt 4.3)
und auf der Webseite konsistent wiederholt werden.

**Konsequenzen:**

- (+) Klare Positionierung: Cohereny ist die Adaptionsschicht über LiteLLM + Langfuse
- (+) Keine Code-Doppelung: Routing bleibt bei LiteLLM, Tracing bei Langfuse
- (+) Synergieeffekt: Langfuse-Daten fließen in Benchmark-Auswertung ein
- (-) Abhängigkeit von LiteLLM und Langfuse als externe Systeme — Betreiber müssen beide bereitstellen
- (-) Abgrenzungstexte müssen aktiv gepflegt werden (Gefahr des Verwischens in Kommunikation)

---

### ADR-013: Pilot SSE-Streaming — Event-Liste statt asyncio.Queue

**Status:** Entschieden (2026-07-04)

**Kontext:** Der Study Pilot läuft als Background-Thread (ThreadPoolExecutor). Browser-Clients verbinden sich per SSE (`/v1/pilots/runs/{id}/stream`). Bei einem Page-Reload reißt die SSE-Verbindung ab und eine neue wird aufgebaut — eine Queue würde die bereits gelieferten Events verlieren.

**Problem mit asyncio.Queue:** `Queue.get()` im `finally`-Block einer SSE-Generator-Coroutine (CancelledError bei Disconnect) würde die Queue leeren. Die nächste SSE-Verbindung erhält sofort `eof` — der Run-Fortschritt ist verloren.

**Entscheidung:** In-memory Event-Liste pro Run (`_run_events: dict[UUID, list[dict]]`) kombiniert mit einem `asyncio.Event` als Notification-Mechanismus:

```python
_run_events:  dict[UUID, list[dict]]       # append-only, nie gelöscht bis run done+drained
_run_notify:  dict[UUID, asyncio.Event]    # wird gesetzt bei jedem neuen Event
_run_done:    set[UUID]                    # gesetzt wenn Background-Thread endet
```

Jede SSE-Verbindung hält einen eigenen `cursor`-Index. Bei Reconnect startet cursor=0 und replayed die komplette History. `CancelledError` (Disconnect) verlässt den Generator ohne State-Mutation — die Liste bleibt erhalten.

**Thread → Event-Loop-Bridge:** `main_loop.call_soon_threadsafe(_push_event, run_id, event)` — der Background-Thread schreibt nie direkt in asyncio-Objekte.

**Thread-local Callbacks:** `set_emit` / `set_cancel_event` / `try_emit` / `check_cancel` in `nodes.py` erlauben es LangGraph-Nodes, Fortschrittsereignisse zu emittieren und auf Cancel zu reagieren, ohne ihre Signatur zu ändern.

**Cancel-Mechanismus:** `DELETE /v1/pilots/runs/{id}` setzt ein `threading.Event`. Nodes prüfen dieses Event an jedem Batch-/Node-Boundary (`check_cancel()`). Bei gesetztem Event → `WorkflowCancelledError` → Thread-Exit → httpx-Socket schließt → Ollama bekommt TCP-Reset und stoppt die Generierung.

> **Nachtrag (2026-11-07, CHY-153):** Wartet der Lauf am Freigabe-Gate, löst der Abbruch das Gate mit dem dritten Wert `cancelled`, nie mit einer Entscheidung, und der Lauf endet als `failed` mit der Meldung des Abbruchs. Bis dahin schickte der Abbruch `rejected`: Der Thread verbuchte eine Art.-14-Ablehnung, der Trigger aus 014 wies sie ab, und der Lauf blieb ohne Ergebnis stehen.

**Konsequenzen:**

- (+) Reconnect-safe: Page Reload, mehrere Tabs, Browser-Back — alles sicher
- (+) Cancel propagiert zuverlässig bis zum laufenden LLM-Call
- (+) Keine Signature-Änderungen an LangGraph-Nodes nötig (thread-local)
- (-) Ereignisse leben nur im RAM — Server-Neustart löscht in-flight Events
- (-) Abandonierte Runs ohne explizites EOF akkumulieren in `_run_events` bis zum Prozess-Ende

**Status-Update v2.0:** Migration 004 (`events_json JSONB` in `pilot_runs`) ist deployed (04.07.2026); die **Write-/Replay-Logik** (Bulk-Write am Run-Ende, Replay bei GET für beendete Runs) ist noch offen → Issue #42 bleibt bis dahin offen. SSE-Live-Updates zudem noch nicht abschließend getestet (#15).

**Status-Update (31.10.2026):** Write und Replay sind gebaut (CHY-58): Am Laufende stehen die Ereignisse in `pilot_runs.events_json`, und einen Lauf, den der Prozess nicht kennt — nach einem Neustart oder weil er beendet ist —, gibt der Stream von dort wieder; ein entschiedenes Gate kommt dabei nicht als offen zurück. Die drei Strukturen oben hält seit CHY-104 `RunRegistry` (`contexts/pilots/registry.py`). #42 und #15 sind Nummern des früheren Trackers, beide erledigt mit CHY-58.

---

### ADR-014: Kryptokonzept — at-rest-Verschlüsselung (entschieden 05.11.2026)

**Status:** Entschieden (2026-11-05, Thomas; CHY-15): Ruhende Daten verschlüsselt der Betreiber; Cohereny liefert das Kryptokonzept für Betreiber. Vorgeschlagen in v2.0 als LUKS auf OS-Ebene — das gilt jetzt als Vorschlag für die Referenzinstallation (INFRA-163)

> **Umsetzungsstand (05.11.2026):** Die Entscheidung betrifft den Betrieb, nicht die Software — Cohereny verschlüsselt ruhende Daten nicht selbst — mit einer Ausnahme seit 07.11.2026, der Zuordnung der Platzhalter je Lauf (siehe Nachtrag); wer es betreibt, wählt die Maßnahme. Das Kryptokonzept für Betreiber ist `CRYPTOGRAPHY.md` im Repository (CHY-15): Zuständigkeit, was Cohereny selbst prüft (RS256, Pflicht-Claims, Issuer-Liste), Anforderungen an Transport, Ruhezustand und Geheimnisse, Verzeichnis aller Geheimnisse. Der Vorschlag unten beschreibt die Referenzinstallation (INFRA-163).

**Kontext:** Ohne eigene Maßnahme liegen alle PostgreSQL-Daten (Traces, Audit-Logs, Actor-Records) unverschlüsselt auf dem Storage des Datenbank-Hosts. BSI IT-Grundschutz CON.1 (Kryptokonzept) und CRA Anhang I Teil I Nr. 2 Buchst. e fordern Vertraulichkeitsschutz ruhender Daten. Auf aktueller x86-Hardware mit AES-NI ist der CPU-Overhead für LUKS vernachlässigbar.

**Optionen:**

| Option                                                         | Aufwand          | Performance-Impact        | Granularität                 |
| -------------------------------------------------------------- | ---------------- | ------------------------- | ----------------------------- |
| LUKS auf OS-Ebene (Daten-Volume verschlüsselt)                | Einmalig, mittel | Minimal (Hardware-AES)    | Grobgranular — ganzer Volume |
| PostgreSQL`pg_crypto` + spaltenweise Verschlüsselung        | Hoch, dauerhaft  | Merklich bei vielen Reads | Feingranular — Column-Level  |
| Transparent Data Encryption via PostgreSQL-Extension           | Mittel           | Niedrig                   | Mittelgranular — Tablespace  |
| Application-Level Encryption (FastAPI Encrypt/Decrypt-Schicht) | Sehr hoch        | Hoch                      | Feingranular — Field-Level   |

**Entscheidungsvorschlag (v2.0):** LUKS (dm-crypt) auf dem OS-Volume des Datenbank-Hosts. Begründung: einmalige Konfiguration, AES-NI hält den Overhead minimal, kein Code-Change, adressiert BSI CON.1 und CRA Anhang I Teil I Nr. 2 Buchst. e beim Betreiber als Baseline. Umsetzungshinweise: (1) Migration = Volume neu aufsetzen + Restore aus Backup — zugleich der fällige **Restore-Test** (zwei Fliegen); (2) Keyfile via `crypttab` für unbeaufsichtigten Boot, zusätzlich Notfall-Passphrase im Passwort-Manager; (3) `pg_crypto`/TDE nur nachrüsten, falls ein Auditor Column-Level fordert. Performance-Test (`cryptsetup benchmark` + pgbench vorher/nachher) dokumentieren, dann Status auf „Entschieden" heben.

**Konsequenzen:**

- (+) Adressiert BSI CON.1 und CRA Anhang I Teil I Nr. 2 Buchst. e beim Betreiber mit minimalem Aufwand
- (-) LUKS: kein Schutz gegen privilegierte DB-Zugriffe innerhalb des laufenden Systems
- (-) Muss vor erstem Produktiv-Einsatz mit Patientendaten abgeschlossen sein (Blockiert: DPIA, CHY-18)

**Nachtrag (2026-11-07, Thomas; CHY-234): eine Ausnahme, eng gefasst.**

Seit dem Ersetzen am Eingang (ADR-018, zweiter Nachtrag) speichert Cohereny Workflow-Läufe nur mit Platzhaltern. Die Zuordnung zurück zu den Werten ist der Teil eines Laufs, der nur aus personenbezogenen Daten besteht, und gegen einen Dump durch jemanden mit Datenbankzugang hilft die Volume-Verschlüsselung des Betreibers nicht. Diese Zuordnung verschlüsselt Cohereny selbst, in der Anwendung (`kernel/mapping_cipher.py`):

- AES-256-GCM aus `cryptography`; die Lauf-ID ist zugehörige Daten, eine Zuordnung öffnet also nicht für einen anderen Lauf, und ein geändertes Byte lässt das Entschlüsseln scheitern, statt eine andere Zuordnung zu liefern. Jede Versiegelung zieht eine neue Nonce.
- Der Schlüssel kommt aus `PII_MAPPING_KEYS`; die Datenbank sieht ihn nie. Ohne gültigen Schlüssel startet die API nicht.
- Wechsel: Der erste Schlüssel versiegelt, jeder genannte öffnet. Der alte bleibt, bis seine Läufe die Inhaltsfrist hinter sich haben. Geht ein Schlüssel verloren, sind die Werte seiner Läufe weg; die Platzhalter bleiben.

Gegenüber der Tabelle oben ist das Application-Level Encryption für genau eine Tabelle, nicht für jedes Feld — der Aufwand bleibt deshalb klein. Alles andere bleibt beim Betreiber: Volumes, Backups, Transport. `CRYPTOGRAPHY.md` führt den Schlüssel im Verzeichnis der Geheimnisse, die Referenzinstallation hält je Stufe einen (INFRA-169).

---

### ADR-015: WAF / DAST für API-Endpoints (Entscheidung offen)

**Status:** Entscheidungsvorschlag v2.0: **SAST sofort + DAST-Baseline nightly, kein WAF** — formale Bestätigung ausstehend (→ CHY-7)

> **Umsetzungsstand (01.11.2026):** (1) umgesetzt — SAST mit Semgrep läuft in der CI des Projekts in jeder Pipeline, meldend (CHY-75). (2) nicht als OWASP-ZAP-Scan: Stattdessen läuft Contract-Fuzzing mit Schemathesis gegen eine laufende Instanz, je Oberfläche und strikt (`scripts/contract_fuzz.py`, CHY-124); es prüft Contract-Treue und Fehlerklassen, keine Angriffe. (3) Kein WAF. Die Entscheidung zu WAF und DAST kommt mit M7b (CHY-7).

**Kontext:** In der Referenzinstallation ist die API nur intern erreichbar. BSI APP.3.1 (Web-Anwendungen) und PCI-DSS Req. 6 fordern Web Application Firewall und DAST (Dynamic Application Security Testing). Eine klassische WAF ist bei rein interner Exposition kaum sinnvoll — DAST in CI ist realistischer.

**Optionen:**

| Option                                            | Ansatz                                                | Aufwand                                          |
| ------------------------------------------------- | ----------------------------------------------------- | ------------------------------------------------ |
| OWASP ZAP in CI (DAST gegen laufende dev-Instanz) | Automatischer API-Scan bei jedem Build                | Mittel — braucht laufende Instanz im Runner     |
| Semgrep SAST (CHY-13)       | Statische Code-Analyse, kein laufender Service nötig | Niedrig                                          |
| nginx ModSecurity (WAF als Sidecar vor FastAPI)   | WAF-Regelwerk vor API                                 | Hoch — Konfiguration komplex, falsche Positives |
| Cloudflare WAF                                    | Nur wenn API öffentlich via Tunnel exponiert         | Nicht anwendbar bei interner Nutzung             |

**Entscheidungsvorschlag (v2.0):** (1) **Semgrep SAST** (CHY-13) sofort in CI — läuft ohne Instanz, Sekunden pro Build. (2) **OWASP ZAP Baseline-Scan** als **nightly** Job gegen die laufende dev-Instanz (nicht pro Build — hält die CI-Laufzeit sauber; Baseline-Modus statt Full-Scan reicht intern). (3) **Kein WAF-Sidecar**, solange die API nicht extern exponiert ist; Neubewertung beim OSS-Release/Tunnel-Freischaltung. (4) Manueller Pentest bleibt Pflicht vor reguliertem Einsatz.

**Konsequenzen:**

- (+) DAST deckt API-spezifische Schwachstellen auf (IDOR, Injection, Auth-Bypass)
- (-) OWASP ZAP erhöht CI-Laufzeit signifikant
- (-) Kein Ersatz für manuelle Penetrationstests bei reguliertem Einsatz (DORA, MaRisk)

---

### ADR-016: DSGVO Lösch-Endpoint — Pseudonymisierung statt Hard Delete (Entscheidung offen)

**Status:** Entscheidungsvorschlag v2.0: **Pseudonymisierung mit Salt** — formale Bestätigung ausstehend (→ Issue #46)

> **Umsetzungsstand (31.10.2026):** Gebaut ist nicht dieser Vorschlag, sondern die Löschung nach ADR-025: `POST /v1/admin/actors/{actor_ref}/anonymize` (CHY-34). Die Actor-ID bleibt, `idp_sub` wird `NULL`, `display_name` wird `[anonym]`, `is_anonymized` und `anonymized_at` werden gesetzt — kein Hash, kein Salt, kein `/erase`. Die Löschung erreicht nur den Actor; Inhalte, die Personenbezug tragen können (Prompts, Ausgaben, Laufergebnisse), erreicht sie nicht — dafür ist ADR-018 vorgesehen (CHY-63, offen). „#46“ ist eine Nummer des früheren Trackers.

**Kontext:** DSGVO Art. 17 (Recht auf Löschung) erfordert, dass personenbezogene Daten auf Anfrage gelöscht werden können. Cohereny nutzt das Actor-Pattern mit Append-Only-Versionierung — ein klassisches Hard Delete würde die referentielle Integrität des Audit Trails verletzen und Lücken in der Nachvollziehbarkeit erzeugen.

**Optionen:**

| Option                                            | DSGVO-Konformität                        | Audit-Trail-Integrität                        | Aufwand |
| ------------------------------------------------- | ----------------------------------------- | ---------------------------------------------- | ------- |
| Hard Delete (alle Rows mit Actor-UUID entfernen)  | ✅ vollständige Löschung                | ❌ Lücken im Trail, Referenzfehler            | Mittel  |
| Pseudonymisierung (UUID →`anon_<sha256[:12]>`) | ✅ kein Personenbezug nach Art. 4(5) mehr | ✅ Trail bleibt konsistent, Sequenzen erhalten | Mittel  |
| Anonymisierung mit festem Sentinel-UUID           | ✅                                        | ✅ einfachste Implementierung                  | Niedrig |

**Entscheidungsvorschlag (v2.0):** Pseudonymisierung — Actor-UUID wird durch `anon_<sha256(salt‖uuid)[:12]>` ersetzt, deterministisch (gleicher Actor bleibt im Trail als „derselbe anonyme Actor" erkennbar), aber nicht rückverfolgbar. **Wichtig: mit Salt** — ohne Salt wäre der Hash bei bekannter UUID (Logs, Backups) trivial rückrechenbar. Salt liegt im Secret-Store des Betreibers; die Löschung des Salts wirkt als endgültige Anonymisierung (ErwG 26). Endpoint-Name einheitlich `/v1/actors/{actor_ref}/erase` (ersetzt das bisherige `/anonymize` in §5.7 und die abweichenden Namen in den Compliance-Tabellen).

**Konsequenzen:**

- (+) Actor-Pattern und Append-Only-Invariante bleiben architektonisch konsistent
- (+) Audit Trail bleibt vollständig auswertbar (Sequenzen und Kausalitäten erhalten)
- (+) Art. 17 DSGVO erfüllt nach Pseudonymisierung
- (-) Kein 100%-iges "Vergessen" — Restrisiko bei De-Anonymisierung über externe Datensätze
- (-) Sentinel-Strategie muss im VVT (Art. 30) dokumentiert und im Datenschutzkonzept vermerkt sein

---

### ADR-017: Governance-Ratchet für Privacy-Regeln (neu in v2.0)

**Status:** Vorgeschlagen (v2.0)

> **Umsetzungsstand (31.10.2026):** Angenommen und umgesetzt in der Fortschreibung v2.17 unten (CHY-62, Migration 016). Deren Satz „`routing_rule` existiert nicht“ ist seit ADR-037/038 überholt: Die Routing-Regel ist ein Block vom Typ `routing_rule`, und der Ratchet gilt für sie ohne eigenen Code (ADR-038, Folge 3).

**Kontext:** ADR-011 erlaubt Direct-Write im project/personal-Scope; die Konfliktregel „tiefster Scope gewinnt" plus `force_override` würde es erlauben, Privacy-Vorgaben (z. B. `cloud_routing_allowed=false` aus dem healthcare-Template) per ungereviewtem project-Block zu kippen — im Widerspruch zum Versprechen „kein Override ohne MR" der Referenz-Designs (§6.6/§6.7).

**Entscheidung:** Ratchet-Prinzip — privacy-relevante Regeln dürfen scope-abwärts nur **verschärft**, nie gelockert werden:

1. `routing_rule.action` erhält ein abgeleitetes Feld `privacy_impact: restrictive | permissive`.
2. Die Kompositions-Engine lehnt permissive Overrides gegen höher-scopige restriktive Regeln hart ab (`409`, MR in den **höheren** Scope erforderlich).
3. Zusätzlich können org-Blöcke `locked: true` tragen — nicht überschreibbar, unabhängig von `force_override`.

**Konsequenzen:**

- (+) Das Healthcare-/Banking-Versprechen ist im Modell erzwungen, nicht nur dokumentiert
- (+) Ein Vergleichsschritt vor den bestehenden Konfliktregeln — geringer Implementierungsaufwand
- (-) `privacy_impact`-Ableitung muss pro Regel-Typ definiert und getestet werden (Pflicht-Testfall: project-Block versucht `cloud_routing_allowed=true` gegen healthcare-Template → muss scheitern)

**Fortschreibung v2.17 (2026-09-03, CHY-62) — erklärt statt abgeleitet, am Block statt an der Regel:**

**Status:** Angenommen (umgesetzt)

Die Entscheidung oben hängt das Feld an `routing_rule.action`. **`routing_rule` existiert nicht** — `router/` ist eine YAML-Konfiguration und eine `RouterConfig`-Klasse, nichts, was Regeln als Zeilen führt. Das Feld sitzt deshalb am Block, wo Governance-Aussagen tatsächlich leben.

Damit ändert sich mehr als der Ort. **Abgeleitet wird nichts, es wird erklärt** — und zwar aus einem Grund, der beim Bauen erst sichtbar wurde:

> **Die Komposition überschreibt technisch überhaupt nichts.** `ComposedContext.as_prompt` fügt alle Blöcke aneinander, flach nach tief; der tiefere steht lediglich weiter hinten im Text. Die Notiz „tiefster Scope gewinnt" beschreibt eine Lesart, keinen Mechanismus. Ein Widerspruch zwischen zwei Prosatexten ist maschinell nicht feststellbar, und ob ein Modell dem Org-Satz oder dem Projekt-Satz folgt, entscheidet dieses System nicht.

Eine Zusicherung, die davon abhängt, wie ein Modell zwei Absätze gewichtet, ist keine Zusicherung. Ein Block muss also **sagen**, worüber er spricht:

1. **`governance_key`** — welche Regel gemeint ist. Ohne ihn lässt sich nicht feststellen, dass zwei Blöcke dieselbe Sache regeln. Kein Freitext, sondern **Fremdschlüssel auf `governance_keys`**: ein Tippfehler ließe zwei Blöcke wie verschiedene Regeln aussehen und höbe den Schutz *lautlos* auf. Ein neuer Schlüssel kostet einen bewussten Eintrag — bei Governance-Vokabular ein Vorteil
2. **`privacy_impact`** (`restrictive` | `permissive`) — in welche Richtung. Nur gemeinsam mit dem Schlüssel setzbar, per CHECK erzwungen: eine Richtungsangabe ohne Bezug läse sich wie Governance und täte nichts
3. **`locked`** — stärker als das Ratchet: **kein** tieferer Scope darf den Schlüssel nennen, auch nicht strenger. Eine Wiederholung eine Ebene tiefer schafft einen zweiten Ort für eine Regel, und zwei Quellen für eine Wahrheit laufen auseinander. Eine berechtigte Verschärfung gehört in den Scope des gesperrten Blocks oder unter einen eigenen Schlüssel

**Zwei Durchsetzungspunkte statt einem.** Die ursprüngliche Entscheidung nennt nur die Kompositions-Engine. Ein 409 aus einem *Lesepfad* heißt aber: ein Scope wird unkomponierbar, weil jemand etwas freigegeben hat, und der Fehler erscheint zur Laufzeit im Workflow — weit weg von der Ursache. Geprüft wird deshalb **bei der Freigabe** (dort steht ein Mensch, dort ist die Korrektur billig) *und* **bei der Komposition** als Auffangnetz. Das zweite ist nicht überflüssig: mehrere Versionen eines Blocks können gleichzeitig `active` sein — das ist Absicht, damit ein Block während der Prüfung seines Nachfolgers weiter komponiert wird —, und Zeilen erreichen `active` auf Wegen, die nicht durch den Endpunkt führen. Am 13.08.2026 tauchte genau durch diese Lücke ein zurückgezogener Block mit altem Inhalt wieder auf.

**Der Governance-MR ist der bestehende Review-Workflow.** Punkt 3 der Entscheidung verlangt, dass `locked` nur über einen ausdrücklichen MR gelöst wird; ein MR-Datenmodell war für CHY-65 vorgesehen — seit ADR-042 gibt es keins, freigegeben wird die Block-Version. Der Schutz besteht trotzdem schon: die drei Spalten sind **nur im Zustand `draft` änderbar** (Trigger, Migration 016), ein PATCH auf einen veröffentlichten Block erzeugt eine neue Draft-Version, und deren Freigabe verlangt die Rolle `reviewer` (installationsweit — Schreibrechte je Bereich: CHY-243).

**Zwei Hintertüren, die beim Bauen zugemacht wurden** — beide hätten die Zusicherung still ausgehebelt:

| Weg | Was passiert wäre |
|---|---|
| `UPDATE` auf einen freigegebenen Block | `locked` von `true` auf `false`, ohne Review. Die drei Spalten sind jetzt Teil des Einfrierens außerhalb `draft` |
| `cc_supersede_block` | Die Funktion listet ihre INSERT-Spalten einzeln auf; die drei neuen hätten Standardwerte bekommen. Ein Nachfolger ohne Anspruch — durch genau die Funktion, die der vorgesehene Weg *ist*. Sie werden jetzt übernommen |

Der **Pflicht-Testfall** der ursprünglichen Entscheidung ist wörtlich umgesetzt. Ebenso wichtig sind die Gegenproben: Eine Prüfung, die *jeden* Anspruch weiter unten ablehnte, bestünde den Pflichtfall und wäre eine **Wand statt eines Ratchets** — ohne dass es auffiele, bis ein Team eine Regel für sich nicht mehr verschärfen könnte.

---

### ADR-018: PII-Redaction vor dem LLM (neu in v2.0)

**Status:** Entschieden (2026-11-05, Thomas; CHY-63): ersetzt wird an der Grenze vor jedem Modellaufruf; Ersetzen am Eingang mit verschlüsselter Zuordnung gebaut (CHY-234, 07.11.); siehe Nachträge. Vorgeschlagen in v2.0

> **Umsetzungsstand (07.11.2026):** An der Grenze gebaut (CHY-63): mitgelieferte Muster, Bezeichner des Aufrufers, Regeln des Bereichs als `redaction_rule`-Blöcke (Migration 032). Am Eingang gebaut (CHY-234): Workflow-Läufe nur mit Platzhaltern, Zuordnung je Lauf verschlüsselt in `pii_mappings` (Migration 036), Auflösen mit Audit. Pilotläufe tragen die Werte bis zu ihrer Ablösung (CHY-172). Offen: Art.-17-Suche in Lauf-Inhalten, Abgleich mit den Rechenschritten und Anbindung der UI (CHY-239, M7); NER (CHY-235, M7).

**Kontext:** Payload-Daten (Prompts, Outputs, Traces — §8.3 Kategorie 3) können personenbezogene Inhalte tragen, die das Actor-Pattern nicht erfasst. Ohne Gegenmaßnahme sind Langfuse-Traces, ExecutionLogs und `result_json` bei Art.-17-Anfragen praktisch unlöschbar (Append-Only, Backups).

**Entscheidung:** Identifizierende Angaben werden **vor** dem ersten LLM-Aufruf im Ingest-Pfad durch Platzhalter ersetzt (`{{PATIENT_1}}`, `{{GEB_DATUM_1}}` …). Das Mapping liegt in einer separaten, hard-deletable Tabelle (`pii_mappings`), außerhalb der Append-Only-Regeln.

Stufen: (1) regelbasiert/deterministisch (Namenslisten aus Dokument-Metadaten, Datums-/Adress-Regex) — **kein** LLM-basiertes Redacting (sonst landet PII im Redaction-Prompt); (2) **on-premise NER** (spaCy `de_core_news_lg` oder GliNER für mehrsprachige Entitätserkennung) als Phase-2-Pflichtanforderung vor der Kompositions-Engine — kein Cloud-NER, kein API-Aufruf mit unredaktierten Daten.

**Konsequenzen:**

- (+) Art. 17 wird trivial: Mapping löschen → alle nachgelagerten Artefakte (Logs, Traces, Cache, Backups ab dann) sind anonym
- (+) Cloud-Routing wird eine Sicherheitsstufe robuster (abstrahierte UND pseudonyme Daten)
- (+) Buch-/Whitepaper-Pattern: „Pseudonymisierung als Routing-Enabler"
- (-) Re-Identifikation im UI (Anzeige für den Arzt) braucht einen kontrollierten Resolve-Schritt mit Audit-Eintrag

**Nachtrag (2026-11-05, Thomas; CHY-63): Ersetzt wird an der Grenze; Ersetzen am Eingang kommt dazu.**

Der Kontext oben ging davon aus, dass Lauf-Inhalte praktisch unlöschbar sind. Seit ADR-046 vergehen sie: `run_content` wird nach seiner Frist geleert. Unlöschbar aus Sicht von Cohereny ist nur noch, was die Installation verlassen hat — beim Modellanbieter, in den Protokollen des Gateways, in Langfuse. Daraus folgt die Reihenfolge:

1. **An der Grenze (gebaut):** Unmittelbar vor jedem Modellaufruf — `/v1/complete` und jeder `llm_call` eines Laufs — ersetzt `core/completion.py`, die einzige Stelle, die ein Modell aufruft, Bezeichner durch Platzhalter (`{{EMAIL_1}}`, `{{PERSON_1}}`) und setzt die Werte in der Antwort zurück. Modell, Gateway und Trace sehen nur Platzhalter, auch in Fehlertexten, die das Gateway zurückgibt. Die Zuordnung lebt im Speicher, solange der Aufruf läuft; es gibt keine Tabelle und keinen Schlüssel. Die Cohereny-Datenbank speichert die echten Werte, geschützt durch Rollen, Inhaltsfrist (ADR-046) und die Verschlüsselung des Betreibers (ADR-014).
2. **Bei jedem Modell**, nicht nur bei gehosteten: Langfuse zeichnet bei jedem Modell auf, eine Fallback-Kette kann innerhalb einer Anfrage von lokal auf gehostet springen, und „lokal“ in der Routing-Konfiguration beweist nicht, dass die Daten im Haus bleiben.
3. **Erkennung Stufe 1, ohne Modell:** mitgelieferte Muster (E-Mail, IBAN, Telefon, Steuer-ID mit Prüfziffer, Krankenversichertennummer, Geburtsdatum nur mit Kennzeichnung — nicht jedes Datum, sonst wird „Termin am 05.11.“ zum Platzhalter), Bezeichner, die der Aufrufer nennt (`identifiers_to_redact`; bei einem Lauf in seiner Eingabe, gilt für jeden Modellaufruf des Laufs), und die Regeln des Bereichs. Namen, die niemand nennt, findet Stufe 1 nicht; SECURITY.md sagt das ausdrücklich. Stufe 2 mit NER → M7 (CHY-235).
4. **Regeln des Bereichs als Blöcke (gebaut):** Blocktyp `redaction_rule` (Migration 032), geprüft, versioniert, entlang des Baums vererbt, nie komponiert — wie `routing_rule` (ADR-037). Was entscheidet, welche Daten das Haus verlassen, geht durch denselben Prüfweg wie das, was entscheidet, wohin sie gehen. Regeln addieren sich; was oben hinzukam, nimmt unten niemand weg. Eine mitgelieferte Regel abzuschalten ist die einzige Lockerung: nur in einem Bereich ohne Elternbereich, nur als `pii_redaction` mit `privacy_impact: permissive` erklärt — damit ein Datenfluss-Baustein mit vier Augen (ADR-045) —, und die Ratsche (ADR-017) verbietet es neben einer restriktiven Aussage im selben Bereich. Der Governance-Schlüssel `pii_redaction` aus Migration 016 bekommt damit seine Bedeutung. Muster, deren Backtracking jeden Prompt aufhalten könnte (`(a+)+`), werden beim Freigeben abgewiesen; Python-`re` kennt kein Timeout.
5. **Ersetzen am Eingang ist fest eingeplant (→ M5, CHY-234; am 06.11. aus M7 vorgezogen)**, unabhängig vom Ergebnis der DPIA: Lauf-Inhalte nur mit Platzhaltern, Zuordnung in `pii_mappings` je Lauf, in der Anwendung verschlüsselt, mit der Inhaltsfrist geleert, Auflösen mit Audit. Es baut auf der Grenze auf — die Erkennung liefert ihre Zuordnung als Objekt, die Grenze verwirft es, der Eingang wird es speichern —, und die Grenze bleibt als zweite Linie. Die Suche je Person per HMAC (Art. 17), den Abgleich mit den Rechenschritten und die UI bringt CHY-239 (→ M7).

Gegenüber dem Entscheidungstext heißt das: `pii_mappings` ist erst mit CHY-234 eine Tabelle. Bis dahin gilt „Art. 17 wird trivial“ nur für das, was die Installation verlassen hat. Der Resolve-Schritt aus der letzten Konsequenz entfällt an der Grenze — der Aufrufer bekommt die Werte zurück, die er selbst geliefert hat — und kommt mit CHY-234.

**Verworfen:** Ersetzen nur bei gehosteten Modellen (Punkt 2). Regeln als Tabelle mit Admin-API — eine zweite Quelle neben `pii_redaction`. Muster ohne Prüfung der Backtracking-Form.

**Zweiter Nachtrag (2026-11-07, Thomas; CHY-234, Entscheidungen E1–E9 vom 06.11.): Ersetzen am Eingang ist gebaut.**

1. **Start:** Jeder Text der Laufeingabe wird ersetzt, bevor die erste Zeile geschrieben ist (E2) — mit den mitgelieferten Mustern, den Regeln des Bereichs und den Bezeichnern des Aufrufers. Die Liste `identifiers_to_redact` wird nicht gespeichert: Jeder genannte Wert bekommt einen Platzhalter in der Zuordnung, auch wenn die Eingabe ihn nicht enthält.
2. **Schritte:** Jeder Schritt sucht nach jedem Wert, den der Lauf kennt — genannt oder per Muster gefunden. Die Antwort des Modells wird geprüft und mit Platzhaltern gespeichert, im Lauf nicht zurückgesetzt (E3); Werte, die erst die Antwort bringt, ergänzen die Zuordnung. Nummeriert wird fortlaufend je Lauf; ergänzt wird unter Zeilensperre, und haben zwei Schritte zugleich nummeriert, wandern die neuen Namen des zweiten (E1). So meint `{{PERSON_2}}` nie zwei Menschen.
3. **Begründungen am Gate** werden ebenso ersetzt (E7).
4. **Zuordnung:** je Lauf eine Zeile in `pii_mappings` (Migration 036), in der Anwendung verschlüsselt (ADR-014, Nachtrag), mit der Inhaltsfrist geleert und mit dem Lauf gelöscht; sichtbar, wem der Lauf sichtbar ist (ADR-047).
5. **Auflösen:** `POST /v1/workflows/runs/{id}/resolve` für die Person, die den Lauf gestartet hat, und ab `reviewer` für alle, die ihn sehen (E4). Jeder Aufruf schreibt `workflow.run_content_resolved` — wer, welcher Lauf, welche Schritte, nie ein Wert. Damit ist der Resolve-Schritt aus der letzten Konsequenz gebaut.
6. **Schlüssel:** Ohne gültigen `PII_MAPPING_KEYS` startet die API nicht (E5); die Referenzinstallation hält je Stufe einen (E9, INFRA-169).
7. **Bestand:** Läufe von vor Migration 036 haben keine Zeile und laufen weiter wie bisher, mit ihren Werten bis zum Ende ihrer Frist (E6). Ob ein Lauf Platzhalter führt, entscheidet seine Zeile, nie ein vorhandener Schlüssel: Ein fehlender Schlüssel stoppt den Lauf, er lässt keinen Wert durch. Pilotläufe tragen die Werte, bis sie auf den Runner umziehen (CHY-172).

Belegt durch einen Integrationstest, der nach einem Lauf über zwei Modellaufrufe jede Zeile jeder Tabelle nach den Werten durchsucht — ohne Treffer —, und durch je eine Gegenprobe für jeden Baustein. Dabei gefunden und behoben: Der Trace zeichnete bei `/v1/complete` die Antwort des Modells ungeprüft auf (CHY-244). Die Suche je Person nach Art. 17 bleibt CHY-239 (→ M7).

**Dritter Nachtrag (2026-11-08, Thomas; CHY-250): Was „vor jedem Modellaufruf“ abdeckt.** Punkt 1 nennt `core/completion.py` die einzige Stelle, die ein Modell aufruft. Das gilt für `/v1/complete` und die `llm_call`-Knoten der Workflows. Die LangGraph-Piloten rufen das Modell über LangChain selbst auf, mit den Werten, und der Bank-Pilot schreibt sie auch in seinen Langfuse-Trace. `/v1/route` nennt nur ein Modell; ein Client, der danach das Gateway selbst aufruft, schickt, was er schickt. Gefunden bei der Prüfung der öffentlichen Dateien vor dem Schnappschuss vom 08.11. Die Piloten sind seitdem abgekündigt (ADR-028, Nachtrag 08.11.); beides steht als Restrisiko in `DATA-PROTECTION.md` §5. Der Entscheidungstext bleibt stehen.

**Betrifft:** CHY-63, CHY-234, CHY-244, CHY-239, CHY-235, CHY-18, CHY-205, ADR-014, ADR-017, ADR-037, ADR-045, ADR-046, ADR-047

---

### ADR-019: A/B-Benchmark-Modus — Wirksamkeitsnachweis der Adaption (neu in v2.0)

**Status:** Entschieden (2026-10-06, Thomas; ADR-043) — vorgeschlagen in v2.0; Umsetzung CHY-64 (offen)

> **Umsetzungsstand (31.10.2026):** Das Schema steht (Migration 028: `benchmark_runs.mode` mit `drift` | `ab_adaptation`, Variante `composed` | `raw` je Messung). Läufe (CHY-68), A/B-Auswertung und Report (CHY-64) sind nicht gebaut. Einen `NightlyBenchmarkReport` gibt es nicht; der Benchmark läuft nach Zeitplan, bei Änderungen und auf Abruf (ADR-044), der Report kommt mit CHY-64.

**Kontext:** Qualitätsziel Nr. 1 und Szenario Q1 fordern „Adaption verbessert Output messbar > 10 % gegenüber unadaptiertem Prompt" — das Benchmark-Modell (Domäne 5) misst aber nur Modell × Anchor. Ohne A/B-Messung bleibt die Kernthese des Projekts unbelegt.

**Entscheidung:** `BenchmarkRun.mode: drift | ab_adaptation`. Im `ab_adaptation`-Modus wird je Anchor **zweimal** ausgeführt (roher Prompt vs. Composed Context) und das Delta als `adaptation_gain` im `NightlyBenchmarkReport` ausgewiesen. Eine Teilmenge reicht (1 Anchor je `task_type`); Bewertung über die vorhandene Validator-Pyramide (ADR-006).

**Konsequenzen:**

- (+) „Gemessene Adaptionsverbesserung: +X %" — die eine Zahl, an der sich die Adaption messen lassen muss
- (+) Nutzt vorhandene Validatoren; Mehrkosten nur doppelte LLM-Calls auf der Teilmenge
- (-) Roher Prompt muss pro Anchor definiert/eingefroren sein (Baseline-Disziplin)

**Anmerkung Prompt-Injection (CHY-5):** Dieselbe Maschinerie trägt Injection-Regressionstests — präparierte Dokumente mit eingebetteten Anweisungen als eigene CalibrationAnchors; erwartetes Verhalten: Ignorieren. Damit testet der Benchmark die Injection-Resilienz bei jedem Lauf mit (nach Zeitplan, bei Änderungen und auf Abruf, ADR-044; Läufe kommen mit CHY-68).

---

### ADR-020: Model-Sunset-Workflow (neu in v2.0)

**Status:** Vorgeschlagen (v2.0)

> **Umsetzungsstand (31.10.2026):** nicht gebaut, Umsetzung CHY-158. `model_profile`, `translation_rules` und `optimal_prompts` gibt es nicht: Modelle stehen in `router/routing-config.yaml`, Kandidatenketten in `routing_rule`-Blöcken (ADR-038), Testfälle sind Anker-Blöcke (ADR-043). Die GIN-Index-Zeile entfällt (ADR-043). Der Benchmark läuft nach Zeitplan, bei Änderungen und auf Abruf (ADR-044); dass er stillgelegte Modelle überspringt, gilt weiter.

**Kontext:** Die BaFin-Orientierungshilfe KI (18.12.2025) fordert den vollständigen Lebenszyklus **inkl. Stilllegung** — die selbst notierte Lücke „Phase 6 Decommissioning fehlt". Konkret: Wird ein Modell aus der LiteLLM-Config entfernt, referenzieren `translation_rules` (source/target), `calibration_anchors` (optimal_prompts je model_id), `routing_rules` und `model_profiles` ins Leere — ohne definiertes Verhalten.

> **Seit ADR-042 (29.09.2026):** Einen eigenen Merge-Request-Datensatz gibt es nicht; der „Sammel-MR" unten wird zu je einem Entwurf pro betroffenem Block. Siehe dort.

**Entscheidung:** `model_profile → deprecate` löst eine Konsistenzprüfung aus (welche aktiven Blöcke referenzieren das Modell?) und erzeugt einen **Sammel-MR** „abhängige Regeln deprecaten / Fallback-Chains anpassen". Der Nightly Benchmark überspringt deprecated Modelle, meldet aber verwaiste Referenzen als Warnung.

**Konsequenzen:**

- (+) Schließt die dokumentierte BaFin-Lücke mit Bordmitteln (Validierungsquery + MR-Generator, kein neues Schema)
- (+) Referenzen stehen im content-JSONB → **GIN-Index zwingend beim Aktivieren anlegen:** `CREATE INDEX ON translation_rules USING GIN ((content->>'target_model_id'))` — analog für `source_model_id` und `calibration_anchors.model_id`; ohne Index sequenzieller Scan über das gesamte Repository bei jedem Modell-Sunset
- (-) Sammel-MRs können groß werden — Review-Aufwand beim Modellwechsel ist gewollt sichtbar

---

### ADR-023: Abgrenzung zu Gateway-Produkten (Beispiel: Databricks Mosaic AI Gateway) (neu in v2.4)

**Status:** Entschieden (2026-07-31), bereinigt am 2026-10-30 (CHY-188): Ersetzungsrest im Namen,
Positionierungsaussagen und Behauptungen über das Fremdprodukt entfernt.

**Kontext:** Gateway-Produkte wie das Mosaic AI Gateway von Databricks bündeln den Zugang zu
mehreren Modellen hinter einem Endpunkt und steuern ihn mit Zugriffsrechten, Richtlinien,
Guardrails, Observability und Kostenkontrolle. Die Oberfläche ähnelt Cohereny.

**Entscheidung:** Cohereny grenzt sich über die Frage ab, nicht über das Produkt:

| Dimension | Gateway | Cohereny |
|---|---|---|
| **Frage** | Wer darf welches Modell mit welchem Budget nutzen? | Welcher geprüfte Kontext gehört in diesen Workflow-Schritt, und welches Modell darf ihn sehen? |
| **Gegenstand** | Zugriff, Richtlinien, Kosten | Versionierter, freigegebener Kontext je Geltungsbereich; Modellwahl nach Datenklasse |
| **Betrieb** | als Teil der jeweiligen Plattform | selbst betrieben |
| **Adaption je Modell** | — | geplant, abhängig vom Benchmark |

Beides schließt sich nicht aus: Cohereny nutzt für den Modellzugang selbst ein Gateway
(LiteLLM). Die Abgrenzung steht gleichlautend im Whitepaper (§11.4).

**Konsequenzen:**

- (+) Die Abgrenzung hängt an der Frage, die ein Produkt beantwortet, und veraltet nicht mit dessen nächster Version
- (-) Ergänzt ein Gateway geprüften Kontext je Schritt, muss dieser ADR überprüft werden

### ADR-024: AdaptVault — Versioniertes Adaption-Repository + DPO-Flywheel (neu in v2.5)

**Status:** Entschieden (2026-08-01)

> **Teilweise überholt durch ADR-043 (06.10.2026):** Punkt 1 gilt nicht mehr — Testfälle sind Kalibrieranker im Kontext-Repository, Messungen stehen in `benchmark_runs`/`benchmark_measurements`. Der nächtliche Lauf aus Punkt 2 gilt wieder (ADR-044, 03.11.2026), dazu Läufe bei Änderungen und auf Abruf. **Umsetzungsstand (31.10.2026):** Adaption-Discovery, `PromptAdaptation`, die DPO-Pipeline (CHY-70) und `GET /v1/adaptvault/export/dpo` sind nicht gebaut. `TranslationRule`-Einträge, auf die sich der Kontext stützt, gab es nie als Blocktyp.

**Kontext:** Die Adaptionsschicht von Cohereny wird aktuell durch manuell gepflegte `TranslationRule`-Einträge im Kontext-Repository betrieben. Das ist für den PoC ausreichend, skaliert aber nicht: Bei wachsender Modell- und Domänenanzahl (jedes neue Modell × jede neue Domäne = eigene Regeln) übersteigt der Pflegeaufwand die verfügbare Zeit. Gleichzeitig entstehen durch den Multi-Modell-Betrieb natürliche ranked pairs (Modell A liefert bessere Outputs als Modell B auf demselben Prompt), die für DPO-Training genutzt werden könnten.

**Entscheidung:** Drei aufeinander aufbauende Mechanismen, die zusammen einen selbstverstärkenden Qualitäts-Loop bilden:

1. **Prompt-Benchmark-DB** (`prompt_benchmarks` + `benchmark_results`): persistente Datenbank generalisierter Prompts mit maschinenlesbaren Evaluierungskriterien (`schema`, `contains`, `llm_judge`). Jede behauptete Adaption ist quantitativ validierbar.

2. **Nightly Evaluation + Adaption-Discovery** (nächtlicher Lauf): Alle aktiven Benchmarks × alle konfigurierten Modelle werden nächtlich ausgeführt. Bei Score < Schwellwert werden regelbasierte Varianten getestet (Chain-of-Thought, Format-Hint, Kontext-Erweiterung) und — bei Erfolg — automatisch als `PromptAdaptation` gespeichert. Unsichere Fälle (gain < 5 %) gehen in die HITL-Queue (CHYUI-3). Ersetzt manuelle Regelpflege für strukturierbare Domänen vollständig.

3. **DPO-Pipeline mit zwei Dimensionen** (bei Beschluss M7; heute → M6, CHY-70):

   **3a — Internes Validierungsmodell:** Aus `benchmark_results` WHERE `score > 0.8` AND (`validated_by IS NOT NULL` OR auto high-confidence) werden (prompt, chosen, rejected)-Tripel als JSONL exportiert. Training mit HuggingFace TRL `DPOTrainer` auf Phi-3-mini-4k-instruct (MIT) oder Gemma 3 (Apache 2.0). Deployment als `cohereny-phi3-dpo:v1` in Ollama (Compute lokal) — registriert als reguläres Modell in LiteLLM und über dasselbe interne Gateway geroutet wie alle anderen Modelle. Zweck: interne Qualitätssicherung des AdaptVault, kein Kundenprodukt.

   **3b — Kunden-Export-API:** `GET /v1/adaptvault/export/dpo` (Admin-only) liefert validierte ranked pairs als JSONL (HuggingFace-kompatibel), filterbar nach `domain`, `min_score`, `validated_only`, `since`. **Bring Your Own Model:** Der Kunde entscheidet selbst, ob und mit welchem Base-Modell er fine-tunet; Wartungsaufwand (Sicherheits-Patches, Re-Training) bleibt beim Kunden.

   **Bootstrapping:** Öffentliche Datensätze lösen das Henne-Ei-Problem für CHY-70 (`Intel/orca_dpo_pairs`, `HuggingFaceH4/ultrafeedback_binarized`, `Anthropic/hh-rlhf`); domänenspezifische eigene Paare verfeinern den Pool iterativ.

**Warum DPO statt SFT?**

Supervised Fine-Tuning (SFT) benötigt Gold-Labels — exakte "richtige" Ausgaben für jeden Prompt. DPO benötigt nur ranked pairs: "Output A war besser als Output B für denselben Prompt." Diese entstehen beim Multi-Modell-Benchmark-Betrieb natürlich und ohne zusätzlichen Annotationsaufwand. Kein separates Reward-Modell nötig; die Implementierung ist mit HuggingFace TRL standardisiert und gut dokumentiert.

**Konsequenzen:**

- (+) `adaptation_gain` ist der quantitative Beweis, dass Cohereny Outputs messbar verbessert — Qualitätsziel QK1 (§10.1)
- (+) Flywheel-Dynamik: Jeder Produktionslauf erzeugt potenzielle Trainingsdaten; Qualität verbessert sich ohne manuellen Eingriff
- (+) LiteLLM-Integration: Das interne DPO-Modell wird über dasselbe Gateway geroutet wie alle anderen Modelle — keine separate Infrastruktur nötig
- (+) HITL bleibt für semantisch komplexe Domänen erhalten — konsistent mit ADR-004 (Human Oversight als nicht abschaltbarer Kern)
- (+) Public-Dataset-Bootstrapping löst Henne-Ei-Problem: CHY-70 kann starten bevor ausreichend eigene Daten vorhanden sind
- (+) "Bring Your Own Model": Cohereny ist kein Fine-Tuning-Service — Kunden erhalten Daten, keine Modelle; Compliance-Risiken beim Kunden
- (+) Lizenzlage der Basismodelle: Phi-3 MIT, Gemma 4 Apache 2.0 (seit 04/2026) — beide für kommerziellen Einsatz freigegeben. Gemma 3 und älter stehen unter den Gemma Terms of Use: kommerzielle Nutzung erlaubt, aber mit Nutzungsbeschränkungen, die bei Weitergabe eines abgeleiteten Modells mitgegeben werden müssen (Korrektur 01.11.2026). Wechsel der Referenzinstallation und der Vorgaben auf Gemma 4: → M7b (CHY-210)
- (-) DPO-Training ist compute-intensiv (lokaler Compute-Host, kein Container) — Reproduzierbarkeit hängt an der Verfügbarkeit dieses Geräts; mittelfristig Cloud-Training evaluieren
- (-) Trainingsdaten-Qualität (GIGO-Risiko): Wenn Benchmark-Scores durch schlechte Evaluierungskriterien verfälscht sind, lernt das Modell falsche Präferenzen — Qualitätssicherung der `evaluation_criteria` ist kritisch

---

### ADR-025: Actor-Beziehungen — typsichere Tabellen statt polymorpher Referenz (neu in v2.6)

**Status:** Entschieden (2026-08-11)

**Kontext:** CHY-34 sieht neben `Actor` eine generische Zwischentabelle `ActorReference(actor_id, entity_type, entity_id, role)` vor und begründet sie mit dem DSGVO-Löschrecht. Die Prüfung des bestehenden Schemas (2026-08-07/11) ergab, dass diese Begründung nicht trägt und der Vorschlag ein Integritätsproblem einführt.

**Befund:** Elf Stellen referenzieren `actors(id)` bereits direkt. Sie folgen drei verschiedenen Mustern:

| Muster | Stellen | Kardinalität | Beispiele |
|---|---|---|---|
| Urheberschaft | 7 | genau einer, unveränderlich | `scope_nodes.created_by`, `context_blocks.author_id`, `audit_log.actor_id` |
| Mitgliedschaft mit Rolle | 1 | n:m — **bereits als Tabelle** | `scope_memberships(scope_id, actor_id, role, granted_by)` |
| Freigabe | 2 | derzeit einer | `learning_proposals.reviewed_by`, `benchmark_reports.approved_by` |

**Entscheidung:**

1. **Urheberschaft bleibt direkter Fremdschlüssel.** Eine Zwischentabelle gewänne nichts und erlaubte einen ungültigen Zustand — zwei Urheber für einen Block.
2. **n:m-Beziehungen erhalten eine eigene, typsichere Tabelle** mit Fremdschlüsseln auf beide Seiten, wie `scope_memberships` es vormacht.
3. **Keine generische `ActorReference`.**
4. **Mehrfach-Freigaben werden als Schritte der Status-Maschine modelliert**, nicht als parallele Zuordnungen. Ein Übergang hat einen Actor und einen Zeitpunkt; die Historie mehrerer Freigaben liegt im Audit-Log (CHY-31).

**Warum keine polymorphe Referenz:** `entity_type TEXT` + `entity_id UUID` lässt sich **nicht per Fremdschlüssel absichern** — ein FK verweist immer auf genau eine Tabelle. Wird ein ContextBlock gelöscht, bleibt die Referenz verwaist, ohne dass die Datenbank es bemerkt; Integrität müsste über Trigger oder Anwendungslogik nachgebaut werden.

Für ein System, dessen Zweck Governance und Nachweisbarkeit ist, wäre ausgerechnet die Tabelle, die belegen soll wer was getan hat, die einzige ohne Integritätsgarantie.

**Right to Erasure funktioniert ohne Zwischentabelle:** `idp_sub = NULL`, `display_name = '[anonym]'`, `is_anonymized = true`. Alle elf Referenzen bleiben bestehen, die Domain-Daten unberührt, der Personenbezug ist gekappt. Die Spalte `actors.idp_sub` trägt seit `001_initial` den Kommentar „NULL wenn anonymisiert" — das Muster war von Beginn an so angelegt.

**Nachtrag aus der Umsetzung (Migration 010, 2026-08-12):** Drei Punkte kamen bei der Implementierung hinzu, weil `is_anonymized` allein nicht reicht.

*`anonymized_at` ist Pflicht, wenn das Flag gesetzt ist.* `is_anonymized` allein sagt, **dass** gelöscht wurde, nicht **wann** — und die Monatsfrist aus DSGVO Art. 12(3) ist genau eine Frage nach dem Wann. Zwei CHECK-Constraints setzen das durch: `actors_anonymized_consistent` (Flag und Zeitstempel stimmen überein) und `actors_anonymized_has_no_idp_sub` (ein anonymisierter Actor behält keine IdP-Identität). Die zweite Regel steht bewusst in der Datenbank statt im Endpunkt — sie ist die Invariante, auf der die Löschzusage ruht.

*`idp_sub IS NULL` war mehrdeutig.* Es ist auch der Normalzustand eines lokalen Actors, der nie eine IdP-Identität hatte — ein Dienstkonto sah aus wie ein ehemaliger Kollege. Erst das Flag trennt beides.

*Der letzte aktive Admin kann nicht anonymisiert werden.* `idp_sub = NULL` heißt, dass kein JWT diese Zeile je wieder trifft; da der Endpunkt einen Admin verlangt, würde ein Aufruf jeden künftigen unmöglich machen. Der Endpunkt antwortet dort mit `409` statt eine irreversible Aussperrung zuzulassen.

**Konsequenzen:**

- (+) Referenzielle Integrität bleibt durchgehend von der Datenbank garantiert
- (+) Ein Muster weniger: n:m als typsichere Tabelle, 1:1 als Fremdschlüssel — konsistent zum Bestand
- (+) CHY-34 verkleinert sich auf `is_anonymized` plus Implementierung des Anonymisierungs-Endpunkts
- (−) Wird künftig eine weitere n:m-Beziehung zu Actors gebraucht, kostet sie eine eigene Tabelle statt einer Zeile in einer generischen
- (−) Abfragen über *alle* Beziehungen eines Actors hinweg erfordern UNION statt eines einzelnen Selects — relevant für eine mögliche Auskunftsfunktion nach DSGVO Art. 15

**Betrifft:** CHY-34 (Actor Pattern), CHY-31 (Audit Log), CHY-32 (Review-Workflow), CHY-65 (MR-Datenmodell)

---

### ADR-026: Zyklen im Workflow erlaubt — Iterationsgrenze verpflichtend (neu in v2.7)

**Status:** Entschieden (2026-08-11)

> **Umsetzungsstand (31.10.2026):** Gerichteter Graph mit Zyklen, `loop_limit` je Rückkante, Zähler je Kante und Lauf und die Prüfung, dass Sub-Workflow-Aufrufe keinen Zyklus bilden, sind gebaut (Migration 018, Validator in `contexts/orchestration/graph.py`, Runner CHY-127). Abweichend: Eine Rückkante ohne `loop_limit` lehnt die Aktivierung mit 409 ab, nicht mit 422. Nicht gebaut: der Vermerk `loop_limit_reached` — der Runner erkennt die erschöpfte Kante, schreibt sie aber nicht ins Audit —, und das Übersteuern der Grenze je Lauf; Workflow-Läufe haben keinen `effective_config`-Schnappschuss.

**Kontext:** §5.6 Domäne 2 legte den Workflow bis v2.6 als **DAG** fest. Die Prüfung am 11.08. ergab, dass diese Festlegung weder begründet noch umgesetzt war:

| Behauptung | Befund |
|---|---|
| Der Workflow ist azyklisch | Kein ADR, keine Begründung. Der Satz argumentierte gegen die *geordnete Liste*, nicht gegen Zyklen |
| Das Modell bildet die Pilotanwendungen ab | Der Study-Pilot hat **zwei** Rückschleifen — `coverage_check → summarize` und `quiz ⇄ evaluate` |
| Die Implementierung setzt es durch | `001_initial` prüft Azyklizität nirgends; die Tabellenkommentare sagen „DAG-Kanten", der Code nicht |

Das Whitepaper hielt die Frage ausdrücklich offen: *„Workflow-DAG-Semantik: … Formale Klärung im Schema-Dokument (Backlog)."* Das ist diese Klärung.

**Warum Zyklen gebraucht werden:** Die verbreitetsten LLM-Muster sind Rückkopplungen — verfeinern bis die Abdeckung reicht, neu formulieren bis das Ergebnis das Prüfkriterium besteht, Werkzeug erneut aufrufen wenn die Antwort unvollständig war. Ohne Zyklus lassen sie sich nur durch Abrollen darstellen: dieselbe Teilkette *n*-mal kopiert. Damit wandert die Iterationszahl in die Struktur des Graphen, ist nicht mehr änderbar ohne den Workflow umzubauen, und die Wiederholungen sind im Audit-Trail nicht als solche erkennbar.

**Warum die Grenze verpflichtend ist:** Ein Zyklus ohne Abbruchbedingung ist bei einem LLM-Workflow kein Programmierfehler mit Endlosschleife, sondern **unbegrenzte Kosten gegenüber einem Dritten** — jede Runde ist ein bezahlter Inferenzaufruf. Das ist genau die Klasse von Fehler, die einem im Betrieb erst durch die Rechnung auffällt. Deshalb liegt die Grenze nicht in der Zuständigkeit dessen, der den Workflow modelliert.

**Entscheidung:**

1. Ein Workflow ist ein **gerichteter Graph**; Zyklen sind zulässig.
2. Jede **Rückkante** — eine Kante auf einen im selben Pfad bereits besuchten Knoten — trägt verpflichtend `loop_limit: int > 0`. Der Validator lehnt eine Definition ohne diese Angabe mit `422` ab.
3. Ist die Grenze erreicht, wird die Rückkante nicht mehr genommen; der Run läuft auf dem regulären Pfad weiter und schreibt `loop_limit_reached` in den Audit-Trail. **Kein Abbruch** — sonst wäre ein Workflow, der beim dritten Versuch immer noch nicht überzeugt, gar nicht mehr freigebbar.
4. Der Zähler ist **pro Kante und Run** geführt, nicht pro Workflow — zwei unabhängige Schleifen begrenzen sich nicht gegenseitig.
5. **Zwischen** Workflows sind Zyklen ausgeschlossen: Der Aufrufgraph der Sub-Workflows muss azyklisch sein. Dort gäbe es keine Kante, an der eine Grenze hängen könnte.

**Warum `loop_limit_reached` im Audit-Trail steht:** Ohne diesen Vermerk lässt sich hinterher nicht unterscheiden, ob ein Ergebnis das Prüfkriterium bestanden hat oder ob nur das Iterationsbudget aufgebraucht war. Für einen Freigebenden ist das der Unterschied zwischen „geprüft und gut" und „so gut wie es in drei Anläufen wurde" — er muss ihn sehen können (EU AI Act Art. 14: wirksame Aufsicht setzt voraus, dass der Mensch die Grundlage seiner Entscheidung kennt).

**Konsequenzen:**

- (+) Der bestehende Study-Pilot ist im dokumentierten Modell darstellbar — vorher war er es nicht
- (+) Die Iterationszahl ist Konfiguration statt Graphstruktur; sie lässt sich pro Run übersteuern (CHY-42) und im `effective_config`-Schnappschuss nachweisen
- (+) Kostenobergrenze je Run ist statisch aus der Definition berechenbar: Summe der Knoten mal Produkt der umschließenden `loop_limit`
- (−) Der Runner braucht Zyklenerkennung (DFS mit Besuchsstapel) statt topologischer Sortierung — mehr Aufwand, aber ohnehin nötig für die Validierung
- (−) Statische Analyse des Graphen wird schwieriger: „welche Knoten laufen sicher genau einmal" ist keine triviale Frage mehr
- (−) Terminierung ist nicht mehr aus der Struktur allein garantiert, sondern hängt an der durchgesetzten Grenze — also am Validator und dessen Tests

**Verworfene Alternative — Zyklen verbieten, Iteration im Knoten kapseln:** Ein `llm_call` mit eingebautem „bis zu 3 Versuchen". Der Graph bliebe azyklisch, die Wiederholung wäre aber unsichtbar: Ein Betrachter des Workflows sähe einen Aufruf, wo drei stattfinden. Das widerspricht dem Zweck des Modells, den Ablauf nachvollziehbar zu machen.

**Betrifft:** CHY-81 (dieses Dokument), CHY-42 (Workflow-Konfiguration), Migration 018 (damals als 011 vorgesehen)

---

### ADR-027: Serialisierung und Austauschformat — JSON nativ, XML nur an der BPMN-Grenze (neu in v2.7)

**Status:** Entschieden (2026-08-11)

> **Umsetzungsstand (31.10.2026):** Workflow-Definitionen werden als JSON gelesen (`GET /v1/workflows/{id}/graph`); ein `PUT …/graph` gibt es nicht — der Graph entsteht über `POST …/nodes` und `POST …/connections`. Das JSON Schema der Modelle steht in den erzeugten Contracts (`openapi.yaml`, `contracts/`), nicht als eigenes Artefakt. Nicht gebaut sind der BPMN-Import (`lxml` ist, wie Punkt 4 es will, keine Abhängigkeit) und der Inhalts-Hash aus Punkt 5.

**Kontext:** Für die Workflow-Beschreibung wurde ein **XML/XSD/XSLT-Gespann** vorgeschlagen: XML als Format, XSD als deklarative Validierung, XSLT als Transformation von und nach BPMN. Der Vorschlag hat einen echten Kern — im Enterprise-Umfeld ist XML die Sprache der Workflow-Werkzeuge, und BPMN 2.0 *ist* XML. Zu klären ist, welche der drei Rollen — Speicherung, Validierung, Austausch — davon profitiert.

**Rolle 1 — Speicherung: bleibt relational.** `workflow_nodes` und `workflow_connections` existieren seit `001_initial`. Ein Dokument in einer Spalte würde Abfragen unmöglich machen, die für den Betrieb gebraucht werden: *welche Workflows rufen Modell X auf*, *welche haben keinen Freigabeschritt*, *welche Version lief bei Run 4711*. Über `XMLTABLE` ginge es, aber ohne Index und ohne Fremdschlüssel.

**Rolle 2 — Validierung: XSD deckt die falsche Hälfte ab.** Gemessen an den drei Regeln aus §5.6:

| Regel | XSD 1.0 |
|---|---|
| Alle drei Pflichtknoten vorhanden | ✗ Kardinalität *pro Typwert* ist nicht ausdrückbar; `node_type` ist ein Attributwert, kein Element |
| Jede Rückkante trägt `loop_limit` | ✗ Zyklenerkennung ist Graphanalyse |
| `human_review` nach dem letzten `llm_call` | ✗ Erreichbarkeitsanalyse |
| Jede Kante zeigt auf einen existierenden Knoten | ✅ über `xs:key` / `xs:keyref` |

Keine der Regeln, die den Compliance-Anspruch tragen, ist in XSD ausdrückbar — alle drei sind Graphanalyse. Was XSD kann, leisten Pydantic und der Fremdschlüssel in PostgreSQL bereits. XSD 1.1 (`xs:assert`) käme etwas weiter, erreicht aber ebenfalls keine Zyklen — und `libxml2`, damit auch `lxml`, unterstützt nur **XSD 1.0**.

**Rolle 3 — Austausch: hier trägt das Argument.** BPMN 2.0 liefert XML, daran führt kein Weg vorbei. Die Frage ist nur, womit es verarbeitet wird. Gegen XSLT sprechen drei Punkte:

- `lxml` beherrscht nur **XSLT 1.0**; für die nötige Gruppierung und Nachschlagearbeit bräuchte es die Muenchian-Methode — schwer zu lesen und zu testen. XSLT 3.0 hieße Saxon als native Zusatzbibliothek.
- Die Abbildung ist **Bedeutungs-, nicht Formatierungsarbeit**: Welcher `serviceTask` mit welchen Extension-Attributen wird zu einem `llm_call`, mit welchem Modell, nach welcher Routing-Regel? Das erfordert Nachschlagen in der Modellregistrierung, Plausibilitätsprüfung und Fehlermeldungen mit Bezug auf die Zeile in der Quelldatei. XSLT ist stark bei Struktur→Struktur und schwach genau dort.
- Der klassische XSLT-Vorteil — Fachleute passen die Abbildung ohne Programmierung an — setzt voraus, dass es diese Fachleute gibt. Bei der aktuellen Teamgröße ist er null.

**Entscheidung:**

1. **JSON ist das native Austauschformat** für Workflow-Definitionen (`GET/PUT /v1/workflows/{id}/graph`). Es passt zur API, zu `config JSONB` und zu Pydantic.
2. **Validierung bleibt Code mit Tests.** Zusätzlich wird das aus den Pydantic-Modellen erzeugte **JSON Schema veröffentlicht und versioniert** — als Prüfartefakt gegenüber einer Aufsicht, nicht als Ausführungsmechanismus.
3. **XML nur an der BPMN-Grenze.** Der Import parst BPMN 2.0 mit `lxml` und bildet in Python ab; kein XSLT, keine XSD-Validierung der eigenen Struktur.
4. **`lxml` wird erst aufgenommen, wenn der BPMN-Import gebaut wird** — heute ist es keine Abhängigkeit von Cohereny.
5. **Unverfälschtheit über Inhalts-Hash**, nicht über XML-DSig: SHA-256 über die kanonisierte JSON-Darstellung (RFC 8785), im Audit-Trail mitgeschrieben. Deutlich weniger Apparat als Kanonisierung plus Signaturprüfung, bei gleicher Aussage.

**Zum Prüfartefakt:** Der stärkste Punkt am XML-Vorschlag war das veröffentlichte, versionierte Schema — vor einer Aufsicht ist „hier ist unser Workflow-Schema, maschinell prüfbar" etwas anderes als „steht im Code". Dieser Punkt wird übernommen, nur mit JSON Schema statt XSD. Der Vorsprung von XSD ist Vertrautheit im Behördenumfeld, nicht Substanz. Verlangt jemand konkret ein XSD, lässt es sich aus denselben Modellen erzeugen — ohne die Werkzeugkette umzustellen.

**Konsequenzen:**

- (+) Ein Format, eine Validierungsstelle. Kein Regelwerk, das an zwei Orten in zwei Sprachen gepflegt wird und auseinanderlaufen kann
- (+) Keine JVM, kein Saxon, keine native XSLT-Bibliothek im Container
- (+) Das veröffentlichte JSON Schema ist aus dem Code erzeugt und kann deshalb nicht veralten
- (−) Wer aus der BPMN-Welt kommt, erwartet XML und findet JSON — der Import muss das auffangen und gut dokumentiert sein
- (−) Ein Export **nach** BPMN ist damit nicht automatisch mitgeliefert; er wäre ein eigener Adapter in die Gegenrichtung (Backlog)
- (−) JSON Schema kennt keine Entsprechung zu `xs:keyref`; die referenzielle Integrität der Knoten-IDs prüft der Validator, nicht das Schema

**Betrifft:** CHY-81, ADR-028, Backlog: BPMN-Import-Adapter, BPMN-Export

---

### ADR-028: Eigener Workflow-Runner statt LangGraph im Kern (neu in v2.7)

**Status:** Entschieden (2026-08-11)

**Kontext:** Cohereny nutzt LangGraph in den Piloten (Study, Banking). Mit dem Schritt vom fest verdrahteten Piloten zum konfigurierbaren Workflow (§5.6) stellt sich die Frage, ob LangGraph auch die Ausführung der aus der Datenbank gelesenen Definitionen übernimmt.

#### Befund: Was der Code heute tut

Vor der Entscheidung wurde die bestehende Umsetzung gelesen statt eingeschätzt. Das Ergebnis:

| Erwartung | Wirklichkeit |
|---|---|
| LangGraph verwaltet den Freigabe-Wartezustand | `langgraph.interrupt` kommt im Projekt **nicht vor**. Gewartet wird auf einem `threading.Event` in einem prozesslokalen Dict (`api/v1/pilots.py`), mit `ev.wait(timeout=7200)` in einem blockierten Thread |
| Der Checkpointer trägt die Wiederaufnahme | Checkpoints werden geschrieben und **nirgends gelesen**. Es gibt keine Resume-Logik; `graph.invoke()` startet stets neu. Der `PostgresSaver` läuft vollständig ins Leere |
| Der Zustand liegt in LangGraph | Er liegt seit Migration 006 in eigenen Spalten: `status='awaiting_oversight'`, `oversight_pending`, `oversight_decision` |

Nebenbefund, als CHY-83 aufgenommen: Der Rückfallpfad der Freigabe lautet zweimal `approved` — bei Zeitüberschreitung und bei fehlendem Gate. Ein Art.-14-Kontrollpunkt, der bei Schweigen zustimmt, verletzt ADR-004.

#### Korrektur einer früheren Begründung

Ein erster Entwurf dieses ADR argumentierte, LangGraphs Checkpointer sei „für kurze Unterbrechungen gebaut". **Das trifft nicht zu.** `interrupt()` in Verbindung mit `Command(resume=…)` ist genau für langlebige Wartezustände vorgesehen und in der aktuellen Fassung ausgereift (1.2.x; der Pin `>=0.2` zieht sie). Die Entscheidung fällt daher nicht, weil LangGraph es nicht könnte — sondern aus den vier folgenden Gründen.

#### Entscheidungsgründe

**1. `loop_limit` ist eine Eigenschaft der Kante, nicht des Laufs.** ADR-026 verlangt einen Zähler je Rückkante und Run; zwei unabhängige Schleifen dürfen sich nicht gegenseitig begrenzen. LangGraph kennt ein globales `recursion_limit`. Der Zähler wäre also von Hand im State zu führen — an der Bibliothek vorbei, und ohne dass sie ihn durchsetzt.

**2. Sub-Workflows sind eigenständige Objekte, keine Subgraphen.** §5.6 verlangt eigenen Endpunkt, gepinnte Version und eigenen Audit-Trail je aufgerufenem Workflow. Ein LangGraph-Subgraph ist keines davon: nicht unabhängig aufrufbar, nicht versioniert, kein eigener Laufdatensatz.

**3. Definitionen kommen zur Laufzeit aus der Datenbank.** Der Graph würde je Lauf aus Zeilen neu zusammengesetzt. Damit wird die Verträglichkeit von Checkpoints über Workflow-Versionen hinweg zur eigenen Sorge — ein Problem, das ohne Checkpointer nicht entsteht.

**4. Der Audit-Trail entsteht ohnehin selbst.** Modellwahl, Routing-Begründung, Iterationsstand, Freigebender und Zeitpunkt müssen einzeln abfragbar sein. Ein serialisierter Checkpoint taugt dafür nicht; er wäre ein zweiter, redundanter Zustandsspeicher neben dem, der die Nachweispflicht trägt.

Hinzu kommt die Anforderung aus Whitepaper §8.2.2: Cohereny muss **ohne** fremde Engine laufen und **in** einer fremden Engine als Knoten eingebunden werden können, der intern selbst einen Workflow ausführt. Eine Engine ist in jedem Fall nötig; die Frage ist nur, wessen.

#### Entscheidung

1. **Eigener Runner im Kern.** Traversierung über `workflow_nodes` / `workflow_connections` (DFS mit Besuchsstapel — dieselbe Analyse, die der Validator braucht), Zustand in eigenen Tabellen.
2. **`human_review` ist ein Wartezustand der Status-Maschine**, kein blockierter Thread. Eine Zeile in der Datenbank, fortsetzbar nach Neustart und von jeder API-Instanz.
3. **Knotenausführung hinter einer Schnittstelle** (`NodeExecutor`), damit einzelne Knotentypen delegierbar bleiben, falls sich die Einschätzung als falsch erweist.
4. **LangGraph bleibt in den Piloten**, solange sie laufen. Kein Umbau von Study und Banking als Voraussetzung — aber auch kein neuer Pilot mehr auf LangGraph.
5. **`langgraph-checkpoint-postgres` entfällt** mit dem letzten abgelösten Piloten. Bis dahin bleibt es unbenutzter Schreibaufwand.

**Aufwandsschätzung:** 800–1500 Zeilen einschließlich Tests — Traversierung, Zyklenerkennung, Join-Policies, Wiederaufnahme, Sub-Workflow-Aufruf. Der Validator (§5.6) teilt sich die Graphanalyse mit dem Runner.

#### Verworfene Alternative: LangGraph richtig nutzen

`interrupt()` plus `PostgresSaver` würde Befund 3 der Tabelle mit wenig Code beheben und ist technisch tragfähig. Verworfen, weil die vier Entscheidungsgründe dadurch nicht kleiner werden: `loop_limit` je Kante, versionsgepinnte Sub-Workflows und der eigene Audit-Trail wären weiterhin daneben zu bauen. Man hätte dann beides — die Bibliothek *und* die Eigenbauten — statt nur das eine.

**Konsequenzen:**

- (+) Der Wartezustand liegt genau einmal, in auditierbaren Tabellen, per SQL abfragbar — und übersteht Neustart wie Mehrfachinstanz
- (+) Die ausgeführte Semantik ist die dokumentierte Semantik; keine Abbildung auf ein fremdes Modell und keine Divergenz bei dessen Weiterentwicklung
- (+) Eine strategische Abhängigkeit weniger an der Stelle, an der die Alleinstellung liegt (ADR-012 zieht dieselbe Grenze für LiteLLM und Langfuse)
- (+) Ein Zustandsspeicher weniger: der ungenutzte Checkpointer entfällt mitsamt seinen Tabellen
- (−) 800–1500 Zeilen, die gewartet und getestet werden müssen — einschließlich der Fehlerfälle, die eine reife Bibliothek bereits kennt
- (−) Kein Zugang zum LangGraph-Ökosystem (Studio, vorgefertigte Knoten, LangSmith)
- (−) Nebenläufigkeit und Wiederaufnahme sind erfahrungsgemäß fehleranfällig; das Risiko liegt nicht in der ersten Fassung, sondern in den Randfällen. Gegenmaßnahme: Join-Policies und Wiederaufnahme bekommen Tests, bevor sie Aufrufer bekommen
- (−) Zwei Ausführungsmodelle nebeneinander, bis die Piloten abgelöst sind

**Betrifft:** CHY-81, CHY-82 (Migration 018, damals als 011 vorgesehen — jetzt entblockt), CHY-83 (Freigabe-Rückfallpfade), ADR-004, ADR-012, ADR-026

**Nachtrag 30.09.2026 — Banking wird abgelöst (CHY-172).** Der Banking-Pilot ist gegenüber dem Kern zurückgefallen: Prompts als Literale, Modellstufen fest verdrahtet, ein Gate ohne Rollenprüfung, das `fast_track` umgeht, Rechtsverweise im Prompt, die nicht mehr stimmen, Schritte als Liste im Ergebnis statt im Prüfprotokoll. Thomas hat entschieden, ihn auf den Runner abzulösen, parallel zu CHY-64. Punkt 4 gilt bis dahin weiter — keine Arbeit am Piloten, die mit der Ablösung wegfiele. Die Lücken des Runners, die der ganze Ablauf braucht, stehen als Unteraufgaben: Rechenschritte ohne Modell (CHY-173, eigene ADR noch offen), Dokumenteingang (CHY-174), Ausgabeschema für `llm_call` (CHY-175), vier Augen (CHY-176), Zweckbindung je Knoten (CHY-177, entschieden 06.11.: im Ablauf gelöst — der Kontoauszug kommt in einen eigenen Schritt, dessen einzige Kante zum Entwurf führt, und ein Test an der Banking-Definition hält das fest; strikte Eingaben je Workflow → M7, CHY-238). Der Ablauf selbst ist CHY-178, der Rückbau CHY-179, die Oberfläche CHYUI-10. Punkt 5 bleibt: der Checkpointer fällt erst mit Study.

**Nachtrag 08.11.2026 — beide Piloten abgekündigt (Thomas; CHY-250).** `/v1/pilots` und `/v1/questions` tragen im API-Vertrag `deprecated`, ein Test hält genau diese Menge. README, Changelog und `DATA-PROTECTION.md` vermerken, dass die Piloten als Workflows auf den Runner umgestellt werden: Banking mit CHY-172, Study danach (Punkt 5). Anlass: Sie rufen das Modell an der Ersetzung vor dem Modell vorbei auf (ADR-018, dritter Nachtrag).

---

### ADR-029: Die OpenAPI-Spezifikation entsteht aus dem Code, nicht daneben (neu in v2.8)

**Status:** Entschieden (2026-08-13)

**Kontext:** CHY-28 verlangte Contract-First — eine handgeschriebene `core/openapi.yaml`, **bevor** die Implementierung steht, damit die GUI gegen einen Prism-Mock entwickelt werden kann. Das Ticket stammt aus der Gitea-Zeit. Inzwischen ist die API auf drei Stufen deployt, und `cohereny-ui` spricht über Astro-Server-Routen mit der echten Instanz. Jeder Teil der Prämisse ist abgelaufen.

Eine handgeschriebene Datei neben dem Code wäre jetzt eine **zweite Quelle der Wahrheit**. Genau daran ist diese Woche mehrfach etwas gescheitert: die Betriebsdokumentation beschrieb Zustände, die es nicht gab; ADR-010 trug zwei unvereinbare Cache-Modelle nebeneinander; `deprecated` und `archived` bezeichneten dasselbe.

#### Befund: Der veröffentlichte Contract war unwahr

Vor der Entscheidung wurde die erzeugte Spezifikation gelesen statt eingeschätzt:

| Erwartung | Wirklichkeit |
|---|---|
| Der Contract beschreibt, was die API kann | **29 von 54 Operationen** antworteten HTTP **200** mit `{"status": "not_implemented"}`. Ein erzeugter Client liest das als Erfolg |
| Fehler sind Teil des Contracts | `responses=` kam **nirgends** vor. Kein 403, kein 404, kein 409 war beschrieben — obwohl 409 die Wirkungsweise des Freigabe-Gates aus ADR-009/CHY-32 *ist* |
| `/docs` ist in Produktion abgeschaltet | `docs_url` und `redoc_url` waren es. `/openapi.json` behielt seine Vorgabe und lieferte in Produktion genau das aus, wovor der Docstring warnt |
| Der SSE-Stream ist als solcher beschrieben | `GET /v1/pilots/runs/{id}/stream` war als `application/json` deklariert |

Die Umkehr des Tickets ist damit nicht bloß pragmatisch. Ein Contract, der zur Hälfte Erfolg meldet, wo nichts geschieht, ist schädlicher als keiner: Er begründet Vertrauen, das er nicht trägt.

#### Entscheidung

1. **Die Anwendung ist die Quelle.** `openapi.yaml` im Repositoriumswurzelverzeichnis ist eine erzeugte Momentaufnahme, geschrieben von `scripts/export_openapi.py`, mit `sort_keys=True` deterministisch sortiert.
2. **CI erzwingt die Übereinstimmung.** `tests/test_openapi_contract.py` vergleicht die eingecheckte Datei mit `app.openapi()`. Jede Änderung am Contract erscheint damit als reviewbarer Diff im Merge Request; eine unbeabsichtigte lässt die Pipeline fallen. Das ist der Wert, der von Contract-First übrig bleibt — nicht paralleles Arbeiten, sondern **absichtliche** Contract-Drift.
3. **Geplante Operationen antworten 501** und tragen `x-cohereny-status: planned` samt Ticket. 501 ist die Semantik, die HTTP für „kenne ich, kann ich noch nicht" bereithält. Die Routen bleiben, weil die Spezifikation damit zugleich die Roadmap zeigt — aber sie behaupten keinen Erfolg mehr.
4. **Fehlerantworten werden dort deklariert, wo der Fehler der Contract ist** (Blocklebenszyklus, Komposition, Audit-Log). Nicht flächendeckend: 401 auf jeder geschützten Route ist wahr und nutzlos.
5. **`openapi_url` folgt `docs_url`.** In Produktion wird kein Schema mehr ausgeliefert.

#### Verworfene Alternative: die Spec von Hand pflegen

Sie hätte den Vorteil, den Contract unabhängig von der Umsetzung formulieren zu können — man beschreibt, was gelten *soll*, nicht was gerade ist. Verworfen, weil dieser Vorteil genau die Trennung ist, die zur Drift führt: Zwei Beschreibungen desselben Dings laufen auseinander, und die falsche ist immer diejenige, die niemand ausführt. Der Wunsch, den Contract vorauszudenken, gehört ins Ticket, nicht in eine zweite Datei.

**Konsequenzen:**

- (+) Der Contract kann nicht mehr unbemerkt abweichen — er ist getestet wie Code
- (+) `cohereny-ui` liegt in einem eigenen Repositorium mit eigener Pipeline und merkt eine brechende Änderung sonst erst zur Laufzeit
- (+) Die Spezifikation zeigt Geplantes als geplant statt als vorhanden
- (−) Ein FastAPI-Update, das die Erzeugung ändert, lässt den Test im Renovate-MR fallen. Gewollt: Genau dann will man die Änderung sehen — aber es ist ein Merge-Request-Schritt mehr
- (−) `openapi.yaml` ist mit ~2700 Zeilen ein großes erzeugtes Artefakt im Diff. Abgemildert durch die Sortierung: Eine Änderung an einer Route berührt nur ihre Zeilen
- (−) Die Spec ist in Produktion nicht mehr abrufbar. Für Kunden-Deployments braucht es dafür einen bewussten, authentifizierten Weg — nicht die Vorgabe

**Betrifft:** CHY-28, CHY-84 (Kennzahlen — die *Zählung* der Platzhalter gehört dorthin, nicht hierher), CHY-86 (Scopes-API), ADR-009

---

### ADR-030: Vertikaler Domänenschnitt statt horizontaler Schichten (neu in v2.9)

**Status:** Entschieden (2026-08-16)

**Kontext:** Die Codebasis ist horizontal geschnitten — `api/`, `core/`, `domain/`. Der Schnitt trägt nicht mehr, und zwar messbar.

#### Befund: die Schichten heißen so, tun aber etwas anderes

Erhoben am 16.08. über den gesamten Produktivcode, nicht geschätzt:

| Erwartung | Wirklichkeit |
|---|---|
| `domain/` enthält das Domänenmodell | **32 Klassen, 31 davon Pydantic oder Enum, drei Methoden mit Logik auf 1004 Zeilen.** Ein Schema-Ordner mit dem Namen einer Schicht — ein anämisches Modell im Lehrbuchsinn |
| `api/` reicht durch | **2934 Zeilen**, gegen 916 in `core/`. Die Schicht, die weitergeben soll, hält das meiste |
| Die Logik liegt unterhalb des Transports | `api/v1/pilots.py` (721 LOC, 33 Verzweigungen, 12 Ausnahmen, 13 SQL-Aufrufe) und `api/v1/blocks.py` (613 / 23 / 10 / 13) enthalten **26 der 35** Datenbankaufrufe in `api/` |
| `core/` ist kopflos nutzbar (ADR-001) | Fünf Importe zeigen von `core/`/`domain/` zurück nach `api/` (CHY-94) |

Der Freigabe-Lebenszyklus eines Blocks — submit, withdraw, approve, reject — ist damit **nur über HTTP erreichbar**. Weder CLI noch Hintergrundjob noch der künftige Runner (ADR-028) können ihn auslösen.

> **Diese Tabelle ist ein Befund vom 16.08. und bleibt als solcher stehen.** Sie begründet die
> Entscheidung; sie beschreibt nicht den heutigen Zustand. Was sich seither bewegt hat, Stand
> **27.08.2026**:
>
> | | 16.08. | heute |
> |---|---|---|
> | Importe von unten zurück nach `api` | 5 | **3** — die drei sind `core/db`, `core/redis`, `core/tracing` |
> | Zonenverstöße insgesamt | 9 | **3** |
> | `HTTPException` unterhalb von Zone 1 | 3 | **0** |
> | `api/` Zeilen | 2934 | 3486 — gewachsen, weil CHY-86 vier Endpunkte hinzufügte |
> | Datenbankaufrufe in `api/` | 35 | 43 — bewusst angehoben, bis CHY-105 einen Kontext-Service dafür hat |
>
> Die beiden letzten Zeilen sind **keine Verbesserung**, und sie sollen auch nicht so
> aussehen. Der Umbau hatte zu diesem Zeitpunkt `kernel/` herausgelöst; die Kontexte, die
> `api/` leichter machen, standen noch aus.
>
> **Nachtrag 27.08. (CHY-104):** Der erste davon ist da. `api/` verlor 637 Zeilen an einer
> Datei, die Datenbankaufrufe gingen von 43 auf **30** zurück. Vier weitere Kontexte
> (CHY-105…108) stehen noch aus.

#### Die Diagnose ist nicht „Schichten falsch befüllt"

Man könnte die Logik in `core/` schieben und wäre die Kennzahl los. Das wäre der Fehler: `core/` würde zum selben Gottmodul unter neuem Namen. Was fehlt, ist nicht eine sauberere Horizontale, sondern eine **Vertikale** — Module, die je einen fachlichen Gegenstand vollständig besitzen.

Die Nähte liegen bereits in den Daten. Über die Fremdschlüssel der 17 Tabellen bilden sich Cluster, und `actors`/`scope_nodes` hängen an fast allem — das ist kein Kontext, sondern ein gemeinsamer Kern.

#### Entscheidung

**Fünf Bounded Contexts über einem Shared Kernel, als modularer Monolith** (seit ADR-042 vier: `governance` entfiel).

| Modul | Besitzt | Sprache |
|---|---|---|
| `kernel/` | `actors`, `scope_nodes`, `scope_memberships`, `audit_log` | Akteur, Geltungsbereich, Nachweis |
| `contexts/repository/` | `context_blocks`; dazu Komposition und Routing | Block, Version, Freigabestand, Komposition |
| ~~`contexts/governance/`~~ | — | entfernt am 29.09.2026 mit ADR-042; der Freigabeweg ist der Block-Lebenszyklus in `repository` |
| `contexts/orchestration/` ✅ | `workflow_definitions`, `workflow_nodes`, `workflow_connections`, `workflow_runs`, `node_executions`, `loop_counters`, `review_votes`; der Runner (ADR-028) | Workflow, Knoten, Lauf |
| `contexts/learning/` ✅ | `feedback_events`, `learning_proposals`, `benchmark_reports`, `benchmark_runs`, `benchmark_measurements`; Auswertung der Kalibrieranker (ADR-043) | Rückmeldung, Vorschlag, Anker, Messung, `adaptation_gain` |
| `contexts/pilots/` | `pilot_runs`, `question_bank`, `run_coverage`, `transparency_records`, `workflow_configs` | Lauf, Aufsicht, Abdeckung |

1. **`domain/` wird aufgelöst.** Jede Schemadatei zieht in den Kontext, der sie besitzt; `actor.py`, `scope.py` und `roles.py` werden Teil von `kernel/`. Der Name war irreführend, und ein gemeinsamer Schema-Ordner koppelt alle Kontexte an ein Verzeichnis, ohne dass ein Wächter echte von bloßer Schema-Kopplung unterscheiden könnte.
2. **`api/` behält nur, was existiert, weil HTTP existiert:** Statuscodes, Seitenaufteilung, SSE-Rahmung, Abhängigkeitsinjektion. Das Kriterium ist Erreichbarkeit, nicht Ort — was auch CLI, Hintergrundjob oder Runner können müssen, liegt unterhalb.
3. **Komposition und Routing bleiben im Repository-Kontext.** Sie haben keine eigene Tabelle und lesen ausschließlich Blöcke. Lese- und Schreibseite über eine Kontextgrenze zu trennen kostet eine Übersetzungsschicht und bringt nichts.
4. **Orchestrierung wird eigener Kontext**, obwohl eine Workflow-Definition versionierter, freigabepflichtiger Inhalt ist — weil der Runner (ADR-028) der plausibelste künftige eigene Dienst ist und ein Kontext, der später herausgelöst wird, besser von Anfang an einer ist. **Freigabe und Versionierung werden dort nicht nachgebaut:** Orchestrierung nutzt die veröffentlichte Schnittstelle des Repository-Kontexts. Zwei Freigabemechanismen wären zwei Wahrheiten darüber, was freigegeben ist.
5. **Die Grenze wird erzwungen, nicht vereinbart.** `scripts/quality_gate.py` prüft heute nur `core`/`domain` → `api`. Der Detektor wird auf Kontextgrenzen erweitert: ein Kontext darf `kernel/` importieren und sonst keinen zweiten Kontext. Mit vorgeführtem Fehlerfall (CHY-102) — eine Grenze ohne Wächter ist eine Bitte.
6. **Schrittweise, ein Kontext je Merge Request**, in dieser Reihenfolge: Charakterisierungstests → Pilots → Repository → Governance → Orchestrierung → Learning. Pilots zuerst, weil dort die größte Datei, die schlechteste Abdeckung (damals 48 %, beim Schnitt am 27.08. 54 %) und die geringste Kopplung zusammentreffen.

#### Verworfene Alternative: Microservices jetzt

Der Gedanke lag nahe — Pilots hat ein anderes Laufzeitprofil (Hintergrund-Threads, LangGraph, schwere Abhängigkeiten) und außer `actor_id`/`oversight_by` keine Fremdschlüssel nach außen. Verworfen, weil Grenzen zu ziehen, **bevor** sie sich bewährt haben, eine Refaktorisierung in ein Problem verteilter Systeme verwandelt: Netzwerkfehler, Versionierung zwischen Diensten, verteilte Transaktionen — bei einer Person und knapper Zeit. Die Extraktion aus einem sauberen Modul ist später billig; die Rückabwicklung eines verfrühten Dienstes ist es nicht.

#### Verworfene Alternative: der volle DDD-Apparat

Repositories um Ein-Satz-Abfragen, eine Use-Case-Klasse je Endpunkt, Domain Events ohne Verbraucher, Aggregate-Wurzeln mit Invariantenprüfung überall. Verworfen, weil DDD seinen Preis über **Sprach- und Teamgrenzen** einspielt und es hier keine Teamgrenzen gibt. Was bleibt und den Aufwand trägt, ist Änderungseindämmung und Testbarkeit. Alles darüber hinaus ist die Zeremonie, aus der `core/` das nächste Gottmodul wird.

**Konsequenzen:**

- (+) Der Freigabe-Lebenszyklus wird ohne HTTP aufrufbar — Voraussetzung für CLI, Hintergrundjobs und den Runner
- (+) Eine fachliche Änderung berührt ein Verzeichnis statt drei
- (+) Der Detektor macht Zonenverstöße sichtbar, statt sie der Disziplin zu überlassen
- (+) Pilots wird extrahierbar, falls es je nötig wird — ohne dass jetzt etwas verteilt wird
- (−) Ein längerer Zeitraum mit zwei Strukturen nebeneinander. Abgemildert durch die Reihenfolge: jeder Merge Request lässt einen vollständigen Kontext zurück
- (−) Importpfade ändern sich flächendeckend; jeder offene Zweig bekommt Konflikte. Deshalb vor M4 und nicht mittendrin
- (−) Charakterisierungstests kosten Zeit, bevor irgendetwas besser aussieht. Ohne sie ist die Verschiebung aber ungeprüft: die Abdeckung liegt dort bei 48 % und 44 %

#### Fortschreibung 17.08.2026 — drei Präzisierungen

Die Struktur selbst steht in **§5.7**; hier nur, was gegenüber der Fassung vom 16.08.
*entschieden* wurde und warum.

1. **Der Kernel ist dreigeteilt.** `core/authz.py` trug den Namen seines kleinsten Teils —
   13 von 219 Zeilen waren Autorisierung, der Rest Identitätslebenszyklus. Unverändert
   verschoben, wäre derselbe Fehlname mitgewandert, an dem `domain/` bereits litt.
   `access.py` wird dabei SQL-frei; das ist die Bedingung, nicht die Folge.
2. **Domain Events kommen zurück auf die Nehmen-Liste** — als Rückgabewert des Aggregats,
   nicht als Bus. Die erste Fassung strich sie mit „ein Ereignisbus ohne Abonnenten ist
   Zeremonie". Das Argument gilt für den Bus; die *Ereignisse* existieren längst als 16
   Audit-Konstanten in Vergangenheitsform, und eine Reaktion darauf ist bereits an zwei
   Stellen dupliziert. Der eigentliche Grund ist aber ein anderer: veröffentlichte
   Schnittstellen tragen Abfragen, keine Benachrichtigungen — und Kontexte brauchen beides.
3. **Eine Naht für scope-abhängige Rechte, ohne das Modell zu bauen.**
   `access.require(actor, minimum, *, scope=None)` sollte den Geltungsbereich entgegennehmen und
   vorerst ignorieren. `scope_memberships` stand seit Migration 001 ungenutzt im Schema; ob und wie
   daraus Rechte werden, ist CHY-92 — eine Entscheidung, keine Umsetzung. *Umsetzungsstand
   (06.11.2026): Die Naht ist nicht gebaut, `require` nimmt `(actor, minimum)`. Mitgliedschaften
   werden angelegt und gelesen (`kernel/scopes.py`, `/v1/scopes/{scope_id}/members`); für
   Laufinhalte setzt die Datenbank sie durch (ADR-047), für Blöcke noch nicht (CHY-237).*

Alle drei folgen derselben Überlegung: Kosten heute nahe null, und der spätere Schritt ist
ein *Hinzufügen* statt einer Änderung an jeder Aufrufstelle.

#### Nachtrag 17.08.2026 — was die SOLID-Prüfung ergänzt hat

Der Entwurf wurde gegen SOLID und die Clean-Architecture-Kriterien **gemessen**, nicht
eingeschätzt. Drei Befunde deckte er nicht ab.

**Ausgangslage (17.08., Produktivcode):**

| Kriterium | Messwert |
|---|---|
| Abstraktionen an Grenzen | **0** `Protocol`, **0** `ABC` |
| Parameter mit konkretem Infrastrukturtyp | **25×** `asyncpg.Pool`, **11×** `aioredis.Redis`, **2×** `asyncpg.Connection` |
| Fachlogik kennt UI/Speicherung | `core/`: **3×** `HTTPException` (geraist), ~19 SQL-Anweisungen · `cli/`: ~5 SQL |
| Abhängigkeitsrichtung | `api→core` 25 · `api→domain` 8 · **`core→api` 5** |
| Tests ohne Stack | **12 von 19** Testdateien brauchen DB oder Client |

1. **Dependency Inversion: Protocol an Kontextgrenzen, konkret innerhalb.** Für die
   Testbarkeit braucht Python kein Protocol — es braucht, dass die Regel den Store nicht
   anfasst, und das leistet das Aggregat. An der **Kontextgrenze** ist es aber keine
   Zeremonie: ADR-030 verlangt, dass Orchestrierung die Freigabe des Repositories *nutzt*
   statt sie nachzubauen. Ohne schmale, deklarierte Schnittstelle greift sie irgendwann in
   dessen `store.py`, und der Grenzwächter kann nichts prüfen, was nicht deklariert ist.
2. **Open/Closed: eine Registrierung für Piloten.** `api/v1/pilots.py` verzweigt an zwei
   Stellen auf `pilot_name == "banking"` (Zeile 160 und 394). Ein dritter Pilot heißt heute:
   bestehenden Code an zwei Orten ändern. Ein Pilot meldet sich künftig an, statt gekannt
   zu werden.
3. **Das CLI-Kriterium war zu schwach.** „Kann die CLI einen Block freigeben" reicht nicht —
   sie schreibt bereits heute direkt in die Datenbank, mit **fünf eigenen SQL-Anweisungen**
   bei 21 % Abdeckung. Das ist eine zweite Implementierung derselben Regeln, nicht deren
   Wiederverwendung. Das Kriterium lautet: **`cli/` enthält null SQL und ruft ausschließlich
   Kontext-Services.**

Die Messwerte gehen als Kennzahlen in das Ratchet (CHY-84) — „25 konkrete Pool-Parameter"
soll eine Zahl sein, die sinkt, keine Behauptung, die altert.

#### Die fünf Clean-Architecture-Kriterien als erzwingbare Regeln

> **Korrektur 19.08.:** Die Zahl für `HTTPException` in `core/` lautete hier zuerst **5**.
> Sie stammte aus `grep -c` und zählte die Importzeile sowie einen Kommentar in
> `tracing.py:211` mit. Der Detektor aus CHY-103 liest den Syntaxbaum und findet **3**
> tatsächliche `raise`. Derselbe Fehler, vor dem der Detektor-Docstring warnt — Prosa als
> Code gezählt, in dem Dokument, das die Regel aufstellt.

Gemessen zu haben genügt nicht — ein Kriterium ohne Detektor ist eine Absichtserklärung, und
davon hatten wir diese Woche genug. Jedes Kriterium bekommt deshalb eine Regel, einen
Zielwert und die Stelle, die ihn prüft.

| # | Kriterium | Regel | 16.08. | **27.08. (Ende M3b)** | Ziel | Erzwungen durch |
|---|---|---|---|---|---|---|
| 1 | **Unabhängigkeit von Frameworks** | Kein `contexts/**` importiert `fastapi` oder `starlette` | `core/` 1 Import | **0** ✅ | 0 | `boundary_framework_free` |
| 2 | **Austauschbare UI** | `HTTPException` nur in `api/`; Kontexte werfen Domänenfehler | **3** in `core/` | **0** ✅ | 0 | `boundary_http_below_transport` |
| 3 | **Austauschbare Datenbank** | SQL nur in `contexts/*/store.py` und `kernel/` | ~19 in `core/`, ~5 in `cli/` | ✅ **0** | 0 außerhalb | `api_db_calls` **und** ein Test — ein Ratchet, das null erreicht, wird eine Wand |
| 4 | **Strenge Abhängigkeitsregel** | Nur nach innen: Zone 1 → 2 → 3. Kein Kontext → Kontext | `core→api` 5 | **3** — alle `core/` → `api.config` | 0 | `boundary_inward_only` |
| 5 | **Testbarkeit der Fachlogik** | Aggregat und `access.py` ohne DB und ohne Client testbar | 12 von 19 Testdateien brauchen einen Stack | `pilots` **57**, `repository` **63** Tests ohne Stack, zusammen 0,14 s | je Kontext | CHY-102, Abdeckung je Modul |
| 6 | **Lose Kopplung** | Ein Aggregat wird an einer Stelle angefasst | `actors` in **6** Dateien, `context_blocks` in 4, `audit_log` in 4 | `pilot_runs`, `context_blocks`, `question_bank`, `audit_log` je **1** · `actors` **2**, beide in `kernel/` | 1 Store je Aggregat | CHY-84 als Kennzahl |

Die Spalte „27.08." ist gemessen, nicht fortgeschrieben — Kriterium 3 aus
`quality-baseline.json`, die vier Wächterzahlen aus `scripts/architecture.py`. Zu 6 fast ein
Fehler eingebaut: Ein `grep` nach `pilot_runs` meldete zwei Dateien, weil `service.py` die
Tabelle in Docstrings nennt und `Disposition.DELETE` wie SQL aussieht. Mit SQL fasst sie
genau eine an. Vierter Fall dieses Monats, in dem Prosa als Code gezählt wurde.

##### Wenn die Kennzahl mitschrumpft (CHY-105)

Die Tabelle oben wäre am 27.08. beinahe mit falschen Zahlen gefüllt worden, und
der Grund verdient einen eigenen Absatz, weil er sich wiederholen kann.

**Zwei Quelllisten hatten `kernel/` und `contexts/` nie aufgenommen** —
`[tool.coverage.run] source` in `pyproject.toml` und `SRC` in
`scripts/quality_gate.py`. Seit CHY-101 nahm damit jeder herausgelöste Kontext
seine Anweisungen aus dem Nenner mit.

Die Wirkung ist tückischer als eine fehlende Messung: **Ein Umbau, der Code an
eine ungemessene Stelle schob, verbesserte die Zahl.** Die Kennzahl belohnte das
Verschieben, statt es zu messen. Bei CHY-104 sah das nach Fortschritt aus; erst
bei CHY-105 schlug es andersherum aus (Abdeckung 44,7 → 39,4 %) und die Pipeline
fiel. `ruff N` stand auf 0 und war tatsächlich 19.

Ehrlich gemessen liegt die Abdeckung bei **52,0 %** statt 44,7 % — nicht besser
getestet, sondern vollständiger gezählt. Die beiden Zahlen sind nicht
vergleichbar, und deshalb steht in der Tabelle oben `dev` daneben und nicht die
alte Grundlinie.

Dasselbe Muster ein drittes Mal am selben Tag: `codespell` zählte, was im
Verzeichnis lag statt was im Repositorium steht. Die sechs von
`generate_fixtures.py` erzeugten PDFs existieren auf der Entwicklungsmaschine
und nicht im CI-Checkout — derselbe Commit ergab 172 hier und 146 dort.

Drei Maßnahmen, damit es nicht wiederkommt:

1. **Ein Test hält die Quelllisten vollständig** — jedes Paket mit `__init__.py`
   muss in `coverage source` stehen. Hätte am Tag von CHY-101 angeschlagen
2. **`codespell` zählt `git ls-files`**, nicht Verzeichnisse
3. **Kein stiller Rückfall.** Fehlt `git`, bricht das Gate mit Begründung ab.
   Ein Wächter, der nach außen ausfällt, ist schlechter als keiner — dieselbe
   Feststellung wie bei Überwachungs- und Sicherungsjobs, die still ausfielen

> Verwandt und derselben Form: ADR-017 pinnt die Linter-Versionen, weil ein
> ungepinntes Werkzeug das Lineal mitten in der Pipeline austauscht. Dort ändert
> sich das Messgerät, hier der Messbereich. Beide Male wird die Differenz dem
> Commit zugeschrieben, der zufällig als Nächstes kam.

Zwei Anmerkungen, damit die Tabelle nicht mehr verspricht, als sie hält:

**Zu 3 und 6 gibt es keinen scharfen Wächter, sondern ein Ratchet.** Ein Detektor, der SQL
außerhalb von `store.py` verbietet, wäre am ersten Tag rot und in der ersten Woche
abgeschaltet — dieselbe Überlegung wie bei den Ruff-Familien in CHY-84. Die Zahl darf
sinken und nicht steigen; das genügt, solange sie sichtbar ist.

**Zu 5 ist der Zielwert bewusst eng gefasst.** „Alle Tests ohne Stack" wäre falsch: die
Datenbankregeln aus Migration 013/014 *müssen* gegen eine echte Datenbank geprüft werden,
und genau dieses Verfahren hat fünf Fehler gefunden, die Mocks nicht zeigen konnten
(CHY-102). Ohne Stack testbar sein muss die **Fachregel**, nicht ihre Absicherung.

Kriterium 2 verdient eine eigene Zeile in der Umsetzung: Domänenfehler ersetzen
`HTTPException` nicht nur, sie werden **einmal zentral** auf Statuscodes abgebildet. Heute
steht die Zuordnung an jeder Fundstelle; nach dem Umbau in einem Exception-Handler je
Oberfläche (ADR-031).

**Betrifft:** CHY-101, CHY-103…108 (Umsetzung), CHY-94 (Abhängigkeitsrichtung), CHY-100 (`pilots.py`), CHY-93 (Abdeckung `deps.py`), CHY-92 (Scope-Rechte), CHY-102 (Teststrategie), CHY-84 (Wächter), ADR-001, ADR-011, ADR-016, ADR-028 · Struktur: §5.7

---

### ADR-031: Die veröffentlichte Oberfläche wird nach Konsumenten geschnitten (neu in v2.9)

**Status:** Entschieden (2026-08-17)

**Kontext:** ADR-030 schneidet den *Code* nach Domänen. Diese Entscheidung schneidet die
*Außensicht* nach Konsumenten. Beide Achsen dürfen sich kreuzen — man darf sie nur nicht
verwechseln.

Cohereny ist als Middleware gebaut: die UI ist ein Konsument, ein MCP-Server soll einer
werden (CHY-26), eine Kundenintegration ebenfalls. Das Interface-Segregation-Prinzip sagt
dazu: *kein Konsument soll von Operationen abhängen, die er nicht braucht.*

#### Befund

| | |
|---|---|
| Audiences | **genau eine** — `oidc_audience: str = "cohereny"` (`api/config.py:44`) |
| Operationen insgesamt | 55, gruppiert nach **Ressource** (`/v1/blocks`, `/v1/workflows` …) |
| Was `cohereny-ui` nutzt | **`/v1/pilots`, 7 Aufrufstellen — 8 von 55 Operationen** |
| Was das Token der UI darf | **alle 55** |

Der einzige heutige Konsument hängt damit an einer Oberfläche, die siebenmal größer ist als
sein Bedarf. Die Rollenprüfung fängt einen Teil davon ab — aber sie beantwortet eine andere
Frage: Rollen sagen „wer darf was", die Audience sagt „wofür wurde dieses Token überhaupt
ausgestellt". Heute beantwortet ein Mechanismus beide.

#### Entscheidung

**Vier Oberflächen, je eine Audience.**

| Oberfläche | Audience | Konsumenten | Enthält |
|---|---|---|---|
| **serve** | `cohereny.serve` | Kundenintegration, MCP | `complete`, `compose`, `route` |
| **author** | `cohereny.author` | UI, Menschen | `blocks`, ~~`merge-requests`~~ (entfallen mit ADR-042), `scopes`, `workflows` |
| **observe** | `cohereny.observe` | Dashboards, Benchmarks | `audit-log`, `benchmarks`, Abdeckung |
| **admin** | `cohereny.admin` | Betrieb | `admin/actors`, Anonymisierung |

1. **Je Oberfläche eine Audience**, ausgestellt von Authentik als eigene Anwendung. Ein
   UI-Token kann einen Admin-Endpunkt dann nicht *ansprechen*, statt erst an der
   Rollenprüfung zu scheitern. Rollen bleiben unverändert — die Audience wirkt davor.
2. **Ein Contract je Oberfläche**, erzeugt aus denselben Routen über Tags. Kein zweiter
   Contract, keine zweite Wahrheit — ADR-029 gilt unverändert.
3. **MCP ist ein Adapter in Zone 1**, gleichberechtigt neben `api/v1` und der CLI, und ruft
   dieselben Kontext-Services. Ein MCP-Server, der über HTTP auf die eigene REST-API
   zugreift, baut Authentifizierung, Fehlerabbildung und Seitenaufteilung ein zweites Mal.
   Präzedenz: GitLab 19.2 bedient MCP nativ im selben Prozess unter `/api/v4/mcp`.
4. **Die Ressourcengruppierung bleibt** für die Dokumentation. Sie ist als Gliederung
   richtig und war nur als *Contract* falsch — „hier ist alles" ist kein Contract.

#### Verworfene Alternative: nur Rollen, keine Audiences

Wäre billiger und deckt den Missbrauchsfall teilweise ab. Verworfen, weil ein Rollen-Gate
erst *nach* der Zuordnung eines Akteurs greift und damit voraussetzt, dass die
Rollenprojektion stimmt. Zwei unabhängige Gründe, eine Anfrage abzulehnen, sind mehr wert
als ein sorgfältigerer — dieselbe Überlegung, aus der ADR-009 die Übergangstabelle
zusätzlich in der Datenbank führt.

#### Verworfene Alternative: getrennte Deployments je Oberfläche

Technisch die sauberste Trennung. Verworfen als verfrüht, aus demselben Grund wie die
Microservices in ADR-030: erst muss sich der Schnitt bewähren.

**Konsequenzen:**

- (+) Ein neuer Konsument bekommt eine schmale Oberfläche statt eines Generalschlüssels
- (+) Der Bruch ist heute mit **einem** Konsumenten fast kostenlos; mit fünf wäre es eine Migration
- (+) MCP bringt keine eigene Logik mit, weil es keine mitbringen kann
- (−) Brechende Änderung für `cohereny-ui`: neues Token, neue Audience
- (−) Vier Authentik-Anwendungen statt einer — Arbeit in der IdP-Konfiguration, und vier Stellen, an denen eine Fehlkonfiguration möglich ist
- (−) Die Zuordnung Route → Oberfläche muss gepflegt werden. Sie gehört als Tag an die Route, nicht in eine Liste daneben

#### Nachtrag aus der Umsetzung (CHY-109, 2026-08-21)

Drei Punkte des Beschlusses haben der Umsetzung nicht standgehalten. Sie stehen hier, statt
oben stillschweigend korrigiert zu werden — ein Beschluss, der nachträglich immer recht
behält, ist als Beschluss wertlos.

**1. Tags taugen nicht als Träger der Zuordnung.** Punkt 2 und der letzte Konsequenzpunkt
verlangen beide, die Oberfläche am Tag der Route abzulesen. Das ist schon heute nicht
möglich: der Tag `governance` trägt Merge Requests (author) *und* das Prüfprotokoll
(observe). Die Zuordnung liegt jetzt als Liste `_ASSIGNMENT` in `api/v1/router.py` — genau
die „Liste daneben", die der Beschluss vermeiden wollte. Sie ist es aber nicht *daneben*:
dieselbe Schleife registriert die Route, hängt das Gate an und füllt `SURFACE_PATHS`, aus
dem die Contracts entstehen. Eine Route kann damit nicht in einem Contract stehen, für den sie
nicht abgesichert ist, und umgekehrt (`tests/test_openapi_contract.py`).

**2. Der Beschluss hat drei Router übersehen.** Die Tabelle nennt sieben Ressourcen; die API
hat dreizehn abzusichernde Router. Nicht genannt und deshalb ergänzt: `pilots` → **author**
(heute der einzige Konsument der UI, und ein Pilotlauf ist menschlich angestoßene Arbeit),
`questions` → **observe** (der Fragenkatalog ist Nachweis über einen Lauf, nicht Autorenwerk),
`feedback` → **author** (eine Korrektur einzureichen *ist* Autorenwerk; das Auswerten gehört
zu observe und existiert noch nicht, CHY-40). Ohne diese Ergänzung wären 40 von 44 Pfaden
ungeschützt geblieben — die Lücke wäre größer gewesen als der geschlossene Teil.

**3. Das Vokabular liegt in `domain/`, nicht in `api/`.** `api/deps.py` braucht die
Audience-Menge zum Prüfen eines Tokens, und das Gate braucht `api/deps.py`, um den Aufrufer
zu kennen — ein Zyklus. `domain/surfaces.py` trug damals Enum und Audiences (dieselbe Form
wie `domain/roles.py`; seit der Auflösung von `domain/` `kernel/surfaces.py` und `kernel/roles.py`),
`api/surfaces.py` nur noch das Gate. Beide zeigen nach innen.

**Was zusätzlich entstand:** `contracts/openapi-{serve,author,observe,admin}.yaml`, erzeugt
aus derselben Anwendung wie `openapi.yaml`, mit auf Erreichbarkeit beschnittenen Schemata
(31 → 3/10/6/20). Erst der Beschnitt macht Interface Segregation zu einer Eigenschaft der
Datei statt zu einer Behauptung im Dokument: wer aus `openapi-serve.yaml` einen Client
erzeugt, bekommt keine Admin-Klassen. `/v1/health` steht in keinem der vier — es nimmt kein
Token an und darf keines verlangen, sonst meldet die Bereitschaftsprüfung den Dienst als tot,
sobald ein Token falsch ist.

**Noch offen (Stand des Beschlusses):** die vier Authentik-Anwendungen. Bis sie existieren, akzeptiert
`_require_known_audience` zusätzlich die alte Audience `cohereny` — ohne diese Übergangszeit
wäre die UI am Merge-Tag ausgesperrt. Solange kein Token eine der vier Audiences trägt, ist
das Gate wirkungslos und nur die Contracts wirken. Das ist die ehrliche Beschreibung des
Zustands, nicht ein halb umgesetzter Beschluss.

> **Nachtrag (erledigt):** Die vier Anwendungen existieren in der Referenzinstallation, die Übergangs-Audience
> `cohereny` ist aus `_require_known_audience` entfernt (`api/deps.py`). Das Gate wirkt: ein Token
> ohne eine der bekannten Audiences wird abgewiesen.

#### Nachtrag: Maschinen-Clients je Oberfläche (CHY-124, 2026-09-24)

**Anlass.** Die Prüfung nach dem Deploy auf die Teststufe braucht Tokens, hinter
denen kein Mensch steht. Die vier Anwendungen stellen Tokens nur über
`authorization_code` aus — für einen Browser, nicht für eine Pipeline.

**Verworfen: `client_credentials` an den bestehenden vier Providern.** Authentik kennt bei
diesem Grant einen Modus, in dem das Client-Secret *allein* ein Token prägt — für ein Konto,
das Authentik dafür selbst anlegt (`ak-<provider>-client_credentials`). Am Provider der UI
würde deren Secret damit zu einem Weg, Tokens ohne Person dahinter zu erzeugen. Das hatte
die IdP-Konfiguration der Referenzinstallation schon am 21.08. ausgeschlossen, und der Grund hat sich beim
Nachlesen bestätigt.

**Entschieden (Thomas, 24.09.):** Je Oberfläche ein zweiter Client, nur für Maschinen.

| Oberfläche | UI-Client | Maschinen-Client |
|---|---|---|
| serve | `cohereny.serve` | `cohereny.serve.ci` |
| author | `cohereny.author` | `cohereny.author.ci` |
| observe | `cohereny.observe` | `cohereny.observe.ci` |
| admin | `cohereny.admin` | `cohereny.admin.ci` |

1. **Die Maschinen-Clients kennen nur `client_credentials`**, die UI-Clients weiterhin nur
   `authorization_code` und `refresh_token`. Kein Provider hat beide
2. **Ihre Secrets liegen nur im Secret-Store des Betreibers und in den CI-Variablen** (maskiert, geschützt) — nie in
   der UI, nie in einer `.env` der Anwendung
3. **Die API akzeptiert je Oberfläche genau zwei Audiences**, die der UI und die der CI.
   `kernel/surfaces.accepted_audiences()` ist die einzige Stelle, die das festlegt
4. **Die Kerneigenschaft dieses ADR bleibt:** ein Token, eine Oberfläche.
   `cohereny.author.ci` öffnet `author` und sonst nichts, genau wie `cohereny.author`

**Was sich damit verschiebt.** Aus vier Authentik-Anwendungen werden acht — der letzte
Minuspunkt der Konsequenzen („vier Stellen, an denen eine Fehlkonfiguration möglich ist")
verdoppelt sich. Die Slugs stehen zweimal, in `kernel/surfaces.py` und in der IdP-Konfiguration des
Betreibers — sie kann kein Python importieren, die Grenze lässt sich nicht zusammenlegen. Ein
Unit-Test hält nur die Namensregel fest, sodass eine Umbenennung auf der Python-Seite auffällt;
gegen die IdP-Konfiguration prüft erst die Prüfung nach dem Deploy: ein abweichender Slug ergibt einen
unbekannten Issuer, und der Job wird rot. Dass die Oberflächen getrennt bleiben, zeigen zwei
Gegenproben (Gate akzeptiert jede bekannte Audience: 7 Tests rot; `accepted_audiences`
liefert alle: 11 rot).

**Offen bei Niederschrift:** Die Clients selbst. Bis sie existieren, trägt kein
Token eine `.ci`-Audience, und die Änderung wirkt nicht — sie öffnet nichts, solange niemand
ein passendes Token prägen kann.

**Betrifft:** CHY-26 (MCP), Authentik-Konfiguration, ADR-029 (erzeugter Contract), ADR-030 (Zonen), CHY-92 (Scope-Rechte) · Oberflächen: §5.7

---

### ADR-032: Ein Nachweis enthält nur, was gewirkt hat (neu in v2.18)

**Status:** Entschieden (2026-09-04, CHY-42)

**Kontext:** `pilot_runs.effective_config` soll festhalten, welche Konfiguration ein Lauf tatsächlich benutzt hat — die Grundlage für Art. 12 EU AI Act und für die Frage „warum kam bei diesem Lauf das heraus". CHY-42 legt die Konfiguration in eine Tabelle (`workflow_configs`) mit zwei Sektionen: `model_map` und `params`.

Beim Umsetzen trafen zwei Vorgaben aufeinander:

- Der Nachweis soll **vollständig** sein — jeder Wert, der den Lauf beeinflusst hat
- **ADR-028 Punkt 4** hält Study und Banking aus dem Umbau heraus: der eigene Runner (CHY-127) löst sie ohnehin ab, ein Umbau wäre Arbeit an Code mit Ablaufdatum

Vollständigkeit hieße, alle 80 Verwendungen der Tuning-Werte in beiden `nodes.py` umzustellen. Die Alternative — die Werte in die Tabelle schreiben und die Knoten weiter ihre Konstanten lesen lassen — sieht nach dem billigeren Kompromiss aus.

**Sie ist der teuerste.** Ein Nachweis, der einen Wert nennt, den niemand gelesen hat, ist nicht unvollständig, sondern **falsch** — und zwar unbemerkt, weil er vollständig aussieht. Wer ihn später prüft, prüft eine Behauptung, die nie stimmte, und merkt es nicht.

**Entscheidung:** Der Inhalt eines Nachweises ist genau das, was auch wirkt — keine Zeile mehr.

1. `contexts/pilots/workflow_config.py` benennt die wirksamen Sektionen in **einer** Konstante (`EFFECTIVE_SECTIONS`). Der Schnappschuss wird daraus gebaut
2. Sektionen, die gespeichert und ausgeliefert werden, aber (noch) nicht wirken, stehen getrennt in `STORED_ONLY_SECTIONS` — „nicht wirksam" und „unbekannt" sind verschiedene Zustände und ergeben verschiedene Fehler
3. **Ein Override auf eine unwirksame Sektion wird abgelehnt, nicht ignoriert.** Eine API, die eine Anweisung entgegennimmt und nichts damit tut, meldet Erfolg, ohne hingesehen zu haben — der Aufrufer glaubt, sein Wert sei in Kraft
4. Ein Test hält das offen: wächst der Schnappschuss über `EFFECTIVE_SECTIONS` hinaus, wird er rot. Die Sektionsliste zu erweitern ist damit eine bewusste Handlung mit sichtbarem Diff
5. Wo ein Rückfall auf lokale Konstanten nötig bleibt (Direktaufruf über die CLI ohne Konfigurationszeile), belegt ein Test, dass er **in Produktion nicht erreichbar** ist — die gesäte Karte deckt jede Stufe ab, die die Knoten erfragen

**Konsequenzen:**

- (+) Der Nachweis ist knapper und wahr statt vollständig und falsch. Für eine Aufsichtsfrage ist das der einzige Unterschied, der zählt
- (+) Die Grenze ist an einer Stelle benannt und getestet, nicht in Kommentaren verteilt
- (+) CHY-82 erbt eine klare Aufgabe: `params` wirksam machen **und** in `EFFECTIVE_SECTIONS` aufnehmen — beides oder keines. *Stand 31.10.2026: nicht geschehen — CHY-82 und der Runner (CHY-127) sind ohne diesen Schritt abgeschlossen, `params` ist weiter nur gespeichert.*
- (−) Ein Aufrufer, der `params` je Lauf setzen will, bekommt bis dahin eine Ablehnung statt einer Wirkung. Das ist beabsichtigt und trotzdem lästig
- (−) Zwei Sektionslisten sind eine Stelle mehr, die beim Erweitern mitgeführt werden muss. Der Test macht das Vergessen sichtbar, verhindert es aber nicht

**Verallgemeinerung, die über dieses Ticket hinausgeht:** Dieselbe Frage stellt sich bei jedem Artefakt, das etwas bezeugt — Transparenz-Datensätzen, Audit-Einträgen, Abdeckungsberichten. Die Regel lautet nicht „so vollständig wie möglich", sondern **„so weit, wie die Behauptung trägt"**. Beim Bauen fiel unter derselben Überschrift auf, dass `model_used` im Transparenz-Datensatz aus den Einstellungen des Deployments kam statt aus dem, was der Lauf benutzte.

**Betrifft:** CHY-42, CHY-82 (`params` wirksam machen), ADR-028, ADR-004.

---


### ADR-033: Ein Knoten sieht die Ausgabe seiner unmittelbaren Vorgänger, nach Herkunft getrennt (neu in v2.20)

**Status:** Entschieden (2026-09-04, CHY-131)

**Kontext:** Bis CHY-131 trug `NodeContext.input_data` die **Laufeingabe** — für jeden Knoten dieselbe, von der ersten bis zur letzten Runde. `node_executions.output_data` wurde geschrieben und von niemandem gelesen. Damit war der Pflichtkern von ADR-004 nicht verbunden: `context_compose` baute einen Kontext zusammen, den `llm_call` nicht empfangen konnte.

Der Fehler war nicht sichtbar, weil er nichts kaputt machte. Läufe liefen durch, jeder Knoten meldete Erfolg, die Ablaufverfolgung war lückenlos. Nur die Arbeit passierte nicht — dasselbe Muster wie bei Sicherungsjobs, die Erfolg melden und nichts tun, und beim Nachweis in ADR-032.

**Entscheidung:** Ein Knoten sieht drei Quellen, und sie teilen sich **keinen** Namensraum.

1. Die Laufeingabe unverändert unter dem Schlüssel `run`
2. Die Ausgabe jedes **unmittelbaren** Vorgängers unter dessen `node_ref`
3. Was das eigene `input_mapping` daraus auswählt, **obendrauf** statt anstelle

**Warum getrennt und nicht zusammengeführt.** Der naheliegende Entwurf ist ein Wörterbuch, in das alle schreiben. Es ist auch der, bei dem zwei Knoten einander stillschweigend überschreiben und der Verlierer derjenige ist, dessen Zeile zufällig als zweite zurückkam. Ein Fehler, der von der Zeilenreihenfolge abhängt, erscheint und verschwindet von allein.

**Warum unmittelbare Vorgänger und nicht alles bisherige.** „Alles" ist bequemer und macht den Lauf zu einem großen gemeinsamen Geltungsbereich, in dem ein Knoten unbemerkt von einem Wert abhängen kann, der drei Zweige entfernt entstand. Wer etwas von weiter oben braucht, bekommt eine **Kante** — dann steht der Datenweg im Bild statt in einem Wörterbuch.

**Warum obendrauf und nicht anstelle.** Ersetzte das Mapping die Struktur, bräche das Hinzufügen eines Mappings an *einem* Knoten jeden Executor, der die rohe Form liest — zur Laufzeit, in einem Workflow, in Produktion.

**Was aufgezeichnet wird.** Die zusammengebaute Eingabe wird beim **Beanspruchen** nach `node_executions.input_data` geschrieben, nicht später rekonstruiert. Bei einer Schleife sind das verschiedene Dinge: Die dritte Runde las, was ihr Vorgänger in der dritten Runde erzeugte, und bis jemand fragt, hat eine vierte Runde die Antwort ersetzt, die eine Rekonstruktion fände. Deshalb liest `store.predecessor_outputs` mit `DISTINCT ON (node_id) … ORDER BY node_id, iteration DESC` und **nur** abgeschlossene Durchläufe: eine gescheiterte hat keine weitergabewürdige Ausgabe, eine laufende noch keine — beide würden dem Knoten die Reste der Vorrunde reichen und dabei aussehen wie ein frischer Wert.

**Folgen.**

- Ein Pfad, der ins Leere zeigt, wird **weggelassen**, nicht als `None` geschrieben. Ein Knoten, der `input_data["prompt"]` liest, soll einen `KeyError` bekommen, wenn niemand einen Prompt erzeugt hat — keine leere Zeichenkette, die ein Modell erreicht und mit einer Antwort auf keine Frage zurückkommt
- „Nicht vorhanden" und „null" sind verschiedene Antworten. Deshalb hat die Pfadauflösung einen eigenen Wert für Abwesenheit
- Pfadauflösung gibt es **einmal** (`dataflow.resolve_path`), von Bedingungen (ADR-026, CHY-130) mitbenutzt. Zwei Auflösungen von „was bedeutet `a.b.c`" driften, und an dem Tag, an dem sie sich widersprechen, lesen ein Wächter und ein Mapping dieselbe Adresse verschieden — ein Fehler, den niemand sucht, weil die beiden Funktionen unverwandt wirken
- `NodeContext` bekommt zugleich `config`, `scope_id` und `actor_id`, die aus demselben Grund nie übergeben wurden. Der Geltungsbereich kommt von der **Definition**, nicht vom Lauf: ein zweiter Geltungsbereich je Lauf wäre eine zweite Antwort auf eine Frage, die `workflow_definitions.scope_id` schon beantwortet

**Nachtrag 04.09.2026 (CHY-133): `workflow_id` gehört dazu.** Beim Bau des
`context_compose`-Executors kam heraus, dass `node_ref` nur **je Workflow** eindeutig ist
(UNIQUE in `001_initial`). Alles, was allein über den Namen schlüsselt, kollidiert, sobald ein
zweiter Workflow einen Knoten gleich nennt — beim Kompositions-Cache (ADR-010) heißt das:
jeder bekommt den Kontext des anderen, und es sieht aus wie ein Cache-Treffer, nicht wie ein
Fehler. Der Name allein ist keine Adresse; er wird erst mit dem Workflow zu einer.

### ADR-034: Was der Validator verlangt, muss das Deployment ausführen können (neu in v2.21)

**Status:** Entschieden (2026-09-04, CHY-133)

**Kontext:** ADR-004 macht drei Knotentypen zum Pflichtkern, und der Validator weigert sich, einen Workflow ohne sie zu aktivieren:

    context_compose  →  llm_call  →  human_review

Am 04.09. war genau **einer** davon ausführbar. `human_review` braucht keinen Executor — es ist ein Wartezustand der Zustandsmaschine (ADR-028) —, aber `context_compose` hatte keinen. **Jeder gültige Workflow wäre am ersten Knoten gescheitert**, solange es den Runner gibt.

Der Fehler war strukturell, nicht schludrig: Die eine Seite — was ein Workflow enthalten **muss** — steht im Validator. Die andere — was dieses Deployment ausführen **kann** — steht in der Kompositionswurzel. Beide waren für sich vollständig, und **nichts verglich sie**.

Das ist dasselbe Muster wie die Zonenverstöße, die eingesammelt und nie geprüft wurden (CHY-103), und wie ADR-032: zwei Listen, die übereinstimmen müssen, ohne dass jemand die Übereinstimmung erzwingt. Übereinstimmung durch Zufall hält, bis eine der beiden Seiten wächst.

**Entscheidung:** Ein Test leitet aus `MANDATORY_NODE_TYPES` ab, dass die Registry sie bedienen kann.

1. Die Prüfung wird aus der **Konstante erzeugt**, nicht aus einer daneben geschriebenen Liste. Ein vierter Pflichttyp wäre sonst wieder validatorpflichtig und still nicht lauffähig
2. `human_review` ist die dokumentierte Ausnahme, und eine **Gegenprobe** hält sie offen: die Registry darf ihn *nicht* führen. Ohne sie bestünde der Test auch, wenn jemand einfach alles registriert
3. Der Executor sitzt im **Transport**, nicht im Kontext. `contexts/orchestration` darf `contexts/repository` nicht importieren (ADR-030); die Kompositionswurzel darf beide kennen — das ist ihre Aufgabe

**Was der erste Anlauf des Wächters gezeigt hat.** Er rief `registry.for_type(...)` und wertete das Ergebnis nie aus. `for_type` wirft bei einem unbekannten Typ nicht, sondern gibt einen *Refuser* zurück, der erst beim Aufruf wirft — der Test beschrieb die Regel also, ohne sie auszuüben, und blieb grün, als die Gegenprobe `context_compose` wieder entfernte. Jetzt über `has()`.

**Zwei stumme Stellen sind dabei laut geworden:**

- Komposition **ohne Geltungsbereich** wird abgelehnt statt leer zusammengesetzt. Ein leerer Kontext erreicht ein Modell und sieht dabei aus wie ein echter
- `llm_call` **ohne Prompt** wird abgelehnt, statt `""` zu senden. Die Gegenprobe zeigt: ohne die Prüfung geht die Anfrage raus und scheitert erst am Gateway — die Meldung nannte dann den Gateway statt den Workflow, dem die Abbildung fehlt

**Folge für den Datenweg.** Nichts verbindet `context_compose` und `llm_call` durch Konvention. Die Kante trägt **Steuerung**, das `input_mapping` trägt **Daten** (ADR-033) — `{"prompt": "<compose-knoten>.prompt"}`, im Workflow benannt und im Graphen sichtbar. Seit CHY-189 erreicht auch `system` den `llm_call` über das Mapping: `{"system": "compose.prompt", "prompt": "run.message"}` gibt den Kontext als Anweisung und die Eingabe des Laufs als Anfrage, dieselbe Aufteilung wie bei `/v1/complete`.

**Nachweisumfang.** Der Knoten gibt `block_versions` und `scope_generations` mit aus, nicht nur den Prompt. Sie sagen, *was* die Komposition enthielt; ohne sie ist der Lauf nicht reproduzierbar. Ein Nachweis, der nur das Ergebnis nennt, ist ADR-032 mit umgekehrtem Vorzeichen — er sieht vollständig aus und ist nicht prüfbar.

### ADR-035: Cohereny wählt die Fähigkeit, LiteLLM verteilt die Last (neu in v2.22)

**Status:** Entschieden (2026-09-07, CHY-87)

**Kontext:** Am 13.08.2026 zeigte **jeder** Task-Typ auf ein Modell, das nur ein Host bedient. Der Host war aus, und `/v1/complete` antwortete durchgehend 502. `resolve()` lieferte einen Namen — ein Name hat keine Antwort auf „und wenn nicht der".

Die naheliegende Reparatur wäre, die Ausweichentscheidung LiteLLM zu überlassen: das Gateway kennt die Hosts und verteilt ohnehin. Damit entschieden aber **zwei Schichten dasselbe mit unterschiedlichem Wissen** — Cohereny kennt die Datenschutzstufe eines Knotens, LiteLLM kennt sie nicht.

**Entscheidung:** Der Schnitt liegt an der Frage, *was* gewählt wird.

- **Cohereny wählt die Fähigkeit:** welches logische Modell eine Aufgabe bearbeitet. Das ist eine fachliche Entscheidung mit Datenschutzfolgen und gehört in den Nachweis
- **LiteLLM verteilt innerhalb eines logischen Modells:** welcher Host antwortet. Das ist Betrieb und interessiert den Nachweis nicht

**Die Regel läuft anders herum als die naheliegende Lesart**, und das ist der eigentliche Ertrag dieser ADR. Alle Kandidaten gehen an **dasselbe** Gateway. Daraus folgt:

| Fall | Bedeutung | Kette |
|---|---|---|
| `ModelUnreachable` | Das Gateway schweigt | **hilft nicht** — der zweite Kandidat fragt dasselbe stumme Gateway |
| `ModelRefused` 5xx | Das Gateway antwortet und meldet den Ausfall *dieses* Modells | **genau dafür da** — das ist der 13.08. |
| `ModelRefused` 429 | Ratenbegrenzung, eine Aussage über Verfügbarkeit | schaltet weiter |
| `ModelRefused` übrige 4xx | Die Anfrage ist falsch: unbekanntes Modell, abgelaufener Schlüssel | **hält an** — kein Kandidat repariert das, und weiterzuschalten versteckte einen Tippfehler hinter einem funktionierenden zweiten Eintrag |

Wer „bei Unerreichbarkeit weiterschaltet", hätte den Vorfall, für den die Kette gebaut wurde, **nicht** verhindert.

**Das Privacy-Ratchet.** Ein Fallback feuert, wenn ohnehin schon etwas kaputt ist — der schlechteste Moment dafür, dass Daten irgendwohin wandern, wo sie weniger geschützt sind. Deshalb:

1. Jedes Modell erklärt in derselben Datei wie die Routen, welche **strengste Datenklasse** es sehen darf. Eine zweite Quelle für Modell-Metadaten wäre eine zweite Wahrheit
2. Eine Kette darf diese Stufe nicht **unterschreiten**. Gemessen wird gegen den **ersten** Kandidaten: er ist der, den der Autor für diese Aufgabe gewählt hat, also setzt seine Obergrenze den Boden. Ein paarweiser Vergleich der Nachbarn lehnt `[gehostet, lokal, gehostet]` fälschlich ab — dort hält jeder Kandidat den Boden, und der Abstieg vom strengeren Mittelglied zurück ist keiner
3. Geprüft wird beim **Laden**, nicht beim Aufruf. Jeder Fehler, den diese Prüfung findet, lässt sich durch einen Redeploy beheben und nicht im Betrieb; ihn zur Fallback-Zeit zu entdecken hieße, ihn im ungünstigsten Moment zu entdecken

**Der Nachweis.** arc42 §6.6 sagte, `routing_decision` lande im Audit-Trail. Eine solche Aktion gab es nicht. Sie heißt jetzt `routing.fell_back` und wird **nur beim Fallback** geschrieben: ein Eintrag je Aufruf ersäufte das Protokoll und versteckte die Ausnahme, die er zeigen soll. Die Routing-Tabelle hält fest, was **beabsichtigt** war; nur diese Zeile hält fest, was **geschehen** ist — und das ist die Frage, die eine DORA- oder MaRisk-Prüfung stellt.

Geschrieben wird sie im Transport, nicht in `core.completion`: dort gibt es einen Verbindungspool, hier bewusst keinen (ADR-030, CHY-129). Die Vervollständigung gibt die Tatsachen zurück, der Aufrufer schreibt sie auf.

**Offen, vermerkt statt behoben.** Der Kontext wird für den **ersten** Kandidaten kompiliert. Heute ist `model_id` nur eine Dimension des Cache-Schlüssels — die Komposition formt den Inhalt nicht nach Modell. Sobald sie das tut (ADR-010), bekäme das antwortende Modell einen Kontext, der für ein anderes gebaut wurde.

### ADR-036: Nachweispflichtig ist, was in den Prompt gelangt — nicht, was gefunden wurde (neu in v2.23)

**Status:** Entschieden (2026-09-07, CHY-140)

> **Umsetzungsstand (31.10.2026):** nicht gebaut. Den Knotentyp gibt es nicht (CHY-140, offen), und `as_prompt()` reiht weiterhin nur aneinander. Auch `tool_call`, auf dessen Reihenfolge Punkt 1 verweist, hat noch keinen Executor (CHY-136).

> **Präzisiert durch ADR-044 (03.11.2026):** „Einbettungen“ in der Zeile „Bleibt Non-Goal“ meint Einbettungen für Retrieval — als Index, für Ähnlichkeitssuche, als gespeicherter Bestand. Als Messfühler für Drift sind sie zulässig: ohne Index, ohne Suche, ohne Vektoren je Anfrage; gespeichert werden Kennzahlen je Zeitfenster. Die Entscheidung selbst bleibt, wie sie getroffen wurde.

**Kontext:** Das Whitepaper führte unter den Non-Goals „**Vector-Datenbank / RAG-System** — kein Retrieval-Augmented Generation Stack; das Kontext-Repository speichert Adaptionsregeln, keine Dokument-Chunks".

Das ist eine Aussage über die **Speicherung**. Gelesen wurde sie als Aussage über das **Verfahren** — und traf damit auch die Frage mit, *welche Abschnitte in einen Prompt geraten sind*. Genau diese zweite Frage ist die regulatorisch harte, und sie hat mit einem Vektorspeicher nichts zu tun.

Aufgefallen ist es von außen: Beim Schreiben über RAG für das Buch stellte sich heraus, dass es das einzige substanzielle Kapitel ohne eigene Messung dahinter ist — weil Cohereny zu RAG keine Position hatte, die über „machen wir nicht" hinausgeht.

**Die Entscheidung:** Die beiden Fragen werden getrennt.

| | |
|---|---|
| **Bleibt Non-Goal** | Vektorspeicher, Ähnlichkeitssuche, Chunking, Einbettungen. Dafür gibt es pgvector und LlamaIndex; ein zweiter davon macht das Produkt schlechter |
| **Wird Ziel** | Die **Herkunft jedes Prompt-Bestandteils**: welcher Korpusstand galt, was geliefert wurde, **was davon den Zusammenbau überlebt hat und was nicht** |

**Der Schwerpunkt liegt auf dem zweiten Schritt, nicht auf dem ersten.** Das ist beim ersten Entwurf dieser Entscheidung zu kurz gekommen und der Grund, warum sie umbenannt wurde: Sie hieß „Der Nachweis des Retrievals".

RAG hat drei Schritte, und die Regulierung trifft den mittleren am härtesten:

| Schritt | Was er festlegt | Wer es heute aufzeichnet |
|---|---|---|
| Retrieval | welche Abschnitte in Frage kommen | der Retriever, mehr oder weniger |
| **Augmentierung** | **welche davon tatsächlich im Prompt stehen — in welcher Reihenfolge, gekürzt worauf** | **niemand** |
| Generierung | die Antwort | Langfuse, `transparency_records` |

**Artikel 26 Absatz 4 spricht von Eingabedaten.** Das ist der fertige Prompt, nicht die Treffermenge. Ob die Eingabedaten „der Zweckbestimmung entsprechen und ausreichend repräsentativ sind", entscheidet der Schritt, der zwanzig Treffer auf vier kürzt, weil das Kontextfenster voll ist — und nicht der, der die zwanzig gefunden hat. Ein Nachweis, der bei der Treffermenge endet, belegt eine Repräsentativität, die der Prompt nicht mehr hat.

**Artikel 12 ebenso:** Dieselbe Treffermenge ergibt je nach Reihenfolge, Kürzung und Vorlage einen anderen Prompt. Ohne die Zusammenbauregel ist ein Lauf nicht wiederholbar, auch wenn jede Eingabe protokolliert ist.

**Und die Kürzung ist stumm.** Ein Abschnitt, der wegen des Token-Budgets herausfällt, hinterlässt nirgends eine Spur — nicht in der Antwort, nicht beim Retriever, der von der Kürzung nichts erfährt. Das ist dieselbe Fehlerform wie ein verworfener Treffer, eine Stufe später und schlechter sichtbar.

**Cohereny hat dafür bereits die Hälfte.** `compose_context` schreibt `block_versions` — welche Fassung welches Bausteins in den Prompt gegangen ist (ADR-010), und `as_prompt()` ist die eine Stelle, die aus Bausteinen einen Prompt macht. Das *ist* Augmentierungs-Nachweis, nur für die eigenen Bausteine. Für zugeliefertes Material gibt es kein Gegenstück, und genau dort klafft die Lücke.

**Warum das keine Aufweichung ist, sondern eine Schärfung.** Die drei Artikel liegen auseinander, und der Abstand ist der Punkt:

- **Artikel 26 Absatz 4** trifft den **Betreiber**: soweit die Eingabedaten seiner Kontrolle unterliegen, sorgt er dafür, dass sie „der Zweckbestimmung entsprechen und ausreichend repräsentativ sind". Bei Retrieval bestimmt ein Programm die Eingabedaten **je Anfrage neu**. Wer die Auswahl nicht aufzeichnet, kann diese Pflicht weder erfüllen noch im Streitfall widerlegen — und aufzeichnen kann sie nur, wer im Ablauf sitzt
- **Artikel 12** verlangt Rückverfolgbarkeit. Der Prompt genügt dafür nicht: derselbe Prompt liefert bei einem anderen Korpusstand eine andere Antwort
- **Artikel 10 greift nicht.** Er gilt für Trainings-, Validierungs- und Testdatensätze, und ein Retrieval-Korpus ist keines von dreien. Ausgerechnet der Bestand, der die Antwort im Betrieb bestimmt, fällt aus dem Wortlaut und wird nur über Artikel 26 Absatz 4 beim Betreiber wieder eingefangen

Ein Werkzeug, das diese Lücke schließt, ohne selbst zu suchen, ist genau das Unterscheidungsmerkmal, das die Non-Goal-Zeile bisher verdeckt hat.

**Umsetzung, zweiteilig.** Der eine Teil nimmt entgegen, der andere hält fest, was überlebt hat.

**1 — Ein achter Knotentyp** neben `context_compose`, `llm_call`, `tool_call`, `fork_gateway`, `join_gateway`, `human_review` und `sub_workflow`. Sein Vertrag: **er führt das Retrieval nicht aus, er nimmt es entgegen.**

- Eingabe sind die gelieferten Abschnitte mit Kennung, Bewertung und Herkunft, dazu eine Kennung des **Korpusstands**
- Die Auswahl steht im Protokoll, **bevor** der nächste Knoten den Prompt baut — dieselbe Reihenfolge wie beim Werkzeugaufruf (CHY-136) und aus demselben Grund
- **Auch das vom Retriever Verworfene gehört hinein**, soweit er es meldet
- Fehlt die Korpuskennung, **bricht der Knoten ab**. Ein Nachweis ohne Bezugsgröße ist keiner — dieselbe Regel wie bei `context_compose` ohne Geltungsbereich (ADR-034)

**2 — Der Zusammenbau wird nachweispflichtig, und das ist der schwerere Teil.** `as_prompt()` ist heute eine Aneinanderreihung mit Trennzeichen: sie kürzt nicht, ordnet nicht um und lässt nichts weg — deshalb *kann* sie heute nichts verschweigen. Genau das ändert sich, sobald zugeliefertes Material dazukommt, das nicht ins Fenster passt.

Deshalb gilt ab dann: **jeder Bestandteil, der den Zusammenbau nicht überlebt, wird mit seinem Grund festgehalten** — Fenster erschöpft, Rangschnitt, Doppelung. Eine Kürzung ohne Vermerk ist die stumme Variante desselben Fehlers, und sie trifft die Repräsentativitätspflicht aus Art. 26 Abs. 4 unmittelbar.

**Die naheliegende Alternative — den fertigen Prompt einfach mitspeichern — trägt nicht.** Sie beantwortet „was stand drin", nicht „was stand nicht drin und warum". Die zweite Frage ist die, die eine Aufsicht stellt, und aus dem Prompt allein ist sie nicht zu beantworten: Was fehlt, sieht man ihm nicht an.

**Was diese Entscheidung nicht tut.** Sie macht Cohereny nicht zu einem RAG-System und ändert nichts an der Speicherung. Wer einen Retriever betreibt, betreibt ihn weiter selbst; Cohereny nimmt dessen Ergebnis entgegen und macht es nachweisbar. Der Knoten ist eine **Schnittstelle mit Beweispflicht**, kein Suchindex.

**Und sie erweitert eine Linie, die schon liegt.** ADR-033 hat festgelegt, dass jeder Knoten sieht, woher seine Eingabe stammt — die Laufeingabe unter `run`, jeder Vorgänger unter seinem `node_ref`. ADR-010 hält fest, welche Bausteinfassungen in einen kompilierten Kontext gingen. Zugeliefertes Material ist die dritte Quelle, und sie war die einzige ohne Herkunftsangabe.

### ADR-037: Nicht jeder Kontextbaustein darf in einen Prompt (neu in v2.24)

**Status:** Entschieden (2026-09-07, CHY-87)

**Kontext:** Die Routing-Tabelle lag in `router/routing-config.yaml` — eine Datei im Abbild, änderbar nur per Redeploy. Der Entwurf sagt seit jeher etwas anderes: Eine Routing-Regel ist ein **ContextBlock**, versioniert und freigabepflichtig. ADR-017 hängt sogar ein Feld an `routing_rule.action`, und `016_privacy_ratchet.sql` hält daneben fest, dass es solche Zeilen nicht gibt.

Sie in `context_blocks` aufzunehmen bringt die vorhandene Maschinerie mit: Versionen, den Freigabepfad, den Privacy-Ratchet aus CHY-62 und die Scope-Vererbung. **Ein tieferer Geltungsbereich darf eine Routing-Regel verschärfen und nicht lockern** — das fällt aus ADR-017 heraus, statt hier ein zweites Mal gebaut zu werden.

Eines kommt nicht umsonst mit, und dafür gibt es diese Entscheidung.

**Das Problem.** `compose` nahm ohne Typfilter **jeden aktiven Block im Geltungsbereich**. Das war richtig, solange jeder Blocktyp Prompt-Material war — und genau das galt für die fünf bisherigen. Eine Routing-Regel im System-Prompt verriete dem Modell, welche Modelle es gibt und wie eines gewählt wird.

**Und zwar lautlos.** Ein längerer Prompt sieht aus wie ein reicherer Kontext, nicht wie ein Leck. Nichts würde rot, keine Antwort sähe falsch aus, und die Routing-Tabelle stünde in jeder Spur, die Langfuse hält.

**Entscheidung:** Es gibt eine **Positivliste** der komponierbaren Blocktypen, und der Filter ist Teil der Abfrage.

1. `COMPOSABLE_BLOCK_TYPES` in `contexts/repository/schema.py`, gegen die CHECK-Bedingung gehalten von `tests/test_status_vocabularies.py` — dasselbe Verfahren wie bei den übrigen Vokabularen, weil SQL kein Python importieren kann
2. **Positivliste, nicht Negativliste.** Ein siebter Typ ist morgen *nicht* komponierbar, bis jemand etwas anderes sagt. Bei einer Ausschlussliste wäre er komponiert, sobald er existiert, und wer ihn anlegt, hätte von einer Regel wissen müssen, die er nie gelesen hat. Das ist die sichere Richtung, sich zu irren
3. **Die Abfrage filtert immer**, nicht nur wenn gefragt. „Nichts Bestimmtes" heißt jetzt „was komponiert werden darf", nie mehr „alles"
4. **Eine ausdrückliche Anfrage gewinnt nicht.** `block_types` wird mit der Positivliste geschnitten. Wer einen nicht komponierbaren Typ nennt, irrt sich oder hat Böses vor — und keines von beiden ist ein Grund, die Routing-Tabelle vor ein Modell zu stellen

**Warum der Filter in der Abfrage sitzt und nicht in einer Review-Regel.** Der Fehler, den er verhindert, ist still. Eine Regel, gegen die man verstoßen kann, ohne dass etwas passiert, wird irgendwann übersehen; eine Abfrage, die die Zeilen gar nicht erst liefert, kann man nicht vergessen.

**Folge für die Lesenden.** Routing-Regeln werden **nicht** über `compose` gelesen. Komposition ist für Prompt-Material; Konfiguration bekommt ihre eigene Abfrage. Zwei Wege in dieselbe Tabelle, mit verschiedenen Fragen — und ohne die Möglichkeit, sich zu vertun.

### ADR-038: Was gewählt werden darf ist Infrastruktur, was gewählt wird ist Governance (neu in v2.25)

**Status:** Entschieden (2026-09-07, CHY-87)

**Kontext:** `router/routing-config.yaml` trug zwei Dinge in einer Datei: welche Modelle diese Installation erreicht, und welches davon welche Aufgabe bearbeitet. Beide änderten sich nur per Redeploy.

Der Entwurf sagt für das Zweite etwas anderes — eine Routing-Regel ist ein versionierter, freigabepflichtiger Baustein —, und ADR-037 hat die Voraussetzung dafür geschaffen. Beim Umzug stellte sich heraus, dass die beiden Hälften **nicht dieselbe Art Sache** sind.

**Entscheidung:** Der Schnitt liegt zwischen *Möglichkeit* und *Wahl*.

| | | wo |
|---|---|---|
| **Infrastruktur** | Welche Modelle erreichbar sind und welche Datenklasse jedes sehen darf | `routing-config.yaml` |
| **Governance** | Welches Modell welche Aufgabe bearbeitet | `context_blocks`, als `routing_rule` |

**Das ist dieselbe Trennung, die CHY-42 schon gezogen hat**, an einem anderen Gegenstand: *„`workflow_configs` trägt nur Tuning. Infrastruktur (LiteLLM-URL, Langfuse-Schlüssel, `POSTGRES_URI`) bleibt in ENV/SOPS: eine Basis-URL, die ein `PUT` ändern kann, ist ein SSRF-Vektor."* Eine Routing-Tabelle, die ein `PUT` ändern kann, ist eine Umgehung der Freigabe.

Und der Einwand „zwei Quellen für eine Wahrheit" löst sich damit auf: Es sind **zwei verschiedene Fragen**. Die eine beantwortet der Betrieb, die andere ein Mensch mit einem Reviewer.

**Folge 1 — Ohne Regel wird nicht geroutet.** Ein unbekannter Task-Typ fiel vorher still auf `default`. Für eine Datei ist das ein vernünftiger Vorgabewert; für eine Governance-Entscheidung ist es der falsche. Die Spur nennt danach ein Modell, und **nichts unterscheidet „jemand hat das gewählt" von „niemand hat"**. Deshalb: keine Regel in der Kette → der Aufruf schlägt fehl und nennt Task-Typ und Geltungsbereich.

**409 und nicht 404.** Der Geltungsbereich existiert, der Name ist gut — es fehlt eine *Entscheidung*. Ein 404 schickte den Leser nach einem Tippfehler suchen.

**Folge 2 — Routing hat einen Geltungsbereich.** `/v1/route` bekommt ihn als Pflichtfeld. Der Endpunkt hatte außer Tests keine Konsumenten, die Vertragsänderung kostete nichts, und sie macht ehrlich, was ohnehin gilt: eine Routing-Entscheidung fällt immer *in* einem Geltungsbereich, weil eine Organisation eine Vorgabe macht und ein Vorhaben sie schärft.

`/v1/complete` verlangt ihn auch auf dem Weg mit ausdrücklichen Block-Kennungen. Dort braucht die *Komposition* keinen Geltungsbereich — die *Modellwahl* schon. Zwei Entscheidungen, nicht eine.

**Folge 3 — Der Ratchet greift ohne eine Zeile neuen Code.** `ratchet.py` prüft keinen Blocktyp. Ein tieferer Geltungsbereich darf eine Routing-Regel verschärfen und nicht lockern, und das ist ADR-017, nicht eine zweite Umsetzung davon. Der Docstring dort sagte bis heute *„There is no routing_rule"* — jetzt gibt es eine.

**Wo was liegt**, folgt den Eigentumsregeln aus ADR-030:

- die **Abfrage** in `contexts/repository` — dort liegt die Tabelle, und eine Tabelle zu besitzen heißt, den Zugriff zu besitzen
- die **Auslegung** in `router/rules.py` — was eine Routing-Regel bedeutet, ist nicht Sache des Repositoriums
- das **Zusammensetzen** im Transport — nur der darf beide kennen

**Der Seed ist keine Migration.** `scripts/seed_routing_rules.py` schreibt die früheren sechs Routen als `draft` in einen benannten Geltungsbereich. Eine Migration träfe Governance-Entscheidungen im Namen dessen, der sie ausführt; diese Zeilen sollen einen Autor haben und eine Freigabe brauchen. Genau dafür haben sie die Datei verlassen.

#### Nachtrag: die Regel begrenzt die Antwortlänge (CHY-166, 2026-09-25)

**Befund.** `core/completion.py` schickte kein `max_tokens`, und `/v1/complete` nahm keins entgegen — wie lang eine Antwort wurde, entschied allein das Modell. Am 25.09. antwortete `phi3-mini` auf dem gedrosselten Raspberry Pi 4 auf „ping" mit 215 Token und brauchte dafür 5 min 4 s. Die API hatte nach 300 s abgebrochen, vier Sekunden vorher. Bei einem Cloud-Modell hieße dasselbe unbegrenzter Verbrauch der Quote (CHY-159).

**Entschieden (Thomas):** Die Voreinstellung lebt **in der Routing-Regel**. Eine Längengrenze entscheidet über Kosten und Wartezeit — dieselbe Art Entscheidung wie die Modellwahl, und die Regel geht ohnehin durch die Freigabe.

1. **`max_tokens` ist Pflicht** in `metadata`, eine positive Ganzzahl. Geprüft beim Lesen und bei der Freigabe, wie `check_chain` — eine Regel ohne Grenze ist genau der Zustand, den der Nachtrag beendet. Kostete heute nichts: außer Wegwerfregeln des E2E gab es in keiner Umgebung eine Regel
2. **Die Regel ist Voreinstellung und Obergrenze.** Ein Aufrufer darf weniger verlangen (eine Klassifikation braucht keine 1024 Token), nie mehr — das wäre ein Weg an einer freigegebenen Entscheidung vorbei. Mehr verlangt → **422**, bevor ein Modell gefragt wird. Abgewiesen statt gekürzt: eine still gekürzte Antwort sähe aus wie ein Modell, das früh aufgehört hat
3. **Auch `/v1/route` nennt die Grenze.** Der Endpunkt gibt die Gateway-Adresse heraus, damit ein Aufrufer das Modell selbst fragt — ohne die Grenze wäre er der eine Weg daran vorbei
4. **Der Workflow-Runner** reicht die Grenze der Regel durch; ein Knoten hat kein eigenes Feld dafür
5. **Der Seed schlägt je Route eine Grenze vor** (1024, für `audio_script` 2048). Übernommen werden konnte keine — die YAML-Datei kannte keine. Die Entwürfe brauchen weiter eine Freigabe, und dort wird die Zahl geprüft

**Nebenbei behoben, weil die Pflicht es sonst jede alte Regelvorlage hätte treffen lassen:** Eine unbrauchbare Regel bei der Freigabe — fehlende Grenze oder absteigende Kette — war eine **500**. Derselbe Parser lief beim Lesen und beim Freigeben, und beim Lesen ist eine kaputte aktive Regel tatsächlich ein Serverproblem. Bei der Freigabe gehört der Fehler dem, der freigibt: jetzt `RoutingRuleNotApprovable` → **422**.

**Belegt am echten Weg:** Generalprobe auf dem langsamsten lokalen Modellrechner (Raspberry Pi 4, gedrosselt) — 44/44, der Modellaufruf erzeugte genau 16 Token und dauerte 1 min 10 s statt 5 min 4 s. Fünf Gegenproben (Durchreichung, Obergrenze, Freigabe-422, Pflichtfeld, Runner) machen je Tests rot.

### ADR-039: Der Zaun ist der Mechanismus, die Musterliste ist der Alarm (neu in v2.26)

**Status:** Entschieden (2026-09-07, CHY-135)

> **Umsetzungsstand (31.10.2026):** Aufgerufen wird die Prüfung nur im Banking-Piloten. Der Workflow-Runner und `/v1/complete` rufen `core/ingress.py` nicht auf; ein Dokumenteingang für Workflow-Läufe ist CHY-174 (offen).

**Kontext:** `node_ingest` im Banking-Piloten las ein hochgeladenes PDF und schickte die ersten 3.000 Zeichen an ein Modell. Ein Dokument mit einer eingebetteten Anweisung — weiße Schrift, Kommentarfeld, Metadaten — erreichte es ungeprüft. Eine Suche nach `sanitiz|injection|jailbreak|guardrail` fand im Quellbaum **sechs Treffer, alle FastAPIs `dependency injection`**.

**Die naheliegende Abwehr trägt nicht.** Dem Modell zu sagen, es solle Anweisungen im Inhalt ignorieren, greift auf **derselben Ebene** an wie der Angriff: Für ein Sprachmodell sind Daten und Anweisungen dieselben Token, und der Angreifer schreibt den letzten Satz.

**Entscheidung:** Zwei Hälften, und nur eine ist eine Garantie.

**1 — Der Zaun.** Fremder Text steht zwischen Markern, und der Prompt sagt, dass dazwischen Material liegt und keine Anweisung. Das allein ist eine Hoffnung — ein Modell kann dem Inhalt trotzdem folgen. Es wird zu einer belastbaren Aussage in Verbindung mit dem zweiten Teil: **Aus einem Zaun auszubrechen heißt, ihn zu reproduzieren**, und ob die Bytes des Markers im Inhalt stehen, ist keine Heuristik. Der Ausbruch ist damit *feststellbar*, auch wenn er nicht verhindert wird.

**2 — Die Musterliste.** Sie erhöht die Kosten des unsorgfältigen Angriffs und hinterlässt vor allem einen **Nachweis**. Eine Aufsicht, die fragt „ist etwas Verdächtiges hereingekommen", bekommt eine Antwort statt eines Schulterzuckens. **Der Wert ist Evidenz, nicht Verhinderung** — derselbe Zuschnitt wie die Retrieval-Herkunft in ADR-036.

**Was die Liste brauchbar statt lästig macht: Phrasen, keine Wörter.** Der Pilot liest deutsche Bankdokumente. Dort ist „Anweisung" eine **Zahlungsanweisung**, „System" ein IT-System und „Anleitung" ein Handbuch. Eine Wortliste feuerte bei jedem zweiten Dokument und brächte allen bei, den Befund zu ignorieren — **was schlechter ist als kein Befund**. Jeder Eintrag muss ein Verb an den Leser *und* ein Objekt enthalten, das nur Sinn ergibt, wenn der Leser ein Modell ist.

**Die Grenze steht im Modul und als Test.** Es erkennt Muster, keine Absichten; wer die Liste kennt, schreibt darum herum. Ein Befund ist ein Grund hinzusehen, nie ein Beweis — und **kein Befund ist kein Beweis für Unbedenklichkeit**. Ein Test behauptet ausdrücklich, dass ein umformulierter Angriff durchkommt: Ein Test, der das Gegenteil zusicherte, wäre die stärkste Falschaussage im Repositorium.

**Wo es liegt.** Die Erkennung sitzt **unterhalb des Transports** (`core/ingress.py`), damit sie der Runner ebenso erreicht wie der Pilot und ein späterer Dokumentenendpunkt. Der Pilot bekommt einen **Aufruf, keinen Umbau** — ADR-028 Punkt 4 hält Study und Banking aus Umbauten heraus, und der ausnutzbare Pfad liegt ausgerechnet dort.

**`scan()` wirft nicht und filtert nicht.** Der Befund ist Daten, die Richtlinie gehört dem Aufrufer: Ein Pilot schreibt sie auf, ein Workflow kann sie an ein Freigabe-Gate leiten, ein Batch-Lauf verweigern. Hier zu entscheiden machte die Richtlinie des strengsten Aufrufers zu der aller.

**Die Zeile steht bei jedem Lauf im Protokoll**, auch ohne Befund. „Geprüft, nichts gefunden" und „nie geprüft" sind verschiedene Antworten, und nur eine davon lässt sich hinterher noch geben.

### ADR-040: Was einen Knoten steuert, bekommt eine Spalte (neu in v2.27)

**Status:** Entschieden (2026-09-07, CHY-134)

**Kontext:** `workflow_nodes.config` ist ein JSONB ohne CHECK, und fünf Schlüssel werden daraus gelesen: `model`, `prompt`, `system`, `task_type`, `block_types`. Zwei davon scheitern laut, wenn sie fehlen — drei schweigen. Ein Tippfehler in `block_types` komponiert alles statt einer Auswahl, ein fehlendes `system` wird zum leeren String, ein falsches `task_type` fällt auf den Knotentyp zurück. Jedes Mal antwortet der Lauf mit Erfolg.

Dazu kam der Anlass: Das Whitepaper führt seit v2.x ein typisiertes Feld `context_requirements` für `context_compose`. Es gab keine Spalte, keinen Code und keinen Contract-Eintrag — dieselbe Abweichung zwischen Modell und Schema wie `condition TEXT` vor CHY-130, und sie blieb genau so lange unsichtbar, wie sie niemand auswertete.

**Entscheidung:** Was das Verhalten eines Knotens bestimmt, bekommt eine **typisierte Spalte mit Kohärenz-CHECK** — die Bauform, die `workflow_nodes` für `sub_workflow` und `human_review` seit Migration 018 schon hat. `config` bleibt für das, was wirklich nur Parameter ist.

**In Schritten, jeder für sich reviewbar:**

1. **CHY-134 (hier):** `block_types` → Spalte `context_requirements`. Zuerst, weil dieses Feld entscheidet, *welcher Kontext ein Modell erreicht* ✅
2. **CHY-145:** Der Runner benutzt die Routing-Kette statt `config["model"]` — der nie angelegte Schritt 3 aus CHY-87 ✅ (Migration 023: `task_type` wird Spalte, weil er jetzt der Routing-Schlüssel ist; `model` und `task_type` sind in `config` unspeicherbar)
3. **CHY-146:** `prompt` und `system` werden Kontextbausteine. Ein Systemprompt in einer Knotenkonfiguration ist ein Baustein ohne Version, ohne Freigabe und ohne Ratchet

**Das Vokabular steht in SQL, nicht im Modell.** Die Blocktypen gehören dem repository-Kontext, und orchestration darf ihn nicht importieren (ADR-030). Die Datenbank kennt beide Tabellen — und ist der Durchsetzungspunkt, an dem kein Schreibweg vorbeikommt. Dieselbe Begründung, die `ratchet.py` für zwei Prüfstellen gibt: Zeilen erreichen `active` auf Wegen, die nicht über den Freigabe-Endpunkt laufen.

**Die alte Schreibweise wird abgelehnt, nicht ignoriert.** `config["block_types"]` ist seit Migration 022 unspeicherbar. Sie weiter anzunehmen hieße: 201 antworten und etwas anderes komponieren, als der Autor geschrieben hat.

**Die leere Liste ist unspeicherbar.** `[]` ist in Python falsy, und der Store liest einen falschen Filter als „keiner angegeben", also als *alles*. Ein Wert, der das Gegenteil von dem sagt, was er meint, gehört nicht in eine Spalte. „Keine Einschränkung" heißt: gar keine Anforderungen.

**Gegen-Argument, das trägt:** Mehr Spalten sind mehr Migrationen. Angenommen und bezahlt — die Alternative ist ein Feld, dessen Inhalt niemand prüfen kann, und die vier Fundstellen in `config` sind endlich. Wenn ein Knotentyp einmal wirklich freie Parameter braucht, bleibt `config` genau dafür da.

**Folge für die Fehlerbehandlung:** Die CHECKs von `workflow_nodes` kamen bisher als `CheckViolationError` an und verließen die API als **500**. Der Aufrufer erfuhr, der Server sei kaputt, während der Server etwas Bestimmtes verweigert hat. Seit CHY-134 werden sie zu `NodeNotStorable` und zu einem 422 mit dem Satz, den die Regel meint — für alle sieben Constraints der Tabelle.

### ADR-041: Die Datenklasse gehört dem Geltungsbereich (neu in v2.30)

**Status:** Entschieden (2026-09-24, Thomas; umgesetzt in CHY-160)

**Kontext:** Drei Mechanismen schützten die Modellwahl, und keiner verglich die **Klasse der Daten** mit der **Obergrenze des Modells**. `check_chain` prüft, dass eine Kette nicht unter ihren ersten Kandidaten fällt. Der Privacy-Ratchet (ADR-017) prüft die *erklärte* Richtung eines Bausteins, nicht seine Kandidaten. Die Freigabe ist ein Mensch, der hinsieht. `max_privacy_level` in `routing-config.yaml` wurde nur relativ benutzt. Solange jedes Modell `critical` war, ließ sich die Frage nicht stellen; mit Ollama Cloud (CHY-157, `public`) wäre eine freigegebene Regel `[gemma4-cloud, …]` für kritische Daten durch jede maschinelle Prüfung gegangen.

**Erwogen:**

| Woher die Klasse kommt | deckt ab | Einwand |
|---|---|---|
| **Geltungsbereich** ✅ | `/v1/route`, `/v1/complete`, Runner | — |
| Workflow (`risk_classification.privacy_level`) | nur den Runner | `/v1/complete` bliebe offen; zwei Workflows im selben Bereich könnten dieselben Daten verschieden einstufen |
| Routing-Regel selbst | alles | wer die Regel schreibt, stuft sie auch ein — die Prüfung prüfte sich selbst |

**Entscheidung:** Die Klasse ist eine Eigenschaft des **Ortes, an dem die Daten liegen**, nicht des Weges, den sie gerade nehmen.

1. `scope_nodes.privacy_level` (Migration 024). `NULL` heißt „hier nicht erklärt", nicht „frei"
2. **Wirksam ist die strengste Klasse auf der Kette.** Ein Projekt kann nicht lockern, was seine Organisation erklärt hat — dieselbe Richtung wie der Ratchet
3. **Hat niemand etwas erklärt, gilt `critical`.** Vergessen öffnet nichts; eine Cloud-Stufe ist eine Entscheidung, die jemand in einen Bereich schreibt
4. Ein Kindbereich, der lockerer wäre als sein Elternbereich, wird **abgelehnt, nicht gespeichert** — wirksam wäre er nicht, nur irreführend
5. **Jeder Kandidat** muss die Klasse sehen dürfen, nicht nur der erste: Ein Rückfall bekommt dieselbe Anfrage

**Zwei Prüfstellen**, aus dem Grund, den ADR-017 für den Ratchet gibt: bei der **Freigabe** einer Routing-Regel, wo ein Mensch steht und die Korrektur billig ist (eingehängt in die Transition, auf der gesperrten Zeile), und beim **Auflösen**, wo kein Schreibweg vorbeikommt und ein nachträglich strenger eingestufter Bereich greift.

**Das Vokabular der Stufen liegt im Kernel**, der Router importiert es. Scope und Modell werden am selben Maßstab gemessen; zwei Listen wären zwei Maßstäbe.

**Folge für ADR-006 (CHY-39):** Die Frage „dürfen diese Daten in die Cloud?" ist damit strukturell beantwortet, einmal je Bereich und Regel, beide Male auditiert — nicht je Anfrage. Ob es zusätzlich ein menschliches Gate *vor* jedem Cloud-Aufruf braucht, war die offene Frage von CHY-39 — am selben Tag beantwortet in der Präzisierung von ADR-006: kein menschliches Gate je Anfrage.

### ADR-042: Ein Freigabeweg — ADR-011 auf dem Block-Lebenszyklus (neu in v2.33)

**Status:** Entschieden (2026-09-29, Thomas; CHY-65) · **Löst ab:** ADR-005 · **Präzisiert:** ADR-011

**Kontext:** ADR-005 (30.06.) entschied eigene Tabellen `merge_requests` und `merge_request_comments` für „Diffs, Versionen und Review-State". Sie kamen mit Migration 001. Die sieben Routen unter `/v1/merge-requests` antworteten 501, niemand schrieb in die Tabellen — am 29.09. auf allen drei Stufen leer.

In der Zwischenzeit entstand das, wofür ADR-005 Speicher suchte, an `context_blocks` selbst: je Änderung eine Version (CHY-30), `draft → pending_review → active` mit Freigabe und begründeter Ablehnung, von der Datenbank erzwungen (Migration 013, CHY-32), dazu der Privacy-Ratchet (ADR-017, CHY-62). Die Beschreibung von CHY-65 stammte noch vom 31.07.: ein Merge Request mit eigenem Diff, der beim Merge auf die Blöcke angewendet wird.

Das wäre ein **zweiter Weg, einen Block zu ändern** — mit eigener Versionierung, eigenem Freigabestatus und einer eigenen Stelle, an der Blöcke aktiv werden. Zwei Quellen für dieselbe Wahrheit.

**Entscheidung: ein Freigabeweg.** „Merge Request" in ADR-011 heißt ab jetzt: die Freigabe einer Block-Version.

1. **Strenge Scopes** (`org`, `team`, `workspace`) nutzen den vorhandenen Lebenszyklus: Entwurf → Einreichen → Freigabe durch einen Reviewer. Vier Augen soll CHY-163 erzwingen (→ M5, offen; bis dahin kann ein Reviewer seinen eigenen Block freigeben, siehe SECURITY.md)
2. **Direkte Scopes** (`project`, `sub_project`, `personal`) aktivieren sofort: `draft → pending_review → active` in einer Transaktion, der Autor als Freigebender, `direct` im Audit. Die Zustandsmaschine aus 013 bleibt unangetastet — sie verbietet `draft → active` weiterhin, der direkte Weg geht durch `pending_review` hindurch. Ratchet und `before_activation` laufen auf beiden Wegen
3. **Promote** kopiert einen Block aus einem direkten Scope als Entwurf in einen strengen übergeordneten Scope; dort gilt Punkt 1
4. **`merge_requests` und `merge_request_comments` entfallen** (Migration 025), ebenso die sieben Routen und der Kontext `governance`, der als Gerüst für das Merge-Request-Modell angelegt war (CHY-106). ADR-030 hat damit **vier Kontexte** statt fünf

**Verworfen: Merge Request als Bündel über Block-Versionen.** Ein Datensatz, der mehrere Versionen zusammenfasst, gemeinsam freigibt und Kommentare trägt — ohne eigenen Diff, der Unterschied ergibt sich aus den Versionen. Das ist die richtige Form, sobald mehrere Blöcke nur gemeinsam wirken dürfen. Verworfen für jetzt, nicht grundsätzlich: etwa doppelter Aufwand bei knappem Zeitplan, und es gibt noch keinen Fall, der es verlangt. Es lässt sich später auf den Lebenszyklus setzen, ohne ihn zu ändern.

**Verworfen: Merge Request mit eigenem Diff** (CHY-65 vom 31.07.) — siehe Kontext.

**Konsequenzen:**

- (+) Eine einzige Stelle, an der ein Block aktiv wird; Ratchet, Audit und die Zustandsmaschine der Datenbank gelten ohne Ausnahme
- (+) Weniger Code: sieben 501-Routen, zwei Tabellen und ein leerer Kontext weniger. Das Ratchet (CHY-84) fiel dabei um 12 ARG-Befunde, 7 geplante Operationen und 7 Endpunkte, die den Aufrufer verwarfen
- (−) Keine Änderung über mehrere Blöcke als eine Einheit, keine Kommentare je Feld. Eine Ablehnung trägt eine Begründung, eine Diskussion gibt es nicht
- (−) Der Name „Merge Request" weckt Erwartungen aus Git (Bündel, Squash), die die Freigabe einer Version nicht erfüllt. Die Dokumente sprechen deshalb von **Freigabe**
- ADR-031: Die Oberfläche author verliert `merge-requests`
- **ADR-020 (Modellwechsel) sieht einen „Sammel-MR" vor.** Nach dieser Entscheidung heißt das: je betroffenem Block ein Entwurf, jeder mit eigener Freigabe. Sie gemeinsam freizugeben wäre das hier verworfene Bündel — der erste echte Fall, der es verlangen könnte, und damit der Anlass, die Frage neu zu stellen

**Nachtrag (2026-09-29, Umsetzung von Punkt 2):** Zwei Folgen, die beim Bauen sichtbar wurden:

- **Governance-Angaben kommen im direkten Scope bei einer Bearbeitung mit.** Außerhalb von `draft` sind sie gesperrt (Migration 016), und im strengen Scope sind Neufassung und neue Angabe bewusst zwei Schritte, damit ein Reviewer die zweite als Änderung sieht. Ein direkter Scope lässt aber keinen Entwurf stehen. Ohne Ausnahme wäre eine Angabe dort nach der ersten Aktivierung eingefroren. Die Angabe landet auf der neuen Version, solange diese noch Entwurf ist, und der Ratchet prüft sie bei der Aktivierung wie sonst
- **`audit_log.created_at` ist die Zeit der Anweisung, nicht der Transaktion** (Migration 026, `clock_timestamp()`). `now()` gab allen Einträgen einer Transaktion denselben Zeitstempel. Das Audit wird nach `created_at DESC, id DESC` gelesen, also entschied dann die zufällige UUID die Reihenfolge. Der direkte Weg schreibt erstmals mehrere Lebenszyklus-Einträge in eine Transaktion (beim Bearbeiten drei: `block.superseded`, `block.submitted`, `block.approved`). Auf der Wegwerf-Datenbank stand „approved" vor „submitted"

Offen: Im direkten Scope schaltet ein Contributor einen Block live, zurückziehen darf ihn weiterhin nur ein Reviewer. Entschieden am 06.11.2026 (Thomas): Wer in einem direkten Bereich aktivieren darf, darf dort auch zurückziehen und ausdrücklich freigeben oder ablehnen; strenge Bereiche bleiben, wie sie sind (→ M5, CHY-170).

**Zweiter Nachtrag (2026-09-29, Umsetzung von Punkt 3):** Promote ist `POST /v1/blocks/{id}/promote` mit dem Ziel-Scope.

- **Kopiert wird die neueste aktive Version, nicht die aktuelle.** Die aktuelle kann ein Entwurf in Prüfung sein, während die davor die ist, die der Scope komponiert. Befördert wird, was sich im Einsatz bewährt hat. Ohne aktiven Stand gibt es nichts zu befördern (409)
- **Die Kopie ist eine neue Linie**, keine Version der alten, und landet als Entwurf im Ziel-Scope. Das Original bleibt unverändert und aktiv: Das Projekt nutzt seinen Prompt weiter, ob das Team die Kopie annimmt oder nicht
- **Die Governance-Angabe reist mit.** Der Ratchet prüft sie bei der Freigabe gegen die Kette des neuen Scopes. Eine Angabe, die im Projekt zulässig war, kann dort an einer restriktiveren scheitern — so ist es gewollt
- **Die Herkunft steht im Audit, nicht in der Tabelle:** Der erste Eintrag der Kopie ist `block.promoted` mit Linie, Version und Scope des Originals. Eine Spalte dafür hätte eine Migration gekostet, um eine Frage zu beantworten, die das Audit schon beantwortet
- **Abweichung vom Wortlaut von Punkt 3:** Der Ausgangs-Scope muss nicht direkt sein, `team → org` geht genauso. Geprüft wird, dass das Ziel **über** dem Block liegt und **streng** ist (sonst 422). Das allein stellt sicher, dass beförderter Inhalt eine Freigabe durchläuft; eine Einschränkung auf direkte Ausgangs-Scopes hätte dem nichts hinzugefügt
- Zweimal befördern legt zwei Entwürfe an. Wer im Ziel-Scope anlegen darf, regelt CHY-243 (CHY-92 hat nur das Lesen eingeschränkt)

**Dritter Nachtrag (2026-11-05, ADR-045):** Vier Augen sind für Datenfluss-Bausteine erzwungen — Routing-Regeln mit einem Modell unterhalb `critical` und Bausteine mit `privacy_impact = permissive`. Für sie gilt Punkt 2 nicht: Auch im direkten Scope werden sie eingereicht und von jemandem freigegeben, der sie nicht geschrieben hat. Der Satz in Punkt 1 („bis dahin kann ein Reviewer seinen eigenen Block freigeben“) gilt weiter für Prompt-Material.

**Betrifft:** CHY-65, CHY-163 (Vier Augen), CHY-106 (Kontext `governance`), ADR-005, ADR-011, ADR-017, ADR-030, ADR-031

---

### ADR-043: Benchmark auf Ankern — Testfälle im Kontext-Repository, Lauf auf Abruf (neu in v2.37)

**Status:** Entschieden (2026-10-06, Thomas; CHY-67, CHY-68, CHY-64) · **Präzisiert:** ADR-006 · **Entscheidet:** ADR-019

> **Umsetzungsstand (31.10.2026):** Schritt 1 ist gebaut (CHY-67: Anker als Blöcke, Messtabellen, Validatoren — Migration 028, `contexts/learning/`). Schritt 2 und 3 sind offen: Einen Lauf starten (`POST /v1/benchmarks/run`, CLI `cc benchmark run`) gibt es noch nicht (CHY-68), Report und Freigabe antworten 501 (CHY-64). **Zweiter Nachtrag (03.11.2026):** Punkt 6 beschrieb die Referenzinstallation, nicht das Produkt. Zeitplan, Läufe bei Änderungen und Probelauf regelt ADR-044 (→ M6).

**Kontext:** Drei Tickets aus dem Juli beschrieben Teile derselben Maschine: CHY-67 ein Datenmodell mit eigener Tabelle `prompt_benchmarks` für Testfälle, CHY-68 einen nächtlichen Lauf gegen alle Modelle mit Vergleich zum Vortag, CHY-64 Drift, A/B-Messung und Report. ADR-006 hatte schon entschieden, dass Testfälle **CalibrationAnchors** sind, „manuell gepflegt, versioniert im Kontext-Repository"; der Block-Typ `calibration_anchor` existiert seit Migration 001. Eine eigene Tabelle daneben wäre die Doppelung, die ADR-042 bei den Merge Requests aufgelöst hat.

Beim Lesen fiel auf: `calibration_anchor` stand in `COMPOSABLE_BLOCK_TYPES`. Ein aktiver Anker wäre in jeden Prompt seines Bereichs kopiert worden — das Modell hätte die erwartete Antwort gesehen, jede Messung wäre wertlos gewesen. Folgenlos nur, weil am 06.10. auf keiner Stufe ein Anker existierte.

**Entscheidung:**

1. **Testfälle sind Anker-Blöcke.** `content` ist die Gold-Referenz; `metadata` trägt `task_type`, `input`, den eingefrorenen `raw_prompt` (ADR-019) und die Validatoren. Damit gelten Versionierung, Freigabe (ADR-042), Audit und Scope ohne eigenen Mechanismus. Die Auswertung eines Ankers gehört dem Kontext `learning`, die Zeile dem Kontext `repository` — dieselbe Teilung wie `routing_rule` und Router
2. **Anker sind kein Prompt-Material** — wie `routing_rule` (ADR-037)
3. **Nur Messergebnisse bekommen eigene Tabellen** (`benchmark_runs`, `benchmark_measurements`): Sie sind Beobachtungen, kein Inhalt, und werden nicht freigegeben, sondern bewertet
4. **Bewertet wird deterministisch**, ADR-006 Stufe 1 und 2 (Schema, Regex, Länge, Begriffe, ROUGE-L). Der LLM-Judge (Stufe 3) gehört nicht dazu
5. **Ein Ausfall ist kein Messwert.** Nicht erreichbar, von der Datenklasse verweigert (ADR-041) und Fehler werden als solche gespeichert — nie als Wert null, der wie eine Verschlechterung aussähe
6. **Lauf auf Abruf.** CLI `cc benchmark run`, von Hand oder vom Betrieb angestoßen, ohne Zeitplan. Ein Teil der Modelle läuft lokal auf Rechnern, die nicht durchgehend eingeschaltet sind; ein nächtlicher Lauf würde sie kaum messen. Der Zeitplan ist geparkt (CHY-181), bis diese Modelle nachts erreichbar sind
7. **Drei Schritte, drei Tickets:** CHY-67 (Anker, Messtabelle, Validatoren), CHY-68 (Lauf, Drift), CHY-64 (A/B nach ADR-019, Report, Metrik und Alarm). ADR-019 ist damit entschieden

**Verworfen: eigene Tabelle `prompt_benchmarks`** (CHY-67 vom Juli) — zweite Quelle für Testfälle, ohne Freigabe und Versionierung.

**Verworfen: nächtlicher Zeitplan jetzt** — er würde vor allem „nicht erreichbar" messen.

**Konsequenzen:**

- (+) Testfälle durchlaufen dieselbe Freigabe wie Prompts; wer einen Anker ändert, ändert ihn sichtbar
- (+) Ein Testfall kann nicht mehr versehentlich die Antwort in den Prompt tragen
- (−) Ein Anker ist ein Block mit Pflichtfeldern in `metadata`; die Prüfung dafür läuft bei der Freigabe (`before_activation`), nicht im Datenbankschema
- (−) Ohne Zeitplan entsteht Drift-Evidenz nur, wenn jemand misst. Der Frische-Alarm aus CHY-64 macht das sichtbar
- Die GIN-Index-Zeile aus CHY-64 (auf `translation_rules`) entfällt: die Tabelle gibt es nicht; Modell-Stilllegung ist CHY-158

**Nachtrag (2026-10-06, Umsetzung von Schritt 1):**

- **Eine Messung nennt Linie und Version des Ankers selbst**, abgesichert über einen Fremdschlüssel auf `context_blocks (block_id, version)`. Dafür hat `context_blocks` jetzt `UNIQUE (block_id, version)`, eine Annahme, die der Lebenszyklus immer gemacht und nie erklärt hatte (gezählt vor dem Anlegen: keine Dublette auf allen Stufen). Ein Verweis auf die Zeilen-ID hätte jeden Report gezwungen, die Tabelle des Kontexts `repository` zu lesen; der Wächter `test_the_sql_lives_in_one_module` hat genau diesen ersten Entwurf abgewiesen (ADR-030)
- **Ein Ausfall ist kein Messwert, und das prüft die Datenbank:** `score` ist genau dann gesetzt, wenn `outcome = 'measured'`. Messungen sind nur anfügbar (Trigger wie beim Audit, 011)
- **Die gesperrte Zeile einer Freigabe trägt `content`** (`lock_current_block`). Auf dem direkten Weg (ADR-042) bekommt die Prüfung vor der Aktivierung die Zeile aus `RETURNING *`, auf dem strengen fehlte die Spalte — die Freigabe eines Ankers wäre dort mit 500 geendet, und nur ein Test gegen echtes PostgreSQL sieht das
- Validatoren mit unbekanntem Schlüssel werden abgelehnt, nicht übergangen. Regex laufen ohne Zeitgrenze; dass ein Muster einen Lauf nicht aufhalten darf, gehört zu CHY-68

**Zweiter Nachtrag (2026-11-03, Thomas; CHY-181): Punkt 6 und „Verworfen: nächtlicher Zeitplan jetzt“ sind durch ADR-044 abgelöst.**

Beide begründen sich mit Rechnern, die nachts nicht laufen. Das ist eine Eigenschaft der Referenzinstallation, nicht von Cohereny. Der Benchmark läuft nach Zeitplan (einstellbar, Vorgabe nächtlich), bei Änderungen, als Probelauf vor einer Freigabe und auf Abruf; alle Auslöser starten denselben Lauf über dieselben Anker. Punkt 5 trägt den Zeitplan: Ein Modell, das zum geplanten Zeitpunkt nicht erreichbar ist, wird als `unreachable` gespeichert, nie als Wert, der wie eine Verschlechterung aussähe. Die Referenzinstallation misst weiter auf Abruf, solange ihre lokalen Inferenz-Rechner nachts aus sind — dieselbe Trennung von Produkt und Betrieb, die Whitepaper v4.22 für Monitoring und Backup gezogen hat. Alles Weitere, auch was über die Anker hinausgeht, regelt ADR-044.

**Betrifft:** CHY-67, CHY-68, CHY-64, CHY-181, CHY-183, CHY-154 (Prüfsumme, nicht hier), ADR-004, ADR-006, ADR-019, ADR-030, ADR-037, ADR-041, ADR-042, ADR-044

---

### ADR-044: Drift-Erkennung — drei Ursachen, vier Wege, das Urteil fällt ein Mensch (neu in v2.47)

**Status:** Entschieden (2026-11-03, Thomas; CHY-220) · **Ergänzt:** ADR-043 · **Präzisiert:** ADR-036 · **Grenzt ab gegen:** ADR-006

> **Umsetzungsstand (03.11.2026):** nicht gebaut. Gebaut sind Anker, Validatoren und Messtabellen (ADR-043, CHY-67). Läufe mit Wiederholungen und Kontextstand (CHY-68), Zeitplan (CHY-181), Läufe bei Änderungen und Probelauf (CHY-217), Signale aus dem Betrieb (CHY-218) sowie Report, Freigabe und Frische-Alarm (CHY-64) → M6; Messfühler (CHY-219) → M7; vorgeführter Fehlerfall je Ursache (CHY-220) → Bedingung für M8. `nomic-embed-text` steht in `router/routing-config.yaml`, wird aber von nichts benutzt (CHY-141).

**Kontext:** Model Drift ist eines der drei Qualitätsphänomene, gegen die Cohereny antritt (Whitepaper §1.1), und Drift-Monitoring ist die Erwartung, die Aufsicht und Leitlinien quer durch die Branchen formulieren (Whitepaper §5.5); für Kreditinstitute verlangt MaRisk AT 4.3.4 Tz. 5, die Qualität der Modellergebnisse regelmäßig zu analysieren. Daran muss sich die Architektur messen lassen: Woran merkt Cohereny, dass ein Ablauf über Wochen schlechter wird, obwohl Tests und Protokoll unauffällig bleiben? ADR-043 beantwortet das nur zum Teil:

- **Punkt 6 lässt den Benchmark nur auf Abruf laufen**, begründet mit Rechnern, die nachts aus sind. Das ist eine Eigenschaft der Referenzinstallation, nicht des Produkts. Ein Ablauf, der fehlerfrei läuft und vollständig protokolliert ist, gibt niemandem Anlass, einen Lauf zu starten — auch dann nicht, wenn er leise schlechter wird
- **Ein Lauf weiß nicht, was sich geändert hat.** Er sieht, dass ein Wert gefallen ist, aber nicht, ob ein Anbieter-Update, eine eigene Blockänderung oder eine neue Routing-Regel die Ursache war. Eine Messung nennt bisher nicht einmal den Kontextstand, den sie gemessen hat
- **Eine Messung ist verrauscht.** Dasselbe Modell antwortet auf denselben Prompt nicht immer gleich. Eine Baseline aus einer einzigen Messung mit fester Schwelle meldet Zufall als Drift; jeder Fehlalarm kostet eine menschliche Prüfung, und nach wenigen falschen prüft niemand mehr genau
- **Anker messen feste Eingaben.** Ändert sich, was im Betrieb hereinkommt — andere Dokumenttypen, längere Vorlagen, eine neue Fachsprache —, bleiben die Anker grün, während die echten Ergebnisse schlechter werden. Der Testsatz veraltet, und nichts meldet das
- **Anker messen Aufgaben, nicht den Workflow.** Eine Verschiebung, die erst aus dem Zusammenspiel der Schritte entsteht, sieht kein einzelner Anker

Im August entstand die Idee eines Embedding-basierten Drift-Detektors, der bei jeder Anfrage mitläuft. Sie ist in kein Dokument gelangt und stößt auf zwei Festlegungen:

1. **`contexts/learning/validators.py` ist modellfrei, und das ist der Kern seiner Aussage:** „No model, no database, no clock — the same output scores the same tomorrow, which is the only way a drop in the score can mean the model changed rather than the ruler.“ Ein Embedding-Modell im Score macht das Lineal selbst zu einem Modell, das driften kann
2. **Die Non-Goal-Zeile aus ADR-036 nennt „Einbettungen“.** Geschrieben wurde sie über Retrieval und Speicherung, gelesen wird sie als Verbot jeder Einbettung — dieselbe Verwechslung von Speicherung und Verfahren, die ADR-036 bei RAG aufgelöst hat

**Entscheidung: Drift wird automatisch erkannt und von einem Menschen beurteilt. Cohereny erkennt drei Ursachen auf vier Wegen; jede Meldung endet bei einem Reviewer.**

| Ursache | Was sich ändert | Erkannt durch |
|---|---|---|
| **Modell** | Ein Modell antwortet anders: Anbieter-Update, neue Version, Austausch unter gleichem Namen | Lauf bei geänderter Modellkennung (Weg 1), Nachtlauf (Weg 2) |
| **Kontext** | Eigene Änderungen an Blöcken, Routing-Regeln oder Ankern verschlechtern das Ergebnis | Probelauf vor der Freigabe, Lauf nach der Aktivierung (Weg 1) |
| **Eingaben** | Was im Betrieb hereinkommt, entfernt sich von den Ankern; der Testsatz veraltet | Signale aus dem Betrieb (Weg 3), Messfühler (Weg 4) |

1. **Alle drei Ursachen sind Ziel 1.0.**
2. **Weg 1 — Läufe bei Änderungen.** Gemessen wird, sobald sich nachweislich etwas geändert hat, und nur, was die Änderung betrifft:
   - eine Blockversion wird aktiv → alle Anker, deren Geltungsbereichskette den Bereich enthält (darunterliegende Bereiche erben)
   - eine Routing-Regel wird aktiv (ADR-038) → die Anker der betroffenen `task_type`
   - ein Anker ist neu oder geändert → dieser Anker, als erste Messung seiner Version
   - ein Modell in `router/routing-config.yaml` ist neu oder anders angebunden, oder eine neue Cohereny-Version ändert Komposition oder Validatoren → beim Start erkannt, alle betroffenen Anker
   - die Modellkennung ändert sich: Jede Antwort nennt das Modell, das geantwortet hat, und Cohereny liest diese Angabe schon (`core/completion.py`); bei lokalen Modellen kommt der Digest dazu (CHY-154). Meldet ein Modell einen anderen Stand als bei der letzten Messung, laufen alle Anker gegen dieses Modell

   Ein solcher Lauf nennt seine Ursache: „schlechter seit Freigabe von Block X“ statt „schlechter seit gestern“. Änderungen kurz hintereinander werden zu einem Lauf zusammengefasst; für gehostete Modelle gilt ein Tagesbudget
3. **Probelauf vor der Freigabe.** Wird eine Blockversion in einem strengen Geltungsbereich zur Freigabe eingereicht, laufen die betroffenen Anker mit dieser Version, bevor sie aktiv wird. Der Reviewer sieht das Ergebnis neben der Änderung; freigeben kann er erst, wenn es vorliegt — „nicht erreichbar“ ist ein Ergebnis und steht so im Audit. Geprüft wird das im Haken `before_activation`, durch den jede Aktivierung geht: eine Abfrage, kein Modellaufruf in der Transaktion. Der Probelauf sperrt nicht von selbst: Eine Verschlechterung kann gewollt sein, etwa wenn eine strengere Vorgabe von einer alten Referenz abweicht. Gibt der Reviewer trotzdem frei, steht seine Entscheidung mit der Messung im Audit, und der Lauf nach der Aktivierung verweist darauf, statt dieselbe Verschlechterung ein zweites Mal als neu zu melden. In direkten Geltungsbereichen bleibt es bei der sofortigen Aktivierung (ADR-042, Punkt 2); dort läuft die Messung unmittelbar danach, und spätestens beim Promote in einen strengen Bereich greift der Probelauf
4. **Weg 2 — Nachtlauf nach Zeitplan.** Für das, was kein Ereignis meldet: Ein Anbieter tauscht Gewichte unter gleichem Namen und gleicher Kennung, ändert Quantisierung, Inferenz-Software oder Hardware. Der Zeitplan ist Produktfunktion, einstellbar, Vorgabe nächtlich (CHY-181). Ausgeliefert wird er als Konfiguration für den Betrieb (Timer, Dienst im Compose-Stack, CronJob), nicht als Scheduler im API-Prozess; der Lauf selbst ist derselbe wie auf Abruf (`cc benchmark run`). Ein Modell, das zum geplanten Zeitpunkt nicht erreichbar ist, wird `unreachable` (ADR-043, Punkt 5), nie ein Wert, der wie eine Verschlechterung aussähe. Die Referenzinstallation misst weiter auf Abruf, solange ihre lokalen Inferenz-Rechner nachts aus sind — das gehört zu ihrem Betrieb, nicht zu den Eigenschaften des Produkts
5. **Jeder Lauf nennt seinen Auslöser:** Zeitplan, Änderung (mit Verweis auf sie), Probelauf oder Person. `triggered_by` bleibt die Person, wo es eine gibt; die Art des Auslösers bekommt eine eigene Spalte (neue Migration; 028 bleibt unverändert)
6. **Messrauschen wird beherrscht, nicht übergangen.** Jeder Anker wird je Lauf mehrfach gemessen (Anzahl einstellbar); Temperatur und weitere Sampling-Parameter werden festgelegt, wo das Modell es zulässt, und stehen an der Messung. Die Baseline ist eine Spanne aus mehreren Messungen, kein Einzelwert. Als Verschlechterung gilt ein Ergebnis außerhalb der Spanne, das eine Wiederholung bestätigt. Dafür braucht `benchmark_measurements` eine neue Migration: Heute erlaubt die Tabelle je Lauf, Anker-Version, Modell und Variante genau eine Zeile
7. **Jede Messung nennt ihren Kontextstand.** In der Variante `composed` schreibt jede Messung die `block_versions` des komponierten Kontexts (wie `ComposedContext`) in eine eigene Spalte; bei `raw` bleibt sie leer. Damit trennt ein Report die Ursachen: das Modell (`raw` bewegt sich mit), den Kontext (`composed` bewegt sich, `raw` nicht) und die einzelnen Blöcke, die sich zwischen Baseline und Messung geändert haben (CHY-64)
8. **Weg 3 — Signale aus dem Betrieb, ohne Modell.** Am Gate sieht ein Mensch echte Ausgaben, und jede Entscheidung ist gespeichert (`review_votes`, `approved` oder `rejected`, Migration 027). Steigt die Ablehnungsquote eines Workflow-Schritts in einem Zeitfenster gegenüber einem Referenzfenster, ist das ein Signal aus dem echten Betrieb statt aus dem Testsatz. Dazu laufen die deterministischen Validatoren an echten Ausgaben, wo ein Ausgabeschema existiert (CHY-175), und Verweigerungs-, Fehler- und Längenraten je Schritt werden beobachtet. Quelle sind Workflow-Läufe (`node_executions`); einzelne Aufrufe von `/v1/complete` stehen nur im Trace. Ein Signal erzeugt einen Audit-Eintrag (`drift.signal`) und einen Hinweis an die Reviewer. Kein Modell ist beteiligt — das Lineal driftet nicht
9. **Weg 4 — Messfühler mit Einbettungen (M7).** Seine Hauptaufgabe ist, den Testsatz zu überwachen, nicht das Modell:
   - **Kein Score.** Ein Embedding-Abstand geht nie in `benchmark_measurements.score` und nie in den Mittelwert der Validatoren; `validators.py` bleibt modellfrei
   - **Eingabeverschiebung gegen die Anker:** Je `task_type` werden Eingaben echter Workflow-Läufe eingebettet und mit den Eingaben der aktiven Anker desselben `task_type` verglichen. Entfernt sich der Betrieb von den Ankern, entstehen ein Audit-Eintrag (`drift.input_shift`) und ein Hinweis an die Reviewer: Anker prüfen, ergänzen oder ersetzen
   - **Ausgabeverschiebung je Schritt:** Je `node_ref` werden Ausgaben gegen ein Referenzfenster verglichen (`drift.output_shift`). Das kann einen zusätzlichen Ankerlauf auslösen, ersetzt aber keinen geplanten
   - **Gepinnt:** Modellkennung und Version stehen an jedem Signal. Wechselt das Embedding-Modell, beginnt ein neues Referenzfenster; über einen Wechsel hinweg wird nicht verglichen. Sonst misst der Fühler nach einem Update seine eigene Drift
   - **Kein Vektorspeicher:** Gespeichert werden je Zeitfenster, `task_type` und `node_ref` Kennzahlen — Schwerpunkt, Streuung, Abstand zur Referenz und zu den Ankern —, keine Vektoren je Anfrage. Die Einbettungen der Anker-Eingaben werden je Anker-Version und Modellversion berechnet, nicht als Bestand gehalten. So entsteht kein Bestand an Ableitungen von Payload-Daten (Whitepaper §4.2), aus dem sich Inhalte annähernd zurückgewinnen ließen
   - **Außerhalb des Antwortpfads:** Die Einbettung läuft nachgelagert und darf keine Antwort verzögern oder verhindern. Die Stichprobe je `task_type` ist einstellbar; „jede Anfrage“ ist die Obergrenze, nicht die Pflicht
   - **Dieselben Regeln wie jedes Modell:** Aufruf über LiteLLM, Datenklasse des Geltungsbereichs (ADR-041). Mitgeliefert ist ein lokales Modell mit Obergrenze `critical`
10. **Jede Meldung endet bei einem Menschen.** Eine Regression im Lauf setzt `review_needed` am Lauf; ein Signal aus Weg 3 oder 4 erzeugt einen Hinweis an die Reviewer. Nichts wird automatisch zurückgerollt, umgeschaltet oder freigegeben (ADR-004). Ein Reviewer bestätigt oder verwirft, und seine Entscheidung steht im Audit (CHY-64)
11. **Die Erkennung wird selbst überwacht.** Bleiben Läufe eines Modells oder Signale einer Quelle länger aus als erwartet, schlägt der Frische-Alarm an (CHY-64). Ein grüner Status ist kein Nachweis, eine Messung schon
12. **Wasserdicht heißt vorgeführt.** Für jede Ursache gibt es einen vorgeführten Fehlerfall, den Cohereny meldet, und eine Gegenprobe, bei der es schweigt:
    - Modell: ein schlechteres Modell unter demselben Namen → der Nachtlauf meldet; eine geänderte Kennung → der sofortige Lauf meldet
    - Kontext: eine verschlechterte Blockversion → der Probelauf zeigt es vor der Freigabe; nach der Aktivierung `review_needed` mit Ursache
    - Eingaben: verschobene Eingaben bei grünen Ankern → Messfühler und Ablehnungsquote melden
    - Gegenprobe: Ein unverändertes System bleibt über mehrere Läufe still; die Fehlalarmrate ist gemessen und steht im Report

    Das ist Bedingung für M8 (CHY-220)
13. **Eigentum:** Läufe, Signale und Messfühler gehören dem Kontext `learning`, wie die Validatoren (ADR-030); Messungen und Kennzahlen sind Beobachtungen, nur anfügbar, nicht freigegeben (ADR-043, Punkt 3). Änderungen aus dem Freigabeweg erreichen `learning` über den Kernel (Audit-Einträge) und über den Haken `before_activation`, der in Zone 1 verdrahtet wird wie heute die Prüfung eines Ankers vor der Aktivierung; kein Kontext importiert einen anderen
14. **Die Non-Goal-Zeile aus ADR-036 wird präzisiert, nicht geändert.** Ausgeschlossen bleiben Einbettungen für Retrieval — als Index, für Ähnlichkeitssuche, als gespeicherter Bestand. Der Kasten an ADR-036 und die Zeile „Vektorspeicher und Retrieval-Maschinerie“ im Whitepaper sagen das ausdrücklich

**Verworfen: nur auf Abruf** (ADR-043, Punkt 6, als Produkteigenschaft) — verfehlt genau den Ablauf, der leise schlechter wird.

**Verworfen: nur ein Nachtlauf.** Er findet Modell-Drift, aber nicht ihre Ursache, und eine schlechte Blockversion erst, nachdem sie aktiv war.

**Verworfen: der Probelauf als automatische Sperre.** Eine Verschlechterung gegen eine alte Referenz kann gewollt sein; entscheiden muss der Mensch, der freigibt (ADR-004).

**Verworfen: der Messfühler als Ersatz für den Zeitplan.** „Verschoben“ ist ein schwächeres Signal als „gemessen“; der Fühler ergänzt die Läufe, er ersetzt keinen.

**Verworfen: Embedding-Ähnlichkeit als Validator im Ankerscore** (ADR-006, Stufe 2). Sie würde die Schwäche von ROUGE-L ausgleichen, das nur Wortlaut vergleicht: Eine richtige Umformulierung schneidet schlecht ab, eine falsche Antwort mit ähnlichen Wörtern gut. Der Preis wäre ein Lineal, das selbst driftet. Verworfen für jetzt, nicht grundsätzlich: Wird es gebraucht, dann als eigene Messung mit Version des Embedding-Modells, getrennt vom Mittelwert — nie darin.

**Verworfen: LLM-Judge im Betrieb.** Kosten je Anfrage, und der Richter driftet wie das Gerichtete. ADR-006 hat ihn ans Ende der Pyramide gestellt; im laufenden Verkehr gehört er erst recht nicht hin.

**Verworfen: Vektoren je Anfrage speichern** (pgvector). Würde Abfragen im Nachhinein erlauben, ist aber genau der Vektorspeicher, den die Non-Goal-Zeile ausschließt, und ein neuer Bestand an Payload-Ableitungen ohne Löschkonzept (CHY-17, CHY-63).

**Nicht Teil dieser Entscheidung:** der LoRA-Adapter für Korrekturvorschläge aus derselben Idee vom August. Er gehört zur Adaptionsschicht und zum internen Validierungsmodell (→ M6, CHY-70, CHY-204) und braucht eine eigene Abwägung. Für ihn gilt dieselbe Regel wie für jedes Prüfmodell: Er lernt in Stufen, jeder Stand ist versioniert, freigegeben und selbst gegen die Anker gemessen. Ein Prüfer, der laufend mit dem Geprüften mitlernt, driftet mit ihm.

**Konsequenzen:**

- (+) Eine Verschlechterung kommt mit ihrer Ursache: Modell, Kontext oder einzelner Block
- (+) Drift durch eigenen Kontext wird in strengen Bereichen vor der Freigabe sichtbar, nicht danach
- (+) Es gibt eine Messgröße dafür, ob der Testsatz noch zum Betrieb passt
- (+) Die Validatoren bleiben modellfrei; ein Rückgang im Score heißt weiterhin, dass sich das Modell bewegt hat und nicht das Lineal
- (−) Mehr Läufe, mehr Kosten bei gehosteten Modellen; Bündelung und Tagesbudget begrenzen sie
- (−) Eine Freigabe im strengen Bereich wartet auf den Probelauf, in der Regel Minuten
- (−) **Verschoben heißt nicht schlechter.** Neue Dokumenttypen oder eine geänderte Vorlage lösen Weg 3 und 4 ebenfalls aus. Schwellen müssen je `task_type` kalibriert werden; ein Fehlalarm kostet einen Blick, keinen Stillstand
- (−) Eine Antwort, die gleich klingt und sachlich falsch ist, erkennen nur die Anker (Pflichtbegriffe, Referenz) und der Mensch am Gate
- (−) Ein weiteres Modell im Betrieb (Weg 4), dessen Version gepflegt werden muss; jeder Wechsel setzt das Referenzfenster zurück
- (−) Weg 3 und 4 hängen an Payload-Daten: Aufbewahrungsfristen (CHY-17) und PII-Redaction (CHY-63) gelten für ihre Eingaben mit
- **Meilensteine:** M6 — Läufe mit Wiederholungen und Kontextstand (CHY-68), Zeitplan (CHY-181), Läufe bei Änderungen und Probelauf (CHY-217), Signale aus dem Betrieb (CHY-218), Report, Freigabe und Frische-Alarm (CHY-64); M7 — Messfühler (CHY-219); M8 — vorgeführter Fehlerfall je Ursache (CHY-220)

**Betrifft:** CHY-220, CHY-217, CHY-218, CHY-219, CHY-64, CHY-68, CHY-70, CHY-141, CHY-154, CHY-175, CHY-17, CHY-63, CHY-181, ADR-004, ADR-006, ADR-030, ADR-036, ADR-041, ADR-042, ADR-043

---

### ADR-045: Vier Augen, wo Daten den Ort wechseln — Datenfluss-Bausteine, Einzelbetrieb, technische Konten (neu in v2.53)

**Status:** Entschieden (2026-11-05, Thomas; CHY-163) · **Ergänzt:** ADR-011, ADR-042 · **Baut auf:** ADR-017, ADR-041

> **Umsetzungsstand (05.11.2026):** gebaut, auf dev seit 05.11. (MR !184): `contexts/repository/four_eyes.py`, Migration 029, `block.created` und `block.edited` im Audit (CHY-171). Die Gruppe `chy-service-accounts` der Referenzinstallation folgt in ihrer Infrastruktur (INFRA-165).

**Kontext:** Seit CHY-87 und CHY-160 entscheiden Bausteine darüber, wohin Daten gehen. Routing-Regeln wählen das Modell, die Datenklasse des Geltungsbereichs begrenzt, welche Modelle in Frage kommen (ADR-041). Die Freigabe zeichnete Autor und Freigebenden auf, erzwang aber nicht, dass es zwei Personen sind — eine harte Regel hätte die Freigabe im Einzelbetrieb unmöglich gemacht (CHY-43, ADR-042 Punkt 1).

Beim Umsetzen zeigte sich eine größere Lücke: **In direkten Geltungsbereichen brauchte eine Routing-Regel gar keine Freigabe.** Ein Contributor legte sie an, und sie war sofort aktiv (ADR-042 Punkt 2). Stand der Bereich auf `public`, schickte er damit allein Daten in die Cloud.

Zwei Voraussetzungen fehlten:

- **Wer eine Version geschrieben hat, war nur für den Autor der Linie bekannt.** Weder das Anlegen (`block.created`, definiert und nie geschrieben, CHY-171) noch das Bearbeiten eines Entwurfs hinterließ einen Eintrag. Wer einen fremden Entwurf um ein Cloud-Modell ergänzte und ihn danach freigab, gab seine eigene Änderung frei, ohne dass es auffiel
- **„Nur eine Person darf freigeben“ ließ sich nicht zählen.** Am 05.11. hatte auf der Teststufe nur das CI-Konto `cohereny-ci` ein Freigaberecht. Rollen allein hätten es zum Einzelbetreiber gemacht

Daten verlassen die Installation nur, wenn beides zusammenkommt: eine wirksame Klasse unterhalb `critical` und eine aktive Routing-Regel mit einem Kandidaten unterhalb `critical`. Die Klasse eines Bereichs wird nur beim Anlegen gesetzt (Rolle `architect`) und ist danach nicht änderbar; ohne aktive Regel routet nichts (`NoRouteForTask`).

**Entscheidung: Vier Augen dort, wo Daten den Ort wechseln — in jedem Geltungsbereich.**

1. **Datenfluss-Bausteine** sind eine Routing-Regel, die ein Modell unterhalb `critical` nennt, und ein Baustein mit `privacy_impact = permissive` (ADR-017). Verschärfende Angaben, Prompt-Material und Anker bleiben, wie sie sind
2. **Freigeben darf, wer in dieser Runde nichts geschrieben hat.** Geschrieben hat, wer seit der letzten Freigabe der Linie eine Version angelegt, befördert, neu gefasst oder als Entwurf bearbeitet hat — nicht nur der Autor der Linie. Gelesen wird das aus dem Audit (`block.created`, `block.promoted`, `block.superseded`, `block.edited`). Für Linien aus der Zeit vor CHY-171 zählt der Autor, solange nichts freigegeben wurde
3. **Kein direkter Weg für Datenfluss-Bausteine.** Auch in `project`, `sub_project` und `personal` werden sie geprüft wie zur Aktivierung (Ratchet, `before_activation`) und dann eingereicht; sie kommen als `pending_review` zurück, mit `four_eyes: required` am Eintrag. ADR-042 Punkt 2 gilt für Prompt-Material, nicht für Datenflüsse
4. **Einzelbetrieb wird gezählt, nicht geschaltet.** Darf in der Installation nur eine Person freigeben — Rolle `reviewer` oder höher, nicht anonymisiert, kein technisches Konto —, gibt sie ihren eigenen Datenfluss-Baustein frei. Mit der zweiten Person greift die Regel von selbst
5. **Eine Selbstfreigabe ist an vier Stellen gekennzeichnet:** `four_eyes: self_approved` im Audit-Eintrag der Freigabe, eine Warnung im Log, der Header `Cohereny-Notice` in der Antwort und ein Abschnitt in SECURITY.md. Die Folge steht dort ausdrücklich: Niemand Zweites hat geprüft, ob die Daten die Installation verlassen dürfen; für regulierten Einsatz reicht das nicht
6. **Technische Konten sind markiert** — über die Gruppe `chy-service-accounts` beim Identity Provider, als Merkmal `actors.is_service_account` (Migration 029), bei jeder Anfrage nachgezogen wie die Rolle und bei Änderung auditiert (`actor.service_account_changed`). Sie zählen nicht als Person und geben nie einen Datenfluss-Baustein frei
7. **Kein eigenes Gate für die Einstufung eines Bereichs.** Geprüft wird dort, wo Daten fließen: bei der Freigabe der Regel. Die Einstufung allein bewegt nichts
8. **Die Antwort des Routers wird hereingereicht.** Ob eine Regel ein Modell unterhalb `critical` nennt, weiß nur der Router, und der Kontext `repository` darf ihn nicht importieren (ADR-030). Die Kompositionswurzel übergibt diese Antwort zusammen mit `before_activation` als ein Objekt (`ActivationHooks`)

Jede Freigabe eines Datenfluss-Bausteins trägt im Audit `four_eyes: second_person` oder `self_approved`. Eine abgewiesene Freigabe antwortet `403`.

**Verworfen: vier Augen für jeden Baustein.** Für jeden Systemprompt eine zweite Person macht die Freigabe zum Ritual, und ein Ritual wird durchgeklickt. ADR-011 hat die Freiheit der direkten Bereiche aus genau diesem Grund entschieden.

**Verworfen: ein Schalter für den Einzelbetrieb.** Ein Schalter bleibt stehen, wenn das Team wächst; eine Zählung nicht.

**Verworfen: Rollen zählen, wie sie sind.** CI- und Automatisierungskonten mit Freigaberecht hätten als Personen gezählt. In jeder Installation mit einem solchen Konto wäre der Einzelbetrieb unmöglich gewesen, und wer allein ist, hätte mit dem CI-Konto freigegeben.

**Verworfen: vier Augen beim Anlegen eines Bereichs mit lockerer Klasse.** Ein zweites Gate an einer Stelle, an der keine Daten fließen.

**Verworfen: Datenfluss-Bausteine im direkten Bereich als Entwurf liegen lassen.** Wer dort schreibt, will veröffentlichen; ein Entwurf, der ein eigenes Einreichen braucht, steht in keiner Prüfliste.

**Konsequenzen:**

- (+) Keine Routing-Regel, über die Daten die Installation verlassen, und keine gelockerte Datenschutzregel wird von ihrem Schreiber allein freigegeben, sobald zwei Personen freigeben dürfen
- (+) Die direkte Freigabe für Prompt-Material bleibt, wie ADR-011 sie wollte
- (+) Das Audit weiß für jede Version, wer sie geschrieben hat, und jede Freigabe eines Datenfluss-Bausteins sagt, wie vier Augen erfüllt wurden
- (−) Eine Cloud-Regel im eigenen Projekt wartet auf eine zweite Person
- (−) **Der Einzelbetrieb ist kein Vier-Augen-Prinzip.** Die Kennzeichnung macht ihn sichtbar, nicht sicher
- (−) Ein nicht markiertes CI-Konto mit Freigaberecht zählt als zweite Person, und wer seinen Zugang hat, gibt als es frei. Der Schutz hängt an der Pflege der Gruppe beim Identity Provider
- (−) Vor CHY-171 bearbeitete Entwürfe tragen keine Spur ihrer Bearbeiter; für sie zählt nur der Autor
- (−) Eine beförderte Kopie (`promote`) zählt den Befördernden als Schreiber, nicht die Schreiber der Quelle. Ein Datenfluss-Baustein, der seit dieser Entscheidung im Quellbereich aktiv wurde, hatte dort bereits vier Augen
- (−) **Der Banking-Pilot wählt Modelle noch an den Routing-Regeln vorbei**, über `workflow_configs` und Overrides je Lauf (CHY-42, CHY-196). Für ihn gilt diese Entscheidung nicht; er wird abgelöst (CHY-172, CHY-179)

**Betrifft:** CHY-163, CHY-171, CHY-43, CHY-170, CHY-223, CHY-179, INFRA-165, ADR-011, ADR-017, ADR-030, ADR-041, ADR-042

---

### ADR-046: Aufbewahrung — die Inhalte vergehen, die Spur bleibt; Fristen sind Konfiguration (neu in v2.54)

**Status:** Entschieden (2026-11-05, Thomas; CHY-17)

> **Umsetzungsstand (05.11.2026):** gebaut in MR !187 (Fristen, Admin-API, Aufstellung) und !188 (Löschlauf). In der Referenzinstallation stößt den Löschlauf noch kein Timer an (INFRA-167).

**Kontext:** In Cohereny lief nichts ab. Ein grep nach `retention`, `purge` oder `expires_at` fand nur Cache-TTLs (Kommentar an CHY-17, 08.09.2026), und ein TTL gibt Speicher frei, er erfüllt keine Pflicht. Die Lauf-Tabellen sind die datenreichste Ablage im System und die einzige, die mit jeder Nutzung wächst; in ihnen stehen Ein- und Ausgaben jedes Knotens (§8.3, Payload-Daten).

Zwei Anforderungen ziehen in verschiedene Richtungen. DSGVO Art. 5(1)(e) verlangt, Personendaten nicht länger zu halten als nötig, und Art. 5(2) verlangt, die Regel nachweisen zu können — bebußt wurde die Betreiberin von „Replika“ 2025 unter anderem dafür, dass niemand sagen konnte, wie lange gespeichert wird und wonach sich das richtet. Der AI Act verlangt umgekehrt, dass Betreiber von Hochrisiko-Systemen die Protokolle mindestens sechs Monate aufbewahren (Art. 26(6)). Und die Fristen unterscheiden sich je Einsatz deutlich: Ein Kreditprozess hält länger als eine Lernhilfe.

**Entscheidung:**

1. **Die Inhalte eines Laufs vergehen vor seiner Spur.** `run_content` ist, was hinein- und herausging: Ein- und Ausgaben, Fehlertexte, Begründungen und Kommentare in Freitext. Es wird nach Ablauf geleert. `run_trace` ist die Zeile selbst: wer den Lauf gestartet hat, welches Modell geantwortet hat, wer am Gate wie entschieden hat und wann. Sie wird später gelöscht. Ein geleerter Lauf trägt `content_purged_at` — sonst sähe er aus wie einer mit leerer Eingabe
2. **Fristen sind Konfiguration, nicht Code.** Sie stehen in `retention_policies` (Migration 030), je Aufbewahrungsklasse und Geltungsbereich, gepflegt über die Admin-API, jede Änderung im Audit mit altem und neuem Wert, lesbar ab `reviewer`. Es gilt die **nächstgelegene** Angabe auf der Kette, nicht die strengste wie bei der Datenklasse (ADR-041): Bei Fristen ist weder länger noch kürzer immer strenger. Vorgaben: Inhalte 30 Tage, Spur 183 Tage; `null` hält unbegrenzt
3. **Was es an Daten gibt, ist Code.** `kernel/retention.py` ordnet jede Tabelle einer Aufbewahrungsklasse zu und nennt ihre Inhaltsspalten. Ein Test fällt, sobald eine Tabelle der Migrationen fehlt; der DB-Test gleicht gegen die Datenbank ab. Speicher außerhalb der Tabellen (LangGraph-Checkpoints, Langfuse, Redis, Pilot-Eingabedateien) sind mit aufgeführt, samt der Stelle, die für sie zuständig ist
4. **Eine Frist nehmen vorerst nur die beiden Lauf-Klassen.** Audit-Log, Kontext-Blöcke und Messungen sperren UPDATE und DELETE per Trigger, und solange die Anwendung als Superuser läuft, ist ein Weg daran vorbei CHY-85s Aufgabe. Konten gehen über die Anonymisierung (ADR-016). Die Übersicht nennt je Klasse den Grund, statt eine Frist anzubieten, die nichts löschen kann
5. **Der Löschlauf ist ein Befehl** — `cc retention purge`, angestoßen vom Timer des Betriebs, nicht vom API-Prozess (wie ADR-044 Punkt 4). Eine Transaktion: Der Kernel rechnet die Frist je Geltungsbereich aus, jeder Kontext leert und löscht seine eigenen Tabellen. Gelöscht werden nur abgeschlossene Läufe, von unten nach oben; ein Lauf, dessen Unterlauf länger aufbewahrt wird, bleibt stehen. `--dry-run` nimmt denselben Weg und rollt zurück
6. **Jeder Lauf hinterlässt einen Eintrag** (`retention.purged`), auch wenn nichts ablief. Die Übersicht zeigt den letzten; überwacht wird sein Alter, nicht der Exit-Code — ein stiller Löschlauf ist sonst von einem ausgefallenen nicht zu unterscheiden

**Begriff:** „Aufbewahrungsklasse“, nicht „Datenklasse“ — der Name gehört der Schutzstufe eines Geltungsbereichs (ADR-041).

**Verworfen: Fristen im Code oder in Umgebungsvariablen.** Jede Änderung wäre ein Release oder ein Neustart, die Aufsicht könnte sie nicht lesen, und ein Szenario mit anderen Pflichten bräuchte eine eigene Installation.

**Verworfen: die strengste Frist gilt.** Bei der Datenklasse ist „strenger“ eindeutig. Bei Fristen kann ein Bereich länger aufbewahren müssen als sein Elternbereich (Kreditakte) oder kürzer (Lernpilot); keine Richtung ist sicher.

**Verworfen: ganze Läufe nach einer Frist löschen.** Das hätte den Nachweis der Aufsicht mit den Inhalten zusammen gelöscht — oder die Inhalte so lange gehalten wie den Nachweis.

**Verworfen: ein Scheduler im API-Prozess.** Wann auf einem Host Daten ablaufen, ist Betrieb; ein Prozess, der Anfragen bedient, ist der falsche Ort, und mehrere Instanzen würden sich die Arbeit streitig machen.

**Verworfen: das SQL zur Laufzeit aus der Aufstellung zusammensetzen.** Es hätte dasselbe gesagt, und S608 hätte jedes Statement als zusammengesetzte Abfrage gezählt. Die Stores tragen statische Statements; ein Test bindet jede SET-Klausel an die Aufstellung.

**Konsequenzen:**

- (+) Für jede Tabelle steht fest, wie lange sie bleibt, und die Regel ist zur Laufzeit lesbar — die Rechenschaftspflicht aus Art. 5(2)
- (+) Ein Szenario mit eigenen Pflichten bekommt seine Fristen am eigenen Geltungsbereich
- (+) Der Nachweis der Aufsicht überlebt die Inhalte
- (−) Ohne Timer gilt nichts davon. Die Fristen sind gesetzt, angewandt werden sie erst vom Löschlauf; das Alter von `last_purge_at` muss überwacht werden
- (−) Audit-Log, Blöcke und Messungen haben noch keine Frist (→ M7, CHY-242)
- (−) LangGraph-Checkpoints der Piloten halten den vollen Zustand und laufen nicht ab; sie gehen mit den Piloten (CHY-179). Langfuse-Traces regelt der Betreiber in Langfuse
- (−) `feedback_events` hängt an einer Langfuse-Trace-ID, nicht an einem Lauf; es altert ab seiner eigenen Anlage

**Nachtrag (2026-11-06, CHY-85):** Die Anwendung läuft nicht mehr als Superuser (ADR-009, Nachtrag). Einen Weg an den Triggern vorbei, wie ihn Punkt 4 voraussetzt, hätte jetzt die Eigentümerin der Tabellen, nicht die Anwendung. Ob Audit-Log, Blöcke und Messungen überhaupt eine Frist bekommen, und wenn ja als Löschweg der Eigentümerin, entscheidet CHY-242 (→ M7).

**Betrifft:** CHY-17, CHY-19, CHY-63, CHY-85, CHY-179, CHY-231, CHY-242, INFRA-167, ADR-016, ADR-030, ADR-041, ADR-044

---

### ADR-047: Lese-Isolation für Laufinhalte — die Datenbank setzt durch, wer einen Lauf sieht (neu in v2.58)

**Status:** Entschieden (2026-11-06, Thomas; CHY-92) · **Setzt voraus:** ADR-009 (eigene Datenbankrollen, CHY-85) · **Ergänzt:** ADR-002, ADR-008, ADR-041

> **Umsetzungsstand (06.11.2026):** Für Laufinhalte gebaut (Migration 035, `kernel/acting.py`, `GET /v1/me`). Blöcke, Compose und Route → M6 (CHY-237). Schreiben je Geltungsbereich → CHY-243.

**Kontext:** Jede angemeldete Person las jeden Lauf der Installation: seine Eingabe, die Ausgabe jedes Schritts, jede Begründung am Gate (Codebasis-Audit 14.08.2026, Befunde A4/AR4). Der Bereichsbaum trennte, was Modelle sehen (ADR-041), nicht, was Menschen sehen. Mitgliedschaften (`scope_memberships`) wurden seit CHY-86 gespeichert und wirkten nicht. Eine Klinik hätte in ihre DSFA schreiben müssen, dass jede angemeldete Person die Läufe aller Patientinnen und Patienten sieht. Eine Prüfung im Code hält nur, solange keine Abfrage ohne sie geschrieben wird.

**Entscheidung:**

1. **Lesen wird eingeschränkt, in zwei Schnitten:** zuerst die Laufinhalte (M5), dann Blöcke, Compose und Route mit derselben Auflösung (→ M6, CHY-237)
2. **Einen Workflow-Lauf sehen,** wer ihn gestartet hat, die Mitglieder des Bereichs seines Workflows und jedes Bereichs darüber, und `admin`. Pilot-Läufe haben keinen Bereich; sie sehen der Starter und `admin`. Die globale Rolle bleibt die Obergrenze, die Mitgliedschaft entscheidet, wo sie gilt
3. **Durchgesetzt in der Datenbank, per Row-Level Security** auf jeder Tabelle mit Inhaltsspalten laut `kernel/retention.py`: `workflow_runs`, `node_executions`, `review_votes`, `pilot_runs`, `feedback_events`. Ein Test leitet die Liste aus der Tabellenliste ab; eine neue Inhaltstabelle ohne Policy fällt auf
4. **Für wen eine Abfrage läuft, steht an der Verbindung.** Je Anfrage setzt `kernel/acting.py` den Akteur als Sitzungsvariable (`cohereny.actor_id`) und löscht ihn, bevor die Verbindung in den Pool zurückgeht. Ohne Akteur antworten die Tabellen ohne Zeilen und verweigern jedes Schreiben: Eine vergessene Abfrage bleibt dicht statt offen
5. **Ein ausdrücklicher Systemkontext** (`cohereny.system`) für Arbeit, die die Installation selbst tut: den Runner beim Fortsetzen nach einem Gate, die Pilot-Threads, den Löschlauf. Er hat einen eigenen Pool; welche Module ihn benutzen dürfen, hält ein Test fest. Ob eine Person einen Lauf sehen oder entscheiden darf, prüft ihr eigener Kontext, bevor der Runner übernimmt
6. **Entscheiden setzt Sehen voraus.** Ein Reviewer ohne Mitgliedschaft im Bereich bekommt am Gate „nicht gefunden“, auch mit passender Rolle. Ein fremder Lauf antwortet wie ein fehlender (404), damit seine Existenz nicht durchsickert
7. **`GET /v1/me`** nennt Actor-ID, Rolle und Mitgliedschaften des Aufrufers (Oberfläche `author`). Ohne ihn konnte niemand eine Mitgliedschaft vergeben, denn die API nannte die Actor-ID nirgends

**Verworfen: Lesen bleibt offen** (als ADR festhalten, `scope_memberships` löschen). Vertretbar für eine Person, nicht für ein Produkt, dessen Kernversprechen ein Bereichsbaum mit Governance ist.

**Verworfen: ein Filter je Abfrage im Code.** Die Regel wäre dieselbe, aber jede neue Abfrage müsste an sie denken.

**Verworfen: `SET LOCAL` je Transaktion.** Die meisten Lesezugriffe laufen ohne Transaktion, die Einstellung wäre vor der Abfrage schon weg. Stattdessen gilt die Sitzungsvariable bis zum Zurückgeben und wird dann ausdrücklich gelöscht.

**Konsequenzen:**

- (+) Eine Abfrage, die nicht sagt, für wen, sieht nichts. Die Regel hängt nicht an der Sorgfalt jeder neuen Abfrage
- (+) Ein Mitglied eines Nachbarbereichs liest keinen Lauf und entscheidet keinen; vorgeführt in 14 Integrationstests, jede Ablehnung mit ihrem Gegenstück
- (−) **Keine Abwehr gegen die Zugangsdaten der Anwendung.** Wer sie besitzt, kann jeden Akteur behaupten oder den Systemkontext setzen. Gegen jede Abfrage der Anwendung hält die Regel, gegen ein gestohlenes Passwort so viel wie das Passwort
- (−) Die Eigentümerin der Tabellen (ADR-009) unterliegt den Policies nicht; Migrationen und Wartung sehen alles
- (−) Wer am Gate entscheiden soll und nicht `admin` ist, braucht eine Mitgliedschaft. Eine Installation ohne Mitgliedschaften funktioniert nur für `admin`
- (−) Blöcke bleiben bis CHY-237 für alle Angemeldeten lesbar, und geschrieben wird mit der globalen Rolle in jedem Bereich (CHY-243)

**Betrifft:** CHY-92, CHY-85, CHY-86, CHY-237, CHY-243, CHY-18, ADR-002, ADR-008, ADR-009, ADR-016, ADR-030, ADR-041, ADR-046

---

## 10. Qualitätsanforderungen

### 10.1 Qualitätsbaum

```mermaid
graph TD
    Q[Qualitaet\nCohereny] --> QK[Korrektheit]
    Q --> QS[Sicherheit]
    Q --> QW[Wartbarkeit]
    Q --> QN[Nachvollziehbarkeit]
    Q --> QP[Performance]
    Q --> QD[Datenschutz]
    Q --> QE[Erweiterbarkeit]

    QK --> QK1[Adaption verbessert Output\nmessbar]
    QK --> QK2[Kontext-Komposition\nkonfliktfrei]

    QS --> QS1[Keine Privilege\nEscalation moeglich]
    QS --> QS2[Secrets niemals in\nGit oder Logs]

    QW --> QW1[Neue Modell-Profile ohne\nCode-Aenderung einfuegbar]
    QW --> QW2[Bootstrap-Templates fuer\nneue Teams]

    QN --> QN1[Jede Aenderung\nauditiert und versioniert]
    QN --> QN2[Regressionen erkannt\nund eskaliert]

    QP --> QP1[Cache-Hit unter\n50ms Overhead]
    QP --> QP2[Cache-Miss unter\n200ms zusaetzlich]

    QD --> QD1[Schutzwürdige Daten\nnie in die Cloud]
    QD --> QD2[GDPR-Loeschantrag\ninnerhalb 72h ausfuehrbar]

    QE --> QE1[Neue Modalitaet ohne\nCode-Aenderung integrierbar]

    classDef cloud fill:#1a3a4f,color:#38bdf8
    class Q cloud
```

### 10.2 Qualitätsszenarien

| ID | Qualitätsmerkmal    | Szenario                                                        | Messgröße                                                                            |
| -- | -------------------- | --------------------------------------------------------------- | -------------------------------------------------------------------------------------- |
| Q1 | Korrektheit          | Adaptierter Prompt wird gegen unadaptierten Prompt evaluiert    | Qualitäts-Score > 10% besser (CalibrationAnchor)                                      |
| Q2 | Datenschutz          | Anfrage aus einem Geltungsbereich der Klasse `critical` (oder ohne Klasse) wird geroutet | Kein Aufruf an ein Modell mit niedrigerer Obergrenze; eine Regel, die eines nennt, scheitert schon bei der Freigabe (ADR-041)                                                      |
| Q3 | Wartbarkeit          | Ein erreichbares Modell einer Aufgabe zuweisen                  | Kein Code-Deploy nötig; Routing-Regel als Block, Freigabe per API (ADR-038). Ein neues Modell braucht eine neue Fassung von `router/routing-config.yaml` |
| Q4 | Nachvollziehbarkeit  | Revision eines Kontext-Blocks zurückverfolgen                  | Vollständige Version-History inkl. Actor-Ref abrufbar                                 |
| Q5 | Performance          | Häufig genutzter Step-Kontext                                  | Cache-Hit: < 50ms Overhead; Cache-Miss: < 200ms                                        |
| Q6 | Sicherheit           | Token mit falscher Rolle versucht Freigabe                      | `403 Forbidden`; kein Audit-Eintrag als Freigabe                                     |
| Q7 | Human Oversight      | Benchmark erkennt Regression (📋 → M6)                           | `review_needed` am Lauf; kein Rollout bis zur menschlichen Freigabe (CHY-64)          |
| Q8 | Wirksamkeitsnachweis | A/B-Benchmark (ADR-019) misst Composed Context vs. rohen Prompt | `adaptation_gain` ≥ +10 % über der Anchor-Teilmenge                                |
| Q9 | Erweiterbarkeit      | Neues Vision-Modell integrieren (📋, setzt Adaption und Multimodalität voraus: M6, nach 1.0) | Modell in `router/routing-config.yaml` (ADR-038); Anpassung über Modell-Profil und Übersetzungsregel im Repository, ohne Code-Deploy |

---

## 11. Risiken und technische Schulden

### 11.1 Risiken

| Risiko                                                       | Wahrscheinlichkeit   | Auswirkung | Mitigierung                                                        |
| ------------------------------------------------------------ | -------------------- | ---------- | ------------------------------------------------------------------ |
| Cold-Start-Problem: leeres Repository nützt wenig           | Hoch (unvermeidlich) | Mittel     | Bootstrap-Templates; manuelle Starter-Regeln                       |
| CalibrationAnchor-Pflege: wer hält Anchors aktuell?         | Mittel               | Hoch       | Template-Governance; Community-Review                              |
| Kontext-Komposition zu langsam für Realtime                 | Mittel               | Hoch       | Redis Cache; Cache-Warming für bekannte Workflows                 |
| LiteLLM als Single Point of Failure                          | Niedrig              | Hoch       | Keine im Produkt: Die Fallback-Kette hilft nur, wenn das Gateway antwortet (ADR-035). Überwachung und Ausfallsicherheit des Gateways liegen beim Betreiber                                         |
| OIDC-Provider-Ausfall sperrt alle Nutzer aus                 | Niedrig              | Kritisch   | Keine Lösung — bewusste Entscheidung (kein Fallback auf unauth.) |
| Qualitätsmessung subjektiv / Benchmark nicht repräsentativ | Mittel               | Mittel     | Mehrere Evaluierungsmethoden; Human Review                         |

### 11.2 Technische Schulden

Sicherheitsrelevante offene Punkte stehen auch in SECURITY.md unter „Known limitations“; hier mit dem Meilenstein, der sie schließt.

| Schuld                            | Begründung                                                                                     | Geplante Lösung                                                                                                            |
| --------------------------------- | ----------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------- |
| ~~MR-Backend (ADR-005)~~         | ✅ Entschieden 2026-06-30 (Option B: PostgreSQL) — überholt durch ADR-042 (29.09.)              | erledigt                                                                                                                    |
| ~~Benchmark-Baseline (ADR-006)~~ | ✅ Entschieden 2026-06-30 (Validator-Pyramide)                                                  | erledigt                                                                                                                    |
| ~~Cache-Invalidierung~~          | ✅ Entschieden via ADR-010 (Generation-Counter, v2.0)                                           | erledigt                                                                                                                    |
| Template-Governance               | Wer pflegt Bootstrap-Templates?                                                                 | Empfehlung (Whitepaper §12): zunächst Admin-only (Templates selbst kommen nach 1.0, CHY-209), danach die Rolle `reviewer` über die strenge Freigabe im org-Scope (ADR-042) — kein neuer Rollentyp  |
| Multi-Language Kontext            | DE/EN verschiedene Regeln pro Modell?                                                           | Empfehlung (Whitepaper §12):`lang`-Feld/Tag am Block + Filter in `context_requirements`; keine getrennten Repositories |
| ~~SSE Write-/Replay-Logik~~ | ✅ Umgesetzt (CHY-58): Ereignisse am Laufende in `events_json`, Wiedergabe beim Abruf für Läufe, die der Prozess nicht live kennt | erledigt |
| ~~ltree-Encoding (ADR-008)~~     | ✅ Entschieden 2026-07-11:`org_uuid.team_uuid.project_uuid`, `node_type` als separates Feld | erledigt — ADR-008 aktualisiert                                                                                            |
| Model-Sunset                      | Modell-Lifecycle-Ende nicht modelliert                                                          | ADR-020 umsetzen → M6 (CHY-158)                                                                                                            |
| Autorisierung je Geltungsbereich  | Laufinhalte isoliert (ADR-047); Blöcke liest jeder Angemeldete, geschrieben wird mit der installationsweiten Rolle in jedem Bereich | Blöcke → M6 (CHY-237), Schreiben → CHY-243 |
| Vier Augen für Prompt-Material | Erzwungen bei Datenfluss-Bausteinen (ADR-045); bei anderem Material werden Autor und Freigebende aufgezeichnet, aber nicht verglichen | bewusst so (ADR-045, verworfen: vier Augen für jeden Baustein) |
| ~~Eine Datenbankrolle als Superuser~~ | ✅ Eigene Rollen je Datenbank (CHY-85, ADR-009): Die Anwendung ist weder Superuser noch Eigentümerin und startet nicht mit mehr Rechten. Die Eigentümerin kann Trigger weiterhin abschalten | erledigt |
| ~~`python-jose`~~                 | ✅ Ersetzt durch PyJWT (CHY-202): Audience in der Dekodierung, `exp`/`iss`/`aud`/`sub` Pflicht; Token-Prüfung getestet (CHY-93) | erledigt |
| ~~`DEV_MODE`~~                    | ✅ Entfernt (CHY-192): jede Stufe prüft Tokens, Entwicklung und CI nutzen den lokalen Entwickler-Issuer | erledigt |
| Tag-Filter der Komposition        | `required_tags`/`exclude_tags` stehen im Modell, nicht im Code | nach 1.0 (CHY-147) |

### 11.3 Compliance-Risiken

> Erfasst Juli 2026, fortgeschrieben am 01.11.2026 | Die Maßnahmen sind als Tickets im internen Tracker erfasst; der Meilenstein steht dabei

| Risiko                                                                                               | Wahrscheinlichkeit                                   | Auswirkung                                     | Mitigierung                                                                          | Issue |
| ---------------------------------------------------------------------------------------------------- | ---------------------------------------------------- | ---------------------------------------------- | ------------------------------------------------------------------------------------ | ----- |
| DPIA nicht durchgeführt vor einem Einsatz mit Patientendaten (Arztbrief-Szenario)                   | Hoch (bei einem Einsatz des Arztbrief-Szenarios)     | Kritisch — DSGVO Art. 83: bis 20 Mio € Buße | Pilot ausschließlich mit synthetischen/anonymisierten Daten bis DPIA abgeschlossen; Grundlage für die DSFA des Betreibers in `DATA-PROTECTION.md`, Muster-DSFA in `DATA-PROTECTION-EXAMPLE.md` | CHY-18 ✅ |
| Keine at-rest-Verschlüsselung — Traces und Audit-Logs liegen bei Datenleck im Klartext             | Mittel                                               | Hoch                                           | Verschlüsselte Volumes und Backups beim Betreiber, gefordert im Kryptokonzept (`CRYPTOGRAPHY.md`, ADR-014) | CHY-15 ✅, Referenzinstallation INFRA-163 |
| CVE-Prüfung meldet nur — pip-audit und Container Scanning laufen in CI, blockieren aber nicht; Container Scanning prüft ein anderes Basis-Image als das Dockerfile | Mittel | Hoch | Ergebnisse blockierend machen; Container Scanning auf das gebaute Image richten | CHY-198 |
| Kein Incident-Reporting-Prozess — CRA-24h-Frist (Hersteller) und DORA-4h-Frist (bei reguliertem Einsatz) würden verfehlt | Mittel                                               | Hoch                                           | Incident-Handling-Konzept und Meldeprozess; die Meldepflicht nach CRA gilt ab der Veröffentlichung | CHY-16 (M7b) |
| SaMD-Einstufung ungeklärt — Einsatz des Arztbrief-Szenarios (§6.7) ohne MDR-Konformität            | Niedrig (solange das Szenario nicht eingesetzt wird)  | Kritisch — CE-Kennzeichnungspflicht           | Entscheidung vor einem Einsatz des Arztbrief-Szenarios                               | CHY-4 (nach 1.0) |
| Personenbezug in Payloads (Prompts/Traces/Results) von Art.-17-Löschung nicht erfasst               | Hoch (sobald echte Personendaten verarbeitet werden) | Hoch                                           | PII-Redaction an der Grenze gebaut (ADR-018): Modell, Gateway und Trace sehen Platzhalter; Fristen für Lauf-Inhalte gebaut (ADR-046); Workflow-Läufe nur mit Platzhaltern gespeichert (CHY-234). Restrisiko: Namen, die niemand nennt, und Klarwerte in Pilotläufen | CHY-63 ✅; CHY-234 ✅; CHY-235, CHY-239 (M7) |
| Art.-50(2)-Kennzeichnung KI-generierter Inhalte fehlt in der Bewertung                              | Mittel                                               | Mittel                                         | Kennzeichnung ergänzen; Hinweis nach Art. 50(1) in der Referenz-UI gebaut, TransparencyRecord nur für Pilotläufe (Workflow-Läufe und `/v1/complete` → M7b, CHY-201) | CHY-118 (M7b) |

---

## 12. Glossar

| Begriff                                         | Definition                                                                                                                                                                                                                                                                                                                                 |
| ----------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **Actor**                                 | Interne Referenz die Personenbezug von Fachdaten entkoppelt. Kann anonymisiert werden ohne Fachhistorie zu beschädigen.                                                                                                                                                                                                                   |
| **Actor-Pattern**                         | Architekturmuster: Personenbezug nie direkt in Fachdaten, sondern über anonymisierbare Actor-Referenz.                                                                                                                                                                                                                                    |
| **ADR**                                   | Architecture Decision Record — dokumentiert eine Architekturentscheidung mit Kontext, Optionen und Konsequenzen.                                                                                                                                                                                                                          |
| **Agentic Workflow**                      | Workflow aus mehreren, sequenziell oder parallel ausgeführten LLM-Schritten, bei dem jeder Schritt ein anderes Modell nutzen kann.                                                                                                                                                                                                        |
| **Append-Only**                           | Schreibstrategie: Freigegebene Inhalte werden nicht geändert, sondern durch eine neue Version ersetzt; alte Versionen bleiben erhalten. Bearbeitbar ist nur ein Entwurf (`draft`), Statuswechsel folgen einer festen Übergangstabelle, das Audit-Log nimmt nur Einfügungen an — durchgesetzt per Trigger (Migrationen 011, 013), auch gegen die Datenbankrolle der Anwendung, nicht gegen die Eigentümerin der Tabellen (ADR-009, CHY-85).                                                                                                                                                                                                                      |
| **Aufbewahrungsklasse**                   | Wie lange etwas bleibt, nach dem, was es ist: `run_content` (Inhalte eines Laufs, werden geleert), `run_trace` (der Lauf selbst, wird gelöscht), dazu Klassen ohne Frist (Audit, Konten, Kontext, Struktur, Messungen). Fristen je Klasse und Geltungsbereich sind Konfiguration (ADR-046). Nicht zu verwechseln mit der Datenklasse eines Geltungsbereichs (ADR-041). |
| **Bootstrap-Template** (📋 nach 1.0, CHY-209) | Vorkuratiertes Starter-Set von Kontext-Blöcken für ein Team oder Projekt (z.B. Healthcare, SoftwareEngineering).                                                                                                                                                                                                                         |
| **CalibrationAnchor**                     | Testfall des Benchmarks, gespeichert als Block vom Typ `calibration_anchor`: `content` ist die Referenzantwort, `metadata` trägt Aufgabentyp, Eingabe, den eingefrorenen unadaptierten Prompt und die Validatoren (ADR-006 Stufe 1 und 2). Versioniert und freigegeben wie jeder Block, aber kein Prompt-Material; Messwerte liegen in `benchmark_measurements` (ADR-043).                                                                                                                                                                                                                         |
| **Composed Context**                      | Das Ergebnis der Kontext-Komposition: eine Zusammenstellung von Blöcken aus Global + Team + Projekt für einen spezifischen Workflow-Step.                                                                                                                                                                                                |
| **Contributor**                           | Nutzerrolle (`contributor`): Legt Kontext-Blöcke an, bearbeitet sie und reicht sie ein. In strengen Scopes (org, team, workspace) wird ein Block erst durch einen Reviewer aktiv, in direkten Scopes (project, sub_project, personal) mit dem Schreiben (ADR-011, ADR-042) — außer einem Datenfluss-Baustein, der auf eine zweite Person wartet (ADR-045).                                                                                                                                                                                                                                                  |
| **Datenfluss-Baustein**                   | Baustein, dessen Freigabe entscheidet, wohin Daten gehen: eine Routing-Regel mit einem Modell unterhalb `critical` oder ein Baustein, der eine Datenschutzregel lockert (`privacy_impact = permissive`). Freigeben darf ihn nur, wer in dieser Runde keine Version geschrieben hat, in jedem Geltungsbereich; im direkten Bereich wird er eingereicht statt aktiv (ADR-045). |
| **Drift-Erkennung** (📋 → M6/M7, ADR-044)  | Automatische Erkennung von Drift aus drei Ursachen — Modell, eigener Kontext, veränderte Eingaben — auf vier Wegen: Läufe bei Änderungen mit Probelauf vor der Freigabe, Nachtlauf nach Zeitplan, Signale aus dem Betrieb, ab M7 ein Messfühler. Jede Meldung beurteilt ein Mensch.                                                       |
| **DSGVO**                                 | Datenschutz-Grundverordnung — EU-Verordnung zum Schutz personenbezogener Daten.                                                                                                                                                                                                                                                           |
| **Einzelbetrieb**                         | Installation, in der nur eine Person freigeben darf (Rolle `reviewer` oder höher, kein technisches Konto). Sie gibt ihre eigenen Datenfluss-Bausteine frei; das ist in Audit, Log und Antwort als Selbstfreigabe gekennzeichnet. Kein Vier-Augen-Prinzip; endet mit der zweiten Person (ADR-045). |
| **Headless**                              | Architekturmuster: Keine Geschäftslogik exklusiv in einem UI; alle Funktionen über API verfügbar.                                                                                                                                                                                                                                       |
| **Human Oversight**                       | Verpflichtende menschliche Freigabe, nicht abschaltbar (ADR-004). Gebaut: Jeder Workflow braucht einen `human_review`-Knoten, kein Pfad von einem `llm_call` endet ohne ihn; Gates mit `four_eyes` verlangen zwei verschiedene Personen. Geplant: Freigabepflicht nach einer im Benchmark erkannten Regression (CHY-64).                                                                                                                                                                                                               |
| **Kontext-Block**                         | Kleinste versionierbare Einheit im Repository. Typen: `system_prompt`, `user_context`, `tool_schema`, `example_io` (Prompt-Material, werden komponiert) sowie `calibration_anchor` und `routing_rule` (kein Prompt-Material, ADR-037, ADR-043).                                                                                                                                                                                                  |
| **Laufzeitrolle**                         | Datenbankrolle, als die sich API, CLI und Löschlauf verbinden (`<db>_runtime`): Sie besitzt keine Tabelle, führt kein DDL aus und darf keinen Trigger abschalten. Die Tabellen besitzt die Rolle der Migrationen (`<db>_migrator`), die Eigentümerin (ADR-009, CHY-85). |
| **LiteLLM**                               | Open-Source Gateway der verschiedene LLM-Provider (OpenAI-API-kompatibel, Anthropic, Ollama etc.) hinter einer einheitlichen API abstrahiert. Stack-Baustein von Cohereny — kein Konkurrent (ADR-012).                                                                                                                              |
| **Merge Request**                         | Früherer Name für die **Freigabe** einer Änderung an einem Kontext-Block. Seit ADR-042 kein eigener Datensatz: freigegeben wird die Block-Version (`pending_review → active`); in direkten Scopes (ADR-011) aktiv ohne Zweitperson.                                                                                                                                                                                                                            |
| **Messfühler** (📋 → M7, ADR-044)          | Einbettungsbasierte Beobachtung des Betriebs: meldet, wenn sich die Eingaben eines Aufgabentyps von den Kalibrierankern entfernen (der Testsatz veraltet) oder die Ausgaben eines Workflow-Schritts von ihrem Referenzfenster. Löst eine Prüfung oder einen Ankerlauf aus, bewertet nicht und geht in keinen Score ein.                   |
| **Model Drift (LLM Drift)**               | *Wenn dasselbe Modell über die Zeit anders antwortet*, weil der Anbieter ein Update, Patch oder Fine-Tuning eingespielt hat. Ungewollt aus Nutzersicht. Cohereny soll dies über den Benchmark gegen Kalibrieranker erkennen (🔧: Anker und Messtabellen stehen, Läufe folgen, ADR-043). Nicht zu verwechseln mit Modell-Varianz (verschiedene Modelle) oder Prompt Sensitivity (gleiche Modell-Version). |
| **Model Drifting**                        | Marketing-Oberbegriff der alle drei LLM-Qualitätsphänomene zusammenfasst: Modell-Varianz, Model Drift und Prompt Sensitivity. In technischen Dokumenten wird die präzise Taxonomie bevorzugt.                                                                                                                                           |
| **Modell-Differenz / Modell-Varianz**     | Das normale und gewollte unterschiedliche Verhalten*verschiedener* Modelle — verschiedene Architekturen und Trainingsdaten führen zu verschiedenen "Persönlichkeiten". Cohereny überbrückt diese Unterschiede durch die Adaptionsschicht.                                                                                     |
| **Reviewer**                              | Nutzerrolle (`reviewer`): Gibt eingereichte Kontext-Blöcke frei, lehnt sie ab oder zieht sie zurück; im strengen Scope Voraussetzung für die Freigabe.                                                                                                                                                                                                                                                                  |
| **Nightly Benchmark**                     | Benchmark über die freigegebenen Kalibrieranker gegen die konfigurierten Modelle, Instrument zur Erkennung von Model Drift (ADR-006, ADR-043). Läuft nach Zeitplan — einstellbar, Vorgabe nächtlich —, bei Änderungen und auf Abruf (ADR-044, → M6, CHY-68, CHY-181, CHY-217). Gebaut sind Anker und Messtabellen (🔧).                                                                                                                                                                                                      |
| **OIDC**                                  | OpenID Connect — Authentifizierungsprotokoll auf Basis von OAuth 2.0, genutzt für SSO.                                                                                                                                                                                                                                                   |
| **RevisionedEntity**                      | Modellbegriff aus §5.6 für versionierte Fachdaten: Version, Autor als Actor-Referenz, Zeitstempel, Vorgänger. Im Code keine Basisklasse, sondern Spalten, in `context_blocks` etwa `block_id`, `version`, `superseded_by_version`, `author_id` und `created_at`.                                                                                                                                                                                                                            |
| **Row-Level Security (RLS)**              | Regel in PostgreSQL, die je Zeile entscheidet, wer sie sieht oder schreibt. Cohereny setzt damit durch, wer einen Lauf sieht (ADR-047): Eine Abfrage, die nicht sagt, für wen, sieht nichts. Hält gegen Fehler der Anwendung, nicht gegen ihre Zugangsdaten. |
| **Scope-Hierarchie**                      | Dynamische Hierarchie via`scope_path` (ADR-008): org, team, workspace, project, sub_project, personal — beliebige Tiefe. Tiefere Scopes erben: Die Komposition fügt die Blöcke aller Vorfahren flach nach tief aneinander und überschreibt nichts (§5.3), bei Routing-Regeln gilt die des tiefsten Scopes; Privacy-Regeln nur verschärfend (Ratchet, ADR-017). „Global → Team → Projekt" ist die didaktische Kurzform.                                                       |
| **Step-spezifischer Kontext**             | Für einen einzelnen Workflow-Step komponierter Kontext — minimal, modell-optimiert, aus der Scope-Hierarchie zusammengestellt.                                                                                                                                                                                                           |
| **Systemkontext**                         | Ausdrücklich benannter Zugang, in dem die Datenbank alle Läufe zeigt, für Arbeit, die die Installation selbst tut: Fortsetzen nach einem Gate, Pilot-Threads, Löschlauf (ADR-047). Welche Module ihn benutzen dürfen, hält ein Test fest. |
| **Probelauf** (📋 → M6, ADR-044)           | Lauf der betroffenen Kalibrieranker mit einer vorgeschlagenen Blockversion, bevor sie in einem strengen Geltungsbereich aktiv wird. Der Reviewer sieht das Ergebnis vor der Freigabe; freigeben kann er erst, wenn es vorliegt.                                                                                                           |
| **Prompt Sensitivity**                    | Eigenschaft eines Modells: minimale Änderungen im Prompt-Wortlaut führen zu anderen Ergebnissen — beim gleichen Modell, gleicher Version. Nicht eliminierbar, aber durch kuratierte Formulierungsbausteine im Kontext-Repository systematisch managebar.                                                                                |
| **Technisches Konto**                     | Konto für CI oder Automatisierung, markiert über die Gruppe `chy-service-accounts` beim Identity Provider (`actors.is_service_account`). Zählt nicht als Person, die freigeben darf, und gibt keinen Datenfluss-Baustein frei (ADR-045). |
| **Übersetzungsregel (Translation Rule)** (📋 → M6) | Regelsatz, der beschreibt wie Prompt-Elemente von Modell A nach Modell B übertragen / adaptiert werden.                                                                                                                                                                                                                                    |

---

## 13. Anhang — Vollständige Regulatorische Einordnung

> **Regulatorik-Update v2.0 (Faktencheck 07/2026) — gilt für alle Untertabellen dieses Anhangs:**
>
> 1. **Digital Omnibus** (VO (EU) 2026/1744, ABl. L vom 24.07.2026, in Kraft seit 27.07.2026): Anhang III Hochrisiko → **02.12.2027**; Anhang I → **02.08.2028**; **Art. 50 unverändert 02.08.2026**; Watermarking-Übergang Bestandssysteme 02.12.2026.
> 2. **AI-Act-Einordnung:** Quelle für „Cohereny wahrscheinlich kein KI-System" sind die Kommissions-Leitlinien **C(2025) 924** (06.02.2025, unverbindlich). **Art.-25-Risiko** ergänzt: Das Gesamtsystem Cohereny+LLM ist ein KI-System — wer es unter eigenem Namen bereitstellt, kann in die Anbieterrolle rutschen.
> 3. **Finanzaufsicht:** BAIT für DORA-Institute seit 17.01.2025 aufgehoben (Vollaufhebung 31.12.2026); **9. MaRisk-Novelle** (RS 06/2026) seit 30.06.2026 in Kraft (AT 7.2 ohne IT-Anforderungen, Modelle in AT 4.3.4); **BaFin-Orientierungshilfe zu IKT-Risiken beim Einsatz von KI (18.12.2025, nicht verpflichtend)** nennt Model Drift + Lebenszyklus inkl. Stilllegung → ADR-020.
> 4. **CRA:** Art.-14-Meldepflichten seit **11.09.2026** (24h/72h/14d, ENISA-Plattform) — gelten für Cohereny als Hersteller (§13.3).
> 5. **BSI:** Übergang zu **Grundschutz++** (erster Leitfaden 01.04.2026; ~985 Anforderungen/19 Praktiken, OSCAL/JSON, Übergang bis ~2029); **C5:2026** (07.04.2026) enthält keine KI-Anforderungen; einschlägig ist der freiwillige Katalog AIC4 (Whitepaper, Änderungen v4.13).
> 6. **NIS2:** NIS2UmsuCG in Kraft seit 06.12.2025; Registrierungsfrist 06.03.2026 abgelaufen — relevant für Betreiber in NIS2-Sektoren.

> **Hinweis:** Dieser Anhang erhebt keinen Anspruch auf Vollständigkeit und ist keine Rechtsberatung. Er dokumentiert den Kenntnisstand von Juli 2026, in Teilen fortgeschrieben bis Oktober 2026, auf Basis öffentlich zugänglicher Regulierungstexte. Je nach Deployment-Kontext und Anwendungsfall können weitere Anforderungen gelten. Die Einzeldokumente mit Artikel-Referenzen werden intern geführt und sind nicht Teil des öffentlichen Repositorys.
>
> **Legende:** ✅ Cohereny liefert die technische Grundlage | ⚠️ Grundlage vorhanden, Lücke im Produkt oder Betreiber muss ergänzen | ❌ Nicht implementiert / organisatorische Aufgabe | **→ M5** Meilenstein, in dem die Lücke geschlossen wird (Whitepaper §10). Das Zeichen zeigt den heutigen Stand. Kein Eintrag bedeutet Zertifizierung oder Konformität.

---

### 13.1 DSGVO (EU 2016/679) + NIS2

**Geltungsbereich:** Voll anwendbar — Cohereny verarbeitet personenbezogene Daten (Actor-Referenzen, Audit Trail, OIDC-Tokens).

| Artikel/Anforderung                                 | Status | Cohereny-Feature                                                     | Lücke                                                                     |
| --------------------------------------------------- | ------ | -------------------------------------------------------------- | -------------------------------------------------------------------------- |
| Art. 5 Grundsätze (Zweckbindung, Datenminimierung) | ⚠️   | Actor-Pattern entkoppelt Personenbezug von Fachdaten ✅        | Keine dokumentierte Zweckbindungs-Policy                                   |
| Art. 9 Besondere Kategorien                         | ⚠️   | Datenklasse je Geltungsbereich; unklassifiziert gilt `critical`; jedes Modell einer Routing-Regel muss die Klasse sehen dürfen (ADR-041) | Ob ein Geltungsbereich besondere Kategorien enthält, entscheidet der Betreiber; Bezeichner werden vor jedem Modellaufruf von `/v1/complete` und in Workflows ersetzt (ADR-018, CHY-63), Namen ohne Liste erst mit NER (→ M7, CHY-235); die Piloten nicht, sie sind abgekündigt (CHY-172) |
| Art. 17 Recht auf Löschung | ⚠️ | Actor-Anonymisierung per `POST /v1/admin/actors/{actor_ref}/anonymize` (Pseudonymisierung, ADR-016, CHY-34) | Payloads (Prompts, Ergebnisse) laufen nach ihrer Frist ab (ADR-046), vom Löschersuchen werden sie nicht erfasst; Modell, Gateway und Trace sehen seit CHY-63 nur Platzhalter, gespeicherte Workflow-Läufe seit CHY-234 ebenso (ADR-018); Löschersuchen je Person in Lauf-Inhalten → M7 (CHY-239); Langfuse-Traces beim Betreiber. Restarbeiten Art. 17 erledigt (CHY-19): Klarnamen nur in `actors`, Freitext der Person läuft mit `run_content` ab |
| Art. 25 Privacy by Design                           | ✅     | Actor-Pattern als Kern-Architekturmuster; Platzhalter vor jedem Modellaufruf von `/v1/complete` und in Workflows als Voreinstellung — die mitgelieferten Muster gelten ohne Zutun, abschalten nur oben im Baum mit vier Augen (ADR-018); Workflow-Läufe nur mit Platzhaltern gespeichert (CHY-234) | —                                                                         |
| Art. 30 VVT                                         | ❌     | —                                                             | Organisatorische Aufgabe des Betreibers; Checkliste nach 1.0 (CHY-10)      |
| Art. 32 Technische Sicherheitsmaßnahmen            | ⚠️     | OIDC Pflicht; Append-only Audit-Log (gegen Fehler der Anwendung und gegen ihre Datenbankrolle, CHY-85); Laufinhalte nur für Berechtigte lesbar (ADR-047); Zuordnung der Platzhalter je Lauf in der Anwendung verschlüsselt (ADR-014, Nachtrag; CHY-234) | TLS terminiert der Reverse Proxy des Betreibers, die Anwendung spricht HTTP — Betrieb nur mit verschlüsselten Verbindungen → M7b (CHY-195); at-rest-Verschlüsselung beim Betreiber, gefordert im Kryptokonzept (`CRYPTOGRAPHY.md`, CHY-15) |
| Art. 35 DPIA                                        | ⚠️     | Grundlage für die DSFA des Betreibers: `DATA-PROTECTION.md` (Gliederung nach Art. 35(7), Maßnahmen mit Testnachweis, Restrisiken) | Die DSFA führt der Verantwortliche durch, **vor einem Einsatz mit Patienten- oder Gesundheitsdaten zwingend**; Muster-DSFA für das Arztbrief-Szenario ✅ (`DATA-PROTECTION-EXAMPLE.md`, CHY-18) |

---

### 13.2 EU AI Act (Regulation 2024/1689)

**Geltungsbereich:** Cohereny selbst wahrscheinlich kein KI-System (regelbasierte Routing-Middleware; Komm.-Leitlinien C(2025) 924). Deployer-Pflichten beim Betreiber; bei Hochrisiko trägt der Betreiber die Pflichten nach Art. 26. **Art.-25-Risiko:** Gesamtsystem Cohereny+LLM ist ein KI-System. Fristen i. d. F. Digital Omnibus: Anhang III ab 02.12.2027, Anhang I ab 02.08.2028, Art. 50 ab 02.08.2026.

| Artikel/Anforderung                                    | Status | Cohereny-Feature                                                                                      | Lücke                                                                                 |
| ------------------------------------------------------ | ------ | ----------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------- |
| Art. 5 Verbotene Praktiken                             | ✅     | Kein Nudging, kein Social Scoring, keine Biometrie                                              | —                                                                                     |
| Art. 9 Risikomanagementsystem                          | ⚠️   | Benchmark als Basis: Anker und Messtabellen gebaut, Läufe → M6 (CHY-68)                        | Kein formales RM-Dokument (nur bei High-Risk-Provider-Status)                          |
| Art. 13 Transparenz / Instructions for Use             | ⚠️   | Langfuse-Tracing mit den verwendeten Bausteinen samt Version; Audit-Log                      | Kein formales "Instructions for Use" nach Art. 13(3) — nach 1.0 (CHY-203); TransparencyRecord für Workflow-Läufe und `/v1/complete` → M7b (CHY-201) |
| Art. 14 Human Oversight                                | ⚠️     | Pflicht-Gate `human_review` in jedem Workflow (Validator, Runner), Vier-Augen-Gates; Abbruch von Workflow-Läufen (CHY-200) und Pilotläufen (`cancel_event`, am Gate CHY-153) | `/v1/complete` liefert Modellantworten ohne Gate → M6 (CHY-199); Freigabe nach einer Benchmark-Regression → M6 (CHY-64) |
| Art. 14(4)(a) Anomalien erkennen                       | 📋     | Drift-Erkennung → `review_needed` (Anker und Messtabellen gebaut; Läufe → M6, Messfühler → M7; ADR-044) | —                                                                                     |
| Art. 26 Deployer-Pflichten                             | ⚠️   | Human Oversight Gate als technische Grundlage ✅                                                | Betreiber muss qualifizierte Oversight-Personen benennen (Art. 26(2))                  |
| Art. 50 Transparenz (Deployer,**ab 02.08.2026**) | ⚠️   | TransparencyRecord für Pilotläufe; KI-Hinweis in der Referenz-UI (Veröffentlichung → M8, CHYUI-11) | TransparencyRecord für Workflow-Läufe und `/v1/complete` → M7b (CHY-201); Kennzeichnung nach Art. 50(2) → M7b (CHY-118) |
| Art. 72 Post-Market Monitoring                         | ⚠️     | Langfuse-Tracing; Drift-Erkennung → M6/M7 (ADR-044)                                            | Kein schriftlicher Monitoring-Plan (Art. 72(3))                                        |
| Art. 73 Incident Reporting                             | ❌     | —                                                                                              | Kein Incident-Meldeprozess (nur bei Provider-Status + High-Risk)                       |
| Art. 71 EU-Datenbank-Registrierung                     | ❌     | —                                                                                              | Nur bei Hochrisiko + Provider-Status                                                   |

---

### 13.3 EU Cyber Resilience Act (CRA, Regulation 2024/2847)

**Geltungsbereich:** Anwendbar. Mit der Veröffentlichung unter AGPLv3 und dem Angebot einer kommerziellen Lizenz behandelt sich Cohereny als Hersteller. Die Herstellerpflichten gelten ab der Veröffentlichung. Vorhanden sind Meldeadresse und Richtlinie zur koordinierten Offenlegung (`SECURITY.md`); ein SBOM entsteht seit 02.11.2026 in jeder Pipeline (CHY-22); in Arbeit (→ M7b) sind ein beschriebener Meldeprozess nach Art. 14 (CHY-16) und der Unterstützungszeitraum (CHY-149). Art.-14-Meldepflichten ab **11.09.2026** (24h Frühwarnung / 72h Meldung / 14 Tage Bericht, ENISA-Plattform; gelten auch für Bestandsprodukte). Volle Anwendung 11.12.2027.

| Anforderung                                   | Status | Cohereny-Feature                             | Lücke                                                           |
| --------------------------------------------- | ------ | -------------------------------------- | ---------------------------------------------------------------- |
| Anhang I Teil I Nr. 2 Buchst. b: Secure by Default | ⚠️ | OIDC Pflicht auf jeder Stufe, kein Schalter zum Abschalten (CHY-192); `ENVIRONMENT` ab Werk `prod`, ein Issuer über http nur unter `ENVIRONMENT=dev`; die Anwendung startet nicht als Superuser, als Eigentümerin der Tabellen oder mit DDL-Rechten (CHY-85) | Voreinstellung von `DATABASE_URL` mit Entwicklungs-Zugangsdaten |
| Anhang I Teil I Nr. 2 Buchst. d: Zugriffsschutz | ⚠️ | OIDC (getestet mit Authentik); Rollen aus dem Gruppen-Claim, Audience je Oberfläche (ADR-031); Laufinhalte nur für Berechtigte lesbar, durchgesetzt per Row-Level Security (CHY-92) | Blöcke liest jeder Angemeldete → M6 (CHY-237); Rollen gelten beim Schreiben installationsweit → CHY-243 |
| Anhang I Teil I Nr. 2 Buchst. c: Sicherheitsupdates | ⚠️ | Korrekturen fließen in die laufende Entwicklungslinie und erscheinen als neuer Stand des Repositorys (`SECURITY.md`) | Keine Release-Zweige, keine Backports; Patch-SLA und Unterstützungszeitraum → M7b (CHY-12, CHY-149) |
| Anhang I Teil I Nr. 2 Buchst. a: Keine bekannten ausnutzbaren Schwachstellen | ⚠️ | Basis-Image `python:3.14-slim`; pip-audit auf die Abhängigkeiten in CI | Prüfungen blockieren nicht; Container Scanning prüft ein anderes Basis-Image als das Dockerfile (CHY-198); kein Digest-Pinning → M7b (CHY-14) |
| Anhang I Teil II Nr. 1: SBOM | ✅ | CycloneDX-SBOM in jeder Pipeline, mit Lieferant je Komponente und gegen das Schema geprüft (`sbom`-Job, `scripts/check_sbom.py`, CHY-22) | Beschreibt die Auflösung zur Pipeline-Zeit; ohne Lock-Datei kann ein späteres Image neuere Releases enthalten → M8 (CHY-211) |
| Anhang I Teil II Nr. 5: Koordinierte Offenlegung | ✅  | `SECURITY.md`, Meldeweg security@cohereny.com | —                                                     |
| Art. 14 Meldepflichten (ab 11.09.2026)        | ❌     | Meldeadresse (`SECURITY.md`)          | Kein beschriebener Meldeprozess für ausgenutzte Schwachstellen und schwere Vorfälle (24h/72h/14d) → M7b (CHY-16) |

---

### 13.4 Finanzregulierung: DORA + MaRisk + PCI-DSS

**Geltungsbereich:** DORA Kapitel II (Art. 5–16) vollständig bei Finanzinstitut-Betreibern. MaRisk AT 4.3.4 für automatisierte Modelle.

#### DORA (EU 2022/2554)

| Artikel/Anforderung            | Status | Cohereny-Feature                                               | Lücke                                              |
| ------------------------------ | ------ | -------------------------------------------------------- | --------------------------------------------------- |
| Art. 9 IAM / Least Privilege   | ⚠️     | OIDC Pflicht; Actor-Pattern mit `idp_sub`-Tracking; Rollen aus dem Gruppen-Claim; Datenbankrolle mit den Rechten, die die Anwendung braucht (CHY-85); Laufinhalte nur für Berechtigte lesbar (ADR-047) | Blöcke liest jeder Angemeldete → M6 (CHY-237); Rollen gelten beim Schreiben installationsweit → CHY-243 |
| Art. 10 Detektion              | ⚠️   | Langfuse-Traces (optional); Infrastruktur-Monitoring beim Betreiber | Kein SIEM; kein automatisches LLM-Anomalie-Alerting |
| Art. 12 Backup                 | ⚠️   | Sache des Betreibers; Zustand liegt in PostgreSQL       | Keine mitgelieferte Backup-Policy                   |
| Art. 17–23 Incident Reporting | ❌     | Langfuse Tracing als Grunddaten                          | Kein Incident-Klassifikations- und Reporting-System; Incident-Handling-Konzept → M7b (CHY-16) |
| Art. 11 Business Continuity    | ❌     | Zustand vollständig in PostgreSQL; Sicherung Sache des Betreibers (siehe Art. 12)                      | Kein formaler BCP; kein RTO/RPO; Betreiber-Checkliste nach 1.0 (CHY-8) |

#### MaRisk AT 4.3.4 (RS 06/2026)

| Anforderung                        | Status | Cohereny-Feature                                       | Lücke                                           |
| ---------------------------------- | ------ | ------------------------------------------------ | ------------------------------------------------ |
| Modell-Governance / Human Override | ⚠️     | Pflicht-Gate `human_review` im Workflow-Runner; Versionierung und Freigabe der Bausteine; Actor-Pattern | Regelmäßige Analyse der Modellergebnisse über Benchmark-Läufe → M6 (CHY-68); `/v1/complete` ohne Gate → M6 (CHY-199) |
| Regelmäßige Validierung          | 📋     | Benchmark nach Zeitplan und bei Änderungen → `review_needed` (→ M6, CHY-68, CHY-181; ADR-044) | Betreiber muss Ergebnisse formal dokumentieren   |
| Pre-Deployment-Bewertung           | ⚠️   | Benchmark als Grundlage (Anker gebaut, Läufe → M6) | Kein formales Pre-Deployment-Assessment-Dokument; Betreiber-Checkliste nach 1.0 (CHY-9) |
| Erklärbarkeit                     | ⚠️   | Je Workflow-Lauf `node_executions` mit Modell und Zeitstempeln; Langfuse-Traces mit Bausteinen samt Version | Keine formale Explainability-Dokumentation       |

**PCI-DSS v4.0.1:** Nur bei Verarbeitung von Karteninhaberdaten (CHD). Aktuell kein CHD im Banking-Pilot (Jahresabschlüsse). Scope-Klärung erforderlich wenn Kontext-Blöcke PANs enthalten können.

---

### 13.5 Öffentlicher Sektor: BSI IT-Grundschutz + EVB-IT

**Geltungsbereich:** BSI-Anforderungen für Behörden-Betreiber. Cohereny liefert technische Grundlagen, Behörde muss Informationsverbund eigenständig definieren.

| Baustein / Anforderung                                    | Status | Cohereny-Feature                                                     | Lücke                                                                           |
| --------------------------------------------------------- | ------ | -------------------------------------------------------------- | -------------------------------------------------------------------------------- |
| ORP.4 IAM                                                 | ⚠️     | OIDC; Rollen aus dem Gruppen-Claim; Actor-Pattern; vier Augen bei Datenfluss-Bausteinen, technische Konten markiert (ADR-045); Laufinhalte nur für Berechtigte lesbar (ADR-047) | Blöcke liest jeder Angemeldete → M6 (CHY-237); Schreiben je Bereich → CHY-243 |
| OPS.1.1.5 Protokollierung                                 | ⚠️     | Audit-Log; Langfuse-Traces (optional); Auflösen der Werte eines Laufs protokolliert (CHY-234) | Kein formales Log-Konzept → M5 (CHY-6); Aufbewahrung der Lauf-Daten konfigurierbar (ADR-046), des Audit-Logs → M7 (CHY-242) |
| APP.3.1 Webanwendungen                                    | ⚠️   | FastAPI + OIDC + Pydantic Input-Validierung ✅                 | Kein WAF; dynamische API-Tests mit Schemathesis in CI, kein Sicherheitsscanner; WAF/DAST → M7b (CHY-7) |
| SYS.1.6 Containerisierung                                 | ⚠️   | Dockerfile auf `python:3.14-slim`; Schnellstart mit Docker Compose (`deploy/quickstart/`, CHY-189) | Kein Image-Signing, kein Digest-Pinning → M7b (CHY-14)                          |
| CON.1 Kryptokonzept                                       | ⚠️     | Kryptokonzept für Betreiber (`CRYPTOGRAPHY.md`, CHY-15): Zuständigkeit, Verfahren, Schlüsselverzeichnis; TLS am Reverse Proxy des Betreibers | at-rest-Verschlüsselung beim Betreiber; nur verschlüsselte Verbindungen → M7b (CHY-195); JWKS bei Schlüsselwechsel, Geheimnisse maskieren → M7b (CHY-232, CHY-233) |
| OPS.1.1.3 Patch Management                                | ⚠️   | Sicherheitskorrekturen in der laufenden Entwicklungslinie (`SECURITY.md`) | CVE-Prüfung (pip-audit, Container Scanning) nur meldend (CHY-198); Patch-SLA → M7b (CHY-12) |
| BSI Kriterienkatalog KI (Juni 2025): Menschliche Aufsicht | ⚠️     | Pflicht-Gate `human_review` im Workflow-Runner; Abbruch von Workflow-Läufen (CHY-200) | `/v1/complete` ohne Gate → M6 (CHY-199) |
| BSI Kriterienkatalog KI: Prompt Injection                 | ⚠️   | Klare Trennung Systemkontext/User-Input ✅                     | Zaun und Musterprüfung (ADR-039) nur im Banking-Piloten verdrahtet; `/v1/complete` und Workflow-Runner prüfen zugelieferten Text nicht → M7b (CHY-5) |
| EVB-IT System (on-prem Integration)                       | ✅     | Self-hosted Stack passt zu EVB-IT System Vertragstyp           | —                                                                               |
| EVB-IT 2026: Open Source als Standard (veröffentlicht 03/2026) | ✅ | Cohereny ist Open Source (AGPLv3)                         | SBOM, das die Muster vorsehen: CycloneDX in jeder Pipeline (CHY-22)                                     |
| BSI C5 (§ 393 SGB V)                                     | n/a    | **Nicht anwendbar** — kein Cloud-Dienst; on-premise | Strategischer Vorteil                                                            |

---

### 13.6 Healthcare: MDR + IEC 62304 + Gematik ePA

**Geltungsbereich:** Nur bei SaMD-Klassifizierung. Die Entscheidung muss vor einem Einsatz des Arztbrief-Szenarios (§6.7) getroffen werden (nach 1.0, CHY-4).

| Anforderung                                                    | Status | Cohereny-Feature                                         | Lücke                                                             |
| -------------------------------------------------------------- | ------ | -------------------------------------------------- | ------------------------------------------------------------------ |
| **SaMD-Entscheidung** (Voraussetzung für MDR/IEC 62304) | ❌     | —                                                 | **Muss zuerst getroffen werden**                             |
| MDR Klassifizierung (Annex VIII)                               | ❌     | —                                                 | Wenn klinische Entscheidungsunterstützung: Klasse IIa möglich    |
| IEC 62304 Clause 8: SCM                                        | ✅     | Git-Versionierung, jede Änderung mit Ticket und Review (README); veröffentlicht werden Stände, nicht die Historie | —                                                                 |
| IEC 62304: SDP (Software Development Plan)                     | ❌     | —                                                 | Kein formaler SDP                                                  |
| IEC 62304: RTM (Requirements Traceability Matrix)              | ❌     | —                                                 | Kein RTM                                                           |
| IEC 62304: SOUP Risk Assessment                                | ❌     | —                                                 | Keine formale SOUP-Risikobeurteilung                               |
| DPIA (DSGVO Art. 35) für Gesundheitsdaten                     | ⚠️     | Grundlage für die DSFA des Betreibers: `DATA-PROTECTION.md` | **Kritisch:** DSFA des Verantwortlichen vor jedem Einsatz mit Patientendaten; Muster-DSFA ✅ (`DATA-PROTECTION-EXAMPLE.md`, CHY-18) |
| BSI C5 (Healthcare Cloud)                                      | n/a    | **Nicht anwendbar** — on-premise Betrieb | Strategischer Vorteil gegenüber Cloud-basierten MedTech-Lösungen |
| Gematik ePA: FHIR R4 REST                                      | ❌     | —                                                 | Zukunfts-Feature; nicht implementiert                              |
| Gematik ePA: VAU (Vertrauenswürdige Ausführungsumgebung)     | ❌     | —                                                 | Nicht implementiert                                                |

---

*Dieses Dokument folgt dem Arc42 Template (arc42.org, Version 8.2). Es ist ein lebendes Dokument und wird mit der Architektur weiterentwickelt.*

*Shape. Adapt. Learn. Repeat.*
