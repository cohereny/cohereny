"""
tests/test_version.py

The version has one place, pyproject.toml, and everything that names it reads
it from there (CHY-271).

Until CHY-271 the version stood in pyproject.toml and in the setting
`api_version`, and every stage's environment set API_VERSION=0.1.0. Raising the
number in one place would have changed nothing a caller sees.
"""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from api.config import Settings
from api.main import app
from core import version

ROOT = Path(__file__).resolve().parent.parent


def test_the_version_is_what_pyproject_says():
    assert version.VERSION == version.read(ROOT / "pyproject.toml")


def test_it_is_not_a_setting_an_environment_could_override():
    assert "api_version" not in Settings.model_fields


def test_no_env_template_sets_it():
    """An operator who copies a template would carry a stale number along."""
    templates = sorted(ROOT.glob(".env*.example"))
    assert templates, "no .env templates found — the check would pass on nothing"
    assert [t.name for t in templates if "API_VERSION" in t.read_text(encoding="utf-8")] == []


def test_the_contract_and_the_health_check_name_it():
    assert app.openapi()["info"]["version"] == version.VERSION
    assert TestClient(app).get("/v1/health").json()["version"] == version.VERSION


def test_reading_follows_the_file(tmp_path):
    """Counter-probe to the first test: read() reports the file it is given,
    so the equality above is not a constant compared with itself."""
    other = tmp_path / "pyproject.toml"
    other.write_text('[project]\nname = "x"\nversion = "9.8.7"\n', encoding="utf-8")
    assert version.read(other) == "9.8.7"
