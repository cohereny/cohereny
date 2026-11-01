"""
contexts/learning/validators.py

Scoring a model's output against a calibration anchor (ADR-006, CHY-67).

Pure functions: an output and an anchor go in, numbers come out. No model, no
database, no clock — the same output scores the same tomorrow, which is the
only way a drop in the score can mean the model changed rather than the ruler.

Every validator yields a value in [0, 1]. The score is their mean, rounded to
the four places `benchmark_measurements.score` stores. The parts are kept, so a
drop can be traced to the rule that moved.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from jsonschema.validators import validator_for

from contexts.learning.anchor import Validators

#: Places kept: benchmark_measurements.score is NUMERIC(5, 4).
_PLACES = 4

_WORD = re.compile(r"\w+")


@dataclass(frozen=True)
class Score:
    """The mean and the parts it is the mean of."""

    total: float
    parts: dict[str, float]


def score(validators: Validators, reference: str, output: str) -> Score:
    """Score one output. Validators that are not set do not take part."""
    parts: dict[str, float] = {}
    if validators.json_schema is not None:
        parts["json_schema"] = json_schema(output, validators.json_schema)
    if validators.regex:
        parts["regex"] = regex(output, validators.regex)
    if validators.length is not None:
        parts["length"] = length(output, validators.length.min_chars, validators.length.max_chars)
    if validators.required_terms:
        parts["required_terms"] = required_terms(output, validators.required_terms)
    if validators.forbidden_terms:
        parts["forbidden_terms"] = forbidden_terms(output, validators.forbidden_terms)
    if validators.rouge_l:
        parts["rouge_l"] = rouge_l(output, reference)

    if not parts:
        # Unreachable through parse(): rouge_l is on by default and needs a
        # reference. Refused here anyway — a mean of nothing would have to be
        # invented, and an invented number is worse than an error.
        msg = "no validator applies — nothing to score"
        raise ValueError(msg)
    parts = {name: round(value, _PLACES) for name, value in parts.items()}
    return Score(total=round(sum(parts.values()) / len(parts), _PLACES), parts=parts)


def json_schema(output: str, schema: dict) -> float:
    """1 if the output is JSON that conforms to the schema, else 0."""
    try:
        document = json.loads(output)
    except (ValueError, TypeError):
        return 0.0
    return 1.0 if validator_for(schema)(schema).is_valid(document) else 0.0


def regex(output: str, patterns: list[str]) -> float:
    """The share of patterns found anywhere in the output."""
    return sum(1 for p in patterns if re.search(p, output)) / len(patterns)


def length(output: str, min_chars: int | None, max_chars: int | None) -> float:
    """1 if the output's length is within the bounds, else 0.

    Characters of the stripped output: leading and trailing whitespace is not
    content, and counting it would let a model pad its way into the bounds.
    """
    n = len(output.strip())
    if min_chars is not None and n < min_chars:
        return 0.0
    if max_chars is not None and n > max_chars:
        return 0.0
    return 1.0


def required_terms(output: str, terms: list[str]) -> float:
    """The share of terms present, case-insensitive."""
    folded = output.casefold()
    return sum(1 for t in terms if t.casefold() in folded) / len(terms)


def forbidden_terms(output: str, terms: list[str]) -> float:
    """1 if none of the terms occurs, else 0.

    All or nothing, not a share: one forbidden term in an answer is the
    failure, and a partial score would let five clean terms outweigh it.
    """
    folded = output.casefold()
    return 0.0 if any(t.casefold() in folded for t in terms) else 1.0


def rouge_l(output: str, reference: str) -> float:
    """ROUGE-L F1 between output and reference, over lowercase words.

    The longest common subsequence of the two word sequences: words in the
    same order, gaps allowed. Precision is LCS over the output's length, recall
    LCS over the reference's, F1 their harmonic mean. Written out instead of
    pulling in a package that brings tokenisers and stemmers along — the
    measure is twenty lines, and a dependency would be a second ruler that can
    change under the numbers with an update.
    """
    candidate = _WORD.findall(output.casefold())
    target = _WORD.findall(reference.casefold())
    if not candidate or not target:
        return 0.0
    lcs = _lcs_length(candidate, target)
    if lcs == 0:
        return 0.0
    precision = lcs / len(candidate)
    recall = lcs / len(target)
    return 2 * precision * recall / (precision + recall)


def _lcs_length(a: list[str], b: list[str]) -> int:
    """Length of the longest common subsequence, two rows of memory."""
    if len(a) < len(b):
        a, b = b, a
    previous = [0] * (len(b) + 1)
    for word in a:
        current = [0]
        for j, other in enumerate(b, start=1):
            current.append(previous[j - 1] + 1 if word == other
                           else max(previous[j], current[j - 1]))
        previous = current
    return previous[-1]
