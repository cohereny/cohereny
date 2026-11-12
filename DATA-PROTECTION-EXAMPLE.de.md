# Muster-DSFA: Arztbriefe für die ePA strukturieren

> **Ein ausgefülltes Beispiel, kein Rechtsrat.** Klinik, Abläufe, Fristen und Bewertungen sind
> erfunden. Das Beispiel prüft die Vorlage [DATA-PROTECTION.md](DATA-PROTECTION.md) an einem Fall:
> Reicht sie, um eine Datenschutz-Folgenabschätzung nach Art. 35 DSGVO für eine Verarbeitung mit
> Gesundheitsdaten zu tragen? Wer Cohereny ähnlich einsetzt, kann es als Ausgangspunkt nehmen —
> die Bewertung trifft der Verantwortliche, nicht dieses Dokument. English version:
> [DATA-PROTECTION-EXAMPLE.md](DATA-PROTECTION-EXAMPLE.md).
>
> Stand der Software: Cohereny mit Platzhaltern in gespeicherten Läufen (CHY-234, November 2026).

## 0 · Eckdaten

| | |
|---|---|
| Verantwortlicher | Klinikum Beispielstadt, Klinik für Innere Medizin (erfunden) |
| Verarbeitung | Ein Sprachmodell entwirft aus dem Freitext eines Entlassbriefs eine strukturierte Fassung (Diagnosen, Medikation, Empfehlungen) für die elektronische Patientenakte; eine Ärztin oder ein Arzt prüft und gibt frei |
| Warum eine DSFA | Umfangreiche Verarbeitung von Gesundheitsdaten (Art. 35(3)(b), Art. 9(1)) mit einer neuen Technologie; die Liste der Aufsichtsbehörde nach Art. 35(4) ist zusätzlich zu prüfen |
| Beteiligt | Datenschutzbeauftragte (Art. 35(2)), IT-Sicherheit, Chefarzt der Klinik, Patientenfürsprecherin (Art. 35(9)) |
| Nicht Gegenstand | Ob das System als Medizinprodukt (MDR) und damit als Hochrisiko-KI-System nach der KI-Verordnung gilt — das klärt der Verantwortliche gesondert. Das Krankenhausinformationssystem (KIS) und die ePA haben eigene Folgenabschätzungen |
| Überprüfung | Bei jeder wesentlichen Änderung (neues Modell, neuer Bereich, geänderter Workflow, neue Cohereny-Version mit Datenschutzbezug), sonst jährlich (Art. 35(11)) |

## 1 · Beschreibung der Verarbeitung — Art. 35(7)(a)

### Zweck und Rechtsgrundlage

Ärztinnen und Ärzte verbringen viel Zeit damit, Entlassbriefe für die ePA in Abschnitte zu
gliedern. Das Modell liefert einen Entwurf, die Entscheidung bleibt bei der Ärztin: Kein Entwurf
gelangt ohne ihre Freigabe ins KIS.

Rechtsgrundlage ist Art. 9(2)(h) DSGVO, Gesundheitsversorgung, mit der nationalen Grundlage des
Verantwortlichen (in Deutschland unter anderem § 22 Abs. 1 Nr. 1 lit. b BDSG; für die ePA die
Vorschriften des SGB V). Im Beispiel ist das als geprüft angenommen; in einem echten Einsatz prüft
es die Rechtsabteilung.

### Wessen Daten, welche Daten

| Betroffene | Daten | Herkunft |
|---|---|---|
| Patientinnen und Patienten | Name, Geburtsdatum, Krankenversichertennummer, Diagnosen, Befunde, Medikation, Verlauf — besondere Kategorie nach Art. 9 | Entlassbrief aus dem KIS |
| Dritte im Brief | Angehörige, einweisende Ärztinnen und Ärzte, mitbehandelnde Kliniken | Freitext des Briefs |
| Beschäftigte | Konto (Anzeigename, Kennung beim Identitätsanbieter), Freigaben und Begründungen am Gate | Identitätsanbieter des Klinikums, Cohereny |

### Wie die Daten fließen

1. **Das KIS startet den Lauf.** Es sendet den Brieftext an `POST /v1/workflows/{id}/runs`, mit
   einem technischen Konto in der Gruppe `chy-service-accounts`. Unter `identifiers_to_redact`
   nennt es, was es über den Fall weiß: Name und Geburtsdatum der Patientin, die Namen der
   Angehörigen aus den Kontaktdaten, die einweisende Ärztin. Einen PDF-Eingang gibt es noch nicht
   (CHY-174); das KIS liefert Text.
2. **Cohereny ersetzt, bevor es speichert.** Jeder Text der Eingabe wird durchsucht: die
   mitgelieferten Muster (darunter Krankenversichertennummer und gekennzeichnetes Geburtsdatum),
   die genannten Namen und die Regeln des Bereichs — hier die Namen der Beschäftigten und das
   Format der Fallnummern als `redaction_rule`-Block. Gespeichert wird der Lauf mit Platzhaltern
   (`{{PERSON_1}}`). Die Zuordnung zu den Werten legt Cohereny je Lauf verschlüsselt ab; den
   Schlüssel hält das Klinikum außerhalb der Datenbank.
3. **Der Kontext wird komponiert.** Der Workflow holt die freigegebenen Bausteine seines
   Bereichs: Gliederung, Anweisungen zur Medikation, Stil. Bausteine enthalten keine
   Patientendaten.
4. **Das Modell entwirft.** Der Bereich hat die Datenklasse `critical`; Cohereny ruft nur Modelle
   auf, deren Obergrenze `critical` ist. Im Klinikum ist das ein Modell auf eigenen Servern. Es
   sieht nur Platzhalter. Was es von sich aus an Bezeichnern schreibt, ersetzt Cohereny, bevor die
   Antwort gespeichert oder im Trace aufgezeichnet wird.
5. **Die Ärztin prüft am Gate.** Der Workflow hält vor dem Ergebnis an (`human_review`). Die
   Stationsärztin liest den Entwurf mit Platzhaltern und lässt sich die Werte auflösen, um zu
   prüfen, ob die richtige Patientin gemeint ist; jedes Auflösen steht im Audit-Log. Sie gibt frei
   oder lehnt ab; eine Ablehnung beendet den Lauf. Auch ihre Begründung wird mit Platzhaltern
   gespeichert.
6. **Das KIS übernimmt das Ergebnis.** Nach der Freigabe löst das KIS als Starter des Laufs auf und
   schreibt den strukturierten Brief in die ePA. Ab hier gilt die Folgenabschätzung von KIS und ePA.
7. **Die Inhalte vergehen.** Der Bereich hält Inhalte sieben Tage — das Ergebnis liegt dann im
   KIS. Danach leert der nächtliche Löschlauf Eingabe, Ausgaben, Begründungen und die Zuordnung.
   Wer gestartet, welches Modell geantwortet und wer freigegeben hat, bleibt 183 Tage
   (Art. 26(6) KI-Verordnung als Untergrenze), dann wird der Lauf gelöscht. Sieben Tage für die
   Inhalte setzen voraus, dass der Workflow als `limited` eingestuft ist, weil der
   Verantwortliche ihn nicht als Hochrisiko-System bewertet hat (oben, nicht Gegenstand).
   Stünde er auf `high` oder gar nicht eingestuft, verlangte Cohereny vor dem Aktivieren auch für
   die Inhalte mindestens 183 Tage (CHY-266), und R6 und R9 wögen anders.

### Wo was liegt, und wer es lesen kann

| Ort | Was | Wer liest |
|---|---|---|
| Cohereny-Datenbank | Läufe mit Platzhaltern; Zuordnung verschlüsselt; Audit-Log | Wer den Lauf gestartet hat, die Mitglieder des Bereichs „Innere Medizin“ und der Bereiche darüber, `admin` — durchgesetzt per Row-Level Security |
| Werte eines Laufs | Über `POST /v1/workflows/runs/{id}/resolve` | Der Starter (hier das KIS) und ab Rolle `reviewer`, wer den Lauf sieht; jedes Mal im Audit |
| Modellserver | Prompt und Antwort, nur mit Platzhaltern | Betrieb der Klinik-IT; der Server speichert keine Anfragen (vom Betrieb geprüft) |
| Langfuse | Trace je Modellaufruf, mit Platzhaltern | IT-Betrieb; Aufbewahrung dort auf sieben Tage gesetzt |
| Backups | Datenbank, verschlüsselt | IT-Betrieb; der Schlüssel für die Zuordnungen liegt nicht im selben Backup |

## 2 · Notwendigkeit und Verhältnismäßigkeit — Art. 35(7)(b)

- **Geeignet und erforderlich:** Ohne Modell gliedern Ärztinnen jeden Brief von Hand. Der Entwurf
  spart diese Zeit; die fachliche Entscheidung bleibt bei ihnen. Ein Modell beim Anbieter
  bräuchte einen Auftragsverarbeitungsvertrag und hätte die Daten außer Haus — die Datenklasse
  `critical` schließt das aus.
- **Datenminimierung:** Das Modell sieht keine Bezeichner, die Cohereny erkennt. Gespeichert wird
  mit Platzhaltern, die Werte nur verschlüsselt. Dass ein Name, den niemand nennt, durchrutscht,
  ist ein Restrisiko (Abschnitt 5).
- **Speicherbegrenzung:** Sieben Tage für Inhalte reichen, weil das Ergebnis nach der Freigabe im
  KIS liegt. 183 Tage für die Spur, weil die KI-Verordnung für Protokolle mindestens sechs Monate
  verlangt; die Spur enthält keine Gesundheitsdaten.
- **Keine automatisierte Entscheidung (Art. 22):** Jeder Entwurf passiert das Gate; der Validator
  des Workflows lässt keinen Weg am Gate vorbei zu.
- **Rechte der Betroffenen:** Die Patienteninformation nach Art. 13 nennt den Einsatz. Auskunft
  (Art. 15) über die Inhalte der letzten sieben Tage gibt das Auflösen; danach sind sie geleert.
  Eine Löschung je Person vor Ablauf der Frist kann Cohereny noch nicht (CHY-239) — bei sieben
  Tagen ist die Frist kürzer als die Monatsfrist aus Art. 12(3).

## 3 · Risiken — Art. 35(7)(c)

Bewertet nach Eintrittswahrscheinlichkeit und Schwere, jeweils geringfügig, überschaubar,
substanziell oder groß. „Vorher“ ohne die Maßnahmen aus Abschnitt 4, „nachher“ mit ihnen.

| # | Risiko für die Betroffenen | Wie es eintreten könnte | Schwere | Wahrscheinlichkeit vorher | Wahrscheinlichkeit nachher |
|---|---|---|---|---|---|
| R1 | Offenlegung an einen Modellanbieter | Ein Entwurf geht an ein gehostetes Modell | groß | substanziell | geringfügig — nur Modelle mit Obergrenze `critical`; Platzhalter vor jedem Aufruf |
| R2 | Offenlegung an andere Beschäftigte | Wer nicht in der Inneren arbeitet, liest Läufe | substanziell | substanziell | geringfügig — Row-Level Security nach Bereich |
| R3 | Offenlegung durch eine Kopie der Datenbank | Ein Backup oder Dump geht verloren | groß | überschaubar | geringfügig — Platzhalter, Zuordnung verschlüsselt, Schlüssel getrennt verwahrt, Volumes verschlüsselt |
| R4 | Ein falscher Entwurf erreicht die ePA | Das Modell erfindet eine Diagnose oder Dosis | groß | substanziell | überschaubar — Freigabe durch die Ärztin ist Pflicht; das Restrisiko liegt im Übersehen |
| R5 | Verwechslung von Personen | Ein Platzhalter wird dem falschen Wert zugeordnet | groß | überschaubar | geringfügig — eine Nummerierung je Lauf, Zusammenführen unter Sperre, getestet; die Ärztin löst am Gate auf |
| R6 | Ein Name, den niemand nennt, wird gespeichert | Der Brief erwähnt einen Nachbarn, den das KIS nicht kennt | überschaubar | substanziell | überschaubar — er bleibt im Haus (lokales Modell), steht aber sieben Tage im Klartext im Lauf |
| R7 | Unbefugtes Auflösen | Jemand mit Zugang liest Werte ohne Anlass | substanziell | überschaubar | geringfügig — nur Starter und `reviewer`; jedes Auflösen im Audit, monatlich ausgewertet |
| R8 | Missbrauch privilegierter Zugänge | Wer Schlüssel und Datenbankzugang hält, liest alles | groß | geringfügig | geringfügig — getrennte Verwahrung, kein Dienstkonto hält beides |
| R9 | Löschersuchen nicht erfüllbar | Eine Patientin verlangt Löschung | überschaubar | überschaubar | geringfügig — Frist sieben Tage |

## 4 · Maßnahmen — Art. 35(7)(d)

**Was Cohereny mitbringt**, mit dem Test, der jede Maßnahme scheitern lässt, steht in
[DATA-PROTECTION.md, Abschnitt 4](DATA-PROTECTION.md#4--measures--art-357d). Für diesen Fall
tragen davon vor allem:

- Platzhalter vor jedem Modellaufruf des Workflows und in seinen gespeicherten Läufen; die Zuordnung je Lauf
  verschlüsselt und an ihren Lauf gebunden (R1, R3, R5)
- Datenklasse des Bereichs mit Obergrenze je Modell; ein nicht eingestufter Bereich gilt als
  `critical` (R1)
- Row-Level Security für Läufe; Auflösen nur für Starter und ab `reviewer`, jedes Mal im Audit (R2, R7)
- Pflicht-Gate in jedem Workflow; Ablehnung beendet den Lauf (R4)
- Fristen je Bereich und ein Löschlauf, der sie anwendet (R6, R9)

**Was das Klinikum festlegt** (organisatorisch und in der Konfiguration):

| Maßnahme | gegen |
|---|---|
| Bereich „Innere Medizin“ mit Datenklasse `critical`; nur das Modell auf eigenen Servern mit Obergrenze `critical` | R1 |
| Das KIS nennt alle Namen, die es zum Fall kennt, unter `identifiers_to_redact`; ein `redaction_rule`-Block nennt die Beschäftigten und das Format der Fallnummern | R1, R6 |
| Mitglieder des Bereichs sind die Ärztinnen und Ärzte der Klinik; Freigeben ab Rolle `reviewer`; das KIS-Konto in `chy-service-accounts` | R2, R7 |
| Inhaltsfrist sieben Tage, Spur 183 Tage; `cc oversight expire` und `cc retention purge` nächtlich, Alarm, wenn der letzte Lauf älter als 36 Stunden ist | R6, R9 |
| Schlüssel für die Zuordnungen im Secret Store, Kopie offline beim Datenschutz; nicht im Datenbank-Backup | R3, R8 |
| Monatliche Auswertung der Audit-Einträge `workflow.run_content_resolved` durch die Datenschutzbeauftragte | R7 |
| Schulung: Der Entwurf ist kein Befund; wer freigibt, prüft Diagnosen und Dosierungen gegen den Brief | R4 |
| Keine Patientendaten in Bausteinen — Bausteine liest jede angemeldete Person (CHY-237) | R2 |

## 5 · Restrisiken und Ergebnis

| Restrisiko | Bewertung | Umgang |
|---|---|---|
| Namen, die niemand nennt, stehen bis zu sieben Tage im Klartext im Lauf (R6) | überschaubar | Hingenommen: Sie verlassen das Haus nicht, die Frist ist kurz, und das KIS nennt, was es weiß. Namenserkennung ohne Liste kommt mit CHY-235 |
| Ein übersehener Fehler im freigegebenen Entwurf (R4) | überschaubar | Hingenommen mit Schulung und Stichproben der Freigaben durch den Chefarzt |
| Wer Datenbank- und Schlüsselzugang zugleich hat, liest alles (R8) | geringfügig | Getrennte Verwahrung; die Inhaber sind benannt |
| Bausteine lesbar für alle Angemeldeten (CHY-237) | geringfügig | Keine Personendaten in Bausteinen |

**Ergebnis (im Beispiel):** Nach den Maßnahmen bleibt kein hohes Risiko. Eine vorherige
Konsultation der Aufsichtsbehörde nach Art. 36 ist nicht erforderlich. Diese Entscheidung trifft
der Verantwortliche; ändert sich der Einsatz — ein gehostetes Modell, eine längere Frist, ein
weiterer Bereich —, ist die Bewertung neu zu treffen.

| | |
|---|---|
| Stellungnahme der Datenschutzbeauftragten (Art. 35(2)) | Zugestimmt mit der Auflage, die Auswertung der Auflöse-Einträge monatlich zu dokumentieren |
| Standpunkt der Betroffenen (Art. 35(9)) | Patientenfürsprecherin angehört; ihr Hinweis, den Einsatz in der Patienteninformation zu nennen, ist umgesetzt |
| Freigabe | (Name, Funktion, Datum) |
| Nächste Überprüfung | (Datum, spätestens ein Jahr nach Freigabe) |

## 6 · Was das Beispiel an der Vorlage geprüft hat

Die Vorlage trägt den Fall: Jeder Abschnitt dieser DSFA ließ sich aus
[DATA-PROTECTION.md](DATA-PROTECTION.md) füllen, die Maßnahmen samt Tests übernehmen und die
Checkliste abarbeiten. Drei Dinge hat das Beispiel gezeigt:

1. **Ein Name, den niemand nennt, wird auch gespeichert, nicht nur ans Modell geschickt.** Die
   Vorlage sagte das nur für das Modell. Berichtigt in DATA-PROTECTION.md, SECURITY.md und
   README.md, zusammen mit diesem Beispiel.
2. **Ein technisches Konto als Starter darf auflösen.** Das ist gewollt — das KIS braucht die
   Werte für die ePA —, und es heißt: Wer dieses Konto bedienen kann, kann die Werte jedes Laufs
   lesen, den es gestartet hat. Die Vorlage nennt es jetzt bei den Restrisiken.
3. **Die Bewertung von Wahrscheinlichkeit und Schwere** bleibt beim Verantwortlichen; die Vorlage
   liefert die Risiken, nicht die Stufen. So gewollt: Dieselbe Software trägt in einem Lernpilot
   ein anderes Risiko als in einer Klinik.
