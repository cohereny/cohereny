"""
tests/test_redaction.py

Replacing identifiers before a model sees them (CHY-63, ADR-018).

Every rule is shown twice: catching what it claims to catch, and leaving alone
the near-miss that would make it useless if it fired. A redaction that turns
every date into a placeholder protects nothing worth protecting and ruins the
answer, and the first person to notice switches it off (CHY-102: no protection
without a demonstrated failure case -- and no rule without its near-miss).
"""
from __future__ import annotations

import re

import pytest

from kernel.errors import InvalidIdentifiers
from kernel.redaction import (
    MAX_IDENTIFIERS,
    SHIPPED_RULES,
    Rule,
    identifiers_from,
    identifiers_in,
    placeholders_in,
    rebased,
    redact,
    redact_structure,
    renamed,
    reserve,
    resolved,
    tax_id_check_digit_ok,
    unresolved_in,
)


def _prompt(text: str, **kw) -> str:
    return redact({"prompt": text}, **kw).texts["prompt"]


# ── shipped rules: hit and near-miss ──────────────────────────────────────────

class TestEmail:
    def test_an_address_is_replaced(self):
        assert _prompt("Antwort an erika.mustermann@example.de bitte") == (
            "Antwort an {{EMAIL_1}} bitte"
        )

    def test_the_full_stop_after_an_address_stays(self):
        assert _prompt("Schreib an a.b@example.de.") == "Schreib an {{EMAIL_1}}."

    @pytest.mark.parametrize("text", ["@erika im Chat", "a@b ohne Domain", "x @ y.de"])
    def test_what_only_looks_like_an_address_stays(self, text: str):
        assert _prompt(text) == text


class TestIban:
    @pytest.mark.parametrize("iban", [
        "DE89 3704 0044 0532 0130 00",
        "DE89370400440532013000",
        "NO9386011117947",
    ])
    def test_an_iban_is_replaced_grouped_or_not(self, iban: str):
        assert _prompt(f"Konto {iban} belasten") == "Konto {{IBAN_1}} belasten"

    def test_a_code_shorter_than_any_iban_stays(self):
        # Twelve characters fit the pattern; no country issues an IBAN that
        # short. This is the article number the length check exists for.
        assert _prompt("Artikel AB12 CDEF GHIJ") == "Artikel AB12 CDEF GHIJ"


class TestPhone:
    @pytest.mark.parametrize("number", [
        "+49 69 1234567",
        "+49 (0)69 1234567",
        "0049 69 1234567",
        "069 1234567",
        "069/1234567",
        "(069) 123 45 67",
        "0170-1234567",
        "06151 12345",
    ])
    def test_a_phone_number_is_replaced(self, number: str):
        assert _prompt(f"Ruf {number} an.") == "Ruf {{PHONE_1}} an."

    @pytest.mark.parametrize("text", [
        "Termin am 05.11.2026",
        "Stichtag 2026-11-05",
        "01067 Dresden",
        "Preis 0,99 €",
        "Durchwahl 0800",
        "Vorgang 012345678901234567",
        "von 08-12 Uhr",
    ])
    def test_dates_postcodes_amounts_and_long_numbers_stay(self, text: str):
        assert _prompt(text) == text


class TestTaxId:
    def test_the_official_example_is_valid(self):
        assert tax_id_check_digit_ok("86095742719")

    def test_one_wrong_digit_fails_the_check(self):
        assert not tax_id_check_digit_ok("86095742718")

    @pytest.mark.parametrize("tax_id", ["86095742719", "86 095 742 719"])
    def test_a_tax_id_is_replaced(self, tax_id: str):
        assert _prompt(f"Steuer-ID {tax_id}") == "Steuer-ID {{TAX_ID_1}}"

    def test_eleven_digits_with_a_wrong_check_digit_stay(self):
        # An order number of the same length. Without the check digit every
        # eleven-digit number would be taken for a tax id.
        assert _prompt("Bestellung 86095742718") == "Bestellung 86095742718"


class TestHealthInsuranceNumber:
    def test_a_health_insurance_number_is_replaced(self):
        assert _prompt("KVNR A123456789") == "KVNR {{HEALTH_INSURANCE_NUMBER_1}}"

    @pytest.mark.parametrize("text", ["A12345678", "AB123456789", "A1234567890"])
    def test_other_lengths_stay(self, text: str):
        assert _prompt(text) == text


class TestDateOfBirth:
    @pytest.mark.parametrize(("text", "expected"), [
        ("geb. 01.02.1960", "geb. {{DATE_OF_BIRTH_1}}"),
        ("Geburtsdatum: 1960-02-01", "Geburtsdatum: {{DATE_OF_BIRTH_1}}"),
        ("geboren am 1. Februar 1960", "geboren am {{DATE_OF_BIRTH_1}}"),  # codespell:ignore
        ("DOB: 02/01/1960", "DOB: {{DATE_OF_BIRTH_1}}"),
    ])
    def test_a_labelled_birth_date_loses_the_date_and_keeps_the_label(
        self, text: str, expected: str
    ):
        assert _prompt(text) == expected

    @pytest.mark.parametrize("text", [
        "Termin am 05.11.2026",
        "Rechnung vom 1. Februar 1960",  # codespell:ignore
        "Ausgabe geb. 12,90 €",
    ])
    def test_a_date_without_the_label_stays(self, text: str):
        assert _prompt(text) == text


# ── identifiers the caller names ──────────────────────────────────────────────

class TestNamedIdentifiers:
    def test_a_named_value_is_replaced_whatever_its_case(self):
        out = _prompt(
            "Erika Mustermann und ERIKA MUSTERMANN",
            identifiers={"PERSON": ["Erika Mustermann"]},
        )
        assert out == "{{PERSON_1}} und {{PERSON_1}}"

    def test_a_name_inside_another_word_stays(self):
        # Both ends: "Annahme" starts with the name, "Johanna" ends with it.
        # Each needs its own boundary, and a test with only one of them leaves
        # the other unguarded.
        out = _prompt("Annahme, Johanna und Anna", identifiers={"PERSON": ["Anna"]})
        assert out == "Annahme, Johanna und {{PERSON_1}}"

    def test_the_longest_name_wins(self):
        # Both names start at the same place. With "Mustermann" as the shorter
        # one the longer name would win anyway, because it starts earlier.
        out = _prompt(
            "Erika Mustermann, kurz Erika",
            identifiers={"PERSON": ["Erika", "Erika Mustermann"]},
        )
        assert out == "{{PERSON_1}}, kurz {{PERSON_2}}"

    def test_a_name_broken_across_a_line_is_still_the_name(self):
        out = _prompt("Frau Erika\nMustermann", identifiers={"PERSON": ["Erika Mustermann"]})
        assert out == "Frau {{PERSON_1}}"

    def test_the_category_numbers_its_own_placeholders(self):
        out = _prompt(
            "Akte 4 C 123/26 von Erika Mustermann",
            identifiers={"CASE_NUMBER": ["4 C 123/26"], "PERSON": ["Erika Mustermann"]},
        )
        assert out == "Akte {{CASE_NUMBER_1}} von {{PERSON_1}}"

    def test_where_casefold_and_ignorecase_disagree_the_value_is_still_replaced(self):
        # The Turkish dotless i: IGNORECASE matches "ıris" to "Iris", but
        # casefold() keeps them apart, so the category lookup misses. The
        # value is replaced anyway, under the first category named.
        out = _prompt("ıris kommt", identifiers={"PERSON": ["Iris"], "CITY": ["Rom"]})
        assert out == "{{PERSON_1}} kommt"


# ── one numbering per call ────────────────────────────────────────────────────

class TestSharedNumbering:
    def test_system_and_prompt_share_one_numbering(self):
        r = redact(
            {"system": "Patientin: Erika Mustermann", "prompt": "Brief an Erika Mustermann"},
            identifiers={"PERSON": ["Erika Mustermann"]},
        )
        assert r.texts == {
            "system": "Patientin: {{PERSON_1}}",
            "prompt": "Brief an {{PERSON_1}}",
        }

    def test_counts_are_occurrences_per_category_never_values(self):
        r = redact({"prompt": "a@example.de, a@example.de, b@example.de"})
        assert r.counts == {"EMAIL": 3}
        assert r.replacements == {"EMAIL_1": "a@example.de", "EMAIL_2": "b@example.de"}

    def test_nothing_found_changes_nothing(self):
        r = redact({"prompt": "Fasse den Text zusammen."})
        assert r.texts == {"prompt": "Fasse den Text zusammen."}
        assert r.counts == {} and r.replacements == {}

    def test_a_placeholder_already_in_the_input_is_not_handed_out_again(self):
        r = redact({"prompt": "Vorlage {{EMAIL_1}} für a@example.de"})
        assert r.texts["prompt"] == "Vorlage {{EMAIL_1}} für {{EMAIL_2}}"
        restored = r.restore("Vorlage {{EMAIL_1}} für {{EMAIL_2}}")
        assert restored.text == "Vorlage {{EMAIL_1}} für a@example.de"
        assert restored.unresolved == 0

    def test_an_empty_match_is_never_replaced(self):
        # A rule that can match nothing at all would otherwise put a
        # placeholder between every two characters. Refused at approval for a
        # scope's rule; this is the guard for anything that gets past that.
        r = redact({"prompt": "ab"}, rules=(Rule("NOTHING", re.compile(r"x*")),))
        assert r.texts["prompt"] == "ab"
        assert r.counts == {}

    def test_without_rules_only_named_values_are_replaced(self):
        r = redact({"prompt": "Erika, a@example.de"}, identifiers={"PERSON": ["Erika"]}, rules=())
        assert r.texts["prompt"] == "{{PERSON_1}}, a@example.de"


# ── putting values back ───────────────────────────────────────────────────────

class TestRestore:
    def _redaction(self):
        return redact(
            {"prompt": "Brief an Erika Mustermann, a@example.de"},
            identifiers={"PERSON": ["Erika Mustermann"]},
        )

    def test_every_placeholder_comes_back(self):
        restored = self._redaction().restore("Liebe {{PERSON_1}}, Kopie an {{EMAIL_1}}.")
        assert restored.text == "Liebe Erika Mustermann, Kopie an a@example.de."
        assert restored.unresolved == 0

    def test_whitespace_inside_the_braces_is_tolerated(self):
        assert self._redaction().restore("{{ PERSON_1 }}").text == "Erika Mustermann"

    def test_an_unknown_placeholder_stays_and_is_counted(self):
        restored = self._redaction().restore("{{PERSON_7}} schreibt")
        assert restored.text == "{{PERSON_7}} schreibt"
        assert restored.unresolved == 1

    @pytest.mark.parametrize("changed", ["PERSON_1 schreibt", "{PERSON_1} schreibt"])
    def test_a_placeholder_the_model_changed_stays_and_is_counted(self, changed: str):
        # Not guessed back: a visible gap is better than a wrong name.
        restored = self._redaction().restore(changed)
        assert restored.text == changed
        assert restored.unresolved == 1

    def test_a_backslash_in_a_value_comes_back_unchanged(self):
        # re.sub reads backslashes in a replacement string as escapes; a value
        # like this one would come back altered if restore used one.
        r = redact({"prompt": r"Pfad C:\new\table"}, identifiers={"PATH": [r"C:\new\table"]})
        assert r.restore("{{PATH_1}}").text == r"C:\new\table"

    def test_a_value_is_not_searched_again(self):
        # Single pass: a value that itself looks like a placeholder is not
        # replaced a second time.
        r = redact({"prompt": "x {{X_1}}y"}, identifiers={"CODE": ["x"]}, rules=())
        assert r.restore("{{CODE_1}}").text == "x"


# ── what a caller may name ────────────────────────────────────────────────────

class TestIdentifiersFrom:
    def test_none_and_empty_mean_nothing_named(self):
        assert identifiers_from(None) == {}
        assert identifiers_from({}) == {}

    def test_values_are_trimmed_and_kept_in_order(self):
        assert identifiers_from({"PERSON": [" Erika ", "Max"]}) == {"PERSON": ("Erika", "Max")}

    @pytest.mark.parametrize("value", [
        ["PERSON"],
        {"person": ["Erika"]},
        {"1PERSON": ["Erika"]},
        {"P" * 33: ["Erika"]},
        {"PERSON": "Erika"},
        {"PERSON": ["E"]},
        {"PERSON": ["x" * 201]},
        {"PERSON": [42]},
        {"PERSON": ["   "]},
    ])
    def test_what_cannot_be_used_is_refused(self, value):
        with pytest.raises(InvalidIdentifiers):
            identifiers_from(value)

    def test_the_total_is_bounded(self):
        with pytest.raises(InvalidIdentifiers, match=str(MAX_IDENTIFIERS)):
            identifiers_from({"PERSON": [f"Name{i}" for i in range(MAX_IDENTIFIERS + 1)]})


def test_the_shipped_rules_are_the_ones_the_docs_name():
    # SECURITY.md, the README and the API description list these categories.
    # A rule added or dropped here has to be added or dropped there too.
    assert [r.category for r in SHIPPED_RULES] == [
        "EMAIL", "IBAN", "DATE_OF_BIRTH", "TAX_ID", "PHONE", "HEALTH_INSURANCE_NUMBER",
    ]


# ── across a run (CHY-234) ────────────────────────────────────────────────────

class TestAcrossARun:
    """A run is stored with placeholders, so a person keeps one name from the
    first step to the last. Each test shows what breaks without `known`."""

    KNOWN = {"PERSON_1": "Erika Mustermann", "EMAIL_1": "erika@example.de"}

    def test_a_known_value_keeps_its_placeholder(self):
        """Without `known` the second step would call her PERSON_1 by luck at
        best, and EMAIL_1 would be whoever the step met first."""
        r = redact({"p": "Kopie an erika@example.de"}, known=self.KNOWN)
        assert r.texts["p"] == "Kopie an {{EMAIL_1}}"

    def test_the_same_person_in_other_spelling_is_the_same_person(self):
        r = redact({"p": "ERIKA  mustermann"}, identifiers={"PERSON": ["ERIKA  mustermann"]},
                   known=self.KNOWN)
        assert r.texts["p"] == "{{PERSON_1}}"

    def test_a_new_value_is_numbered_after_the_known_ones(self):
        r = redact({"p": "und an max@example.de"}, known=self.KNOWN)
        assert r.texts["p"] == "und an {{EMAIL_2}}"
        assert r.replacements == {**self.KNOWN, "EMAIL_2": "max@example.de"}

    def test_numbering_continues_after_the_highest_name_not_after_the_count(self):
        """A run that knows only EMAIL_3 must not hand out EMAIL_1 or EMAIL_2:
        a stored text might still say EMAIL_2 for somebody else."""
        r = redact({"p": "neu@example.de"}, known={"EMAIL_3": "alt@example.de"})
        assert r.texts["p"] == "{{EMAIL_4}}"

    def test_placeholders_from_earlier_steps_stay_and_are_not_counted(self):
        r = redact({"p": "Liebe {{PERSON_1}}, Kopie an {{EMAIL_1}}"}, known=self.KNOWN)
        assert r.texts["p"] == "Liebe {{PERSON_1}}, Kopie an {{EMAIL_1}}"
        assert r.counts == {}

    def test_the_whole_mapping_comes_back_even_when_nothing_was_found(self):
        """The run seals what it gets back. A step that found nothing new must
        not hand back an empty mapping and lose the run's names."""
        assert redact({"p": "nichts"}, known=self.KNOWN).replacements == self.KNOWN

    def test_a_name_outside_the_pattern_is_taken_but_does_not_steer_the_numbering(self):
        r = redact({"p": "a@example.de"}, known={"EMAIL": "x@example.de"})
        assert r.texts["p"] == "{{EMAIL_1}}"
        assert r.replacements["EMAIL"] == "x@example.de"

    def test_without_known_nothing_changes(self):
        """The boundary of `/v1/complete` stays as it was."""
        r = redact({"p": "a@example.de"})
        assert r.texts["p"] == "{{EMAIL_1}}"
        assert r.replacements == {"EMAIL_1": "a@example.de"}


class TestRedactStructure:
    """Every text of a run input, however nested (decided 06.11.2026, E2)."""

    def test_every_text_is_replaced_however_deep(self):
        value = {"message": "an a@example.de",
                 "letter": {"to": ["Erika Mustermann", {"cc": "b@example.de"}]}}
        replaced, r = redact_structure(value, identifiers={"PERSON": ["Erika Mustermann"]})
        assert replaced == {"message": "an {{EMAIL_1}}",
                            "letter": {"to": ["{{PERSON_1}}", {"cc": "{{EMAIL_2}}"}]}}
        assert r.replacements == {"EMAIL_1": "a@example.de", "PERSON_1": "Erika Mustermann",
                                  "EMAIL_2": "b@example.de"}

    def test_one_numbering_across_all_fields(self):
        replaced, _ = redact_structure({"a": "x@example.de", "b": ["x@example.de"]})
        assert replaced == {"a": "{{EMAIL_1}}", "b": ["{{EMAIL_1}}"]}

    def test_keys_numbers_booleans_and_null_stay(self):
        value = {"a@example.de": 1, "n": 2.5, "flag": True, "none": None}
        replaced, r = redact_structure(value)
        assert replaced == value
        assert r.replacements == {}

    def test_it_continues_a_run(self):
        replaced, r = redact_structure(["neu@example.de"], known={"EMAIL_1": "alt@example.de"})
        assert replaced == ["{{EMAIL_2}}"]
        assert set(r.replacements) == {"EMAIL_1", "EMAIL_2"}

    def test_a_tuple_becomes_a_list(self):
        """JSON knows no tuples; the stored input would come back as a list anyway."""
        replaced, _ = redact_structure(("a@example.de",))
        assert replaced == ["{{EMAIL_1}}"]


class TestUnresolvedIn:
    MAPPING = {"PERSON_1": "Erika Mustermann"}

    def test_a_known_placeholder_is_resolvable(self):
        assert unresolved_in("Liebe {{PERSON_1}}", self.MAPPING) == 0

    def test_a_name_the_run_never_handed_out_is_counted(self):
        assert unresolved_in("Liebe {{PERSON_2}}", self.MAPPING) == 1

    def test_a_placeholder_the_model_changed_is_counted(self):
        assert unresolved_in("Liebe PERSON_1", self.MAPPING) == 1


class TestReserve:
    """The caller's list goes into the mapping, not into the stored input (CHY-234)."""

    def test_every_listed_name_gets_one_found_or_not(self):
        """Without it a listed name the input did not contain would not be
        searched for in any later step: the list itself is not stored."""
        mapping = reserve({"PERSON": ["Erika Mustermann", "Max Mustermann"]},
                          known={"PERSON_1": "Erika Mustermann"})
        assert mapping == {"PERSON_1": "Erika Mustermann", "PERSON_2": "Max Mustermann"}

    def test_a_placeholder_already_in_the_input_is_not_handed_out_again(self):
        assert reserve({"PERSON": ["Max"]}, taken=frozenset({"PERSON_1"})) == {"PERSON_2": "Max"}

    def test_the_same_person_listed_twice_is_one_name(self):
        assert reserve({"PERSON": ["Erika", "ERIKA"]}) == {"PERSON_1": "Erika"}


class TestIdentifiersIn:
    def test_each_value_is_searched_under_its_category(self):
        assert identifiers_in({"PERSON_1": "Erika", "PERSON_2": "Max",
                               "CASE_NUMBER_1": "AZ-7"}) == {
            "PERSON": ("Erika", "Max"), "CASE_NUMBER": ("AZ-7",)}

    def test_a_value_found_once_is_found_again_where_no_pattern_matches(self):
        """The point of searching the mapping, not only the list: the model
        writes the address back in capitals, and the e-mail rule would still
        find it -- but an IBAN without its spaces is not an IBAN to the rule."""
        known = {"IBAN_1": "DE89 3704 0044 0532 0130 00"}
        later = redact({"p": "IBAN: DE89 3704 0044 0532 0130 00"},
                       identifiers=identifiers_in(known), rules=(), known=known)
        assert later.texts["p"] == "IBAN: {{IBAN_1}}"
        without = redact({"p": "IBAN: DE89 3704 0044 0532 0130 00"}, rules=(), known=known)
        assert without.texts["p"] == "IBAN: DE89 3704 0044 0532 0130 00"


class TestRebased:
    """Two steps of one run add names at the same time; the second to record
    merges onto what the first wrote, under the row lock."""

    START = {"PERSON_1": "Erika"}

    def test_nobody_recorded_in_between_nothing_moves(self):
        returned = {**self.START, "PERSON_2": "Max"}
        merged, renames = rebased(self.START, returned, self.START)
        assert merged == returned and renames == {}

    def test_the_same_name_for_another_person_is_moved(self):
        """Without it PERSON_2 would mean Max in one output and Anna in the
        other, and resolving would show a reviewer the wrong person."""
        current = {**self.START, "PERSON_2": "Max"}
        merged, renames = rebased(self.START, {**self.START, "PERSON_2": "Anna"}, current)
        assert merged == {**current, "PERSON_3": "Anna"}
        assert renames == {"PERSON_2": "PERSON_3"}

    def test_another_name_for_the_same_person_takes_the_one_recorded(self):
        """The step met Anna first and called Max PERSON_3; the run already
        knows him as PERSON_2. The two names swap."""
        current = {**self.START, "PERSON_2": "Max"}
        step = {**self.START, "PERSON_2": "Anna", "PERSON_3": "max"}
        merged, renames = rebased(self.START, step, current)
        assert renames == {"PERSON_2": "PERSON_3", "PERSON_3": "PERSON_2"}
        assert merged == {**current, "PERSON_3": "Anna"}

    def test_a_taken_name_is_skipped(self):
        current = {**self.START, "PERSON_2": "Max"}
        _, renames = rebased(self.START, {**self.START, "PERSON_2": "Anna"}, current,
                             taken=frozenset({"PERSON_3"}))
        assert renames == {"PERSON_2": "PERSON_4"}


class TestRenamed:
    def test_every_text_of_the_output_is_renamed_in_one_pass(self):
        """A swap: one pass, or PERSON_2 would become PERSON_3 and then PERSON_2 again."""
        output = {"content": "{{PERSON_2}} und {{ PERSON_3 }}", "n": 1,
                  "parts": ["an {{PERSON_2}}"]}
        assert renamed(output, {"PERSON_2": "PERSON_3", "PERSON_3": "PERSON_2"}) == {
            "content": "{{PERSON_3}} und {{PERSON_2}}", "n": 1, "parts": ["an {{PERSON_3}}"]}

    def test_without_renames_the_value_is_returned_as_it_is(self):
        output = {"content": "{{PERSON_2}}"}
        assert renamed(output, {}) is output

    def test_placeholders_in_finds_the_names_in_every_text(self):
        assert placeholders_in({"a": "{{PERSON_1}}", "b": ["x {{ EMAIL_2 }}"], "c": 3}) == {
            "PERSON_1", "EMAIL_2"}


class TestResolved:
    """Putting the values back, for a person allowed to see them (CHY-234, E4)."""

    MAPPING = {"PERSON_1": "Erika Mustermann", "EMAIL_1": "erika@example.de"}

    def test_every_text_of_a_nested_value_gets_its_values(self):
        value = {"letter": "Liebe {{PERSON_1}}", "cc": ["{{ EMAIL_1 }}"], "pages": 2}
        assert resolved(value, self.MAPPING) == {
            "letter": "Liebe Erika Mustermann", "cc": ["erika@example.de"], "pages": 2}

    def test_a_placeholder_the_mapping_does_not_know_stays_standing(self):
        assert resolved("{{PERSON_9}}", self.MAPPING) == "{{PERSON_9}}"

    def test_a_value_that_looks_like_a_placeholder_is_not_resolved_again(self):
        """One pass: a value is never searched a second time."""
        assert resolved("{{PERSON_1}}", {"PERSON_1": "{{EMAIL_1}}", "EMAIL_1": "x"}) == (
            "{{EMAIL_1}}")

    def test_none_and_numbers_pass_through(self):
        assert resolved(None, self.MAPPING) is None
        assert resolved({"n": 1}, {}) == {"n": 1}
