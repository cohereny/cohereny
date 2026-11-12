"""The kernel speaks no HTTP — and the status codes are the same regardless.

CHY-101 removed three `HTTPException` raises from `core/authz.py` and replaced
them with domain errors. That is only an improvement if two things hold: the
rules become usable without FastAPI, **and** nothing changes for a caller
outside.

The second is the one people forget. A refactoring that quietly turns a 409
into a 500 breaks clients without a single test noticing, so this file checks
both directions.
"""
from __future__ import annotations

import ast
import importlib
import inspect
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import uuid

from api.deps import CurrentUser
from api.main import _KERNEL_STATUS
from kernel.access import ResolvedActor
from kernel.errors import AccessDenied, ActorGone, ActorStateInconsistent, KernelError
from kernel.identity import Caller


class TestTheCodesAreUnchanged:
    """The numbers from before the move, written down."""

    @pytest.mark.parametrize("exc,expected", [
        (AccessDenied("admin", "consumer"), 403),
        (ActorGone(), 403),
        (ActorStateInconsistent(), 409),
    ])
    def test_mapping(self, exc, expected):
        assert _KERNEL_STATUS[type(exc)] == expected

    def test_every_kernel_error_is_mapped(self):
        """An unregistered error type would silently become 500.

        The handler does that on purpose — the pessimistic default is right.
        But it must not happen unnoticed, so this counts the family.
        """
        known = set(_KERNEL_STATUS)
        everything = set(KernelError.__subclasses__())
        assert everything <= known, (
            f"no status code for: {sorted(c.__name__ for c in everything - known)}"
        )


class TestTheKernelKnowsNoHTTP:
    """The actual gain: the rules run without a transport layer."""

    @pytest.mark.parametrize("module", [
        "kernel.access", "kernel.audit", "kernel.roles",
        "kernel.identity", "kernel.errors", "kernel.surfaces",
    ])
    def test_no_import_of_fastapi_or_api(self, module):
        """Against the AST, not a grep.

        The docstrings tell the story of the move and therefore mention api/
        and HTTPException by name. Four times this week a grep counted prose as
        code; this reads import statements only.
        """
        tree = ast.parse(inspect.getsource(importlib.import_module(module)))
        offenders = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                if node.module.split(".")[0] in ("api", "fastapi", "starlette"):
                    offenders.append(f"line {node.lineno}: from {node.module}")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] in ("api", "fastapi", "starlette"):
                        offenders.append(f"line {node.lineno}: import {alias.name}")
        assert not offenders, f"{module} reaches upward: {offenders}"

    def test_a_role_check_runs_without_importing_fastapi(self):
        """The practical proof, not just the absence of an import."""
        actor = ResolvedActor(uuid.uuid4(), "reviewer")
        assert actor.has_at_least("contributor")
        assert not actor.has_at_least("admin")


class TestTheCallerProtocol:
    def test_currentuser_satisfies_it_without_knowing(self):
        """Structural typing is why the inversion costs nothing.

        api/deps.py was not touched for this move.
        """
        assert isinstance(CurrentUser(sub="s", username="u", groups=None), Caller)

    def test_any_object_with_the_three_fields_will_do(self):
        """A CLI or a background job needs no HTTP type to ask about a role."""

        class CommandLine:
            sub = "cli"
            username = "batch"
            groups = ["chy-admins"]

        assert isinstance(CommandLine(), Caller)
