"""
core/ingress.py

Looking at foreign text before it reaches a model (CHY-135).

A document that a stranger uploaded is data. A prompt is instructions. For a
language model the two are the same tokens, which is why "tell the model to
ignore instructions in the content" does not work: that countermeasure attacks
on the same level as the attack, and the attacker writes the last sentence.

What this module does instead is smaller and honest about it.

**The fence is the mechanism, the patterns are the alarm.**

`fence()` puts foreign text between markers and says, in the prompt, that what
lies between them is data. That is not a guarantee on its own — a model may
still obey what it reads inside. It becomes one useful guarantee in combination
with `scan()`, which reports *exactly* whether the text contains the marker
itself. Breaking out of a fence requires reproducing the fence, and that is not
a heuristic: either the bytes are there or they are not.

The pattern list is the other half and it is a heuristic. It raises the cost of
the careless attack and — more to the point of this system — it leaves a
**record** that something instruction-shaped arrived. An auditor asking "did
anything suspicious enter this run" gets an answer instead of a shrug. That is
the same shape as the retrieval provenance in ADR-036: the value is evidence,
not prevention.

**The boundary, stated so it does not have to be discovered:** this recognises
patterns, not intentions. Anyone who knows the list can write around it. A
finding is a reason to look, never proof of an attack — and no finding is not
proof of safety.

Nothing here writes anywhere. The caller decides what a finding means, because
that decision differs: a pilot run records it, a workflow can route it to a
human gate, and a batch job may refuse. Deciding here would make the strictest
caller's policy everyone's.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

#: The marker foreign text is wrapped in. Deliberately unusual: it has to be
#: something a real bank document does not contain by accident, because every
#: occurrence in the content is reported as a fence break.
FENCE_OPEN = "<<<COHERENY:UNTRUSTED_BEGIN>>>"
FENCE_CLOSE = "<<<COHERENY:UNTRUSTED_END>>>"

#: How much of the hit is quoted in a finding. Enough to judge it, short enough
#: that the record does not become a copy of the document — which would put the
#: very content under suspicion into a second place.
EXCERPT_CHARS = 120

#: Instruction-shaped phrases, German and English.
#:
#: PHRASES, not words, and that distinction is the difference between a tool
#: and a nuisance here. The banking pilot reads German financial documents, in
#: which "Anweisung" is a payment order, "System" is an IT system and
#: "Anleitung" is a manual. A word list would fire on every second document and
#: teach everyone to ignore the finding — which is worse than not having it.
#:
#: Each entry has to contain a verb aimed at the reader AND an object that only
#: makes sense if the reader is a model.
INSTRUCTION_PATTERNS: tuple[tuple[str, str], ...] = (
    ((r"ignorier\w*\s+(alle\s+)?(bisherigen?|vorherigen?|obigen?)\s+"  # codespell:ignore
      r"(anweisungen|befehle|instruktionen)"), "ignore previous instructions (de)"),  # codespell:ignore
    ((r"vergiss\s+(alle\s+)?(bisherigen?|vorherigen?|obigen?)\s+"  # codespell:ignore
      r"(anweisungen|befehle)"), "forget previous instructions (de)"),  # codespell:ignore
    # `all` and `the` are both optional and independent: "ignore all previous",
    # "ignore the above" and "disregard the above" are all real phrasings, and
    # a single alternation of qualifiers matches only the first two. The
    # reachability test below found this — the pattern had an example it did
    # not match, which is a rule that says nothing.
    ((r"ignore\s+(all\s+)?(the\s+)?(previous|prior|above|earlier|foregoing)\s+"
      r"(instructions?|prompts?|rules?)"), "ignore previous instructions (en)"),
    ((r"disregard\s+(all\s+)?(the\s+)?(previous|prior|above|earlier|foregoing)\s+"
      r"(instructions?|prompts?|rules?)"), "disregard previous instructions (en)"),
    (r"du\s+bist\s+(ab\s+)?(jetzt|nun)\s+ein", "role reassignment (de)"),
    (r"you\s+are\s+now\s+a", "role reassignment (en)"),
    (r"deine\s+neue\s+aufgabe\s+ist", "task reassignment (de)"),  # codespell:ignore
    (r"your\s+new\s+(task|instruction|role)\s+is", "task reassignment (en)"),
    # Chat role markers in a document have no legitimate reading: the document
    # is not a conversation, and a model may well treat them as turn boundaries.
    (r"<\|im_(start|end)\|>", "chat template marker"),
    (r"\[/?INST\]", "instruction template marker"),
    (r"^\s*(system|assistant)\s*:", "role marker at line start"),
)

_COMPILED = tuple(
    (re.compile(pattern, re.IGNORECASE | re.MULTILINE), name)
    for pattern, name in INSTRUCTION_PATTERNS
)


@dataclass(frozen=True)
class Finding:
    """One place in the text worth looking at.

    `kind` separates the two halves of this module, and the separation matters
    to whoever reads the record: `fence_break` is a fact, `instruction_shape`
    is a suspicion. Treating them the same would either make every heuristic
    hit look like proof or make the one exact check look like a guess.
    """

    kind: str  # "fence_break" | "instruction_shape"
    rule: str
    offset: int
    excerpt: str


def _excerpt(text: str, start: int, end: int) -> str:
    """The hit with a little context, on one line."""
    start_at = max(0, start - 20)
    end_at = min(len(text), end + 20)
    return " ".join(text[start_at:end_at].split())[:EXCERPT_CHARS]


def scan(text: str) -> tuple[Finding, ...]:
    """Everything worth looking at, in the order it appears.

    Returns findings and nothing else — no verdict, no filtering, no exception.
    A caller that wants to refuse can; a caller that wants to record and carry
    on can too. Both are legitimate, and which is right depends on what the run
    is for.
    """
    findings: list[Finding] = []

    for marker in (FENCE_OPEN, FENCE_CLOSE):
        for hit in re.finditer(re.escape(marker), text):
            findings.append(Finding(
                kind="fence_break",
                rule="content contains the untrusted-content marker",
                offset=hit.start(),
                excerpt=_excerpt(text, hit.start(), hit.end()),
            ))

    for pattern, name in _COMPILED:
        for hit in pattern.finditer(text):
            findings.append(Finding(
                kind="instruction_shape",
                rule=name,
                offset=hit.start(),
                excerpt=_excerpt(text, hit.start(), hit.end()),
            ))

    return tuple(sorted(findings, key=lambda f: f.offset))


def fence(text: str) -> str:
    """Foreign text between markers, ready to be put in a prompt.

    Does not sanitise and does not claim to. It makes the boundary explicit so
    that a model has something to hold on to, and — the part that is not a
    hope — it makes an escape *detectable*: `scan` reports the marker appearing
    in the content, exactly, before this is ever called.

    Callers scan first and fence second. Fencing text that was never scanned
    gives the appearance of a control without one, which is the failure this
    project keeps finding elsewhere.
    """
    return f"{FENCE_OPEN}\n{text}\n{FENCE_CLOSE}"


def summarise(findings: tuple[Finding, ...]) -> str:
    """One line for an audit log.

    Says how many and of which kind, not the excerpts: a log line that repeats
    the suspicious content puts it somewhere new, and the excerpts are in the
    findings for whoever asks.
    """
    if not findings:
        return "Ingress-Prüfung: kein Befund"
    breaks = sum(1 for f in findings if f.kind == "fence_break")
    pattern = len(findings) - breaks
    parts = []
    if breaks:
        parts.append(f"{breaks} Marker im Inhalt")
    if pattern:
        parts.append(f"{pattern} anweisungsförmige Stellen")
    return "Ingress-Prüfung: " + ", ".join(parts)
