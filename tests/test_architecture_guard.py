"""The boundary detector has to catch what it claims to catch (CHY-103).

CHY-102 in one sentence: no protection without a demonstrated failure case.
Every rule below is shown to fire on a violation *and* to stay quiet on the
legitimate near-miss that would make it useless if it were noisy.

The near-misses matter as much as the hits. A detector that flags a relative
import, or a context importing itself, or the word HTTPException in a comment,
gets switched off the first week -- and this file exists because the previous
layering detector did exactly that with a docstring.
"""
from __future__ import annotations

import ast
import inspect
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))

import architecture  # noqa: E402


def _rules(code: str, *, pkg: str = "core", rel: str = "core/thing.py",
           context: str | None = None) -> list[str]:
    """Rule names fired by a snippet, as if it were the given file."""
    tree = ast.parse(code)
    found = architecture._import_violations(pkg, rel, context, tree)
    found += architecture._http_violations(rel, tree)
    return sorted(v.rule for v in found)


class TestInwardOnly:
    """Zone 2 and below must not reach up into the transport layer (CHY-94)."""

    def test_importing_api_is_caught(self):
        assert _rules("from api.config import settings") == ["inward_only"]

    def test_plain_import_is_caught_too(self):
        assert _rules("import api.deps") == ["inward_only"]

    def test_a_relative_import_is_not_a_boundary_crossing(self):
        # `from .config import x` inside core/ cannot leave core/, whatever it
        # is called. Flagging it would make the rule untrustworthy.
        assert _rules("from .config import settings") == []

    def test_a_module_merely_named_like_the_layer_is_left_alone(self):
        assert _rules("import apiclient") == []
        assert _rules("from apischema import validate") == []


class TestFrameworkFree:
    """The rules must not know about HTTP."""

    def test_fastapi_below_transport_is_caught(self):
        assert _rules("from fastapi import Depends") == ["framework_free"]

    def test_starlette_too(self):
        assert _rules("import starlette.responses") == ["framework_free"]

    def test_pydantic_is_not_a_web_framework(self):
        # Schemas live in the contexts by design (ADR-030). Catching pydantic
        # here would forbid the structure the detector is meant to protect.
        assert _rules("from pydantic import BaseModel") == []


class TestHttpBelowTransport:
    """A status code is a transport concern; below it, raise a domain error."""

    def test_a_raise_is_caught(self):
        assert _rules("raise HTTPException(status_code=403)") == ["http_below_transport"]

    def test_an_aliased_module_still_shows_the_symbol(self):
        assert "http_below_transport" in _rules("raise fa.HTTPException(404)")

    def test_a_comment_is_not_a_violation(self):
        """The exact defect that inflated the figure to five.

        core/tracing.py:211 mentions HTTPException in a comment. grep counted
        it; the AST does not see it at all.
        """
        assert _rules("# HTTPException from the call would vanish here\nx = 1") == []

    def test_a_docstring_mentioning_it_is_not_a_violation(self):
        assert _rules('"""Maps HTTPException to a domain error."""\nx = 1') == []


class TestContextIsolation:
    """A context may use the kernel and no second context."""

    def test_a_foreign_context_is_caught(self):
        assert _rules(
            "from contexts.orchestration.service import start_run",
            pkg="contexts", rel="contexts/repository/service.py", context="repository",
        ) == ["context_isolation"]

    def test_its_own_context_is_fine(self):
        assert _rules(
            "from contexts.repository.store import BlockStore",
            pkg="contexts", rel="contexts/repository/service.py", context="repository",
        ) == []

    def test_the_kernel_is_always_allowed(self):
        assert _rules(
            "from kernel.roles import Role",
            pkg="contexts", rel="contexts/pilots/service.py", context="pilots",
        ) == []

    def test_a_file_outside_any_context_is_not_judged_by_this_rule(self):
        # core/ is not a context yet; importing contexts.* from there is part of
        # the migration, not a violation of context isolation.
        assert _rules("from contexts.repository.store import BlockStore") == []


class TestTheDetectorMatchesReality:
    """Guards against the detector drifting from the codebase it measures."""

    def test_counts_and_violations_agree(self):
        v = architecture.violations()
        c = architecture.counts()
        assert sum(c.values()) == len(v)

    def test_every_rule_is_reported_even_at_zero(self):
        """A metric that disappears when clean cannot be seen to regress."""
        assert set(architecture.counts()) == {
            "inward_only", "context_isolation", "framework_free", "http_below_transport",
            # new with CHY-101, ADR-030 zones 2 and 3
            "kernel_uses_nothing", "context_encapsulation",
        }

    def test_context_isolation_is_already_at_zero(self):
        """Contexts do not exist yet, so this rule is hard-gated from today.

        If this ever fails, the refactor introduced the first cross-context
        import -- which is precisely the moment to find out.
        """
        assert architecture.counts()["context_isolation"] == 0

    @pytest.mark.parametrize("rule", ["inward_only", "framework_free", "http_below_transport"])
    def test_violations_actually_runs_every_rule(self, rule, tmp_path, monkeypatch):
        """Not that the rule function exists — that `violations()` calls it.

        Written after deleting the `found += _http_violations(...)` line and
        watching all 22 tests stay green: the unit tests above reach the rule
        functions directly, so they cannot see the wiring. That is the same
        defect as a collector in quality_gate.py that never calls _require, and
        it was found the same way — by breaking the code on purpose.
        """
        offence = {
            "inward_only": "from api.config import settings",
            "framework_free": "from fastapi import Depends",
            "http_below_transport": "raise HTTPException(status_code=403)",
        }[rule]
        core = tmp_path / "core"
        core.mkdir()
        (core / "probe.py").write_text(offence, encoding="utf-8")
        monkeypatch.setattr(architecture, "ROOT", tmp_path)

        assert rule in {v.rule for v in architecture.violations()}
        assert architecture.counts()[rule] == 1

    def test_scanning_reaches_into_subdirectories(self, tmp_path, monkeypatch):
        """rglob, not glob — the defect that would have surfaced on move day.

        The previous detector used glob("*.py") and would have walked straight
        past contexts/repository/store.py, reporting zero for the very structure
        it was written to guard. Today's tree is flat below the transport layer,
        so the property cannot be observed on real files: it is checked against
        a tree shaped like the one ADR-030 creates.
        """
        ctx = tmp_path / "contexts" / "repository"
        ctx.mkdir(parents=True)
        (ctx / "store.py").write_text("from api.config import settings\n", encoding="utf-8")
        (tmp_path / "contexts" / "__init__.py").write_text("", encoding="utf-8")
        monkeypatch.setattr(architecture, "ROOT", tmp_path)

        found = architecture.violations()

        assert [v.rule for v in found] == ["inward_only"]
        assert found[0].path == "contexts/repository/store.py"
        assert found[0].line == 1


def _violations_for(relpath: str, source: str):
    """Write a file into the real tree, check, clean up.

    No tmp_path: the guard runs against ROOT, and copying the whole tree costs
    more than one file that is guaranteed to disappear again.
    """
    target = architecture.ROOT / relpath
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(source, encoding="utf-8")
    try:
        return [v for v in architecture.violations() if v.path == relpath]
    finally:
        target.unlink(missing_ok=True)


# ── The zone 2 and zone 3 rules from ADR-030 (CHY-101) ───────────────────────
#
# Both report 0 today. That can mean "clean" or "not wired up", and the
# difference is exactly what slipped through four times this week. So: create
# the violation, expect the report.

class TestTheKernelUsesNothing:
    def test_a_context_import_in_the_kernel_is_reported(self):
        v = _violations_for("kernel/x.py",
                            "from contexts.pilots import service\n")
        assert any(x.rule == "kernel_uses_nothing" for x in v), v

    def test_the_kernel_may_import_itself(self):
        v = _violations_for("kernel/x.py",
                            "from kernel.roles import Role\n")
        assert not v, v


class TestZone1DoesNotReachInside:
    @pytest.mark.parametrize("module", ["store", "run", "events"])
    def test_reaching_past_the_service_is_reported(self, module):
        v = _violations_for("api/v1/x.py",
                            f"from contexts.pilots.{module} import X\n")
        assert any(x.rule == "context_encapsulation" for x in v), v

    @pytest.mark.parametrize("module", ["service", "schema", "errors"])
    def test_the_published_interface_is_allowed(self, module):
        v = _violations_for("api/v1/x.py",
                            f"from contexts.pilots.{module} import X\n")
        assert not any(x.rule == "context_encapsulation" for x in v), v

    def test_api_is_read_at_all(self):
        """Until CHY-101 the guard never looked at api/.

        Without this assertion someone could delete the loop and every test
        above would stay green — they would simply report nothing, because
        nothing is being read.
        """
        src = inspect.getsource(architecture.violations)
        assert "TRANSPORT" in src, "violations() no longer reads api/"
