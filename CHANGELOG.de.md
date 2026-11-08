# Änderungsprotokoll

Was sich von einem Schnappschuss dieses Repositorys zum nächsten geändert hat, geschrieben für
alle, die Cohereny betreiben oder darauf aufbauen. Das Neueste zuerst. In English:
[CHANGELOG.md](CHANGELOG.md).

Cohereny ist ein Pre-Release. Ein Schnappschuss ist ein veröffentlichter Stand des
Entwicklungs-Repositorys, keine versionierte Ausgabe; jeder hat auf GitHub ein Pre-Release, das
nach seinem Datum heißt.

## 2026-11-08

### Neu
- Workflow-Läufe werden mit Platzhaltern gespeichert. Bezeichner in der Eingabe eines Laufs, in Ein- und Ausgabe jedes Schritts und in den Begründungen am Gate werden ersetzt, bevor sie gespeichert werden; die Zuordnung zu den Werten ist je Lauf verschlüsselt, mit einem Schlüssel, den die Datenbank nie sieht. Wer den Lauf gestartet hat, und ab `reviewer` jeder, der ihn sieht, kann sich die Werte mit `POST /v1/workflows/runs/{run_id}/resolve` zurückholen, jedes Mal protokolliert. Die API braucht `PII_MAPPING_KEYS` und startet ohne nicht ([CRYPTOGRAPHY.md](CRYPTOGRAPHY.md)). Läufe, die vor diesem Update gestartet wurden, behalten ihre Werte, bis ihre Inhaltsfrist abläuft ([DATA-PROTECTION.md](DATA-PROTECTION.md)).
- Bezeichner werden vor jedem Modellaufruf von `/v1/complete` und in Workflows ersetzt: E-Mail-Adressen, Telefonnummern, IBANs, Steuer-IDs und Krankenversichertennummern, gekennzeichnete Geburtsdaten, die Werte, die ein Aufrufer in `identifiers_to_redact` nennt, und die eigenen Regeln eines Bereichs als geprüfte `redaction_rule`-Blöcke. Modell, Gateway und Trace sehen Platzhalter; `/v1/complete` liefert die Antwort mit den Werten. Einen Namen, den niemand nennt, erkennt Cohereny noch nicht.
- `POST /v1/workflows/runs/{run_id}/cancel` hält einen Lauf an, der noch nicht beendet ist (KI-Verordnung Art. 14(4)(e)) — für die Person, die ihn gestartet hat, und ab `reviewer` für alle, die ihn sehen. Der Lauf endet als `cancelled`, nicht als Ablehnung am Gate.
- Aufbewahrungsfristen für die ganze Installation oder je Bereich: `GET /v1/admin/retention`, `PUT /v1/admin/retention/{retention_class}`. `cc retention purge` leert die Inhalte eines Laufs, wenn ihre Frist abläuft (Vorgabe 30 Tage), und löscht seine Spur später (Vorgabe 183 Tage). Regelmäßig ausführen, sonst wird nichts gelöscht.
- Vier Augen, wo Daten den Ort wechseln: Eine Routing-Regel, die ein Modell unterhalb von `critical` nennt, und ein Block, der eine Datenschutzregel lockert, gibt jemand frei, der an dieser Version nichts geschrieben hat. Konten in der Gruppe `chy-service-accounts` des Identity Providers geben sie nie frei ([SECURITY.md](SECURITY.md)).
- Für Betreiber: [DATA-PROTECTION.md](DATA-PROTECTION.md), was Cohereny zu einer Datenschutz-Folgenabschätzung beiträgt, jede Maßnahme mit dem Test, der fällt, wenn man sie entfernt; ein ausgefülltes Beispiel für eine Klinik ([DATA-PROTECTION-EXAMPLE.de.md](DATA-PROTECTION-EXAMPLE.de.md), englisch: [DATA-PROTECTION-EXAMPLE.md](DATA-PROTECTION-EXAMPLE.md)); und das Kryptokonzept ([CRYPTOGRAPHY.md](CRYPTOGRAPHY.md)).

### Geändert
- Migrationen gehen nur vorwärts. Es gibt keine Rückwärtsmigrationen; der Weg zurück aus einem Update ist das Backup davor ([DEPENDENCIES.md](DEPENDENCIES.md)).
- Die beiden LangGraph-Piloten, `/v1/pilots` und `/v1/questions`, sind abgekündigt. Anders als ein Workflow rufen sie das Modell mit den Werten auf und speichern sie; der API-Vertrag kennzeichnet jede ihrer Operationen als `deprecated`. Sie werden als Workflows auf den Runner umgestellt. Bis dahin gehören Personendaten in Workflows oder `/v1/complete` ([DATA-PROTECTION.md](DATA-PROTECTION.md)).
- Einen Schnellstart aktualisieren `git pull` und `docker compose up`: Jeder Start baut das Image aus dem Quellcode, die Datenbank behält ihre Daten und wird migriert. Bei einem Schnellstart vom 2026-11-04 trägt der Seed `reviewer` als Mitglied von Example Corp ein; ohne das sähe `reviewer` die Läufe nicht mehr (siehe Sicherheit) ([deploy/quickstart](deploy/quickstart/README.md#update)).

### Behoben
- Ein freigegebener Workflow ließ sich auf `draft` zurücksetzen, und sein Graph war dann änderbar, unter den Läufen, die ihn benutzt hatten. Das wird jetzt mit `409` abgewiesen; ein freigegebener Workflow ändert sich über eine neue Version.
- Ein Pilotlauf, der am Gate wartete und abgebrochen wurde, wurde als Ablehnung verbucht und blieb ohne Ergebnis auf `running`. Er endet jetzt als `failed`, mit dem Abbruch als Meldung.

### Sicherheit
- Läufe lesen nur, wer sie gestartet hat, die Mitglieder des Workflow-Bereichs und der Bereiche darüber und `admin`; Row-Level Security setzt das in der Datenbank durch. Bisher las jede angemeldete Person jeden Lauf. Wer Läufe prüft, muss Mitglied der Bereiche sein, in denen sie entstehen (`POST /v1/scopes/{scope_id}/members`), sonst bleiben sie verborgen.
- Die API läuft mit einer eigenen Datenbankrolle, `<database>_runtime`; `<database>_migrator` besitzt die Tabellen und führt die Migrationen aus, und als Superuser oder Eigentümerin startet die API nicht. Bisher konnte sie die Trigger abschalten, die das Audit-Log nur anfügbar halten. Bestehende Installationen legen beide Rollen einmal mit `python -m migrations.roles` an und richten `DATABASE_URL` der API auf die Laufzeitrolle ([DEPENDENCIES.md](DEPENDENCIES.md)).

## 2026-11-04

### Neu
- Ein Schnellstart mit einem Befehl in [deploy/quickstart](deploy/quickstart/): API, Datenbank, Cache, ein lokales Modell (Ollama mit `qwen2.5:1.5b`) und ein Identity Provider für die Entwicklung auf einem Rechner, mit einem durchgearbeiteten Beispiel — Geltungsbereiche, Blöcke, Routing-Regeln und ein Workflow, in dem ein Modell eine Antwort entwirft und ein Mensch sie freigibt. Dazu eine README und fünf Anleitungen (englisch); der Durchgang läuft bei jeder Änderung als Test.
- `GET /v1/workflows/runs/{run_id}/nodes` zeigt, was jeder Knoten eines Laufs erzeugt hat. Wer an einem `human_review`-Gate entscheidet, liest den Entwurf jetzt vorher; bisher entschied er, ohne ihn zu sehen.
- `ROUTING_CONFIG` nennt die Datei, in der die eigenen Modelle stehen (`routing-config.yaml`). Bisher musste man dafür die Datei im Quellbaum ändern.
- Ein `llm_call`-Knoten kann seine System-Nachricht über `input_mapping` bekommen. So erreicht ein Modell der komponierte Kontext zusammen mit der Eingabe des Laufs, wie bei `/v1/complete`.
- `API_DOCS` liefert `/docs`, `/redoc` und `/openapi.json` aus (ab Werk aus). `PILOT_NODE_METRICS` schaltet die Token-Messwerte des Banking-Piloten.
- `scripts/dev_issuer.py`, ein lokaler Ersatz für den Identity Provider in Entwicklung und CI. Er stellt jedem ein Token aus, der fragt; nie dort betreiben, wo echte Daten liegen.

### Geändert
- Tokens prüft PyJWT statt python-jose. `exp`, `iss`, `aud` und `sub` sind Pflicht; ein Token, dem eines davon fehlt, wird abgewiesen.

### Entfernt
- `DEV_MODE`. Steht er noch in einer `.env`, muss er heraus: Die API startet nicht mit einem Schlüssel, den sie nicht kennt. Für die Entwicklung ohne Identity Provider gibt es `scripts/dev_issuer.py` ([DEPENDENCIES.md](DEPENDENCIES.md)).

### Sicherheit
- Kein Schalter schaltet die Authentifizierung mehr ab. Jede Route außer `/v1/health` antwortet ohne Token mit `401`; ein Test geht alle Routen durch.
- Das Oberflächen-Gate weist einen Aufrufer ohne Token-Audience ab, statt ihn durchzulassen.
- Einen Identity Provider über unverschlüsseltes `http://` nimmt die API nur mit `ENVIRONMENT=dev` an.

## 2026-11-03

### Neu
- Jede Änderung erzeugt eine Software-Stückliste (SBOM, CycloneDX), geprüft gegen die erklärten Abhängigkeiten, mit Lieferant je Komponente. Auf Anfrage erhältlich ([SECURITY.md](SECURITY.md)).
- Architekturdokumentation: Drift-Erkennung ist Ziel für 1.0 (ADR-044) — Läufe nach Zeitplan und bei jeder Änderung, Signale aus dem Betrieb, ein Messfühler für veraltete Anker; jede Meldung beurteilt ein Mensch.

### Behoben
- Ein Dateiname mit NUL-Byte wird mit `404` beantwortet, nicht mit `500`.

## 2026-11-01

### Neu
- Erstveröffentlichung: die Middleware, ihre Tests, die API-Verträge je Oberfläche, die Architekturdokumentation (deutsch) und die doppelte Lizenz (AGPL-3.0-only oder kommerziell).
