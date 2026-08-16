import unittest
from unittest import mock

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw

from src.domain.models import Movie
from src.ui.detail_page import DetailPage


def make_page():
    item = Movie(tmdb_id=42, title="Test Movie")
    page = DetailPage(object(), object(), object(), "movie", item)
    return page


class WatchedOpensRateDialogTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            Gtk.init()
            Adw.init()
        except TypeError:
            pass

    def test_marking_watched_opens_rate_dialog(self):
        # Pressing "Mark Watched" should follow with the rating dialog so
        # the user can rate right away.
        page = make_page()
        page._is_watched = False
        page._marking_watched = True
        with mock.patch.object(page, "_open_rate_dialog") as opener:
            page._watch_done(page.watched_btn)
        opener.assert_called_once()

    def test_unmarking_watched_does_not_open_rate_dialog(self):
        # Un-marking a title must not pop the rating dialog.
        page = make_page()
        page._is_watched = True
        page._marking_watched = False
        with mock.patch.object(page, "_open_rate_dialog") as opener:
            page._watch_done(page.watched_btn)
        opener.assert_not_called()

    def test_marking_already_rated_title_skips_dialog(self):
        # An already-rated title should not nag when its watched state is
        # toggled on.
        page = make_page()
        page._is_watched = False
        page._marking_watched = True
        page._my_rating = 4
        with mock.patch.object(page, "_open_rate_dialog") as opener:
            page._watch_done(page.watched_btn)
        opener.assert_not_called()

    def test_toggle_watched_records_direction(self):
        # The watched button press remembers whether it is marking or
        # unmarking so _watch_done knows when to open the dialog.
        page = make_page()
        page._is_watched = False
        with mock.patch("gi.repository.GLib.Thread.new"):
            page._toggle_watched(page.watched_btn)
        self.assertTrue(page._marking_watched)


if __name__ == "__main__":
    unittest.main()