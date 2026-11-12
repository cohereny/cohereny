#!/usr/bin/env python3
"""
scripts/contract_fuzz.py

Schemathesis against a running cohereny, one run per surface contract (CHY-124).

The contracts in contracts/ are generated from the code and held against it by
tests/test_openapi_contract.py (ADR-029). That proves the file describes the
code. It does not prove the code keeps the promises the file makes: bounds,
error codes, response shapes under inputs nobody wrote a test for. Schemathesis
derives those inputs from the contract itself — no second, hand-kept source of
truth next to it, which is why CHY-124 chose it over a Postman collection.

Introduced like the ruff families: first as a report, then strict. Findings are
printed and written as JUnit; without `--strict` they do not fail the run. The
first run on 2026-09-24 found five classes of 500 for client mistakes, the
first run with everything merged two more (CHY-165). The CI job runs with
`--strict` since 2026-09-25, after all four surfaces ran clean.

Two checks since CHY-168. Until then only `not_a_server_error` ran, and the
shapes named above went unchecked: on 58 operations a 422 could answer with
a sentence where the contract promised a list, and no check would have said
so. `response_schema_conformance` holds every documented answer's body to its
schema.

The counter-probe is always strict. A fuzzer that reports nothing is only
worth something if it would have reported a broken contract, so a copy of the
serve contract is broken on purpose — `UnprocessableDetail.detail` may only be
a string, so the list a validation 422 carries no longer fits — and the run
against it must fail while the run against the real contract passes. Both
directions, or the probe could pass for the wrong reason. Until CHY-168 the
broken schema was FastAPI's `HTTPValidationError`, which the contract no longer
contains.

Planned endpoints (`x-cohereny-status: planned`) are excluded: they are meant
to answer 501, and that is not a finding.

Tokens (CHY-192): each surface is fuzzed with a token of its own machine
client, fetched from --token-url — in CI that is scripts/dev_issuer.py. Until
CHY-192 the target ran with DEV_MODE and no token was sent at all. Without
that switch a missing or rejected token means 401 on every request, and 401 is
not a server error: the run would pass having tested nothing past the door. So
before each surface is fuzzed, its token has to be let in at least once.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kernel.surfaces import CI_AUDIENCES

ROOT = Path(__file__).resolve().parent.parent
SURFACES = ("serve", "author", "observe", "admin")
PLANNED = '/x-cohereny-status == "planned"'
METHODS = ("get", "post", "put", "patch", "delete")
NIL_UUID = "00000000-0000-0000-0000-000000000000"


def _wait_for(url: str, seconds: int = 30) -> None:
    for _ in range(seconds):
        try:
            httpx.get(f"{url}/v1/health", timeout=2).raise_for_status()
        except httpx.HTTPError:
            time.sleep(1)
        else:
            return
    sys.exit(f"{url} did not answer /v1/health within {seconds}s")


def token(token_url: str, surface: str, user: str, password: str) -> str:
    """An access token for one surface's machine client (INFRA-122 shape).

    Retried for a while on connection errors: in CI the issuer is started in
    the background moments before this runs.
    """
    for _ in range(30):
        try:
            resp = httpx.post(token_url, data={
                "grant_type": "client_credentials",
                "client_id": CI_AUDIENCES[surface],
                "username": user,
                "password": password,
                "scope": "openid profile",
            }, timeout=10)
        except httpx.TransportError:
            time.sleep(1)
            continue
        if resp.status_code != httpx.codes.OK:
            sys.exit(f"no token for {surface}: HTTP {resp.status_code} {resp.text[:200]}")
        return resp.json()["access_token"]
    sys.exit(f"{token_url} did not answer within 30s")


def let_in(url: str, contract: Path, bearer: str) -> bool:
    """True if the surface accepts the token on at least one operation.

    Path parameters get the nil UUID and request bodies `{}`: a 404 for an
    object that does not exist, or a 422 for the missing fields, is the answer
    of a caller who got past the door, which is all this asks. Reads first,
    then operations without parameters — the admin surface has a single
    operation, and it takes one (`/v1/admin/actors/{actor_ref}/anonymize`).
    """
    spec = yaml.safe_load(contract.read_text(encoding="utf-8"))
    ops = [(path, method)
           for path, item in spec["paths"].items()
           for method, op in item.items() if method in METHODS
           and op.get("x-cohereny-status") != "planned"]
    ops.sort(key=lambda o: (o[1] != "get", "{" in o[0]))
    headers = {"Authorization": f"Bearer {bearer}"}
    for path, method in ops:
        body = {} if method in ("post", "put", "patch") else None
        concrete = re.sub(r"\{[^/]+\}", NIL_UUID, path)
        resp = httpx.request(method.upper(), f"{url}{concrete}", json=body, headers=headers, timeout=30)
        if resp.status_code not in (httpx.codes.UNAUTHORIZED, httpx.codes.FORBIDDEN):
            return True
    return False


def _schemathesis(contract: Path, url: str, *extra: str) -> tuple[int, str]:
    result = subprocess.run(
        [sys.executable, "-m", "schemathesis.cli", "run", str(contract), "--url", url,
         "--no-color", "--max-examples", "15", *extra],
        capture_output=True, text=True, check=False,
    )
    return result.returncode, result.stdout + result.stderr


def _summary(output: str) -> str:
    lines = [ln.strip() for ln in output.splitlines()]
    picked = [ln for ln in lines if ln.startswith("Selected") or " generated" in ln]
    return " · ".join(picked[-2:]) or "(no summary)"


def surfaces(url: str, reports: Path, checks: str, bearer) -> int:
    """One run per surface. Returns the number of surfaces with findings.

    `bearer(surface)` returns that surface's token. A surface that turns its
    own token away counts as a finding without being fuzzed.
    """
    reports.mkdir(parents=True, exist_ok=True)
    with_findings = 0
    for surface in SURFACES:
        contract = ROOT / "contracts" / f"openapi-{surface}.yaml"
        tok = bearer(surface)
        if not let_in(url, contract, tok):
            print(f"✗ {surface:8} its own token is refused everywhere — nothing past "
                  f"the door would be tested")
            with_findings += 1
            continue
        code, out = _schemathesis(
            contract, url,
            "--header", f"Authorization: Bearer {tok}",
            "--exclude-by", PLANNED, "--checks", checks,
            "--report", "junit",
            "--report-junit-path", str(reports / f"schemathesis-{surface}.xml"),
        )
        marker = "✓" if code == 0 else "✗"
        print(f"{marker} {surface:8} {_summary(out)}")
        for line in out.splitlines():
            if line.startswith("_") and " /" in line:
                print(f"      {line.strip('_ ')}")
        with_findings += code != 0
    return with_findings


def counter_probe(url: str, bearer: str) -> bool:
    """True if a deliberately broken contract is caught and the real one is not.

    `bearer` is a serve token: the probe needs a 422, and without a token the
    answer is 401 before the body is ever looked at.
    """
    real = ROOT / "contracts" / "openapi-serve.yaml"
    spec = yaml.safe_load(real.read_text(encoding="utf-8"))
    spec["components"]["schemas"]["UnprocessableDetail"]["properties"]["detail"] = {"type": "string"}
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as broken:
        yaml.safe_dump(spec, broken)
    probe = ("--include-path", "/v1/route", "--checks", "response_schema_conformance",
             "--header", f"Authorization: Bearer {bearer}")
    real_code, real_out = _schemathesis(real, url, *probe)
    broken_code, _ = _schemathesis(Path(broken.name), url, *probe)
    print(f"  counter-probe: real contract exit {real_code}, broken contract exit {broken_code}")
    if real_code != 0:
        print("  the REAL contract already fails this check — the probe proves nothing:")
        print("\n".join("    " + ln for ln in real_out.splitlines()[-8:]))
        return False
    return broken_code != 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[1])
    ap.add_argument("--url", required=True)
    ap.add_argument("--token-url", required=True,
                    help="Token endpoint, e.g. scripts/dev_issuer.py's .../application/o/token/")
    ap.add_argument("--user", default="admin",
                    help="Account the tokens are issued for; admin reaches every operation.")
    ap.add_argument("--reports", default="reports", type=Path)
    ap.add_argument("--checks", default="not_a_server_error,response_schema_conformance")
    ap.add_argument("--strict", action="store_true",
                    help="fail when any surface has findings (default: report only)")
    args = ap.parse_args()

    # The dev issuer does not check passwords; against Authentik this is the
    # account's app password, from the environment and never the command line.
    password = os.environ.get("COHERENY_CI_APP_PASSWORD", "unchecked-by-dev-issuer")

    def bearer(surface: str) -> str:
        # Fetched per surface, right before its run: a token lives ten minutes.
        return token(args.token_url, surface, args.user, password)

    _wait_for(args.url)
    with_findings = surfaces(args.url, args.reports, args.checks, bearer)
    bites = counter_probe(args.url, bearer("serve"))

    if not bites:
        print("✗ the counter-probe did not bite: a broken contract would go unnoticed")
        return 1
    print("✓ counter-probe: a broken contract is reported")
    if with_findings:
        print(f"{with_findings} surface(s) with findings — "
              f"{'failing (--strict)' if args.strict else 'report only, see reports/'}")
        return 1 if args.strict else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
