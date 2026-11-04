# Änderungsprotokoll

Was sich von einem Schnappschuss dieses Repositorys zum nächsten geändert hat, geschrieben für
alle, die Cohereny betreiben oder darauf aufbauen. Das Neueste zuerst. In English:
[CHANGELOG.md](CHANGELOG.md).

Cohereny ist ein Pre-Release. Ein Schnappschuss ist ein veröffentlichter Stand des
Entwicklungs-Repositorys, keine versionierte Ausgabe; jeder hat auf GitHub ein Pre-Release, das
nach seinem Datum heißt.

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
