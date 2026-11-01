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

The counter-probe is always strict. A fuzzer that reports nothing is only
worth something if it would have reported a broken contract, so a copy of the
serve contract is broken on purpose — `HTTPValidationError.detail` becomes a
string — and the run against it must fail while the run against the real
contract passes. Both directions, or the probe could pass for the wrong reason.

Planned endpoints (`x-cohereny-status: planned`) are excluded: they are meant
to answer 501, and that is not a finding.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
import yaml

ROOT = Path(__file__).resolve().parent.parent
SURFACES = ("serve", "author", "observe", "admin")
PLANNED = '/x-cohereny-status == "planned"'


def _wait_for(url: str, seconds: int = 30) -> None:
    for _ in range(seconds):
        try:
            httpx.get(f"{url}/v1/health", timeout=2).raise_for_status()
        except httpx.HTTPError:
            time.sleep(1)
        else:
            return
    sys.exit(f"{url} did not answer /v1/health within {seconds}s")


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


def surfaces(url: str, reports: Path, checks: str) -> int:
    """One run per surface. Returns the number of surfaces with findings."""
    reports.mkdir(parents=True, exist_ok=True)
    with_findings = 0
    for surface in SURFACES:
        code, out = _schemathesis(
            ROOT / "contracts" / f"openapi-{surface}.yaml", url,
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


def counter_probe(url: str) -> bool:
    """True if a deliberately broken contract is caught and the real one is not."""
    real = ROOT / "contracts" / "openapi-serve.yaml"
    spec = yaml.safe_load(real.read_text(encoding="utf-8"))
    spec["components"]["schemas"]["HTTPValidationError"]["properties"]["detail"] = {"type": "string"}
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as broken:
        yaml.safe_dump(spec, broken)
    probe = ("--include-path", "/v1/route", "--checks", "response_schema_conformance")
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
    ap.add_argument("--reports", default="reports", type=Path)
    ap.add_argument("--checks", default="not_a_server_error")
    ap.add_argument("--strict", action="store_true",
                    help="fail when any surface has findings (default: report only)")
    args = ap.parse_args()

    _wait_for(args.url)
    with_findings = surfaces(args.url, args.reports, args.checks)
    bites = counter_probe(args.url)

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
