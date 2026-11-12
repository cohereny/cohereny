"""
steps/

Computation steps a workflow runs as a `compute` node (ADR-048, CHY-173).

A step is a pure function, registered by name and version and named by a
workflow definition — `banking.ratios`, version 1. There is no code in the
database and no expression language in a definition: a calculation changes by
review and release, not by a click.

Pure means checkably pure: this package imports the standard library's
arithmetic and itself, nothing else, and scripts/architecture.py holds that as
the rule `impure_step`. No network, no database, no clock, no randomness — the
same input gives the same output, tomorrow and in an audit a year from now.

A missing or unusable input fails the step with a reason that names the field,
never its value (steps/base.py). A step that changes what it computes gets a
new version; the old one stays as long as a released definition names it.
"""
from steps.base import Step, StepInputError
from steps.registry import known, lookup

__all__ = ["Step", "StepInputError", "known", "lookup"]
