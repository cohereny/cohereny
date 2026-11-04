"""
api/deps.py

FastAPI dependency injection: OIDC JWT Bearer validation.

Every authenticated endpoint receives a CurrentUser object via Depends(require_auth).
The flow:
  1. HTTPBearer extracts the token from the Authorization header
  2. The issuer is read from the unverified payload and checked against the
     known ones — each surface's UI client (INFRA-92) and machine client
     (INFRA-122). It decides which keys apply, so it has to be
     read before verification and constrained before it is used
  3. That issuer's JWKS are fetched from Authentik (cached 30 min, per issuer)
  4. RS256 signature is validated against the matching public key (kid matching)
  5. In the same decode: the audience against the surfaces' audiences (ADR-031),
     the issuer, expiry and not-before; exp, iss, aud and sub must be present
  6. On success: CurrentUser built from JWT claims

JWKS caching: prevents an HTTP request to the identity provider on every API call.
Double-checked locking (asyncio.Lock) prevents parallel fetches on the first
request or after cache expiry.

There is no way past this without a token. Until CHY-192 a DEV_MODE switch
short-circuited require_auth and answered every request as a static admin;
development now gets real tokens from scripts/dev_issuer.py instead.

Called by:
  - All route handlers in api/v1/*.py via Depends(require_auth)

Dependencies:
  - api.config    (settings.oidc_base_url)
  - kernel.surfaces (the four audiences and the four issuers)
  - PyJWT         (JWT decoding and signature verification; replaced python-jose,
                   CHY-202)
  - httpx         (async HTTP for JWKS fetch)
"""
import asyncio
import time
from dataclasses import dataclass

import httpx
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jwt import PyJWK

from api.config import settings
from kernel.surfaces import ALL_AUDIENCES, issuers

security = HTTPBearer(auto_error=False)


@dataclass
class CurrentUser:
    """Authenticated API caller, extracted from JWT claims.

    Built by require_auth and injected into route handlers via dependency injection.

    sub:      OIDC Subject — unique, immutable actor ID (UUID from Authentik)
    username: preferred_username from JWT, falls back to sub if not present
    groups:   group memberships from JWT, or None when the token carried no
              groups claim at all

    Why groups is Optional and not simply an empty list:
      "the IdP does not send groups" and "this person is in no group" are
      different facts with opposite consequences. The first means the mapping
      has nothing to say and a stored role must be left alone; the second is a
      deprovisioning signal and must take effect. Collapsing both to [] — as
      this did until CHY-33 — makes an offboarded admin indistinguishable from
      an unconfigured claim, and the safe reading of one is the unsafe reading
      of the other.
    """

    sub: str
    username: str
    groups: list[str] | None = None
    #: The `aud` claim this token carried. api/surfaces.py compares it against
    #: the surface being called (ADR-031) and refuses None: require_auth always
    #: sets it, so a caller without one was built somewhere else, and "no
    #: audience" must never read as "any audience" (CHY-192).
    audience: str | None = None


#: issuer URL → (document, fetched_at). Keyed by issuer since INFRA-92: each
#: surface is its own Authentik application with its own JWKS endpoint. They
#: share a signing certificate today, so one cache entry would in fact validate
#: all four — and would keep doing so silently right up until someone rotates
#: one of them.
_jwks_cache: dict[str, tuple[dict, float]] = {}
_jwks_lock = asyncio.Lock()
_JWKS_TTL = 1800.0  # 30 min


async def _fetch_jwks(issuer: str) -> dict:
    """Fetch the JWKS document for one issuer, cached for 30 minutes.

    Double-checked locking: check without lock first (fast path for 99% of
    requests), then check again with lock (prevents parallel fetches when
    several coroutines simultaneously consider the cache expired).

    `issuer` must already have been checked against the known set. It is used to
    build a URL, so an unchecked value here would let a token nominate where its
    own signing key comes from — which is no validation at all.
    """
    now = time.monotonic()
    cached = _jwks_cache.get(issuer)
    if cached is not None and (now - cached[1]) < _JWKS_TTL:
        return cached[0]

    async with _jwks_lock:
        cached = _jwks_cache.get(issuer)
        if cached is not None and (time.monotonic() - cached[1]) < _JWKS_TTL:
            return cached[0]

        # Authentik publishes it directly under the issuer, verified against the
        # discovery document of all four applications on 21.08.2026:
        #   jwks_uri == iss + "jwks/"
        jwks_url = issuer.rstrip("/") + "/jwks/"
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(jwks_url, timeout=10.0)
                resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"OIDC JWKS endpoint unreachable: {exc}",
            ) from exc

        document = resp.json()
        _jwks_cache[issuer] = (document, time.monotonic())
        return document


def _require_known_issuer(token: str) -> str:
    """The issuer named by the token, if it is one of ours.

    Read from the *unverified* payload, because the issuer decides which key
    verifies the signature — there is no way round reading it first. That is
    only safe because of the order: the value is checked against a fixed set
    here, and the signature is then verified against that issuer's keys. A token
    can therefore choose among four issuers and no others, and cannot choose one
    whose key it controls.
    """
    try:
        issuer = jwt.decode(token, options={"verify_signature": False}).get("iss")
    except jwt.InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token payload: {exc}",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    if issuer not in issuers(settings.oidc_base_url):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Token issuer {issuer!r} is not one of this service's.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return issuer


#: The audiences this service accepts: the four surfaces' UI clients and their
#: machine clients (INFRA-122), and nothing else. There used to be a
#: transitional entry for the old single audience `cohereny`; it was removed
#: once INFRA-92 showed that no token had ever carried it — the application of
#: that name never existed, so there was nothing to transition from.
#:
#: Checked in the decode itself. Until CHY-202 it was a separate step after it,
#: with `verify_aud` switched off, because python-jose took a single audience
#: and ADR-031 issues eight. The vocabulary comes from kernel/surfaces.py rather
#: than api/surfaces.py so that this module does not import the one that
#: imports it.
_AUDIENCES = sorted(ALL_AUDIENCES)

#: Claims a token must carry. Without `exp` a leaked token never expires;
#: without `sub` there is no actor — require_auth failed on it with a 500 before
#: CHY-202. `iss` and `aud` are checked anyway, but only when present unless
#: listed here.
_REQUIRED_CLAIMS = ["exp", "iss", "aud", "sub"]


def _validate_token(token: str, jwks: dict, issuer: str) -> dict:
    """Validate an RS256 JWT against the issuer's keys, matching by kid where possible.

    Only a signature that does not match moves on to the next key. Every other
    failure — expired, foreign audience, missing claim, an algorithm other than
    RS256 — belongs to the token, not to the key, and another key would not
    change it: the token is refused, not retried.

    `aud` may be a string or a list; the spec allows both, Authentik has used
    both, and a list passes when one of its entries is ours.
    """
    try:
        header = jwt.get_unverified_header(token)
    except jwt.InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token header: {exc}",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    kid = header.get("kid")
    keys = jwks.get("keys", [])
    candidates = [k for k in keys if k.get("kid") == kid] if kid else keys
    if not candidates:
        candidates = keys  # fallback: try all keys

    last_exc: jwt.PyJWTError | None = None
    for key_data in candidates:
        try:
            key = PyJWK(key_data, algorithm="RS256").key
            return jwt.decode(
                token,
                key,
                # Pinned, so the token's header cannot choose: no `none`, no
                # HS256 signed with the public key (tests/test_deps_tokens.py).
                algorithms=["RS256"],
                audience=_AUDIENCES,
                issuer=issuer,
                options={"require": _REQUIRED_CLAIMS},
            )
        except (jwt.InvalidSignatureError, jwt.PyJWKError, jwt.InvalidKeyError) as exc:
            last_exc = exc  # this key did not sign it, or is unusable; try the next
        except jwt.InvalidTokenError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Token validation failed: {exc}",
                headers={"WWW-Authenticate": "Bearer"},
            ) from exc

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=f"Token validation failed: {last_exc}",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def require_auth(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> CurrentUser:
    """FastAPI dependency: validates Bearer token and returns CurrentUser.

    Used via Depends(require_auth) in route parameters. Without a valid token
    the answer is 401, on every stage (CHY-192).
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Bearer token required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    issuer = _require_known_issuer(credentials.credentials)
    jwks = await _fetch_jwks(issuer)
    claims = _validate_token(credentials.credentials, jwks, issuer)

    aud = claims.get("aud")
    return CurrentUser(
        audience=(aud[0] if isinstance(aud, list) and aud else aud),
        sub=claims["sub"],
        username=claims.get("preferred_username", claims["sub"]),
        # No default: a missing claim must stay None. See CurrentUser.
        groups=claims.get("groups"),
    )
