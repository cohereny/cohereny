"""
tests/integration/test_contract_fuzz_hooks.py

What scripts/schemathesis_hooks.py changes for the contract fuzzer (CHY-274).

Schemathesis has no serializer for `application/pdf`. The document upload takes
PDF or plain text, so the PDF branch was never sent, and cases built to break
the operation were thrown away until Hypothesis gave up with `filter_too_much`:
the strict `integration` job failed three times with nothing wrong in the API.

Shown in both directions on a copy of the real author contract whose upload
offers PDF only: without the hook Schemathesis cannot build a single case, with
it every case carries a PDF body. No seed decides the outcome: without a
serializer there is no case at all, whatever Hypothesis draws.

Each direction runs in a process of its own. The hook registers the serializer
for the whole process, so a test that loaded it once could not show the
direction without it afterwards — and a separate process picks the hook up the
way the fuzzer's runs do, through SCHEMATHESIS_HOOKS.

Marked `integration` although it needs no database: Schemathesis is in the
`contract` group of pyproject.toml, which only the integration job installs.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]
AUTHOR = ROOT / "contracts" / "openapi-author.yaml"
HOOKS = ROOT / "scripts" / "schemathesis_hooks.py"
UPLOAD = "/v1/workflows/runs/{run_id}/documents/{name}"

# Builds a few cases for the upload and prints their media types, or the name
# of the exception if none can be built.
_GENERATE = """
import sys
import schemathesis
from hypothesis import given, seed, settings

operation = schemathesis.openapi.from_path(sys.argv[1])[sys.argv[2]]["PUT"]
media = set()

@seed(0)
@settings(max_examples=5, database=None, deadline=None)
@given(operation.as_strategy())
def build(case):
    media.add(case.media_type)

try:
    build()
except Exception as exc:
    print(type(exc).__name__)
else:
    print(" ".join(sorted(media)))
"""


def _pdf_only(tmp_path: Path) -> Path:
    spec = yaml.safe_load(AUTHOR.read_text(encoding="utf-8"))
    content = spec["paths"][UPLOAD]["put"]["requestBody"]["content"]
    assert "application/pdf" in content, (
        "the upload no longer takes PDF — the hook may have nothing left to do")
    spec["paths"][UPLOAD]["put"]["requestBody"]["content"] = {
        "application/pdf": content["application/pdf"]}
    path = tmp_path / "openapi-author-pdf-only.yaml"
    path.write_text(yaml.safe_dump(spec), encoding="utf-8")
    return path


def _generate(contract: Path, tmp_path: Path, *, hooks: bool) -> str:
    env = {k: v for k, v in os.environ.items() if k != "SCHEMATHESIS_HOOKS"}
    if hooks:
        env["SCHEMATHESIS_HOOKS"] = str(HOOKS)
    result = subprocess.run(
        [sys.executable, "-c", _GENERATE, str(contract), UPLOAD],
        capture_output=True, text=True, env=env, cwd=tmp_path, timeout=120, check=False,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    return result.stdout.strip()


def test_without_the_hook_no_pdf_case_can_be_built(tmp_path):
    """The failure the hook exists for: no serializer, no case."""
    assert _generate(_pdf_only(tmp_path), tmp_path, hooks=False) == "SerializationNotPossible"


def test_with_the_hook_pdf_cases_are_built(tmp_path):
    assert _generate(_pdf_only(tmp_path), tmp_path, hooks=True) == "application/pdf"
