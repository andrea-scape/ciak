"""Watchlist section headers: on the first load a "Movies"/"Shows"
header appears only when its first poster is actually painted (or the
poster-ready safety fires); repopulates keep showing non-empty
sections at drain as before."""

import sys
import types
import unittest
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


class _FakeRepo:
    data_version = 1

    def get_watchlist(self, media_type):
        return []

    def get_watched_ids(self, media_type):
        return set()

    def get_watched_show_ids(self):
        return set()

    def get_currently_watching_shows(self):
        return []


class _FakeMetadata:
    def get_movie(self, tmdb_id):
        raise AssertionError("not used")

    def get_show(self, tmdb_id):
        raise AssertionError("not used")


class _Win:
    settings = SimpleNamespace(
        get_boolean=lambda k: False,
        get_string=lambda k: "",
        get_int=lambda k: 14,
        get_int64=lambda k: 0,
    )


def _make_page():
    with mock.patch("gi.repository.GLib.Thread.new"):
        page = WatchlistPage(_Win(), _FakeRepo(), _FakeMetadata(), None)
    page.upcoming_enabled = False
    return page


def _item(i, kind="movie"):
    return SimpleNamespace(
        tmdb_id=i, title=f"{kind}{i}", media_type=kind, year=2024,
        poster_url=None, runtime=None, imdb_id=None, genres=None,
        added_at=i,
    )


def _seed(page, kinds=("movie", "show"), revealed=False):
    """Queue one item per kind and drain synchronously. Mirrors what
    _build does ahead of pumping: headers hidden, header-gate and
    first-load markers reset."""
    page._revealed = revealed
    page._revealed_early = False
    page._shown_section_headers = set()
    page.movies_section[0].set_visible(False)
    page.shows_section[0].set_visible(False)
    queue = iter([(k, _item(i, k)) for i, k in enumerate(kinds)])
    page._pending_chunk = (page._reload_token, queue, frozenset(),
                           frozenset())
    page._pump_build(schedule=False)


class WatchlistSectionRevealTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not Gtk.is_initialized():
            Gtk.init()

    def test_first_load_drain_leaves_headers_hidden(self):
        page = _make_page()
        _seed(page, ("movie", "show"))
        self.assertFalse(page.movies_section[0].get_visible())
        self.assertFalse(page.shows_section[0].get_visible())
        # cards are present — only the headers wait for posters
        self.assertIsNotNone(page.movies_grid.get_first_child())
        self.assertIsNotNone(page.shows_grid.get_first_child())

    def test_header_appears_with_first_painted_poster(self):
        page = _make_page()
        _seed(page, ("movie", "show"))
        page._show_section_header(page.movies_section)
        self.assertTrue(page.movies_section[0].get_visible())
        self.assertFalse(page.shows_section[0].get_visible())

    def test_show_section_header_is_idempotent_per_section(self):
        page = _make_page()
        _seed(page, ("movie", "show"))
        page._show_section_header(page.movies_section)
        page._show_section_header(page.movies_section)
        self.assertEqual(len(page._shown_section_headers), 1)

    def test_repopulate_keeps_showing_sections_at_drain(self):
        page = _make_page()
        _seed(page, ("movie",), revealed=True)
        self.assertTrue(page.movies_section[0].get_visible())
        self.assertFalse(page.shows_section[0].get_visible())

    def test_pump_wires_poster_ready_to_opposite_headers(self):
        page = _make_page()
        page._revealed = False
        page._revealed_early = False
        page._shown_section_headers = set()
        page.movies_section[0].set_visible(False)
        page.shows_section[0].set_visible(False)
        queue = iter([("movie", _item(1, "movie")),
                      ("show", _item(2, "show"))])
        page._pending_chunk = (page._reload_token, queue, frozenset(),
                               frozenset())
        with mock.patch(
                "src.ui.watchlist_page.make_media_card",
                return_value=Gtk.Box()) as mk:
            page._pump_build(schedule=False)

        movie_ready = mk.call_args_list[0].kwargs["on_poster_ready"]
        show_ready = mk.call_args_list[1].kwargs["on_poster_ready"]
        movie_ready()
        self.assertTrue(page.movies_section[0].get_visible())
        self.assertFalse(page.shows_section[0].get_visible())
        show_ready()
        self.assertTrue(page.shows_section[0].get_visible())


if __name__ == "__main__":
    unittest.main()