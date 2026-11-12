import json
import re
import threading
import time
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage
from pypdf import PdfReader
from .config import (
    LITELLM_BASE_URL, LITELLM_API_KEY,
    MODEL_SMALL, MODEL_MEDIUM,
    CHUNK_SIZE, BATCH_SIZE, MERGE_GROUP_SIZE, MIN_FACTS,
    MAX_QUALITY_ITERATIONS, MAX_QUIZ_ITERATIONS,
    COVERAGE_THRESHOLD, QUESTION_BATCH_SIZE,
)
from .state import StudyState

#: Used only when a run carries no model_map — see _model().
_FALLBACK_MODELS = {"small": MODEL_SMALL, "medium": MODEL_MEDIUM}


def _model(state: dict, tier: str) -> str:
    """The model for this tier in this run (CHY-42).

    The run's composed `model_map` wins; the module constant is the fallback
    for a direct invocation from the CLI or a script, which has no
    configuration row behind it.

    The fallback is narrow on purpose and must never apply to a run started
    through the API: `pilot_runs.effective_config` records the map as what the
    run used, and a node quietly reading something else would make that record
    false. `tests/test_workflow_config.py` asserts the seeded map covers every
    tier these nodes ask for, so the fallback cannot be reached in production
    without a test going red first.
    """
    return (state.get("model_map") or {}).get(tier) or _FALLBACK_MODELS[tier]



# ── Cancel support ─────────────────────────────────────────────────────────────
# Each background thread stores its cancel event in thread-local storage so
# check_cancel() can be called from any node without changing signatures.

_cancel_ctx = threading.local()
_emit_ctx = threading.local()
_run_ctx = threading.local()  # run_id + db_dsn for coverage scoring (CHY-23)


class WorkflowCancelledError(RuntimeError):
    """Raised when the user explicitly cancels a running workflow."""


def set_cancel_event(event: threading.Event | None) -> None:
    _cancel_ctx.event = event


def check_cancel() -> None:
    """Raise WorkflowCancelledError if the cancel event is set."""
    event = getattr(_cancel_ctx, "event", None)
    if event is not None and event.is_set():
        raise WorkflowCancelledError("Run cancelled by user")


def set_emit(fn) -> None:
    """Store the SSE emit callback in thread-local storage."""
    _emit_ctx.fn = fn


def try_emit(event: dict) -> None:
    """Emit a progress event if an emit callback is registered."""
    fn = getattr(_emit_ctx, "fn", None)
    if fn is not None:
        fn(event)


def set_run_context(run_id: str | None, db_dsn: str | None) -> None:
    """Register the run_id and DB DSN for coverage scoring (CHY-23)."""
    _run_ctx.run_id = run_id
    _run_ctx.db_dsn = db_dsn


def _llm(model: str, temperature: float = 0.3) -> ChatOpenAI:
    return ChatOpenAI(
        base_url=LITELLM_BASE_URL,
        api_key=LITELLM_API_KEY,
        model=model,
        temperature=temperature,
    )



def _token_counts(resp) -> tuple[int | None, int | None]:
    usage = resp.response_metadata.get("token_usage") or {}
    return usage.get("prompt_tokens"), usage.get("completion_tokens")


def _hierarchical_merge(
    summaries: list[str],
    llm,
    group_size: int,
    total_prompt: int,
    total_completion: int,
) -> tuple[str, int, int]:
    """Cascade-merge a list of summaries down to one, processing group_size at a time."""
    current = summaries
    round_num = 0
    while len(current) > 1:
        round_num += 1
        groups = [current[i:i + group_size] for i in range(0, len(current), group_size)]
        next_level = []
        for gidx, group in enumerate(groups):
            if len(group) == 1:
                next_level.append(group[0])
                continue
            combined = "\n\n---\n\n".join(group)
            resp = llm.invoke([
                SystemMessage("Erstelle eine kohärente, prägnante Gesamtzusammenfassung aus diesen Teil-Zusammenfassungen. Strukturiere nach Themen, nicht nach Reihenfolge der Abschnitte."),
                HumanMessage(f"Teil-Zusammenfassungen:\n\n{combined}"),
            ])
            p, c = _token_counts(resp)
            total_prompt += p or 0
            total_completion += c or 0
            next_level.append(resp.content)
            print(f"   Merge Runde {round_num}, Gruppe {gidx + 1}/{len(groups)} ✓")
            check_cancel()
            try_emit({"type": "node_progress", "node": "summarize", "details": f"Merge Runde {round_num}, Gruppe {gidx + 1} / {len(groups)} fertig", "elapsed_ms": None})
        current = next_level
    return current[0], total_prompt, total_completion


def _append_timing(state: StudyState, entry: dict) -> dict:
    nodes = state.get("timings", {}).get("nodes", [])
    return {"nodes": nodes + [entry]}


# --- Node 1: Document Ingest (lokal, kein LLM) ---

def node_ingest(state: StudyState) -> dict:
    print("\n── Node 1/9 — Ingest ──────────────────────────────────")
    print("   PDF laden und in Chunks aufteilen ...")
    print("   Weiter: Zusammenfassung → Fakten → Mindmap → Hörbuch → Qualitätscheck")
    t0 = time.perf_counter()
    reader = PdfReader(state["pdf_path"])
    text = "\n".join(page.extract_text() or "" for page in reader.pages)

    step = CHUNK_SIZE - 100  # 100-Zeichen Overlap
    chunks = [
        text[i : i + CHUNK_SIZE]
        for i in range(0, len(text), step)
        if text[i : i + CHUNK_SIZE].strip()
    ]
    duration_ms = (time.perf_counter() - t0) * 1000

    print(f"   ✓ {len(reader.pages)} Seiten → {len(chunks)} Chunks")
    return {
        "chunks": chunks,
        "iteration_count": 0,
        "timings": _append_timing(state, {"node": "ingest", "duration_ms": round(duration_ms, 2), "model": None, "prompt_tokens": None, "completion_tokens": None}),
    }


# --- Node 2: Zusammenfassung — Map-Reduce über alle Chunks ---

def node_summarize(state: StudyState) -> dict:
    iteration = state.get("iteration_count", 0)
    chunks = state["chunks"]
    batches = [chunks[i:i + BATCH_SIZE] for i in range(0, len(chunks), BATCH_SIZE)]
    label = "Node 2/9 — Zusammenfassung" + (f" (Iteration {iteration+1})" if iteration > 0 else "")
    print(f"\n── {label} ──────────────────────────────────")
    print(f"   Modell: {_model(state, "small")} | {len(chunks)} Chunks → {len(batches)} Batches")
    print("   Weiter: Fakten-Extraktion")
    llm = _llm(_model(state, "small"))

    t0 = time.perf_counter()
    total_prompt = total_completion = 0
    partial_summaries = []

    for idx, batch in enumerate(batches):
        check_cancel()
        context = "\n\n".join(batch)
        resp = llm.invoke([
            SystemMessage("Du bist ein Lernassistent. Fasse den folgenden Textabschnitt prägnant auf Deutsch zusammen. Nur Kernaussagen, keine Meta-Kommentare über das Dokument."),
            HumanMessage(f"Fasse diesen Abschnitt zusammen:\n\n{context}"),
        ])
        p, c = _token_counts(resp)
        total_prompt += p or 0
        total_completion += c or 0
        partial_summaries.append(resp.content)
        print(f"   Batch {idx + 1}/{len(batches)} ✓")
        try_emit({"type": "node_progress", "node": "summarize", "details": f"Batch {idx + 1} / {len(batches)} fertig", "elapsed_ms": None})

    if len(partial_summaries) == 1:
        final_summary = partial_summaries[0]
    else:
        print(f"   Merge: {len(partial_summaries)} Summaries → hierarchisch à {MERGE_GROUP_SIZE}")
        final_summary, total_prompt, total_completion = _hierarchical_merge(
            partial_summaries, llm, MERGE_GROUP_SIZE, total_prompt, total_completion
        )

    duration_ms = (time.perf_counter() - t0) * 1000
    print(f"   ✓ {len(final_summary)} Zeichen")
    return {
        "zusammenfassung": final_summary,
        "timings": _append_timing(state, {"node": "summarize", "iteration": iteration, "duration_ms": round(duration_ms, 2), "model": _model(state, "small"), "prompt_tokens": total_prompt, "completion_tokens": total_completion, "batches": len(batches)}),
    }


# --- Node 3: Fakten-Extraktion — Map-Reduce über alle Chunks ---

def node_extract_facts(state: StudyState) -> dict:
    chunks = state["chunks"]
    batches = [chunks[i:i + BATCH_SIZE] for i in range(0, len(chunks), BATCH_SIZE)]
    print("\n── Node 3/9 — Fakten-Extraktion ──────────────────────────────────")
    print(f"   Modell: {_model(state, "medium")} | {len(chunks)} Chunks → {len(batches)} Batches")
    print("   Weiter: Mindmap")
    llm = _llm(_model(state, "medium"), temperature=0.2)

    t0 = time.perf_counter()
    total_prompt = total_completion = 0
    all_facts: list[str] = []

    focus = state.get("extraction_focus") or []
    focus_addition = ""
    if focus:
        focus_list = "\n".join(f"- {f}" for f in focus)
        focus_addition = (
            f"\n\nBesonders wichtig in diesem Durchlauf — diese Themen fehlen noch:\n{focus_list}"
        )

    extraction_system = (
        "Du extrahierst Fakten als nummerierte Liste.\n\n"
        "Regel: Jeder Fakt muss konkret und prüfungsrelevant sein.\n\n"
        "SCHLECHT (zu allgemein — diese Art von Fakten NICHT extrahieren):\n"
        "- Die Elektrotechnik beschreibt den Umgang mit elektrischer Energie.\n"
        "- Das Kapitel behandelt Netzwerkprotokolle und ihre Aufgaben.\n"
        "- Die Thermodynamik erklärt Energieumwandlungen.\n\n"
        "GUT (konkret, mit Werten, Mechanismen oder Unterschieden):\n"
        "- TCP baut eine Verbindung mit einem Drei-Wege-Handshake auf: SYN, SYN-ACK, ACK.\n"
        "- UDP garantiert weder Reihenfolge noch Zustellung; TCP garantiert beides.\n"
        "- Wasser siedet bei sinkendem Luftdruck früher, weil sein Dampfdruck den "
        "Umgebungsdruck dann schon bei niedrigerer Temperatur erreicht.\n"
        "- Die Kapazität eines Plattenkondensators steigt mit der Plattenfläche "
        "und sinkt mit dem Plattenabstand.\n\n"
        "Format: '1. Fakt' — eine Zeile pro Fakt. "
        "Extrahiere ALLE konkreten Fakten aus dem Abschnitt, auch wenn es viele sind."
        + focus_addition
    )

    for idx, batch in enumerate(batches):
        check_cancel()
        context = "\n\n".join(batch)
        resp = llm.invoke([
            SystemMessage(extraction_system),
            HumanMessage(f"Extrahiere alle lernrelevanten Fakten aus diesem Textabschnitt:\n\n{context}"),
        ])
        p, c = _token_counts(resp)
        total_prompt += p or 0
        total_completion += c or 0

        lines = [
            ln.strip() for ln in resp.content.split("\n")
            if ln.strip() and (ln.strip()[0].isdigit() or ln.strip().startswith("-"))
        ]
        all_facts.extend(lines)
        print(f"   Batch {idx + 1}/{len(batches)}: {len(lines)} Fakten")
        try_emit({"type": "node_progress", "node": "extract_facts", "details": f"Batch {idx + 1} / {len(batches)} — {len(lines)} Fakten", "elapsed_ms": None})

    # Deduplicate by full normalized text — only exact duplicates removed, no length limit
    seen: set[str] = set()
    unique_facts: list[str] = []
    for f in all_facts:
        key = f.lstrip("0123456789.- ").strip().lower()
        if key and key not in seen:
            seen.add(key)
            unique_facts.append(f.lstrip("0123456789. ").strip())

    duration_ms = (time.perf_counter() - t0) * 1000
    print(f"   ✓ {len(unique_facts)} einzigartige Fakten (aus {len(all_facts)} gesamt)")
    return {
        "fakten": unique_facts,
        "timings": _append_timing(state, {"node": "extract_facts", "duration_ms": round(duration_ms, 2), "model": _model(state, "medium"), "prompt_tokens": total_prompt, "completion_tokens": total_completion, "batches": len(batches)}),
    }


# --- Node 4: Mindmap (phi3:mini, small local node) — Mermaid mindmap ---

def node_mindmap(state: StudyState) -> dict:
    print("\n── Node 4/9 — Mindmap ──────────────────────────────────")
    print(f"   Modell: {_model(state, "small")} | Mermaid mindmap-Syntax")
    print("   Weiter: Hörbuch-Skript")
    llm = _llm(_model(state, "small"))
    fakten_text = "\n".join(state["fakten"][:25])

    t0 = time.perf_counter()
    resp = llm.invoke([
        SystemMessage(
            "Erstelle eine Mindmap im Mermaid-Format. Gib NUR den Mermaid-Code aus, ohne Codeblock-Marker.\n"
            "Verwende exakt diese Syntax:\n"
            "mindmap\n"
            "  root((Hauptthema))\n"
            "    Oberthema 1\n"
            "      Unterpunkt 1a\n"
            "      Unterpunkt 1b\n"
            "    Oberthema 2\n"
            "      Unterpunkt 2a\n"
            "3–5 Hauptthemen, je 2–4 Unterpunkte. Nur Mermaid-Syntax, kein Markdown, keine Erklärungen."
        ),
        HumanMessage(
            f"Zusammenfassung:\n{state['zusammenfassung'][:600]}\n\n"
            f"Wichtige Fakten:\n{fakten_text}\n\n"
            "Erstelle die Mindmap."
        ),
    ])
    duration_ms = (time.perf_counter() - t0) * 1000
    prompt_tokens, completion_tokens = _token_counts(resp)

    # Clean: strip accidental code fences, ensure starts with 'mindmap'
    content = resp.content.strip().replace("```mermaid", "").replace("```", "").strip()
    if not content.startswith("mindmap"):
        content = "mindmap\n  root((Hauptthemen))\n" + content

    print(f"   ✓ {len(content)} Zeichen (Mermaid)")
    return {
        "mindmap": content,
        "timings": _append_timing(state, {"node": "mindmap", "duration_ms": round(duration_ms, 2), "model": _model(state, "small"), "prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens}),
    }


# --- Node 5: Hörbuch-Skript (gemma4:e4b, local Apple silicon) ---

def node_audio_script(state: StudyState) -> dict:
    print("\n── Node 5/9 — Hörbuch-Skript ──────────────────────────────────")
    print(f"   Modell: {_model(state, "medium")} | Natürlicher Fließtext für Audio")
    print("   Weiter: Qualitäts-Check")
    llm = _llm(_model(state, "medium"), temperature=0.6)
    fakten_text = "\n".join(state["fakten"][:10])

    t0 = time.perf_counter()
    resp = llm.invoke([
        SystemMessage("Du schreibst natürliche, gesprochene Hörbuch-Skripte für Lerninhalte. Deutsch, klar, lehrreich."),
        HumanMessage(
            f"Zusammenfassung:\n{state['zusammenfassung'][:600]}\n\n"
            f"Fakten:\n{fakten_text}\n\n"
            "Schreibe ein 3–5-minütiges Hörbuch-Skript."
        ),
    ])
    duration_ms = (time.perf_counter() - t0) * 1000
    prompt_tokens, completion_tokens = _token_counts(resp)

    print(f"   ✓ {len(resp.content)} Zeichen")
    return {
        "hoerbuch_skript": resp.content,
        "timings": _append_timing(state, {"node": "audio_script", "duration_ms": round(duration_ms, 2), "model": _model(state, "medium"), "prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens}),
    }


# --- Node 6: Qualitäts-Check (gemma4:e4b, local Apple silicon) ---

def node_quality_check(state: StudyState) -> dict:
    iteration = state.get("iteration_count", 0)
    print("\n── Node 6/9 — Qualitäts-Check ──────────────────────────────────")
    print(f"   Modell: {_model(state, "medium")} | Abdeckung prüfen")
    print("   Weiter: Gap-Analyse (falls Lücken) ODER Lernfragen")
    llm = _llm(_model(state, "medium"), temperature=0.1)
    fakten_text = "\n".join(state["fakten"][:30])

    t0 = time.perf_counter()
    resp = llm.invoke([
        SystemMessage("Du prüfst Lernmaterialien auf Vollständigkeit. Antworte präzise."),
        HumanMessage(
            f"Extrahierte Fakten ({len(state['fakten'])} Stück):\n{fakten_text}\n\n"
            f"Mindmap (Auszug):\n{state['mindmap'][:600]}\n\n"
            f"Hörbuch-Skript (Auszug):\n{state['hoerbuch_skript'][:600]}\n\n"
            "Antworte:\n"
            "1. Erste Zeile: VOLLSTÄNDIG oder LÜCKEN\n"
            "2. Bei LÜCKEN: Liste der konkret fehlenden Themen (je eine Zeile mit '- ')"
        ),
    ])
    duration_ms = (time.perf_counter() - t0) * 1000
    prompt_tokens, completion_tokens = _token_counts(resp)

    content = resp.content
    complete = "VOLLSTÄNDIG" in content.upper() and "LÜCKEN" not in content.upper()
    gaps = [
        ln.strip().lstrip("- ").strip()
        for ln in content.split("\n")
        if ln.strip().startswith("-")
    ]

    # Force another iteration if too few facts were extracted
    if len(state["fakten"]) < MIN_FACTS:
        complete = False
        if not gaps:
            gaps = [f"Zu wenige Fakten extrahiert ({len(state['fakten'])} < {MIN_FACTS} erwartet)"]

    # Tier-2 LLM-as-Judge: rate fact specificity on 1–5 scale
    judge_llm = _llm(_model(state, "small"), temperature=0.0)
    sample = "\n".join(state["fakten"][:15])
    judge_resp = judge_llm.invoke([
        SystemMessage(
            "Bewerte die folgende Fakten-Liste nach Konkretheit und Lernrelevanz.\n"
            "Skala 1–5:\n"
            "1 = fast alles Kategoriebeschreibungen ('X ist die Lehre von Y')\n"
            "3 = Mischung aus allgemeinen und konkreten Fakten\n"
            "5 = fast alle Fakten enthalten konkrete Werte, Mechanismen oder Unterschiede\n"
            "Antworte mit genau einer Zahl (1–5) und keinem anderen Text."
        ),
        HumanMessage(f"Fakten (Stichprobe):\n{sample}"),
    ])
    try:
        quality_score = max(1, min(5, int(judge_resp.content.strip()[0])))
    except (ValueError, IndexError):
        quality_score = 0

    # If judge scores too low, add it as a gap signal
    if quality_score < 3 and not complete:
        gaps.append(
            f"Fakten zu allgemein (Qualitäts-Score {quality_score}/5) — "
            "konkretere Fakten mit Werten, Mechanismen und Unterschieden nötig"
        )

    print(f"   ✓ vollständig={complete}, score={quality_score}/5, lücken={len(gaps)}, fakten={len(state['fakten'])}")
    return {
        "quality_complete": complete,
        "quality_gaps": gaps,
        "quality_score": quality_score,
        "timings": _append_timing(state, {"node": "quality_check", "iteration": iteration, "duration_ms": round(duration_ms, 2), "model": _model(state, "medium"), "prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens}),
    }


# --- Node 7: Gap-Analyse (gemma4:e4b, local Apple silicon) ---

def node_gap_analysis(state: StudyState) -> dict:
    iteration = state.get("iteration_count", 0) + 1
    print(f"\n── Node 7/9 — Gap-Analyse (Iteration {iteration}) ──────────────────────────────────")
    print(f"   Modell: {_model(state, "medium")} | {len(state['quality_gaps'])} Lücken gefunden")
    print("   Weiter: zurück zu Zusammenfassung → Qualitäts-Check")
    llm = _llm(_model(state, "medium"))
    gaps_text = "\n".join(f"- {g}" for g in state["quality_gaps"])

    t0 = time.perf_counter()
    resp = llm.invoke([
        SystemMessage("Du analysierst Lücken in Lernmaterialien und erstellst Ergänzungstexte."),
        HumanMessage(
            f"Diese Themen fehlen:\n{gaps_text}\n\n"
            "Schreibe einen kurzen Ergänzungstext (2–3 Absätze) der diese Lücken schließt."
        ),
    ])
    duration_ms = (time.perf_counter() - t0) * 1000
    prompt_tokens, completion_tokens = _token_counts(resp)

    erweiterung = state["zusammenfassung"] + "\n\n---\nErgänzung (Gap-Analyse):\n" + resp.content

    # Pass quality_gaps directly as extraction_focus for next node_extract_facts call
    extraction_focus = list(state.get("quality_gaps") or [])

    print(f"   ✓ Ergänzungstext erstellt | {len(extraction_focus)} Fokus-Themen für nächste Extraktion")
    return {
        "zusammenfassung": erweiterung,
        "extraction_focus": extraction_focus,
        "iteration_count": iteration,
        "timings": _append_timing(state, {"node": "gap_analysis", "iteration": iteration, "duration_ms": round(duration_ms, 2), "model": _model(state, "medium"), "prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens}),
    }


# --- Node 8: Lernfragen (phi3:mini, small local node) ---

def node_quiz(state: StudyState) -> dict:
    """Generate quiz questions from extracted facts.

    If quality_gaps contains pre-defined exam questions from the question_bank
    (CHY-23), uses those directly instead of asking the LLM to generate new ones.
    """
    iteration = state.get("iteration_count", 0)
    gaps = state.get("quality_gaps", [])
    print("\n── Node 8/9 — Lernfragen ──────────────────────────────────")

    t0 = time.perf_counter()

    # CHY-23: reuse uncovered exam questions instead of generating new ones
    if gaps:
        print(f"   Quelle: question_bank | {len(gaps)} offene Fragen übernommen")
        fragen = gaps[:5]
        duration_ms = (time.perf_counter() - t0) * 1000
        return {
            "lernfragen": fragen,
            "timings": _append_timing(state, {"node": "quiz", "iteration": iteration, "duration_ms": round(duration_ms, 2), "model": "none", "prompt_tokens": 0, "completion_tokens": 0}),
        }

    print(f"   Modell: {_model(state, "small")} | 5 Prüfungsfragen generieren")
    print("   Weiter: Wissensprüfung")
    llm = _llm(_model(state, "small"))
    fakten_text = "\n".join(state["fakten"][:10])

    resp = llm.invoke([
        SystemMessage("Du erstellst Prüfungsfragen. Nummerierte Liste, konkrete Fragen."),
        HumanMessage(f"Erstelle 5 Lernfragen zu diesen Fakten:\n\n{fakten_text}"),
    ])
    duration_ms = (time.perf_counter() - t0) * 1000
    prompt_tokens, completion_tokens = _token_counts(resp)

    lines = [ln.strip() for ln in resp.content.split("\n") if ln.strip() and ln.strip()[0].isdigit()]
    fragen = lines if len(lines) >= 3 else resp.content.split("\n")[:5]
    print(f"   ✓ {len(fragen)} Fragen generiert")
    return {
        "lernfragen": fragen,
        "timings": _append_timing(state, {"node": "quiz", "iteration": iteration, "duration_ms": round(duration_ms, 2), "model": _model(state, "small"), "prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens}),
    }


# --- Node 9: Wissensprüfung (gemma4:e4b, local Apple silicon) ---

def node_evaluate(state: StudyState) -> dict:
    iteration = state.get("iteration_count", 0)
    print("\n── Node 9/9 — Wissensprüfung ──────────────────────────────────")
    print(f"   Modell: {_model(state, "medium")} | Lernziel bewerten")
    print("   Weiter: Fertig (falls Lernziel erreicht) ODER nochmal Lernfragen")
    llm = _llm(_model(state, "medium"), temperature=0.1)
    fragen_text = "\n".join(state["lernfragen"])
    fakten_text = "\n".join(state["fakten"][:10])

    t0 = time.perf_counter()
    resp = llm.invoke([
        SystemMessage(
            "Du bist ein Lernender, der eine Prüfung ablegt. "
            "Beantworte jede Frage direkt und kurz (1–2 Sätze). "
            "Kein Kommentar über die Qualität der Fragen — nur Antworten, dann das Urteil. "
            "Schreibe ausschließlich normalen Fließtext — kein LaTeX, keine Sonderzeichen wie $, \\, →."
        ),
        HumanMessage(
            f"Lernfragen:\n{fragen_text}\n\n"
            f"Verfügbare Fakten:\n{fakten_text}\n\n"
            "Beantworte jede Frage kurz (1–2 Sätze).\n"
            "Schreibe am Ende exakt eine dieser Zeilen:\n"
            "LERNZIEL ERREICHT\n"
            "LERNZIEL NICHT ERREICHT"
        ),
    ])
    duration_ms = (time.perf_counter() - t0) * 1000
    prompt_tokens, completion_tokens = _token_counts(resp)

    content = resp.content
    erreicht = "LERNZIEL ERREICHT" in content and "NICHT ERREICHT" not in content
    print(f"   ✓ Lernziel erreicht: {erreicht}")
    return {
        "wissenspruefung": content,
        "lernziel_erreicht": erreicht,
        "iteration_count": iteration + 1,
        "timings": _append_timing(state, {"node": "evaluate", "iteration": iteration, "duration_ms": round(duration_ms, 2), "model": _model(state, "medium"), "prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens}),
    }


# --- Conditional Edges ---

def route_after_quality(state: StudyState) -> str:
    if state["quality_complete"] or state.get("iteration_count", 0) >= MAX_QUALITY_ITERATIONS:
        return "quiz"
    return "gap_analysis"


def route_after_evaluate(state: StudyState) -> str:
    if state["lernziel_erreicht"] or state.get("iteration_count", 0) >= MAX_QUIZ_ITERATIONS + MAX_QUALITY_ITERATIONS:
        return "__end__"
    return "quiz"


# --- CHY-23: Question Bank Coverage Scoring ───────────────────────────────────

def _load_questions(db_dsn: str, limit: int = QUESTION_BATCH_SIZE) -> list[dict]:
    """Return questions from question_bank ordered randomly. Returns [] on any error."""
    try:
        import psycopg  # lazy import — mocked in unit tests
        with psycopg.connect(db_dsn) as conn:
            rows = conn.execute(
                "SELECT id::text, question FROM question_bank ORDER BY random() LIMIT %s",
                (limit,),
            ).fetchall()
        return [{"id": r[0], "question": r[1]} for r in rows]
    except Exception:
        return []


def _write_coverage(db_dsn: str, run_id: str, questions: list[dict], covered_indices: set[int]) -> None:
    """Persist per-question coverage to run_coverage. Best-effort — never raises."""
    try:
        import psycopg
        with psycopg.connect(db_dsn) as conn:
            for idx, q in enumerate(questions, start=1):
                conn.execute(
                    "INSERT INTO run_coverage (run_id, question_id, covered) "
                    "VALUES (%s::uuid, %s::uuid, %s) "
                    "ON CONFLICT (run_id, question_id) DO UPDATE SET covered = EXCLUDED.covered",
                    (run_id, q["id"], idx in covered_indices),
                )
    except Exception:
        pass


def _parse_covered_indices(text: str, count: int) -> set[int]:
    """Parse a comma-separated or JSON-array list of 1-based question numbers from LLM output."""
    # Try JSON array first
    m = re.search(r"\[([0-9,\s]+)\]", text)
    if m:
        try:
            nums = json.loads("[" + m.group(1) + "]")
            return {int(n) for n in nums if 1 <= int(n) <= count}
        except (json.JSONDecodeError, ValueError):
            pass
    # Fall back to all bare integers in the text
    return {int(n) for n in re.findall(r"\b(\d+)\b", text) if 1 <= int(n) <= count}


def node_coverage_check(state: StudyState) -> dict:
    """Coverage check against the question_bank (CHY-23).

    Loads up to QUESTION_BATCH_SIZE exam questions from the DB,
    asks the LLM which of them are addressed by the extracted facts, and
    computes a coverage ratio. Falls back to node_quality_check when the
    question_bank is empty (e.g. before the ingest script has been run).
    """
    iteration = state.get("iteration_count", 0)
    db_dsn = getattr(_run_ctx, "db_dsn", None)
    run_id = getattr(_run_ctx, "run_id", None)

    questions = _load_questions(db_dsn) if db_dsn else []

    if not questions:
        # Fallback: original self-referential quality check
        print("\n── Node 6/9 — Coverage-Check (Fallback: Qualitäts-Check) ──")
        return node_quality_check(state)

    print("\n── Node 6/9 — Coverage-Check (question_bank) ──────────────────────────────────")
    print(f"   {len(questions)} Fragen geladen | Schwellenwert {COVERAGE_THRESHOLD:.0%}")
    print("   Weiter: Gap-Analyse (falls Lücken) ODER Lernfragen")

    fakten_text = "\n".join(state["fakten"][:30])
    numbered = "\n".join(f"{i}. {q['question']}" for i, q in enumerate(questions, start=1))

    t0 = time.perf_counter()
    llm = _llm(_model(state, "medium"), temperature=0.0)
    resp = llm.invoke([
        SystemMessage(
            "Du prüfst, welche Prüfungsfragen durch eine Faktenliste abgedeckt werden. "
            "Antworte ausschließlich mit einer kommaseparierten Liste der Fragenummern, "
            "die abgedeckt sind, z.B.: 1,3,7,12. Keine Erklärungen."
        ),
        HumanMessage(
            f"Extrahierte Fakten:\n{fakten_text}\n\n"
            f"Prüfungsfragen:\n{numbered}\n\n"
            "Welche Fragenummern werden durch die Fakten abgedeckt?"
        ),
    ])
    duration_ms = (time.perf_counter() - t0) * 1000
    prompt_tokens, completion_tokens = _token_counts(resp)

    covered = _parse_covered_indices(resp.content, len(questions))
    coverage_ratio = len(covered) / len(questions)
    complete = coverage_ratio >= COVERAGE_THRESHOLD

    uncovered = [q for i, q in enumerate(questions, start=1) if i not in covered]
    gaps = [q["question"] for q in uncovered[:10]]

    if run_id and db_dsn:
        _write_coverage(db_dsn, run_id, questions, covered)

    if len(state["fakten"]) < MIN_FACTS:
        complete = False
        if not gaps:
            gaps = [f"Zu wenige Fakten extrahiert ({len(state['fakten'])} < {MIN_FACTS} erwartet)"]

    quality_score = min(5, round(coverage_ratio * 5))
    print(f"   ✓ Abdeckung={coverage_ratio:.0%} ({len(covered)}/{len(questions)}), vollständig={complete}")

    return {
        "quality_complete": complete,
        "quality_gaps": gaps,
        "quality_score": quality_score,
        "timings": _append_timing(state, {
            "node": "quality_check",
            "iteration": iteration,
            "duration_ms": round(duration_ms, 2),
            "model": _model(state, "medium"),
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "coverage_ratio": round(coverage_ratio, 3),
        }),
    }
