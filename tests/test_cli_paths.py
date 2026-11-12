"""cc pilot: where pilot data and pilot sources live (CHY-188).

Both used to be hard-coded paths of one host: a mount of a NAS that no longer
exists, and a stack directory on the application server. They now come from
PILOT_DATA_DIR and PILOT_SOURCE_DIR, with defaults that work in a checkout.
"""
from pathlib import Path

from typer.testing import CliRunner

from cli import main as cli

REPO_ROOT = Path(__file__).resolve().parent.parent


def _output(result) -> str:
    """stdout and stderr, whichever way the installed click reports them."""
    return result.output + (getattr(result, "stderr", "") or "")


def test_defaults_fit_a_fresh_checkout(tmp_path, monkeypatch):
    monkeypatch.delenv("PILOT_DATA_DIR", raising=False)
    monkeypatch.delenv("PILOT_SOURCE_DIR", raising=False)
    monkeypatch.chdir(tmp_path)

    assert cli._data_dir() == (tmp_path / "pilot-data").resolve()
    dirs = cli._pilot_dirs()
    assert set(dirs) == {"study", "banking"}
    for path in dirs.values():
        assert path.parent == (REPO_ROOT / "pilots").resolve()
        assert path.is_dir(), f"default pilot source {path} does not exist"


def test_the_environment_overrides_both(tmp_path, monkeypatch):
    monkeypatch.setenv("PILOT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("PILOT_SOURCE_DIR", str(tmp_path / "src"))

    assert cli._data_dir() == (tmp_path / "data").resolve()
    assert cli._pilot_dirs()["banking"] == (tmp_path / "src" / "banking").resolve()


def test_pilot_list_reads_the_configured_directory(tmp_path, monkeypatch):
    examples = tmp_path / "examples"
    examples.mkdir()
    (examples / "banking-sample.pdf").write_bytes(b"%PDF-1.4\n")
    monkeypatch.setenv("PILOT_DATA_DIR", str(tmp_path))

    result = CliRunner().invoke(cli.app, ["pilot", "list"])

    assert result.exit_code == 0, _output(result)
    assert "banking-sample.pdf" in result.output
    assert str(examples.resolve()) in result.output


def test_pilot_list_names_the_missing_directory(tmp_path, monkeypatch):
    missing = tmp_path / "nowhere"
    monkeypatch.setenv("PILOT_DATA_DIR", str(missing))

    result = CliRunner().invoke(cli.app, ["pilot", "list"])

    assert result.exit_code == 1
    assert str((missing / "examples").resolve()) in _output(result)
