#!/usr/bin/env python3
"""
scripts/check_sbom.py

Does the bill of materials list what the product depends on (CHY-22)?

The `sbom` job of the development pipeline installs the product into a fresh
virtual environment and lets cyclonedx-py describe that environment. The
generator reports whatever it finds: pointed at the wrong interpreter, or at an
install that half failed, it still writes a valid CycloneDX file, and its own
schema check passes it. So this script holds the SBOM against the declarations
instead:

  1. Every top-level dependency of every installed package is a component with
     a version and a pkg:pypi purl naming that version. The CRA asks for at
     least these (Annex I Part II (1)).
  2. Every installed package is in it: the first as the root component, the
     others (the pilots) as components. They are not on PyPI and carry no purl,
     so for them name and version are enough.
  3. The generator is not. cyclonedx-bom runs from a venv of its own; installed
     next to the product, its dependencies would be listed as ours.
  4. Every component, the root included, names a supplier -- an NTIA minimum
     element that cyclonedx-py leaves empty and scripts/sbom_supplier.py fills
     in from the package metadata.

With --schema it also validates the file against the CycloneDX 1.6 schema,
because sbom_supplier.py rewrites what the generator had validated. That needs
cyclonedx-python-lib, so run it with the generator's interpreter; without the
library --schema stops with exit 2 instead of quietly skipping the check.

What it does not establish: that these are the versions in a deployed image.
Runtime dependencies float (`>=`) and there is no lock file, so an image
resolves at its own build. The SBOM describes the resolution at pipeline time
(SECURITY.md, "Software bill of materials").

Environment markers are not evaluated. A dependency whose marker excludes the
CI interpreter would be reported missing -- the loud direction.

Usage:
    python scripts/check_sbom.py [--schema] sbom.cdx.json . ./pilots/study ./pilots/banking

The package arguments are the directories pip installed; the first one is the
root. Exit code 1 when anything is missing, 2 when --schema cannot run.
"""
from __future__ import annotations

import argparse
import importlib
import json
import re
import sys
import tomllib
from pathlib import Path

#: Distributions that belong to the generator, not to the product.
GENERATOR = frozenset({"cyclonedx-bom", "cyclonedx-python-lib"})

# The distribution name at the start of a requirement string, before any
# extras, version specifier, URL or marker: "uvicorn[standard]>=0.30" -> uvicorn.
_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")


def normalize(name: str) -> str:
    """PEP 503: PyYAML, pyyaml and py_yaml name the same distribution."""
    return re.sub(r"[-_.]+", "-", name).lower()


def declared(package_dir: Path) -> tuple[str, list[str]]:
    """(package name, its top-level runtime dependencies), both normalised."""
    pyproject = package_dir / "pyproject.toml"
    project = tomllib.loads(pyproject.read_text())["project"]
    deps = []
    for requirement in project.get("dependencies", []):
        match = _NAME.match(requirement)
        if not match:
            msg = f"{pyproject}: cannot read the name in {requirement!r}"
            raise ValueError(msg)
        deps.append(normalize(match.group(1)))
    return normalize(project["name"]), deps


def _purl_names(component: dict) -> tuple[str, str] | None:
    """(name, version) from a pkg:pypi purl, or None if it is not one."""
    purl = component.get("purl", "")
    if not purl.startswith("pkg:pypi/"):
        return None
    name, _, version = purl.removeprefix("pkg:pypi/").partition("@")
    # Qualifiers (?file_name=…) and subpaths (#…) are not part of the version.
    return normalize(name), re.split(r"[?#]", version, maxsplit=1)[0]


def _package_problem(name: str, component: dict | None) -> str | None:
    if component is None:
        return f"{name}: installed package missing"
    if not component.get("version"):
        return f"{name}: installed package without a version"
    return None


def _dependency_problem(dep: str, component: dict | None) -> str | None:
    if component is None:
        return f"{dep}: top-level dependency missing"
    version = component.get("version", "")
    if not version:
        return f"{dep}: no version"
    if _purl_names(component) != (dep, version):
        return f"{dep}: purl {component.get('purl', '')!r} does not name {dep}@{version}"
    return None


def problems(sbom: dict, packages: list[tuple[str, list[str]]]) -> list[str]:
    """Everything the SBOM fails to say about the packages. Empty means it holds."""
    components = {normalize(c.get("name", "")): c for c in sbom.get("components", [])}
    found: list[str | None] = []

    root_name, _ = packages[0]
    root = normalize(sbom.get("metadata", {}).get("component", {}).get("name", ""))
    if root != root_name:
        found.append(f"root component is {root or 'missing'!r}, expected {root_name!r}")

    found += [_package_problem(name, components.get(name)) for name, _ in packages[1:]]

    # Root and pilots share dependencies; each is reported once.
    deps = dict.fromkeys(d for _, ds in packages for d in ds)
    found += [_dependency_problem(dep, components.get(dep)) for dep in deps]

    found += [f"{name}: the generator is listed as part of the product"
              for name in sorted(GENERATOR & components.keys())]

    found += _supplier_problems(sbom)

    return [line for line in found if line]


def _supplier_problems(sbom: dict) -> list[str]:
    root = sbom.get("metadata", {}).get("component", {})
    return [f"{c.get('name', '?')}: no supplier"
            for c in [root, *sbom.get("components", [])]
            if not (c.get("supplier") or {}).get("name")]


def schema_problems(text: str) -> list[str]:
    """Validation against the CycloneDX 1.6 JSON schema; needs cyclonedx-python-lib."""
    # Looked up at call time, not imported at the top: only the generator's venv
    # has the library, and every other caller of this script runs without it.
    schema = importlib.import_module("cyclonedx.schema")
    validation = importlib.import_module("cyclonedx.validation.json")

    error = validation.JsonStrictValidator(schema.SchemaVersion.V1_6).validate_str(text)
    return [f"schema: {str(error).splitlines()[0]}"] if error else []


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--schema", action="store_true",
                    help="Also validate against the CycloneDX 1.6 schema (needs cyclonedx-python-lib)")
    ap.add_argument("sbom", type=Path, help="CycloneDX JSON written by cyclonedx-py")
    ap.add_argument("packages", type=Path, nargs="+",
                    help="Directories pip installed, root first")
    args = ap.parse_args(argv)

    text = args.sbom.read_text()
    sbom = json.loads(text)
    packages = [declared(p) for p in args.packages]
    found = problems(sbom, packages)
    if args.schema:
        try:
            found += schema_problems(text)
        except ImportError:
            print("--schema needs cyclonedx-python-lib; run this with the generator's "
                  "interpreter.", file=sys.stderr)
            return 2

    if found:
        print(f"SBOM incomplete -- {len(found)} problem(s):", file=sys.stderr)
        for line in found:
            print(f"  {line}", file=sys.stderr)
        return 1

    top_level = len({d for _, deps in packages for d in deps})
    print(f"SBOM holds: {len(sbom.get('components', []))} components with supplier, "
          f"{top_level} top-level dependencies of {len(packages)} packages, "
          f"generator not included{', schema valid' if args.schema else ''}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
