"""
contexts/learning/ — the feedback loop and its evidence (ADR-030, CHY-108).

Owns: feedback_events, learning_proposals, benchmark_reports,
      benchmark_runs, benchmark_measurements (migration 028)
Its own language: feedback, proposal, anchor, measurement, adaptation_gain

    service.py     published use cases — the anchor's activation check,
                   reading benchmark runs
    schema.py      transport shapes
    errors.py      what it refuses
    anchor.py      what a calibration_anchor block means as a test case
    validators.py  scoring an output, ADR-006 layers 1 and 2
    store.py       SQL

The test cases are calibration_anchor blocks in the repository (ADR-043); this
context reads them and keeps what was measured. Feedback and proposals are
still a skeleton: api/v1/feedback.py answers 501.

This context owns the evidence, not the decision. A proposal it raises becomes
a block draft in `repository` and goes through that block's own review
(ADR-042) — through the published interface. Writing a block from here would
be exactly the automatic overwrite the loop is designed to prevent.
"""
