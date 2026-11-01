#!/usr/bin/env python3
"""
scripts/e2e_context_lifecycle.py

End-to-end check of the context repository against a running deployment
(CHY-71).

What it covers, in one pass: create a block, fail to publish it without review,
submit, reject with a reason, resubmit, approve, compose, edit while active,
compose again, approve the edit, retire, compose once more. Plus the history
and the audit trail that should have accumulated along the way — and, since
CHY-162, the routing governance of CHY-87: no rule refuses, an approved rule
answers.

Shape as well as values (CHY-124):
  Every response is checked against openapi.yaml by an httpx event hook, not
  only the fields a check happens to read. Before, a field could vanish from a
  response and all 36 checks stayed green, because each looked at the one
  value it cared about. The hook sees every call, so a new call cannot slip
  past it, and the contract it checks against is the one CI already holds
  against the code (ADR-029) — not a second copy kept by hand.

Why this exists as a script and not only as unit tests:
  On 2026-08-13 the suite was 333 tests green and the system had never once
  done its actual job — `actors`, `scope_nodes` and `context_blocks` were all
  empty on dev. The first manual run found a defect no unit test could have:
  retiring a block brought its previous version back into every composition,
  because each unit test works with a single version and the failure needs two.

  Mocked tests answer "does this function do what I think". This answers "does
  the thing work", which is a different question and the one nobody had asked.

Scope creation:
  Over the API, like everything else here (CHY-86). Pass --scope-id to reuse an
  existing scope, or omit it and one is created for the run. Until CHY-86 this
  script opened its own database connection and ran an INSERT, because creating
  a scope had no endpoint — an end-to-end check that needed database
  credentials to exercise an HTTP API.

Tokens (CHY-124, ADR-031):
  A token opens exactly one surface, and this run touches three — serve
  (compose, complete), author (blocks, scopes) and observe (audit log). So it
  carries one token per surface, fetched from that surface's machine client
  (`cohereny.<surface>.ci`, INFRA-122), and a request hook picks the token by
  the request's path. Which path belongs to which surface is read from
  contracts/openapi-<surface>.yaml — the files CI already holds against the
  router — not from a list kept here.

  The run then also checks that the separation holds on the real deployment:
  a token for one surface is turned away by another.

  Without --token-url no token is sent at all, which is what a target running
  with DEV_MODE=true expects. The former --token flag is gone: a single token
  can no longer carry a run that spans three surfaces.

Usage:
    python scripts/e2e_context_lifecycle.py --base-url https://...
    python scripts/e2e_context_lifecycle.py --base-url https://... --scope-id <uuid>
    COHERENY_CI_APP_PASSWORD=... python scripts/e2e_context_lifecycle.py \
        --base-url https://... --token-url https://auth.../application/o/token/

Exit code 0 when every check passed, 1 otherwise. Prints one line per check.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import time
from pathlib import Path

import httpx
import yaml
from jsonschema import Draft202012Validator

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kernel.surfaces import CI_AUDIENCES

_ok = 0
_failed: list[str] = []

#: How long the run waits for /v1/complete. The API gives a model 300 s
#: (core.completion.REQUEST_TIMEOUT_SECONDS) because a Raspberry Pi inference node
#: needs minutes for a long prompt; Ollama also unloads an idle model after five
#: minutes, so a run after a pause meets a cold start. The client's default 30 s
#: gave up while the server was still working — first seen in the dress
#: rehearsal of verify:test on 24.09. Not imported: the CI job installs three
#: packages, not the application. tests/test_e2e_surface_tokens.py holds the two
#: numbers together.
COMPLETE_TIMEOUT_S = 330.0


def check(label: str, condition: bool, detail: str = "") -> None:
    global _ok
    if condition:
        _ok += 1
        print(f"  \033[32m✓\033[0m {label}")
    else:
        _failed.append(label)
        print(f"  \033[31m✗\033[0m {label}{'  — ' + detail if detail else ''}")


class ContractCheck:
    """Validates responses against the committed contract (CHY-124).

    One instance per run. `observe` is an httpx response hook; it records every
    response whose operation and status the contract describes, and every
    mismatch with the first error message. A response the contract does not
    describe at all is recorded as well — an undocumented status code is the
    finding CHY-28 was about.
    """

    _CONTRACT = Path(__file__).resolve().parents[1] / "openapi.yaml"

    def __init__(self, contract: Path | None = None) -> None:
        spec = yaml.safe_load((contract or self._CONTRACT).read_text(encoding="utf-8"))
        self._components = spec.get("components", {})
        self._routes = [
            (re.compile("^" + re.sub(r"\{[^/]+\}", "[^/]+", path) + "$"), path, ops)
            for path, ops in spec["paths"].items()
        ]
        self.checked = 0
        self.mismatches: list[str] = []

    def _schema_for(self, method: str, url_path: str, status: int):
        for pattern, template, ops in self._routes:
            if pattern.match(url_path) and method.lower() in ops:
                responses = ops[method.lower()].get("responses", {})
                entry = responses.get(str(status))
                if entry is None:
                    return template, None, "undocumented status"
                schema = entry.get("content", {}).get("application/json", {}).get("schema")
                return template, schema, None
        return None, None, None

    def validate(self, method: str, url_path: str, status: int, body) -> str | None:
        """The first mismatch, or None. Public so a test can drive it without HTTP."""
        template, schema, problem = self._schema_for(method, url_path, status)
        if template is None:
            return None                     # not an API path of this contract
        self.checked += 1
        if problem:
            return f"{method} {template} → {status}: {problem}"
        if schema is None:
            return None                     # e.g. 204, nothing to compare
        # $refs point at #/components/...; resolving them against a root that
        # carries the components keeps the whole spec out of a registry.
        error = next(iter(Draft202012Validator(
            {**schema, "components": self._components}
        ).iter_errors(body)), None)
        if error is None:
            return None
        where = "/".join(str(p) for p in error.absolute_path) or "(root)"
        return f"{method} {template} → {status}: {where}: {error.message}"

    def observe(self, response: httpx.Response) -> None:
        response.read()
        # A 204 may still carry a JSON content type; an empty body has nothing
        # to compare, and parsing it would stop the run instead of checking it.
        is_json = response.headers.get("content-type", "").startswith("application/json")
        body = response.json() if is_json and response.content else None
        mismatch = self.validate(
            response.request.method, response.request.url.path, response.status_code, body
        )
        if mismatch:
            self.mismatches.append(mismatch)


class SurfaceTokens:
    """One token per surface, from that surface's machine client (CHY-124).

    `authorize` is an httpx request hook. It finds the surface of the request's
    path in the per-surface contracts and attaches that surface's token; a path
    in none of them (/v1/health) goes out without one, as it must.

    Tokens are fetched on first use and again shortly before they expire —
    Authentik issues them for ten minutes, and a run with model calls can come
    close to that.
    """

    _CONTRACTS = Path(__file__).resolve().parents[1] / "contracts"
    _REFRESH_MARGIN_S = 60

    def __init__(self, token_url: str, account: tuple[str, str], *,
                 contracts: Path | None = None, post=httpx.post, clock=time.monotonic) -> None:
        """`account` is (username, app password) — one credential, not two."""
        self._token_url = token_url
        self._username, self._password = account
        self._post = post
        self._clock = clock
        self._cache: dict[str, tuple[str, float]] = {}
        self._routes: list[tuple[re.Pattern, str, str]] = []
        for f in sorted((contracts or self._CONTRACTS).glob("openapi-*.yaml")):
            surface = f.stem.removeprefix("openapi-")
            for path, ops in yaml.safe_load(f.read_text(encoding="utf-8"))["paths"].items():
                pattern = re.compile("^" + re.sub(r"\{[^/]+\}", "[^/]+", path) + "$")
                self._routes.extend((pattern, method.upper(), surface) for method in ops)
        self.used: set[str] = set()

    def surface_for(self, method: str, url_path: str) -> str | None:
        """The one surface serving this operation, None for none.

        Two would be a contract defect, and guessing would send a token to a
        surface it was not issued for — so it stops the run instead.
        """
        hits = {s for pattern, m, s in self._routes if m == method.upper() and pattern.match(url_path)}
        if len(hits) > 1:
            msg = f"{method} {url_path} is in several surface contracts: {sorted(hits)}"
            raise SystemExit(msg)
        return next(iter(hits), None)

    def token_for(self, surface: str) -> str:
        cached = self._cache.get(surface)
        now = self._clock()
        if cached and cached[1] - now > self._REFRESH_MARGIN_S:
            return cached[0]
        client_id = CI_AUDIENCES[surface]
        resp = self._post(self._token_url, data={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "username": self._username,
            "password": self._password,
            "scope": "openid profile",
        }, timeout=30.0)
        if resp.status_code != httpx.codes.OK:
            # Authentik answers every refusal with invalid_grant; the reason is
            # only in its server log. Name the usual suspects instead.
            msg = (
                f"no token from {client_id}: HTTP {resp.status_code} {resp.text[:200]}\n"
                "  Usual causes: the app password of the cohereny-ci account expired "
                "(INFRA-122), COHERENY_CI_APP_PASSWORD is stale after a "
                "rebuild of Authentik, or the account is not bound to this application. "
                "The reason is in the Authentik server log (`docker logs authentik-server`)."
            )
            raise SystemExit(msg)
        body = resp.json()
        self._cache[surface] = (body["access_token"], now + float(body.get("expires_in", 0)))
        return body["access_token"]

    def authorize(self, request: httpx.Request) -> None:
        surface = self.surface_for(request.method, request.url.path)
        if surface is None:
            return
        self.used.add(surface)
        request.headers["Authorization"] = f"Bearer {self.token_for(surface)}"


def section(title: str) -> None:
    print(f"\n{title}")


def _ensure_scope(c: httpx.Client) -> str:
    """Create a throwaway scope over the API. Only used when no --scope-id was given.

    Until CHY-86 this opened its own database connection and ran an INSERT,
    because creating a scope had no endpoint. An end-to-end check of an HTTP API
    that needs database credentials is not an end-to-end check of an HTTP API —
    and the gap it papered over was real: the system could not be put into
    service through its own interface.
    """
    resp = c.post("/v1/scopes", json={
        "name": "e2e", "node_type": "org",
        "description": "created by e2e_context_lifecycle.py",
    })
    if resp.status_code == 403:
        print("POST /v1/scopes refused: the caller needs the architect role. "
              "cohereny-ci holds it through chy-architects (INFRA-122); a target "
              "with DEV_MODE=true passes without a token.", file=sys.stderr)
        raise SystemExit(2)
    resp.raise_for_status()
    return resp.json()["id"]


def _check_an_unbounded_rule_is_refused(c: httpx.Client, scope_id: str) -> None:
    """A rule without a length limit cannot be approved (CHY-166).

    And the approver hears it as a 422, not a 500. A second task type, so the
    rule section 7 approved keeps answering; this one stays in review in the
    run's scope.
    """
    r = c.post("/v1/blocks", json={
        "scope_id": scope_id,
        "block_type": "routing_rule",
        "content": "E2E: a rule without max_tokens, to be refused at approval.",
        "metadata": {"task_type": "classify", "candidates": ["phi3-mini"]},
    })
    if r.status_code != httpx.codes.CREATED:
        check("a rule without max_tokens can be drafted", False,
              f"HTTP {r.status_code}: {r.text[:160]}")
        return
    unbounded = r.json()["block_id"]
    c.post(f"/v1/blocks/{unbounded}/submit", json={"version": 1})
    r = c.post(f"/v1/blocks/{unbounded}/approve", json={"version": 1})
    check("a rule without max_tokens is refused at approval",
          r.status_code == httpx.codes.UNPROCESSABLE_ENTITY and "max_tokens" in r.text,
          f"HTTP {r.status_code}: {r.text[:160]}")


def _check_a_direct_scope(c: httpx.Client, parent_id: str) -> None:
    """In a personal scope a block goes live on write (ADR-042, CHY-65).

    Acceptance criteria 1 and 3 through the deployed stack: a personal scope
    below the run's scope, one block, active at once, composed, and two audit
    entries marked `direct` — in the order they were written (migration 026).
    Then promoted into the run's scope, where it waits as a draft.
    """
    r = c.post("/v1/scopes", json={
        "name": "e2e-personal", "node_type": "personal", "parent_id": parent_id,
        "description": "created by e2e_context_lifecycle.py (ADR-042)",
    })
    if r.status_code != httpx.codes.CREATED:
        check("a personal scope can be created", False, f"HTTP {r.status_code}: {r.text[:160]}")
        return
    personal = r.json()["id"]

    r = c.post("/v1/blocks", json={
        "scope_id": personal, "block_type": "system_prompt",
        "content": "E2E: live on write, no reviewer.",
    })
    check("a block in a personal scope is active at once",
          r.status_code == httpx.codes.CREATED and r.json().get("status") == "active",
          f"HTTP {r.status_code}: {r.text[:160]}")
    if r.status_code != httpx.codes.CREATED:
        return
    bid = r.json()["block_id"]

    r = c.post("/v1/compose", json={"scope_id": personal, "refresh": True})
    check("and composed", any(b["block_id"] == bid for b in r.json().get("blocks", [])),
          f"HTTP {r.status_code}")

    entries = c.get("/v1/audit-log", params={"target": f"block:{bid}"}).json()
    # Newest first from the API; read as it happened.
    steps = [(e["action"], e["payload"].get("mode")) for e in reversed(entries)]
    check("the audit shows both steps, marked direct",
          steps == [("block.submitted", "direct"), ("block.approved", "direct")], str(steps))

    # Acceptance criterion 3: up into the run's scope, as a draft; the original
    # stays active. The run's own scope is an org — strict — unless --scope-id
    # named something else.
    r = c.post(f"/v1/blocks/{bid}/promote", json={"target_scope_id": parent_id})
    copy = r.json() if r.status_code == httpx.codes.CREATED else {}
    check("promote puts a draft into the scope above",
          copy.get("status") == "draft" and copy.get("scope_id") == parent_id,
          f"HTTP {r.status_code}: {r.text[:160]}")
    original = c.get(f"/v1/blocks/{bid}").json()
    check("and leaves the original as it was",
          (original.get("status"), original.get("version")) == ("active", 1), str(original)[:160])


def _check_calibration_anchors(c: httpx.Client, scope_id: str) -> None:
    """A test case is not prompt material (ADR-043, CHY-67).

    A complete anchor is approved in the run's scope and must not appear in
    what that scope composes — it carries the answer a benchmark measures. An
    incomplete one is refused at approval with the reason, not activated to
    fail at the first run.
    """
    case = {"task_type": "summarize", "input": "E2E: a credit file.",
            "raw_prompt": "Summarize the credit file.",
            "validators": {"required_terms": ["loan"]}}
    anchors = {}
    for name, metadata in (("complete", case),
                           ("incomplete", {k: v for k, v in case.items() if k != "raw_prompt"})):
        r = c.post("/v1/blocks", json={
            "scope_id": scope_id, "block_type": "calibration_anchor",
            "content": "E2E: the loan is approved.", "metadata": metadata,
        })
        if r.status_code != httpx.codes.CREATED:
            check(f"an {name} anchor can be drafted", False, f"HTTP {r.status_code}: {r.text[:160]}")
            return
        anchors[name] = r.json()["block_id"]
        c.post(f"/v1/blocks/{anchors[name]}/submit", json={"version": 1})

    r = c.post(f"/v1/blocks/{anchors['incomplete']}/approve", json={"version": 1})
    check("an anchor without raw_prompt is refused at approval",
          r.status_code == httpx.codes.UNPROCESSABLE_ENTITY and "raw_prompt" in r.text,
          f"HTTP {r.status_code}: {r.text[:160]}")
    r = c.post(f"/v1/blocks/{anchors['complete']}/approve", json={"version": 1})
    check("a complete anchor is approved", r.status_code == httpx.codes.OK, f"HTTP {r.status_code}: {r.text[:160]}")

    r = c.post("/v1/compose", json={"scope_id": scope_id, "refresh": True})
    composed = {b["block_id"] for b in r.json().get("blocks", [])}
    check("and is never composed", anchors["complete"] not in composed,
          "the expected answer of a benchmark case went into the prompt")

    r = c.get("/v1/benchmarks")
    check("benchmark runs can be read", r.status_code == httpx.codes.OK and "items" in r.json(),
          f"HTTP {r.status_code}: {r.text[:160]}")


def client_for(base_url: str, tokens: SurfaceTokens | None,
               contract: ContractCheck | None = None) -> httpx.Client:
    """The client every step uses. Tokens go on by request hook, so a call
    added later cannot forget its surface's token."""
    hooks: dict[str, list] = {"request": [tokens.authorize] if tokens else [],
                              "response": [contract.observe] if contract else []}
    return httpx.Client(base_url=base_url, timeout=30.0, event_hooks=hooks)


def run(base_url: str, scope_id: str, tokens: SurfaceTokens | None) -> int:
    contract = ContractCheck()
    c = client_for(base_url, tokens, contract)

    def compose() -> list[dict]:
        # refresh=True throughout: the cache is exercised separately below, and
        # a stale hit here would make a later step pass for the wrong reason.
        r = c.post("/v1/compose", json={"scope_id": scope_id, "refresh": True})
        r.raise_for_status()
        return r.json()["blocks"]

    section("1 — create")
    r = c.post("/v1/blocks", json={
        "scope_id": scope_id,
        "block_type": "system_prompt",
        "content": "Answer concisely.",
        "metadata": {"lang": "en"},
        "tags": ["style"],
    })
    check("block created", r.status_code == 201, f"HTTP {r.status_code}: {r.text[:200]}")
    if r.status_code != 201:
        return 1
    block = r.json()
    bid = block["block_id"]
    check("starts as draft", block["status"] == "draft", block["status"])
    check("author recorded", block["author_id"] is not None,
          "author_id is NULL — the caller was not resolved to an actor")

    section("2 — the review gate")
    check("a draft is not composed", compose() == [])
    r = c.post(f"/v1/blocks/{bid}/approve", json={"version": 1})
    check("cannot skip review", r.status_code == 409, f"HTTP {r.status_code}")

    section("3 — submit, reject, resubmit, approve")
    r = c.post(f"/v1/blocks/{bid}/submit", json={"version": 1})
    check("submitted", r.status_code == 200 and r.json()["status"] == "pending_review")
    r = c.post(f"/v1/blocks/{bid}/reject", json={"version": 1})
    check("a rejection needs a reason", r.status_code == 422, f"HTTP {r.status_code}")
    r = c.post(f"/v1/blocks/{bid}/reject", json={"version": 1, "reason": "too terse"})
    check("rejected", r.status_code == 200 and r.json()["status"] == "rejected")
    check("a rejected block is not composed", compose() == [])
    c.post(f"/v1/blocks/{bid}/submit", json={"version": 1})
    r = c.post(f"/v1/blocks/{bid}/approve", json={"version": 1})
    check("approved", r.status_code == 200 and r.json()["status"] == "active")

    section("4 — composition")
    blocks = compose()
    check("the approved block is composed", len(blocks) == 1, f"{len(blocks)} blocks")
    check("with its content", blocks and blocks[0]["content"] == "Answer concisely.")
    r = c.post("/v1/compose", json={"scope_id": scope_id})
    c.post("/v1/compose", json={"scope_id": scope_id})
    r = c.post("/v1/compose", json={"scope_id": scope_id})
    check("a second call hits the cache", r.json()["cache_hit"] is True)

    section("5 — editing a live block must not take it offline")
    r = c.patch(f"/v1/blocks/{bid}", json={
        "content": "Answer concisely and cite sources.",
        "change_reason": "sources required",
    })
    check("edit creates a new version", r.status_code == 201 and r.json()["version"] == 2,
          f"HTTP {r.status_code}")
    check("the new version is a draft", r.json().get("status") == "draft")
    blocks = compose()
    check("the approved version is still composed", len(blocks) == 1 and blocks[0]["version"] == 1,
          f"got {[b['version'] for b in blocks]} — an edit took the block offline")

    section("6 — approving the edit")
    r = c.post(f"/v1/blocks/{bid}/approve", json={"version": 2})
    check("a stale version is refused", r.status_code == 409,
          "approve without submitting should not succeed")
    c.post(f"/v1/blocks/{bid}/submit", json={"version": 2})
    r = c.post(f"/v1/blocks/{bid}/approve", json={"version": 1})
    check("the wrong version is refused", r.status_code == 409, f"HTTP {r.status_code}")
    r = c.post(f"/v1/blocks/{bid}/approve", json={"version": 2})
    check("approved", r.status_code == 200)
    blocks = compose()
    check("the new version is composed", len(blocks) == 1 and blocks[0]["version"] == 2,
          f"got {[b['version'] for b in blocks]}")

    section("7 — /v1/complete: no rule, no model; an approved rule, an answer")
    # Since CHY-87 the model is chosen by an approved routing rule in the scope
    # tree, and there is no deployment-wide default. This script creates a fresh
    # scope and, until CHY-162, no rule — so /v1/complete answered 409 and
    # `verify:dev` was red after every merge for sixteen days. It ran after the
    # merge, where nobody was looking.
    #
    # The 409 is kept, as the counter-probe: without a decision, no model.
    complete = {"task_type": "summarize", "prompt": "ping", "scope_id": scope_id}
    r = c.post("/v1/complete", json=complete)
    check("without a routing rule, complete refuses",
          r.status_code == httpx.codes.CONFLICT,
          f"HTTP {r.status_code}: {r.text[:160]}")

    # Local models only. Since CHY-157 the router knows hosted ones, and until
    # CHY-160 compares a scope's data class with a model's ceiling, no rule may
    # name one. Same candidates the seed carried over from the YAML (CHY-87).
    r = c.post("/v1/blocks", json={
        "scope_id": scope_id,
        "block_type": "routing_rule",
        "content": "E2E: summarize runs locally, a second host as fallback.",
        # max_tokens is required since CHY-166. Small on purpose: this checks
        # routing, not writing, and with the local Apple-silicon host off the answer
        # comes from the Raspberry Pi — at 0.9 tokens/s when throttled (INFRA-128), an unbounded
        # answer to "ping" took five minutes on 25.09.
        "metadata": {"task_type": "summarize", "candidates": ["gemma3-4b", "phi3-mini"],
                     "max_tokens": 32},
    })
    check("routing rule created", r.status_code == httpx.codes.CREATED,
          f"HTTP {r.status_code}: {r.text[:160]}")
    if r.status_code == httpx.codes.CREATED:
        rule = r.json()["block_id"]
        c.post(f"/v1/blocks/{rule}/submit", json={"version": 1})
        r = c.post(f"/v1/blocks/{rule}/approve", json={"version": 1})
        check("routing rule approved", r.status_code == httpx.codes.OK,
              f"HTTP {r.status_code}")
        # ADR-037: configuration, not prompt material. Checked here because
        # this is the first place a rule and a composable block share a scope.
        check("the rule is not composed into the prompt", len(compose()) == 1,
              f"{len(compose())} blocks")

        # CHY-166, against the real deployment: the rule's limit is a ceiling.
        # Refused before any model is asked, so this costs no model time.
        r = c.post("/v1/complete", json={**complete, "max_tokens": 64})
        check("complete refuses a max_tokens above the rule's",
              r.status_code == httpx.codes.UNPROCESSABLE_ENTITY and "at most 32" in r.text,
              f"HTTP {r.status_code}: {r.text[:160]}")

    _check_an_unbounded_rule_is_refused(c, scope_id)

    # Until CHY-24 this endpoint ran its own SELECT: flat by scope (an
    # org-level block never reached a project), filtered on
    # superseded_by_version (so an edit took it offline), ordered differently
    # (so the same context produced a different prompt), and uncached. It now
    # goes through the same composition.
    requested = 16
    r = c.post("/v1/complete", json={**complete, "max_tokens": requested},
               timeout=COMPLETE_TIMEOUT_S)
    if r.status_code == 200:
        check("the answer came from a candidate of the rule",
              r.json()["model_used"] in ("gemma3-4b", "phi3-mini"),
              r.json()["model_used"])
        # The gateway reports usage; the limit is the caller's, below the
        # rule's 32. Not every backend reports it, hence the None.
        used = r.json()["output_tokens"]
        check("the answer kept to the requested length",
              used is None or used <= requested, f"output_tokens={used}")
        check("complete used the composed context", r.json()["blocks_used"] == 1,
              f"blocks_used={r.json()['blocks_used']}")
    else:
        # LiteLLM may be unreachable from wherever this runs; the composition
        # still happened, and a routing failure is not what this check is about.
        check("complete reached the model gateway", r.status_code in (502, 503),
              f"HTTP {r.status_code}: {r.text[:160]}")

    section("8 — history and audit trail")
    h = c.get(f"/v1/blocks/{bid}/history").json()
    check("two versions", h["total"] == 2, str(h["total"]))
    check("exactly one is current", sum(v["is_current"] for v in h["versions"]) == 1)
    entries = c.get("/v1/audit-log", params={"target": f"block:{bid}"}).json()
    actions = [e["action"] for e in entries]
    for expected in ("block.submitted", "block.rejected", "block.approved", "block.superseded"):
        check(f"audit records {expected}", expected in actions)
    rejection = next((e for e in entries if e["action"] == "block.rejected"), None)
    check("the rejection reason is readable",
          bool(rejection and rejection["payload"].get("reason")))

    section("9 — a direct scope goes live on write, and promotes upward (ADR-042)")
    _check_a_direct_scope(c, scope_id)

    section("10 — a calibration anchor is a test case, not prompt material (ADR-043)")
    _check_calibration_anchors(c, scope_id)

    section("11 — retirement")
    r = c.delete(f"/v1/blocks/{bid}")
    check("retired", r.status_code == 204, f"HTTP {r.status_code}")
    blocks = compose()
    # The case the first manual run caught: retiring only the current version
    # let the superseded-but-active predecessor win the "newest active" query.
    check("nothing is composed any more", blocks == [],
          f"got {[b['version'] for b in blocks]} — a retired block is still being served")
    r = c.delete(f"/v1/blocks/{bid}")
    check("retiring twice is harmless", r.status_code == 204, f"HTTP {r.status_code}")

    section("12 — every response matched the contract")
    check(f"{contract.checked} responses checked against openapi.yaml",
          contract.checked > 0, "the hook saw nothing — it is not attached")
    for mismatch in contract.mismatches:
        check("response matches the contract", False, mismatch)
    if not contract.mismatches:
        check("no response deviated from the contract", True)

    if tokens is not None:
        section("13 — one token, one surface (ADR-031)")
        check(f"tokens used for {sorted(tokens.used)}",
              {"serve", "author", "observe"} <= tokens.used,
              "a surface this run needs got no token of its own")
        # The gate against the real deployment, with real tokens: the author
        # token is refused by observe, the observe token by author. The unit
        # tests prove the rule; this proves the deployment enforces it.
        #
        # Each request is built so that without the gate it would NOT be a 403:
        # the audit log with a target needs only reviewer (cohereny-ci is
        # architect) and would answer 200; an empty block body would be a 422.
        # And the detail must be the gate's own wording — a 403 from a role
        # check would otherwise pass this for the wrong reason.
        foreign = [
            ("GET", "/v1/audit-log", {"params": {"target": f"block:{bid}"}}, "author"),
            ("POST", "/v1/blocks", {"json": {}}, "observe"),
        ]
        bare = httpx.Client(base_url=base_url, timeout=30.0)
        for method, path, kwargs, surface in foreign:
            r = bare.request(method, path, **kwargs,
                             headers={"Authorization": f"Bearer {tokens.token_for(surface)}"})
            check(f"{method} {path} refuses the {surface} token",
                  r.status_code == httpx.codes.FORBIDDEN and "issued for" in r.text,
                  f"HTTP {r.status_code}: {r.text[:160]}")
        bare.close()

    c.close()
    return 0 if not _failed else 1


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--base-url", required=True)
    p.add_argument("--scope-id", help="Existing scope. Omit to have one created over the API.")
    p.add_argument("--token-url", help="Authentik token endpoint. Omit when the target "
                                       "runs with DEV_MODE=true; then no token is sent.")
    p.add_argument("--client-user", default="cohereny-ci",
                   help="Service account whose app password is in COHERENY_CI_APP_PASSWORD.")
    args = p.parse_args()

    tokens = None
    if args.token_url:
        password = os.environ.get("COHERENY_CI_APP_PASSWORD", "")
        if not password:
            p.error("--token-url needs COHERENY_CI_APP_PASSWORD in the environment "
                    "(never on the command line: it would show up in the job log)")
        tokens = SurfaceTokens(args.token_url, (args.client_user, password))

    scope_id = args.scope_id
    if not scope_id:
        with client_for(args.base_url, tokens) as c:
            scope_id = _ensure_scope(c)
        print(f"created scope {scope_id}")

    print(f"target: {args.base_url}   scope: {scope_id}   "
          f"tokens: {'per surface' if tokens else 'none (DEV_MODE)'}")
    rc = run(args.base_url, scope_id, tokens)
    total = _ok + len(_failed)
    print(f"\n{_ok}/{total} checks passed")
    if _failed:
        print("failed: " + ", ".join(_failed))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
