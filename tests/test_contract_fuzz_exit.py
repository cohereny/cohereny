"""
tests/test_contract_fuzz_exit.py

What `scripts/contract_fuzz.py` answers the CI with (CHY-124).

The CI job runs it with `--strict` since 25.09.2026. That switch is only worth
something if a finding really turns into a failing exit code — and if the
counter-probe still fails the run on its own. Schemathesis itself is replaced:
its findings are random, and a test that depended on one appearing would pass
or fail by chance. What is under test is the decision, not the fuzzing.
"""
from __future__ import annotations

import sys

import pytest

from scripts import contract_fuzz


def _run(monkeypatch, *, findings: int, bites: bool, strict: bool) -> int:
    monkeypatch.setattr(contract_fuzz, "_wait_for", lambda _url: None)
    monkeypatch.setattr(contract_fuzz, "surfaces", lambda *_a: findings)
    monkeypatch.setattr(contract_fuzz, "counter_probe", lambda _url: bites)
    argv = ["contract_fuzz.py", "--url", "http://x"] + (["--strict"] if strict else [])
    monkeypatch.setattr(sys, "argv", argv)
    return contract_fuzz.main()


def test_a_finding_fails_the_run_when_strict(monkeypatch):
    assert _run(monkeypatch, findings=1, bites=True, strict=True) == 1


def test_a_finding_is_only_reported_without_strict(monkeypatch):
    assert _run(monkeypatch, findings=1, bites=True, strict=False) == 0


@pytest.mark.parametrize("strict", [True, False])
def test_no_finding_passes(monkeypatch, strict):
    assert _run(monkeypatch, findings=0, bites=True, strict=strict) == 0


@pytest.mark.parametrize("strict", [True, False])
def test_a_counter_probe_that_does_not_bite_always_fails(monkeypatch, strict):
    """Without it a clean report could mean a fuzzer that sees nothing."""
    assert _run(monkeypatch, findings=0, bites=False, strict=strict) == 1
