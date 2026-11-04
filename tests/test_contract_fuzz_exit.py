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
from types import SimpleNamespace

import pytest
import yaml

from scripts import contract_fuzz


def _run(monkeypatch, *, findings: int, bites: bool, strict: bool) -> int:
    monkeypatch.setattr(contract_fuzz, "_wait_for", lambda _url: None)
    monkeypatch.setattr(contract_fuzz, "token", lambda *_a: "tok")
    monkeypatch.setattr(contract_fuzz, "surfaces", lambda *_a: findings)
    monkeypatch.setattr(contract_fuzz, "counter_probe", lambda *_a: bites)
    argv = ["contract_fuzz.py", "--url", "http://x", "--token-url", "http://issuer/token/"]
    argv += ["--strict"] if strict else []
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


# ── A token that is refused is not a clean run (CHY-192) ──────────────────────
#
# Until CHY-192 the target ran with DEV_MODE and no token was sent. Without
# that switch, a missing or rejected token means 401 everywhere — and 401 is not
# a server error, so the fuzzer would report a clean run having tested nothing.

def test_a_surface_that_refuses_its_own_token_is_a_finding(monkeypatch, tmp_path):
    fuzzed = []
    monkeypatch.setattr(contract_fuzz, "let_in", lambda *_a: False)
    monkeypatch.setattr(contract_fuzz, "_schemathesis", lambda *a: fuzzed.append(a) or (0, ""))
    findings = contract_fuzz.surfaces("http://x", tmp_path, "not_a_server_error", lambda _s: "tok")
    assert findings == len(contract_fuzz.SURFACES)
    assert fuzzed == [], "a surface that refuses its token must not be fuzzed and counted clean"


def test_an_accepted_token_is_passed_to_schemathesis(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(contract_fuzz, "let_in", lambda *_a: True)
    monkeypatch.setattr(contract_fuzz, "_schemathesis", lambda *a: calls.append(a) or (0, ""))
    assert contract_fuzz.surfaces("http://x", tmp_path, "c", lambda s: f"tok-{s}") == 0
    assert all("Authorization: Bearer tok-" + s in c for s, c in zip(contract_fuzz.SURFACES, calls))


def _contract(tmp_path, paths: dict):
    f = tmp_path / "openapi-x.yaml"
    f.write_text(yaml.safe_dump({"paths": paths}), encoding="utf-8")
    return f


def _answers(monkeypatch, status_by_path: dict):
    seen = []

    def request(method, url, **_kw):
        path = url.removeprefix("http://x")
        seen.append((method, path))
        return SimpleNamespace(status_code=status_by_path.get(path, 401))
    monkeypatch.setattr(contract_fuzz.httpx, "request", request)
    return seen


def test_let_in_is_false_when_every_answer_is_401(monkeypatch, tmp_path):
    _answers(monkeypatch, {})
    contract = _contract(tmp_path, {"/v1/a": {"get": {}}, "/v1/b": {"post": {}}})
    assert contract_fuzz.let_in("http://x", contract, "tok") is False


def test_let_in_is_true_on_the_first_answer_past_the_door(monkeypatch, tmp_path):
    """A 422 for an empty body is a caller who got in."""
    _answers(monkeypatch, {"/v1/b": 422})
    contract = _contract(tmp_path, {"/v1/a": {"get": {}}, "/v1/b": {"post": {}}})
    assert contract_fuzz.let_in("http://x", contract, "tok") is True


def test_let_in_skips_planned_operations(monkeypatch, tmp_path):
    seen = _answers(monkeypatch, {"/v1/planned": 200})
    contract = _contract(tmp_path, {"/v1/planned": {"get": {"x-cohereny-status": "planned"}}})
    assert contract_fuzz.let_in("http://x", contract, "tok") is False
    assert seen == []


def test_let_in_fills_path_parameters(monkeypatch, tmp_path):
    """The admin surface has one operation, and it takes a parameter. A 404
    for an actor that does not exist is a caller who got in."""
    path = f"/v1/admin/actors/{contract_fuzz.NIL_UUID}/anonymize"
    seen = _answers(monkeypatch, {path: 404})
    contract = _contract(tmp_path, {"/v1/admin/actors/{actor_ref}/anonymize": {"post": {}}})
    assert contract_fuzz.let_in("http://x", contract, "tok") is True
    assert seen == [("POST", path)]


def test_let_in_asks_reads_without_parameters_first(monkeypatch, tmp_path):
    seen = _answers(monkeypatch, {"/v1/a": 200})
    contract = _contract(tmp_path, {"/v1/b/{id}": {"get": {}}, "/v1/c": {"post": {}},
                                    "/v1/a": {"get": {}}})
    assert contract_fuzz.let_in("http://x", contract, "tok") is True
    assert seen == [("GET", "/v1/a")]
