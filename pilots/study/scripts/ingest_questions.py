"""
scripts/ingest_questions.py

One-time ingest script: parses exam questions from PDF files
and inserts them into the question_bank table (CHY-23).

Usage (inside the API container, or anywhere with a valid POSTGRES_URI):

  POSTGRES_URI="postgresql://..." python -m pilots.study.scripts.ingest_questions \\
      --source "Example exam 2024" \\
      --topic  "study" \\
      /path/to/exam-questions.pdf

Or via docker exec:
  docker exec -it cohereny-api-dev python -m pilots.study.scripts.ingest_questions \\
      --source "Example exam 2024" --topic "study" /data/exam-questions.pdf

Prerequisites:
  - POSTGRES_URI env var pointing to cohereny_dev/test/prod database
  - The PDF must be machine-readable (not a scanned image)
  - Requires: pypdf, langchain-openai, python-dotenv (already installed in the API image)

Notes:
  - Uses ON CONFLICT DO NOTHING — safe to run multiple times with the same source file
  - A "question" is any numbered item of the form "1. <text>" or "Frage 1: <text>"
  - The LLM is used only when simple regex extraction fails (e.g. in free-text exams)
"""
import argparse
import re
import sys

from pypdf import PdfReader

# study.config calls load_dotenv() itself, so the .env is loaded by importing
# it — one place that decides where configuration comes from.
#
# From the pilot's own configuration module, not read again here (CHY-96).
# This script used to declare all four values itself, with its own defaults —
# and they had already drifted: LITELLM_API_KEY was "dummy" here and "" there.
# Whoever corrects the configuration does not correct a script that never
# imports it.
from study.config import (
    LITELLM_API_KEY,
    LITELLM_BASE_URL,
    MODEL_SMALL,
    POSTGRES_URI,
)


# ── PDF text extraction ───────────────────────────────────────────────────────

def _extract_text(pdf_path: str) -> str:
    reader = PdfReader(pdf_path)
    return "\n".join(page.extract_text() or "" for page in reader.pages)


# ── Question extraction (regex first, LLM fallback) ──────────────────────────

_NUMBERED_RE = re.compile(
    r"(?:(?:Frage|F)\s*\.?\s*)?(\d{1,3})\s*[.:)]\s+(.+?)(?=(?:(?:Frage|F)\s*\.?\s*)?\d{1,3}\s*[.:)]|\Z)",
    re.DOTALL,
)


def _extract_regex(text: str) -> list[str]:
    """Try to extract numbered questions via regex. Returns [] if fewer than 5 found."""
    questions = []
    for m in _NUMBERED_RE.finditer(text):
        q = " ".join(m.group(2).split())  # normalise whitespace
        if 10 < len(q) < 800:             # sanity length filter
            questions.append(q)
    return questions if len(questions) >= 5 else []


def _extract_llm(text: str) -> list[str]:
    """LLM-based extraction for exams that do not use numbered-list formatting."""
    from langchain_openai import ChatOpenAI
    from langchain_core.messages import SystemMessage, HumanMessage

    llm = ChatOpenAI(
        model=MODEL_SMALL,
        base_url=LITELLM_BASE_URL,
        api_key=LITELLM_API_KEY,
        temperature=0.0,
    )
    # Process in chunks to stay within context window
    chunk_size = 4000
    chunks = [text[i:i + chunk_size] for i in range(0, len(text), chunk_size)]
    all_questions: list[str] = []

    for i, chunk in enumerate(chunks, start=1):
        print(f"   LLM extraction chunk {i}/{len(chunks)} ...", end="\r")
        resp = llm.invoke([
            SystemMessage(
                "Extrahiere alle Prüfungsfragen aus dem folgenden Prüfungstext. "
                "Gib jede Frage auf einer eigenen Zeile aus. Keine Nummerierung, nur die Fragetexte."
            ),
            HumanMessage(chunk),
        ])
        for line in resp.content.splitlines():
            line = line.strip().lstrip("0123456789.) ").strip()
            if len(line) > 10:
                all_questions.append(line)

    print()
    return all_questions


def extract_questions(text: str) -> list[str]:
    """Extract questions from text, preferring regex, falling back to LLM."""
    questions = _extract_regex(text)
    if questions:
        print(f"   Regex: {len(questions)} Fragen gefunden")
        return questions
    print("   Regex erfolglos — LLM-Extraktion läuft ...")
    return _extract_llm(text)


# ── Database insert ───────────────────────────────────────────────────────────

def insert_questions(questions: list[str], topic: str, source: str) -> int:
    """Insert questions into question_bank. Returns number of rows inserted."""
    import psycopg

    if not POSTGRES_URI:
        print("ERROR: POSTGRES_URI not set", file=sys.stderr)
        sys.exit(1)

    inserted = 0
    with psycopg.connect(POSTGRES_URI) as conn:
        for q in questions:
            result = conn.execute(
                "INSERT INTO question_bank (question, topic, source) "
                "VALUES (%s, %s, %s) "
                "ON CONFLICT DO NOTHING "
                "RETURNING id",
                (q.strip(), topic, source),
            ).fetchone()
            if result:
                inserted += 1

    return inserted


# ── CLI entry point ───────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ingest exam questions into the question_bank table."
    )
    parser.add_argument("pdf", nargs="+", help="Path(s) to exam PDF file(s)")
    parser.add_argument("--topic",  default="study", help="Topic tag (default: study)")
    parser.add_argument("--source", required=True, help="Source label, e.g. 'Example exam 2024'")
    args = parser.parse_args()

    total_inserted = 0
    for pdf_path in args.pdf:
        print(f"\n── {pdf_path}")
        text = _extract_text(pdf_path)
        print(f"   {len(text):,} Zeichen extrahiert")

        questions = extract_questions(text)
        if not questions:
            print("   ⚠ Keine Fragen gefunden — PDF überspringen")
            continue

        print(f"   {len(questions)} Fragen → INSERT ...")
        inserted = insert_questions(questions, topic=args.topic, source=args.source)
        print(f"   ✓ {inserted} neue Zeilen in question_bank (ON CONFLICT DO NOTHING)")
        total_inserted += inserted

    print(f"\n✓ Gesamt: {total_inserted} neue Fragen in question_bank")


if __name__ == "__main__":
    main()
