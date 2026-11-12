# Cohereny — Coding-Konventionen

> **Stand:** 07.11.2026 · Gilt für `cohereny` und `cohereny-ui`
> Architektur: [`cohereny-arc42.md`](https://github.com/cohereny/cohereny/blob/main/docs/cohereny-arc42.md) §5.7, ADR-030, ADR-031
> CI-Jobs wie `quality` und `integration` laufen in der Pipeline des selbst betriebenen GitLab;
> der öffentliche Spiegel hat nur die Veröffentlichungs-Pipeline.
> Teststrategie: CHY-102 · Kennzahlen: `scripts/quality_gate.py` (CHY-84)

Jede Regel nennt, **wo sie geprüft wird**. Regeln ohne Prüfstelle sind als solche
gekennzeichnet — eine Konvention, die niemand durchsetzt, ist eine Absichtserklärung, und
davon hatten wir genug: Die Befunde vom 14. und 16.08. bestanden fast durchweg aus
Schutzmechanismen, die dokumentiert waren und nicht wirkten.

| Prüfstelle | Bedeutung |
|---|---|
| **ruff** | Bricht die Pipeline sofort. `pyproject.toml`, `[tool.ruff.lint] select` |
| **Ratchet** | `scripts/quality_gate.py` — die Zahl darf sinken, nicht steigen |
| **Test** | Ein benannter Test in `tests/` |
| **Review** | Nur menschliche Prüfung. Kandidat für Automatisierung |

## Das Muster hinter den meisten Regeln

**Etwas meldet Erfolg, weil es nicht hingesehen hat.** Dieselbe Form, immer wieder, in
Systemen, die miteinander nichts zu tun haben:

| Wo | Was gemeldet wurde | Was war |
|---|---|---|
| Abdeckungskennzahl | nach dem Refactoring besser | die Quellliste war geschrumpft, gemessen wurde weniger |
| `deploy:dev` | Exit 0 | der Container lief in einer Neustartschleife, 9.915 mal |
| Backup-Jobs | alle erfolgreich | seit Wochen wurden dieselben alten Daten kopiert |
| Qualitätsschranke | „unverändert" | die neuen Dateien waren ungetrackt und wurden nicht gemessen |
| Grenzen-Wächter | vier von sechs Regeln | zwei wurden erhoben und nie verglichen |
| `# noqa` | Warnung weg | die Regel war nicht durchgesetzt, unterdrückt wurde nichts |
| Gegenprobe | rot, also greift der Test | rot aus einem anderen Grund als dem geprüften |

Wer das einmal erkannt hat, findet es überall. Die Regeln unten sind Gegenmaßnahmen gegen
diese eine Sache. Wenn nur drei Sätze bleiben dürften:

1. **Etwas, das Erfolg meldet, hat nicht unbedingt hingesehen.** Prüfe, ob es hingesehen hat
2. **Eine Absicherung, die nie rot war, ist eine Bitte.** Mach sie einmal rot
3. **Steht dieselbe Wahrheit an zwei Stellen, laufen sie auseinander.** Halte sie mit einem
   Test zusammen oder lösche eine

---

## 1 — Sprache

**Englisch** für Code, Kommentare, Docstrings, Bezeichner und Commit-Nachrichten.
**Deutsch** für Tickets, Dokumentation und Gespräch.

Der Grund ist nicht Geschmack: Die Repositorien werden nach GitHub gespiegelt
(`github.com/cohereny/cohereny`). Ein deutscher Bezeichner in einem öffentlichen
Repositorium schließt Mitwirkende aus, ein deutsches Ticket niemanden.

**Deutsche Doku nicht zitieren, sondern wiedergeben.** ADRs, arc42 und
Whitepaper sind deutsch; ein Kommentar, der daraus wörtlich zitiert, ist zwar
belegend, aber eine Sprachausnahme *und* eine `codespell`-Fundstelle für ein
korrekt geschriebenes Wort. Am 04.09.2026 an einem Tag dreimal passiert —
Migration 018, `contexts/orchestration/execution.py` und
`tests/test_workflow_runner.py`. Der Verweis auf die Stelle (»ADR-028 Punkt 3«)
trägt dieselbe Beweislast wie das Zitat und kostet keine Ausnahme.

**Deutsche Prosa auf Deutsch denken, nicht übersetzen.** Lehnübersetzungen
fallen auf, und zwar dem Leser vor dem Autor:

| statt | besser | warum |
|---|---|---|
| „Test fällt" | **„Test schlägt fehl"** | wörtlich aus *test fails* übersetzt; im Deutschen fällt ein Test nicht |
| „Kaltdaten" | **„selten genutzte Daten"** | *cold data* hat kein deutsches Wort, also beschreibt man die Sache (INFRA-101) |
| „Ratsche" | **„Ratchet"** | der Fachbegriff existiert nur englisch (MR !90) |
| „Vertragstest" | **„Contract-Test"** | dieselbe Lage (INFRA-102, MR !68) |

**Existiert der Fachbegriff nur englisch, bleibt er englisch** — statt ein
deutsches Wort daneben zu erfinden. Bei „Ratsche" stand zwischenzeitlich
**„Rückschrittsperre"** da: gutes Deutsch, und trotzdem falsch, weil „Ratchet"
zu dem Zeitpunkt schon in ADR-017 und in drei Tickettiteln stand. **Ein zweiter
Name für dieselbe Sache ist dieselbe Doppelung wie zwei Quellen für dieselbe
Wahrheit** (§3) — und die bestand bereits: YouTrack sagte „Ratchet", die
Planung „Ratsche".

**Geprüft wird der Sinn, nicht das Wort.** Beim Sweep am 27.08. wurden **32
Fundstellen ersetzt und 22 bewusst stehen gelassen** — fast so viele behalten
wie geändert. In den Compliance-Unterlagen ist „Vertrag" Juristendeutsch:
Rechtsgrundlage nach Art. 6 DSGVO, EVB-IT-Wartungsvertrag,
ICT-Vertragsvereinbarungen nach DORA Art. 28. Ein pauschales Ersetzen hätte die
falsch gemacht.

> Deshalb ist das hier eine **Review**-Regel und keine für ein Skript: Das
> Verhältnis 32 zu 22 ist der Grund. Ein `sed` über die Repos hätte in jedem
> dritten Fall danebengegriffen, und die Fehlgriffe wären genau dort gelandet,
> wo ein falsches Wort am teuersten ist — in den Compliance-Unterlagen.

> **Was `codespell` misst — und was nicht** (CHY-142, 07.09.2026). Die Zahl las
> 190, und **neun** davon standen im Produktcode; die zehn häufigsten Meldungen
> waren deutsche Wörter. Ein Schuldenzähler, der wächst, wenn man sich an §1
> hält, misst das Falsche — und dieser drängte bereits in die andere Richtung:
> Ein Testfixture muss ein *deutsches* Bankdokument sein, um über eine
> Installation, die deutsche Bankdokumente liest, etwas zu beweisen.
>
> Ausgenommen sind seither **Fixtures, Generatoren und `pilots/`** — dort ist das
> Deutsch die Ausgabe der Datei oder ein Prompt, und ein Exemplar auf
> Rechtschreibung zu prüfen ist ein Kategorienfehler. Drin bleiben Produktcode,
> Migrationen, Tests und `scripts/`; die wenigen absichtlichen Stellen tragen
> `# codespell:ignore`, je Zeile und im Diff sichtbar.
>
> Eine pauschale Ignorierliste deutscher Funktionswörter wurde **verworfen**:
> Sie machte „ist" nie wieder als Tippfehler für „is" auffindbar und tauschte
> ein sichtbares Problem gegen ein unsichtbares.

*Prüfstelle:* **Review** für die Wortwahl · **Ratchet** (`codespell`, 41 nach
CHY-142) für Tippfehler in englischer Prosa · Rückstand: CHY-99

---

## 2 — Namen

**Ein Modul heißt nach dem, was es überwiegend tut.** `core/authz.py` trug den Namen seines
kleinsten Teils: 13 von 219 Zeilen waren Autorisierung, der Rest Identitätslebenszyklus.
Der Docstring gab es zu — gelesen hat ihn niemand, weil der Dateiname etwas anderes
versprach.

**Ein Verzeichnis, das nach einer Schicht heißt, enthält diese Schicht.** `domain/` enthielt
32 Klassen, davon 31 Pydantic oder Enum, und drei Methoden mit Logik auf 1004 Zeilen.

**Abkürzungen nur als etablierte Fachbegriffe** — `authn`/`authz`, `MR`, `PII` sind in
Ordnung; selbst erfundene Kürzel nicht.

*Prüfstelle:* **Review**. Automatisierbar ist das nicht, aber die beiden Fälle oben haben je
einen halben Tag gekostet.

**Bezeichner sagen, was sie enthalten, nicht wie sie entstanden sind.** `writer_ids` statt
`found`, `last_approved_version` statt `since`, `role_from_token` statt `derived`, `entry`
statt `e` — auch in einer Comprehension. Ein Wahrheitswert ist eine Frage
(`is_service_account`), eine Menge steht im Plural und nennt ihren Inhalt, eine Zahl nennt,
was gezählt wird (`people_who_may_approve`). Stehen zwei Quellen für denselben Wert
nebeneinander, nennt der Name die Herkunft.

**Umbenannt wird beim Anfassen, nicht in einem Rutsch.** Wer eine Datei ändert, benennt
unklare Namen in den berührten Funktionen mit um und hält das am Ticket der Änderung fest:
vorher, nachher, Grund. Eine eigene Umbenennungsrunde über den Bestand brächte viel Diff
ohne fachlichen Gewinn.

*Anlass:* 05.11.2026, CHY-163 — `wrote`, `since`, `found` und `e` in
`contexts/repository/four_eyes.py`, im Review bemerkt; der Kommentar an CHY-163 zeigt die
Tabelle vorher/nachher/Grund. *Prüfstelle:* **Review**.

---

## 3 — Ein Vokabular, eine Definition

Für jedes `CHECK (x IN (...))` in einer Migration gibt es **eine** Python-Definition, aus der
Typ und Prüfungen abgeleitet werden. Vorbild ist `kernel/roles.py` (INFRA-92): die Rangfolge
ergibt sich aus der Deklarationsreihenfolge, die Gruppennamen aus Präfix plus Rollenname,
und beides wird nirgends zweitgeschrieben.

Wo SQL nicht ableiten kann, steht ein **Wächtertest**, der beide Seiten vergleicht. Das gilt
insbesondere für die Übergangstabelle: `TRANSITIONS` im Aggregat und der Trigger aus
Migration 013 sind zwei Orte, und das ist richtig so — die Datenbank ist der letzte Wall,
die Anwendung liefert die verständliche Meldung. Aber sie müssen übereinstimmen.

**Übereinstimmung aus Zufall ist gefährlicher als eine Abweichung.** Am 03.09. standen
fünfzehn Vokabulare als `CHECK` und als `Literal` nebeneinander; **alle vierzehn Paare
stimmten überein, und nichts hielt sie so.** Eine Abweichung fällt beim ersten Gebrauch auf,
Zufall gar nicht — bis jemand ein `CHECK` erweitert und die API den neuen Wert weiter mit
einem 422 ablehnt, der nichts benennt.

**Die Zuordnung wird von Hand gepflegt.** Ein Abgleich über die Werte allein sähe ordentlicher
aus und wäre falsch: `actors.role` und `scope_memberships.role` tragen dieselben Werte und
sind nicht dasselbe Vokabular — und ein Paar, das auseinanderläuft, fiele aus dem Vergleich
**heraus**, die Prüfung hörte still auf zu prüfen.

**Ein Name für zwei Sachen ist derselbe Fehler andersherum.** `/v1/workflows` für
Workflow-Definitionen *und* für Pilot-Konfiguration wäre eine Ressource zu wenig; deshalb
heißt die zweite `/v1/pilots/configs`.

**Ein Nachweis enthält nur, was gewirkt hat** (ADR-032). Ein Wert im Nachweis, den kein Code
liest, macht ihn nicht unvollständig, sondern **falsch** — und unbemerkt falsch, weil er
vollständig aussieht. Die Regel lautet nicht „so vollständig wie möglich", sondern **„so weit,
wie die Behauptung trägt"**. Gilt für Transparenz-Datensätze, Audit-Einträge und
Abdeckungsberichte gleichermaßen.

**Eine neue Tabelle bringt ihre Rechte mit** (CHY-85). Die Anwendung läuft als Rolle, der keine
Tabelle gehört; sie darf, was `INVENTORY[...].access` in `kernel/retention.py` nennt, und
nichts sonst. Die Migration, die eine Tabelle anlegt, vergibt in derselben Datei
`GRANT … TO cohereny_app`, und der Eintrag in der Tabellenliste kommt im selben Commit. Ohne
Eintrag fällt der Test, ohne GRANT antwortet die erste Anfrage mit `permission denied`. Wer
für einen Test die Eigentümerin braucht (`owner_pool`), prüft entweder einen Trigger oder
hat ein fehlendes Recht gefunden.

*Prüfstelle:* **Test** (`tests/test_role_vocabulary.py`, `tests/test_status_vocabularies.py`,
`tests/test_app_privileges.py`, `tests/integration/test_app_role_db.py`)

> Die Konsolidierung des Rollen-Vokabulars übersah am 14.08. zwei von vier Kopien.
> Gefunden hat das nicht der Autor, sondern der Wächtertest zwei Tage später.

---

## 4 — Konfiguration

- **Ausschließlich in Konfigurationsmodulen.** Kein `os.getenv` in Fach- oder
  Transportmodulen, keine Laufzeitmutation von `os.environ`
- **Nur `https://` in Vorgabewerten** für interne Dienste
- **Ein Werkzeug, von dem eine Prüfung abhängt, ist eine Abhängigkeit** — es gehört in
  `[dev]`, nicht in ein `pip install` im CI-Skript. Sonst misst der Wächter lokal etwas
  anderes als in der Pipeline
- **Werkzeuge, deren Ausgabe in eine Grundlinie eingeht, werden exakt gepinnt**
  (`ruff==`, nicht `ruff>=`). Ein Ratchet vergleicht über die Zeit; das bedeutet nur etwas,
  solange das Lineal gleich bleibt

*Prüfstelle:* **Ratchet** · **Test** für die `https://`-Vorgaben · Rückstand: CHY-96

---

## 5 — Fehler

- **Kein `except: pass`.** Entweder protokollieren oder begründen — begründen heißt
  Kommentar **und** Log-Zeile. Auch `contextlib.suppress(Exception)` ist ein `pass`: Ein
  Redis, das nicht mehr antwortete, fiel nur daran auf, dass alles langsamer wurde (CHY-97)
- **Fremder Fehlertext bleibt drinnen.** Was die Installation verlässt — eine Antwort, ein
  gespeicherter Lauf, das Audit-Log — sagt, was schiefging, und trägt die Request-ID
  (`kernel.current_call.with_reference`). Was ein Gateway oder eine Bibliothek dazu schreibt,
  gehört ins Anwendungslog. Muster: `core/errors.py`, `str()` für außen und `detail` für das Log.
  Anlass: Ein 502 vom 14.08.2026 nannte jedem Aufrufer Adresse und Port eines
  Inferenz-Rechners (Audit B6, CHY-97)
- **Eine Logzeile nennt Dinge bei ihrer ID, sie zitiert sie nicht.** Kein Prompt, keine
  Antwort, kein Dokumenttext, kein Wert aus einem Lauf. Akteure stehen dort per UUID, nie per
  Token-Subject, das ein Identity Provider mit der E-Mail-Adresse füllen kann. Felder kommen
  über `extra=`, nicht in den Satz formatiert, damit jedes für sich auswertbar bleibt.
  Anlass: uvicorns Zugriffszeile schrieb zu jedem Request Client-Adresse und vollen
  Query-String (gemessen 09.11.2026); ersetzt durch die Request-Zeile in `api/request_log.py`
- **Nie breiter fangen, als man behandeln kann.** `except Exception` braucht einen Satz,
  warum genau hier alles gefangen werden muss
- **Kontexte werfen Domänenfehler, keine `HTTPException`.** Die Zuordnung auf Statuscodes
  geschieht **einmal zentral** je Oberfläche, nicht an jeder Fundstelle
- **Ein Wächter, der nach außen ausfällt, ist schlimmer als keiner** — er erzeugt Belege
  dafür, dass geschützt wurde. Fehlt ein Werkzeug, bricht die Prüfung ab, statt null zu zählen
- **Verweigern statt still nichts tun.** Ein Standard, der stillschweigend nichts tut, lässt
  einen Lauf auf `complete` kommen, ohne seine Arbeit getan zu haben — und das sieht von außen
  aus wie Erfolg. Kein Executor für einen Knotentyp: **wirft**, mit Ticketnummer
- **Eine falsche Erklärung kostet mehr als keine.** Ein stilles `false` bei einer nicht
  auswertbaren Bedingung läse die Zustandsmaschine als „kein Arm hat gepasst"; der Lauf bräche
  mit einer Meldung über eine fehlende `default`-Kante ab und beschuldigte den Workflow für
  eine Fähigkeit, die fehlt
- **Ein Override, der nicht wirkt, wird abgelehnt, nicht ignoriert.** Eine API, die eine
  Anweisung entgegennimmt und nichts damit tut, meldet Erfolg, ohne hingesehen zu haben
- **Ein Fehler des Aufrufers ist ein 4xx, nie ein 500.** Schemathesis fand beim ersten Lauf
  fünf Klassen, die mit 500 antworteten: Ganzzahlen ohne Obergrenze, Fremdschlüssel auf einen
  unbekannten Scope, eine CHECK über zwei Spalten, NUL in Text und in JSONB (CHY-165). Die
  Grenze einer Ganzzahl gehört **ins Schema** (`le=PG_INT_MAX`), damit sie im Contract steht;
  eine Datenbankablehnung übersetzt **der Store**, der die Constraint-Namen kennt
  (`NodeNotStorable`, `BlockNotStorable`) — nur NUL fängt der Transport, weil er in jedem Feld
  ankommen kann und kein Constraint ihn benennt.
  **An jeder Stelle, die schreibt, nicht nur beim Anlegen:** !132 übersetzte `insert_block`,
  aber nicht `edit_draft`, und `insert_node` die CHECKs, aber nicht den Fremdschlüssel auf das
  Ziel eines `sub_workflow` — zwei 500er, gefunden am Tag nach dem Merge (CHY-165). Übersetzt
  wird nach dem **Namen** des Constraints, damit ein anderer Fremdschlüssel nicht als der
  bekannte gemeldet wird
- **Verschiedene Reaktionen brauchen verschiedene Klassen.** `ModelRefused` (502) scheitert
  beim Wiederholen genauso, `ModelUnreachable` (503) ist einen Neuversuch wert und weckt
  jemanden, wenn es anhält. Ebenso endet eine Ablehnung am Freigabe-Gate als `rejected`, nicht
  als `failed`: es ist nichts schiefgegangen, jemand hat hingesehen und nein gesagt. Beides zu
  vermengen ließe eine Governance-Entscheidung in jedem Fehlerbericht auftauchen

*Prüfstelle:* **ruff** (`TRY`, `EM` als Ratchet) · **Test** für fremden Fehlertext und die
Request-Zeile (`tests/test_request_log.py`, `tests/test_complete.py`) · **`scripts/architecture.py`** — Regel
`http_below_transport`, Ausgangswert **3** (nicht 5; die frühere Zahl stammte aus `grep` und
zählte Importzeile und Kommentar mit)

---

## 6 — Transaktionen und Reihenfolge

```
prüfen  →  sperren  →  ändern  →  protokollieren  →  committen  →  invalidieren
```

- **Rechteprüfung vor der Transaktion.** Sonst hält eine Sperre für jemanden, der nicht darf
- **Zustandsänderung und Audit-Eintrag in derselben Transaktion. Ausnahmslos.** Sonst
  überlebt die Änderung und der Nachweis nicht
- **Cache-Invalidierung nach dem Commit.** Ein Redis-Ausfall darf keine bereits korrekte
  Freigabe zurücknehmen. Ein veralteter Cache ist der einzige der vier Fehler, den man
  tragen kann
- **Optimistische Sperre bei jeder Zustandsänderung**, die ein Mensch entschieden hat: Wer
  einen bestimmten Text freigibt, darf nicht versehentlich eine spätere Fassung freigeben

- **Ein JSONB-Parameter geht als Text: `json.dumps(...)` an `$n::text::jsonb`.** Der Pool der
  App hat einen JSON-Codec; ein schon serialisierter String an `$n::jsonb` wurde ein zweites
  Mal kodiert, und Postgres speicherte einen JSON-*String* statt eines Objekts. Auf einer
  nackten Verbindung ohne Codec war dieselbe Zeile richtig — dieselbe Schreibweise, zwei
  Ergebnisse, je nachdem, wer die Verbindung geöffnet hat. Alle 98 `metadata`-Zeilen in dev
  waren Strings, und die CHECKs aus 022/023 griffen auf dem API-Weg nicht (CHY-164). Mit dem
  expliziten Cast parst Postgres den Text, auf jeder Verbindung

- **Migrationen gehen nur vorwärts** (CHY-113). Eine Migration, die gemergt ist, wird nicht mehr
  geändert: Installationen, die sie schon ausgeführt haben, sähen die Änderung nie, und neue
  bekämen aus derselben Version ein anderes Schema. Ein Fehler wird durch eine neue Migration
  berichtigt — so berichtigt 037 die falsche Begründung aus 014 (CHY-152). Rückwärts-Migrationen
  gibt es nicht: Von 13 Dateien hatte im August keine einen Rückweg, und ein nie geprobter
  Rückweg wäre eine Behauptung. Der Rückweg einer Installation ist das Backup vor dem Update;
  Arbeitsregel 12 ist damit Produkteigenschaft statt Warnung (arc42 §8.11). Jede neue Migration
  hängt ihre Zeile an `migrations/CHECKSUMS` an. **Eine geänderte Zeile dort heißt: eine Migration
  wurde umgeschrieben** — der MR muss sagen, warum

*Prüfstelle:* **Review** · Begründung in arc42 §8.10 · **`tests/test_jsonb_parameters.py`**
(kein `$n::jsonb` in den Schreibern) · **`tests/integration/test_jsonb_lands_as_objects.py`**
(`jsonb_typeof` nach jedem Schreibweg) · **`tests/test_migrations_forward_only.py`** (jede
Migration gegen ihre Zeile in `migrations/CHECKSUMS`)

---

## 7 — Grenzen (ADR-030)

- **Abhängigkeiten zeigen nach innen.** Zone 1 → 2 → 3. Kein Kontext importiert einen
  zweiten Kontext; jeder darf `kernel/`
- **Kein `contexts/**` importiert `fastapi` oder `starlette`**
- **SQL nur in `contexts/*/store.py` und `kernel/`**
- **`api/` enthält nur, was es wegen HTTP gibt:** Statuscodes, Seitenaufteilung,
  SSE-Rahmung, Abhängigkeitsinjektion. Kriterium ist Erreichbarkeit, nicht Ort — was auch
  CLI, Hintergrundjob oder Runner können müssen, liegt unterhalb
- **`cli/` enthält kein SQL** und ruft ausschließlich Kontext-Services
- **Protocol an Kontextgrenzen, konkret innerhalb.** Bei einer Implementierung kauft ein
  Protocol nur Dokumentation — an der Grenze verhindert es, dass ein Kontext in den Store
  eines anderen greift
- **Wer für eine Person liest, liest in `acting_as`** (CHY-92). Die Lauf-Tabellen stehen
  unter Row-Level Security: Eine Abfrage, die nicht sagt, für wen, sieht nichts. Den
  System-Pool nimmt nur der Runner, und erst nachdem die Person in ihrem eigenen Kontext
  geprüft wurde. Wer ihn in einer Route braucht, um einen Lauf zu lesen, hebt die
  Isolation für diese Route auf

*Prüfstelle:* **CHY-103** (Wächter) für die ersten beiden · **Ratchet** für SQL-Streuung ·
**Test** `tests/test_run_isolation.py` für den Systemkontext

---

## 8 — Endpunkte und Contracts

- **Geplant heißt 501**, mit `x-cohereny-status: planned` und Ticket. Nie 200 mit
  `{"status": "not_implemented"}` — eine Operation, die Erfolg meldet und nichts tut, ist
  schädlicher als eine fehlende
- **Ein Endpunkt, der `actor` entgegennimmt, benutzt ihn** — oder nimmt ihn nicht entgegen.
  Andernfalls ist der Aufrufer geprüft und die Identität weggeworfen
- **Fehlerantworten deklarieren, wo der Fehler der Contract ist** (Blocklebenszyklus, Audit).
  Nicht flächendeckend: 401 auf jeder geschützten Route ist wahr und nutzlos
- **`openapi.yaml` wird erzeugt, nie von Hand geändert** (ADR-029)
- **Kein Dokument zählt Endpunkte auf.** Prosa-Listen haben genau eine Eigenschaft, die die
  erzeugte Spezifikation nicht hat: unbemerkt falsch zu werden

*Prüfstelle:* **Ratchet** (`unused_actor`, Ausgangswert 40; `planned_operations` 29) ·
**Test** (`tests/test_openapi_contract.py`)

---

## 9 — Tests

- **Kein Schutz ohne vorgeführten Fehlerfall.** Wer eine Kontrolle einbaut — Trigger, CHECK,
  Rollen-Gate, Alarmregel, Wächter — führt zuerst den Fall vor, den sie fangen soll, und
  zeigt, dass er ohne sie durchgeht
- **Charakterisierungstests vor dem Verschieben** bestehenden Codes, nicht danach
  (Feathers). Erst das heutige Verhalten festnageln, dann bewegen
- **Datenbankregeln gegen eine echte Datenbank**, in einer zurückgerollten Transaktion.
  Dieses Verfahren hat fünf Fehler gefunden, die Mocks nicht zeigen konnten. **Seit CHY-124
  tut das der Job `integration` in jeder Pipeline** (`tests/integration/`, Postgres als
  Service, Migrationen ab 001) — vorher geschah es von Hand, und dreimal an einem Tag
- **Derselbe Job fuzzt die echte App aus ihren eigenen Contracts** (Schemathesis,
  `scripts/contract_fuzz.py`), **seit 25.09.2026 scharf**: ein Fund lässt ihn fallen. Erst
  Bericht, dann scharf, wie die ruff-Familien — scharf geschaltet, als alle vier Oberflächen
  ohne Fund liefen. Die Gegenprobe im Skript (ein absichtlich gebrochener Contract muss
  gemeldet werden) war von Anfang an scharf
- **Der Test-Pool wird gebaut wie der der App** (`_register_codecs` aus `core.db`). Ein
  nackter Pool testete eine andere Welt: der erste Lauf scheiterte an einem Dict für JSONB,
  und erst mit dem Codec der App zeigte sich die doppelte Kodierung (CHY-164)
- **Ein Doppel beweist nur, was der Test ihm vorgibt.** Ein nachgebautes
  `CheckViolationError` mit selbst gesetztem `constraint_name` bestätigt die Übersetzung, nicht
  den Namen, den Postgres schickt. Zu jeder Übersetzung gehört ein Integrationstest, der die
  Datenbank fragt (CHY-164, CHY-165)
- **Die Fachregel ist ohne Stack testbar.** Das Aggregat fasst den Store nicht an
- **Laufzeit der Pipeline ist kein Gegenargument**

### Gegenproben

Der vorgeführte Fehlerfall wird zum Verfahren: **jede Absicherung wird einmal entfernt, und
der zugehörige Test muss fallen.** Danach wird sie zurückgesetzt. In acht Tickets hintereinander
hat das jedes Mal mindestens einen Test entlarvt, der nichts absicherte.

- **Durch den Aufrufer treiben, nicht durch die Funktion.** Ein Wächter für `_require_git()`
  rief die Prüfung direkt auf; den Aufruf aus `codespell_count` zu löschen ließ die Suite grün
- **Die Probe zählt ihren Anker** und meldet, wenn er null- oder mehrfach trifft. Nach jedem
  Refactoring müssen die Proben nachgezogen werden — das ist der Preis dafür, dass sie etwas
  wert sind
- **Jede Probe bekommt eine Zeitgrenze.** Das Abschalten der Zyklenerkennung brach die
  *Terminierung* statt der Regel; der Probenlauf hing, nicht der Test. **Ein Werkzeug, das beim
  Prüfen stehenbleibt, prüft nichts**
- **Den Grund prüfen, nicht nur den Statuscode.** Ein Test auf `== 409` blieb grün, weil mit
  entfernter Regel eine *andere* dieselbe Antwort lieferte
- **Ein Test kann eine Regel beschreiben, ohne sie auszuüben.** Bleibt so einer stehen, gehört
  **in den Test**, dass er keine Deckung bietet — sonst hält ihn der Nächste dafür
- **Positivfälle wiegen so schwer wie Verbote.** Ein Validator, der alles ablehnt, besteht jede
  Verbotsprüfung und ist unbrauchbar. Zu jeder Ablehnung gehört der Fall, der durchgehen
  **muss**: ein Ratchet muss Verschärfen durchlassen, sonst ist er eine Wand
- **Eine Eigenschaft prüfen, nicht nur Regeln.** Der wichtigste Test des Runners prüft, dass
  ein unterbrochener und fortgesetzter Lauf dort endet, wo ein durchgehender endet
- **Der Testfall muss die Fälle unterscheiden können, über die er urteilt.** Der Schleifentest
  in CHY-131 prüfte, dass ein Knoten die Ausgabe seines Vorgängers sieht — aber jede Runde
  erzeugte denselben Text, also wäre er auch grün geblieben, wenn der Knoten die **erste**
  Runde gelesen hätte. Erst als die Ausgabe ihre Iterationsnummer mittrug, prüfte er etwas
- **Vor der Probe eine echte Kopie anlegen.** `git checkout` stellt eine **ungetrackte** Datei
  nicht wieder her: In CHY-131 überschrieb eine Probe die neue `dataflow.py`, und das
  Zurücksetzen lief ins Leere. Entweder vorher `git add`, oder eine Kopie außerhalb des Baums

*Prüfstelle:* **Test** · Akzeptanzkriterien an jedem Ticket (CHY-102) · Gegenproben im MR
belegt

---

## 10 — Dokumentation im Code

- **Docstrings sagen *warum*, nicht *was*.** Was der Code tut, steht im Code
- **`Called by:` nennt tatsächliche Aufrufer.** Beabsichtigte gehören unter eine eigene
  Überschrift oder ins Ticket
- **Keine undokumentierte öffentliche Funktion oder Klasse**
- **Ein Kommentar ist keine Kontrolle.** *„never use in prod"* über einer Zeile, die in prod
  laufen kann, ist eine Notiz und kein Schutz
- **Aussagen über Code über den AST, nie per `grep`.** Prosa als Code zu zählen ist an einem
  Tag sechsmal passiert: Docstrings, ein `SELECT` in einer Erklärung, eine Route-URL in einem
  Dekorator, ein Enum-Mitglied. Docstrings über die **Position** ausschließen (`fn.body[1:]`),
  nicht über einen Wertvergleich — der ist einen Tippfehler davon entfernt, sie durchzulassen
- **Dasselbe in SQL:** `--`-Kommentare abschneiden, bevor man etwas behauptet. Ein Test suchte
  drei Zeichenketten in einer Migration und überlebte, dass der `CREATE TRIGGER`
  **auskommentiert** wurde
- **Die Prüfung selbst prüfen.** Ein Parser, der nichts findet, macht jede Folgeaussage leer
  wahr. Zu jeder Extraktion gehört eine Behauptung über die Menge (`assert len(...) >= 15`)

*Prüfstelle:* **Review** · **Test** für die Wächter selbst

---

## 11 — Commits und Merge Requests

- **Prüfen und Committen als eine ungepipte `&&`-Kette:**
  `pytest -q >/dev/null && ruff check . && git add -A && quality_gate.py && git commit …`.
  `gate | tail && commit` committet auch bei Rot — die Pipe liefert den Exit-Code von `tail`;
  ein `;` statt `&&` ebenso. Am 07.09. und zweimal am 24.09. ging so ein Rückschritt durch
  (CHY-162). Der Zonenwächter gehört **nicht** in die Kette: er endet mit 1, solange es
  überhaupt Verstöße gibt, auch die drei bekannten

- **YouTrack-Kennung am Ende der Commit-Nachricht:** `feat: add router engine CHY-35`
- **Sperre für Sicherheitskorrekturen:** Ein Fix für eine Lücke, die noch nicht öffentlich
  ist, trägt im Trailer-Block die Zeile `Embargo: CHY-…`. Solange er auf `test` liegt und noch
  nicht auf `main`, veröffentlicht `publish:next` nichts (CHY-272). Ohne die Zeile stünde der
  Fix nach dem nächsten Deploy auf test öffentlich auf GitHub `next` — vor dem Release, das
  Betreiber erst schützt
- **Englisch**, Beschreibung im Imperativ, Begründung im Rumpf
- **Kein direkter Push auf `main`/`test`.** `dev` → MR → `test` → MR → `main`, reguläre
  Merge Commits
- **Nach dem Push prüfen, ob es dafür noch einen offenen MR gibt.** Ein gemergter MR ist
  geschlossen; spätere Commits hängen unverbunden am Branch
- **Wird eine Grundlinie angehoben, steht die Begründung im MR.** Der Sinn der Prüfung ist,
  dass diese Möglichkeit auffällt

*Prüfstelle:* **Review** · **Ratchet** meldet Anhebungen sichtbar

---

## 12 — Messen

Eine Kennzahl vergleicht über die Zeit. Das bedeutet nur etwas, solange **das Lineal gleich
bleibt** — und die Wege, auf denen es sich unbemerkt ändert, sind alle schon vorgekommen.

- **Werkzeugversionen pinnen und in der Grundlinie mitschreiben.** Eine neue Linter-Version
  bringt neue Regeln, und die zählen als Verschlechterung, die niemand geschrieben hat. Die
  Schranke hält an, sobald die Version abweicht
- **Die Quellliste vollständig halten.** Jede Extraktion in ein neues Paket verkleinerte den
  Nenner: `kernel/` und `contexts/` fehlten in der Abdeckungsmessung, und ein Refactoring
  **verbesserte** die Zahl, indem es weniger maß — ehrlich waren 52,4 %, gemeldet 44,7 %
- **Nur versionierte Dateien zählen** (`git ls-files`). Sechs Fixture-PDFs ergaben 172 Treffer
  lokal und 146 in der CI. **Die Kehrseite:** neue Dateien sind ungetrackt und fallen aus der
  Messung — genau die, in denen ein neuer Befund am wahrscheinlichsten steckt. Die Schranke
  hält deshalb an, wenn ungetrackte Quelldateien vorliegen
- **Eine Kennzahl, die erhoben und nie gemeldet wird, ist schlimmer als keine.** Zwei von sechs
  Grenzregeln wurden nie verglichen; ein Verstoß wäre bei `--update` stillschweigend
  eingebacken worden. Sie sieht abgedeckt aus — das ist der Schaden
- **Vor jedem `--update` den Diff der Grundlinie lesen**, nicht die Meldung. Die Meldung zeigt,
  was sie zu zeigen weiß
- **Statt anzuheben: den Grund beseitigen.** In vier Fällen erzwang die Schranke eine echte
  Verbesserung — zwei Funktionen mit derselben Spaltenliste wurden eine, ein `status`-Parameter
  wurde zwei Funktionen, vier zusammen reisende Werte wurden ein Objekt, sechs Meldungsliterale
  wurden Konstruktoren an der Fehlerklasse. Anheben ist der zweite Weg, mit Begründung im MR,
  und der Sinn der Prüfung ist, dass er **auffällt**

*Prüfstelle:* **Ratchet** (`scripts/quality_gate.py`) · **Test** (`tests/test_quality_gate.py`)

---

## 13 — Wächter

- **Ein Wächter darf nicht an einer Zahl kleben.** Ein Spaltenwächter war auf Migration 013
  festgenagelt, obwohl der Kommentar darüber genau diese Falle für 012 beschrieb — die damalige
  Korrektur bestand darin, *die Zahl zu ändern* statt die Bindung aufzulösen. Er rechnet sich
  die letzte Definition jetzt aus. **Eine Warnung im Kommentar ist keine Absicherung**: wenn
  die Falle beschreibbar ist, ist sie auch ausrechenbar
- **Verhindern schlägt melden.** Für Zugangsdaten im Klartext wäre CI ein Melder gewesen, kein
  Wächter — sie läuft **nach** dem Push. Ein `pre-commit`-Hook verhindert, und global über
  `core.hooksPath`, weil `.git/hooks` beim Klonen nicht mitkommt
- **Auf Frische alarmieren, nicht auf Status.** Ein Job, der Erfolg meldet und nichts tut, ist
  unsichtbar, wenn man den Job-Status überwacht. Überwacht wird das **Alter der Daten**
- **Muster statt Entropie**, wo die Umgebung Prosa ist. Ein Entropie-Scanner schlägt in einem
  Repositorium voller Commit-Hashes auf jeder Seite Alarm und ist in einer Woche abgeschaltet

*Prüfstelle:* **Test** je Wächter · Gegenprobe nach §9

---

## 14 — Zuschnitt und Reihenfolge

- **Vor der Planung am Host prüfen, nicht im Dokument nachlesen.** Von elf geprüften Zeilen der
  Betriebsdokumentation waren neun falsch. Vor jedem Schemaeingriff dieselbe Frage: sind die
  Tabellen wirklich leer?
- **Widersprüche zwischen Dokument und Code aktiv suchen.** Dreimal beschrieb eine Vorlage
  etwas, das es nicht gab — ein Feld an einer Tabelle ohne Zeilen, eine Konfliktregel, die eine
  ADR selbst überholt hatte, eine Abgrenzung, die ein Beschluss aufgehoben hatte
- **Eine in Prosa reservierte Nummer ist nicht reserviert.** Dreimal war eine Migrationsnummer
  beim Umsetzen längst vergeben — einmal davon war die Angabe selbst schon eine Korrektur
- **Ein Beschluss ohne Ticket ist kein Plan.** Eine ADR beschloss einen eigenen Runner,
  schätzte 800–1500 Zeilen und listete drei betroffene Tickets — keines davon baute ihn
- **Bilder veralten stiller als Text.** Diagramme werden bei jeder Architekturentscheidung
  mitgeprüft, und **gerendert, nicht gezählt**
- **Tabellen ohne den Code, der sie liest, sind geraten.** Wo das trotzdem gewollt ist, wird
  der Preis **vorher** benannt — bei den Zustandstabellen stand er wörtlich in der
  Optionsliste, und es kam genau so
- **Riskantes zuerst, mit Tests vor Aufrufern.** Das Risiko liegt nicht in der ersten Fassung,
  sondern in den Randfällen
- **Große Tickets in Schritte zerlegen, die einzeln vollständig sind.** Jeder Schritt hat
  eigene Gegenproben, und jeder deckte eine Lücke im vorherigen auf
- **Ideen zuerst ins Backlog.** Jeder Nebenschauplatz ist für sich begründbar; in Summe
  verschieben sie die Prioritäten unbemerkt. Ausnahme: der Fund blockiert die laufende Aufgabe
  oder es droht akut Schaden — dann benennen, *warum* es nicht warten kann

*Prüfstelle:* **Review** · Ticket je Fund

---

## 15 — Bewusst keine Regel

Damit niemand sie neu verhandelt:

| | Warum nicht |
|---|---|
| Zeilenlänge | `E501` ist abgeschaltet. Der Formatierer entscheidet das, nicht die Pipeline |
| Typannotationen überall | mypy läuft nicht in CI. Eine Regel ohne Prüfstelle wäre genau das, was dieses Dokument vermeiden soll |
| Value Objects für Kennungen | `BlockId(UUID)` fängt, was eine Typprüfung schon fänge, und kostet Konvertierungen an jeder Grenze |
| Ereignisbus, Outbox, CQRS | Erst wenn ein Verbraucher außerhalb des Requests läuft (ADR-030) |
| Eine Handler-Klasse je Endpunkt | `ApproveBlockCommandHandler` sagt nichts, was `approve_block` nicht sagt |
| Repositories um Ein-Satz-Abfragen | Zeremonie — und der Weg, auf dem `core/` zum nächsten Gottmodul wird |

---

## Änderungen an diesem Dokument

**09.11.2026** — §5 *Fremder Fehlertext bleibt drinnen* und *Eine Logzeile zitiert nicht*,
dazu `suppress` als Form von `pass` (CHY-97; Anlass: der 502 vom 14.08. mit interner Adresse,
die gemessene Zugriffszeile von uvicorn, sechs stille Fehlerstellen).

**07.11.2026** — §6 *Migrationen gehen nur vorwärts* (CHY-113; Anlass: Von 13 Migrationen hatte
keine einen Rückweg, und Arbeitsregel 12 stand nur als Warnung da; die falsche Begründung in 014
war vorwärts zu berichtigen, CHY-152).

**06.11.2026** — §7 *Wer für eine Person liest, liest in `acting_as`* (CHY-92; Anlass: Jeder
Angemeldete las jeden Lauf, und die Pilot-Lesepfade kannten den Aufrufer nicht einmal).

**06.11.2026** — §3 *Eine neue Tabelle bringt ihre Rechte mit* (CHY-85; Anlass: die Anwendung
lief als Superuser und Eigentümerin, die Trigger gegen das Umschreiben der Historie hielten
deshalb nur gegen Programmfehler).

**05.11.2026** — §2 *Bezeichner sagen, was sie enthalten* und *Umbenannt wird beim Anfassen*
(CHY-110; Anlass: unklare Namen im ersten Wurf von CHY-163).

**25.09.2026** — §5 *an jeder Stelle, die schreibt* (CHY-165: zwei 500er, die !132 übersehen
hatte); §9: Schemathesis läuft im Job `integration` jetzt scharf (`--strict`, CHY-124).

**24.09.2026** — vier Regeln aus einem Tag: §5 *Fehler des Aufrufers ist ein 4xx* (CHY-165,
fünf Klassen von 500, gefunden von Schemathesis); §6 *JSONB-Parameter als Text* (CHY-164, 98
doppelt kodierte Zeilen in dev); §9 *Test-Pool wie der der App* und *ein Doppel beweist nur,
was der Test vorgibt* (CHY-164); §11 *ungepipte `&&`-Kette* (drei durchgerutschte
Rückschritte).

Eine neue Regel braucht **einen Vorfall oder eine Messung**, nicht eine Meinung. Jede Regel
oben lässt sich auf einen benannten Befund zurückführen; wo das nicht geht, gehört sie unter
§15 oder gar nicht hierher.
