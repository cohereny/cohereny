#!/usr/bin/env python3
"""
deploy/quickstart/seed.py

The quickstart's example, written through the API once (CHY-189).

What it builds — a customer service that drafts replies with a local model and
lets a person approve them:

    Example Corp                       org, strict, data class `confidential`
      tone of voice                    system_prompt
      customer data stays local        user_context, governance claim `cloud_routing`
    └─ Customer Support                team, strict
         product facts                 user_context
         an example reply              example_io
         routing: draft_reply, summarize → the local model
       └─ Order inquiries              project, direct: blocks are live at once
            current carrier delay      user_context
            workflow "Draft a reply to a customer"
              compose → draft (llm_call) → review (human_review)

Why through the API and not as SQL: the example should be made the way a user
would make it, so it shows what the API asks for — and so the audit log of a
fresh quickstart already tells the story. Two people are involved on purpose:
`admin` writes and submits, `reviewer` approves. In the strict scopes nothing
goes live on the say of its author alone. The reviewer is a member of Example
Corp: a run is visible only to whoever started it, to the members of its scope
and the scopes above, and to admin (CHY-92) — without the membership the
reviewer would not find the run they are to decide.

A second run finds "Example Corp" and leaves the example as it is. It only adds
the reviewer's membership if it is missing: a quickstart seeded before
2026-11-08 has none, and after an update its reviewer would see no run
(CHY-249). QUICKSTART_SEED=false skips it altogether.

Environment:
    API_URL           where the API is reached (Compose: http://api:8000)
    ISSUER_URL        where the development issuer is reached (Compose: http://issuer:9010)
    QUICKSTART_MODEL  the model the routing rules name; must be declared in
                      routing-config.yaml, or approving the rules is refused
    QUICKSTART_SEED   "false" to skip

Standard library plus httpx, which the API depends on anyway.
"""
from __future__ import annotations

import os
import sys
import time

import httpx

API = os.environ.get("API_URL", "http://127.0.0.1:8000").rstrip("/")
ISSUER = os.environ.get("ISSUER_URL", "http://127.0.0.1:9010").rstrip("/")
MODEL = os.environ.get("QUICKSTART_MODEL", "qwen2.5:1.5b")

ORG = "Example Corp"
TEAM = "Customer Support"
PROJECT = "Order inquiries"
WORKFLOW = "Draft a reply to a customer"

#: The task type the workflow's model call is routed by. A routing rule in the
#: team scope names the model; the workflow never does (ADR-038).
DRAFT_TASK = "draft_reply"

TONE = (
    "You write replies to customers on behalf of Example Corp. Be friendly, brief and "
    "concrete. First check the current situation below: if it affects the customer's "
    "order, say so first and what it means for them. Then answer from the facts only; "
    "if they do not answer the question, say that a colleague will follow up. Never "
    "invent dates, numbers or order details. You are the support team, so never send "
    "the customer to support. End with: The Example Corp support team"
)

DATA_RULE = (
    "Customer messages contain personal data. They are processed by models that "
    "run on our own infrastructure only and are never sent to a cloud service."
)

PRODUCT_FACTS = (
    "Shipping: orders leave the warehouse within one business day and arrive "
    "within 2-4 business days inside the EU. A tracking link is e-mailed when "
    "the parcel leaves the warehouse.\n"
    "Returns: unused items can be returned within 30 days. Refunds are issued "
    "within 5 business days after the return arrives."
)

#: Deliberately without dates or durations: a small model copies them into
#: every answer. The first version began "I returned my order two weeks ago",
#: and each draft then told the customer it had been two weeks.
EXAMPLE_REPLY = (
    "Customer: Can I still return the toaster I bought?\n"
    "Reply: Yes. Unused items can be returned within 30 days of delivery, and the "
    "refund follows within 5 business days after the return reaches us.\n"
    "The Example Corp support team"
)

CARRIER_NOTICE = (
    "Current situation: because of a carrier delay, orders placed between "
    "Monday and Wednesday this week arrive about two business days later than "
    "usual. Tracking links work as normal."
)

RULE_REASON = (
    "Customer messages contain personal data (see the data rule of Example "
    "Corp), so replies are drafted by the local model only. Length limited to "
    "what a support reply needs."
)


# ── talking to the API ──────────────────────────────────────────────────────

def token(user: str, surface: str) -> str:
    """A token for `user` on `surface`, from the development issuer.

    The machine client of each surface (`cohereny.<surface>.ci`), in the shape
    Authentik's client_credentials takes. The issuer checks no password.
    """
    resp = httpx.post(f"{ISSUER}/application/o/token/", data={
        "grant_type": "client_credentials", "client_id": f"cohereny.{surface}.ci",
        "username": user, "password": "unchecked",
    }, timeout=10)
    resp.raise_for_status()
    return resp.json()["access_token"]


def client(user: str, surface: str = "author") -> httpx.Client:
    """An API client acting as `user` on one surface (ADR-031)."""
    return httpx.Client(base_url=API, timeout=120,
                        headers={"Authorization": f"Bearer {token(user, surface)}"})


def expect(resp: httpx.Response, status: int, what: str) -> dict:
    """The JSON body, or stop with what failed and what the API said."""
    if resp.status_code != status:
        sys.exit(f"seed: {what} failed — HTTP {resp.status_code}: {resp.text[:400]}")
    return resp.json() if resp.content else {}


def wait_for_api(seconds: int = 120) -> None:
    for _ in range(seconds):
        try:
            if httpx.get(f"{API}/v1/health", timeout=2).status_code == 200:
                return
        except httpx.TransportError:
            pass
        time.sleep(1)
    sys.exit(f"seed: the API at {API} did not answer within {seconds}s")


# ── the example ─────────────────────────────────────────────────────────────

def scope(c: httpx.Client, name: str, node_type: str, parent: str | None = None,
          **extra) -> str:
    body = {"name": name, "node_type": node_type, "parent_id": parent,
            "description": "Created by the quickstart (deploy/quickstart/seed.py).", **extra}
    sid = expect(c.post("/v1/scopes", json=body), 201, f"creating scope {name!r}")["id"]
    print(f"  scope      {name:20} {node_type:8} {sid}")
    return sid


def member(admin: httpx.Client, person: httpx.Client, scope_id: str, label: str) -> bool:
    """Add `person` to a scope unless they are in it already (CHY-92, CHY-249).

    By the actor id they read from /v1/me, which lists their memberships, too.
    True if they were added.
    """
    me = expect(person.get("/v1/me"), 200, f"reading the {label}'s actor id")
    if any(m["scope_id"] == scope_id for m in me["memberships"]):
        return False
    expect(admin.post(f"/v1/scopes/{scope_id}/members",
                      json={"actor_id": me["actor_id"], "role": "reviewer"}),
           201, f"adding the {label} to the scope")
    print(f"  member     {label:29} sees the runs of {ORG!r} and below")
    return True


def reviewed_block(author: httpx.Client, reviewer: httpx.Client, label: str, **body) -> str:
    """A block in a strict scope: written and submitted by one person, approved by another."""
    created = expect(author.post("/v1/blocks", json=body), 201, f"creating {label}")
    bid = created["block_id"]
    expect(author.post(f"/v1/blocks/{bid}/submit", json={"version": 1}), 200,
           f"submitting {label}")
    expect(reviewer.post(f"/v1/blocks/{bid}/approve", json={"version": 1}), 200,
           f"approving {label}")
    print(f"  block      {label:29} approved by reviewer")
    return bid


def direct_block(author: httpx.Client, label: str, **body) -> str:
    """A block in a direct scope (project): live on write, no review (ADR-042)."""
    created = expect(author.post("/v1/blocks", json=body), 201, f"creating {label}")
    print(f"  block      {label:29} {created['status']} at once (direct scope)")
    return created["block_id"]


def routing_rule(author: httpx.Client, reviewer: httpx.Client, scope_id: str,
                 task_type: str, max_tokens: int) -> None:
    reviewed_block(
        author, reviewer, f"routing rule {task_type}",
        scope_id=scope_id, block_type="routing_rule", content=RULE_REASON,
        metadata={"task_type": task_type, "candidates": [MODEL], "max_tokens": max_tokens},
    )


def workflow(author: httpx.Client, reviewer: httpx.Client, scope_id: str) -> str:
    """compose → draft → review, activated by the reviewer."""
    wf = expect(author.post("/v1/workflows", json={
        "scope_id": scope_id, "name": WORKFLOW,
        "description": "The model drafts a reply from the composed context; "
                       "a person approves it before it is sent.",
    }), 201, "creating the workflow")["id"]

    nodes = (
        {"node_ref": "compose", "node_type": "context_compose",
         "name": "Assemble what Example Corp knows"},
        # The composed context becomes the model's instructions, the customer's
        # message its request — the split /v1/complete makes, too.
        {"node_ref": "draft", "node_type": "llm_call", "name": "Draft the reply",
         "task_type": DRAFT_TASK,
         "input_mapping": {"system": "compose.prompt", "prompt": "run.message"}},
        {"node_ref": "review", "node_type": "human_review",
         "name": "A person approves the draft", "review_role": "approver"},
    )
    for node in nodes:
        expect(author.post(f"/v1/workflows/{wf}/nodes", json=node), 201,
               f"adding node {node['node_ref']}")
    for source, target in (("compose", "draft"), ("draft", "review")):
        expect(author.post(f"/v1/workflows/{wf}/connections",
                           json={"source_node_ref": source, "target_node_ref": target}),
               201, f"connecting {source} → {target}")

    expect(reviewer.put(f"/v1/workflows/{wf}", json={"status": "active"}), 200,
           "activating the workflow")
    print(f"  workflow   {WORKFLOW!r} active, {wf}")
    return wf


def seeded_org(c: httpx.Client) -> str | None:
    """The id of Example Corp if an earlier run wrote the example, else None."""
    scopes = expect(c.get("/v1/scopes"), 200, "listing scopes")
    return next((s["id"] for s in scopes if s["name"] == ORG and s["node_type"] == "org"),
                None)


def main() -> int:
    if os.environ.get("QUICKSTART_SEED", "true").lower() == "false":
        print("seed: QUICKSTART_SEED=false — starting with an empty database")
        return 0

    wait_for_api()
    admin, reviewer = client("admin"), client("reviewer")
    org = seeded_org(admin)
    if org:
        # The CI checks a second start by "nothing changed" (.gitlab-ci.yml).
        if member(admin, reviewer, org, "reviewer"):
            print(f"seed: {ORG!r} exists — the example is already there; "
                  "the reviewer's membership was missing and is added")
        else:
            print(f"seed: {ORG!r} exists — the example is already there, nothing changed")
        return 0

    print(f"seed: writing the example (model {MODEL})")
    org = scope(admin, ORG, "org", privacy_level="confidential")
    team = scope(admin, TEAM, "team", org)
    project = scope(admin, PROJECT, "project", team)
    member(admin, reviewer, org, "reviewer")

    reviewed_block(admin, reviewer, "tone of voice",
                   scope_id=org, block_type="system_prompt", content=TONE)
    # A governance claim: restrictive and locked, so no scope below Example Corp
    # may send customer messages to a cloud model, not even by saying so more
    # strictly (ADR-017).
    reviewed_block(admin, reviewer, "data rule (cloud_routing)",
                   scope_id=org, block_type="user_context", content=DATA_RULE,
                   governance_key="cloud_routing", privacy_impact="restrictive",
                   locked=True)
    reviewed_block(admin, reviewer, "product facts",
                   scope_id=team, block_type="user_context", content=PRODUCT_FACTS)
    reviewed_block(admin, reviewer, "example reply",
                   scope_id=team, block_type="example_io", content=EXAMPLE_REPLY)
    routing_rule(admin, reviewer, team, DRAFT_TASK, max_tokens=300)
    routing_rule(admin, reviewer, team, "summarize", max_tokens=200)
    direct_block(admin, "carrier delay notice",
                 scope_id=project, block_type="user_context", content=CARRIER_NOTICE)
    wf = workflow(admin, reviewer, project)

    print(f"""
seed: done. Next, from deploy/quickstart/README.md:
  project scope  {project}
  workflow       {wf}
  API docs       http://127.0.0.1:{os.environ.get('API_PORT', '8000')}/docs""")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
