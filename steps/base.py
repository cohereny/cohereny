"""
steps/base.py

What every compute step is made of, and how it refuses an input (ADR-048).

The refusals are the point of this module. The banking pilot divided with
`safe_div(..., default=0.0)` and read a missing current ratio as 1.0: a figure
that was not there came out as a figure, and an equity ratio of 0 % reads as a
finding rather than a gap. Here a missing field, a value that is not a number
and a zero denominator each stop the step, with a reason that names the field
and never its value.
"""
from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Step:
    """A pure function a workflow names by `name` and `version`."""

    name: str
    version: int
    run: Callable[[Mapping[str, Any]], dict[str, Any]]
    #: The keys it reads from its input — what the node's input_mapping has to
    #: provide. Named for the guide and for the refusal, not enforced separately:
    #: reading a key that is not there already fails.
    reads: tuple[str, ...]

    @property
    def ref(self) -> str:
        """`name@version`, as it is recorded with every execution."""
        return f"{self.name}@{self.version}"


class StepInputError(ValueError):
    """The input cannot be computed with. The text names the field, never its value."""

    @classmethod
    def missing(cls, field: str) -> StepInputError:
        return cls(f"{field} is missing")

    @classmethod
    def not_an_object(cls, field: str) -> StepInputError:
        return cls(f"{field} is not an object")

    @classmethod
    def not_a_number(cls, field: str) -> StepInputError:
        return cls(f"{field} is not a finite number")

    @classmethod
    def zero(cls, field: str, result: str) -> StepInputError:
        return cls(f"{field} is 0, so {result} cannot be computed")


def section(data: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    """The object under `key`, or StepInputError."""
    if key not in data:
        raise StepInputError.missing(key)
    value = data[key]
    if not isinstance(value, Mapping):
        raise StepInputError.not_an_object(key)
    return value


def number(data: Mapping[str, Any], key: str, *, within: str) -> float:
    """A finite number under `key` — not a bool, not a string that looks like one.

    `within` is the name of the object it is read from, so the refusal says
    `kpis.umsatz_current` rather than a bare field name.
    """
    field = f"{within}.{key}"
    if key not in data:
        raise StepInputError.missing(field)
    value = data[key]
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise StepInputError.not_a_number(field)
    return float(value)


def ratio(numerator: float, denominator: float, *, denominator_field: str, result: str) -> float:
    """numerator / denominator, or StepInputError when the denominator is 0."""
    if denominator == 0:
        raise StepInputError.zero(denominator_field, result)
    return numerator / denominator
