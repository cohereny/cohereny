"""Permission checks run without a database (ADR-030, CHY-120).

That sentence is the entire reason kernel/access.py exists as a separate module.
Before CHY-120 the same rule lived next to ten SQL statements and an asyncpg
pool, so exercising "may a reviewer approve?" meant standing up the whole stack.
arc42 §5.7 had said since v2.9 that SQL-freedom is *the condition*, not a nicety;
CHY-101 moved the file without honouring it.

Every test here constructs a ResolvedActor by hand. No pool, no fixtures, no
await. If any of that creeps back in, the split has been undone.
"""
from __future__ import annotations

import ast
import inspect
import pathlib
import sys
from uuid import uuid4

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from kernel.access import ResolvedActor, require  # noqa: E402
from kernel.errors import AccessDenied  # noqa: E402
from kernel.roles import ROLE_ORDER  # noqa: E402


def actor(role: str) -> ResolvedActor:
    return ResolvedActor(uuid4(), role)


class TestTheRuleItself:
    @pytest.mark.parametrize("held,minimum", [
        ("admin", "consumer"), ("admin", "admin"),
        ("reviewer", "contributor"), ("contributor", "contributor"),
    ])
    def test_enough_is_enough(self, held, minimum):
        assert require(actor(held), minimum).role == held

    @pytest.mark.parametrize("held,minimum", [
        ("consumer", "contributor"), ("contributor", "reviewer"),
        ("reviewer", "admin"), ("architect", "admin"),
    ])
    def test_too_junior_is_refused(self, held, minimum):
        with pytest.raises(AccessDenied) as exc:
            require(actor(held), minimum)
        assert exc.value.required == minimum
        assert exc.value.actual == held

    def test_an_unknown_minimum_is_a_programming_error_not_a_refusal(self):
        """ValueError, not AccessDenied.

        A typo in a role name must not read as "this caller may not". It is a
        mistake in the calling code and should stop the request loudly.
        """
        with pytest.raises(ValueError, match="unknown role"):
            require(actor("admin"), "supervisor")

    def test_an_unknown_held_role_never_satisfies_anything(self):
        """A role the vocabulary does not know ranks below everything.

        Reachable if the actors table ever carried a value the CHECK does not
        cover — then the safe reading is "no permission", not "unranked, so let
        it pass".
        """
        for minimum in ROLE_ORDER:
            with pytest.raises(AccessDenied):
                require(actor("wizard"), minimum)

    def test_the_actor_comes_back_so_a_call_site_can_be_one_expression(self):
        a = actor("admin")
        assert require(a, "reviewer") is a


class TestTheSplitHolds:
    """The structural half. Without this, the tests above prove only that the
    rule works *today* — not that it stays reachable without a database."""

    @staticmethod
    def _imports(module: str) -> list[str]:
        tree = ast.parse(inspect.getsource(__import__(module, fromlist=["_"])))
        out: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                out.append(node.module)
            elif isinstance(node, ast.Import):
                out += [a.name for a in node.names]
        return out

    def test_access_imports_no_database_and_no_identity(self):
        """Against the AST, not a grep.

        The docstring of access.py names kernel/identity.py twice while
        explaining the split — a grep counts those and reports a violation that
        is not there. Four times this week prose was counted as code.
        """
        forbidden = {"asyncpg", "api", "fastapi", "kernel.identity"}
        found = [m for m in self._imports("kernel.access")
                 if m in forbidden or m.split(".")[0] in forbidden]
        assert not found, f"kernel/access.py reaches for {found}"

    def test_access_contains_no_sql(self):
        src = pathlib.Path("kernel/access.py").read_text(encoding="utf-8")
        # Only string literals can hold SQL; comments explaining the absence
        # of SQL must not count. Same reason as above.
        tree = ast.parse(src)
        literals = [n.value for n in ast.walk(tree)
                    if isinstance(n, ast.Constant) and isinstance(n.value, str)]
        sql = [s for s in literals
               if any(k in s.upper() for k in ("SELECT ", "INSERT ", "UPDATE ", "DELETE "))]
        assert not sql, f"SQL in kernel/access.py: {sql}"

    def test_the_dependency_points_one_way(self):
        """identity → access, never back. Otherwise it is a cycle in disguise."""
        assert "kernel.access" in self._imports("kernel.identity")

    def test_no_test_in_this_file_needs_a_pool(self):
        """The claim of the module docstring, checked against this file.

        If someone adds a pool fixture here to make one more case convenient,
        the module has quietly become untestable-without-a-database again.
        """
        tree = ast.parse(pathlib.Path(__file__).read_text(encoding="utf-8"))

        awaiting = [n.name for n in ast.walk(tree)
                    if isinstance(n, ast.AsyncFunctionDef) and n.name.startswith("test_")]
        assert not awaiting, f"async tests need a stack: {awaiting}"

        # Names actually called, not the word appearing anywhere. The first
        # version of this assertion searched the raw source and tripped over
        # its own failure message — the very mistake the test above warns about.
        called = {n.func.id for n in ast.walk(tree)
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        assert "make_pool" not in called, "a pool crept into the pure-rule tests"
