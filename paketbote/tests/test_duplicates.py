"""Clearing up parcels filed twice under Amazon's changing identifiers."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from app.main import Paketbote
from app.config import Config
from app.models import SOURCE_AMAZON, SOURCE_MANUAL, Shipment
from app.state import Store

ORDER = "302-6054268-4901944"


def shipment(shipment_id, order_id=ORDER, items=None, title="Paket",
             source=SOURCE_AMAZON):
    return Shipment(
        shipment_id=shipment_id, order_id=order_id, tracking_url="u",
        title=title, items=items or [], source=source,
    )


class TestDropRenamed(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self._tmp.name) / "state.db")
        self.publisher = Mock(announced=set())
        self.bote = Paketbote(Config(), self.store, self.publisher)

    def tearDown(self):
        self.store.close()
        self._tmp.cleanup()

    def _run(self, stored, seen):
        for s in stored:
            self.store.save(s)
        self.bote._drop_renamed(stored, seen)
        return {s.shipment_id for s in self.store.all_shipments()}

    def test_the_row_under_the_old_identifier_goes(self):
        # Before dispatch Amazon named it by package index, after dispatch by
        # shipment id — the same parcel, filed twice.
        old = shipment("DhX7Kq2", items=[{"title": "Reolink"}, {"title": "SUNLU"}])
        new = shipment(f"{ORDER}-0", items=[{"title": "Reolink"}, {"title": "SUNLU"}])
        left = self._run([old], [new])
        self.assertEqual(left, set())
        self.publisher.retire_shipment.assert_called_once_with("DhX7Kq2")

    def test_a_genuine_second_package_survives(self):
        first = shipment(f"{ORDER}-0", items=[{"title": "Reolink"}])
        second = shipment(f"{ORDER}-1", items=[{"title": "SUNLU"}])
        left = self._run([first, second], [first])
        self.assertIn(f"{ORDER}-1", left)

    def test_a_parcel_from_another_order_is_untouched(self):
        old = shipment("DhX7Kq2", order_id="111-0000000-0000000",
                       items=[{"title": "Reolink"}])
        new = shipment(f"{ORDER}-0", items=[{"title": "Reolink"}])
        self.assertIn("DhX7Kq2", self._run([old], [new]))

    def test_an_order_no_longer_listed_is_left_to_the_missing_count(self):
        # Nothing of this order is on the list, so this says nothing about a
        # rename; the three-strikes rule handles it instead.
        old = shipment("DhX7Kq2", items=[{"title": "Reolink"}])
        self.assertIn("DhX7Kq2", self._run([old], []))

    def test_titles_stand_in_when_there_are_no_articles(self):
        old = shipment("DhX7Kq2", title="Spiegelglas")
        new = shipment(f"{ORDER}-0", title="Spiegelglas")
        self.assertEqual(self._run([old], [new]), set())

    def test_different_contents_are_not_merged(self):
        old = shipment("DhX7Kq2", title="Spiegelglas")
        new = shipment(f"{ORDER}-0", title="Etwas anderes")
        self.assertIn("DhX7Kq2", self._run([old], [new]))

    def test_manual_and_mail_parcels_are_never_touched(self):
        by_hand = shipment("manual-1", items=[{"title": "Reolink"}],
                           source=SOURCE_MANUAL)
        new = shipment(f"{ORDER}-0", items=[{"title": "Reolink"}])
        self.assertIn("manual-1", self._run([by_hand], [new]))

    def test_a_parcel_still_on_the_list_is_kept(self):
        current = shipment(f"{ORDER}-0", items=[{"title": "Reolink"}])
        self.assertIn(f"{ORDER}-0", self._run([current], [current]))

    def test_the_order_of_the_articles_does_not_matter(self):
        old = shipment("DhX7Kq2", items=[{"title": "SUNLU"}, {"title": "Reolink"}])
        new = shipment(f"{ORDER}-0", items=[{"title": "Reolink"}, {"title": "SUNLU"}])
        self.assertEqual(self._run([old], [new]), set())


if __name__ == "__main__":
    unittest.main()
