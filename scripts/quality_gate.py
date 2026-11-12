#!/usr/bin/env python3
"""
scripts/quality_gate.py

Ratchet for the metrics the 2026-08-14 audit measured (CHY-84).

The audit found five grade-A defects, and none of them came from looking — they
all surfaced during other work. A document decays; a pipeline does not. This
turns the measurable part of that audit into a gate that runs on every push.

Why a ratchet and not a threshold:
  Enabling all 24 ruff families outright means 1216 failures on day one, and a
  check that fails on day one gets switched off in week one. The ratchet accepts
  the debt that exists and refuses only *new* debt. Eleven families were already
  at zero in production code; those are enforced strictly in pyproject.toml and
  do not appear here.

Direction:
  A count that grows, or coverage that falls, fails the job.
  A count that shrinks passes with a note asking for the baseline to be lowered.
  Improvements are deliberately not enforced — a gate that fails when the code
  gets better teaches people to avoid improving it.

The interesting metric is `unused_actor`. An endpoint that takes
`actor: CurrentUser = Depends(require_auth)` and never reads it is authenticated
and then anonymous: the caller was verified and their identity thrown away. That
is exactly what CHY-88 was, and how CHY-90 and CHY-92 were found. It has its own
counter for that reason.

Usage:
    python scripts/quality_gate.py            # check, exit 1 on regression
    python scripts/quality_gate.py --update   # write the current numbers

Dependencies:
  - ruff and codespell are required; a missing one aborts rather than
    scoring zero. Coverage comes from a prior `pytest --cov` run and is
    reported as unmeasured when absent.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import pathlib
import ast
import re
import shutil
import subprocess
import sys

import architecture

ROOT = pathlib.Path(__file__).resolve().parent.parent
BASELINE = ROOT / "quality-baseline.json"

# Production code only. Tests legitimately use `assert` (582 hits) and magic
# values; counting them would drown the signal.
#
# `pilots` joined on 19.08. (CHY-111). It had been outside every measurement:
# 4842 tracked lines in no SRC list and no --cov flag, including both graph.py
# and the pilot node logic. api/v1/pilots.py was measured at 48 % while the code
# it calls was not measured at all.
# `kernel` and `contexts` added with CHY-105, and the omission was the same
# mistake as in [tool.coverage.run] one file over: every metric below — twelve
# ruff families, unused_actor, api_db_calls, codespell — was counted over a set
# that shrank each time a context was extracted. A refactor that moved code out
# of api/ therefore improved the numbers by measuring less, which is the exact
# failure this script exists to prevent.
#
# `pilots` left again on 09.11.2026 (CHY-262): deprecated since 08.11., going
# with CHY-179. They held 97 of the 234 ruff findings counted here, a debt that
# disappears with them rather than by being paid. codespell had already left
# them out for the same reason (`_is_specimen` below). The strict families in
# pyproject.toml still apply to them through `ruff check .`.
SRC = ["api", "core", "cli", "router", "scripts",
       "kernel", "contexts", "steps"]

#: Paths excluded from every count. Tests live inside the pilot packages rather
#: than in a single tests/ directory, so the exclusion has to be a rule about
#: paths and not a list of directories — otherwise a new pilot brings its own
#: assert statements into the security family and nobody notices why S jumped.
_TEST_PATH = ("/tests/", "/test_", "/conftest.py")

# Families with existing debt. The ones already at zero are enforced by
# pyproject.toml instead — see the module docstring.
RATCHETED = ["B", "SIM", "ARG", "C90", "N", "UP", "S", "EM", "TRY", "PL", "RUF", "ASYNC"]


class ToolMissingError(RuntimeError):
    """A measuring tool is not installed, so its number would be a fiction.

    The message is built here rather than at the raise site so the text stays
    with the type (TRY003/EM102) -- and because there is only ever one way to
    say this.
    """

    def __init__(self, module: str, detail: str) -> None:
        super().__init__(
            f"{module} is not installed in {sys.executable}.\n"
            f"  {detail}\n"
            "Every number would be invented without it. Run inside the venv: "
            ".venv/bin/python scripts/quality_gate.py"
        )


def _require_git() -> None:
    """git is a measuring tool here, so its absence must abort like ruff's.

    codespell counts tracked files, which needs `git ls-files`. The CI image is
    python:3.14-slim and has no git — the first run raised FileNotFoundError,
    which is the right failure. Falling back to "scan the directories" would be
    the wrong one: that is exactly the mode that counted six locally generated
    fixture PDFs and made CI and this machine disagree by 26.
    """
    if shutil.which("git") is None:
        sys.exit(
            "git is required: codespell counts tracked files so the number does "
            "not depend on what happens to be lying in the working tree.\n"
            "  CI: add git to the quality job's before_script."
        )


def _require(module: str) -> None:
    """Fail loudly when a linter is absent instead of counting zero findings.

    Found on 2026-08-16, in this file, by running it outside the venv: every
    ruff family reported 0, `unused_actor` fell 40 -> 0 and codespell 16 -> 0.
    Nothing had been fixed. `python -m ruff` writes "No module named ruff" to
    **stderr** and leaves stdout empty, and `_run` only read stdout — so a
    missing tool was indistinguishable from a clean codebase, and the ratchet
    congratulated us on the improvement.

    A gate that fails open is worse than no gate: it produces evidence. This is
    the same shape as INFRA-96 (node_exporter drops the file, the staleness
    alert cannot see it) and INFRA-63 (jobs reported success and did nothing).

    Exit codes cannot tell the two cases apart — `ruff check` with findings and
    `python -m ruff` without ruff both exit 1. Asking the import system is both
    cheaper and exact: every measurement runs as `sys.executable -m <module>`,
    so the interpreter that resolves the spec is the one that will run the tool.
    """
    try:
        found = importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        found = False
    if not found:
        raise ToolMissingError(module, f"No module named {module!r}")


# Tools whose version changes the numbers. Recorded in the baseline so a
# mismatch is named rather than mistaken for a regression -- see tool_versions.
MEASURING_TOOLS = ("ruff", "codespell")


def tool_versions() -> dict[str, str]:
    """The versions that produced these numbers.

    A ratchet compares counts across time, which only means something if the
    ruler stays the same. `ruff>=0.4` resolved to 0.15.20 locally and 0.16.3 in
    CI, and the same commit scored PL 52 here and 55 there -- three rules that
    simply did not exist in the older version. The gate reported a regression
    nobody had introduced, which is the fastest way to teach people that the
    gate is noise.

    Pinning in pyproject.toml stops it happening. Recording it here makes the
    remaining case -- someone running an unpinned environment -- say so.
    """
    out: dict[str, str] = {}
    for tool in MEASURING_TOOLS:
        # The distribution name and the import name differ for codespell, and
        # _require speaks import names.
        module = "codespell_lib" if tool == "codespell" else tool
        _require(module)
        raw = _run([sys.executable, "-m", module, "--version"]).strip()
        # ruff prints "ruff 0.16.3", codespell prints the bare version
        out[tool] = raw.split()[-1] if raw else "unknown"
    return out


def _run(cmd: list[str]) -> str:
    return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True).stdout


def ruff_counts() -> dict[str, int]:
    _require("ruff")
    out: dict[str, int] = {}
    for family in RATCHETED:
        lines = _run(
            [sys.executable, "-m", "ruff", "check", "--select", family,
             "--ignore", "E501", "--output-format", "concise", *SRC]
        )
        out[family] = sum(
            1 for ln in lines.splitlines()
            if re.match(r"^(%s)/" % "|".join(SRC), ln)
            and not any(marker in ln.split(":")[0] for marker in _TEST_PATH)
        )
    return out


def unused_actor() -> int:
    """Endpoints that authenticate the caller and discard the identity.

    Counted separately from ARG001 because the number means something on its
    own, and because it should reach zero — the placeholder routers will either
    be implemented or deleted.
    """
    _require("ruff")
    lines = _run(
        [sys.executable, "-m", "ruff", "check", "--select", "ARG001",
         "--output-format", "concise", "api"]
    )
    return sum(1 for ln in lines.splitlines() if "actor" in ln)


def boundaries() -> dict[str, int]:
    """Per-rule boundary violations from scripts/architecture.py (CHY-103).

    Replaces the old `layering_violations`, which had two defects the move would
    have exposed: it used glob("*.py") and so would never have seen
    contexts/repository/store.py, and it returned a single number for what are
    four different rules with four different fixes.

    Reported as four metrics rather than one because a ratchet whose floor is
    zero is a hard gate for anything already at zero. `context_isolation`
    starts at 0 -- contexts do not exist yet -- and is therefore gated from the
    first commit; the other three convert themselves on reaching zero.
    """
    return architecture.counts()


def api_db_calls() -> int:
    """Database calls in the transport layer.

    83 % of the SQL lives in api/ (35 calls against 7 in core/) — the layer that
    should hand work on holds most of the work. ADR-001 calls the core headless;
    it is not, and CHY-94 shows the dependency broken in the other direction too.

    Ratcheted for direction, **not towards zero**. A single-statement read
    behind one endpoint gains nothing from a repository and a use case wrapped
    around it; that is ceremony, and the ceremony is how core/ turns into the
    same god module under a different name.

    The criterion is reachability, not location: a rule that can only be
    executed by sending an HTTP request is in the wrong place. Everything the
    CLI, a background job or the future workflow runner (ADR-028) must also be
    able to do belongs below api/. Pagination, status codes and SSE framing
    exist because HTTP exists and stay where they are.

    api/v1/compose.py shows the shape — 63 lines, no SQL, because CHY-61 put the
    work in core/compose.py where /v1/complete could then reuse it (CHY-24).
    """
    n = 0
    for path in (ROOT / "api").rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr in {"fetch", "fetchrow", "fetchval", "execute"}
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id in {"pool", "conn"}):
                n += 1
    return n


def coverage_percent() -> float | None:
    """Read the last pytest --cov run. None when no data is present, so the
    gate degrades to 'not measured' rather than failing for the wrong reason."""
    for name in ("coverage.json", ".coverage.json"):
        p = ROOT / name
        if p.exists():
            return round(json.loads(p.read_text())["totals"]["percent_covered"], 1)
    return None


#: Files whose German is not a typo, so counting it teaches nothing (CHY-142).
#:
#: The counter read 190 on 07.09.2026 and **nine** of those were in product
#: code. The rest were German — and the ten most frequent findings were `ist`,  # codespell:ignore
#: `oder`, `Dezember`, `als`, `Modell`. A debt counter that grows when somebody  # codespell:ignore
#: follows the conventions measures the wrong thing, and this one had started
#: pushing the other way: a test fixture had to be a *German* bank document to
#: prove anything about a deployment that reads German bank documents, and
#: writing it that way cost 29 points.
#:
#: Three exclusions, each for a different reason:
#:
#:   fixtures and generators -- the German is the *output* of the file. A
#:       document specimen is data, and spell-checking a specimen is a category
#:       error
#:   pilots/ -- the prompts are German because the models are prompted in
#:       German, and ADR-028 point 4 keeps this code out of rebuilds anyway.
#:       Ratcheting code with an expiry date buys nothing
#:   .md and .json -- prose and data, already skipped below
#:
#: What is NOT excluded, deliberately: `scripts/`, whose German talks to the
#: operator, and product code. Both must stay measurable, and the few
#: deliberate German lines there carry `# codespell:ignore` — named, one per
#: line, visible in a diff. A blanket ignore-words list was rejected: it would
#: make `ist` unfindable as a typo for `is` in an English docstring, which  # codespell:ignore
#: trades a visible problem for an invisible one.
def _is_specimen(path: str) -> bool:
    return (
        "/fixtures/" in path
        or path.rsplit("/", 1)[-1].startswith("generate_")
        or path.startswith("pilots/")
    )


def codespell_count() -> int | None:
    """Typos in the English prose this project writes.

    Not "German words in the repository", which is what it counted until
    CHY-142. See `_is_specimen` above for what is left out and why.

    Counted over **tracked files only**, and that is not a detail. Handing the
    directories to codespell scanned whatever happened to be lying in them:
    the six fixture PDFs that `generate_fixtures.py` writes exist on a
    developer machine and not in a fresh CI checkout, and they carry German
    prose. The same commit scored 172 here and 146 there — a ratchet compares
    counts over time, which means nothing if the ruler changes with the
    machine. Same lesson as the pinned tool versions above.
    """
    _require("codespell_lib")
    _require_git()
    tracked = [
        p for p in _run(["git", "ls-files", *SRC, "migrations", "tests"]).splitlines()
        if not _is_specimen(p)
    ]
    if not tracked:
        return 0
    out = _run([sys.executable, "-m", "codespell_lib", "--skip",
                "*.venv*,__pycache__,*.lock,openapi.yaml,*.json", *tracked])
    return len(out.splitlines()) if out else 0


def planned_operations() -> int:
    """Operations marked planned in the generated contract.

    Counts YAML keys, not occurrences of the string. The naive count returned 30
    instead of 29 because the API description itself explains the marker — the
    document describing the convention was counted as an instance of it.
    """
    spec = ROOT / "openapi.yaml"
    if not spec.exists():
        return 0
    return sum(
        1 for line in spec.read_text(encoding="utf-8").splitlines()
        if re.match(r"^\s+x-cohereny-status:\s*planned\s*$", line)
    )


def collect() -> dict:
    m = {
        "_tools": tool_versions(),
        "ruff": ruff_counts(),
        "unused_actor": unused_actor(),
        **{f"boundary_{k}": v for k, v in boundaries().items()},
        "api_db_calls": api_db_calls(),
        "planned_operations": planned_operations(),
    }
    cov = coverage_percent()
    if cov is not None:
        m["coverage_percent"] = cov
    cs = codespell_count()
    if cs is not None:
        m["codespell"] = cs
    return m


def tool_drift(current: dict, base: dict) -> list[str]:
    """Version differences between the run and the baseline.

    Returned rather than raised: the numbers below are still worth printing,
    and the caller decides. But it must **stop the run**, not merely appear in
    the output. Every comparison after a version change is between two rulers,
    and a "regression" measured that way is an accusation against the wrong
    commit.
    """
    was = base.get("_tools") or {}
    now = current.get("_tools") or {}
    if not was:
        # A baseline written before versions were recorded. Say so once; the
        # next --update fixes it.
        return ["Grundlinie kennt keine Werkzeugversionen — einmal --update"]
    return [
        f"{tool}: Grundlinie {was[tool]}, dieser Lauf {now.get(tool, '?')}"
        for tool in sorted(was)
        if now.get(tool) != was[tool]
    ]


def unmeasured(current: dict, base: dict) -> list[str]:
    """Metrics the baseline knows and this run did not produce.

    Without this they vanish silently: `compare` skips any key missing on
    either side, so a metric that stops being collected reads exactly like a
    metric with nothing to report. `coverage_percent` does this whenever
    pytest --cov has not run — the same fail-open that `_require` closes for
    the linters, one level up.
    """
    return [k for k in base if k not in {"ruff", "_tools"} and k not in current]


def untracked_sources() -> list[str]:
    """Source files git does not know about yet.

    Almost every number here is counted over `git ls-files`, so that the ruler
    is the same on a developer machine and in CI — the lesson of the fixture
    PDFs, which scored 172 here and 146 there.

    The cost of that choice is invisible until it bites: a brand-new file is
    not tracked, so it is not measured, and the gate reports "unverändert"
    while ignoring the code most likely to contain something new. It happened
    on 2026-09-04 — the gate was green locally and red in CI on the same
    commit, over a misspelling in a file that had not been added yet.

    Halting rather than warning, and for the same reason a version mismatch
    halts: the numbers are not comparable. A measurement over an incomplete
    file set is not a measurement, and "mostly measured" is the shape of
    reassurance this whole script exists to refuse.
    """
    out = _run(["git", "ls-files", "--others", "--exclude-standard", *SRC,
                "migrations", "tests"])
    return sorted(line for line in out.splitlines() if line.endswith((".py", ".sql")))


def compare(current: dict, base: dict) -> tuple[list[str], list[str]]:
    worse, better = [], []

    for family, count in current["ruff"].items():
        was = base.get("ruff", {}).get(family)
        if was is None:
            better.append(f"ruff {family}: neu in der Messung ({count})")
        elif count > was:
            worse.append(f"ruff {family}: {was} → {count}  (+{count - was})")
        elif count < was:
            better.append(f"ruff {family}: {was} → {count}")

    for key, label in (
        ("unused_actor", "Endpunkte, die den Actor verwerfen"),
        ("boundary_inward_only", "Grenze: Abhängigkeit nach außen"),
        ("boundary_context_isolation", "Grenze: Kontext → Kontext"),
        # These two were collected into the baseline and never compared against
        # it — four of six boundary rules were reported, two were silent. Found
        # in CHY-42 the way such things are found: a violation of one of them
        # rode into `--update` unmentioned, and only the diff of the baseline
        # file gave it away. A metric that is measured and never shown is worse
        # than one that is not measured, because it looks covered.
        ("boundary_context_encapsulation", "Grenze: Zugriff am Kontext vorbei"),
        ("boundary_kernel_uses_nothing", "Grenze: Kernel benutzt einen Kontext"),
        ("boundary_framework_free", "Grenze: Framework in der Fachschicht"),
        ("boundary_http_below_transport", "Grenze: HTTPException in der Fachschicht"),
        ("api_db_calls", "Datenbankaufrufe in api/"),
        ("codespell", "codespell"),
        ("planned_operations", "geplante Operationen (501)"),
    ):
        if key not in current or key not in base:
            continue
        if current[key] > base[key]:
            worse.append(f"{label}: {base[key]} → {current[key]}")
        elif current[key] < base[key]:
            better.append(f"{label}: {base[key]} → {current[key]}")

    if "coverage_percent" in current and "coverage_percent" in base:
        # 0.5 points of slack: coverage moves slightly when a line is
        # reformatted, and a gate that trips on noise gets ignored.
        if current["coverage_percent"] < base["coverage_percent"] - 0.5:
            worse.append(
                f"Testabdeckung: {base['coverage_percent']} % → {current['coverage_percent']} %"
            )
        elif current["coverage_percent"] > base["coverage_percent"] + 0.5:
            better.append(
                f"Testabdeckung: {base['coverage_percent']} % → {current['coverage_percent']} %"
            )

    return worse, better


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--update", action="store_true", help="Aktuelle Zahlen als neue Grundlinie schreiben")
    args = ap.parse_args()

    current = collect()

    if args.update or not BASELINE.exists():
        BASELINE.write_text(json.dumps(current, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"Grundlinie geschrieben: {BASELINE.name}")
        print(json.dumps(current, indent=2, sort_keys=True))
        return 0

    untracked = untracked_sources()
    if untracked:
        print("\033[31mAnderes Lineal als die Grundlinie:\033[0m")
        for path in untracked:
            print(f"  \033[31m!\033[0m ungetrackt: {path}")
        print(
            "\nDiese Dateien werden nicht gemessen. Fast jede Kennzahl hier zählt\n"
            "über `git ls-files`, damit die Zahl auf jeder Maschine dieselbe ist —\n"
            "neue Dateien fallen damit aus der Messung, und das sind genau die,\n"
            "in denen ein neuer Befund am wahrscheinlichsten steckt.\n"
            "`git add` und erneut messen. Eine Messung über eine unvollständige\n"
            "Dateimenge ist keine."
        )
        return 1

    base = json.loads(BASELINE.read_text(encoding="utf-8"))
    worse, better = compare(current, base)
    missing = unmeasured(current, base)
    drift = tool_drift(current, base)

    if drift:
        print("\033[31mAnderes Lineal als die Grundlinie:\033[0m")
        for line in drift:
            print(f"  \033[31m!\033[0m {line}")
        print(
            "\nDie Zahlen sind nicht vergleichbar — eine neue Linter-Version bringt\n"
            "neue Regeln, und die zählen als Verschlechterung, die niemand geschrieben hat.\n"
            'Umgebung angleichen (pip install -e ".[dev]") oder die Grundlinie mit\n'
            "--update neu erheben und die Änderung im MR begründen."
        )
        return 1

    if missing:
        print("\033[33mNicht gemessen in diesem Lauf:\033[0m")
        for key in missing:
            print(f"  \033[33m?\033[0m {key} (Grundlinie: {base[key]})")
        print("  → Eine Kennzahl, die stumm ausfällt, sieht aus wie eine ohne Befund.")
        print("     coverage_percent braucht einen vorherigen `pytest --cov`-Lauf.\n")

    if better:
        print("Verbessert:")
        for line in better:
            print(f"  \033[32m✓\033[0m {line}")
        print("  → scripts/quality_gate.py --update senkt die Grundlinie.\n")

    if worse:
        print("\033[31mVerschlechtert — die Grundlinie hält:\033[0m")
        for line in worse:
            print(f"  \033[31m✗\033[0m {line}")
        print(
            "\nEntweder beheben, oder mit Begründung im MR die Grundlinie anheben.\n"
            "Der Sinn dieser Prüfung ist, dass die zweite Möglichkeit auffällt."
        )
        return 1

    if not better:
        print("Unverändert gegenüber der Grundlinie.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
