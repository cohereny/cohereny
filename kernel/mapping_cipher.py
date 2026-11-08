"""
kernel/mapping_cipher.py

Sealing a run's placeholder mapping (CHY-234, ADR-018 point 5).

A run is stored with placeholders only -- `{{PERSON_1}}` where the name was --
and the mapping back to the values is the one place the values still exist. It
is kept per run in `pii_mappings`, encrypted here with a key the database never
sees. A dump, a backup on an unencrypted disk, a copy handed to someone for
debugging: none of them shows a value without the key.

The first thing Cohereny encrypts itself. ADR-014 leaves data at rest to the
operator, and that stays the rule; the exception is narrow on purpose. The
mapping is the part of a run that is nothing but personal data, and the
operator's disk encryption does not help against a dump taken by someone with
database access.

How
---
AES-256-GCM from `cryptography`. Authenticated: a changed byte fails the
decryption instead of yielding a different mapping. The run id is associated
data, so a mapping copied onto another run's row does not open there. Every
sealing draws a fresh 96-bit nonce from the operating system.

Keys come from the environment as `<key id>:<32 bytes, base64url>`, comma
separated. The first key seals; every key listed opens what it sealed. That is
the whole rotation: put the new key first, keep the old one until the runs it
sealed have passed their content period (ADR-046), then drop it. A key that is
lost takes the mappings it sealed with it -- the placeholders stay, the values
are gone.

Dependencies: cryptography. No database, no clock.
"""
from __future__ import annotations

import base64
import json
import os
import re
import secrets
from collections.abc import Mapping
from dataclasses import dataclass, field
from uuid import UUID

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

#: The environment variable the keys come from. Named in every message, so an
#: operator reading one knows what to change.
KEYS_VARIABLE = "PII_MAPPING_KEYS"

#: AES-256.
KEY_BYTES = 32

#: The size GCM is specified for; any other length is hashed into one, which
#: weakens the guarantee against a repeated nonce.
NONCE_BYTES = 12

#: A key id is stored next to every sealed mapping and written into messages.
#: Short, lower case, and free of the separators of the environment format.
KEY_ID_PATTERN = re.compile(r"[a-z0-9][a-z0-9_-]{0,31}")

# The URL-safe alphabet, padding optional.
_BASE64URL = re.compile(r"[A-Za-z0-9_-]+={0,2}")

# Versioned, so a later format cannot be confused with this one: the same bytes
# sealed under a different scheme would not open.
_ASSOCIATED_PREFIX = b"cohereny.pii_mapping.v1:"


class MappingKeyError(RuntimeError):
    """The keys in the environment cannot be used. Raised at start, never later."""


class MappingUnreadableError(RuntimeError):
    """A sealed mapping does not open: unknown key, another run, or changed bytes."""


@dataclass(frozen=True)
class Sealed:
    """A mapping as it is stored: which key sealed it, the nonce, the ciphertext."""

    key_id: str
    nonce: bytes
    ciphertext: bytes


@dataclass(frozen=True)
class KeyRing:
    """The keys an installation holds. `current` seals; all of them open.

    The key material is left out of the representation: a settings object or a
    traceback that prints the ring shows the ids, never the keys (CHY-233).
    """

    current: str
    keys: Mapping[str, bytes] = field(repr=False)


def _decoded(key_id: str, encoded: str) -> bytes:
    # Checked before decoding: the decoder skips characters outside the alphabet
    # without a word, so a key with a stray character would be read as another
    # key -- or, with the right length left over, accepted.
    if not _BASE64URL.fullmatch(encoded):
        msg = f"{KEYS_VARIABLE}: key {key_id!r} is not base64url"
        raise MappingKeyError(msg)
    padded = encoded + "=" * (-len(encoded) % 4)
    try:
        key = base64.urlsafe_b64decode(padded.encode("ascii"))
    except ValueError:
        msg = f"{KEYS_VARIABLE}: key {key_id!r} is not base64url"
        raise MappingKeyError(msg) from None
    if len(key) != KEY_BYTES:
        msg = f"{KEYS_VARIABLE}: key {key_id!r} has {len(key)} bytes, {KEY_BYTES} are needed"
        raise MappingKeyError(msg)
    return key


def keyring_from(value: str | None) -> KeyRing:
    """Read the keys from the environment's format; refuse anything doubtful.

    The messages name the variable and the key id, never the key: they end up
    in logs.
    """
    if not value or not value.strip():
        msg = (
            f"{KEYS_VARIABLE} is not set. Run content is stored with placeholders, and the "
            f"mapping back to the values is encrypted with this key (CHY-234). Generate one "
            f"with `python -c 'from kernel.mapping_cipher import generate_key; "
            f"print(generate_key(\"k1\"))'` and keep it outside the database."
        )
        raise MappingKeyError(msg)
    keys: dict[str, bytes] = {}
    order: list[str] = []
    for entry in value.split(","):
        key_id, sep, encoded = entry.strip().partition(":")
        if not sep or not key_id or not encoded:
            msg = f"{KEYS_VARIABLE}: every entry is `<key id>:<key>`, separated by commas"
            raise MappingKeyError(msg)
        if not KEY_ID_PATTERN.fullmatch(key_id):
            msg = (
                f"{KEYS_VARIABLE}: key id {key_id!r} must be lower case letters, digits, "
                f"`_` or `-`, starting with a letter or digit, at most 32 characters"
            )
            raise MappingKeyError(msg)
        if key_id in keys:
            msg = f"{KEYS_VARIABLE}: key id {key_id!r} appears twice"
            raise MappingKeyError(msg)
        keys[key_id] = _decoded(key_id, encoded)
        order.append(key_id)
    return KeyRing(current=order[0], keys=keys)


def generate_key(key_id: str) -> str:
    """A new entry for the environment: `<key id>:<32 random bytes, base64url>`."""
    if not KEY_ID_PATTERN.fullmatch(key_id):
        msg = f"key id {key_id!r} does not match {KEY_ID_PATTERN.pattern}"
        raise MappingKeyError(msg)
    encoded = base64.urlsafe_b64encode(secrets.token_bytes(KEY_BYTES)).decode("ascii")
    return f"{key_id}:{encoded.rstrip('=')}"


def _associated(run_id: UUID) -> bytes:
    return _ASSOCIATED_PREFIX + str(run_id).encode("ascii")


def seal(mapping: Mapping[str, str], *, run_id: UUID, keys: KeyRing) -> Sealed:
    """Encrypt a run's mapping with the current key, bound to that run."""
    plaintext = json.dumps(dict(mapping), sort_keys=True, ensure_ascii=False).encode("utf-8")
    nonce = os.urandom(NONCE_BYTES)
    ciphertext = AESGCM(keys.keys[keys.current]).encrypt(nonce, plaintext, _associated(run_id))
    return Sealed(key_id=keys.current, nonce=nonce, ciphertext=ciphertext)


def unseal(sealed: Sealed, *, run_id: UUID, keys: KeyRing) -> dict[str, str]:
    """Decrypt a run's mapping, or say plainly why it does not open.

    Never a partial or a guessed mapping: whatever fails, nothing is returned.
    """
    key = keys.keys.get(sealed.key_id)
    if key is None:
        msg = (
            f"the mapping of run {run_id} was sealed with key {sealed.key_id!r}, "
            f"which is not in {KEYS_VARIABLE}"
        )
        raise MappingUnreadableError(msg)
    try:
        plaintext = AESGCM(key).decrypt(sealed.nonce, sealed.ciphertext, _associated(run_id))
    except InvalidTag:
        msg = (
            f"the mapping of run {run_id} does not open with key {sealed.key_id!r}: "
            f"it was changed, or sealed for another run"
        )
        raise MappingUnreadableError(msg) from None
    mapping = json.loads(plaintext)
    if not isinstance(mapping, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in mapping.items()
    ):
        msg = f"the mapping of run {run_id} opened but is not placeholder → value"
        raise MappingUnreadableError(msg)
    return mapping
