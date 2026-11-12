"""
contexts/pilots/execution.py

Runs a pilot graph in a background thread (ADR-030, CHY-104).

⚠️ This module has an expiry date. ADR-028 replaces the LangGraph pilots, and
when it does, everything here goes with them. It was moved out of
api/v1/pilots.py rather than rewritten, and it is deliberately the one part of
this context without a coverage target: effort spent on tests here is effort
spent on code that is scheduled to disappear. Endpoints, streaming and state
management are not in that position — those are in service.py and tested.

Execution model, unchanged from before the move:

  A thread from a two-slot pool runs the graph synchronously and pushes
  per-node events into the registry via
  main_loop.call_soon_threadsafe(registry.push, ...). Two slots is the
  resource budget of the first, Raspberry-Pi-based installation, not a design
  statement.

  The thread creates its own event loop for database work, which is why
  store.py has a second family of functions taking a DSN instead of a pool: an
  asyncpg pool belongs to the loop that created it.
"""
from __future__ import annotations

import asyncio
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, UTC
from pathlib import Path
from uuid import UUID

from contexts.pilots import store
from contexts.pilots.registry import RunRegistry
from kernel import current_call

_log = logging.getLogger(__name__)

executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="pilot")


@dataclass(frozen=True)
class RunSpec:
    """Everything a run needs to know, decided before the thread starts.

    An object rather than ten parameters, and the configuration values in it
    are the point (CHY-96). `db_dsn` and the metrics switch were read out of the
    process environment inside the thread — the first with
    `os.environ.setdefault`, which mutates the process from within a request
    and lets the first run silently decide for every later one.

    `model_used` and `node_metrics` come from the caller for the same reason
    `registry` does: reaching for api.config from a context would be a zone
    violation, and the executor has no business knowing which settings object
    the application uses.
    """

    run_id: UUID
    pdf_path: Path
    thread_id: str
    pilot_name: str
    db_dsn: str
    model_used: str
    #: Tier → model id, composed from workflow_configs and this run's overrides
    #: (CHY-42). Passed in rather than read from the pilot's module constants,
    #: for the same reason db_dsn is: a value read inside the thread is a value
    #: the caller cannot record, and pilot_runs.effective_config claims to
    #: record exactly this one.
    model_map: dict[str, str] = field(default_factory=dict)
    #: Emit node_metrics events (token usage per model call). Was `dev_mode`
    #: until CHY-192 removed the switch it was named after.
    node_metrics: bool = False


def _failure_text(exc: BaseException, spec: RunSpec) -> str:
    """What a failed run stores and sends its client.

    Until CHY-97 that was the whole traceback, and its first 500 characters
    went to the client: paths, and whatever a library put in its message. Now
    the application log has the traceback and the run has the request's id.

    A cancellation is the exception. Both pilots raise a `WorkflowCancelledError`
    of their own when a person cancels; that is an outcome, not a defect, and
    its text is ours (CHY-153).
    """
    if type(exc).__name__ == "WorkflowCancelledError":
        return str(exc)
    _log.error("pilot run failed", exc_info=exc,
               extra={"run_id": str(spec.run_id), "pilot": spec.pilot_name})
    return current_call.with_reference(
        f"unexpected error ({type(exc).__name__}), details in the application log"
    )


def run_in_thread(
    spec: RunSpec,
    main_loop: asyncio.AbstractEventLoop,
    cancel_event: threading.Event,
    registry: RunRegistry,
    oversight_gate: tuple[threading.Event, list] | None = None,
) -> None:
    """Run the pilot and push events to the main loop."""
    run_id = spec.run_id
    db_dsn = spec.db_dsn
    node_metrics = spec.node_metrics

    # Accumulate emitted events locally for the bulk write at run end. Reading
    # the registry from this thread instead would be a TOCTOU race — it is
    # populated on the main loop.
    local_events: list[dict] = []

    def emit(event: dict | None) -> None:
        if event is not None:
            stamped = {**event, "ts": int(datetime.now(UTC).timestamp() * 1000)}
            local_events.append(stamped)
            main_loop.call_soon_threadsafe(registry.push, run_id, stamped)
        else:
            main_loop.call_soon_threadsafe(registry.push, run_id, None)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(store.t_mark_running(db_dsn, run_id))

        if spec.pilot_name == "banking":
            from banking.graph import stream_workflow  # installed via Dockerfile

            def banking_emit(event: dict | None) -> None:
                if event is not None and event.get("type") == "oversight_required":
                    loop.run_until_complete(
                        store.t_mark_awaiting_oversight(db_dsn, run_id)
                    )
                emit(event)

            result = stream_workflow(
                str(spec.pdf_path), spec.thread_id, banking_emit,
                cancel_event=cancel_event,
                oversight_gate=oversight_gate,
                node_metrics=node_metrics,
                db_dsn=db_dsn,
                model_map=spec.model_map,
            )
            result_data = {
                "company_name":      result.get("company_name", ""),
                "fiscal_year":       result.get("fiscal_year", ""),
                "document_type":     result.get("document_type", ""),
                "kpis":              result.get("kpis", {}),
                "ratios":            result.get("ratios", {}),
                "anomalies":         result.get("anomalies", []),
                "routing_decision":  result.get("routing_decision", ""),
                "risk_level":        result.get("risk_level", ""),
                "risk_reasoning":    result.get("risk_reasoning", ""),
                "compliance_flags":  result.get("compliance_flags", []),
                "oversight_required": result.get("oversight_required", True),
                "summary":           result.get("summary", ""),
                "audit_log":         result.get("audit_log", []),
            }
            done_event = {
                "type":             "done",
                "risk_level":       result_data["risk_level"],
                "anomaly_count":    len(result_data["anomalies"]),
                "compliance_count": len(result_data["compliance_flags"]),
                "oversight_required": result_data["oversight_required"],
                "summary":          result_data["summary"],
            }
        else:  # study (default)
            from study.graph import stream_workflow  # installed via Dockerfile
            result = stream_workflow(
                str(spec.pdf_path), spec.thread_id, emit,
                cancel_event=cancel_event,
                run_id=str(run_id),
                db_dsn=db_dsn,
                model_map=spec.model_map,
            )
            result_data = {
                "zusammenfassung": result.get("zusammenfassung", ""),
                "fakten":          result.get("fakten", []),
                "mindmap":         result.get("mindmap", ""),
                "lernfragen":      result.get("lernfragen", []),
                "wissenspruefung": result.get("wissenspruefung", ""),
                "lernziel_erreicht": result.get("lernziel_erreicht", False),
                "iteration_count": result.get("iteration_count", 0),
                "quality_score":   result.get("quality_score"),
            }
            done_event = {
                "type":             "done",
                "lernziel_erreicht": result_data["lernziel_erreicht"],
                "iteration_count":  result_data["iteration_count"],
                "facts_count":      len(result_data["fakten"]),
            }

        loop.run_until_complete(store.t_mark_complete(db_dsn, run_id, result_data))

        # EU AI Act Art. 50 — the disclosure travels with the done event so the
        # UI can show it before it renders any generated content.
        disclosure = (
            f"AI-generated content — {spec.model_used} via LiteLLM Gateway "
            f"(EU AI Act Art. 50, deployer obligation as of 2026-08-02)"
        )
        done_event["model_used"] = spec.model_used
        done_event["model_disclosure"] = disclosure

        # Persist the record — best-effort. The run itself is already complete,
        # and failing it here would lose the result over a bookkeeping row.
        try:
            loop.run_until_complete(store.t_insert_transparency_record(
                db_dsn, run_id, model_used=spec.model_used, disclosure=disclosure,
            ))
        except Exception:
            # Written down, not passed over: a missing Art. 50 record is a
            # finding, even when the run it belongs to is fine (CHY-97).
            _log.warning("transparency record not stored", exc_info=True,
                         extra={"run_id": str(run_id)})

        emit(done_event)
    except Exception as exc:
        # A run that did not get approval ends as "rejected", not "failed".
        # oversight_outcome tells apart the two ways that happens: a human said
        # no, or nobody answered in time. Both are terminal, but only the first
        # is a decision — the audit trail must not conflate them (CHY-83).
        # A cancellation carries no outcome, at the gate as anywhere else, and
        # ends below as `failed` with its message (CHY-153).
        outcome = getattr(exc, "oversight_outcome", None)
        if outcome is None and "rejected by human oversight" in str(exc).lower():
            outcome = "rejected"
        if outcome is not None:
            partial = getattr(exc, "partial_state", {})
            if outcome == "timeout":
                summary_text = (
                    "Analyse abgelehnt: keine Freigabeentscheidung innerhalb der Frist. "
                    "Schweigen gilt nicht als Zustimmung (EU AI Act Art. 14)."
                )
                error_text = "Rejected by human oversight: decision timeout"
                event_message = "Analysis rejected: oversight decision timed out"
            else:
                summary_text = "Analyse durch menschliche Freigabe abgelehnt (EU AI Act Art. 14)."
                error_text = "Rejected by human oversight"
                event_message = "Analysis rejected by human oversight"
            rejection_result = {
                "company_name":      partial.get("company_name", ""),
                "fiscal_year":       partial.get("fiscal_year", ""),
                "document_type":     partial.get("document_type", ""),
                "kpis":              partial.get("kpis", {}),
                "ratios":            partial.get("ratios", {}),
                "anomalies":         partial.get("anomalies", []),
                "routing_decision":  partial.get("routing_decision", ""),
                "risk_level":        partial.get("risk_level", ""),
                "risk_reasoning":    partial.get("risk_reasoning", ""),
                "compliance_flags":  partial.get("compliance_flags", []),
                "oversight_required": True,
                "oversight_outcome": outcome,
                "rejected": True,
                "summary": summary_text,
            }
            loop.run_until_complete(store.t_mark_rejected(
                db_dsn, run_id, outcome=outcome,
                error_text=error_text, result=rejection_result,
            ))
            emit({
                "type":             "rejected",
                "message":          event_message,
                "oversight_outcome": outcome,
                "risk_level":       partial.get("risk_level", ""),
                "anomaly_count":    len(partial.get("anomalies", [])),
                "compliance_count": len(partial.get("compliance_flags", [])),
                "company_name":     partial.get("company_name", ""),
                "fiscal_year":      partial.get("fiscal_year", ""),
            })
        else:
            message = _failure_text(exc, spec)
            loop.run_until_complete(store.t_mark_failed(db_dsn, run_id, message))
            emit({"type": "error", "message": message[:500]})
    finally:
        try:
            loop.run_until_complete(store.t_store_events(db_dsn, run_id, local_events))
        except Exception:
            # Best effort: the outcome is already stored above. Logged, so a
            # run whose events are missing has a reason somewhere (CHY-97).
            _log.warning("run events not stored", exc_info=True,
                         extra={"run_id": str(run_id)})
        emit(None)  # sentinel — the SSE generator closes after this
        loop.close()
        registry.release_thread(run_id)
