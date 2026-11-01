"""
scripts/public_snapshot.py

What goes into the public repository, and a check that nothing internal goes with it
(CHY-188).

The public GitHub repository is a snapshot. Its history starts fresh: the internal
history never leaves the self-hosted GitLab, because a cleanup commit removes a file from
the current tree, not from the commits before it. Each release is copied from `dev` into
the `publish` branch as one commit — never merged, since a merge would bring the whole
internal history along.

Every tracked file is either PUBLIC or INTERNAL. A file in neither stops the check: a new
top-level file must be classified on purpose, not slip into the snapshot or out of it by
default.

The patterns come in two kinds:

  - generic ones, below: private addresses, private keys, token shapes. Safe to publish
  - internal names, from `.leak-patterns`: one regular expression per line. That file is
    INTERNAL itself — a check that listed the internal host names would publish them

Usage:
    python scripts/public_snapshot.py --check
        Scan the public files of this checkout. Exit 1 on a finding or an unclassified file.
    python scripts/public_snapshot.py --export DIR [--docs DOCS_CHECKOUT]
        Replace the contents of DIR (a checkout of `publish`, `.git` kept) with the public
        files, the publish CI definition and, with --docs, the whitepaper and arc42 under
        docs/. Then scan DIR. Exit 1 on a finding. DIR must be a checkout of `publish`.
    python scripts/public_snapshot.py --scan DIR
        Scan every tracked file of DIR — the publish branch's CI runs this before the push.
        Internal patterns come from the environment variable LEAK_PATTERNS there. Exit 1
        on a finding, and also when DIR tracks no file or no internal pattern is loaded:
        either way the scan would have looked at less than it claims.
    python scripts/public_snapshot.py --visibility OWNER/NAME --private-until YYYY-MM-DD
        Ask GitHub whether the repository is private, with GITHUB_TOKEN. Exit 1 if it is
        public before the date (CHY-132). Reads metadata only; it cannot change anything.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import http.client
from http import HTTPStatus
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: Paths copied into the snapshot. A trailing slash means the whole directory.
PUBLIC: tuple[str, ...] = (
    ".env.example", ".gitignore", "COMMERCIAL-LICENSE.md", "CONTRIBUTING.md",
    "CONVENTIONS.md", "DEPENDENCIES.md", "Dockerfile", "LICENSE.md", "MODEL-DRIFT.md",
    "README.md", "SECURITY.md", "openapi.yaml", "pyproject.toml", "quality-baseline.json",
    "api/", "assets/", "cli/", "contexts/", "contracts/", "core/", "docker/", "kernel/",
    "migrations/", "pilots/", "router/", "scripts/", "tests/",
    # Not in this repository: --export --docs copies the whitepaper and arc42 there
    # from the documentation repository. Listed so the snapshot classifies them.
    "docs/",
)

#: Paths that stay in GitLab. Each for a reason worth one line.
INTERNAL: tuple[str, ...] = (
    ".gitlab-ci.yml",        # deploys to the reference installation
    ".sops.yaml",            # secret encryption setup of the reference installation
    ".env.dev.example",      # stage templates of the reference installation
    ".env.test.example",
    ".env.prod.example",
    "compose.yaml",          # the reference installation's stack
    "compose.override.yaml",
    "Makefile",              # drives compose.yaml
    "renovate.json",         # runs against the self-hosted GitLab
    ".leak-patterns",        # the internal names this check looks for
    "publish/",              # the publish branch's own CI, copied to its root by --export
)

#: The CI definition of the publish branch, kept here and copied to its root.
PUBLISH_CI = "publish/gitlab-ci.yml"

#: The documents published under docs/, and the images they reference.
DOCS: tuple[str, ...] = (
    "cohereny-whitepaper.md", "cohereny-arc42.md",
    "assets/branding/cohereny-logo-horizontal.svg",
)

GENERIC_PATTERNS: tuple[tuple[str, str], ...] = (
    ("private IPv4 address",
     (r"\b(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
      r"|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}"
      r"|192\.168\.\d{1,3}\.\d{1,3})\b")),
    ("private key", r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    ("GitLab token", r"\bglpat-[A-Za-z0-9_-]{20,}"),
    ("GitHub token", r"\bgh[pousr]_[A-Za-z0-9]{30,}"),
    ("YouTrack token", r"\bperm-[A-Za-z0-9=._-]{20,}"),
)

PATTERNS_FILE = ".leak-patterns"

#: Resolved once. None where git is missing, which _git() reports in words.
GIT = shutil.which("git")


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    label: str
    text: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.label}: {self.text}"


def _matches(path: str, entries: tuple[str, ...]) -> bool:
    return any(path == e or (e.endswith("/") and path.startswith(e)) for e in entries)


def _git() -> str:
    if GIT is None:
        msg = "git is not installed — the snapshot is defined by `git ls-files`"
        raise SystemExit(msg)
    return GIT


def tracked_files(root: Path) -> list[str]:
    out = subprocess.run([_git(), "ls-files"], cwd=root, capture_output=True,
                         text=True, check=True).stdout
    return [line for line in out.splitlines() if line]


def classify(files: list[str]) -> tuple[list[str], list[str]]:
    """(public files, files that are neither public nor internal)."""
    public = [f for f in files if _matches(f, PUBLIC)]
    unclassified = [f for f in files if not _matches(f, PUBLIC) and not _matches(f, INTERNAL)]
    return public, unclassified


def load_patterns(root: Path) -> list[tuple[str, re.Pattern[str]]]:
    """Generic patterns plus the internal ones from .leak-patterns, if present.

    In the public repository the file does not exist, and only the generic patterns
    apply — which is the point: they are the ones that may be read there.
    """
    patterns = [(label, re.compile(rx)) for label, rx in GENERIC_PATTERNS]
    lines: list[str] = []
    source = root / PATTERNS_FILE
    if source.exists():
        lines += source.read_text(encoding="utf-8").splitlines()
    # The publish branch has no .leak-patterns; its CI hands the same list in here.
    lines += os.environ.get("LEAK_PATTERNS", "").splitlines()
    for raw in lines:
        rx = raw.strip()
        if rx and not rx.startswith("#"):
            patterns.append(("internal name", re.compile(rx, re.IGNORECASE)))
    return patterns


def scan(root: Path, files: list[str], patterns: list[tuple[str, re.Pattern[str]]]) -> list[Finding]:
    """Every pattern hit in the given files. Binary files are skipped."""
    findings: list[Finding] = []
    for name in files:
        data = (root / name).read_bytes()
        if b"\0" in data:
            continue
        text = data.decode("utf-8", errors="replace")
        for number, line in enumerate(text.splitlines(), start=1):
            for label, rx in patterns:
                if rx.search(line):
                    findings.append(Finding(name, number, label, line.strip()[:120]))
    return findings


def _branch(path: Path) -> str | None:
    if not (path / ".git").exists():
        return None
    # symbolic-ref, not rev-parse: it names the branch even before its first commit,
    # where rev-parse fails — and a failure read as "not publish" refused for the
    # wrong reason (found by the export dry run on 29.10.2026).
    out = subprocess.run([_git(), "symbolic-ref", "--short", "HEAD"], cwd=path,
                         capture_output=True, text=True, check=False)
    return out.stdout.strip() if out.returncode == 0 else None


def export(source: Path, target: Path, docs: Path | None) -> list[str]:
    """Make `target` hold exactly the snapshot. Returns the files written.

    Everything in the target except `.git` is removed first, so a file dropped from
    PUBLIC disappears from the snapshot instead of lingering from an earlier copy.
    """
    for entry in target.iterdir():
        if entry.name == ".git":
            continue
        shutil.rmtree(entry) if entry.is_dir() else entry.unlink()

    public, _ = classify(tracked_files(source))
    written: list[str] = []
    for name in public:
        dest = target / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / name, dest)
        written.append(name)

    shutil.copy2(source / PUBLISH_CI, target / ".gitlab-ci.yml")
    written.append(".gitlab-ci.yml")

    if docs is not None:
        for name in DOCS:
            dest = target / "docs" / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(docs / name, dest)
            written.append(f"docs/{name}")
    return sorted(written)


def visibility(repo: str, private_until: datetime.date, today: datetime.date,
               fetch=None) -> tuple[bool, str]:
    """(ok, message) for the repository's visibility on `today` (CHY-132).

    Before the date the repository must be private. From the date on this only reports:
    going public is a deliberate act (CHY-1), and a check that failed every pipeline
    until somebody clicked would be noise, not a guard.
    """
    fetch = fetch or _github_repo
    data = fetch(repo)
    private = bool(data["private"])
    state = "private" if private else "public"
    if today < private_until and not private:
        return False, (f"{repo} is PUBLIC, and it must stay private until "
                       f"{private_until.isoformat()}. Nothing is mirrored.")
    return True, f"{repo} is {state} ({today.isoformat()})."


def _github_repo(repo: str) -> dict:
    """GET /repos/<repo> on the GitHub API — a fixed host, over HTTPS only."""
    token = os.environ["GITHUB_TOKEN"]
    conn = http.client.HTTPSConnection("api.github.com", timeout=20)
    try:
        conn.request("GET", f"/repos/{repo}", headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "cohereny-publish-check",
        })
        resp = conn.getresponse()
        body = resp.read()
    finally:
        conn.close()
    if resp.status != HTTPStatus.OK:
        msg = f"GitHub answered {resp.status} for {repo}: {body[:200]!r}"
        raise SystemExit(msg)
    return json.loads(body)


def _report(findings: list[Finding], unclassified: list[str]) -> int:
    for f in findings:
        print(f, file=sys.stderr)
    for name in unclassified:
        print(f"{name}: neither PUBLIC nor INTERNAL — classify it in "
              f"scripts/public_snapshot.py", file=sys.stderr)
    if findings or unclassified:
        print(f"\n{len(findings)} finding(s), {len(unclassified)} unclassified file(s). "
              "Nothing may be published like this.", file=sys.stderr)
        return 1
    return 0


def _scan_dir(target: Path, patterns: list[tuple[str, re.Pattern[str]]]) -> int:
    """--scan: every tracked file of target, against the generic and internal patterns."""
    files = tracked_files(target)
    internal = sum(1 for label, _ in patterns if label == "internal name")
    # Both cases used to end in "no finding". On 29.10.2026 a scan before the first
    # `git add` reported "0 files scanned, no finding", and the CI variable had been
    # saved as `Leak_Patterns` — environment names are case-sensitive, so the scan
    # would have checked the generic shapes only, and said so in the same words.
    if not files:
        print(f"{target} tracks no file — nothing was scanned", file=sys.stderr)
        return 1
    if not internal:
        print("No internal patterns loaded: set LEAK_PATTERNS (a protected CI variable "
              "on the publish branch). Without them only the generic shapes are checked.",
              file=sys.stderr)
        return 1
    code = _report(scan(target, files, patterns), [])
    if code == 0:
        print(f"{len(files)} files scanned against {internal} internal and "
              f"{len(patterns) - internal} generic patterns, no finding.")
    return code


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--export", type=Path, metavar="DIR")
    mode.add_argument("--scan", type=Path, metavar="DIR")
    mode.add_argument("--visibility", metavar="OWNER/NAME")
    ap.add_argument("--docs", type=Path, metavar="DOCS_CHECKOUT")
    ap.add_argument("--private-until", type=datetime.date.fromisoformat)
    args = ap.parse_args(argv)

    if args.visibility:
        if args.private_until is None:
            ap.error("--visibility needs --private-until")
        # UTC, not the machine's zone. For the first hour of a German date UTC is still
        # on the day before, so the check demands "private" an hour longer, never shorter.
        today = datetime.datetime.now(datetime.UTC).date()
        ok, message = visibility(args.visibility, args.private_until, today)
        print(message, file=sys.stdout if ok else sys.stderr)
        return 0 if ok else 1

    patterns = load_patterns(ROOT)
    if args.scan:
        return _scan_dir(args.scan.resolve(), patterns)
    if args.check:
        public, unclassified = classify(tracked_files(ROOT))
        code = _report(scan(ROOT, public, patterns), unclassified)
        if code == 0:
            print(f"{len(public)} public files, no finding.")
        return code

    target = args.export.resolve()
    # The export empties its target. Only a checkout of `publish` may be one — not this
    # repository, and not a directory that merely happens to contain a .git.
    if target == ROOT.resolve() or _branch(target) != "publish":
        print(f"{target} is not a checkout of the publish branch — refusing to empty it",
              file=sys.stderr)
        return 2
    written = export(ROOT, target, args.docs)
    code = _report(scan(target, written, patterns), [])
    if code == 0:
        print(f"{len(written)} files exported to {target}, no finding.")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
