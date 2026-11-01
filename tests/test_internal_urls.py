"""Guards every internal endpoint in configuration (CHY-78, widened by CHY-96).

Langfuse received nothing at all between 2026-07-15 and 2026-08-07. The cause
was a single character: LANGFUSE_HOST was configured as `http://` while the
Traefik route only serves HTTPS. Plain HTTP there answers `404 page not found`
rather than redirecting, and the Langfuse SDK deliberately swallows transport
errors so it never disrupts the calling application. The result is a failure
mode with no symptom -- the pilots ran, reported success, and traced nothing.

These tests are deliberately cheap and offline. They cannot prove traces
arrive, only that the one setting that silently broke it stays correct. The
live check belongs in monitoring, not here.

**CHY-96 widened them from Langfuse to every internal service URL.**
The narrow guard was itself an instance of the problem it guarded against: it
covered the endpoint that had already failed, while `LITELLM_BASE_URL` sat two
lines above it in the same files, also on `http://`, behind the same Traefik
route. An LLM call that 404s is not silent the way a lost trace is — but it is
just as avoidable, and nothing was watching.

Same for the hostname: `pilots/study/.env.example` still pointed at two hosts
of an internal domain decommissioned with the move to the current application
server. A template nobody validates is a template somebody
copies.
"""

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

# Every place that carries a Langfuse endpoint. Kept explicit rather than
# globbed: a new pilot should fail this list loudly and get added on purpose.
CONFIG_FILES = [
    REPO_ROOT / "api" / "config.py",
    REPO_ROOT / ".env.example",
    REPO_ROOT / "pilots" / "study" / "study" / "config.py",
    REPO_ROOT / "pilots" / "banking" / "banking" / "config.py",
    REPO_ROOT / "pilots" / "study" / ".env.example",
    REPO_ROOT / "DEPENDENCIES.md",
]

# Any URL of an internal service, not only the ones on a LANGFUSE_HOST line.
# The narrower pattern had a hole that this suite existed to prevent:
# DEPENDENCIES.md carried the Langfuse host as `**Internal:** http://…`
# one line below the block the guard did check, and it went unnoticed until
# CHY-29 — the same wrong URL the guard was written for, in prose instead of in
# an assignment.
# `litellm` since CHY-188: the gateway's placeholder is litellm.example.com,
# and a pattern without it would let that line drop out of the check silently.
HOST_PATTERN = re.compile(r"(https?://[^\s\"')`]*(?:langfuse|ai-routing|litellm|auth|cohereny-api)[^\s\"')`]*)")

# Hostnames that no longer resolve, retired with the application server
# migration (INFRA-31); a configuration template still naming one is a template
# that produces a broken deployment when someone copies it.
#
# The names themselves are internal, so they are not listed here: they live in
# .leak-patterns with every other internal name, a file that never leaves the
# self-hosted repository (scripts/public_snapshot.py). The check below uses all
# of them — a configuration template has no business naming any internal host,
# retired or not. In the public snapshot the file is absent and the check
# narrows to what needs no list.
_PATTERNS_FILE = REPO_ROOT / ".leak-patterns"


def _internal_names() -> list[re.Pattern[str]]:
    if not _PATTERNS_FILE.exists():
        return []
    lines = _PATTERNS_FILE.read_text(encoding="utf-8").splitlines()
    return [re.compile(rx, re.IGNORECASE) for rx in (ln.strip() for ln in lines)
            if rx and not rx.startswith("#")]


def _hosts_in(path: Path) -> list[str]:
    if not path.exists():
        return []
    return HOST_PATTERN.findall(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("path", CONFIG_FILES, ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_internal_endpoints_use_https(path: Path) -> None:
    """No internal endpoint may be configured over plain HTTP."""
    assert path.exists(), f"{path.relative_to(REPO_ROOT)} is missing -- update CONFIG_FILES"

    plain_http = [h for h in _hosts_in(path) if h.startswith("http://")]
    assert not plain_http, (
        f"{path.relative_to(REPO_ROOT)} configures an internal service over plain "
        f"HTTP: {plain_http}. Traefik answers 404 there rather than redirecting -- "
        f"see CHY-78, where that cost three weeks of traces nobody missed."
    )


@pytest.mark.parametrize("path", CONFIG_FILES, ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_endpoints_point_at_a_domain_that_exists(path: Path) -> None:
    """Catches endpoints left behind by a rename.

    `.env.example` pointed at an internal Langfuse host when this was written --
    a hostname that stopped existing long before the bug was found, and which
    nobody noticed because nothing ever reads that file in anger. CHY-96 found
    two more in `pilots/study/.env.example`, eight weeks after the domain was
    retired.
    """
    names = _internal_names()
    stale = [h for h in _hosts_in(path)
             if any(rx.search(h) for rx in names) or "contextcrafter" in h]
    assert not stale, (
        f"{path.relative_to(REPO_ROOT)} still references a retired hostname: {stale}"
    )

    # Bare mentions too, not only inside a URL. Two of the three retired-host
    # references CHY-96 removed from pilots/study/.env.example were in
    # *comments* — "# LiteLLM Gateway (<host>)" — which a URL-shaped pattern
    # walks straight past. A comment naming a host that no longer resolves is
    # the instruction somebody follows.
    text = path.read_text(encoding="utf-8")
    mentioned = [rx.pattern for rx in names if rx.search(text)]
    assert not mentioned, (
        f"{path.relative_to(REPO_ROOT)} mentions a retired domain: {mentioned}"
    )


def test_config_files_are_actually_covered() -> None:
    """Fails when a new internal endpoint appears outside the checked files.

    Without this the suite silently shrinks: someone adds a third pilot, the
    two tests above keep passing, and the new config is unguarded.
    """
    searched = list(REPO_ROOT.glob("pilots/*/*/config.py"))
    covered = set(CONFIG_FILES)
    uncovered = [
        p for p in searched
        if p not in covered and "LANGFUSE_HOST" in p.read_text(encoding="utf-8")
    ]
    assert not uncovered, (
        "These files set LANGFUSE_HOST but are not in CONFIG_FILES: "
        f"{[str(p.relative_to(REPO_ROOT)) for p in uncovered]}"
    )


# ── Configuration is read in configuration modules (CHY-96, audit B4) ─────────

# Entry points that legitimately read the environment: they *are* the place a
# process learns where its database is, and there is no settings object above
# them. Anything else reaching for os.environ is a second source of truth.
_ENTRY_POINTS = (
    "cli/main.py",              # cc — bootstraps before the API exists
    "migrations/runner.py",     # standalone migration run
    "scripts/",                 # one-off tooling
)


def _reads_the_environment(path: Path) -> list[str]:
    """Calls to os.getenv / os.environ, from the syntax tree.

    Not a grep: half the modules below explain in a docstring which variable
    they stopped reading, and prose has been counted as code repeatedly this
    month.
    """
    import ast

    found = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not isinstance(node, ast.Attribute):
            continue
        base = node.value
        if isinstance(base, ast.Name) and base.id == "os":
            if node.attr in {"getenv", "environ"}:
                found.append(f"os.{node.attr}")
    return found


def test_only_configuration_modules_read_the_environment() -> None:
    """Audit finding B4: configuration outside the configuration modules.

    The worst instance was `os.environ.setdefault("POSTGRES_URI", db_dsn)` in
    the pilot executor — a *mutation* of the process environment from inside a
    request handler. `setdefault` means the first run wins: a later run with a
    different DSN would silently use the first one's. One container per stage
    made that harmless in practice, which is exactly why it survived.

    Removed by passing the DSN to the pilot graphs instead (CHY-96).
    """
    import subprocess

    tracked = subprocess.run(
        ["git", "ls-files", "*.py"], cwd=REPO_ROOT, capture_output=True, text=True
    ).stdout.splitlines()

    offenders = {}
    for rel in tracked:
        if rel.endswith("config.py") or "/tests/" in rel or "/test_" in rel:
            continue
        if rel.startswith(_ENTRY_POINTS) or "/.venv/" in rel:
            continue
        calls = _reads_the_environment(REPO_ROOT / rel)
        if calls:
            offenders[rel] = calls

    assert not offenders, (
        f"configuration is read outside a config module: {offenders}. "
        f"A value read in two places is two answers to one question — "
        f'LITELLM_API_KEY was already "dummy" in one and "" in the other.'
    )
