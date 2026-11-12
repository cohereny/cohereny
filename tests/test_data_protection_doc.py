"""DATA-PROTECTION.md points at tests that exist (CHY-18).

The document names, for every measure, the test that fails when the measure is
removed. A renamed or deleted test would leave it pointing at nothing — and an
operator reading "shown failing by" would trust a test that no longer runs. So
every pytest node id in the document is resolved here, by syntax tree, not by
running the tests.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DOCUMENT = ROOT / "DATA-PROTECTION.md"

_NODE_ID = re.compile(r"`(tests/[\w/]+\.py(?:::\w+)+)`")


def _node_ids() -> list[str]:
    return sorted(set(_NODE_ID.findall(DOCUMENT.read_text(encoding="utf-8"))))


def _defines(path: Path, parts: list[str]) -> bool:
    """True if the module defines Class::...::test along the given path."""
    scope: list[ast.stmt] = ast.parse(path.read_text(encoding="utf-8")).body
    for name in parts:
        match = next((n for n in scope
                      if isinstance(n, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                      and n.name == name), None)
        if match is None:
            return False
        scope = match.body if isinstance(match, ast.ClassDef) else []
    return True


def test_the_document_names_tests():
    """Counter-probe: a pattern that matched nothing would let the next test pass empty."""
    assert len(_node_ids()) >= 20


@pytest.mark.parametrize("node_id", _node_ids())
def test_every_named_test_exists(node_id):
    path, *parts = node_id.split("::")
    assert (ROOT / path).is_file(), f"{path} does not exist"
    assert _defines(ROOT / path, parts), f"{node_id} is not defined"
