import threading
import time
from typing import Callable

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.postgres import PostgresSaver
from langfuse.langchain import CallbackHandler

from .config import POSTGRES_URI
from .state import StudyState
from .nodes import (
    node_ingest, node_summarize, node_extract_facts,
    node_mindmap, node_audio_script, node_coverage_check,
    node_gap_analysis, node_quiz, node_evaluate,
    route_after_quality, route_after_evaluate,
    set_cancel_event, check_cancel, set_emit, set_run_context,
)


def _build() -> StateGraph:
    g = StateGraph(StudyState)

    g.add_node("ingest", node_ingest)
    g.add_node("summarize", node_summarize)
    g.add_node("extract_facts", node_extract_facts)
    g.add_node("mindmap", node_mindmap)
    g.add_node("audio_script", node_audio_script)
    g.add_node("quality_check", node_coverage_check)
    g.add_node("gap_analysis", node_gap_analysis)
    g.add_node("quiz", node_quiz)
    g.add_node("evaluate", node_evaluate)

    g.set_entry_point("ingest")

    g.add_edge("ingest", "summarize")
    g.add_edge("summarize", "extract_facts")
    g.add_edge("extract_facts", "mindmap")
    g.add_edge("mindmap", "audio_script")
    g.add_edge("audio_script", "quality_check")

    g.add_conditional_edges(
        "quality_check",
        route_after_quality,
        {"gap_analysis": "gap_analysis", "quiz": "quiz"},
    )
    g.add_edge("gap_analysis", "summarize")

    g.add_edge("quiz", "evaluate")
    g.add_conditional_edges(
        "evaluate",
        route_after_evaluate,
        {"quiz": "quiz", "__end__": END},
    )

    return g


def _initial_state(pdf_path: str, model_map: dict[str, str] | None = None) -> StudyState:
    return {
        "pdf_path": pdf_path,
        "model_map": model_map or {},
        "chunks": [],
        "zusammenfassung": "",
        "fakten": [],
        "mindmap": "",
        "hoerbuch_skript": "",
        "quality_complete": False,
        "quality_gaps": [],
        "lernfragen": [],
        "wissenspruefung": "",
        "lernziel_erreicht": False,
        "iteration_count": 0,
        "timings": {"nodes": []},
    }


def _node_details(node_name: str, update: dict, state: dict) -> str:
    """Human-readable summary for the SSE event after a node completes."""
    if node_name == "ingest":
        return f"{len(update.get('chunks', []))} Chunks geladen"
    if node_name == "summarize":
        text = update.get("zusammenfassung", "")
        return f"Zusammenfassung — {len(text):,} Zeichen"
    if node_name == "extract_facts":
        n = len(update.get("fakten", state.get("fakten", [])))
        return f"{n} Fakten extrahiert"
    if node_name == "mindmap":
        return "Mindmap generiert"
    if node_name == "audio_script":
        text = update.get("hoerbuch_skript", "")
        return f"Hörbuch-Skript — {len(text):,} Zeichen"
    if node_name == "quality_check":
        score = update.get("quality_score")
        complete = update.get("quality_complete", False)
        gaps = update.get("quality_gaps", [])
        score_str = f" (Score {score}/5)" if score is not None else ""
        if complete:
            return f"Qualität OK{score_str}"
        return f"{len(gaps)} Lücke(n) gefunden{score_str}"
    if node_name == "gap_analysis":
        return "Gap-Analyse abgeschlossen"
    if node_name == "quiz":
        n = len(update.get("lernfragen", []))
        return f"{n} Lernfragen generiert"
    if node_name == "evaluate":
        reached = update.get("lernziel_erreicht", False)
        return "Lernziel erreicht" if reached else "Lernziel noch nicht erreicht"
    return ""


def _node_preview(node_name: str, update: dict, state: dict) -> str | None:
    """First few lines of relevant output for display in the run detail view."""
    if node_name == "summarize":
        text = update.get("zusammenfassung", "")
        return text[:400] + ("…" if len(text) > 400 else "")
    if node_name == "extract_facts":
        facts = update.get("fakten", state.get("fakten", []))
        return "\n".join(f"{i + 1}. {f}" for i, f in enumerate(facts[:5]))
    if node_name == "quality_check":
        gaps = update.get("quality_gaps", [])
        return "\n".join(f"- {g}" for g in gaps[:5]) if gaps else None
    return None


def run_workflow(pdf_path: str, thread_id: str = "run-1",
                 model_map: dict[str, str] | None = None) -> StudyState:
    """Direct invocation, used from the CLI and from scripts.

    `model_map` is optional here and mandatory in practice for a run started
    through the API: without it the nodes fall back to their module constants,
    which is fine for a local experiment and would be a false record for a run
    whose effective_config claims otherwise. Which is why the API always
    passes it and a test asserts the seeded map covers every tier.
    """
    langfuse = CallbackHandler()
    builder = _build()

    with PostgresSaver.from_conn_string(POSTGRES_URI) as checkpointer:
        checkpointer.setup()
        graph = builder.compile(checkpointer=checkpointer)
        config = {"configurable": {"thread_id": thread_id}, "callbacks": [langfuse]}
        return graph.invoke(_initial_state(pdf_path, model_map), config=config)


def stream_workflow(
    pdf_path: str,
    thread_id: str,
    on_event: Callable[[dict], None],
    cancel_event: threading.Event | None = None,
    run_id: str | None = None,
    db_dsn: str | None = None,
    model_map: dict[str, str] | None = None,
) -> StudyState:
    """Run the workflow and call on_event(dict) after each node completes.

    Events emitted:
      {"type": "node_complete", "node": str, "elapsed_ms": int,
       "details": str, "iteration": int, "preview": str | None}

    cancel_event: optional threading.Event; when set, the workflow raises
    WorkflowCancelledError at the next node boundary (or between batches in
    long nodes). The background thread then closes its httpx connections,
    which causes Ollama on the inference host to see a TCP reset and stop generating.

    Returns the final accumulated state (equivalent to run_workflow's return value).
    Called from a background thread (api/v1/pilots.py _bg_run).
    """
    # The caller's DSN wins over the module constant (CHY-96). POSTGRES_URI is
    # read from the environment at import time, which is why the API used to
    # write it into os.environ before importing this module — a global mutation
    # from inside a request, where the first run silently won for every later
    # one. Passing it makes the dependency visible and the process untouched.
    dsn = db_dsn or POSTGRES_URI

    set_cancel_event(cancel_event)
    set_emit(on_event)
    set_run_context(run_id, db_dsn)
    langfuse = CallbackHandler()
    builder = _build()

    try:
        with PostgresSaver.from_conn_string(dsn) as checkpointer:
            checkpointer.setup()
            graph = builder.compile(checkpointer=checkpointer)
            config = {"configurable": {"thread_id": thread_id}, "callbacks": [langfuse]}

            state: dict = dict(_initial_state(pdf_path, model_map))
            t_prev = time.perf_counter()

            for event in graph.stream(_initial_state(pdf_path, model_map), config=config, stream_mode="updates"):
                check_cancel()  # node-boundary cancel check
                t_now = time.perf_counter()
                for node_name, update in event.items():
                    elapsed_ms = round((t_now - t_prev) * 1000)
                    t_prev = t_now
                    state.update(update)
                    on_event({
                        "type": "node_complete",
                        "node": node_name,
                        "elapsed_ms": elapsed_ms,
                        "details": _node_details(node_name, update, state),
                        "iteration": state.get("iteration_count", 0),
                        "preview": _node_preview(node_name, update, state),
                    })

            return state
    finally:
        set_cancel_event(None)
        set_emit(None)
        set_run_context(None, None)
