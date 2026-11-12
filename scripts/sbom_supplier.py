#!/usr/bin/env python3
"""
scripts/sbom_supplier.py

Fill in the supplier of every component in a CycloneDX SBOM (CHY-22).

cyclonedx-py lists name, version, purl and license, but leaves `supplier` empty
for all of them -- on 02.11.2026, 0 of 96 components had one. The supplier is
one of the NTIA minimum elements, and the package metadata already carries it.
This script reads it from the environment the SBOM describes, so it must run
with that environment's interpreter:

    /tmp/product/bin/python scripts/sbom_supplier.py sbom.cdx.json

The supplier is taken from the first of these that is set:

  1. Maintainer, 2. Author                      -- the names as written
  3. Maintainer-email, 4. Author-email          -- the names in them, else the addresses
  5. the owner of the source repository         -- github.com/<owner>/... in Project-URL

On 02.11.2026, 84 of 97 distributions had 1-4; LangChain's packages and orjson
declare no author at all, but every one of them names its GitHub repository.
A component for which none of the five gives anything keeps no supplier, and
check_sbom.py reports it.

Standard library only: the product environment has no CycloneDX library, and
this must not add one to it.
"""
from __future__ import annotations

import argparse
import importlib.metadata
import json
import re
import sys
from email.message import Message
from email.utils import getaddresses
from pathlib import Path

_REPO = re.compile(r"https?://(?:www\.)?(github\.com|gitlab\.com|codeberg\.org)/([^/\s]+)/", re.I)
#: Project-URL labels that point at the code, in order of preference.
_SOURCE_LABELS = ("source", "repository", "source code", "code", "homepage")


def _normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _field(meta: Message, key: str) -> str:
    value = (meta.get(key) or "").strip()
    return "" if value.upper() == "UNKNOWN" else value


def _names_in(addresses: str) -> str:
    pairs = getaddresses([addresses])
    names = [name for name, _ in pairs if name]
    return ", ".join(names or [addr for _, addr in pairs if addr])


def _source_urls(meta: Message) -> list[str]:
    labelled = {}
    for entry in meta.get_all("Project-URL") or []:
        label, _, url = entry.partition(",")
        labelled.setdefault(label.strip().lower(), url.strip())
    urls = [labelled[k] for k in _SOURCE_LABELS if k in labelled]
    if _field(meta, "Home-page"):
        urls.append(_field(meta, "Home-page"))
    return urls


def supplier_from(meta: Message) -> dict | None:
    """The CycloneDX supplier for one distribution's metadata, or None."""
    urls = _source_urls(meta)
    for key in ("Maintainer", "Author"):
        if name := _field(meta, key):
            return {"name": name, **({"url": urls[:1]} if urls else {})}
    for key in ("Maintainer-email", "Author-email"):
        if name := _names_in(_field(meta, key)):
            return {"name": name, **({"url": urls[:1]} if urls else {})}
    for url in urls:
        if match := _REPO.match(url):
            host, owner = match.groups()
            return {"name": owner, "url": [f"https://{host.lower()}/{owner}"]}
    return None


def installed() -> dict[str, Message]:
    """Metadata of every distribution in the running interpreter, by normalised name."""
    return {_normalize(d.metadata["Name"]): d.metadata for d in importlib.metadata.distributions()}


def enrich(sbom: dict, metadata: dict[str, Message]) -> list[str]:
    """Set `supplier` where it is missing. Returns the names still without one."""
    targets = [sbom.get("metadata", {}).get("component", {}), *sbom.get("components", [])]
    missing = []
    for component in targets:
        if component.get("supplier"):
            continue
        meta = metadata.get(_normalize(component.get("name", "")))
        supplier = supplier_from(meta) if meta is not None else None
        if supplier:
            component["supplier"] = supplier
        else:
            missing.append(component.get("name", "?"))
    return missing


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("sbom", type=Path, help="CycloneDX JSON, rewritten in place")
    args = ap.parse_args(argv)

    sbom = json.loads(args.sbom.read_text())
    missing = enrich(sbom, installed())
    args.sbom.write_text(json.dumps(sbom, indent=2, ensure_ascii=False) + "\n")

    total = 1 + len(sbom.get("components", []))
    print(f"Supplier set for {total - len(missing)} of {total} components.")
    for name in missing:
        print(f"  no supplier found: {name}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
