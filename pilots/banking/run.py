#!/usr/bin/env python3
import sys
import json
import time
from datetime import datetime, UTC
from pathlib import Path

from banking.graph import run_workflow
from banking.nodes import OversightGateError


def _interactive_oversight() -> str:
    """Ask the operator at the terminal to approve or reject (EU AI Act Art. 14).

    A CLI run has a human at the keyboard, so the gate is real here rather than
    skipped. Without a terminal there is nobody to ask — and no decision means
    the run fails, it is never treated as approval (CHY-83).
    """
    if not sys.stdin.isatty():
        raise OversightGateError(
            "Human oversight required, but stdin is not a terminal. Run this "
            "interactively, or start the run through the API where the decision "
            "arrives via POST /v1/pilots/runs/{id}/oversight."
        )

    print("\n" + "=" * 60)
    print("FREIGABE ERFORDERLICH (EU AI Act Art. 14)")
    print("=" * 60)
    while True:
        answer = input("Analyse freigeben? [j]a / [n]ein: ").strip().lower()
        if answer in ("j", "ja", "y", "yes"):
            return "approved"
        if answer in ("n", "nein", "no"):
            return "rejected"
        print("Bitte 'j' oder 'n' eingeben.")


def _write_artifacts(result: dict, pdf_path: str, thread_id: str, total_ms: float) -> Path:
    date_str = datetime.now().astimezone().strftime("%Y-%m-%d")
    input_stem = Path(pdf_path).stem
    base_name = f"{date_str}_{input_stem}"
    run_dir = Path("output") / base_name
    if run_dir.exists():
        counter = 2
        while (Path("output") / f"{base_name}-{counter}").exists():
            counter += 1
        run_dir = Path("output") / f"{base_name}-{counter}"
    run_dir.mkdir(parents=True, exist_ok=True)

    (run_dir / "meta.json").write_text(
        json.dumps({
            "thread_id":          thread_id,
            "input_file":         Path(pdf_path).name,
            "date":               date_str,
            "company_name":       result.get("company_name", ""),
            "fiscal_year":        result.get("fiscal_year", ""),
            "document_type":      result.get("document_type", ""),
            "risk_level":         result.get("risk_level", ""),
            "oversight_required": result.get("oversight_required", True),
            "anomaly_count":      len(result.get("anomalies", [])),
            "compliance_count":   len(result.get("compliance_flags", [])),
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    (run_dir / "summary.md").write_text(result.get("summary", ""), encoding="utf-8")

    anomalies = result.get("anomalies", [])
    if anomalies:
        anomaly_md = "\n".join(f"- {a}" for a in anomalies)
        (run_dir / "anomalies.md").write_text(anomaly_md, encoding="utf-8")

    flags = result.get("compliance_flags", [])
    if flags:
        flags_md = "\n".join(f"- {f}" for f in flags)
        (run_dir / "compliance.md").write_text(flags_md, encoding="utf-8")

    (run_dir / "audit_log.json").write_text(
        json.dumps(result.get("audit_log", []), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    (run_dir / "kpis.json").write_text(
        json.dumps({
            "kpis":   result.get("kpis", {}),
            "ratios": result.get("ratios", {}),
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    node_timings = result.get("timings", {}).get("nodes", [])
    llm_nodes = [n for n in node_timings if n.get("model") is not None]
    total_llm_ms = sum(n["duration_ms"] for n in llm_nodes)
    (run_dir / "perf.json").write_text(
        json.dumps({
            "timestamp":    datetime.now(UTC).isoformat(),
            "thread_id":    thread_id,
            "total_ms":     round(total_ms, 2),
            "total_llm_ms": round(total_llm_ms, 2),
            "overhead_ms":  round(total_ms - total_llm_ms, 2),
            "nodes":        node_timings,
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return run_dir


def main():
    if len(sys.argv) < 2:
        print("Usage: python run.py <pdf_path> [thread_id]")
        sys.exit(1)

    pdf_path = sys.argv[1]
    thread_id = sys.argv[2] if len(sys.argv) > 2 else "banking-run-1"

    if not Path(pdf_path).exists():
        print(f"Error: PDF not found: {pdf_path}")
        sys.exit(1)

    print(f"PDF:       {pdf_path}")
    print(f"Thread ID: {thread_id}")
    print("Starting Banking Analysis Workflow ...")
    print("=" * 60)

    t_start = time.perf_counter()
    result = run_workflow(pdf_path, thread_id, oversight_prompt=_interactive_oversight)
    total_ms = (time.perf_counter() - t_start) * 1000

    print("\n" + "=" * 60)
    print("RESULT")
    print("=" * 60)
    print(f"Company:            {result.get('company_name', '?')}")
    print(f"Fiscal year:        {result.get('fiscal_year', '?')}")
    print(f"Risk level:         {result.get('risk_level', '?')}")
    print(f"Oversight required: {result.get('oversight_required', True)}")
    print(f"Anomalies:          {len(result.get('anomalies', []))}")
    print(f"Compliance flags:   {len(result.get('compliance_flags', []))}")
    print(f"Runtime:            {total_ms / 1000:.1f}s")

    run_dir = _write_artifacts(result, pdf_path, thread_id, total_ms)

    print(f"\n✓ Artifacts saved to: {run_dir}/")
    print("  ├── meta.json")
    print("  ├── summary.md")
    print("  ├── anomalies.md")
    print("  ├── compliance.md")
    print("  ├── kpis.json")
    print("  ├── audit_log.json")
    print("  └── perf.json")


if __name__ == "__main__":
    main()
