"""
kernel/redaction.py

Replacing identifiers before a model sees them (CHY-63, ADR-018).

What leaves the installation, the installation cannot delete. The content of a
run expires since CHY-17; what a hosted model, the LiteLLM logs or Langfuse
received does not. So identifiers are replaced on the way out -- an e-mail
address becomes `{{EMAIL_1}}` -- and put back on the way in, after the answer
has been recorded.

Where, and why there (decided 05.11.2026)
------------------------------------------
At the boundary: immediately before every model call of `/v1/complete` and of a
workflow, in `core.completion`; the model, the gateway and the trace see
placeholders. The LangGraph pilots call the model on their own, without it, and
are deprecated (CHY-172). For `/v1/complete` the mapping lives in memory for the length of
the call and is never stored.

And at the entrance (CHY-234): a workflow run is stored with placeholders only,
its mapping sealed in `pii_mappings` (kernel/mapping_cipher.py). That builds on
this module: `redact(known=...)` keeps one numbering across all steps
of a run, `redact_structure` covers a run's whole input, `reserve` and
`identifiers_in` carry the caller's list of names through the run, `rebased`
merges what two steps of one run added at the same time, and the boundary stays
as the second line.

Every model, not only hosted ones. Langfuse records the prompt whichever model
answers, a fallback chain can move from a local model to a hosted one within a
single request, and "local" in the routing configuration does not prove that
the data stays in the house.

What stage 1 finds, and what it does not
-----------------------------------------
Deterministic, no model. ADR-018 rules that out on purpose: a model doing the
redaction would see the data it is meant to protect.

  - shipped rules: e-mail address, IBAN, phone number, German tax id, German
    health insurance number, and a date of birth *when it is labelled* as one.
    Not every date -- "Termin am 05.11." as a placeholder makes the answer
    worthless, and a date alone identifies nobody
  - identifiers the caller names: `{"PERSON": ["Erika Mustermann"]}`, the
    "name lists from document metadata" of ADR-018

A name nobody names is not found. Stage 2 (NER, CHY-235) closes that, and
SECURITY.md says it plainly until then.

The word: redaction here is reversible for the length of a call, which makes it
pseudonymisation in the sense of GDPR Art. 4(5). The ticket and ADR-018 say
redaction, and so does this module -- one word for one thing.

Dependencies: none. No database, no clock, no model.
"""
from __future__ import annotations

import json
import re
import string
from collections import Counter
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field

from kernel.errors import InvalidIdentifiers, MalformedRedactionRule

#: Category names become part of the placeholder and of the counts a trace
#: records. Upper case so a placeholder cannot be mistaken for prose, bounded
#: so a placeholder stays shorter than most things it hides.
CATEGORY_PATTERN = re.compile(r"[A-Z][A-Z0-9_]{0,31}")

#: The shortest value a caller may name. A single character would be replaced
#: wherever it stands alone: every "A" in a list, every "I" in English.
MIN_IDENTIFIER_LENGTH = 2

#: Upper bounds for what a caller may name. Each value becomes part of one
#: regular expression; without a bound a request could make that expression
#: arbitrarily expensive to build.
MAX_IDENTIFIER_LENGTH = 200
MAX_IDENTIFIERS = 500

# Double braces: nothing in Cohereny uses them for templating, and models copy
# them faithfully. Whitespace inside is tolerated on the way back -- some models
# write "{{ EMAIL_1 }}" -- because it changes nothing about which name is meant.
_PLACEHOLDER = re.compile(r"\{\{\s*([A-Z][A-Z0-9_]*_\d+)\s*\}\}")


@dataclass(frozen=True)
class Rule:
    """One way of finding an identifier.

    `group` names the part of a match that is replaced. A labelled date of birth
    keeps its label -- "geb. {{DATE_OF_BIRTH_1}}" -- because a model that no
    longer knows the date was a birth date answers worse, and the label
    identifies nobody. `accept` rejects what the pattern cannot: a digit count,
    a check digit.
    """

    category: str
    pattern: re.Pattern[str]
    group: int = 0
    accept: Callable[[str], bool] | None = None


def _digits(text: str) -> str:
    return "".join(ch for ch in text if ch.isdigit())


#: 15 characters (Norway) to 34. The IBAN pattern alone accepts twelve, which
#: would catch article numbers that merely start like an IBAN.
IBAN_LENGTHS = range(15, 35)

#: E.164 allows at most 15 digits; fewer than 7 is an extension or a house
#: number, not something that reaches a person.
PHONE_DIGIT_COUNTS = range(7, 16)

TAX_ID_DIGITS = 11


def _iban_length_ok(text: str) -> bool:
    return len(text.replace(" ", "")) in IBAN_LENGTHS


def _phone_length_ok(text: str) -> bool:
    return len(_digits(text)) in PHONE_DIGIT_COUNTS


def tax_id_check_digit_ok(text: str) -> bool:
    """The check digit of a German tax id (ISO 7064, MOD 11,10).

    Checked because eleven digits on their own are as often an order number as
    a tax id. Public so the tests can drive it with the official example.
    """
    digits = [int(ch) for ch in _digits(text)]
    if len(digits) != TAX_ID_DIGITS:
        return False
    product = 10
    for digit in digits[:-1]:
        total = (digit + product) % 10 or 10
        product = (total * 2) % 11
    # 11 - product lies between 1 and 10, and a check digit of 10 is written 0.
    return (11 - product) % 10 == digits[-1]


# German month names are the point here, not typos.
_MONTHS = (
    "Januar|Februar|März|Maerz|April|Mai|Juni|Juli|August|September|Oktober|"  # codespell:ignore
    "November|Dezember|January|February|March|May|June|July|October|December"  # codespell:ignore
)
_DATE = (
    r"\d{1,2}\.\s?\d{1,2}\.\s?(?:\d{4}|\d{2})(?!\d)"
    r"|\d{4}-\d{2}-\d{2}"
    r"|\d{1,2}/\d{1,2}/\d{4}"
    rf"|\d{{1,2}}\.?\s(?:{_MONTHS})\s\d{{4}}"
)
_BIRTH_LABEL = (
    r"geb\.-datum|geb\.|geboren(?:\s+am)?|geburtsdatum|geburtstag"
    r"|date\s+of\s+birth|born(?:\s+on)?|dob"
)

#: Applied in this order, each to what the previous one left. The specific
#: rules come before the phone rule, the loosest of them: a grouped tax id
#: ("86 095 742 719") ends in something a phone rule takes for a number with
#: an area code, and would leave "86" standing. The tax id is safe to go first
#: -- it never starts with 0, and its check digit has to fit.
SHIPPED_RULES: tuple[Rule, ...] = (
    Rule(
        "EMAIL",
        re.compile(
            r"(?<![\w.+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*"
            r"\.[A-Za-z]{2,}(?![\w-])"
        ),
    ),
    Rule(
        "IBAN",
        re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,3})?\b"),
        accept=_iban_length_ok,
    ),
    Rule(
        "DATE_OF_BIRTH",
        re.compile(rf"\b(?:{_BIRTH_LABEL})\s*:?\s*({_DATE})", re.IGNORECASE),
        group=1,
    ),
    Rule(
        "TAX_ID",
        re.compile(r"(?<!\d)[1-9]\d(?: ?\d{3}){3}(?!\d)"),
        accept=tax_id_check_digit_ok,
    ),
    Rule(
        "PHONE",
        re.compile(
            r"(?<![\w+(])"
            r"(?:(?:\+|00)[1-9]\d{0,2}[ \-/]?(?:\(0\)[ \-/]?)?|\(?0[1-9]\d{1,4}\)?[ \-/]?)"
            r"\d(?:[ \-/]?\d){4,11}(?!\w)"
        ),
        accept=_phone_length_ok,
    ),
    # Krankenversichertennummer: a letter and nine digits. Not checked against
    # its check digit -- a typo in a health insurance number still names
    # someone, and missing it costs more than replacing an article number.
    Rule("HEALTH_INSURANCE_NUMBER", re.compile(r"\b[A-Z]\d{9}\b")),
)


def identifiers_from(value: object) -> dict[str, tuple[str, ...]]:
    """What a caller named, checked and normalised.

    One place for the check, used by the request model of `/v1/complete`, by
    the start of a run and by the runner itself. `None` and `{}` mean nothing
    was named. Raises `InvalidIdentifiers` with a sentence a person can act on.
    """
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        msg = "identifiers_to_redact must be an object of category → list of values"
        raise InvalidIdentifiers(msg)
    result = {
        _checked_category(category): _checked_values(category, values)
        for category, values in value.items()
    }
    if sum(map(len, result.values())) > MAX_IDENTIFIERS:
        msg = f"at most {MAX_IDENTIFIERS} identifiers per request"
        raise InvalidIdentifiers(msg)
    return result


def _checked_category(category: object) -> str:
    if isinstance(category, str) and CATEGORY_PATTERN.fullmatch(category):
        return category
    msg = (
        f"category {category!r} must be upper case letters, digits or "
        f"underscores, starting with a letter, at most 32 characters"
    )
    raise InvalidIdentifiers(msg)


def _checked_values(category: str, values: object) -> tuple[str, ...]:
    if isinstance(values, str) or not isinstance(values, Sequence):
        msg = f"identifiers for {category} must be a list"
        raise InvalidIdentifiers(msg)
    return tuple(_checked_value(category, item) for item in values)


def _checked_value(category: str, item: object) -> str:
    text = item.strip() if isinstance(item, str) else ""
    if MIN_IDENTIFIER_LENGTH <= len(text) <= MAX_IDENTIFIER_LENGTH:
        return text
    msg = (
        f"each identifier for {category} must be text of "
        f"{MIN_IDENTIFIER_LENGTH} to {MAX_IDENTIFIER_LENGTH} characters"
    )
    raise InvalidIdentifiers(msg)


@dataclass(frozen=True)
class Restored:
    text: str
    #: Placeholders left standing: a name this call never handed out, or one
    #: the model changed ("PERSON_1" without its braces). Left as they are and
    #: counted -- a guess at what the model meant would be worse than a visible
    #: gap.
    unresolved: int = 0


@dataclass(frozen=True)
class Redaction:
    """The texts as the model sees them, and how to read its answer back."""

    texts: dict[str, str]
    #: Placeholder name → the value it stands for. At the boundary of a single
    #: call it is dropped afterwards; in a run it is the run's whole mapping,
    #: old and new, which the run seals into `pii_mappings` (CHY-234).
    replacements: dict[str, str] = field(default_factory=dict)
    #: Occurrences replaced per category. Counts, never values: this goes into
    #: the trace and the response.
    counts: dict[str, int] = field(default_factory=dict)
    #: Placeholder-shaped text that was already in the input. Not handed out
    #: again, not counted as unresolved when it comes back.
    preexisting: frozenset[str] = frozenset()

    def restore(self, text: str) -> Restored:
        """Put the values back. Single pass: a value is never searched again."""
        unresolved = 0

        def put_back(match: re.Match[str]) -> str:
            nonlocal unresolved
            name = match.group(1)
            if name in self.replacements:
                return self.replacements[name]
            if name not in self.preexisting:
                unresolved += 1
            return match.group(0)

        # A function, not a replacement string: re.sub would read a backslash
        # in a value ("C:\new") as an escape and change it.
        restored = _PLACEHOLDER.sub(put_back, text)
        if self.replacements:
            unresolved += len(_bare_names(self.replacements).findall(restored))
        return Restored(restored, unresolved)


def _bare_names(replacements: Mapping[str, str]) -> re.Pattern[str]:
    """Names this call handed out, without their double braces.

    Run after the braced ones are back, so whatever still matches here is a
    placeholder the model changed: "PERSON_1", "{PERSON_1}".
    """
    names = sorted(replacements, key=len, reverse=True)
    return re.compile(r"(?<!\w)(?:" + "|".join(map(re.escape, names)) + r")(?!\w)")


def _value_key(value: str) -> str:
    # The same person written twice, or in capitals once, is one person.
    return " ".join(value.casefold().split())


class _Numbering:
    """Hands out placeholders: one per value, numbered per category.

    `known` is a run's mapping so far (CHY-234). Its values keep their names,
    and a new value is numbered after the highest name of its category -- not
    after the count, so a gap left by nothing in particular is never filled
    with a second meaning for the same name.
    """

    def __init__(self, taken: frozenset[str], known: Mapping[str, str] | None = None) -> None:
        known = known or {}
        self._taken = taken | frozenset(known)
        self._by_value: dict[str, str] = {_value_key(v): name for name, v in known.items()}
        self._next: Counter[str] = Counter()
        for name in known:
            category, _, number = name.rpartition("_")
            if category and number.isdigit():
                self._next[category] = max(self._next[category], int(number))
        self.replacements: dict[str, str] = dict(known)
        self.counts: Counter[str] = Counter()

    def name_for(self, category: str, value: str) -> str:
        """The value's name, handed out if it has none yet. Counts nothing:
        reserving a name or moving one is not a replacement in a text."""
        key = _value_key(value)
        name = self._by_value.get(key)
        if name is None:
            self._next[category] += 1
            name = f"{category}_{self._next[category]}"
            while name in self._taken:
                self._next[category] += 1
                name = f"{category}_{self._next[category]}"
            self._by_value[key] = name
            self.replacements[name] = value
        return name

    def placeholder(self, category: str, value: str) -> str:
        """The placeholder that replaces one occurrence of the value, counted."""
        name = self.name_for(category, value)
        self.counts[category] += 1
        return "{{" + name + "}}"


def _identifier_pattern(identifiers: Mapping[str, Sequence[str]]) -> re.Pattern[str] | None:
    """One expression for every named value, longest first.

    Longest first so "Erika Mustermann" is one placeholder, not "Erika" plus a
    second one for "Mustermann". Whitespace inside a value matches any run of
    whitespace: a name broken across a line is still the name.
    """
    values = sorted(
        {v for vs in identifiers.values() for v in vs}, key=len, reverse=True
    )
    if not values:
        return None
    parts = (r"\s+".join(map(re.escape, v.split())) for v in values)
    return re.compile(r"(?<!\w)(?:" + "|".join(parts) + r")(?!\w)", re.IGNORECASE)


def _category_of(identifiers: Mapping[str, Sequence[str]]) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for category, values in identifiers.items():
        for value in values:
            lookup.setdefault(" ".join(value.casefold().split()), category)
    return lookup


def _replace_identifiers(
    text: str, identifiers: Mapping[str, Sequence[str]], numbering: _Numbering
) -> str:
    pattern = _identifier_pattern(identifiers)
    if pattern is None:
        return text
    category_of = _category_of(identifiers)
    # Reached only where IGNORECASE and casefold() disagree about a letter; the
    # value is replaced either way, under the first category named.
    fallback = next(iter(identifiers))

    def substitute(match: re.Match[str]) -> str:
        found = match.group(0)
        category = category_of.get(" ".join(found.casefold().split()), fallback)
        return numbering.placeholder(category, found)

    return pattern.sub(substitute, text)


def _replace_rule(text: str, rule: Rule, numbering: _Numbering) -> str:
    def substitute(match: re.Match[str]) -> str:
        found = match.group(rule.group)
        # An empty match is never replaced: re.sub would put a placeholder
        # between every two characters of a prompt.
        if not found or (rule.accept is not None and not rule.accept(found)):
            return match.group(0)
        start, end = match.span(rule.group)
        offset = match.start(0)
        whole = match.group(0)
        token = numbering.placeholder(rule.category, found)
        return whole[: start - offset] + token + whole[end - offset:]

    return rule.pattern.sub(substitute, text)


def redact(
    texts: Mapping[str, str],
    *,
    identifiers: Mapping[str, Sequence[str]] | None = None,
    rules: Iterable[Rule] = SHIPPED_RULES,
    known: Mapping[str, str] | None = None,
) -> Redaction:
    """Replace identifiers in several texts with one shared numbering.

    Shared, because the system message and the prompt of one call are read
    together: "Erika Mustermann" in both has to be the same `{{PERSON_1}}`, or
    the model sees two people. Named identifiers go first, then the rules in
    order.

    `known` is the mapping of a run so far (CHY-234). The run is stored with
    placeholders, so the same person has to keep the same name from the first
    step to the last: a value it knows keeps its placeholder, a new one is
    numbered after it, and `replacements` returns the whole mapping, old and
    new -- the one the run seals again.
    """
    rules = tuple(rules)
    identifiers = identifiers or {}
    taken = frozenset(
        m.group(1) for text in texts.values() for m in _PLACEHOLDER.finditer(text)
    )
    numbering = _Numbering(taken, known)
    result: dict[str, str] = {}
    for key, original in texts.items():
        redacted = _replace_identifiers(original, identifiers, numbering)
        for rule in rules:
            redacted = _replace_rule(redacted, rule, numbering)
        result[key] = redacted
    return Redaction(
        texts=result,
        replacements=dict(numbering.replacements),
        counts=dict(numbering.counts),
        preexisting=taken,
    )


def redact_structure(
    value: object,
    *,
    identifiers: Mapping[str, Sequence[str]] | None = None,
    rules: Iterable[Rule] = SHIPPED_RULES,
    known: Mapping[str, str] | None = None,
) -> tuple[object, Redaction]:
    """Replace identifiers in every text of a JSON-like value (CHY-234).

    A run's input is an object with whatever fields its caller chose, nested as
    deep as they like. Every text in it is replaced, not only the fields
    someone declared: a field nobody thought of would be stored with its
    values (decided 06.11.2026, E2). One numbering across all of them, as in
    `redact`.

    Keys stay as they are -- they name fields, they do not carry content -- and
    so do numbers, booleans and null. Returns the value rebuilt with
    placeholders, and the `Redaction` whose `replacements` is the mapping; its
    `texts` are keyed by position and of no use outside this function.
    """
    leaves: list[str] = []
    _collect_texts(value, leaves)
    redaction = redact(
        {str(i): text for i, text in enumerate(leaves)},
        identifiers=identifiers, rules=rules, known=known,
    )
    replaced = iter([redaction.texts[str(i)] for i in range(len(leaves))])
    return _with_texts(value, replaced), redaction


def _collect_texts(item: object, into: list[str]) -> None:
    """Every text in a JSON-like value, depth first, in insertion order."""
    if isinstance(item, str):
        into.append(item)
    elif isinstance(item, Mapping):
        for child in item.values():
            _collect_texts(child, into)
    elif isinstance(item, (list, tuple)):
        for child in item:
            _collect_texts(child, into)


def _with_texts(item: object, texts: Iterator[str]) -> object:
    """The same value with its texts taken from `texts`, in the order
    `_collect_texts` found them -- the same walk, so dict order decides both."""
    if isinstance(item, str):
        return next(texts)
    if isinstance(item, Mapping):
        return {key: _with_texts(child, texts) for key, child in item.items()}
    if isinstance(item, (list, tuple)):
        return [_with_texts(child, texts) for child in item]
    return item


def reserve(
    identifiers: Mapping[str, Sequence[str]],
    *,
    known: Mapping[str, str] | None = None,
    taken: frozenset[str] = frozenset(),
) -> dict[str, str]:
    """The mapping with a name for every value the caller listed, found or not (CHY-234).

    A run's list of names is not stored with its input -- the list itself is
    what it protects. It goes into the mapping instead, and every later step
    searches for what the mapping holds (`identifiers_in`): a name the caller
    listed is replaced wherever a model writes it, also when the input never
    contained it. `taken` are placeholder-shaped texts already in the input,
    which no name may duplicate.
    """
    numbering = _Numbering(taken, known)
    for category, values in identifiers.items():
        for value in values:
            numbering.name_for(category, value)
    return dict(numbering.replacements)


def identifiers_in(mapping: Mapping[str, str]) -> dict[str, tuple[str, ...]]:
    """Every value a run's mapping holds, by category: what each step searches for.

    Listed by the caller or found by a pattern, a value the run knows is looked
    for as itself in every later step -- so a model that writes an address back
    in a form no pattern matches still meets the name it already has.
    """
    found: dict[str, list[str]] = {}
    for name, value in mapping.items():
        found.setdefault(name.rpartition("_")[0], []).append(value)
    return {category: tuple(values) for category, values in found.items()}


def rebased(
    started_from: Mapping[str, str],
    returned: Mapping[str, str],
    current: Mapping[str, str],
    *,
    taken: frozenset[str] = frozenset(),
) -> tuple[dict[str, str], dict[str, str]]:
    """Merge what one step added onto the run's mapping as it is now (CHY-234).

    A step numbers new values after the mapping it started from. When another
    step of the same run recorded first, the two may have handed out the same
    name for different values, or different names for the same one. Merged by
    value, under the row lock the caller holds: a value the run knows by now
    keeps that name, a new one is numbered after the highest name of its
    category, and never as one of `taken`.

    Returns the merged mapping and the renames -- old name to new -- that the
    step's output needs, empty when nobody recorded in between.
    """
    numbering = _Numbering(taken, current)
    renames: dict[str, str] = {}
    for name, value in returned.items():
        if name in started_from:
            continue
        final = numbering.name_for(name.rpartition("_")[0], value)
        if final != name:
            renames[name] = final
    return dict(numbering.replacements), renames


def placeholders_in(value: object) -> frozenset[str]:
    """Every placeholder name in the texts of a JSON-like value."""
    texts: list[str] = []
    _collect_texts(value, texts)
    return frozenset(m.group(1) for text in texts for m in _PLACEHOLDER.finditer(text))


def renamed(value: object, renames: Mapping[str, str]) -> object:
    """The value with every placeholder in `renames` replaced by its new name.

    One pass per text, so a rename never meets the result of another
    ("PERSON_2" → "PERSON_3" and "PERSON_3" → "PERSON_4" in one call).
    """
    if not renames:
        return value

    def move(match: re.Match[str]) -> str:
        name = match.group(1)
        return "{{" + renames[name] + "}}" if name in renames else match.group(0)

    texts: list[str] = []
    _collect_texts(value, texts)
    return _with_texts(value, iter([_PLACEHOLDER.sub(move, text) for text in texts]))


def resolved(value: object, mapping: Mapping[str, str]) -> object:
    """The texts of a JSON-like value with the run's values put back (CHY-234).

    For a person allowed to see them (E4, decided 06.11.2026) -- never on the
    way to a model. One pass per text, as `Redaction.restore`: a value is never
    searched again, and a placeholder the mapping does not know stays standing.
    """
    restorer = Redaction(texts={}, replacements=dict(mapping))
    texts: list[str] = []
    _collect_texts(value, texts)
    return _with_texts(value, iter([restorer.restore(text).text for text in texts]))


def unresolved_in(text: str, mapping: Mapping[str, str]) -> int:
    """Placeholders in `text` the mapping cannot answer for.

    The count `Redaction.restore` reports, for text that is kept with its
    placeholders instead of being restored (CHY-234): a name the run never
    handed out, or one the model changed ("PERSON_1" without its braces).
    """
    return Redaction(texts={}, replacements=dict(mapping)).restore(text).unresolved


# ── rules a scope adds (CHY-63 part 2) ────────────────────────────────────────
#
# A scope adds rules as `redaction_rule` blocks (migration 032): reviewed,
# versioned, inherited down the tree like routing rules. Decided 05.11.2026 over
# a table with an admin API, because what decides which data leaves belongs in
# the same review as what decides where it goes.
#
# Three shapes, one per block, all in `metadata`:
#
#     {"category": "STAFF", "texts": ["Dr. Schmidt"]}
#     {"category": "CASE_NUMBER", "pattern": "\\b\\d+ [A-Z] \\d+/\\d{2}\\b"}
#     {"disable": ["PHONE"]}
#
# Rules add up; nothing a scope above added can be taken away below. Switching a
# shipped rule off is the one loosening, allowed only at the top of the tree and
# only by a block that says so (see `check_rule_block`).

#: The governance key a block declares when it switches a shipped rule off.
#: Seeded in migration 016, given its meaning in 032.
GOVERNANCE_KEY = "pii_redaction"

# The ratchet's word for loosening. Spelled here rather than imported: the
# kernel imports no context, and migration 016's CHECK holds the vocabulary.
_PERMISSIVE = "permissive"

#: Longest pattern a scope may add. Every rule runs over every prompt of the
#: scope and of every scope below it.
MAX_PATTERN_LENGTH = 300

SHIPPED_CATEGORIES: frozenset[str] = frozenset(r.category for r in SHIPPED_RULES)

_SHAPES = ("texts", "pattern", "disable")

# For the backtracking check: escapes and character classes are literal text,
# and a parenthesis inside them opens no group. `(?:`, `(?=`, `(?P<name>` and
# the like open a group too, and their `?` is syntax, not a quantifier.
_ESCAPE = re.compile(r"\\.")
_CHARACTER_CLASS = re.compile(r"\[[^\]]*\]")
_GROUP_SYNTAX = re.compile(r"\(\?(?:[:=!]|<[=!]|P?<\w+>|[aiLmsux-]+:?)")
# What repeats a group, and what inside a group varies in length.
_GROUP_REPETITION = re.compile(r"[*+]|\{\d*(?:,\d*)?\}")
_VARIABLE_INSIDE = re.compile(r"[*+?|]|\{\d*,\d*\}")

# Text a pattern is tried on to find out whether it can match nothing at all:
# every printable ASCII character, so a lookahead such as `(?=x)` finds its
# condition. `_replace_rule` skips an empty match anyway; this check is there
# so the writer hears about it at approval instead of wondering why the rule
# never replaces anything.
_PROBE_TEXT = string.printable


@dataclass(frozen=True)
class ScopeRule:
    """What one `redaction_rule` block says. Exactly one of the three is set."""

    category: str | None = None
    texts: tuple[str, ...] = ()
    rule: Rule | None = None
    disabled: frozenset[str] = frozenset()


@dataclass(frozen=True)
class RedactionPolicy:
    """The rules a scope chain asks for, ready for `redact`."""

    rules: tuple[Rule, ...] = SHIPPED_RULES
    identifiers: Mapping[str, tuple[str, ...]] = field(default_factory=dict)


def _as_mapping(metadata: object) -> object:
    # asyncpg hands JSONB over as str on some versions and as dict on others.
    return json.loads(metadata) if isinstance(metadata, str) else metadata


def _checked_pattern(pattern: object) -> re.Pattern[str]:
    if not isinstance(pattern, str) or not 1 <= len(pattern) <= MAX_PATTERN_LENGTH:
        msg = f"pattern must be text of 1 to {MAX_PATTERN_LENGTH} characters"
        raise ValueError(msg)
    try:
        compiled = re.compile(pattern)
    except re.error as exc:
        msg = f"pattern does not compile: {exc}"
        raise ValueError(msg) from exc
    if any(m.start() == m.end() for m in compiled.finditer(_PROBE_TEXT)) or (
        compiled.fullmatch("") is not None
    ):
        # re.sub replaces an empty match too: a placeholder between every
        # character, and a prompt nobody can read.
        msg = "pattern can match empty text"
        raise ValueError(msg)
    if _backtracks_badly(pattern):
        msg = (
            "a repeated group must have an upper bound — (…){1,5}, not (…)+ — "
            "and may contain only fixed counts such as \\d{3}: no +, *, ? "
            "and no alternatives inside it"
        )
        raise ValueError(msg)
    return compiled


def _backtracks_badly(pattern: str) -> bool:
    """Whether a repeated group could make `re` backtrack for minutes.

    `(a+)+`, `(a|aa)*`, `(\\d+){1,1000}`: a group that repeats and varies in
    length inside gives the engine more ways to split a text than there are
    atoms in the universe, and it tries them all before it gives up. Python's
    re has no timeout, and a scope's rule runs over every prompt of the scope:
    one such rule holds every model call there.

    Conservative on purpose. It refuses a few patterns that would have been
    harmless, and the message says how to write them instead.
    """
    text = _GROUP_SYNTAX.sub("(", _CHARACTER_CLASS.sub("x", _ESCAPE.sub("x", pattern)))
    opened: list[int] = []
    for i, char in enumerate(text):
        if char == "(":
            opened.append(i)
        elif char == ")" and opened:
            start = opened.pop()
            repetition = _GROUP_REPETITION.match(text, i + 1)
            if repetition is None:
                continue
            unbounded = repetition.group(0) in ("*", "+") or repetition.group(0).endswith(",}")
            if unbounded or _VARIABLE_INSIDE.search(text, start + 1, i):
                return True
    return False


def _checked_disable(value: object) -> frozenset[str]:
    if isinstance(value, str) or not isinstance(value, Sequence) or not value:
        msg = "disable must be a non-empty list of shipped rules"
        raise ValueError(msg)
    unknown = sorted({str(v) for v in value} - SHIPPED_CATEGORIES)
    if unknown:
        msg = (
            f"no shipped rule named {', '.join(unknown)}; shipped rules: "
            f"{', '.join(r.category for r in SHIPPED_RULES)}"
        )
        raise ValueError(msg)
    return frozenset(value)


def _parsed(metadata: object) -> ScopeRule:
    meta = _as_mapping(metadata)
    if not isinstance(meta, Mapping):
        msg = "metadata must be an object"
        raise TypeError(msg)
    shapes = [shape for shape in _SHAPES if shape in meta]
    if len(shapes) != 1:
        msg = "metadata names exactly one of texts, pattern, disable"
        raise ValueError(msg)
    shape = shapes[0]
    if shape == "disable":
        return ScopeRule(disabled=_checked_disable(meta["disable"]))
    category = _checked_category(meta.get("category"))
    if shape == "texts":
        texts = _checked_values(category, meta["texts"])
        if not texts:
            msg = f"texts for {category} must name at least one value"
            raise ValueError(msg)
        return ScopeRule(category=category, texts=texts)
    return ScopeRule(category=category, rule=Rule(category, _checked_pattern(meta["pattern"])))


def scope_rule_from(row: Mapping[str, object]) -> ScopeRule:
    """Read a `redaction_rule` block. Raises `MalformedRedactionRule`."""
    try:
        return _parsed(row["metadata"])
    except (ValueError, TypeError) as exc:
        # InvalidIdentifiers is a ValueError too: a bad category or a bad text
        # in a block is the same mistake as in a request.
        raise MalformedRedactionRule(row["block_id"], str(exc)) from exc


def _declares_loosening(row: Mapping[str, object]) -> bool:
    return row["governance_key"] == GOVERNANCE_KEY and row["privacy_impact"] == _PERMISSIVE


def check_rule_block(row: Mapping[str, object], *, top_level: bool) -> None:
    """Refuse a `redaction_rule` block that may not become active.

    Run before activation, on every path that activates (approve, and writing
    in a direct scope). Three fences around the one loosening, so that it can
    neither happen quietly nor below the top of the tree:

      1. switching a shipped rule off stands only in a scope without a parent;
         below, nothing is taken away (decided 05.11.2026)
      2. it declares `pii_redaction` as `permissive`. That makes it a data-flow
         block, approved by someone who did not write it (ADR-045)
      3. the ratchet refuses it where a `restrictive` claim on the key stands
         in the same scope (ADR-017) -- checked by the repository, not here

    And the converse: a rule that adds protection may not call itself
    permissive. It would send a tightening through four eyes for nothing and
    read, in the audit, like a loosening.

    Raises `MalformedRedactionRule`; the caller turns it into the approver's
    error.
    """
    rule = scope_rule_from(row)
    if rule.disabled and not top_level:
        raise MalformedRedactionRule(
            row["block_id"],
            "a shipped rule can be switched off only in a scope without a "
            "parent; below it, scopes add rules and take none away",
        )
    if rule.disabled and not _declares_loosening(row):
        raise MalformedRedactionRule(
            row["block_id"],
            f"switching a shipped rule off is a loosening: declare "
            f"governance_key {GOVERNANCE_KEY!r} and privacy_impact "
            f"{_PERMISSIVE!r}, which sends it to a second person (ADR-045)",
        )
    if not rule.disabled and row["privacy_impact"] == _PERMISSIVE:
        raise MalformedRedactionRule(
            row["block_id"],
            "a rule that adds protection is not a loosening; leave "
            "privacy_impact unset or restrictive",
        )


def policy_from(
    rows: Iterable[Mapping[str, object]], scope_order: Sequence[object]
) -> RedactionPolicy:
    """The rules a scope chain asks for: shipped, minus what the top switched
    off, plus everything the chain adds.

    `scope_order` is the chain root first. Rows are read in that order, so a
    rule's position -- and with it which category catches an overlap -- does
    not depend on the order the database returns them in.

    A row that cannot be read raises `MalformedRedactionRule` rather than being
    skipped: a rule meant to protect something and silently left out protects
    nothing. A switch-off that does not meet `check_rule_block` is ignored --
    in that direction, ignoring it is the safe mistake.
    """
    position = {scope_id: i for i, scope_id in enumerate(scope_order)}
    root = scope_order[0] if scope_order else None
    disabled: set[str] = set()
    added: list[Rule] = []
    identifiers: dict[str, tuple[str, ...]] = {}
    ordered = sorted(rows, key=lambda r: (position.get(r["scope_id"], len(position)),
                                          str(r["created_at"]), str(r["block_id"])))
    for row in ordered:
        rule = scope_rule_from(row)
        if rule.disabled:
            if row["scope_id"] == root and _declares_loosening(row):
                disabled |= rule.disabled
        elif rule.rule is not None:
            added.append(rule.rule)
        elif rule.category is not None:
            identifiers[rule.category] = identifiers.get(rule.category, ()) + rule.texts
    shipped = tuple(r for r in SHIPPED_RULES if r.category not in disabled)
    return RedactionPolicy(rules=shipped + tuple(added), identifiers=identifiers)


def merged(*sources: Mapping[str, Sequence[str]]) -> dict[str, tuple[str, ...]]:
    """Identifiers from several places -- the scope's rules, the caller -- as one."""
    result: dict[str, tuple[str, ...]] = {}
    for source in sources:
        for category, values in source.items():
            result[category] = result.get(category, ()) + tuple(values)
    return result
