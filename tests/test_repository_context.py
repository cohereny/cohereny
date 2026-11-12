"""The repository context after CHY-105 — rules without a database.

`api/v1/blocks.py` carried the entire approval lifecycle in 621 lines, and that
lifecycle was reachable **only** over HTTP. "What is approved" is the central
fact of this product; neither the CLI, nor a background job, nor the runner of
ADR-028 could establish it.

Nothing below opens a connection, builds a client or speaks HTTP. The one test
that reads a file reads a *migration*, and that is the point of it: the
transition table now exists in two places on purpose — in Python so the code
can refuse early with an explanation, and in plpgsql so the database is the
wall. Two places for one rule is exactly the duplication that drifts, so it is
checked rather than trusted.
"""
from __future__ import annotations

import ast
import pathlib
import re
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from api.main import _REPOSITORY_STATUS  # noqa: E402
from contexts.repository import block as rules  # noqa: E402
from contexts.repository.block import (  # noqa: E402
    ALL_STATUSES,
    LISTED_BY_DEFAULT,
    TRANSITIONS,
    DeletePlan,
    PatchPlan,
)
from contexts.repository.errors import (  # noqa: E402
    BlockNotFound,
    BlockTypeImmutable,
    InvalidTransition,
    NothingPublished,
    RepositoryError,
    UnknownStatus,
    VersionConflict,
)

_MIGRATION = pathlib.Path("migrations/013_block_status_machine.sql")


def _edges_the_database_allows() -> set[tuple[str, str]]:
    """Every (from, to) pair the trigger in migration 013 permits.

    Parsed out of the plpgsql rather than restated here: a copy of the table
    written into the test would drift with the table it is supposed to guard,
    and would then agree with a wrong Python side just as happily.
    """
    body = _MIGRATION.read_text(encoding="utf-8")
    # Only the condition of the transition check, not the HINT prose below it
    # that spells the same rules out in English.
    guard = body[body.index("IF OLD.status IS DISTINCT FROM NEW.status"):]
    guard = guard[:guard.index("RAISE EXCEPTION")]

    edges: set[tuple[str, str]] = set()
    pattern = re.compile(
        r"OLD\.status\s*=\s*'(\w+)'\s*AND\s*NEW\.status\s*"
        r"(?:=\s*'(\w+)'|IN\s*\(([^)]*)\))"
    )
    for src, single, group in pattern.findall(guard):
        targets = [single] if single else re.findall(r"'(\w+)'", group)
        edges.update((src, t) for t in targets)
    return edges


class TestTheTransitionTableMatchesTheDatabase:
    """The rule lives in Python and in plpgsql. They have to agree."""

    def test_the_migration_is_parseable_at_all(self):
        """Guards the guard: a regex that silently matches nothing would make
        every assertion below vacuously true."""
        edges = _edges_the_database_allows()
        # Five OR-clauses, but the pending_review one names three targets.
        assert len(edges) == 7, f"expected seven edges in migration 013, parsed {edges}"

    @pytest.mark.parametrize("name", sorted(TRANSITIONS))
    def test_every_coded_transition_is_permitted_by_the_database(self, name):
        allowed = _edges_the_database_allows()
        move = TRANSITIONS[name]
        for origin in move.allowed_from:
            assert (origin, move.to) in allowed, (
                f"{name}: the code would attempt {origin} → {move.to}, which the "
                f"trigger in migration 013 rejects. The request would fail with a "
                f"constraint violation surfacing as a 500."
            )

    def test_retiring_is_permitted_too(self):
        """Not in TRANSITIONS — retire_block has its own use case because it
        touches the whole version line, not one row."""
        assert ("active", "deprecated") in _edges_the_database_allows()

    def test_the_one_edge_with_no_code_path_is_named(self):
        """deprecated → active. The database allows reactivating a retired
        block; nothing in the API offers it.

        Recorded rather than removed: the asymmetry is deliberate (a retirement
        should be reversible in the data even if no endpoint does it), and an
        undocumented gap between what the schema permits and what the code can
        do is how the next person concludes the schema is wrong.
        """
        coded = {(o, m.to) for m in TRANSITIONS.values() for o in m.allowed_from}
        coded.add(("active", "deprecated"))
        assert _edges_the_database_allows() - coded == {("deprecated", "active")}

    def test_the_status_list_matches_the_check_constraint(self):
        body = _MIGRATION.read_text(encoding="utf-8")
        line = next(ln for ln in body.splitlines() if "CHECK (status IN" in ln)
        assert set(re.findall(r"'(\w+)'", line)) == set(ALL_STATUSES)


class TestWhoMayDoWhat:
    """Writes need a role because a consumer could otherwise retire the active
    version of an org-wide prompt. A gate with an open side door is not a gate
    (CHY-33)."""

    @pytest.mark.parametrize("name,role", [
        ("submit", "contributor"), ("withdraw", "contributor"),
        ("approve", "reviewer"), ("reject", "reviewer"),
    ])
    def test_the_required_role(self, name, role):
        assert TRANSITIONS[name].role == role


class TestVersionCheck:
    """A reviewer approves a specific text.

    If the author patches the block between the reviewer's GET and their POST,
    the patch supersedes the row that was read — and the reviewer would
    otherwise approve something they never saw.
    """

    def test_the_current_version_passes(self):
        rules.check_version(current=3, expected=3)

    def test_a_stale_version_names_both_numbers(self):
        with pytest.raises(VersionConflict) as e:
            rules.check_version(current=4, expected=3)
        assert (e.value.sent, e.value.current) == (3, 4)
        assert "3" in str(e.value) and "4" in str(e.value)

    def test_a_version_from_the_future_is_refused_too(self):
        """Not only "too old". A client sending a version that does not exist
        yet is as wrong as one sending a retired number, and letting it through
        because the comparison was `<` would approve on a guess."""
        with pytest.raises(VersionConflict):
            rules.check_version(current=2, expected=9)


class TestTransitionCheck:
    @pytest.mark.parametrize("name,origin", [
        ("submit", "draft"), ("submit", "rejected"),
        ("withdraw", "pending_review"),
        ("approve", "pending_review"), ("reject", "pending_review"),
    ])
    def test_a_permitted_origin_passes(self, name, origin):
        rules.check_transition(current_status=origin, transition=TRANSITIONS[name])

    def test_a_block_cannot_skip_review(self):
        """draft → active is the transition the whole gate exists to prevent."""
        with pytest.raises(InvalidTransition) as e:
            rules.check_transition(current_status="draft", transition=TRANSITIONS["approve"])
        assert e.value.to_status == "active"
        assert "pending_review" in str(e.value)

    def test_a_retired_block_cannot_be_resubmitted(self):
        with pytest.raises(InvalidTransition):
            rules.check_transition(current_status="deprecated", transition=TRANSITIONS["submit"])


class TestPatchPlan:
    def test_a_draft_is_edited_in_place(self):
        """It was never published, so there is no history to protect."""
        assert rules.plan_patch("draft") is PatchPlan.EDIT

    @pytest.mark.parametrize("st", ["pending_review", "active", "rejected", "deprecated"])
    def test_everything_else_is_superseded(self, st):
        assert rules.plan_patch(st) is PatchPlan.SUPERSEDE

    def test_the_block_type_cannot_change(self):
        with pytest.raises(BlockTypeImmutable):
            rules.check_block_type(current="system_prompt", requested="user_context")

    def test_resending_the_same_type_is_not_a_change(self):
        rules.check_block_type(current="system_prompt", requested="system_prompt")
        rules.check_block_type(current="system_prompt", requested=None)

    def test_unsent_fields_are_carried_over_not_cleared(self):
        """A new version that silently dropped the tags of the old one would be
        data loss dressed as an edit."""
        current = {"content": "old", "metadata": {"k": 1}, "tags": ["a", "b"]}
        merged = rules.merge_patch(current, content="new", metadata=None, tags=None)
        assert merged == {"content": "new", "metadata": {"k": 1}, "tags": ["a", "b"]}

    def test_an_explicitly_empty_value_does_clear(self):
        """`tags: []` is a request, `tags` absent is silence. Treating both as
        silence would make it impossible to remove the last tag."""
        current = {"content": "old", "metadata": {"k": 1}, "tags": ["a"]}
        assert rules.merge_patch(current, content=None, metadata={}, tags=[]) == {
            "content": "old", "metadata": {}, "tags": [],
        }


class TestDeletePlan:
    def test_the_whole_line_is_retired_not_only_the_current_row(self):
        """Several versions can be active at once, by design: superseding
        leaves the predecessor active so it keeps being composed while its
        successor is in review.

        Retiring only the current version made the *previous* one win the
        "newest active" query — a retired block reappeared with its old
        content. Found by the end-to-end run on 2026-08-13, not by a unit test.
        """
        plan, retired = rules.plan_deletion([
            {"version": 3, "status": "draft"},
            {"version": 2, "status": "active"},
            {"version": 1, "status": "active"},
        ])
        assert plan is DeletePlan.RETIRE
        assert retired == [2, 1], "both composable versions, not just the newest"

    def test_repeating_a_delete_writes_nothing(self):
        """A second DELETE is not a second decision. Each repeat would
        otherwise append another unremovable audit entry."""
        plan, retired = rules.plan_deletion([{"version": 1, "status": "deprecated"}])
        assert plan is DeletePlan.ALREADY_RETIRED
        assert retired == []

    @pytest.mark.parametrize("st", ["draft", "pending_review", "rejected"])
    def test_a_block_that_never_went_live_cannot_be_retired(self, st):
        with pytest.raises(NothingPublished) as e:
            rules.plan_deletion([{"version": 1, "status": st}])
        assert "withdraw" in str(e.value)

    def test_a_deprecated_current_row_over_an_active_one_still_retires(self):
        """The predecessor is what keeps being composed, so there is still
        something to take out of service."""
        plan, retired = rules.plan_deletion([
            {"version": 2, "status": "deprecated"},
            {"version": 1, "status": "active"},
        ])
        assert (plan, retired) == (DeletePlan.RETIRE, [1])


class TestCacheInvalidation:
    """Only a move into or out of `active` alters what a scope composes."""

    @pytest.mark.parametrize("a,b", [
        ("pending_review", "active"), ("active", "deprecated"),
        ("deprecated", "active"),
    ])
    def test_crossing_active_invalidates(self, a, b):
        assert rules.affects_composition(a, b)

    @pytest.mark.parametrize("a,b", [
        ("draft", "pending_review"), ("pending_review", "rejected"),
        ("rejected", "pending_review"), ("pending_review", "draft"),
    ])
    def test_everything_else_leaves_the_cache_alone(self, a, b):
        assert not rules.affects_composition(a, b), (
            "bumping a generation counter for a change nobody can observe "
            "throws away every compiled context of the scope for nothing"
        )


class TestStatusFilter:
    def test_a_known_status_passes_through(self):
        assert rules.check_status_filter("active") == "active"

    def test_a_wrong_case_is_refused_rather_than_silently_empty(self):
        """`?status=Active` used to return zero rows without saying why."""
        with pytest.raises(UnknownStatus) as e:
            rules.check_status_filter("Active")
        assert "Active" in str(e.value)

    def test_the_default_list_is_positive_not_a_blacklist(self):
        """A blacklist would have started returning pending_review and rejected
        blocks the moment migration 013 landed, without any client asking."""
        assert set(LISTED_BY_DEFAULT) == {"draft", "pending_review", "active"}
        assert "deprecated" not in LISTED_BY_DEFAULT
        assert "rejected" not in LISTED_BY_DEFAULT


class TestTheCodesAreUnchanged:
    @pytest.mark.parametrize("exc,expected", [
        (BlockNotFound(), 404),
        (VersionConflict(1, 2), 409),
        (InvalidTransition("draft", "active", ("pending_review",)), 409),
        (BlockTypeImmutable(), 409),
        (NothingPublished("draft"), 409),
        (UnknownStatus("x", ALL_STATUSES), 422),
    ])
    def test_mapping(self, exc, expected):
        assert _REPOSITORY_STATUS[type(exc)] == expected

    def test_every_context_error_is_mapped(self):
        """An unregistered type would silently become 500. The handler does
        that on purpose, but it must not happen unnoticed."""
        missing = set(RepositoryError.__subclasses__()) - set(_REPOSITORY_STATUS)
        assert not missing, f"no status code for: {sorted(c.__name__ for c in missing)}"


class TestTheContextKnowsNoHTTP:
    @pytest.mark.parametrize("module", [
        "block", "service", "store", "compose", "errors", "schema",
    ])
    def test_no_import_of_fastapi_or_api(self, module):
        """Against the AST, not a grep — the docstrings tell the story of the
        move and therefore name api/ and HTTPException."""
        tree = ast.parse(
            pathlib.Path(f"contexts/repository/{module}.py").read_text(encoding="utf-8")
        )
        imported: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported += [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)
        offending = [m for m in imported
                     if m.split(".")[0] in {"fastapi", "api", "starlette"}]
        assert not offending, f"contexts/repository/{module}.py imports {offending}"

    def test_the_approval_lifecycle_is_callable_without_a_request(self):
        """The one-sentence summary of what CHY-105 bought.

        Before the move, approving a block required an HTTP client — which is
        why ADR-028's runner could not have used it. The signature is the
        evidence: no Request, no Depends, no CurrentUser.
        """
        import inspect

        from contexts.repository import service

        params = list(inspect.signature(service.transition).parameters)
        assert params[:5] == ["pool", "redis", "caller", "block_id", "name"]

    def test_the_sql_lives_in_one_module(self):
        """Criterion 6 of ADR-030 for this aggregate: one store, not four."""
        owners = set()
        for path in pathlib.Path().glob("[acgk]*/**/*.py"):
            if ".venv" in str(path) or "test" in path.name:
                continue
            text = path.read_text(encoding="utf-8")
            tree = ast.parse(text)
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    if re.search(r"\b(FROM|INTO|UPDATE|JOIN) context_blocks\b", node.value):
                        owners.add(str(path))
        assert owners == {"contexts/repository/store.py"}, (
            f"context_blocks is written to or read from {sorted(owners)}"
        )


class TestTheCoverageMetricMeasuresEverything:
    """A metric that stops measuring reports success for the wrong reason.

    `[tool.coverage.run] source` listed api, core, domain, cli, router and
    pilots. `kernel` and `contexts` were never added, so from CHY-101 onwards
    every extraction took its statements *out of the denominator* — and a
    refactor that moved code somewhere unmeasured therefore **raised** the
    reported figure. CHY-105 was the run where the effect finally went the
    other way and the ratchet caught it, three cuts late.

    Same shape as CHY-111, one zone further out: there it was modules nobody
    imported looking like nothing rather than like 0 %.
    """

    def test_every_python_package_is_a_coverage_source(self):
        import tomllib

        config = tomllib.loads(pathlib.Path("pyproject.toml").read_text(encoding="utf-8"))
        sources = {s.split("/")[0] for s in config["tool"]["coverage"]["run"]["source"]}

        # Top-level directories that hold importable application code. A
        # directory counts if it has an __init__.py; scripts/, migrations/ and
        # tests/ are deliberately outside the figure.
        packages = {
            p.name for p in pathlib.Path().iterdir()
            if p.is_dir()
            and (p / "__init__.py").exists()
            and p.name not in {"tests", "migrations", "scripts"}
            and not p.name.startswith(".")
        }
        assert packages <= sources, (
            f"not measured: {sorted(packages - sources)}. Coverage would drop "
            f"these statements from the denominator, so moving code into them "
            f"would improve the number by measuring less."
        )
