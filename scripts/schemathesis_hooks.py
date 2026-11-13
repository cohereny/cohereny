"""
scripts/schemathesis_hooks.py

Schemathesis hooks for scripts/contract_fuzz.py, loaded through the
SCHEMATHESIS_HOOKS environment variable that `_schemathesis` sets on every
run (CHY-274).

The document upload, `PUT /v1/workflows/runs/{run_id}/documents/{name}`,
takes `application/pdf` or `text/plain`. Schemathesis ships no serializer for
`application/pdf`. Two things followed from that:

- Cases built to fit the contract only ever used `text/plain`. The PDF branch
  of the upload had never been fuzzed.
- Cases built to break the contract also picked PDF, could not be sent, and
  were thrown away. When too many were, Hypothesis gave up with the health
  check `filter_too_much`, and the strict `integration` job failed with no
  finding about the API: pipelines 939 and 960 on 10 November 2026, 1003 on
  13 November.

A PDF body is bytes like any other, so the built-in `application/octet-stream`
serializer sends it unchanged. Measured without a server, fixed seeds,
`max_examples=15` as in CI: generation of contract-breaking cases for the
upload gave up for 131 of 200 seeds before, for 1 of 200 after, and about two
in three generated cases now carry a PDF body. The one left over needs both
path parameters broken at once; it is Hypothesis' ordinary rate for an
operation with two constrained path parameters and is left alone.
"""
import schemathesis

schemathesis.serializer.alias("application/pdf", "application/octet-stream")
