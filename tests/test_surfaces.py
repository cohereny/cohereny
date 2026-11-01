"""A token reaches the surface it was issued for, and no other (ADR-031, CHY-109).

Measured on 19.08.2026, before any of this existed: one audience, "cohereny",
for 55 operations. cohereny-ui used eight of them and its token could call all
fifty-five. The role check caught some of that — but roles and audiences answer
different questions, and one mechanism was answering both.

The acceptance criterion of CHY-109 is the third test here: a token for the
author surface must be turned away from an admin endpoint *before* anyone asks
what its holder is allowed to do.
"""
from __future__ import annotations

import pathlib
import sys
from uuid import UUID, uuid4

from unittest.mock import patch

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from jose import jwk, jwt

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from api.config import settings
from api.deps import _require_known_audience, _require_known_issuer, _validate_token
from api.surfaces import AUDIENCE_PREFIX, AUDIENCES, Surface
from kernel.surfaces import issuers
from tests.conftest import actor_row, client_for, make_pool, stub_user

_ACTOR = UUID("44444444-4444-4444-4444-444444444444")


class TestTheGateLetsTheRightTokenThrough:
    def test_an_author_token_reaches_an_author_endpoint(self):
        pool = make_pool(actor=actor_row(_ACTOR, role="admin"), fetch=[])
        user = stub_user(audience=AUDIENCES["author"])
        with client_for(pool, user=user) as client:
            assert client.get("/v1/scopes").status_code == 200

    def test_no_token_at_all_stands_aside(self):
        """Dev mode presents no token, so there is no audience to judge.

        Guarded against production by CHY-91: DEV_MODE is refused unless the
        stage says dev and the database is not a real one.
        """
        pool = make_pool(actor=actor_row(_ACTOR, role="admin"), fetch=[])
        with client_for(pool, user=stub_user(audience=None)) as client:
            assert client.get("/v1/scopes").status_code == 200


class TestTheGateTurnsTheWrongTokenAway:
    def test_an_author_token_is_refused_at_an_admin_endpoint(self):
        """The acceptance criterion of CHY-109.

        The actor is an admin — the role check would have passed. The token was
        not issued for this surface, so the question never arises.
        """
        pool = make_pool(actor=actor_row(_ACTOR, role="admin"))
        with client_for(pool, user=stub_user(audience=AUDIENCES["author"])) as client:
            resp = client.post(f"/v1/admin/actors/{_ACTOR}/anonymize")
            assert resp.status_code == 403
            assert "issued for" in resp.json()["detail"], resp.text

    def test_the_refusal_names_both_sides(self):
        """A 403 that does not say which token and which surface is a puzzle."""
        pool = make_pool(actor=actor_row(_ACTOR, role="admin"))
        with client_for(pool, user=stub_user(audience=AUDIENCES["serve"])) as client:
            detail = client.get("/v1/scopes").json()["detail"]
        assert AUDIENCES["serve"] in detail
        assert AUDIENCES["author"] in detail

    @pytest.mark.parametrize("path,method,wrong", [
        ("/v1/scopes", "get", "serve"),
        ("/v1/blocks", "get", "observe"),
        ("/v1/compose", "post", "author"),
        ("/v1/audit-log", "get", "serve"),
    ])
    def test_every_surface_refuses_a_foreign_token(self, path, method, wrong):
        pool = make_pool(actor=actor_row(_ACTOR, role="admin"), fetch=[])
        with client_for(pool, user=stub_user(audience=AUDIENCES[wrong])) as client:
            resp = getattr(client, method)(path, **({"json": {}} if method == "post" else {}))
            assert resp.status_code == 403, f"{method.upper()} {path}: {resp.status_code}"


class TestHealthTakesNoToken:
    def test_health_has_no_surface(self):
        """It answers before authentication and must keep doing so.

        A readiness probe behind an audience gate is a readiness probe that
        reports the service as down whenever the token is wrong.
        """
        pool = make_pool()
        with client_for(pool, user=stub_user(audience=AUDIENCES["admin"])) as client:
            assert client.get("/v1/health").status_code == 200


class TestUnknownAudiencesAreRefused:
    """`verify_aud` is switched off in the decode, so this check is the only one."""

    def test_a_foreign_audience_is_rejected(self):
        with pytest.raises(HTTPException) as e:
            _require_known_audience({"aud": "some-other-service"})
        assert e.value.status_code == 401

    def test_a_missing_audience_is_rejected(self):
        with pytest.raises(HTTPException):
            _require_known_audience({})

    def test_a_list_of_audiences_is_accepted_if_one_matches(self):
        """The spec allows `aud` to be a list, and Authentik has used both."""
        _require_known_audience({"aud": ["other", AUDIENCES["serve"]]})

    @pytest.mark.parametrize("surface", list(Surface))
    def test_every_surface_audience_is_known(self, surface):
        _require_known_audience({"aud": AUDIENCES[surface.value]})


class TestTheVocabularyHasOneHome:
    def test_audiences_are_derived_from_the_enum(self):
        """Adding a surface must not require editing a second list."""
        assert set(AUDIENCES) == {s.value for s in Surface}

    def test_every_audience_carries_the_prefix(self):
        from api.surfaces import AUDIENCE_PREFIX
        assert all(a.startswith(AUDIENCE_PREFIX) for a in AUDIENCES.values())

    def test_the_config_default_matches(self):
        """api/config.py must not restate the map."""
        import inspect

        from api import config
        src = inspect.getsource(config)
        assert "cohereny.serve" not in src, "audiences belong to api/surfaces.py alone"


# ── The check has to be reached, not merely present ──────────────────────────
#
# The first version of this file tested _require_known_audience directly. It was
# 19 green tests, and deleting the call to it from _validate_token left all 19
# green — the same blind spot found twice already this week (CHY-103, CHY-90).
#
# It matters more here than usual: `verify_aud` is switched off in the decode,
# so if this call is not reached, *no* audience check happens anywhere. The
# tokens below are therefore signed for real and put through the real path.

_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_PRIVATE_PEM = _KEY.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.PKCS8,
    encryption_algorithm=serialization.NoEncryption(),
).decode()
_PUBLIC_PEM = _KEY.public_key().public_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PublicFormat.SubjectPublicKeyInfo,
).decode()

_JWKS = {"keys": [{**jwk.construct(_PUBLIC_PEM, "RS256").to_dict(), "kid": "test-key"}]}
_JWKS["keys"][0] = {k: (v.decode() if isinstance(v, bytes) else v)
                    for k, v in _JWKS["keys"][0].items()}


_ISSUERS = issuers(settings.oidc_base_url)
_AUTHOR_ISS = next(i for i, s in _ISSUERS.items() if s == "author")


def _token(aud, iss: str = _AUTHOR_ISS) -> str:
    """A structurally valid token that differs only in its audience (or issuer)."""
    claims = {"sub": "abc", "iss": iss,
              "exp": 4102444800, "iat": 1700000000}
    if aud is not None:
        claims["aud"] = aud
    return jwt.encode(claims, _PRIVATE_PEM, algorithm="RS256",
                      headers={"kid": "test-key"})


class TestTheAudienceCheckIsOnThePath:
    def test_a_surface_token_validates(self):
        claims = _validate_token(_token(AUDIENCES["serve"]), _JWKS, _AUTHOR_ISS)
        assert claims["sub"] == "abc"

    def test_the_old_single_audience_is_refused(self):
        """`cohereny` was never issued by anything, so nothing depends on it.

        The audience map used to carry it as a transitional entry. INFRA-92 then
        showed the application of that name had never existed in Authentik —
        both schemes answered 404 — so the transition was from nowhere. Keeping
        it would have left a permanently valid audience nobody controls.
        """
        with pytest.raises(HTTPException) as e:
            _validate_token(_token("cohereny"), _JWKS, _AUTHOR_ISS)
        assert e.value.status_code == 401

    def test_a_foreign_audience_never_gets_past_validation(self):
        """Delete `_require_known_audience(claims)` from api/deps.py and this fails.

        That is the whole purpose of the test: verify_aud=False means the decode
        itself is indifferent to `aud`.
        """
        with pytest.raises(HTTPException) as e:
            _validate_token(_token("some-other-service"), _JWKS, _AUTHOR_ISS)
        assert e.value.status_code == 401

    def test_a_token_without_an_audience_never_gets_past_validation(self):
        with pytest.raises(HTTPException):
            _validate_token(_token(None), _JWKS, _AUTHOR_ISS)

    def test_the_surface_is_carried_onto_the_caller(self):
        """A claim that is validated and then dropped gates nothing.

        require_auth reads `aud` off the claims and puts it on CurrentUser;
        without that, every actor arrives with audience=None and the gate in
        api/surfaces.py stands aside for all of them.
        """
        import inspect

        from api import deps
        src = inspect.getsource(deps.require_auth)
        assert "audience=" in src, "require_auth must pass the audience through"


# ── Four issuers, and only four (INFRA-92) ───────────────────────────────────
#
# Authentik issues one `iss` per application, so there are four. The issuer also
# decides which keys verify the signature, which means it has to be read from
# the *unverified* payload — before anything about the token is trustworthy.
#
# That is only safe because of the order, and the order is what these tests pin
# down: check the value against a fixed set, then use it. Get it the other way
# round and a token nominates where its own signing key comes from, which is not
# validation but the appearance of it.

class TestTheIssuerMustBeOneOfOurs:
    @pytest.mark.parametrize("surface", list(Surface))
    def test_every_surface_issuer_is_accepted(self, surface):
        iss = next(i for i, s in _ISSUERS.items() if s == surface.value)
        assert _require_known_issuer(_token(AUDIENCES[surface.value], iss)) == iss

    def test_a_foreign_issuer_is_refused(self):
        with pytest.raises(HTTPException) as e:
            _require_known_issuer(_token(AUDIENCES["author"], "https://evil.example/o/x/"))
        assert e.value.status_code == 401
        assert "issuer" in e.value.detail

    def test_the_old_single_application_issuer_is_refused(self):
        """`…/application/o/cohereny/` is what the deployed .env still names.

        It never existed in Authentik. A token claiming it cannot be genuine,
        and accepting it would mean fetching keys from a 404.
        """
        old = f"{settings.oidc_base_url.rstrip('/')}/cohereny/"
        with pytest.raises(HTTPException):
            _require_known_issuer(_token(AUDIENCES["author"], old))

    def test_a_missing_issuer_is_refused(self):
        claims = {"sub": "abc", "exp": 4102444800}
        token = jwt.encode(claims, _PRIVATE_PEM, algorithm="RS256",
                           headers={"kid": "test-key"})
        with pytest.raises(HTTPException):
            _require_known_issuer(token)

    def test_a_token_that_is_not_a_jwt_is_refused_not_crashed(self):
        with pytest.raises(HTTPException) as e:
            _require_known_issuer("not-a-token")
        assert e.value.status_code == 401

    def test_the_check_happens_before_the_key_lookup(self):
        """The property the whole arrangement rests on.

        If _fetch_jwks were reachable with an unchecked issuer, a token could
        point it at a server the holder controls, and the signature would then
        verify against the attacker's key. So: no await on the fetch until the
        issuer has been through the allowlist.
        """
        import inspect

        from api import deps
        src = inspect.getsource(deps.require_auth)
        check = src.index("_require_known_issuer")
        fetch = src.index("_fetch_jwks")
        assert check < fetch, "the issuer must be validated before it selects keys"


class TestJwksIsCachedPerIssuer:
    """One shared entry would validate all four — until a key is rotated.

    The four applications share a signing certificate today, so a single cached
    document does work, and would keep working right up to the moment it
    silently does not. The first version of this test asserted only that the
    cache was a dict, which stayed green when the key was replaced by a
    constant — so it asserted nothing.
    """

    @pytest.fixture(autouse=True)
    def _empty_cache(self):
        from api import deps
        deps._jwks_cache.clear()
        yield
        deps._jwks_cache.clear()

    @staticmethod
    def _counting_client(seen: list[str]):
        class Resp:
            @staticmethod
            def raise_for_status(): return None
            @staticmethod
            def json(): return {"keys": []}

        class Client:
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def get(self, url, timeout=None):
                seen.append(url)
                return Resp()

        return Client

    @pytest.mark.asyncio
    async def test_two_issuers_are_fetched_separately(self):
        from api import deps
        seen: list[str] = []
        with patch("api.deps.httpx.AsyncClient", self._counting_client(seen)):
            for iss in list(_ISSUERS)[:2]:
                await deps._fetch_jwks(iss)
        assert len(seen) == 2, f"expected one fetch per issuer, got {seen}"
        assert len(set(seen)) == 2

    @pytest.mark.asyncio
    async def test_the_same_issuer_is_fetched_once(self):
        from api import deps
        seen: list[str] = []
        with patch("api.deps.httpx.AsyncClient", self._counting_client(seen)):
            await deps._fetch_jwks(_AUTHOR_ISS)
            await deps._fetch_jwks(_AUTHOR_ISS)
        assert len(seen) == 1, "the cache did not hold"

    @pytest.mark.asyncio
    async def test_the_url_is_the_issuer_plus_jwks(self):
        """Verified against all four discovery documents: jwks_uri == iss + jwks/."""
        from api import deps
        seen: list[str] = []
        with patch("api.deps.httpx.AsyncClient", self._counting_client(seen)):
            await deps._fetch_jwks(_AUTHOR_ISS)
        assert seen == [_AUTHOR_ISS.rstrip("/") + "/jwks/"]

    def test_issuers_are_derived_from_the_surfaces(self):
        assert set(_ISSUERS.values()) == {s.value for s in Surface}

    def test_an_issuer_is_the_base_plus_the_slug(self):
        """Either the UI client's slug or the machine client's (INFRA-122)."""
        from kernel.surfaces import CI_SLUGS, SLUGS
        for iss, surface in _ISSUERS.items():
            assert iss.endswith((f"/{SLUGS[surface]}/", f"/{CI_SLUGS[surface]}/")), iss

    def test_the_slugs_match_what_terraform_creates(self):
        """The reference installation's Terraform builds the same names.

        Terraform cannot import Python, so this boundary cannot be collapsed —
        the same shape as kernel/roles.py against the CHECK in 001_initial.sql.
        Asserting the rule at least makes a rename here fail loudly.
        """
        from kernel.surfaces import SLUGS
        assert SLUGS == {s.value: f"cohereny-{s.value}" for s in Surface}



# ── the machine client of each surface (INFRA-122) ────────────────────────────

class TestMachineClients:
    """A second client per surface for the CI, never the UI's own with a grant
    added: Authentik's client_credentials lets a client secret alone mint a
    token for an account it creates itself, and the UI's secret must not be
    able to do that (the reference installation's Terraform, 21.08.2026).

    What must hold is ADR-031's property, unchanged: one token, one surface.
    """

    @pytest.mark.parametrize("surface", list(Surface))
    def test_a_machine_token_opens_its_own_surface(self, surface):
        from kernel.surfaces import CI_AUDIENCES, accepted_audiences
        assert CI_AUDIENCES[surface.value] in accepted_audiences(surface.value)

    @pytest.mark.parametrize("surface", list(Surface))
    def test_a_machine_token_opens_no_other_surface(self, surface):
        """The property that matters. A CI token for `serve` must not reach
        `admin`, exactly as a UI token for `serve` must not."""
        from kernel.surfaces import CI_AUDIENCES, accepted_audiences
        others = [s for s in Surface if s is not surface]
        for other in others:
            assert CI_AUDIENCES[surface.value] not in accepted_audiences(other.value)

    def test_the_gate_lets_a_machine_token_in_and_keeps_it_on_its_surface(self):
        """Through the real router: author accepts its machine token, and the
        admin surface refuses it."""
        from kernel.surfaces import CI_AUDIENCES
        pool = make_pool()
        with client_for(pool, user=stub_user(audience=CI_AUDIENCES["author"])) as client:
            assert client.get("/v1/blocks").status_code != 403
            refused = client.post(f"/v1/admin/actors/{uuid4()}/anonymize")
        assert refused.status_code == 403
        assert CI_AUDIENCES["author"] in refused.json()["detail"]

    def test_a_machine_audience_is_one_of_ours(self):
        from kernel.surfaces import CI_AUDIENCES
        _require_known_audience({"aud": CI_AUDIENCES["serve"]})

    def test_an_invented_machine_audience_is_not(self):
        """Counter-probe: the suffix alone must not make an audience ours."""
        with pytest.raises(HTTPException):
            _require_known_audience({"aud": f"{AUDIENCE_PREFIX}nope.ci"})

    def test_each_machine_client_has_an_issuer_on_the_same_surface(self):
        from kernel.surfaces import CI_SLUGS
        for surface, slug in CI_SLUGS.items():
            iss = f"{settings.oidc_base_url.rstrip('/')}/{slug}/"
            assert _ISSUERS[iss] == surface

    def test_the_machine_slugs_match_what_terraform_creates(self):
        """Mirrored in the reference installation's Terraform (INFRA-122)."""
        from kernel.surfaces import CI_SLUGS
        assert CI_SLUGS == {s.value: f"cohereny-{s.value}-ci" for s in Surface}
