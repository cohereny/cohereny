#!/usr/bin/env python3
import sys
import json
import time
from datetime import datetime, UTC
from pathlib import Path

from study.graph import run_workflow


def _write_report(result: dict, run_dir: Path, pdf_path: str, thread_id: str, total_ms: float) -> None:
    from datetime import datetime

    node_timings = result.get("timings", {}).get("nodes", [])
    llm_nodes = [n for n in node_timings if n.get("model") is not None]
    total_prompt = sum(n["prompt_tokens"] or 0 for n in llm_nodes)
    total_completion = sum(n["completion_tokens"] or 0 for n in llm_nodes)
    total_llm_ms = sum(n["duration_ms"] for n in llm_nodes)

    lines = []
    lines.append("# Study Workflow — Report")
    lines.append("")
    lines.append("| | |")
    lines.append("|---|---|")
    lines.append(f"| **Thread-ID** | `{thread_id}` |")
    lines.append(f"| **Eingabe** | `{Path(pdf_path).name}` |")
    lines.append(f"| **Datum** | {datetime.now().astimezone().strftime('%Y-%m-%d %H:%M')} |")
    lines.append(f"| **Lernziel erreicht** | {'✅ Ja' if result['lernziel_erreicht'] else '❌ Nein'} |")
    lines.append(f"| **Fakten extrahiert** | {len(result['fakten'])} |")
    lines.append(f"| **Iterationen** | {result['iteration_count']} |")
    lines.append(f"| **Gesamtlaufzeit** | {total_ms / 1000:.1f}s |")
    lines.append(f"| **LLM-Zeit** | {total_llm_ms / 1000:.1f}s |")
    lines.append(f"| **Overhead** | {(total_ms - total_llm_ms) / 1000:.1f}s |")
    lines.append("")
    lines.append("## Workflow")
    lines.append("")
    lines.append("```mermaid")
    lines.append("flowchart LR")
    lines.append("    N1[Ingest] --> N2[Zusammenfassung\\nMap-Reduce]")
    lines.append("    N2 --> N3[Fakten\\nMap-Reduce]")
    lines.append("    N3 --> N4[Mindmap]")
    lines.append("    N4 --> N5[Hoerbuch]")
    lines.append("    N5 --> N6{Qualitaets-Check}")
    lines.append("    N6 -- Luecken --> N7[Gap-Analyse] --> N2")
    lines.append("    N6 -- OK --> N8[Lernfragen]")
    lines.append("    N8 --> N9{Bewertung}")
    lines.append("    N9 -- Nicht erreicht --> N8")
    lines.append("    N9 -- Erreicht --> END([Fertig])")
    lines.append("```")
    lines.append("")
    lines.append("## Metriken pro Node")
    lines.append("")
    lines.append("| Node | Modell | Dauer (s) | Tokens In | Tokens Out |")
    lines.append("|---|---|---|---|---|")
    for n in node_timings:
        model = n.get("model") or "—"
        p = n.get("prompt_tokens")
        c = n.get("completion_tokens")
        label = n["node"]
        if n.get("iteration") is not None:
            label += f" (it.{n['iteration']})"
        lines.append(f"| {label} | `{model}` | {n['duration_ms'] / 1000:.2f} | {p if p is not None else '—'} | {c if c is not None else '—'} |")
    lines.append(f"| **Total** | | **{total_ms / 1000:.2f}** | **{total_prompt}** | **{total_completion}** |")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## Zusammenfassung")
    lines.append("")
    lines.append(result["zusammenfassung"])
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(f"## Fakten ({len(result['fakten'])})")
    lines.append("")
    for f in result["fakten"]:
        lines.append(f"- {f}" if not f.startswith("-") else f)
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## Mindmap")
    lines.append("")
    mindmap_content = result["mindmap"]
    if not mindmap_content.strip().startswith("```"):
        mindmap_content = f"```mermaid\n{mindmap_content.strip()}\n```"
    lines.append(mindmap_content)
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## Hörbuch-Skript")
    lines.append("")
    lines.append(result["hoerbuch_skript"])
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## Lernfragen")
    lines.append("")
    for q in result["lernfragen"]:
        lines.append(f"{q}")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## Wissensprüfung")
    lines.append("")
    lines.append(result["wissenspruefung"])
    lines.append("")

    (run_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")


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

    # meta.json
    (run_dir / "meta.json").write_text(
        json.dumps({
            "thread_id": thread_id,
            "input_file": Path(pdf_path).name,
            "date": date_str,
            "lernziel_erreicht": result["lernziel_erreicht"],
            "iteration_count": result["iteration_count"],
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # zusammenfassung.md
    (run_dir / "zusammenfassung.md").write_text(result["zusammenfassung"], encoding="utf-8")

    # fakten.md
    fakten_md = "\n".join(f"- {f}" for f in result["fakten"])
    (run_dir / "fakten.md").write_text(fakten_md, encoding="utf-8")

    # mindmap.md (Mermaid-Block)
    mindmap_content = result["mindmap"]
    if not mindmap_content.strip().startswith("```"):
        mindmap_content = f"```mermaid\n{mindmap_content.strip()}\n```"
    (run_dir / "mindmap.md").write_text(mindmap_content, encoding="utf-8")

    # hoerbuch.md
    (run_dir / "hoerbuch.md").write_text(result["hoerbuch_skript"], encoding="utf-8")

    # quiz.json
    (run_dir / "quiz.json").write_text(
        json.dumps({
            "lernfragen": result["lernfragen"],
            "wissenspruefung": result["wissenspruefung"],
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # perf.json
    node_timings = result.get("timings", {}).get("nodes", [])
    llm_nodes = [n for n in node_timings if n.get("model") is not None]
    total_llm_ms = sum(n["duration_ms"] for n in llm_nodes)
    (run_dir / "perf.json").write_text(
        json.dumps({
            "timestamp": datetime.now(UTC).isoformat(),
            "thread_id": thread_id,
            "total_ms": round(total_ms, 2),
            "total_llm_ms": round(total_llm_ms, 2),
            "overhead_ms": round(total_ms - total_llm_ms, 2),
            "nodes": node_timings,
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    _write_report(result, run_dir, pdf_path, thread_id, total_ms)

    return run_dir


def main():
    if len(sys.argv) < 2:
        print("Usage: python run.py <pdf_path> [thread_id]")
        sys.exit(1)

    pdf_path = sys.argv[1]
    thread_id = sys.argv[2] if len(sys.argv) > 2 else "study-run-1"

    if not Path(pdf_path).exists():
        print(f"Fehler: PDF nicht gefunden: {pdf_path}")
        sys.exit(1)

    print(f"PDF:       {pdf_path}")
    print(f"Thread ID: {thread_id}")
    print("Starte Workflow ...")
    print("=" * 60)

    t_start = time.perf_counter()
    result = run_workflow(pdf_path, thread_id)
    total_ms = (time.perf_counter() - t_start) * 1000

    print("\n" + "=" * 60)
    print("ERGEBNIS")
    print("=" * 60)
    print(f"Lernziel erreicht: {result['lernziel_erreicht']}")
    print(f"Iterationen:       {result['iteration_count']}")
    print(f"Laufzeit gesamt:   {total_ms / 1000:.1f}s")

    run_dir = _write_artifacts(result, pdf_path, thread_id, total_ms)

    print(f"\n✓ Artefakte gespeichert in: {run_dir}/")
    print("  ├── meta.json")
    print("  ├── zusammenfassung.md")
    print("  ├── fakten.md")
    print("  ├── mindmap.md")
    print("  ├── hoerbuch.md")
    print("  ├── quiz.json")
    print("  ├── perf.json")
    print("  └── report.md")


if __name__ == "__main__":
    main()
