"""Reading a shipping notice: flagging it, and guessing what to track."""

import unittest

from app.mail import signatures
from app.mail.extract import find_candidates, read
from app.mail.keywords import fold, looks_like_shipping, matched_terms


class TestFolding(unittest.TestCase):
    def test_case_and_spacing_do_not_matter(self):
        self.assertEqual(fold("  Ihre   SENDUNG  "), "ihre sendung")

    def test_umlauts_are_transliterated_not_dropped(self):
        self.assertEqual(fold("Päckchen"), "paeckchen")
        self.assertEqual(fold("Größe"), "groesse")

    def test_accents_are_stripped(self):
        self.assertEqual(fold("expédition"), "expedition")
        self.assertEqual(fold("envío"), "envio")


class TestFlagging(unittest.TestCase):
    def test_german(self):
        self.assertTrue(looks_like_shipping("Ihre Sendung ist unterwegs"))

    def test_english(self):
        self.assertTrue(looks_like_shipping("Your parcel has been dispatched"))

    def test_other_languages(self):
        for text in ("Votre colis a été expédié", "Je pakket is onderweg",
                     "La tua spedizione è in consegna", "Tu envío está en reparto",
                     "Twoja przesyłka jest w drodze"):
            self.assertTrue(looks_like_shipping(text), text)

    def test_an_ordinary_mail_is_not_flagged(self):
        for text in ("Ihre Rechnung für Januar", "Passwort zurücksetzen",
                     "Newsletter: 20% auf alles"):
            self.assertFalse(looks_like_shipping(text), text)

    def test_the_matched_terms_are_reported(self):
        self.assertIn("sendung", matched_terms("Ihre Sendung ist unterwegs"))

    def test_a_carrier_link_alone_is_enough(self):
        # No shipping word anywhere, but a link that can only mean one thing.
        findings = read("Bestellung 4711", "https://my.dpd.de/redirect.aspx?parcelno=01415129595439")
        self.assertTrue(findings.is_shipping)


class TestFromLinks(unittest.TestCase):
    """A carrier's own host is the strongest evidence there is."""

    def test_dhl_link(self):
        body = ("Ihre Sendung ist unterwegs. Verfolgen: "
                "https://www.dhl.de/de/privatkunden/pakete-empfangen/verfolgen.html"
                "?piececode=00340434161094042557")
        best = find_candidates("Versandbestätigung", body)[0]
        self.assertEqual(best.code, "00340434161094042557")
        self.assertEqual(best.carrier, "dhl")
        self.assertIn("link", best.why)

    def test_dpd_link(self):
        body = "https://my.dpd.de/redirect.aspx?action=12&parcelno=01415129595439"
        best = find_candidates("Ihr Paket kommt", body)[0]
        self.assertEqual(best.code, "01415129595439")
        self.assertEqual(best.carrier, "dpd")

    def test_hermes_link(self):
        body = ("https://www.myhermes.de/empfangen/sendungsverfolgung/"
                "sendungsinformation/#12345678901234")
        best = find_candidates("Sendung", body)[0]
        self.assertEqual(best.carrier, "hermes")

    def test_a_link_beats_a_carrier_named_elsewhere(self):
        # Shops write "nicht per DHL" and mean it.
        body = ("Versand nicht per DHL. "
                "https://my.dpd.de/redirect.aspx?parcelno=01415129595439")
        best = find_candidates("Ihre Sendung", body)[0]
        self.assertEqual(best.carrier, "dpd")


class TestFromShape(unittest.TestCase):
    def test_ups_numbers_are_unmistakable(self):
        best = find_candidates("Shipment", "Tracking: 1Z999AA10123456784")[0]
        self.assertEqual(best.carrier, "ups")
        self.assertIn("shape", best.why)

    def test_a_dhl_parcel_number_is_unmistakable(self):
        best = find_candidates("Sendung", "Nummer 00340434161094042557")[0]
        self.assertEqual(best.carrier, "dhl")

    def test_fourteen_digits_alone_cannot_decide(self):
        # DPD, Hermes and GLS all look like this, so the carrier stays open
        # and the caller has to ask.
        best = find_candidates("Ihre Sendung ist unterwegs", "Nummer 01415129595439")[0]
        self.assertEqual(best.code, "01415129595439")
        self.assertEqual(best.carrier, "")

    def test_a_named_carrier_settles_an_ambiguous_number(self):
        best = find_candidates("Ihre Sendung", "Versand per Hermes, Nummer 01415129595439")[0]
        self.assertEqual(best.carrier, "hermes")
        self.assertIn("named", best.why)

    def test_two_named_carriers_settle_nothing(self):
        best = find_candidates("Sendung", "DHL oder Hermes, Nummer 01415129595439")[0]
        self.assertEqual(best.carrier, "")


class TestNearMisses(unittest.TestCase):
    """Shops quote plenty of numbers that are not tracking numbers."""

    def test_an_order_number_is_not_tracked(self):
        body = "Ihre Sendung ist unterwegs. Bestellnummer: 01415129595439"
        self.assertEqual(find_candidates("Versand", body), [])

    def test_an_invoice_number_is_not_tracked(self):
        body = "Sendung unterwegs. Rechnungsnummer 00340434161094042557"
        self.assertEqual(find_candidates("Versand", body), [])

    def test_the_same_number_mentioned_plainly_elsewhere_still_counts(self):
        body = ("Bestellnummer: 01415129595439\n"
                "Ihre Sendungsnummer lautet 01415129595439")
        best = find_candidates("Versand", body)[0]
        self.assertEqual(best.code, "01415129595439")

    def test_a_tracking_number_next_to_an_order_number_survives(self):
        body = ("Bestellnummer: 4711\n"
                "Sendungsnummer: 00340434161094042557")
        codes = [c.code for c in find_candidates("Versand", body)]
        self.assertIn("00340434161094042557", codes)


class TestRanking(unittest.TestCase):
    def test_at_most_three_candidates(self):
        body = "Sendung " + " ".join(f"0141512959543{n}" for n in range(9))
        self.assertLessEqual(len(find_candidates("Versand", body)), 3)

    def test_the_linked_number_ranks_first(self):
        body = ("Kundennummer 12345678901\n"
                "Sendung 98765432109876\n"
                "https://my.dpd.de/redirect.aspx?parcelno=01415129595439")
        self.assertEqual(find_candidates("Versand", body)[0].code, "01415129595439")

    def test_the_sender_domain_helps(self):
        plain = find_candidates("Sendung", "Nummer 01415129595439, Versand per DPD")
        with_sender = find_candidates(
            "Sendung", "Nummer 01415129595439, Versand per DPD", "noreply@dpd.de"
        )
        self.assertGreater(with_sender[0].score, plain[0].score)


class TestRead(unittest.TestCase):
    def test_a_shop_mail_end_to_end(self):
        findings = read(
            "Deine Bestellung wurde versandt",
            "Hallo,\nvielen Dank! Sendungsnummer 00340434161094042557.\n"
            "Verfolgen: https://www.dhl.de/de/privatkunden/pakete-empfangen/"
            "verfolgen.html?piececode=00340434161094042557",
            "shop@example.invalid",
        )
        self.assertTrue(findings.is_shipping)
        self.assertEqual(findings.best.carrier, "dhl")
        self.assertEqual(findings.best.code, "00340434161094042557")

    def test_a_shipping_mail_without_a_number(self):
        findings = read("Ihre Lieferung kommt bald", "Wir melden uns wieder.")
        self.assertTrue(findings.is_shipping)
        self.assertEqual(findings.candidates, [])

    def test_an_unrelated_mail_is_not_examined(self):
        findings = read("Newsletter", "20% Rabatt auf alles, nur heute!")
        self.assertFalse(findings.is_shipping)
        self.assertEqual(findings.candidates, [])


class TestUrlHelpers(unittest.TestCase):
    def test_a_code_is_read_from_a_query_parameter(self):
        self.assertEqual(
            signatures.code_in_url("https://x.invalid/t?trackingNumber=1Z999AA10123456784"),
            "1Z999AA10123456784",
        )

    def test_a_code_is_read_from_the_path(self):
        self.assertEqual(
            signatures.code_in_url("https://x.invalid/track/00340434161094042557"),
            "00340434161094042557",
        )

    def test_a_path_of_words_yields_nothing(self):
        self.assertEqual(signatures.code_in_url("https://x.invalid/track/parcel"), "")

    def test_trailing_punctuation_is_not_part_of_the_link(self):
        urls = signatures.find_urls("Siehe https://dhl.de/x?piececode=123456789012.")
        self.assertEqual(urls, ["https://dhl.de/x?piececode=123456789012"])


if __name__ == "__main__":
    unittest.main()
