"""
tests/test_deps_tokens.py

The token check lets in what it must and turns away the rest (CHY-93).

Codebase audit 14.08.2026, finding A5: the code that decides whether a token is
valid had 44 % coverage and had never run against a signed token. Each case here
is a real signed token, and each goes through require_auth — the dependency every
route uses — so a check that is present but not reached fails here rather than
passing unnoticed.

The tokens come from tests/signed_tokens.py, built from `cryptography` alone, not
from the JWT library the verifier uses. That library is being replaced (CHY-202);
the same tests have to pass before and after.
"""
from __future__ import annotations

import time
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from api import deps
from api.config import settings
from api.deps import require_auth
from api.surfaces import AUDIENCES
from kernel.surfaces import issuers
from tests.signed_tokens import jwk_of, new_key, public_pem, sign

_KEY = new_key()
_OTHER_KEY = new_key()
_JWKS = {"keys": [jwk_of(_KEY)]}
_ISS = next(i for i, s in issuers(settings.oidc_base_url).items() if s == "author")


def _claims(**changes) -> dict:
    """A token that should pass, with `changes` applied; a value of None removes the claim."""
    now = int(time.time())
    claims = {"sub": "abc", "iss": _ISS, "aud": AUDIENCES["author"],
              "exp": now + 600, "iat": now,
              "preferred_username": "anna", "groups": ["chy-reviewers"]}
    for name, value in changes.items():
        if value is None:
            claims.pop(name, None)
        else:
            claims[name] = value
    return claims


async def _auth(token: str | None, jwks: dict = _JWKS):
    credentials = None if token is None else HTTPAuthorizationCredentials(
        scheme="Bearer", credentials=token)
    with patch("api.deps._fetch_jwks", AsyncMock(return_value=jwks)):
        return await require_auth(credentials)


async def _refused(token: str | None, status: int = 401) -> HTTPException:
    with pytest.raises(HTTPException) as e:
        await _auth(token)
    assert e.value.status_code == status, e.value.detail
    return e.value


class TestAValidTokenIsLetIn:
    async def test_the_caller_carries_the_claims(self):
        user = await _auth(sign(_claims(), _KEY))
        assert (user.sub, user.username, user.groups, user.audience) == (
            "abc", "anna", ["chy-reviewers"], AUDIENCES["author"])

    async def test_an_audience_list_passes_if_one_entry_is_ours(self):
        """RFC 7519 allows `aud` as a list, and Authentik has used both forms."""
        user = await _auth(sign(_claims(aud=["someone-else", AUDIENCES["author"]]), _KEY))
        assert user.sub == "abc"

    async def test_a_missing_groups_claim_stays_none(self):
        """None is "the IdP says nothing", [] is "in no group" — CHY-33 keys on it."""
        user = await _auth(sign(_claims(groups=None), _KEY))
        assert user.groups is None

    async def test_an_unknown_kid_falls_back_to_the_issuers_keys(self):
        """After a rotation the kid may be new; every key of the issuer is tried.

        Safe because the issuer was checked first: only its own keys are tried.
        """
        user = await _auth(sign(_claims(), _KEY, kid="rotated-key"))
        assert user.sub == "abc"

    async def test_an_unusable_key_in_the_jwks_is_skipped(self):
        """PyJWT reports bad key material as InvalidKeyError, which is not a
        token error; uncaught, one broken JWKS entry would turn every login
        into a 500."""
        broken = {"kty": "RSA", "kid": "test-key", "n": "AA", "e": "AQAB"}
        user = await _auth(sign(_claims(), _KEY), jwks={"keys": [broken, jwk_of(_KEY)]})
        assert user.sub == "abc"


class TestAnInvalidTokenIsTurnedAway:
    async def test_only_unusable_keys_is_a_401_not_a_500(self):
        broken = {"kty": "RSA", "kid": "test-key", "n": "AA", "e": "AQAB"}
        with pytest.raises(HTTPException) as e:
            await _auth(sign(_claims(), _KEY), jwks={"keys": [broken]})
        assert e.value.status_code == 401

    async def test_no_token(self):
        await _refused(None)

    async def test_not_a_jwt(self):
        await _refused("not-a-token")

    async def test_expired(self):
        await _refused(sign(_claims(exp=int(time.time()) - 60), _KEY))

    async def test_not_yet_valid(self):
        await _refused(sign(_claims(nbf=int(time.time()) + 600), _KEY))

    async def test_a_foreign_audience(self):
        await _refused(sign(_claims(aud="some-other-service"), _KEY))

    async def test_no_audience(self):
        await _refused(sign(_claims(aud=None), _KEY))

    async def test_a_foreign_issuer(self):
        await _refused(sign(_claims(iss="https://evil.example/application/o/x/"), _KEY))

    async def test_signed_with_a_foreign_key_under_our_kid(self):
        await _refused(sign(_claims(), _OTHER_KEY))

    async def test_a_payload_changed_after_signing(self):
        header, _, signature = sign(_claims(), _KEY).split(".")
        forged = sign(_claims(groups=["chy-admins"]), _KEY).split(".")[1]
        await _refused(f"{header}.{forged}.{signature}")

    async def test_no_expiry(self):
        """A token without `exp` would never expire — a leaked one stays valid forever.

        python-jose checked `exp` only when it was there (CHY-202 makes it required).
        """
        await _refused(sign(_claims(exp=None), _KEY))

    async def test_no_subject(self):
        """Without `sub` there is no actor. Before CHY-202 the token passed the
        check and require_auth then failed on `claims["sub"]` — a 500, not a 401."""
        await _refused(sign(_claims(sub=None), _KEY))

    async def test_the_refusal_asks_for_a_bearer_token(self):
        refusal = await _refused(sign(_claims(exp=int(time.time()) - 60), _KEY))
        assert refusal.headers == {"WWW-Authenticate": "Bearer"}


class TestOnlyRS256IsAccepted:
    """The algorithm is pinned, so the token's header cannot choose it.

    The audit noted `algorithms=["RS256"]` as the right defence and asked for a
    test that keeps it there (CHY-93).
    """

    async def test_alg_none(self):
        await _refused(sign(_claims(), alg="none"))

    async def test_hmac_with_the_public_key_as_secret(self):
        """The classic confusion: the public key is public, so if HS256 were
        accepted, anyone could sign with it."""
        await _refused(sign(_claims(), alg="HS256", secret=public_pem(_KEY)))

    async def test_rs384_even_with_the_right_key(self):
        await _refused(sign(_claims(), _KEY, alg="RS384"))


class TestTheIdentityProviderMustAnswer:
    """No keys, no entry: an unreachable JWKS endpoint is a 503, never a pass."""

    @pytest.fixture(autouse=True)
    def _empty_cache(self):
        deps._jwks_cache.clear()
        yield
        deps._jwks_cache.clear()

    @staticmethod
    def _client(get):
        class Client:
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def get(self, url, timeout=None): return get(url)
        return Client

    async def _through_the_fetch(self, get) -> HTTPException:
        credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=sign(_claims(), _KEY))
        with patch("api.deps.httpx.AsyncClient", self._client(get)), \
                pytest.raises(HTTPException) as e:
            await require_auth(credentials)
        return e.value

    async def test_connection_refused(self):
        def refuse(url):
            raise httpx.ConnectError("connection refused")
        assert (await self._through_the_fetch(refuse)).status_code == 503

    async def test_an_error_status(self):
        def fail(url):
            return httpx.Response(500, request=httpx.Request("GET", url))
        assert (await self._through_the_fetch(fail)).status_code == 503


class TestAShortKeyIsNeverUsed:
    """OpenSSF `crypto_keylength` (CHY-273): a key under 2048 bits signs nothing here."""

    _SHORT = new_key(1024)

    async def test_a_token_signed_with_a_short_key_is_a_503_naming_the_cause(self, caplog):
        import logging

        jwks = {"keys": [jwk_of(self._SHORT, kid="short")]}
        with caplog.at_level(logging.WARNING, logger="api.deps"), \
                pytest.raises(HTTPException) as e:
            await _auth(sign(_claims(), self._SHORT, kid="short"), jwks)
        assert e.value.status_code == 503, e.value.detail
        assert "shorter than 2048 bits" in e.value.detail
        [line] = [r for r in caplog.records if r.getMessage() == "JWKS key too short"]
        assert (line.kid, line.bits) == ("short", 1024)

    async def test_a_short_key_beside_a_good_one_does_not_stand_in_the_way(self):
        """Without a kid every key is tried: the short one is passed over, the
        good one verifies — the token is fine, the short key just never counts."""
        jwks = {"keys": [jwk_of(self._SHORT, kid="short"), jwk_of(_KEY)]}
        user = await _auth(sign(_claims(), _KEY, kid=None), jwks)
        assert user.sub == "abc"

    async def test_a_2048_bit_key_is_used(self):
        """Counter-probe: the minimum itself is enough."""
        user = await _auth(sign(_claims(), _KEY))
        assert user.sub == "abc"

