"""
tests/test_jsonb_parameters.py

A JSONB parameter is always sent as JSON text with an explicit cast (CHY-164).

`core.db` registers a JSONB codec with `encoder=json.dumps`, and the stores
pass strings they already serialised. On the application's pool the codec
encoded them a second time: Postgres stored a JSON *string* where an object
belonged. All 98 rows of `context_blocks.metadata` in dev were strings, the
CHECKs of migrations 022 and 023 never saw an object on the API path, and a
valid `context_requirements` was refused. On a bare connection without the
codec the same statement was correct — the same code, two outcomes, depending
on who opened the connection.

`$n::text::jsonb` makes the parameter text and lets Postgres parse it, on any
connection. This test forbids the other spelling in every module that writes.
It cannot see a parameter written into a JSONB column without any cast at all
(Postgres infers the type from the column); tests/integration covers those by
checking what actually landed.
"""
from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_WRITERS = sorted(
    [*(_ROOT / "contexts").glob("*/store.py"), _ROOT / "kernel" / "audit.py",
     *(_ROOT / "kernel").glob("*.py")]
)
_BARE_JSONB_PARAMETER = re.compile(r"\$\d+::jsonb")


def test_the_writers_are_found():
    """A glob that matches nothing would make the test below vacuously true."""
    assert len(_WRITERS) >= 5, _WRITERS


def test_no_parameter_is_cast_straight_to_jsonb():
    offenders = {
        str(p.relative_to(_ROOT)): _BARE_JSONB_PARAMETER.findall(p.read_text(encoding="utf-8"))
        for p in _WRITERS
    }
    offenders = {k: v for k, v in offenders.items() if v}
    assert not offenders, (
        f"JSONB parameters cast straight to jsonb: {offenders}. Write "
        f"`$n::text::jsonb` and pass json.dumps(...) — straight `::jsonb` lets the "
        f"pool's codec encode the string a second time (CHY-164)."
    )
