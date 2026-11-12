from typing import List, NotRequired, TypedDict


class StudyState(TypedDict):
    # Input
    pdf_path: str

    # Tier -> model id, composed from workflow_configs and this run's overrides
    # (CHY-42). In the state rather than a module global: a global set at graph
    # start is a process-wide mutation from inside one run, which is exactly
    # what CHY-96 removed for POSTGRES_URI. Nodes read it via _model().
    model_map: dict[str, str]

    # Node 1: Ingest
    chunks: List[str]

    # Node 2: Zusammenfassung (phi3:mini)
    zusammenfassung: str

    # Node 3: Fakten-Extraktion (gemma4:e4b)
    fakten: List[str]

    # Node 4: Mindmap (phi3:mini)
    mindmap: str

    # Node 5: Hörbuch-Skript (gemma4:e4b)
    hoerbuch_skript: str

    # Node 6: Qualitäts-Check (gemma4:e4b) → Loop-Steuerung
    quality_complete: bool
    quality_gaps: List[str]
    quality_score: NotRequired[int]  # LLM-Judge specificity score 1–5 (Tier-2)

    # Node 7: Gap-Analyse — strukturierte Themen für nächste Extraktion
    extraction_focus: NotRequired[List[str]]  # fed back into node_extract_facts prompt

    # Node 8: Lernfragen (phi3:mini)
    lernfragen: List[str]

    # Node 9: Wissensprüfung (gemma4:e4b) → Loop-Steuerung
    wissenspruefung: str
    lernziel_erreicht: bool

    # Schleifenzähler (verhindert Endlosschleifen)
    iteration_count: int

    # Performance-Messung: Liste von {node, duration_ms, model, prompt_tokens, completion_tokens}
    timings: dict
