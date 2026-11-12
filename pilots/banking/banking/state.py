from typing import TypedDict


class BankingState(TypedDict):
    # Input
    pdf_path: str

    # Tier -> model id, composed from workflow_configs and this run's overrides
    # (CHY-42). In the state rather than a module global: a global set at graph
    # start is a process-wide mutation from inside one run, which is exactly
    # what CHY-96 removed for POSTGRES_URI. Nodes read it via _model().
    #
    # It matters more here than in study: this pilot's tiers *are* the
    # local/cloud boundary, so which model a tier names is a data-protection
    # decision and not a performance setting.
    model_map: dict[str, str]

    # Node 1 — Ingest
    document_text: str
    document_type: str     # "Jahresabschluss", "Due-Diligence-Bericht", etc.
    company_name: str
    fiscal_year: str

    # Node 2 — KPI extraction (raw figures in EUR)
    kpis: dict

    # Node 3 — Ratio calculation (pure Python, no LLM)
    ratios: dict

    # Node 4 — Anomaly detection
    anomalies: list[str]

    # Node 5 — Risk gateway (conditional routing)
    routing_decision: str  # "fast_track" | "full_analysis"

    # Node 6 — Risk classification
    risk_level: str        # "GRÜN" | "GELB" | "ROT"
    risk_reasoning: str

    # Node 7 — Compliance check
    compliance_flags: list[str]

    # Node 8 — Human oversight gate (hardcoded, always True)
    oversight_required: bool
    oversight_note: str

    # Node 9 — Summary
    summary: str

    # Cross-cutting
    audit_log: list[str]
    #: What the ingress scan found in the uploaded document (CHY-135). Empty
    #: when it found nothing — which is a different statement from the key
    #: being absent, and the audit line says which of the two happened.
    ingress_findings: list[dict]
    timings: dict
