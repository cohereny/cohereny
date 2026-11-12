"""Whoever is asking, as the kernel sees them (kernel.identity.Caller).

`resolve_actor` provisions an unknown caller with the role its groups name, so
a test gets a real actor row by asking — the same path a first login takes.
"""
from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4


@dataclass
class Caller:
    sub: str
    username: str
    groups: list[str] | None


def caller(role: str) -> Caller:
    """A new caller in the chy-<role>s group, unique per call."""
    return Caller(sub=f"it-{role}-{uuid4().hex}", username=f"it {role}",
                  groups=[f"chy-{role}s"])
