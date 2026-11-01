"""The scope tree after CHY-121 — and the end of SQL in zone 1.

`scope_nodes` and `scope_memberships` belong to the kernel: a scope is not
content, it is *where* content lives, and every context refers to one without
owning it. Two zones read the table directly until now — `api/v1/scopes.py`
with eight statements, and `contexts/repository/` for the ancestor chain a
composition walks.

The second was the more telling. A context that knows the ltree schema of a
table it does not own has a dependency nobody declared, and it was declared
nowhere: the boundary guard passes SQL, and the ratchet counted `api/` only.
"""
from __future__ import annotations

import ast
import pathlib
import re
import json
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from api.main import _KERNEL_STATUS
from kernel.errors import (
    KernelError,
    MembershipExists,
    ParentScopeNotFound,
    ScopeNotFound,
)
from kernel.schema import scope_label

_TABLES = ("scope_nodes", "scope_memberships")


def _code_strings(tree: ast.AST) -> list[str]:
    """Every string literal that is not a docstring.

    Docstrings are excluded by *position* — the first statement of a module,
    class or function — and that exclusion is the whole point. Every module
    touched by CHY-121 explains in its docstring which table it stopped
    querying, so a check that reads docstrings finds the sentence saying the
    SQL is gone and reports it as SQL. The first version of this file did
    exactly that.
    """
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            first = node.body[0] if node.body else None
            if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)):
                docstrings.add(id(first.value))
    return [n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and id(n) not in docstrings]


def _sql_owners(table: str) -> set[str]:
    """Which files hold SQL against this table."""
    pattern = re.compile(rf"\b(FROM|INTO|UPDATE|JOIN)\s+{table}\b")
    owners = set()
    for path in pathlib.Path().glob("[acgkr]*/**/*.py"):
        if ".venv" in str(path) or "test" in path.name:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if any(pattern.search(v) for v in _code_strings(tree)):
            owners.add(str(path))
    return owners


class TestTheTreeHasOneOwner:
    @pytest.mark.parametrize("table", _TABLES)
    def test_only_the_kernel_holds_sql_against_it(self, table):
        owners = _sql_owners(table)
        assert owners <= {"kernel/scopes.py"}, (
            f"{table} is queried from {sorted(owners)} — it belongs to the kernel"
        )

    def test_the_composition_no_longer_joins_it(self):
        """The join was the reason a context knew an ltree schema.

        `scope_path` and `depth` come from the ancestor chain the caller has
        already loaded, so the join bought a second table in the query for two
        values that were already in memory.
        """
        src = pathlib.Path("contexts/repository/store.py").read_text(encoding="utf-8")
        offending = [v for v in _code_strings(ast.parse(src)) if "scope_nodes" in v]
        assert not offending, (
            f"the repository context is querying a kernel-owned table again: {offending}"
        )


class TestZoneOneHoldsNoSql:
    """Criterion 3 of ADR-030: SQL only in `contexts/*/store.py` and `kernel/`.

    This is the assertion the whole of M3b was working towards, and it is worth
    stating as a test rather than as a number in a document: `api_db_calls`
    went 43 → 0 over five cuts, and a ratchet that reaches zero should become a
    wall.
    """

    _KEYWORDS = re.compile(r"\b(SELECT\s+\w|INSERT\s+INTO|UPDATE\s+\w+\s+SET|DELETE\s+FROM)\b")

    @pytest.mark.parametrize("zone", ["api", "cli", "router", "core"])
    def test_no_sql_in_the_transport_zone(self, zone):
        offenders = []
        root = pathlib.Path(zone)
        if not root.exists():
            pytest.skip(f"{zone}/ does not exist")
        for path in root.rglob("*.py"):
            for value in _code_strings(ast.parse(path.read_text(encoding="utf-8"))):
                if self._KEYWORDS.search(value):
                    offenders.append(f"{path}: {value[:60]}")
        assert not offenders, "SQL above the context layer:\n" + "\n".join(offenders)

    def test_no_sql_in_a_context_outside_its_store(self):
        offenders = []
        for path in pathlib.Path("contexts").rglob("*.py"):
            if path.name == "store.py":
                continue
            for value in _code_strings(ast.parse(path.read_text(encoding="utf-8"))):
                if self._KEYWORDS.search(value):
                    offenders.append(f"{path}: {value[:60]}")
        assert not offenders, "a context holds SQL outside its store:\n" + "\n".join(offenders)

    def test_the_ratchet_agrees(self):
        """The number and the wall must not disagree.

        If `api_db_calls` is anything but 0 while the test above passes, one of
        the two is measuring the wrong thing — and a metric that disagrees with
        a test is the shape the coverage source list had all month.
        """
        recorded = json.loads(
            pathlib.Path("quality-baseline.json").read_text(encoding="utf-8")
        )["api_db_calls"]
        assert recorded == 0, (
            f"the ratchet records {recorded} database calls in api/ while the "
            f"test above finds none — one of them is wrong"
        )


class TestThePathIsDerived:
    """Two spellings of one scope path address different rows, and the failure
    is invisible: the lookup simply returns nothing."""

    def test_the_label_mirrors_the_sql_function(self):
        assert scope_label("org", "a1b2-c3") == "org_a1b2_c3"

    def test_no_caller_may_supply_a_path(self):
        """`ScopeCreate` has no scope_path field — the path is built from the
        parent's, and accepting one would let a caller graft a node anywhere."""
        from kernel.schema import ScopeCreate

        assert "scope_path" not in ScopeCreate.model_fields


class TestTheCodesAreUnchanged:
    @pytest.mark.parametrize("exc,expected", [
        (ScopeNotFound("s"), 404),
        (ParentScopeNotFound("p"), 404),
        (MembershipExists("a"), 409),
    ])
    def test_mapping(self, exc, expected):
        assert _KERNEL_STATUS[type(exc)] == expected

    def test_there_is_only_one_scope_not_found(self):
        """It lived in contexts/repository/errors.py too, because composition
        was the first thing that noticed a missing scope. Two error types for
        one condition is two answers to "what does 404 mean here"."""
        from contexts.repository import errors as repo_errors

        assert not hasattr(repo_errors, "ScopeNotFound")

    def test_every_kernel_error_is_still_mapped(self):
        missing = set(KernelError.__subclasses__()) - set(_KERNEL_STATUS)
        assert not missing, f"no status code for: {sorted(c.__name__ for c in missing)}"
