"""The Docker image must contain every package the application imports.

**This test exists because the dev deployment was down for four days and
nothing said so.** `kernel/` and `contexts/` were created by CHY-101 on 24.08.
and never added to the Dockerfile. The image built without complaint,
`docker compose up -d` returned 0, and the container then crash-looped on
`ModuleNotFoundError: No module named 'kernel'`.

`deploy:dev` reported **success** through all of it. That is the same shape as
INFRA-63 (jobs reported success and did nothing) and as the coverage source
list of CHY-105 (a metric that measured less and called it an improvement):
something answers "fine" because it never looked.

Two lists have to agree — `[tool.hatch.build] packages` in pyproject.toml and
the COPY lines in the Dockerfile — and until now the only thing connecting them
was somebody remembering. That is not a mechanism.

Offline and cheap: this reads two files. It does not build the image; that
belongs in CI. What it catches is the case that actually happened — a new
top-level package that one list knows about and the other does not.
"""
from __future__ import annotations

import pathlib
import re
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

REPO = pathlib.Path(__file__).resolve().parent.parent
DOCKERFILE = REPO / "Dockerfile"
PYPROJECT = REPO / "pyproject.toml"


def _declared_packages() -> set[str]:
    """The packages pyproject.toml says belong to the distribution."""
    text = PYPROJECT.read_text(encoding="utf-8")
    m = re.search(r"^packages = \[(.*?)\]", text, re.M | re.S)
    assert m, "no `packages = [...]` in pyproject.toml — has the build backend changed?"
    return {p.strip().strip('"').split("/")[0] for p in m.group(1).split(",") if p.strip()}


def _copied_packages() -> set[str]:
    """The top-level directories the image actually receives."""
    return set(re.findall(r"^COPY ([a-z_]+)/ \./", DOCKERFILE.read_text(encoding="utf-8"), re.M))


def _imported_top_level() -> set[str]:
    """First-party packages imported anywhere under api/ — what the app needs to start."""
    import ast

    first_party = {p.name for p in REPO.iterdir() if p.is_dir() and (p / "__init__.py").exists()}
    found = set()
    for path in (REPO / "api").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                found |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                found.add(node.module.split(".")[0])
    return found & first_party


def test_every_declared_package_reaches_the_image():
    missing = _declared_packages() - _copied_packages()
    assert not missing, (
        f"pyproject declares {sorted(missing)} but the Dockerfile never copies them. "
        f"The image builds, the container crash-loops on ModuleNotFoundError, and "
        f"deploy reports success — exactly what happened between 24. and 28.08."
    )


def test_everything_api_imports_reaches_the_image():
    """The stronger check: not what is declared, but what is actually imported.

    A package could be missing from *both* lists and still be needed — that is
    the version of this failure that the test above would not catch.
    """
    missing = _imported_top_level() - _copied_packages()
    assert not missing, f"api/ imports {sorted(missing)}, which the image does not contain"


def test_the_dockerfile_copies_nothing_that_no_longer_exists():
    """CHY-121 removed `domain/`; the Dockerfile kept `COPY domain/`.

    From then on the build itself failed — which is the *better* failure, because
    it is loud. This test makes it louder still, and before the pipeline.
    """
    ghosts = [p for p in _copied_packages() if not (REPO / p).is_dir()]
    assert not ghosts, f"the Dockerfile copies {ghosts}, which do not exist"


@pytest.mark.parametrize("pkg", sorted(_declared_packages()))
def test_each_declared_package_exists_on_disk(pkg):
    assert (REPO / pkg).is_dir(), f"pyproject declares {pkg}, which is not a directory"


def test_a_plain_build_is_the_production_image():
    """The quickstart stage (CHY-189) must never be what a deploy builds.

    Docker builds the last stage unless told otherwise, and every deploy job
    builds without a target. So the file has to end in a stage that is `app`
    and adds nothing — otherwise the development issuer, which hands a token to
    anyone, would ship in the production image.
    """
    stages = re.findall(r"^FROM (\S+)(?: AS (\S+))?$", DOCKERFILE.read_text(encoding="utf-8"), re.M)
    assert stages[-1] == ("app", ""), stages
    last = DOCKERFILE.read_text(encoding="utf-8").rsplit("\nFROM app\n", 1)[1]
    assert not re.search(r"^(COPY|RUN|ADD)\b", last, re.M), "the final stage adds something"
    assert ("app", "quickstart") in stages


# ── No credentials in the image (CHY-85) ───────────────────────────────────────
#
# Until 06.11.2026 the deploy copied .env.<stage> into pilots/banking/ before
# building, and `COPY pilots/banking/` put it into every API image — the
# superuser's password for the database sat in the one container role separation
# exists to keep it out of. Two locks: the deploy no longer copies, and
# .dockerignore keeps environment files out of the build context altogether.

def _ignored_patterns() -> list[str]:
    lines = (REPO / ".dockerignore").read_text(encoding="utf-8").splitlines()
    return [ln.strip() for ln in lines if ln.strip() and not ln.startswith("#")]


def test_environment_files_never_reach_the_build_context():
    patterns = _ignored_patterns()
    assert "**/.env" in patterns
    assert "**/.env.*" in patterns


def test_the_example_files_still_do():
    """Counter-probe: the exclusion is narrow — an example holds no secret, and
    an ignore file that excluded everything would pass the test above too."""
    assert "!**/.env.example" in _ignored_patterns()


def test_no_deploy_step_copies_an_environment_file_into_the_source_tree():
    ci = (REPO / ".gitlab-ci.yml").read_text(encoding="utf-8")
    assert not re.search(r"\bcp\s+\.env\S*\s+\S*pilots/", ci), (
        "a deploy step copies an .env file into pilots/ — it would be built into "
        "the image along with the credentials it holds"
    )
