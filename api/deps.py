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
  5. Audience is verified against the surfaces' audiences (ADR-031); `verify_aud`
     is off in the decode because python-jose takes a single string
  6. On success: CurrentUser built from JWT claims

JWKS caching: prevents an HTTP request to the identity provider on every API call.
Double-checked locking (asyncio.Lock) prevents parallel fetches on the first
request or after cache expiry.

Dev mode: when DEV_MODE=true, require_auth is short-circuited and returns a
static user — no token needed, no Authentik required. Only for .env.dev.

Called by:
  - All route handlers in api/v1/*.py via Depends(require_auth)

Dependencies:
  - api.config    (settings.oidc_base_url, dev_mode)
  - kernel.surfaces (the four audiences and the four issuers)
  - python-jose   (JWT decoding and signature verification)
  - httpx         (async HTTP for JWKS fetch)
"""
import asyncio
import time
from dataclasses import dataclass

import httpx
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt

from api.config import settings
from kernel.surfaces import ALL_AUDIENCES, issuers

security = HTTPBearer(auto_error=False)


@dataclass
class CurrentUser:
    """Authenticated API caller, extracted from JWT claims.

    Built by require_auth and injected into route handlers via dependency injection.
    In dev mode a static dummy user is returned.

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
    #: The `aud` claim this token carried, or None in dev mode where there is
    #: no token. api/surfaces.py compares it against the surface being called
    #: (ADR-031). None means "no token was presented", not "any audience".
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
        issuer = jwt.get_unverified_claims(token).get("iss")
    except JWTError as exc:
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


def _require_known_audience(claims: dict) -> None:
    """The token must name an audience this service issues.

    Split out and called unconditionally because `verify_aud` is switched off
    above: a claim that is not checked somewhere is a claim that is not checked.
    tests/test_surfaces.py removes this call and expects the suite to fail.

    `aud` may be a string or a list — the spec allows both, and Authentik has
    used both across versions.

    The vocabulary comes from kernel/surfaces.py rather than api/surfaces.py so
    that this module does not import the one that imports it.
    """
    # The four surfaces' UI clients and their machine clients (INFRA-122), and
    # nothing else. There used to be a transitional entry here for the old
    # single audience `cohereny`; it was removed once INFRA-92 showed that no
    # token had ever carried it — the application of that name never existed,
    # so there was nothing to transition from.
    known = ALL_AUDIENCES
    aud = claims.get("aud")
    presented = set(aud) if isinstance(aud, list) else {aud} if aud else set()

    if not presented & known:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Token audience {sorted(presented) or '(none)'} is not one of this service's.",
            headers={"WWW-Authenticate": "Bearer"},
        )


def _validate_token(token: str, jwks: dict, issuer: str) -> dict:
    """Validate RS256 JWT against JWKS keys, matching by kid where possible."""
    try:
        header = jwt.get_unverified_header(token)
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token header: {exc}",
            headers={"WWW-Authenticate": "Bearer"},
        )

    kid = header.get("kid")
    keys = jwks.get("keys", [])
    candidates = [k for k in keys if k.get("kid") == kid] if kid else keys
    if not candidates:
        candidates = keys  # fallback: try all keys

    last_exc: JWTError | None = None
    for key_data in candidates:
        try:
            claims = jwt.decode(
                token,
                key_data,
                algorithms=["RS256"],
                # Verified below against the set of known audiences instead.
                # python-jose takes a single string; ADR-031 issues four, one
                # per surface, plus the transitional one that Authentik will
                # stop using with INFRA-92.
                options={"verify_aud": False},
                issuer=issuer,
            )
        except JWTError as exc:
            last_exc = exc
        else:
            # In `else`, not in the `try`: this raises HTTPException, which is
            # not a JWTError and so would not be caught either way — but inside
            # the try it reads as if a failed audience check might fall through
            # to the next key. It must not; a token is refused, not retried.
            _require_known_audience(claims)
            return claims

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=f"Token validation failed: {last_exc}",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def require_auth(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> CurrentUser:
    """FastAPI dependency: validates Bearer token and returns CurrentUser.

    Used via Depends(require_auth) in route parameters.
    In dev mode (DEV_MODE=true) authentication is bypassed and a static admin
    user is returned — never use in prod or test.
    """
    if settings.dev_mode:
        # "chy-admins", not "admins": the group names are the ones the role
        # mapping actually knows (api/config.py). The old value matched nothing,
        # so the dev user would provision as a consumer and then fail every
        # admin-only endpoint — with no hint as to why.
        return CurrentUser(sub="dev-actor", username="dev", groups=["chy-admins"])

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
