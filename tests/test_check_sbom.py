"""
tests/test_check_sbom.py

What `scripts/check_sbom.py` lets through (CHY-22).

The `sbom` CI job is only worth something if an SBOM that leaves out a
dependency fails it. cyclonedx-py does not: it describes whatever environment it
is pointed at, and an empty or wrong one is still valid CycloneDX. So every
rule below has its failing case next to the passing one -- a check that was
never seen to fail could be passing for the wrong reason.

The SBOMs here are hand-built and minimal. What is under test is the
comparison, not the generator.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from scripts import check_sbom

REPO = Path(__file__).resolve().parents[1]


def _package(tmp_path: Path, directory: str, name: str, deps: list[str]) -> Path:
    path = tmp_path / directory
    path.mkdir(parents=True)
    lines = "".join(f'    "{d}",\n' for d in deps)
    (path / "pyproject.toml").write_text(
        f'[project]\nname = "{name}"\nversion = "0.1.0"\ndependencies = [\n{lines}]\n'
    )
    return path


@pytest.fixture
def packages(tmp_path):
    """A root with extras and a mixed-case name, and one pilot -- like the real three."""
    return [
        _package(tmp_path, "root", "cohereny-api",
                 ["fastapi>=0.115", "uvicorn[standard]>=0.30", "PyYAML>=6"]),
        _package(tmp_path, "pilot", "study-workflow", ["psycopg[binary]>=3.1"]),
    ]


_ROTH = {"name": "Thomas Roth"}

GOOD = {
    "bomFormat": "CycloneDX",
    "metadata": {"component": {"name": "cohereny-api", "version": "0.1.0", "supplier": _ROTH}},
    "components": [
        {"name": "fastapi", "version": "0.162.0", "purl": "pkg:pypi/fastapi@0.162.0",
         "supplier": {"name": "Sebastián Ramírez"}},
        {"name": "uvicorn", "version": "0.54.0", "purl": "pkg:pypi/uvicorn@0.54.0",
         "supplier": {"name": "Tom Christie"}},
        # Spelled as the distribution spells itself; the purl is lower case.
        {"name": "PyYAML", "version": "6.0.3", "purl": "pkg:pypi/pyyaml@6.0.3",
         "supplier": {"name": "Kirill Simonov"}},
        {"name": "psycopg", "version": "3.3.6", "purl": "pkg:pypi/psycopg@3.3.6",
         "supplier": {"name": "Daniele Varrazzo"}},
        # A local package: not on PyPI, so no purl.
        {"name": "study-workflow", "version": "0.1.0", "supplier": _ROTH},
    ],
}


def _check(sbom: dict, packages: list[Path]) -> list[str]:
    declared = [check_sbom.declared(p) for p in packages]
    return check_sbom.problems(sbom, declared)


def _without(name: str) -> dict:
    sbom = copy.deepcopy(GOOD)
    sbom["components"] = [c for c in sbom["components"] if c["name"] != name]
    return sbom


def _changed(name: str, **fields) -> dict:
    sbom = copy.deepcopy(GOOD)
    for component in sbom["components"]:
        if component["name"] == name:
            component.update(fields)
    return sbom


def test_a_complete_sbom_holds(packages):
    assert _check(GOOD, packages) == []


def test_a_missing_top_level_dependency_fails(packages):
    assert _check(_without("fastapi"), packages) == [
        "fastapi: top-level dependency missing"
    ]


def test_a_dependency_of_a_pilot_counts_as_well(packages):
    """The pilots are installed into the same image; their dependencies ship too."""
    assert _check(_without("psycopg"), packages) == [
        "psycopg: top-level dependency missing"
    ]


def test_a_component_without_a_version_fails(packages):
    assert _check(_changed("fastapi", version=""), packages) == ["fastapi: no version"]


@pytest.mark.parametrize("purl", [
    "",                                   # none at all
    "pkg:pypi/fastapi@0.115.0",           # another version than the component's
    "pkg:pypi/starlette@0.162.0",         # another distribution
    "pkg:npm/fastapi@0.162.0",            # another ecosystem
])
def test_a_purl_that_does_not_name_the_component_fails(packages, purl):
    found = _check(_changed("fastapi", purl=purl), packages)
    assert len(found) == 1 and found[0].startswith("fastapi: purl")


def test_purl_qualifiers_are_not_part_of_the_version(packages):
    sbom = _changed("fastapi", purl="pkg:pypi/fastapi@0.162.0?file_name=fastapi-0.162.0.whl")
    assert _check(sbom, packages) == []


def test_a_missing_pilot_fails(packages):
    assert _check(_without("study-workflow"), packages) == [
        "study-workflow: installed package missing"
    ]


def test_a_wrong_root_fails(packages):
    sbom = copy.deepcopy(GOOD)
    sbom["metadata"]["component"]["name"] = "something-else"
    assert _check(sbom, packages) == [
        "root component is 'something-else', expected 'cohereny-api'"
    ]


@pytest.mark.parametrize("name", ["cyclonedx-bom", "cyclonedx_python_lib"])
def test_the_generator_inside_the_product_fails(packages, name):
    """One venv for product and generator would list the generator's tree as ours."""
    sbom = copy.deepcopy(GOOD)
    sbom["components"].append({"name": name, "version": "7.6.0",
                               "purl": f"pkg:pypi/{name}@7.6.0",
                               "supplier": {"name": "CycloneDX"}})
    assert _check(sbom, packages) == [
        f"{check_sbom.normalize(name)}: the generator is listed as part of the product"
    ]


@pytest.mark.parametrize("supplier", [None, {}, {"name": ""}, {"url": ["https://x"]}])
def test_a_component_without_a_supplier_fails(packages, supplier):
    """An NTIA minimum element; cyclonedx-py leaves it empty, sbom_supplier.py fills it."""
    sbom = copy.deepcopy(GOOD)
    if supplier is None:
        del sbom["components"][0]["supplier"]
    else:
        sbom["components"][0]["supplier"] = supplier
    assert _check(sbom, packages) == ["fastapi: no supplier"]


def test_the_root_needs_a_supplier_as_well(packages):
    sbom = copy.deepcopy(GOOD)
    del sbom["metadata"]["component"]["supplier"]
    assert _check(sbom, packages) == ["cohereny-api: no supplier"]


def test_schema_without_the_library_stops_instead_of_skipping(packages, tmp_path,
                                                              monkeypatch, capsys):
    """--schema run with an interpreter that lacks cyclonedx-python-lib is exit 2.

    Passing quietly would be a check that is green because it never ran.
    """
    def unavailable(_text):
        raise ImportError("No module named 'cyclonedx'")

    monkeypatch.setattr(check_sbom, "schema_problems", unavailable)
    good = tmp_path / "good.json"
    good.write_text(json.dumps(GOOD))
    assert check_sbom.main(["--schema", str(good), *map(str, packages)]) == 2
    assert "cyclonedx-python-lib" in capsys.readouterr().err


@pytest.mark.parametrize("spelled, normal", [
    ("PyYAML", "pyyaml"),
    ("typing_extensions", "typing-extensions"),
    ("zope.interface", "zope-interface"),
    ("Foo__Bar", "foo-bar"),
])
def test_names_compare_as_pep_503_normalises_them(spelled, normal):
    assert check_sbom.normalize(spelled) == normal


def test_the_exit_code_follows_the_finding(packages, tmp_path, capsys):
    good, bad = tmp_path / "good.json", tmp_path / "bad.json"
    good.write_text(json.dumps(GOOD))
    bad.write_text(json.dumps(_without("fastapi")))
    args = [str(p) for p in packages]

    assert check_sbom.main([str(good), *args]) == 0
    assert check_sbom.main([str(bad), *args]) == 1
    assert "fastapi: top-level dependency missing" in capsys.readouterr().err


def test_the_repositorys_own_declarations_can_be_read():
    """The three directories the Dockerfile installs, as the CI job names them."""
    root, study, banking = (check_sbom.declared(REPO / d)
                            for d in (".", "pilots/study", "pilots/banking"))
    assert root[0] == "cohereny-api" and "fastapi" in root[1]
    assert study[0] == "study-workflow" and "langgraph" in study[1]
    assert banking[0] == "banking-workflow" and "langgraph" in banking[1]
