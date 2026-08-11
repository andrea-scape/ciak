import unittest

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


class RatePersistenceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            Gtk.init()
            Adw.init()
        except TypeError:
            pass

    def test_populate_hero_keeps_rated_state(self):
        # Regression: opening a details page for a title the user already
        # rated must show "Rated ★ 4/5", not "Rate". The rating is fetched on
        # the background prefetch thread and passed through populate_hero.
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
        self.assertEqual(page._my_rating, 4)
        self.assertEqual(page.rate_label.get_text(), "Rated \u2605 4/5")

    def test_populate_hero_shows_rate_when_unrated(self):
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
        self.assertEqual(page._my_rating, 0)
        self.assertEqual(page.rate_label.get_text(), "Rate")

    def test_populate_hero_without_rating_key_is_graceful(self):
        page = make_page()
        page.populate_hero(
            {
                "detail": Movie(tmdb_id=42, title="Test Movie", runtime=120),
                "watchlist_ids": set(),
                "watched_ids": set(),
            },
            None,
        )
        self.assertEqual(page._my_rating, 0)
        self.assertEqual(page.rate_label.get_text(), "Rate")


if __name__ == "__main__":
    unittest.main()
