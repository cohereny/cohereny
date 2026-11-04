import threading
import time
from typing import Callable

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.postgres import PostgresSaver
from langfuse.langchain import CallbackHandler

from .config import BANKING_LANGFUSE_PUBLIC_KEY, POSTGRES_URI
from .state import BankingState
from .nodes import (
    node_ingest, node_extract_kpis, node_calculate_ratios,
    node_detect_anomalies, node_risk_gateway,
    node_classify_risk, node_compliance_check,
    node_human_oversight, node_summarize,
    route_after_gateway,
    set_cancel_event, check_cancel, set_emit,
    set_oversight_gate, set_oversight_prompt, set_node_metrics,
    WorkflowCancelledError,
)


def _build() -> StateGraph:
    g = StateGraph(BankingState)

    g.add_node("ingest",            node_ingest)
    g.add_node("extract_kpis",      node_extract_kpis)
    g.add_node("calculate_ratios",  node_calculate_ratios)
    g.add_node("detect_anomalies",  node_detect_anomalies)
    g.add_node("risk_gateway",      node_risk_gateway)
    g.add_node("classify_risk",     node_classify_risk)
    g.add_node("compliance_check",  node_compliance_check)
    g.add_node("human_oversight",   node_human_oversight)
    g.add_node("summarize",         node_summarize)

    g.set_entry_point("ingest")

    g.add_edge("ingest",           "extract_kpis")
    g.add_edge("extract_kpis",     "calculate_ratios")
    g.add_edge("calculate_ratios", "detect_anomalies")
    g.add_edge("detect_anomalies", "risk_gateway")

    # Conditional routing after gateway:
    #   full_analysis → classify_risk → compliance_check → human_oversight → summarize
    #   fast_track    → summarize directly (no anomalies)
    g.add_conditional_edges(
        "risk_gateway",
        route_after_gateway,
        {"full_analysis": "classify_risk", "fast_track": "summarize"},
    )
    g.add_edge("classify_risk",    "compliance_check")
    g.add_edge("compliance_check", "human_oversight")
    g.add_edge("human_oversight",  "summarize")
    g.add_edge("summarize",        END)

    return g


def _initial_state(pdf_path: str, model_map: dict[str, str] | None = None) -> BankingState:
    return {
        "model_map": model_map or {},
        "pdf_path": pdf_path,
        "document_text": "",
        "document_type": "",
        "company_name": "",
        "fiscal_year": "",
        "kpis": {},
        "ratios": {},
        "anomalies": [],
        "routing_decision": "",
        "risk_level": "",
        "risk_reasoning": "",
        "compliance_flags": [],
        "oversight_required": False,
        "oversight_note": "",
        "summary": "",
        "audit_log": [],
        "ingress_findings": [],
        "timings": {"nodes": []},
    }


def _node_details(node_name: str, update: dict, state: dict) -> str:
    """Human-readable detail line for SSE node_complete events."""
    if node_name == "ingest":
        return f"Dokumenttyp: {update.get('document_type', '?')} — {update.get('company_name', '?')}"
    if node_name == "extract_kpis":
        kpis = update.get("kpis", {})
        bs = kpis.get("bilanzsumme_current", 0)
        return f"Bilanzsumme: {bs:,.0f} EUR" if bs else "KPIs extrahiert"
    if node_name == "calculate_ratios":
        r = update.get("ratios", {})
        return (
            f"EK-Quote: {r.get('ek_quote', 0):.1%} | "
            f"Current Ratio: {r.get('current_ratio', 0):.2f} | "
            f"KV YoY: {r.get('kv_veraenderung_yoy', 0):+.1%}"
        )
    if node_name == "detect_anomalies":
        n = len(update.get("anomalies", []))
        return f"{n} Auffälligkeit(en) erkannt"
    if node_name == "risk_gateway":
        dec = update.get("routing_decision", "?")
        return f"Routing: {dec}"
    if node_name == "classify_risk":
        return f"Risikoklasse: {update.get('risk_level', '?')}"
    if node_name == "compliance_check":
        n = len(update.get("compliance_flags", []))
        return f"{n} Compliance-Flag(s)"
    if node_name == "human_oversight":
        return "Human Oversight Gate: Freigabe erforderlich (EU AI Act Art. 14)"
    if node_name == "summarize":
        text = update.get("summary", "")
        return f"Executive Summary — {len(text):,} Zeichen"
    return ""


def _node_preview(node_name: str, update: dict, state: dict) -> str | None:
    """Short preview text for the run detail log."""
    if node_name == "calculate_ratios":
        r = update.get("ratios", {})
        lines = [
            f"EK-Quote:         {r.get('ek_quote', 0):.1%}",
            f"Current Ratio:    {r.get('current_ratio', 0):.2f}",
            f"Verschuldungsgrad:{r.get('verschuldungsgrad', 0):.2f}x",
            f"EBITDA-Marge:     {r.get('ebitda_marge', 0):.1%}",
            f"KV-Änderung YoY:  {r.get('kv_veraenderung_yoy', 0):+.1%}",
        ]
        return "\n".join(lines)
    if node_name == "detect_anomalies":
        anomalies = update.get("anomalies", [])
        return "\n".join(f"• {a[:120]}" for a in anomalies[:4]) or None
    if node_name == "human_oversight":
        note = update.get("oversight_note", "")
        return note[:500] if note else None
    if node_name == "summarize":
        text = update.get("summary", "")
        return text[:400] + ("…" if len(text) > 400 else "") if text else None
    return None


def run_workflow(
    pdf_path: str,
    thread_id: str = "banking-run-1",
    oversight_prompt: Callable[[], str] | None = None,
    model_map: dict[str, str] | None = None,
) -> BankingState:
    """Run the banking workflow to completion.

    oversight_prompt is called when node_human_oversight is reached and must
    return "approved" or "rejected". Without it the run fails at the gate —
    there is no automatic approval (ADR-004, CHY-83). Callers that have a
    human at the keyboard should pass an interactive prompt; callers driven by
    the API use stream_workflow with an event-based gate instead.
    """
    # Langfuse v4: CallbackHandler reads credentials from env vars (LANGFUSE_PUBLIC_KEY,
    # LANGFUSE_SECRET_KEY, LANGFUSE_HOST). public_key here selects the banking project.
    langfuse = CallbackHandler(public_key=BANKING_LANGFUSE_PUBLIC_KEY)
    builder = _build()
    set_oversight_gate(None, None)
    set_oversight_prompt(oversight_prompt)
    try:
        with PostgresSaver.from_conn_string(POSTGRES_URI) as checkpointer:
            checkpointer.setup()
            graph = builder.compile(checkpointer=checkpointer)
            config = {"configurable": {"thread_id": thread_id}, "callbacks": [langfuse]}
            return graph.invoke(_initial_state(pdf_path, model_map), config=config)
    finally:
        set_oversight_prompt(None)


def stream_workflow(
    pdf_path: str,
    thread_id: str,
    on_event: Callable[[dict], None],
    cancel_event: threading.Event | None = None,
    oversight_gate: tuple[threading.Event, list] | None = None,
    node_metrics: bool = False,
    db_dsn: str | None = None,
    model_map: dict[str, str] | None = None,
) -> BankingState:
    """Run the banking workflow, calling on_event after each node completes.

    oversight_gate is a (threading.Event, result_holder) pair. When provided,
    node_human_oversight blocks on the event until the API signals a decision.
    result_holder is a single-element list; the API writes "approved"/"rejected"
    into index 0 before setting the event.

    node_metrics emits node_metrics events with token usage per LLM call when True.
    It was called dev_mode until CHY-192.

    db_dsn overrides the module constant (CHY-96). POSTGRES_URI is read from
    the environment at import time, which is why the API used to write it into
    os.environ before importing this module — a global mutation from inside a
    request, where the first run silently won for every later one.
    """
    dsn = db_dsn or POSTGRES_URI

    set_cancel_event(cancel_event)
    set_emit(on_event)
    set_node_metrics(node_metrics)
    # Clear any interactive prompt left on this pooled thread by an earlier CLI
    # run — the API path must never take a decision from stdin.
    set_oversight_prompt(None)
    if oversight_gate is not None:
        set_oversight_gate(oversight_gate[0], oversight_gate[1])
    else:
        set_oversight_gate(None, None)
    langfuse = CallbackHandler(public_key=BANKING_LANGFUSE_PUBLIC_KEY)
    builder = _build()

    try:
        with PostgresSaver.from_conn_string(dsn) as checkpointer:
            checkpointer.setup()
            graph = builder.compile(checkpointer=checkpointer)
            config = {"configurable": {"thread_id": thread_id}, "callbacks": [langfuse]}

            state: dict = dict(_initial_state(pdf_path, model_map))
            t_prev = time.perf_counter()

            try:
                for event in graph.stream(_initial_state(pdf_path, model_map), config=config, stream_mode="updates"):
                    check_cancel()
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
                            "preview": _node_preview(node_name, update, state),
                        })
            except WorkflowCancelledError as exc:
                # Re-raise with partial state so pilots.py can persist it for rejected
                # runs. oversight_outcome must survive — it distinguishes a human
                # rejection from a timeout, and the audit trail needs that (CHY-83).
                raise WorkflowCancelledError(
                    str(exc),
                    partial_state=dict(state),
                    oversight_outcome=exc.oversight_outcome,
                ) from exc

            return state
    finally:
        set_cancel_event(None)
        set_emit(None)
        set_node_metrics(False)
        set_oversight_gate(None, None)
        set_oversight_prompt(None)
