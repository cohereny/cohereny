# Model Drift — Beobachtungen & Adaptionen

Dieses Dokument sammelt konkrete Beispiele für **Model Drift**: Situationen, in denen
ein Prompt auf einem Modell ein anderes Verhalten erzeugt als erwartet — und wie der
Prompt adaptiert werden muss, um das gewünschte Ergebnis zu bekommen.

Das ist das Rohmaterial für Cohereny's **Adaptionsschicht** (TranslationRule,
CalibrationAnchor). Jeder Eintrag hier ist ein potentieller `ContextBlock` vom Typ
`calibration_anchor`.

---

## Drift #001 — Node 9 Wissensprüfung: gemma4:e4b antwortet meta statt direkt

**Datum:** 2026-06-26
**Pilot:** Study Workflow
**Node:** Node 9 — Wissensprüfung
**Modell:** `gemma4:e4b` (Reasoning-Modell, MacBook Air M2)

### Was das Prompt verlangt

```
System: Du bewertest Lernfortschritt anhand von Fakten und Fragen. Sei streng aber fair.
Human:  Lernfragen: [5 Fragen]
        Verfügbare Fakten: [...]
        Beantworte jede Frage kurz (1–2 Sätze).
        Schreibe am Ende: LERNZIEL ERREICHT oder LERNZIEL NICHT ERREICHT
```

**Erwartetes Verhalten:** Modell beantwortet jede der 5 Fragen einzeln, dann Urteil.

### Was gemma4:e4b tatsächlich tut

Das Modell ignoriert "Beantworte jede Frage" und liefert stattdessen einen
**Meta-Kommentar über die Qualität der Fragen**:

```
Die Fragen decken ein sehr breites Spektrum [des Fachgebiets] ab und
erfordern sowohl das Wissen um definierte Fakten als auch die Fähigkeit, diese
in einem systemischen Kontext anzuwenden...

LERNZIEL ERREICHT
```

### Ursache

`gemma4:e4b` ist ein **Reasoning-Modell** — es gewichtet das Systemrollen-Wort
"bewertest" stärker als die konkreten Aufgabenanweisungen im Human-Turn.
Das Modell interpretiert seine Rolle als "Evaluator von Lernmaterial"
und nicht als "Student der die Fragen beantwortet".

Das gleiche Prompt auf `gemma3:4b` würde wahrscheinlich die Fragen direkt beantworten,
weil `gemma3:4b` weniger Reasoning-Overhead hat und die Anweisung "Beantworte..."
direkter ausführt.

### Benötigte Adaption für gemma4:e4b

Das Systemrollen-Framing muss für Reasoning-Modelle **explizit den Ausführungs-Modus
verankern** — nicht die Rolle beschreiben, sondern die Handlung:

```
# Statt:
System: Du bewertest Lernfortschritt anhand von Fakten und Fragen.

# Besser für gemma4:e4b:
System: Du bist ein Lernender, der eine Prüfung ablegt.
        Beantworte jede Frage direkt und kurz (1–2 Sätze).
        Kein Kommentar über die Qualität der Fragen.
        Nur Antworten, dann das Urteil.
```

**Status:** Implementiert — PR #8 (dev→main), YouTrack CTX-32

### Cohereny-Relevanz

Dieses Beispiel zeigt das Kernproblem das Cohereny löst:

- **Auf gemma3:4b** würde das Prompt wahrscheinlich direkte Antworten erzeugen
- **Auf gemma4:e4b** (Reasoning-Modell) erzeugt es Meta-Kommentar
- **Die Lösung:** Eine `TranslationRule` die das Systemrollen-Framing für
  `model_family: gemma4` automatisch anpasst

In Cohereny würde das so aussehen:
```yaml
# ContextBlock: calibration_anchor
block_type: translation_rule
name: gemma4-no-meta-commentary
scope: global
payload:
  applies_to_model: gemma4:e4b
  task_type: evaluate
  pattern: "Du bewertest {task}..."
  replacement: "Du führst {task} direkt aus. Kein Meta-Kommentar..."
```

---

## Vorlage für neue Einträge

```markdown
## Drift #00N — [Kurztitel]

**Datum:** YYYY-MM-DD
**Pilot:** [Workflow-Name]
**Node:** [Node X — Name]
**Modell:** [model:tag]

### Was das Prompt verlangt
[Prompt-Auszug]

### Was das Modell tatsächlich tut
[Beobachtetes Output]

### Ursache
[Warum verhält sich das Modell so]

### Benötigte Adaption
[Wie muss der Prompt für dieses Modell angepasst werden]

**Status:** offen / implementiert (PR #N) / als ContextBlock (ID: ...)

### cohereny-Relevanz
[Welcher ContextBlock-Typ würde das lösen]
```
