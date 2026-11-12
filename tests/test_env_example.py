"""README "Getting started" begins with `cp .env.example .env` (CHY-188).

pydantic-settings refuses a key in .env that is not a setting. Until 30.10.2026
the template carried POSTGRES_PASSWORD, for a compose file that is not part of
the public repository, and the API failed on import right after the first step
of the README — `pytest` with it, because tests/conftest.py imports the app.
These tests take that step.
"""
import shutil
from pathlib import Path

import pytest

from api.config import Settings

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = REPO_ROOT / ".env.example"


def _keys(path: Path) -> list[str]:
    """Assigned keys of a dotenv file, comments and blank lines skipped."""
    lines = path.read_text(encoding="utf-8").splitlines()
    return [ln.split("=", 1)[0].strip() for ln in lines
            if "=" in ln and not ln.lstrip().startswith("#")]


def test_every_key_in_the_template_is_a_setting():
    known = {name.upper() for name in Settings.model_fields}
    unknown = [k for k in _keys(TEMPLATE) if k not in known]
    assert not unknown, (
        f".env.example names keys api/config.py does not know: {unknown}. "
        f"With them in .env the API refuses to start."
    )


def test_settings_load_from_a_copy_of_the_template(tmp_path, monkeypatch):
    shutil.copy(TEMPLATE, tmp_path / ".env")
    monkeypatch.chdir(tmp_path)
    # Values must come from the copied file, not from the test environment.
    for key in _keys(TEMPLATE):
        monkeypatch.delenv(key, raising=False)
    settings = Settings()
    assert settings.database_url.endswith("@localhost:5432/cohereny")
    assert settings.environment == "prod"
    assert settings.api_docs is False


def test_an_unknown_key_stops_the_start(tmp_path, monkeypatch):
    """The counter-test: the old template's failure, shown on purpose.

    Without it the two tests above would also pass if pydantic-settings began
    to ignore unknown keys — and the template could fill up again unnoticed.
    """
    (tmp_path / ".env").write_text("POSTGRES_PASSWORD=change-me\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("POSTGRES_PASSWORD", raising=False)
    with pytest.raises(ValueError, match="Extra inputs are not permitted"):
        Settings()
