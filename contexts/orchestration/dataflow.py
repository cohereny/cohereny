"""
contexts/orchestration/dataflow.py

What one node hands to the next (CHY-131, arc42 §5.6).

A node never saw what its predecessor produced. `NodeContext.input_data`
carried the **run's** input — the same for every node, from the first to the
last iteration — and `node_executions.output_data` was written and read by
nothing. The mandatory core of ADR-004 was therefore not connected:
`context_compose` assembled a context that `llm_call` could not receive.

Three sources, kept apart on purpose
-------------------------------------
The obvious design is one merged dictionary that everything writes into. It is
also the one where two nodes silently overwrite each other and the loser is
whichever row came back second.

So a node sees:

  * the **run input**, unchanged, under `run`
  * each **immediate predecessor's** output under that predecessor's `node_ref`
  * whatever its own `input_mapping` selects out of those two

Nothing shares a namespace with anything, and the origin of every value is
visible in the address that produced it.

Why immediate predecessors and not everything so far
------------------------------------------------------
"Everything" is more convenient and makes a run one big shared scope, where a
node can quietly depend on a value produced three branches away. A node that
needs something from further up gets an **edge** — and then the data path is in
the picture rather than in a dictionary.

The aggregate of ADR-030: it decides, it does not execute.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

#: Where the run's own input appears. A key and not a merge: merging it would
#: make a run input named `content` collide with a predecessor's `content`, and
#: the collision would be invisible.
RUN_INPUT = "run"

#: Returned when a path leads nowhere. Distinct from `None`, which is a value a
#: node can legitimately produce — "absent" and "null" are different answers.
MISSING = object()


def resolve_path(path: str, data: Mapping[str, Any]) -> Any:
    """Follow a dotted path through nested mappings, or return MISSING.

    Shared with `conditions.py` rather than written twice. Two implementations
    of "what does `a.b.c` mean" would drift, and the day they disagree is the
    day a guard and a mapping read the same address differently — which is the
    kind of bug nobody looks for because the two features feel unrelated.
    """
    current: Any = data
    for part in path.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return MISSING
        current = current[part]
    return current


def sources(run_input: Mapping[str, Any],
            upstream: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """Everything a node may address, by origin.

    `upstream` maps a predecessor's `node_ref` to its output. A ref is unique
    per workflow (UNIQUE in 001_initial), which is what makes it usable as an
    address at all — and the reason edges name their endpoints by ref
    elsewhere too.
    """
    return {RUN_INPUT: dict(run_input), **{ref: dict(out) for ref, out in upstream.items()}}


def apply_mapping(
    mapping: Mapping[str, Any], available: Mapping[str, Any]
) -> dict[str, Any]:
    """Select and rename, `{"target": "source.path"}`.

    A path that leads nowhere is **left out**, not written as None. A node
    reading `input_data["prompt"]` should get a KeyError when nothing produced
    a prompt — not an empty string that reaches a model and comes back with an
    answer to no question.

    Only strings are treated as paths. A literal value in a mapping passes
    through, which is how a node gets a constant without a configuration entry
    of its own.
    """
    result: dict[str, Any] = {}
    for target, source in mapping.items():
        if not isinstance(source, str):
            result[target] = source
            continue
        value = resolve_path(source, available)
        if value is not MISSING:
            result[target] = value
    return result


def build_input(
    *,
    run_input: Mapping[str, Any],
    upstream: Mapping[str, Mapping[str, Any]],
    input_mapping: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """What a node receives.

    The mapped keys sit **on top of** the by-origin structure rather than
    replacing it. An executor written against the raw shape keeps working when
    someone adds a mapping, and one written against the mapping does not have
    to care that the raw shape is there.

    A mapping that shadows an origin key — `{"run": …}` — wins, and that is the
    author saying so explicitly. It is the one case where overwriting is a
    decision rather than an accident.
    """
    available = sources(run_input, upstream)
    if not input_mapping:
        return available
    return {**available, **apply_mapping(input_mapping, available)}
