#!/usr/bin/env python3
"""
scripts/dev_issuer.py

A local stand-in for the identity provider, for development and CI (CHY-192).

Why this exists:
  Until CHY-192 a DEV_MODE switch let the API skip token validation and answer
  every request as a static admin. It was meant for working without Authentik,
  and it worked by switching off the one thing every other stage depends on:
  the token check, the audiences, the surface gate. Whatever ran on dev had
  never met a token. CHY-91 fenced the switch in, and it still hid three broken
  issuer configurations on dev for weeks.

  This replaces the switch with the thing it was standing in for. The API runs
  exactly as in production; only the issuer is local. Tokens are RS256, carry
  the eight issuers and audiences of ADR-031 and INFRA-122, and go through the
  same require_auth, the same gate and the same role mapping as Authentik's.

What it imitates, and nothing more:
  - JWKS at  <base>/<slug>/jwks/  for each of the eight applications — where
    api/deps.py looks for them (`jwks_uri == iss + "jwks/"`)
  - the token endpoint  <base>/token/  in Authentik's client_credentials shape:
    form fields grant_type, client_id, username, password. That is what
    scripts/e2e_context_lifecycle.py sends, so it runs unchanged against both

  Not imitated: the authorization code flow, discovery, refresh tokens. The UI
  logs in through them (CHYUI-9); a local UI needs them and that is CHY-189's
  decision, not a guess made here.

Who gets which token:
  The username names the role: consumer, contributor, reviewer, architect or
  admin; the token carries the matching group (kernel/roles.py).
  `cohereny-ci` gets architect, like the reference installation's CI account,
  so the e2e script needs no special case. Each username is its own actor —
  sub is stable per name, so two names are two actor_ids.

  The password is NOT checked. Anyone who can reach this server gets any token,
  admin included. That is why it listens on 127.0.0.1 unless told otherwise,
  draws a fresh key on every start (no token outlives the process), and why the
  API refuses an http issuer unless ENVIRONMENT=dev (api/config.py). Never put
  it where a deployment that holds real data could trust it.

Usage:
    python scripts/dev_issuer.py                  # http://127.0.0.1:9010
    python scripts/dev_issuer.py --host 0.0.0.0 --public-url http://issuer:9010  # in Compose
    ENVIRONMENT=dev OIDC_BASE_URL=http://127.0.0.1:9010/application/o \\
        uvicorn api.main:app

    curl -s http://127.0.0.1:9010/application/o/token/ \\
        -d grant_type=client_credentials -d client_id=cohereny.author.ci \\
        -d username=admin -d password=x

Standard library plus PyJWT[crypto], which the API depends on anyway.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kernel.roles import GROUP_ROLE_MAP
from kernel.surfaces import AUDIENCES, CI_AUDIENCES, CI_SLUGS, SLUGS

#: Path prefix, as with Authentik. OIDC_BASE_URL is http://<host>:<port> + this.
PREFIX = "/application/o"

#: Authentik issues access tokens for ten minutes (INFRA-160); so does this, so
#: a client that forgets to renew fails here and not first in production.
DEFAULT_TTL_S = 600

#: Username → group: each role of kernel/roles.py under its own name, derived
#: from the mapping the API applies, so the two cannot disagree. cohereny-ci
#: mirrors the reference installation's CI account (INFRA-122).
GROUPS: dict[str, str] = {role: group for group, role in GROUP_ROLE_MAP.items()}
GROUPS["cohereny-ci"] = GROUPS["architect"]

#: client_id → application slug. In Authentik the client_id of each
#: application is its audience (`cohereny.author`, `cohereny.author.ci`), and
#: the slug names its issuer.
CLIENTS: dict[str, str] = {
    **{AUDIENCES[s]: SLUGS[s] for s in AUDIENCES},
    **{CI_AUDIENCES[s]: CI_SLUGS[s] for s in CI_AUDIENCES},
}


class Issuer:
    """One signing key and the tokens it signs. A new key per instance."""

    def __init__(self, base_url: str, ttl_s: int = DEFAULT_TTL_S) -> None:
        self.base_url = base_url.rstrip("/")
        self.ttl_s = ttl_s
        self._key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.kid = uuid.uuid4().hex
        jwk = json.loads(RSAAlgorithm.to_jwk(self._key.public_key()))
        self.jwks = {"keys": [{**jwk, "kid": self.kid, "use": "sig", "alg": "RS256"}]}

    def issuer_for(self, slug: str) -> str:
        """The `iss` of one application, as api/deps.py expects it."""
        return f"{self.base_url}/{slug}/"

    def token(self, client_id: str, username: str) -> dict:
        """A token response, or ValueError naming what was wrong."""
        slug = CLIENTS.get(client_id)
        if slug is None:
            msg = f"unknown client_id {client_id!r}; known: {', '.join(sorted(CLIENTS))}"
            raise ValueError(msg)
        group = GROUPS.get(username)
        if group is None:
            msg = f"unknown username {username!r}; known: {', '.join(GROUPS)}"
            raise ValueError(msg)
        now = int(time.time())
        claims = {
            "iss": self.issuer_for(slug),
            "aud": client_id,
            "sub": f"dev-issuer:{username}",
            "preferred_username": username,
            "groups": [group],
            "iat": now,
            "nbf": now,
            "exp": now + self.ttl_s,
        }
        token = jwt.encode(claims, self._key, algorithm="RS256", headers={"kid": self.kid})
        return {"access_token": token, "token_type": "Bearer", "expires_in": self.ttl_s}


def handler_for(issuer: Issuer) -> type[BaseHTTPRequestHandler]:
    """The request handler, bound to one issuer."""
    jwks_paths = {f"{PREFIX}/{slug}/jwks/" for slug in CLIENTS.values()}

    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, body: dict) -> None:
            data = json.dumps(body).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:
            if self.path in jwks_paths:
                self._send(200, issuer.jwks)
            else:
                self._send(404, {"error": "not_found", "error_description": self.path})

        def do_POST(self) -> None:
            if self.path != f"{PREFIX}/token/":
                self._send(404, {"error": "not_found", "error_description": self.path})
                return
            length = int(self.headers.get("Content-Length") or 0)
            form = {k: v[0] for k, v in parse_qs(self.rfile.read(length).decode("utf-8")).items()}
            if form.get("grant_type") != "client_credentials":
                self._send(400, {"error": "unsupported_grant_type",
                                 "error_description": "only client_credentials"})
                return
            try:
                body = issuer.token(form.get("client_id", ""), form.get("username", ""))
            except ValueError as exc:
                # Authentik answers every refusal with a bare invalid_grant and
                # keeps the reason in its log. Locally there is nothing to hide.
                self._send(400, {"error": "invalid_grant", "error_description": str(exc)})
                return
            self._send(200, body)

        def log_message(self, _format: str, *args) -> None:
            # Method, path and status only. The default line would do no harm
            # today, but a token must never reach a log, so the format is
            # pinned rather than inherited.
            status = args[1] if len(args) > 1 else ""
            sys.stderr.write(f"dev_issuer: {self.command} {self.path} -> {status}\n")

    return Handler


def serve(host: str, port: int, ttl_s: int = DEFAULT_TTL_S,
          public_url: str | None = None) -> ThreadingHTTPServer:
    """A bound server for `host:port`; port 0 picks a free one. Not started.

    `public_url` is where the API reaches this server, and so what goes into
    `iss` (CHY-189). Without it that is the listening address — right on a
    developer's machine, wrong in a container: listening on 0.0.0.0 inside a
    Compose network, the issuer is reached as http://issuer:9010, and a token
    naming http://0.0.0.0:9010 would be refused by every API that trusts it.
    """
    server = ThreadingHTTPServer((host, port), BaseHTTPRequestHandler)
    bound = server.server_address[1]
    origin = public_url.rstrip("/") if public_url else f"http://{host}:{bound}"
    issuer = Issuer(f"{origin}{PREFIX}", ttl_s)
    server.RequestHandlerClass = handler_for(issuer)
    server.issuer = issuer  # type: ignore[attr-defined] -- for callers and tests
    return server


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[1])
    ap.add_argument("--host", default="127.0.0.1",
                    help="Address to listen on. Anything but loopback lets others mint tokens.")
    ap.add_argument("--port", type=int, default=9010)
    ap.add_argument("--ttl", type=int, default=DEFAULT_TTL_S, help="Token lifetime in seconds.")
    ap.add_argument("--public-url",
                    help="Where the API reaches this server, e.g. http://issuer:9010 in a "
                         "Compose network. Goes into `iss`; default: the listening address.")
    args = ap.parse_args()

    server = serve(args.host, args.port, args.ttl, args.public_url)
    base = server.issuer.base_url  # type: ignore[attr-defined]
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        print(f"WARNING: listening on {args.host} — anyone who reaches it gets an admin token.",
              file=sys.stderr)
    print(f"dev issuer for development only — passwords are not checked\n"
          f"  OIDC_BASE_URL={base}\n"
          f"  token endpoint: {base}/token/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
