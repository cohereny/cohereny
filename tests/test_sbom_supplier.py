"""
tests/test_sbom_supplier.py

Where `scripts/sbom_supplier.py` takes a component's supplier from (CHY-22).

Package metadata is filled in unevenly: some packages name a maintainer, most
only an author e-mail, LangChain's packages and orjson nothing but a GitHub URL.
The order below is the whole method; each case pins one step of it. The
metadata here is hand-built, so the rule is under test and not whatever
happens to be installed.
"""
from __future__ import annotations

from email.message import Message

import pytest

from scripts import sbom_supplier


def _meta(**fields: str | list[str]) -> Message:
    meta = Message()
    for key, value in fields.items():
        for item in value if isinstance(value, list) else [value]:
            meta[key.replace("_", "-")] = item
    return meta


def test_the_maintainer_comes_first():
    meta = _meta(Maintainer="The Maintainers", Author="Original Author")
    assert sbom_supplier.supplier_from(meta)["name"] == "The Maintainers"


def test_then_the_author():
    assert sbom_supplier.supplier_from(_meta(Author="Tom Christie"))["name"] == "Tom Christie"


def test_the_names_in_an_author_email_field():
    meta = _meta(Author_email='"Sebastián Ramírez" <tiangolo@example.com>, Other <o@example.com>')
    assert sbom_supplier.supplier_from(meta)["name"] == "Sebastián Ramírez, Other"


def test_an_address_without_a_name_is_used_as_it_is():
    assert sbom_supplier.supplier_from(_meta(Author_email="dev@example.com"))["name"] == "dev@example.com"


def test_unknown_counts_as_not_set():
    """Old setuptools wrote the literal UNKNOWN into empty fields."""
    meta = _meta(Author="UNKNOWN", Maintainer_email="m@example.com")
    assert sbom_supplier.supplier_from(meta)["name"] == "m@example.com"


@pytest.mark.parametrize("project_url, owner", [
    ("Source, https://github.com/langchain-ai/langgraph/tree/main/libs/checkpoint", "langchain-ai"),
    ("Repository, https://github.com/ijl/orjson", "ijl"),
    ("Source, https://gitlab.com/some-group/project", "some-group"),
])
def test_without_any_name_the_owner_of_the_source_repository(project_url, owner):
    supplier = sbom_supplier.supplier_from(_meta(Project_URL=project_url))
    assert supplier["name"] == owner
    assert supplier["url"][0].endswith(f"/{owner}")


def test_a_homepage_that_is_no_repository_names_nobody():
    meta = _meta(Project_URL="Homepage, https://docs.example.com/")
    assert sbom_supplier.supplier_from(meta) is None


def test_the_source_url_travels_with_a_named_supplier():
    meta = _meta(Author="Ann", Project_URL=["Documentation, https://docs.example.com",
                                            "Source, https://github.com/ann/pkg"])
    assert sbom_supplier.supplier_from(meta) == {"name": "Ann", "url": ["https://github.com/ann/pkg"]}


def test_enrich_fills_root_and_components_and_reports_the_rest():
    sbom = {
        "metadata": {"component": {"name": "cohereny-api"}},
        "components": [
            {"name": "PyYAML"},                                      # found under its normal name
            {"name": "kept", "supplier": {"name": "Already Set"}},   # left alone
            {"name": "nameless"},                                    # metadata without a supplier
            {"name": "absent"},                                      # not installed at all
        ],
    }
    metadata = {
        "cohereny-api": _meta(Author="Thomas Roth"),
        "pyyaml": _meta(Author="Kirill Simonov"),
        "nameless": _meta(Summary="no author, no url"),
    }
    assert sbom_supplier.enrich(sbom, metadata) == ["nameless", "absent"]
    assert sbom["metadata"]["component"]["supplier"] == {"name": "Thomas Roth"}
    assert sbom["components"][0]["supplier"] == {"name": "Kirill Simonov"}
    assert sbom["components"][1]["supplier"] == {"name": "Already Set"}
    assert "supplier" not in sbom["components"][2]
