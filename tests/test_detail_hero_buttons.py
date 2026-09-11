import unittest

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw

from src.domain.models import Movie, Show
from src.ui.detail_page import DetailPage


def make_page():
    item = Movie(tmdb_id=42, title="Test Movie")
    page = DetailPage(object(), object(), object(), "movie", item)
    return page


class HeroButtonTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            Gtk.init()
            Adw.init()
        except TypeError:
            pass

    def test_rate_button_label_applies_immediately_on_page_load(self):
        # Populating a page for an already-rated title must show the rated
        # label right away.
        page = make_page()
        page.populate_hero(
            {
                "detail": Movie(tmdb_id=42, title="Test Movie", runtime=120),
                "watchlist_ids": set(),
                "watched_ids": set(),
                "rating": 4,
            },
            None,
        )
        self.assertEqual(page.rate_label.get_text(), "Rated \u2605 4/5")

    def test_action_row_uses_natural_width(self):
        # The action row tracks the buttons' natural widths so the page can
        # shrink responsively.
        page = make_page()
        page.populate_hero(
            {
                "detail": Movie(tmdb_id=42, title="Test Movie", runtime=120),
                "watchlist_ids": set(),
                "watched_ids": set(),
                "rating": 0,
            },
            None,
        )
        width_request, _ = page.action_box.get_size_request()
        self.assertEqual(width_request, -1)

    def test_action_box_is_flowbox_for_wrapping(self):
        # Buttons should wrap to the next line when space is tight, not
        # overflow the window.  Gtk.FlowBox provides that automatically.
        page = make_page()
        self.assertIsInstance(page.action_box, Gtk.FlowBox)


class PlaceholderHeroTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            Gtk.init()
            Adw.init()
        except TypeError:
            pass

    def test_movie_hero_paints_from_item_before_fetch(self):
        item = Movie(tmdb_id=42, title="Test Movie", year=2024,
                     overview="Some synopsis.")
        page = DetailPage(object(), object(), object(), "movie", item)
        self.assertEqual(page.title_label.get_text(), "Test Movie")
        self.assertEqual(page.meta_label.get_text(), "2024")
        self.assertEqual(page.overview_label.get_text(), "Some synopsis.")

    def test_show_hero_paints_from_item_before_fetch(self):
        item = Show(tmdb_id=7, title="Test Show", year=2020,
                    overview="A show synopsis.")
        page = DetailPage(object(), object(), object(), "show", item)
        self.assertEqual(page.title_label.get_text(), "Test Show")
        self.assertEqual(page.meta_label.get_text(), "2020")
        self.assertEqual(page.overview_label.get_text(), "A show synopsis.")

    def test_missing_fields_leave_hero_blank(self):
        item = Movie(tmdb_id=42, title="", year=None, overview=None)
        page = DetailPage(object(), object(), object(), "movie", item)
        self.assertEqual(page.title_label.get_text(), "")
        self.assertEqual(page.meta_label.get_text(), "")
        self.assertEqual(page.overview_label.get_text(), "")


if __name__ == "__main__":
    unittest.main()
