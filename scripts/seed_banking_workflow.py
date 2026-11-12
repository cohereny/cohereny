#!/usr/bin/env python3
"""
scripts/seed_banking_workflow.py

The credit workflow of the banking pilot, set up in an installation through
the API (CHY-178, CHY-172).

What it writes comes from deploy/examples/banking/: the workflow (workflow.json),
the prompts as blocks (blocks.yaml) and the routing rules (routing.yaml). One
source, read by this script and by the tests that hold the definition to its
promises (tests/test_banking_example.py) — so what is tested is what is seeded.

    COHERENY_API_URL=https://… COHERENY_TOKEN=… \\
        python3 scripts/seed_banking_workflow.py            # the plan, offline
    … python3 scripts/seed_banking_workflow.py --apply      # write it
    … --apply --markt <actor> --marktfolge <actor> --kreditkommission <actor>

The token is one of an `admin` on the `author` surface: scopes and review areas
need the role, blocks and workflows the surface.

**Through the API, not SQL.** The seed of CHY-143 wrote rows directly and
stopped working the day migration 023 added checks it did not know. Through
the API every rule applies that applies to a person — node checks, the privacy
ratchet, the audit log — and the seed cannot drift from them.

**Everything is created as a draft.** The blocks need a reviewer's approval, the
routing rules a second person's (ADR-045), the workflow its activation: a seed
that approved its own governance would be the thing it argues against. What
exists already — the scope, an area, a block with the same type and tags, a rule
for the same task type, a workflow of the same name — is left as it is.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import yaml

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / "deploy" / "examples" / "banking"

#: The scope the example lives in. Confidential: credit files of companies.
SCOPE = {"name": "Musterbank", "node_type": "org", "privacy_level": "confidential",
         "description": "Kreditanalyse (deploy/examples/banking, CHY-178)."}

#: The competence order the gates name (ADR-050, MaRisk BTO 1.1).
AREAS = ("markt", "marktfolge", "kreditkommission")

#: What the API answers for a write that created something, and for one that
#: found it there already (an area, a member).
CREATED, CONFLICT = httpx.codes.CREATED, httpx.codes.CONFLICT


@dataclass(frozen=True)
class Example:
    workflow: dict[str, Any]
    blocks: list[dict[str, Any]]
    routing_reason: str
    rules: list[dict[str, Any]]


def load(directory: Path = EXAMPLE) -> Example:
    """The example as its three files say."""
    blocks = yaml.safe_load((directory / "blocks.yaml").read_text(encoding="utf-8"))
    routing = yaml.safe_load((directory / "routing.yaml").read_text(encoding="utf-8"))
    return Example(
        workflow=json.loads((directory / "workflow.json").read_text(encoding="utf-8")),
        blocks=blocks["blocks"], routing_reason=routing["reason"], rules=routing["rules"],
    )


def block_body(scope_id: str, block: dict[str, Any]) -> dict[str, Any]:
    """The request that creates one prompt block."""
    return {"scope_id": scope_id, "block_type": block["block_type"],
            "content": block["content"].strip(), "tags": block["tags"]}


def rule_body(scope_id: str, reason: str, rule: dict[str, Any]) -> dict[str, Any]:
    """The request that creates one routing rule (ADR-037, ADR-038)."""
    return {"scope_id": scope_id, "block_type": "routing_rule", "content": reason,
            "metadata": {"task_type": rule["task_type"], "candidates": rule["candidates"],
                         "max_tokens": rule["max_tokens"]}}


def workflow_body(scope_id: str, workflow: dict[str, Any]) -> dict[str, Any]:
    return {"scope_id": scope_id, "name": workflow["name"],
            "description": workflow["description"], "risk_class": workflow["risk_class"]}


def plan(example: Example) -> list[str]:
    """What --apply would write, as lines a person reads before saying yes."""
    w = example.workflow
    return [
        f"scope      {SCOPE['name']} ({SCOPE['node_type']}, {SCOPE['privacy_level']})",
        *(f"area       {name}" for name in AREAS),
        *(f"block      {b['block_type']:14} {', '.join(b['tags'])}  (draft)" for b in example.blocks),
        *(f"rule       {r['task_type']:20} {' → '.join(r['candidates'])}  (draft)"
          for r in example.rules),
        (f"workflow   {w['name']} — {len(w['nodes'])} nodes, {len(w['connections'])} "
         f"connections, risk_class {w['risk_class']}  (draft)"),
    ]


# ── writing ───────────────────────────────────────────────────────────────────

def _expect(resp: httpx.Response, status: int | tuple[int, ...], what: str) -> Any:
    """The JSON body, or stop with what failed and what the API said."""
    wanted = status if isinstance(status, tuple) else (status,)
    if resp.status_code not in wanted:
        sys.exit(f"seed: {what} failed — HTTP {resp.status_code}: {resp.text[:400]}")
    return resp.json() if resp.content else {}


def _scope(c: httpx.Client) -> str:
    for s in _expect(c.get("/v1/scopes"), 200, "listing scopes"):
        if s["name"] == SCOPE["name"] and s["node_type"] == SCOPE["node_type"]:
            print(f"  scope      {SCOPE['name']} exists ({s['id']})")
            return s["id"]
    created = _expect(c.post("/v1/scopes", json=SCOPE), CREATED, "creating the scope")
    print(f"  scope      {SCOPE['name']} created ({created['id']})")
    return created["id"]


def _areas(c: httpx.Client, scope_id: str, members: dict[str, list[str]]) -> None:
    for name in AREAS:
        resp = c.post(f"/v1/scopes/{scope_id}/review-areas", json={"name": name})
        _expect(resp, (CREATED, CONFLICT), f"creating the area {name}")
        print(f"  area       {name} {'created' if resp.status_code == CREATED else 'exists'}")
        for actor in members.get(name, []):
            resp = c.post(f"/v1/scopes/{scope_id}/review-areas/{name}/members",
                          json={"actor_id": actor})
            _expect(resp, (CREATED, CONFLICT), f"adding {actor} to {name}")
            print(f"  member     {name}: {actor}")


def _existing_blocks(c: httpx.Client, scope_id: str) -> list[dict[str, Any]]:
    """Drafts, blocks in review and active ones of the scope — what a listing shows
    by default. One page: the example has a dozen, the page holds a hundred."""
    body = _expect(c.get("/v1/blocks", params={"scope_id": scope_id, "page_size": 100}),
                   200, "listing blocks")
    return body["items"]


def _blocks(c: httpx.Client, scope_id: str, example: Example) -> None:
    existing = _existing_blocks(c, scope_id)
    have = {(b["block_type"], tuple(sorted(b.get("tags") or []))) for b in existing}
    rules = {(b.get("metadata") or {}).get("task_type") for b in existing
             if b["block_type"] == "routing_rule"}
    for block in example.blocks:
        key = (block["block_type"], tuple(sorted(block["tags"])))
        if key in have:
            print(f"  block      {block['name']} exists")
            continue
        _expect(c.post("/v1/blocks", json=block_body(scope_id, block)), CREATED,
                f"creating the block {block['name']}")
        print(f"  block      {block['name']} created (draft)")
    for rule in example.rules:
        if rule["task_type"] in rules:
            print(f"  rule       {rule['task_type']} exists")
            continue
        _expect(c.post("/v1/blocks", json=rule_body(scope_id, example.routing_reason, rule)),
                CREATED, f"creating the routing rule {rule['task_type']}")
        print(f"  rule       {rule['task_type']} created (draft)")


def _workflow(c: httpx.Client, scope_id: str, example: Example) -> None:
    w = example.workflow
    listed = _expect(c.get("/v1/workflows", params={"scope_id": scope_id}), 200,
                     "listing workflows")
    if any(item["name"] == w["name"] for item in listed["items"]):
        print(f"  workflow   {w['name']} exists — left as it is")
        return
    wf = _expect(c.post("/v1/workflows", json=workflow_body(scope_id, w)), CREATED,
                 "creating the workflow")["id"]
    for node in w["nodes"]:
        _expect(c.post(f"/v1/workflows/{wf}/nodes", json=node), CREATED,
                f"adding node {node['node_ref']}")
    for edge in w["connections"]:
        _expect(c.post(f"/v1/workflows/{wf}/connections", json=edge), CREATED,
                f"connecting {edge['source_node_ref']} → {edge['target_node_ref']}")
    print(f"  workflow   {w['name']} created (draft, {wf})")


def apply(c: httpx.Client, example: Example, members: dict[str, list[str]]) -> None:
    scope_id = _scope(c)
    _areas(c, scope_id, members)
    _blocks(c, scope_id, example)
    _workflow(c, scope_id, example)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[1])
    ap.add_argument("--apply", action="store_true", help="write; without it, print the plan")
    for area in AREAS:
        ap.add_argument(f"--{area}", action="append", default=[], metavar="ACTOR_ID",
                        help=f"add this actor to the area {area} (repeatable)")
    args = ap.parse_args(argv)
    example = load()

    if not args.apply:
        print("Would write (run with --apply):")
        for line in plan(example):
            print(f"  {line}")
        return 0

    url, token = os.environ.get("COHERENY_API_URL"), os.environ.get("COHERENY_TOKEN")
    if not url or not token:
        sys.exit("seed: set COHERENY_API_URL and COHERENY_TOKEN (admin, author surface)")
    members = {area: getattr(args, area) for area in AREAS}
    with httpx.Client(base_url=url, timeout=60,
                      headers={"Authorization": f"Bearer {token}"}) as c:
        apply(c, example, members)
    print("\nNext: approve the blocks and routing rules, check the areas' members, then\n"
          "validate and activate the workflow (deploy/examples/banking/README.md).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
