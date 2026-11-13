"""
tests/test_after_deploy.py

The check that closes every deploy job (CHY-322), and the jobs that call it.

Until 0.2.1 a deploy job ran build, migrations and `up -d` in one ssh string
without `set -e`: its exit code was that of `up -d`, and a failed migration
reported success (code review R7-01). `scripts/after_deploy.sh` now fails the
job when the API does not answer with the version just deployed or the database
is not on the newest migration. Shown here against stand-ins for `curl` and
`docker` on PATH, each with the case it must catch; and the CI file is read so
that a deploy job without `set -eu` or without the check fails a test.
"""
from __future__ import annotations

import os
import re
import stat
import subprocess
import tomllib
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "after_deploy.sh"
VERSION = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
NEWEST = max(int(p.name[:3]) for p in (ROOT / "migrations").glob("[0-9][0-9][0-9]_*.sql"))
PORTS = {"dev": 8001, "test": 8002, "prod": 8000}


def _stand_in(bin_dir: Path, name: str, body: str) -> None:
    path = bin_dir / name
    path.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _run(tmp_path, *, version: str | None = VERSION, migration: int | None = NEWEST):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    if version is None:
        _stand_in(bin_dir, "curl", "exit 7")
    else:
        _stand_in(bin_dir, "curl", f"printf '%s' '{{\"status\":\"ok\",\"version\":\"{version}\"}}'")
    _stand_in(bin_dir, "docker", f"echo ' {migration if migration is not None else ''}'")
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "AFTER_DEPLOY_TRIES": "1"}
    return subprocess.run(["bash", str(SCRIPT), "test", "8002"], cwd=ROOT, env=env,
                          capture_output=True, text=True, timeout=30)


class TestTheCheck:
    def test_the_deployed_version_on_the_newest_migration_passes(self, tmp_path):
        result = _run(tmp_path)
        assert result.returncode == 0, result.stderr
        assert f"version {VERSION} (expected {VERSION}), migration {NEWEST} (expected {NEWEST})" in result.stdout

    def test_a_database_behind_the_deploy_fails_the_job(self, tmp_path):
        """The case of R7-01: the migration did not run, the API did start."""
        result = _run(tmp_path, migration=NEWEST - 1)
        assert result.returncode == 1
        assert f"the database is on migration {NEWEST - 1}, the deploy brought {NEWEST}" in result.stderr

    def test_an_api_still_on_the_old_code_fails_the_job(self, tmp_path):
        result = _run(tmp_path, version="0.0.1")
        assert result.returncode == 1
        assert f"answers with version 0.0.1, the deploy brought {VERSION}" in result.stderr

    def test_an_api_that_never_answers_fails_the_job(self, tmp_path):
        result = _run(tmp_path, version=None)
        assert result.returncode == 1
        assert "did not answer after 1 tries" in result.stderr


def _jobs() -> dict:
    return yaml.safe_load((ROOT / ".gitlab-ci.yml").read_text(encoding="utf-8"))


@pytest.mark.parametrize("stage", sorted(PORTS))
def test_every_deploy_job_stops_at_a_failing_step_and_checks_what_it_deployed(stage):
    remote = next(step for step in _jobs()[f"deploy:{stage}"]["script"] if "docker compose build" in step)
    lines = [line.strip() for line in remote.splitlines() if line.strip()]
    assert lines.index("set -eu") < next(i for i, text in enumerate(lines) if "docker compose build" in text), (
        "set -eu must come before the first step, or a failed migration reports success again")
    assert f"bash scripts/after_deploy.sh {stage} {PORTS[stage]}" in lines, "the deploy must check what it deployed"
    assert lines.index(f"bash scripts/after_deploy.sh {stage} {PORTS[stage]}") > next(
        i for i, text in enumerate(lines) if "docker compose up -d" in text)


def test_prod_is_verified_without_writing():
    """verify:dev and verify:test write a scope, blocks and runs; prod gets a
    read-only check after its deploy, never the end-to-end script."""
    job = _jobs()["verify:prod"]
    assert job["needs"] == ["deploy:prod"]
    script = "\n".join(job["script"])
    assert "e2e_context_lifecycle" not in script
    assert re.search(r"/v1/health", script) and "401" in script
