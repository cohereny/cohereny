"""Defaults may not be the unencrypted option (CHY-96).

A default is what applies when nobody thought about it. `http://` as a default
means the safe choice needs a decision and the unsafe one needs none — exactly
backwards.

The finding that prompted this: OIDC_ISSUER pointed at the identity provider
over plain http:// in config.py and all four .env examples.
The issuer is where the JWKS comes from, so every accepted token was only as
trustworthy as the channel that delivered the signing key.

It had made no practical difference, which is its own story: the provider does
not exist in Authentik (CHY-79/INFRA-92), both schemes answer 404, and nobody
noticed because DEV_MODE=true skipped validation on dev (CHY-91). Three defects
propping each other up — the scheme was simply never reached.
"""
from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent

#: Loopback and container-internal names are not exposed and have no TLS to
#: offer. Listing them is deliberate: a wildcard would let the next real host
#: through under the guise of being local.
LOCAL = ("localhost", "127.0.0.1", "0.0.0.0", "host.docker.internal")


def _strip_comments(text: str) -> str:
    """Everything after an unquoted # is prose, and prose may quote the bad form.

    Written after the first version of this file failed on its own explanatory
    comment — the fourth time in one afternoon that a check read prose as code.
    One helper, used by every test here, rather than remembering per test.
    """
    return "\n".join(line.split("#", 1)[0] for line in text.splitlines())


def _http_urls(text: str) -> list[str]:
    return [
        u for u in re.findall(r"http://[^\s\"'<>)]+", _strip_comments(text))
        if not any(h in u for h in LOCAL)
    ]


def test_no_http_default_in_the_config_module():
    found = _http_urls((ROOT / "api" / "config.py").read_text(encoding="utf-8"))
    assert not found, f"unencrypted defaults: {found}"


def test_no_http_in_the_env_examples():
    """The examples are copied into real .env files. A bad example ships."""
    offenders: dict[str, list[str]] = {}
    for p in ROOT.glob(".env*.example"):
        found = _http_urls(p.read_text(encoding="utf-8"))
        if found:
            offenders[p.name] = found
    assert not offenders, f"unencrypted URLs in examples: {offenders}"


def test_no_http_anywhere_in_production_code():
    offenders: dict[str, list[str]] = {}
    for layer in ("api", "core", "domain", "router", "cli"):
        for p in (ROOT / layer).rglob("*.py"):
            found = _http_urls(p.read_text(encoding="utf-8"))
            if found:
                offenders[str(p.relative_to(ROOT))] = found
    assert not offenders, f"unencrypted URLs: {offenders}"
