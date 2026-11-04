"""
tests/signed_tokens.py

Signed JWTs for the token tests, assembled from `cryptography` alone (CHY-93).

The tests check the verifier in api/deps.py, so they must not borrow its JWT
library to make their tokens: when that library is replaced (CHY-202), a ruler
made of it would change along with what it measures. Header, payload and
signature are put together by hand here, as RFC 7515 describes them — base64url
without padding, joined by dots, signed with RS256, RS384, HS256 or not at all.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa


def b64url(data: bytes) -> str:
    """base64url without padding, the encoding every part of a JWT uses."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64url_int(n: int) -> str:
    return b64url(n.to_bytes((n.bit_length() + 7) // 8, "big"))


def new_key() -> rsa.RSAPrivateKey:
    """A fresh RSA key pair, 2048 bits, as Authentik signs with."""
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def jwk_of(key: rsa.RSAPrivateKey, kid: str = "test-key") -> dict:
    """The public half of `key` as a JWK, the shape a JWKS document carries."""
    numbers = key.public_key().public_numbers()
    return {"kty": "RSA", "use": "sig", "alg": "RS256", "kid": kid,
            "n": _b64url_int(numbers.n), "e": _b64url_int(numbers.e)}


def public_pem(key: rsa.RSAPrivateKey) -> bytes:
    """The public key as PEM — what an HS256 confusion attack uses as its secret."""
    return key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )


def sign(claims: dict, key: rsa.RSAPrivateKey | None = None, *, alg: str = "RS256",
         kid: str | None = "test-key", secret: bytes | None = None) -> str:
    """A compact JWS over `claims`.

    `alg` is written into the header as given and decides the signature:
    RS256/RS384 with `key`, HS256 with `secret`, `none` with an empty one.
    """
    header = {"alg": alg, "typ": "JWT"}
    if kid is not None:
        header["kid"] = kid
    signing_input = ".".join(
        b64url(json.dumps(part, separators=(",", ":")).encode()) for part in (header, claims)
    ).encode()

    if alg in ("RS256", "RS384"):
        digest = hashes.SHA256() if alg == "RS256" else hashes.SHA384()
        signature = key.sign(signing_input, padding.PKCS1v15(), digest)
    elif alg == "HS256":
        signature = hmac.new(secret, signing_input, hashlib.sha256).digest()
    elif alg == "none":
        signature = b""
    else:
        raise ValueError(f"unsupported alg {alg!r}")
    return f"{signing_input.decode()}.{b64url(signature)}"
