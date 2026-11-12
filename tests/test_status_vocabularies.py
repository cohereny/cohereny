"""
tests/test_status_vocabularies.py

Every vocabulary exists twice: as a CHECK in SQL and as a type in Python
(CHY-95, Audit-Befund B2).

At the time this was written all fourteen pairs agreed — and nothing held them
that way. That is the finding, and it is worse than a disagreement would have
been: a disagreement is visible the first time it matters, whereas agreement by
coincidence is invisible until the day somebody widens a CHECK and the API
keeps rejecting the new value with a 422 that names nothing.

SQL cannot import Python, so the two lists cannot be derived from one another.
A test is the only place they can be held together, which makes this file the
single source of truth for the *fact that they are the same*, not for the
values themselves.

Why the mapping is written out by hand
--------------------------------------
Matching by value set alone would look tidy and be wrong: `actors.role` and
`scope_memberships.role` carry identical values, and so would any two status
columns that happen to share a vocabulary. Worse, a pair that drifts apart
stops matching and would silently drop out of the comparison — the check would
quietly stop checking the thing it was written for.

Adding a vocabulary therefore means adding a line here. That is the moment to
decide where it belongs, which is the same reason TestColumnCoverage in
test_supersede_block.py maintains its column sets by hand.
"""
from __future__ import annotations

import importlib
import json
import re
import typing
from enum import Enum
from pathlib import Path

import pytest

_MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"

#: (table, column) → the Python symbol that must carry the same values.
#:
#: `None` means: this vocabulary deliberately has no Python counterpart yet.
#: Written as an entry rather than an omission, so "not done" and "not noticed"
#: stay distinguishable — an omission looks exactly like an oversight.
VOCABULARIES: dict[tuple[str, str], str | None] = {
    ("actors", "role"): "kernel.roles:Role",
    ("scope_memberships", "role"): "kernel.roles:Role",
    ("scope_nodes", "node_type"): "kernel.schema:ScopeNodeType",
    # Migration 024 (CHY-160). The same four words as a model's ceiling in
    # router/routing-config.yaml, and deliberately one symbol: the check
    # compares one with the other, so two lists would be two scales.
    ("scope_nodes", "privacy_level"): "kernel.schema:PrivacyLevel",
    ("context_blocks", "block_type"): "contexts.repository.schema:BlockType",
    ("context_blocks", "status"): "contexts.repository.schema:BlockStatus",
    ("context_blocks", "privacy_impact"): "contexts.repository.schema:PrivacyImpact",
    ("learning_proposals", "status"): "contexts.learning.schema:ProposalStatus",
    ("learning_proposals", "source_type"): "contexts.learning.schema:ProposalSource",
    ("benchmark_reports", "status"): "contexts.learning.schema:ReportStatus",
    ("workflow_definitions", "status"): "contexts.orchestration.schema:WorkflowStatus",
    ("workflow_nodes", "node_type"): "contexts.orchestration.schema:NodeType",
    ("workflow_nodes", "join_policy"): "contexts.orchestration.schema:JoinPolicy",
    ("workflow_connections", "execution_mode"): "contexts.orchestration.schema:ExecutionMode",
    ("pilot_runs", "status"): "contexts.pilots.schema:RunStatus",
    # Added by migration 018 (CHY-82). workflow_runs.status carries the same
    # six values as pilot_runs.status and deliberately so — but as two separate
    # entries, because they are two tables and could legitimately diverge one
    # day. Sharing one symbol would hide that day.
    ("workflow_nodes", "review_role"): "contexts.orchestration.schema:ReviewRole",
    # 027, CHY-176: a vote is the decision the API takes, word for word.
    ("review_votes", "decision"): "contexts.orchestration.schema:OversightChoice",
    ("workflow_nodes", "on_timeout"): "contexts.orchestration.schema:ReviewTimeout",
    ("workflow_runs", "status"): "contexts.orchestration.schema:RunStatus",
    ("node_executions", "status"): "contexts.orchestration.schema:NodeExecutionStatus",
    # 044/045, CHY-223 (ADR-050): how strict a gate is, and what a workflow risks.
    ("node_executions", "review_level"): "contexts.orchestration.schema:ReviewLevel",
    ("workflow_definitions", "risk_class"): "contexts.orchestration.schema:RiskClass",
    # 047, CHY-199: direct model calls, classified like a workflow. One symbol on
    # purpose, unlike the run statuses above: both feed votes.minimum, so they
    # may not diverge (contexts/orchestration/schema.py imports the kernel's).
    ("direct_call_risk_classes", "risk_class"): "kernel.schema:RiskClass",
    ("review_samples", "verdict"): "contexts.orchestration.schema:SampleVerdict",
    # 048, CHY-269: the verdict on a drawn direct call, the same as on a pass.
    ("direct_call_samples", "verdict"): "contexts.orchestration.schema:SampleVerdict",
    # 028, CHY-67 (ADR-043): benchmark runs and their measurements.
    ("benchmark_runs", "mode"): "contexts.learning.schema:BenchmarkMode",
    ("benchmark_runs", "status"): "contexts.learning.schema:BenchmarkRunStatus",
    ("benchmark_measurements", "variant"): "contexts.learning.schema:MeasurementVariant",
    ("benchmark_measurements", "outcome"): "contexts.learning.schema:MeasurementOutcome",
    # 030, CHY-17 (ADR-046): the retention classes that take a period.
    ("retention_policies", "retention_class"): "kernel.schema:ConfigurableRetentionClass",
}


def _sql_vocabularies() -> dict[tuple[str, str], list[str]]:
    """Every CHECK ... IN (...) in the migrations, latest definition wins.

    Both spellings are read. A column added later carries its CHECK in an
    ALTER TABLE, and a nullable vocabulary is written `x IS NULL OR x IN (...)`
    — a pattern that matches only the column name, not the opening bracket. The
    first draft of this parser missed `context_blocks.privacy_impact` for
    exactly that reason and would have handed the guard a blind spot on its
    first day.
    """
    found: dict[tuple[str, str], list[str]] = {}
    for path in sorted(_MIGRATIONS.glob("[0-9]*.sql")):
        sql = path.read_text(encoding="utf-8")

        for table_match in re.finditer(
            r"CREATE TABLE (?:IF NOT EXISTS )?(\w+)\s*\((.*?)\n\);", sql, re.S
        ):
            table, body = table_match.group(1), table_match.group(2)
            for m in re.finditer(
                r"^\s*(\w+)\s+[A-Z].*?CHECK\s*\(.*?\1\s+IN\s*\(([^)]*)\)", body, re.S | re.M
            ):
                values = sorted(set(re.findall(r"'([a-z_]+)'", m.group(2))))
                if values:
                    found[(table, m.group(1))] = values

        # A column added with its CHECK inline (044, CHY-223). Not seen until
        # then: `risk_class` slipped past this guard for one migration.
        for m in re.finditer(
            r"ALTER TABLE (\w+)\s+ADD COLUMN (?:IF NOT EXISTS )?(\w+)\s+[A-Z]\w*\s+"
            r"CHECK\s*\((.*?)\);", sql, re.S
        ):
            for clause in re.finditer(r"(\w+)\s+IN\s*\(([^)]*)\)", m.group(3)):
                values = sorted(set(re.findall(r"'([a-z_]+)'", clause.group(2))))
                if values and clause.group(1) == m.group(2):
                    found[(m.group(1), m.group(2))] = values

        for m in re.finditer(
            # NOT VALID may follow (048); without it in the pattern the lazy
            # match ran on to the next statement's `);` and gave this table the
            # vocabularies of the one after it.
            r"ALTER TABLE (\w+)\s+ADD CONSTRAINT \w+\s+CHECK\s*\((.*?)\)\s*(?:NOT VALID\s*)?;",
            sql, re.S
        ):
            table = m.group(1)
            for clause in re.finditer(r"(\w+)\s+IN\s*\(([^)]*)\)", m.group(2)):
                values = sorted(set(re.findall(r"'([a-z_]+)'", clause.group(2))))
                if values:
                    found[(table, clause.group(1))] = values

        # A dropped table takes its vocabularies with it. "Latest definition
        # wins" is only true if removal counts as a definition: 025 dropped
        # merge_requests (CHY-65), and without this its CHECK from 001 stayed
        # "found" and demanded a Python type for a table that no longer exists.
        for m in re.finditer(r"^DROP TABLE (?:IF EXISTS )?(\w+)", sql, re.M):
            for key in [k for k in found if k[0] == m.group(1)]:
                del found[key]
    return found


def _python_values(symbol: str) -> list[str]:
    """The values behind `module:Name`, whether it is a Literal or an Enum."""
    module_name, _, name = symbol.partition(":")
    obj = getattr(importlib.import_module(module_name), name)
    if isinstance(obj, type) and issubclass(obj, Enum):
        return sorted(member.value for member in obj)
    return sorted(typing.get_args(obj))


_SQL = _sql_vocabularies()


def test_the_parser_finds_the_vocabularies_at_all():
    """A parser that finds nothing makes every assertion below vacuously true.

    The number is deliberately a lower bound rather than an exact count: this
    test exists to catch a regex that stopped matching, not to be updated every
    time a migration adds a column.
    """
    assert len(_SQL) >= 15, (
        f"only {len(_SQL)} vocabularies found in the migrations — the parser is "
        f"probably no longer matching: {sorted(_SQL)}"
    )


def test_every_sql_vocabulary_is_accounted_for():
    """A new CHECK must be given a Python counterpart, or an explicit None.

    This is the half that catches growth. Without it the guard would only
    protect the vocabularies somebody remembered to list.
    """
    missing = set(_SQL) - set(VOCABULARIES)
    assert not missing, (
        f"CHECK vocabularies with no entry in VOCABULARIES: {sorted(missing)}. "
        f"Add the Python type that mirrors them, or None with a reason."
    )


def test_no_entry_points_at_a_vocabulary_that_is_gone():
    """The other direction: a dropped CHECK leaves a mapping guarding nothing.

    Without this the table would fill up with entries for columns that no
    longer exist, and each one would keep passing.
    """
    stale = set(VOCABULARIES) - set(_SQL)
    assert not stale, (
        f"VOCABULARIES names columns with no CHECK in the migrations: "
        f"{sorted(stale)}. Either the constraint was dropped or the name is wrong."
    )


@pytest.mark.parametrize(
    ("table", "column"),
    sorted(k for k, v in VOCABULARIES.items() if v is not None),
    ids=lambda p: p if isinstance(p, str) else str(p),
)
def test_sql_and_python_agree(table: str, column: str):
    symbol = VOCABULARIES[(table, column)]
    assert symbol is not None
    in_sql = _SQL[(table, column)]
    in_python = _python_values(symbol)
    assert in_sql == in_python, (
        f"{table}.{column} and {symbol} have drifted apart.\n"
        f"  only in SQL:    {sorted(set(in_sql) - set(in_python))}\n"
        f"  only in Python: {sorted(set(in_python) - set(in_sql))}"
    )


# ── The lists derived from a vocabulary ───────────────────────────────────────

@pytest.mark.parametrize("status", sorted(_SQL[("context_blocks", "status")]))
def test_every_stored_status_is_an_acceptable_filter(status: str):
    """Driven through check_status_filter, not by comparing ALL_STATUSES.

    ALL_STATUSES is derived from BlockStatus, and a probe that put a shorter
    hand-written list back in its place left the whole suite green — nothing
    used it in a way any test could see. What the constant actually decides is
    which `?status=` values a listing accepts, so that is what is asserted: a
    state the database can store and the API refuses to filter by is a row
    nobody can find.
    """
    from contexts.repository import block as rules

    assert rules.check_status_filter(status) == status


def test_the_default_listing_is_a_subset_of_the_vocabulary():
    """LISTED_BY_DEFAULT is a policy, not the vocabulary — but it must be made
    of real values. A typo there would silently return nothing for that state
    rather than fail."""
    from contexts.repository import block as rules

    unknown = set(rules.LISTED_BY_DEFAULT) - set(_SQL[("context_blocks", "status")])
    assert not unknown, f"LISTED_BY_DEFAULT names states the database cannot store: {unknown}"


def test_terminal_run_states_are_real_states():
    """Same for the pilots context: TERMINAL_STATES decides when a run can no
    longer be cancelled, and a value outside the vocabulary would make that
    check silently never true."""
    from contexts.pilots.schema import TERMINAL_STATES

    unknown = {str(s) for s in TERMINAL_STATES} - set(_SQL[("pilot_runs", "status")])
    assert not unknown, f"TERMINAL_STATES names states the database cannot store: {unknown}"


# ── A vocabulary that is a subset, not a column (CHY-134) ─────────────────────

def test_the_composable_types_in_the_database_match_the_python_allow_list():
    """The CHECK on workflow_nodes.context_requirements repeats
    COMPOSABLE_BLOCK_TYPES, and nothing else can hold the two together.

    Its latest definition counts: 022 introduced it, 028 took
    calibration_anchor out (ADR-043). A guard reading only 022 would have gone
    on comparing against a list the database no longer has.

    The constraint is not a `CHECK ... IN (...)` over a column — it is jsonb
    containment inside `context_requirements` — so the parser above does not
    see it, and an entry in VOCABULARIES would guard nothing.

    Drift here is quiet in the worst way. If a type is added to the Python set
    and not to the SQL, the database refuses a value the application considers
    valid, and the author reads a 422 about a block type that plainly exists.
    In the other direction a non-composable type becomes storable, and the node
    composes nothing while reporting success (ADR-037).
    """
    from contexts.repository.schema import COMPOSABLE_BLOCK_TYPES

    definitions = [
        (path.name, m.group(1))
        for path in sorted(_MIGRATIONS.glob("[0-9]*.sql"))
        for m in re.finditer(
            r"ADD CONSTRAINT workflow_nodes_requirements_shape.*?<@\s*'(\[[^']*])'::jsonb",
            path.read_text(encoding="utf-8"), re.S,
        )
    ]
    assert definitions, (
        "no jsonb allow-list found for workflow_nodes_requirements_shape — the "
        "constraint was rewritten and this guard stopped guarding"
    )
    where, literal = definitions[-1]
    in_sql = set(json.loads(literal))
    assert in_sql == set(COMPOSABLE_BLOCK_TYPES), (
        f"{where} and COMPOSABLE_BLOCK_TYPES have drifted.\n"
        f"  only in SQL:    {sorted(in_sql - COMPOSABLE_BLOCK_TYPES)}\n"
        f"  only in Python: {sorted(COMPOSABLE_BLOCK_TYPES - in_sql)}"
    )


# ── The migration chain itself (CHY-148) ──────────────────────────────────────

#: Numbers that were never used, and the reason.
#:
#: 008 has no file and no commit in the whole history — checked with
#: `git log --all -- 'migrations/008*'`, which returns nothing. Harmless while
#: everything is applied in sorted order, but it means "migrations 001–022" is
#: **21 files**, not 22. Written here as data so the next reader does not have
#: to re-derive it, and so a *new* gap is distinguishable from this one.
KNOWN_GAPS: frozenset[int] = frozenset({8})


def _migration_numbers() -> list[int]:
    return [int(path.name[:3]) for path in sorted(_MIGRATIONS.glob("[0-9]*.sql"))]


def test_no_two_migrations_share_a_number():
    """The one that would actually break something.

    Two files numbered 023 apply in whatever order the filenames sort in, and
    the loser silently never runs on a database where the winner created what
    it needed. The gap at 008 is cosmetic; a collision is not.
    """
    numbers = _migration_numbers()
    duplicates = sorted({n for n in numbers if numbers.count(n) > 1})
    assert not duplicates, (
        f"two migration files share a number: {duplicates}. The runner applies "
        f"them in filename order, so one of them runs against a schema the "
        f"other assumed it would create."
    )


def test_the_numbering_has_no_undocumented_gap():
    """A gap is fine; an *unexplained* gap is a missing file nobody noticed.

    Without this, a migration deleted by a bad merge looks exactly like a
    number somebody skipped — and the difference only shows up on a fresh
    database, which is the last place anyone looks.
    """
    numbers = _migration_numbers()
    expected = set(range(1, max(numbers) + 1)) - KNOWN_GAPS
    missing = sorted(expected - set(numbers))
    assert not missing, (
        f"migration numbers with no file: {missing}. Either a file was lost, "
        f"or the number was skipped on purpose — then add it to KNOWN_GAPS "
        f"with the reason."
    )
    stale = sorted(KNOWN_GAPS & set(numbers))
    assert not stale, (
        f"KNOWN_GAPS lists {stale}, but those files exist. Remove the entry — "
        f"an exemption that no longer applies hides the next real gap."
    )
