"""The Upcoming section reacts to genre chips and the search box, using
the same gates as the Movies/Shows grids."""

import sys
import types
import unittest
import datetime
from types import SimpleNamespace
from unittest import mock

if "src.config" not in sys.modules:
    _cfg = types.ModuleType("src.config")
    _cfg.APP_ID = "io.github.andrea_scape.ciak.Devel"
    _cfg.APP_VERSION = "0.0.0-test"
    _cfg.APP_NAME = "Ciak"
    sys.modules["src.config"] = _cfg

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

from src.ui.watchlist_page import WatchlistPage


class _Repo:
    data_version = 1

    def get_watchlist(self, media_type):
        return []

    def get_watched_ids(self, media_type):
        return set()

    def get_watched_show_ids(self):
        return set()


def _make_page():
    with mock.patch("gi.repository.GLib.Thread.new"):
        page = WatchlistPage(object(), _Repo(), object(), None)
    page.upcoming_enabled = True
    return page


def _movie_entry(title="Dune", genres='["Science Fiction"]'):
    row = dict(
        tmdb_id=1, media_type="movie", added_at=1,
        title=title, year=2026, poster_url=None, runtime=None,
        imdb_id=None, genres=genres,
    )
    d = datetime.date.today() + datetime.timedelta(days=2)
    return (d, 0, row)


def _show_entry(title="Lanterns", genres='["Drama"]', s=1, e=2):
    show_row = dict(
        tmdb_id=95350, title=title, year=2026, poster_url=None,
        runtime=None, imdb_id=None, genres=genres,
    )
    ep = SimpleNamespace(
        season_number=s, episode_number=e, air_date=None,
        poster_url=None, title="Ep",
    )
    d = datetime.date.today() + datetime.timedelta(days=3)
    return (d, 1, (show_row, ep))


class UpcomingFiltersTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not Gtk.is_initialized():
            Gtk.init()

    def _populate(self, page):
        entries = [
            _movie_entry("Dune"),
            _show_entry("Lanterns"),
        ]
        page._populate_upcoming(page._reload_token, entries)

    @staticmethod
    def _count(grid):
        n = 0
        child = grid.get_first_child()
        while child:
            n += 1
            child = child.get_next_sibling()
        return n

    def test_no_filters_shows_all_entries(self):
        page = _make_page()
        self._populate(page)
        self.assertEqual(self._count(page.upcoming_grid), 2)

    def test_genre_chip_filters_upcoming(self):
        page = _make_page()
        with mock.patch.object(type(page.genre_chips), "selected",
                               new_callable=mock.PropertyMock,
                               return_value={"Drama"}):
            self._populate(page)
        # only Lanterns survives; Dune is Science Fiction
        self.assertEqual(self._count(page.upcoming_grid), 1)

    def test_query_filters_by_title(self):
        page = _make_page()
        page._filter_query = "lanterns"
        self._populate(page)
        self.assertEqual(self._count(page.upcoming_grid), 1)

    def test_all_filtered_out_hides_section(self):
        page = _make_page()
        page._filter_query = "zzz-no-match"
        self._populate(page)
        self.assertFalse(page.upcoming_revealer.get_reveal_child())
        self.assertFalse(page.upcoming_section[0].get_visible())


if __name__ == "__main__":
    unittest.main()
