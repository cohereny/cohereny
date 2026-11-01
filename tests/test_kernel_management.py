"""The kernel's administrative surface after CHY-123.

`actors` and `audit_log` belong to the kernel (ADR-030), and three transport
modules were reading and writing them anyway: `api/v1/admin.py` held the
GDPR Art. 17 erasure, `api/v1/audit.py` assembled its own log query, and
`cli/main.py` bootstrapped the first admin with its own INSERT — plus its own
hand-written copy of the five role names.

The objection is the same one CHY-105 made about the approval lifecycle: a
legal obligation that only an HTTP client can discharge is an obligation with
one door. An erasure has to be triggerable from a script.
"""
from __future__ import annotations

import ast
import asyncio
import pathlib
import re
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from api.main import _KERNEL_STATUS
from kernel import identity
from kernel.errors import ActorAnonymized, KernelError, UnknownRole
from kernel.roles import ROLE_NAMES


class TestTheVocabularyIsNotCopied:
    """`cli/main.py` carried its own tuple of the five role names next to the
    INSERT. A second list is a second answer to "which roles exist", and the
    two only agree until somebody adds a role."""

    def test_an_unknown_role_is_refused_before_any_connection(self):
        """No DSN is needed to find out that "root" is not a role.

        The check runs before asyncpg.connect, which is why this test can pass
        a DSN that would fail to resolve.
        """
        with pytest.raises(UnknownRole) as e:
            asyncio.run(identity.set_role("postgresql://nowhere/x", "sub", "root"))
        assert e.value.role == "root"
        assert "root" in str(e.value)

    @pytest.mark.parametrize("role", sorted(ROLE_NAMES))
    def test_every_published_role_passes_the_check(self, role):
        """Reaching the connection attempt means the name was accepted."""
        with pytest.raises(Exception) as e:
            asyncio.run(identity.set_role("postgresql://nowhere:1/x", "sub", role))
        assert not isinstance(e.value, UnknownRole)

    def test_no_module_outside_the_kernel_lists_the_roles(self):
        """Against the AST — the CLI's --help text names them in prose, and
        that is a different thing from a second source of truth."""
        offenders = []
        for path in pathlib.Path().glob("[acgk]*/**/*.py"):
            if ".venv" in str(path) or path.parts[0] == "kernel":
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, (ast.Tuple, ast.List, ast.Set)):
                    continue
                names = {e.value for e in node.elts
                         if isinstance(e, ast.Constant) and isinstance(e.value, str)}
                if ROLE_NAMES <= names:
                    offenders.append(str(path))
        assert not offenders, (
            f"the role vocabulary is restated in {sorted(set(offenders))} — "
            f"kernel/roles.py publishes ROLE_NAMES"
        )


class TestAnErasedIdentityDoesNotComeBack:
    def test_the_conflict_clause_guards_on_is_anonymized(self):
        """The refusal is a property of the statement, not of a check around it.

        `ON CONFLICT ... DO UPDATE ... WHERE actors.is_anonymized = FALSE`
        matches nothing for an erased row, so RETURNING yields no row and the
        caller learns it from that. A check-then-act around a plain UPSERT
        would leave a window in which the two disagree.
        """
        src = pathlib.Path("kernel/identity.py").read_text(encoding="utf-8")
        fn = next(n for n in ast.walk(ast.parse(src))
                  if isinstance(n, ast.AsyncFunctionDef) and n.name == "set_role")

        # fn.body[1:] skips the docstring by *position* rather than by
        # comparing its value — the docstring explains the guard and therefore
        # contains "is_anonymized = FALSE", which a value comparison is one
        # editing accident away from letting through.
        sql = " ".join(
            n.value
            for stmt in fn.body[1:]
            for n in ast.walk(stmt)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
        )
        assert "ON CONFLICT" in sql
        assert re.search(r"is_anonymized\s*=\s*FALSE", sql), (
            "without the guard an erased subject would be reactivated by the "
            "administrative path (GDPR Art. 17, ADR-025)"
        )

    def test_the_error_says_what_to_do_instead(self):
        exc = ActorAnonymized("sub-1")
        assert "new subject" in str(exc)
        assert exc.idp_sub == "sub-1"


class TestTheCodesAreUnchanged:
    @pytest.mark.parametrize("exc,expected", [
        (UnknownRole("root"), 400),
        (ActorAnonymized("s"), 409),
    ])
    def test_the_new_errors_are_mapped(self, exc, expected):
        """Raised only on the CLI path today. Mapped anyway: the kernel is
        transport-agnostic by design, and the handler's default is 500."""
        assert _KERNEL_STATUS[type(exc)] == expected

    def test_every_kernel_error_is_still_mapped(self):
        missing = set(KernelError.__subclasses__()) - set(_KERNEL_STATUS)
        assert not missing, f"no status code for: {sorted(c.__name__ for c in missing)}"


class TestTheTablesHaveOneOwner:
    """Criterion 6 of ADR-030 for the two kernel-owned tables this ticket names."""

    @pytest.mark.parametrize("table", ["actors", "audit_log"])
    def test_only_the_kernel_writes_sql_against_them(self, table):
        pattern = re.compile(rf"\b(FROM|INTO|UPDATE|JOIN)\s+{table}\b")
        owners = set()
        for path in pathlib.Path().glob("[acgk]*/**/*.py"):
            if ".venv" in str(path) or "test" in path.name:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    if pattern.search(node.value):
                        owners.add(path.parts[0])
        # api/v1/scopes.py still checks actors exists — CHY-121 moves the whole
        # scope side, and splitting that file across two tickets would be worse
        # than naming the exception here.
        assert owners <= {"kernel", "api"}, f"{table} is touched from {sorted(owners)}"
        if table == "audit_log":
            assert owners == {"kernel"}, f"audit_log is touched from {sorted(owners)}"


class TestTheErasureNeedsNoRequest:
    def test_anonymize_is_callable_without_http(self):
        """The point of the move, in one signature."""
        import inspect

        params = list(inspect.signature(identity.anonymize_actor).parameters)
        assert params == ["pool", "caller", "actor_ref"]

    def test_the_endpoint_only_forwards(self):
        """api/v1/admin.py must not have grown its own copy of the rules.

        Walks the function *body*, not the whole node. The first version
        included the decorators and flagged the route path
        `"/actors/{actor_ref}/anonymize"` as SQL — a URL counted as a query,
        which is the same family of mistake as prose counted as code. The
        docstring is excluded for the same reason.
        """
        src = pathlib.Path("api/v1/admin.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.AsyncFunctionDef) and n.name == "anonymize_actor")
        doc = ast.get_docstring(fn, clean=False)
        literals = [
            n.value
            for stmt in fn.body
            for n in ast.walk(stmt)
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and n.value != doc
        ]
        offending = [s for s in literals
                     if re.search(r"\b(SELECT|INSERT|UPDATE|DELETE)\b", s)]
        assert not offending, f"the transport layer is holding SQL again: {offending}"
