#!/usr/bin/env python3
"""
deploy/quickstart/check.py

The steps of README.md, run as a script (CHY-189).

The CI runs this against a fresh quickstart on every merge request, so the
walkthrough cannot go stale without the pipeline turning red. Each step below
corresponds to a numbered step of the README and makes the same requests the
curl commands there make.

    docker compose run --rm seed python deploy/quickstart/check.py

Exit code 0 when every step worked, 1 at the first that did not. Prints what it
saw, never a token.
"""
from __future__ import annotations

import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))

from seed import HELP_CENTER, MODEL, PROJECT, WORKFLOW, client  # noqa: E402 -- path set above

MESSAGE = ("Hi, I ordered a kettle on Tuesday (order 4711) and it still hasn't "
           "arrived. Where is it?")


def step(n: int, title: str) -> None:
    print(f"\n{n}. {title}")


def ok(resp: httpx.Response, status: int, what: str) -> dict:
    if resp.status_code != status:
        sys.exit(f"   ✗ {what}: HTTP {resp.status_code} {resp.text[:400]}")
    print(f"   ✓ {what}")
    return resp.json() if resp.content else {}


def main() -> int:
    author, serve = client("admin", "author"), client("admin", "serve")
    reviewer, observe = client("reviewer", "author"), client("reviewer", "observe")

    step(1, "Find the example")
    scopes = ok(author.get("/v1/scopes"), 200, "scopes listed")
    project = next(s["id"] for s in scopes if s["name"] == PROJECT)
    help_center = next(s["id"] for s in scopes if s["name"] == HELP_CENTER)
    workflows = ok(author.get("/v1/workflows", params={"scope_id": project}), 200,
                   "workflows of the project listed")["items"]
    wf = next(w["id"] for w in workflows if w["name"] == WORKFLOW)

    step(2, "See what the project inherits")
    composed = ok(serve.post("/v1/compose", json={"scope_id": project}), 200,
                  "context composed")
    types = sorted(b["block_type"] for b in composed["blocks"])
    print(f"     {len(types)} blocks from three scopes: {', '.join(types)}")
    if len(types) != 5:
        sys.exit("   ✗ expected tone, data rule, product facts, example and notice")

    step(3, "Ask the model directly — where that is allowed, and where not")
    question = {"task_type": "summarize",
                "prompt": "Summarise our shipping and returns policy in two sentences."}
    answer = ok(serve.post("/v1/complete", json={**question, "scope_id": help_center}),
                200, "help center: answer received")
    print(f"     model {answer['model_used']}, {answer['blocks_used']} blocks used")
    if answer["model_used"] != MODEL or not answer["content"].strip():
        sys.exit(f"   ✗ expected a non-empty answer from {MODEL}")
    # One call in ten is drawn for a check afterwards (CHY-269); which one is a
    # hash of the call, so this says what happened rather than expecting it.
    drawn = f"yes, {answer['sample_id']}" if answer.get("sample_id") else "no"
    print(f"     drawn for a check afterwards (one in ten): {drawn}")
    # Example Corp holds customer data: an answer there needs a person first
    # (CHY-199, ADR-050). The reason is checked, not only the code — a 409 for
    # a missing routing rule would look the same.
    refused = ok(serve.post("/v1/complete", json={**question, "scope_id": project}),
                 409, "order inquiries: refused, an answer here needs review")
    if "confidential" not in refused["detail"]:
        sys.exit(f"   ✗ expected the data class as the reason: {refused['detail']}")

    step(4, "Start a run of the workflow")
    run = ok(author.post(f"/v1/workflows/{wf}/runs",
                         json={"input_data": {"message": MESSAGE}}),
             201, "run started")
    print(f"     status {run['status']}")
    if run["status"] != "awaiting_oversight":
        sys.exit(f"   ✗ expected the run to wait for review: {run.get('error_message')}")

    step(5, "Read the draft before deciding")
    nodes = ok(reviewer.get(f"/v1/workflows/runs/{run['id']}/nodes"), 200,
               "node outputs read")["nodes"]
    draft = next(n for n in nodes if n["node_ref"] == "draft")
    text = (draft["output"] or {}).get("content", "")
    print("     " + text.strip().replace("\n", "\n     ")[:600])
    if not text.strip():
        sys.exit("   ✗ the draft is empty")

    step(6, "Approve it as the reviewer")
    decided = ok(reviewer.post(f"/v1/workflows/runs/{run['id']}/oversight", json={
        "decision": "approved", "reason": "Accurate and polite; mentions the delay.",
    }), 200, "approved")
    if decided["status"] != "complete":
        sys.exit(f"   ✗ expected the run to complete, got {decided['status']}")

    step(7, "Look at the record")
    entries = ok(observe.get("/v1/audit-log", params={"target": f"workflow_run:{run['id']}"}),
                 200, "audit log read")
    print("     " + ", ".join(e["action"] for e in reversed(entries)))

    print("\nAll steps worked.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
