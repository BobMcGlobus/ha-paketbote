"""Tests for recipient and address matching."""

import unittest

from app.people import (
    display_name,
    normalise_address,
    normalise_name,
    postcode_of,
    same_person,
)


class TestNormaliseName(unittest.TestCase):
    def test_case_is_ignored(self):
        self.assertEqual(normalise_name("JONAS ALTHOFF"), normalise_name("jonas althoff"))

    def test_honorifics_are_dropped(self):
        for spelling in ("Herr Jonas Althoff", "Dr. Jonas Althoff", "Frau Jonas Althoff"):
            self.assertEqual(normalise_name(spelling), "jonas althoff", spelling)

    def test_middle_names_are_dropped(self):
        self.assertEqual(normalise_name("Jonas Peter Althoff"), "jonas althoff")
        self.assertEqual(normalise_name("Jonas P. M. Althoff"), "jonas althoff")

    def test_comma_form_is_the_same_person(self):
        self.assertEqual(normalise_name("Althoff, Jonas"), "jonas althoff")

    def test_extra_whitespace_and_punctuation(self):
        self.assertEqual(normalise_name("  Jonas   Althoff  "), "jonas althoff")
        self.assertEqual(normalise_name("Jonas-Althoff"), "jonas althoff")

    def test_umlauts_are_kept(self):
        # Folding them would risk merging genuinely different names.
        self.assertEqual(normalise_name("Jürgen Müller"), "jürgen müller")

    def test_single_token_names_survive(self):
        self.assertEqual(normalise_name("Mcglobus"), "mcglobus")

    def test_empty_input(self):
        self.assertEqual(normalise_name(""), "")
        self.assertEqual(normalise_name(None), "")
        self.assertEqual(normalise_name("Herr"), "")


class TestSamePerson(unittest.TestCase):
    def test_spellings_of_one_person(self):
        for other in ("jonas althoff", "Herr Jonas Althoff", "Althoff, Jonas", "Jonas P. Althoff"):
            self.assertTrue(same_person("Jonas Althoff", other), other)

    def test_different_people_stay_apart(self):
        self.assertFalse(same_person("Jonas Althoff", "Maria Althoff"))
        self.assertFalse(same_person("Jonas Althoff", "Jonas Meier"))

    def test_empty_never_matches(self):
        self.assertFalse(same_person("", ""))
        self.assertFalse(same_person("Jonas Althoff", ""))


class TestDisplayName(unittest.TestCase):
    def test_prefers_the_properly_typed_spelling(self):
        self.assertEqual(display_name(["jonas althoff", "Jonas Althoff"]), "Jonas Althoff")

    def test_honorifics_lose_to_a_plain_spelling(self):
        self.assertEqual(display_name(["Herr Jonas Althoff", "Jonas Althoff"]), "Jonas Althoff")

    def test_honorific_is_kept_when_it_is_all_there_is(self):
        self.assertEqual(display_name(["Herr Jonas Althoff"]), "Herr Jonas Althoff")

    def test_ignores_blanks(self):
        self.assertEqual(display_name(["", "  ", "Jonas Althoff"]), "Jonas Althoff")

    def test_nothing_to_choose_from(self):
        self.assertEqual(display_name([]), "")


class TestAddresses(unittest.TestCase):
    def test_postcode_is_found(self):
        self.assertEqual(postcode_of("Jonas Althoff, Hagedornweg 9a, Hamm 59065"), "59065")
        self.assertEqual(postcode_of("59065 Hamm"), "59065")

    def test_no_postcode(self):
        self.assertEqual(postcode_of("Hagedornweg 9a"), "")
        self.assertEqual(postcode_of(None), "")

    def test_house_number_is_not_mistaken_for_a_postcode(self):
        self.assertEqual(postcode_of("Hagedornweg 9a, Hamm"), "")

    def test_same_address_written_differently(self):
        a = normalise_address("Jonas Althoff, Mcglobus, Hagedornweg 9a, Hamm 59065")
        b = normalise_address("hagedornweg 9a, 59065 hamm")
        self.assertEqual(a, b)

    def test_different_addresses_stay_apart(self):
        a = normalise_address("Hagedornweg 9a, Hamm 59065")
        b = normalise_address("Hagedornweg 12, Hamm 59065")
        self.assertNotEqual(a, b)

    def test_different_towns_stay_apart(self):
        a = normalise_address("Hagedornweg 9a, Hamm 59065")
        b = normalise_address("Hagedornweg 9a, Werl 59457")
        self.assertNotEqual(a, b)


if __name__ == "__main__":
    unittest.main()
