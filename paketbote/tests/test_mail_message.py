"""Turning a raw message into text we can search."""

import unittest
from email.message import EmailMessage

from app.mail.message import html_to_text, parse


def build(subject="Versandbestätigung", sender="shop@example.invalid",
          plain=None, html=None):
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender
    message["Date"] = "Fri, 31 Jul 2026 09:15:00 +0200"
    if plain is not None and html is not None:
        message.set_content(plain)
        message.add_alternative(html, subtype="html")
    elif html is not None:
        message.set_content(html, subtype="html")
    else:
        message.set_content(plain or "")
    return message.as_bytes()


class TestHtmlToText(unittest.TestCase):
    """The tracking link is almost always only in an href."""

    def test_links_survive_the_markup(self):
        text = html_to_text('<p>Ihr Paket ist unterwegs. '
                            '<a href="https://my.dpd.de/x?parcelno=01415129595439">'
                            'Sendung verfolgen</a></p>')
        self.assertIn("Sendung verfolgen", text)
        self.assertIn("https://my.dpd.de/x?parcelno=01415129595439", text)

    def test_entities_are_decoded(self):
        self.assertIn("Größe", html_to_text("<p>Gr&ouml;&szlig;e</p>"))

    def test_ampersands_in_links_are_decoded(self):
        text = html_to_text('<a href="https://x.invalid/t?a=1&amp;b=2">x</a>')
        self.assertIn("https://x.invalid/t?a=1&b=2", text)

    def test_scripts_and_styles_are_dropped(self):
        text = html_to_text("<style>a{color:red}</style><script>var x=1</script><p>Paket</p>")
        self.assertNotIn("color", text)
        self.assertNotIn("var x", text)
        self.assertIn("Paket", text)


class TestParse(unittest.TestCase):
    def test_subject_and_sender(self):
        mail = parse(build(), uid=7)
        self.assertEqual(mail.subject, "Versandbestätigung")
        self.assertIn("shop@example.invalid", mail.sender)
        self.assertEqual(mail.uid, 7)

    def test_an_encoded_subject_is_decoded(self):
        raw = (b"Subject: =?utf-8?B?SWhyZSBTZW5kdW5nIGlzdCB1bnRlcndlZ3M=?=\r\n"
               b"From: shop@example.invalid\r\n\r\nHallo\r\n")
        self.assertEqual(parse(raw).subject, "Ihre Sendung ist unterwegs")

    def test_a_broken_subject_does_not_raise(self):
        raw = b"Subject: =?utf-8?Q?kaputt\r\nFrom: x@y.invalid\r\n\r\nHallo\r\n"
        self.assertIsInstance(parse(raw).subject, str)

    def test_plain_text_body(self):
        mail = parse(build(plain="Sendungsnummer 00340434161094042557"))
        self.assertIn("00340434161094042557", mail.body)

    def test_html_only_mail_keeps_its_link(self):
        mail = parse(build(html='<a href="https://dhl.de/x?piececode=123456789012">go</a>'))
        self.assertIn("piececode=123456789012", mail.body)

    def test_multipart_takes_both_parts(self):
        mail = parse(build(plain="Nur Text",
                           html='<a href="https://my.dpd.de/x?parcelno=01415129595439">x</a>'))
        self.assertIn("Nur Text", mail.body)
        self.assertIn("01415129595439", mail.body)

    def test_the_date_is_read(self):
        self.assertEqual(parse(build()).received.year, 2026)

    def test_a_missing_date_is_not_an_error(self):
        raw = b"Subject: x\r\nFrom: a@b.invalid\r\n\r\nHallo\r\n"
        self.assertIsNone(parse(raw).received)

    def test_an_unknown_charset_falls_back(self):
        raw = (b'Content-Type: text/plain; charset="x-unknown"\r\n'
               b"Subject: x\r\nFrom: a@b.invalid\r\n\r\nSendung 123\r\n")
        self.assertIn("Sendung 123", parse(raw).body)

    def test_the_body_is_capped(self):
        mail = parse(build(plain="x" * 200_000))
        self.assertLessEqual(len(mail.body), 60_000)


if __name__ == "__main__":
    unittest.main()
