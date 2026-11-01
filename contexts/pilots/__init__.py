"""
contexts/pilots/ — pilot runs, oversight and coverage (ADR-030, CHY-104 ✅).

Owns: pilot_runs, question_bank, run_coverage, transparency_records
Its own language: run, oversight, coverage, disposition

    schema.py      transport shapes — moved from domain/pilots.py
    run.py         the rules: ownership, disposition, containment, coverage
    registry.py    what is happening right now in *this* process
    store.py       the only place with SQL
    service.py     use cases — own the transaction, call the above
    events.py      the wire vocabulary the UI switches on
    execution.py   the LangGraph thread ⚠️ expires with ADR-028

Published interface: service, schema, errors. Everything else is internal, and
the guard rule `context_encapsulation` enforces it — including against the
transport layer, which is where the first violation came from.

What the split bought, in one sentence: the EU AI Act Art. 14 oversight
decision can be taken without speaking HTTP to it. Before, api/v1/pilots.py was
846 lines at 54 % coverage with three functions over the complexity threshold,
and it carried six findings of the 14.08. audit at once. It is 209 lines now,
the rules are covered by 57 tests that touch no database, and the boundary
guard reports the same three violations as before — all of them elsewhere.
"""
