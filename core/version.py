"""
core/version.py

The version of this Cohereny, read from pyproject.toml — its one place (CHY-271).

Until CHY-271 it stood twice: in pyproject.toml and as the setting
`api_version`, both 0.1.0 and never raised. A setting was the wrong home for
it: every stage's environment carried API_VERSION=0.1.0, so raising the number
in the code would have changed nothing a caller sees. The version describes the
code, not the deployment, and an operator has no business overriding it.

Read from the file rather than from the installed package's metadata: an
editable install keeps the version it had when it was installed, and a test
would then compare the file with a stale copy of itself. Every image has
pyproject.toml next to the code (`COPY pyproject.toml ./` in the Dockerfile's
`app` stage, which the quickstart builds on too).

Semantic Versioning (decided by Thomas, 10.11.2026): 0.X.Y before 1.0; a MINOR
may change behaviour incompatibly before 1.0, a PATCH only corrects, and is
never a substantial modification in the sense of the CRA. CHANGELOG.md has a
section per version; scripts/changelog.py holds the two together.

Called by:
  - api/main.py (OpenAPI info.version), api/v1/health.py, core/tracing.py
"""
from __future__ import annotations

import tomllib
from pathlib import Path

PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


def read(pyproject: Path = PYPROJECT) -> str:
    """The `project.version` of a pyproject.toml."""
    with pyproject.open("rb") as f:
        return tomllib.load(f)["project"]["version"]


VERSION = read()
