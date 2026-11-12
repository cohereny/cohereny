"""
steps/registry.py

Which compute steps exist, by name and version (ADR-048).

A list written out rather than collected by decorators or imports: what a
workflow may run is then visible in one place and in one diff. A step that is
not in this list does not exist — a definition naming it cannot be activated,
and a node that still reaches it fails with that reason.
"""
from __future__ import annotations

from steps import banking
from steps.base import Step

_STEPS: tuple[Step, ...] = (
    banking.RATIOS,
    banking.ANOMALIES,
)

_BY_REF: dict[tuple[str, int], Step] = {(s.name, s.version): s for s in _STEPS}


def lookup(name: str | None, version: int | None) -> Step | None:
    """The step a node names, or None when there is no such name and version."""
    if name is None or version is None:
        return None
    return _BY_REF.get((name, version))


def known() -> list[str]:
    """Every `name@version`, for a refusal that says what would have been valid."""
    return sorted(step.ref for step in _STEPS)
