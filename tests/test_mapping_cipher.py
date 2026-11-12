"""
tests/test_mapping_cipher.py

Sealing a run's placeholder mapping (CHY-234, ADR-018 point 5).

What the encryption promises is shown by what it refuses: a mapping opens for
its own run with a configured key and for nothing else -- not for another run,
not after a single byte changed, not with a key that is gone. Each refusal has
its counterpart that opens, so a cipher that refused everything would fail half
of this file. The key material never appears in a message or a representation.
"""
from __future__ import annotations

import base64
from uuid import uuid4

import pytest

from kernel.mapping_cipher import (
    KEY_BYTES,
    KEYS_VARIABLE,
    KeyRing,
    MappingKeyError,
    MappingUnreadableError,
    Sealed,
    generate_key,
    keyring_from,
    seal,
    unseal,
)

MAPPING = {"PERSON_1": "Erika Mustermann", "EMAIL_1": "erika@example.de"}


def _key(byte: int) -> str:
    return base64.urlsafe_b64encode(bytes([byte]) * KEY_BYTES).decode("ascii")


K1, K2 = _key(1), _key(2)


def _ring(value: str = f"k1:{K1}") -> KeyRing:
    return keyring_from(value)


class TestSealAndOpen:
    def test_a_mapping_opens_for_its_own_run(self):
        run = uuid4()
        assert unseal(seal(MAPPING, run_id=run, keys=_ring()), run_id=run, keys=_ring()) == MAPPING

    def test_the_values_do_not_appear_in_what_is_stored(self):
        sealed = seal(MAPPING, run_id=uuid4(), keys=_ring())
        stored = sealed.ciphertext + sealed.nonce + sealed.key_id.encode()
        for value in MAPPING.values():
            assert value.encode() not in stored

    def test_every_sealing_draws_a_new_nonce(self):
        """The same mapping twice must not look the same twice: GCM breaks when a
        nonce repeats under one key, and equal ciphertexts would tell that two runs
        named the same person."""
        run = uuid4()
        first, second = (seal(MAPPING, run_id=run, keys=_ring()) for _ in range(2))
        assert first.nonce != second.nonce
        assert first.ciphertext != second.ciphertext

    def test_text_beyond_ascii_survives(self):
        run = uuid4()
        mapping = {"PERSON_1": "Jürgen Weiß-Öztürk", "PERSON_2": "李小龙"}
        assert unseal(seal(mapping, run_id=run, keys=_ring()), run_id=run, keys=_ring()) == mapping

    def test_an_empty_mapping_is_a_mapping(self):
        """A run whose input named nobody still has a row: the boundary may add to it."""
        run = uuid4()
        assert unseal(seal({}, run_id=run, keys=_ring()), run_id=run, keys=_ring()) == {}


class TestWhatDoesNotOpen:
    def test_not_for_another_run(self):
        """A mapping copied onto another run's row stays shut."""
        sealed = seal(MAPPING, run_id=uuid4(), keys=_ring())
        with pytest.raises(MappingUnreadableError, match="sealed for another run"):
            unseal(sealed, run_id=uuid4(), keys=_ring())

    @pytest.mark.parametrize("part", ["ciphertext", "nonce"])
    def test_not_after_one_byte_changed(self, part: str):
        run = uuid4()
        sealed = seal(MAPPING, run_id=run, keys=_ring())
        data = bytearray(getattr(sealed, part))
        data[0] ^= 0x01
        changed = Sealed(**{**sealed.__dict__, part: bytes(data)})
        with pytest.raises(MappingUnreadableError, match="was changed"):
            unseal(changed, run_id=run, keys=_ring())

    def test_not_with_a_key_that_is_gone(self):
        run = uuid4()
        sealed = seal(MAPPING, run_id=run, keys=_ring(f"old:{K1}"))
        with pytest.raises(MappingUnreadableError, match="'old', which is not in PII_MAPPING_KEYS"):
            unseal(sealed, run_id=run, keys=_ring(f"new:{K2}"))

    def test_not_with_another_key_under_the_same_id(self):
        """The id names a key; it does not prove one. A swapped key fails like a
        changed byte."""
        run = uuid4()
        sealed = seal(MAPPING, run_id=run, keys=_ring(f"k1:{K1}"))
        with pytest.raises(MappingUnreadableError, match="does not open"):
            unseal(sealed, run_id=run, keys=_ring(f"k1:{K2}"))


class TestRotation:
    def test_the_first_key_seals(self):
        assert seal(MAPPING, run_id=uuid4(), keys=_ring(f"new:{K2},old:{K1}")).key_id == "new"

    def test_an_old_key_still_opens_what_it_sealed(self):
        run = uuid4()
        sealed = seal(MAPPING, run_id=run, keys=_ring(f"old:{K1}"))
        assert unseal(sealed, run_id=run, keys=_ring(f"new:{K2},old:{K1}")) == MAPPING


class TestKeysFromTheEnvironment:
    @pytest.mark.parametrize("value", [None, "", "   "])
    def test_missing_keys_are_refused(self, value):
        with pytest.raises(MappingKeyError, match=f"{KEYS_VARIABLE} is not set"):
            keyring_from(value)

    @pytest.mark.parametrize("value", [K1, "k1:", f"k1:{K1},", f":{K1}"])
    def test_an_entry_without_id_or_key_is_refused(self, value: str):
        with pytest.raises(MappingKeyError, match="<key id>:<key>"):
            keyring_from(value)

    @pytest.mark.parametrize("key_id", ["K1", "-k1", "k 1", "k" * 33, "k:1"])
    def test_a_doubtful_key_id_is_refused(self, key_id: str):
        with pytest.raises(MappingKeyError):
            keyring_from(f"{key_id}:{K1}")

    def test_a_key_of_the_wrong_length_is_refused(self):
        short = base64.urlsafe_b64encode(b"x" * 16).decode()
        with pytest.raises(MappingKeyError, match="16 bytes, 32 are needed"):
            keyring_from(f"k1:{short}")

    def test_a_key_that_is_not_base64url_is_refused(self):
        with pytest.raises(MappingKeyError, match="not base64url"):
            keyring_from("k1:@@@@")

    def test_a_stray_character_in_a_valid_key_is_refused(self):
        """The decoder would skip it and accept the rest as the key -- found when
        this file was first run (06.11.2026)."""
        stray = K1[:10] + "@" + K1[10:]
        with pytest.raises(MappingKeyError, match="not base64url"):
            keyring_from(f"k1:{stray}")

    def test_an_id_given_twice_is_refused(self):
        with pytest.raises(MappingKeyError, match="appears twice"):
            keyring_from(f"k1:{K1},k1:{K2}")

    def test_unpadded_keys_are_read(self):
        """`generate_key` writes no padding; the environment is the same format."""
        assert keyring_from(f"k1:{K1.rstrip('=')}").keys["k1"] == bytes([1]) * KEY_BYTES

    def test_no_message_shows_a_key(self):
        """Messages reach logs. Whatever is wrong, the key itself is never in them."""
        long_key = base64.urlsafe_b64encode(b"s" * 40).decode()
        for value in (f"k1:{long_key}", f"k1:{K1},k1:{K2}", f"BAD:{K1}"):
            with pytest.raises(MappingKeyError) as caught:
                keyring_from(value)
            for secret in (long_key, K1, K2):
                assert secret.rstrip("=") not in str(caught.value)

    def test_the_ring_does_not_show_its_keys(self):
        """Neither as configured nor as the bytes the ring holds -- the first
        version of this test looked only for the base64 text, which a printed
        ring never contains, and passed with the keys in plain view."""
        ring = _ring(f"k1:{K1},k2:{K2}")
        shown = repr(ring)
        assert "k1" in shown
        for key in ring.keys.values():
            assert repr(key) not in shown
        for configured in (K1, K2):
            assert configured.rstrip("=") not in shown


class TestGenerateKey:
    def test_a_generated_entry_is_read_back(self):
        ring = keyring_from(generate_key("k1"))
        assert ring.current == "k1"
        assert len(ring.keys["k1"]) == KEY_BYTES

    def test_two_generated_keys_differ(self):
        assert generate_key("k1") != generate_key("k1")

    def test_a_doubtful_id_is_refused(self):
        with pytest.raises(MappingKeyError):
            generate_key("K 1")
