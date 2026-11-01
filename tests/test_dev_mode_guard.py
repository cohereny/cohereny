"""DEV_MODE must not be able to reach anything real (CHY-91).

What it does: `api/deps.py` short-circuits token validation and returns a static
admin for every request. What stood between that and production until now: the
right .env file being in the right place, and a comment reading "never use in
prod or test".

A comment is not a control — and the internal CRA assessment certified essential
requirement 2, "Secure by Default", on exactly that basis. The finding was
therefore not only a defect but a false statement in a document written for a
regulator, with the CRA taking effect on 11.09.2026.

Why two conditions rather than one: `environment` alone is self-declaring. The
realistic accident is copying .env.dev over .env.prod, which flips both values
together. The database name is evidence about the data at hand that the flag
cannot fake.
"""
from __future__ import annotations

import pathlib
import sys

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from api.config import Settings  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_environment(monkeypatch):
    """pydantic-settings reads os.environ even with _env_file=None.

    Belt and braces after scripts/export_openapi.py was found setting
    DEV_MODE=true at import time: two tests here failed in the full suite and
    passed alone, because whichever test imported that module first changed the
    process for everything after it. The script no longer does it — this makes
    sure the next one cannot.
    """
    for var in ("DEV_MODE", "ENVIRONMENT", "DATABASE_URL"):
        monkeypatch.delenv(var, raising=False)


def _settings(**kw) -> Settings:
    """Constructed without reading any .env — the test states the whole world."""
    return Settings(_env_file=None, **kw)


class TestDevModeIsRefusedWhereItMatters:
    def test_dev_mode_on_the_dev_stage_is_fine(self):
        s = _settings(dev_mode=True, environment="dev",
                      database_url="postgresql://u:p@h/cohereny_dev")
        assert s.dev_mode is True

    def test_a_local_checkout_still_works(self):
        """A developer's database is often just called `cohereny`.

        The rule is a blocklist for that reason. Demanding a name ending in
        `_dev` would block legitimate local work, and a guard that blocks
        legitimate work gets switched off — which is how we got here.
        """
        s = _settings(dev_mode=True, environment="dev",
                      database_url="postgresql://u:p@localhost:5432/cohereny")
        assert s.dev_mode is True

    def test_dev_mode_without_declaring_the_environment_is_refused(self):
        """The default is `prod`, so silence is treated as production.

        This is the case that an env-var override produces: setting DEV_MODE=true
        on a running container without touching anything else.
        """
        with pytest.raises(ValidationError, match="ENVIRONMENT"):
            _settings(dev_mode=True,
                      database_url="postgresql://u:p@h/cohereny_dev")

    def test_dev_mode_on_the_prod_stage_is_refused(self):
        with pytest.raises(ValidationError, match="ENVIRONMENT"):
            _settings(dev_mode=True, environment="prod",
                      database_url="postgresql://u:p@h/cohereny_prod")

    @pytest.mark.parametrize("db", ["cohereny_prod", "cohereny_test"])
    def test_dev_mode_against_real_data_is_refused_whatever_it_claims(self, db):
        """environment='dev' and a production database: the copied-.env case.

        Both values came from the same file, so the first condition passes.
        The database is the independent witness.
        """
        with pytest.raises(ValidationError, match=db):
            _settings(dev_mode=True, environment="dev",
                      database_url=f"postgresql://u:p@h/{db}")

    def test_a_query_string_does_not_hide_the_database_name(self):
        with pytest.raises(ValidationError, match="cohereny_prod"):
            _settings(dev_mode=True, environment="dev",
                      database_url="postgresql://u:p@h/cohereny_prod?sslmode=require")


class TestTheGuardStaysOutOfTheWay:
    def test_production_without_dev_mode_starts(self):
        s = _settings(dev_mode=False, environment="prod",
                      database_url="postgresql://u:p@h/cohereny_prod")
        assert s.environment == "prod"

    def test_test_stage_without_dev_mode_starts(self):
        s = _settings(dev_mode=False, environment="test",
                      database_url="postgresql://u:p@h/cohereny_test")
        assert s.dev_mode is False

    def test_the_default_environment_is_the_strict_one(self):
        """A deployment that forgot to say gets treated as production.

        The cost of being wrong is asymmetric: guessing `dev` and being wrong
        means no authentication in production; guessing `prod` and being wrong
        means a developer sees an error message.
        """
        assert _settings().environment == "prod"

    def test_the_default_has_dev_mode_off(self):
        assert _settings().dev_mode is False


class TestTheGuardIsReachable:
    def test_it_refuses_at_construction_not_at_first_request(self):
        """A misconfigured process must not start.

        Validating lazily would leave a window in which the service is up and
        answering without authentication — the exact state the guard exists to
        prevent.
        """
        with pytest.raises(ValidationError):
            _settings(dev_mode=True, environment="prod")
