"""Search bar behaviors: per-page placeholders, trending pills on page
entry, and empty-bar restore to the original state."""

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

import src.ui.search_page as search_page_module
from src.ui.history_page import HistoryPage
from src.ui.search_page import SearchPage
from src.ui.watchlist_page import WatchlistPage


class _Repo:
    def get_watchlist(self, media_type):
        return []

    def get_watched_ids(self, media_type):
        return {11}

    def get_watched_show_ids(self):
        return set()


def _movie(tmdb_id):
    return SimpleNamespace(
        tmdb_id=tmdb_id, title=f"M{tmdb_id}", media_type="movie",
        year=2024, poster_url=None, runtime=None, imdb_id=None,
        genres=None, added_at=tmdb_id,
    )


def _make(cls, *args, **kwargs):
    with mock.patch("gi.repository.GLib.Thread.new"):
        return cls(object(), *args, **kwargs)


class SearchBarTestBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not Gtk.is_initialized():
            Gtk.init()
            try:
                from gi.repository import Adw
                Adw.init()
            except (TypeError, ValueError):
                pass


class PlaceholderTest(SearchBarTestBase):
    def test_watchlist_placeholder_names_the_page(self):
        page = _make(WatchlistPage, _Repo(), object())
        self.assertIn(
            "Search in Watchlist",
            page.search_entry.get_placeholder_text())

    def test_history_placeholder_names_the_page(self):
        page = _make(HistoryPage, _Repo(), object())
        self.assertIn(
            "Search in History",
            page.search_entry.get_placeholder_text())


class TrendingPillsTest(SearchBarTestBase):
    def test_trending_movies_carry_watched_flag(self):
        page = _make(SearchPage, _Repo(), object(), None)
        mk = mock.patch.object(search_page_module, "make_media_card",
                               wraps=search_page_module.make_media_card)
        with mk as m:
            page._populate_trending(
                page._render_gen,
                [_movie(11), _movie(12)], [], {11},
            )
        flags = {}
        for c in m.call_args_list:
            item = c.args[0]
            flags[item.tmdb_id] = c.kwargs.get("watched", False)
        self.assertTrue(flags[11])
        self.assertFalse(flags[12])


class RestoreTrendingTest(SearchBarTestBase):
    def test_empty_change_restores_cached_trending(self):
        page = _make(SearchPage, _Repo(), object(), None)
        page._trending_loaded = True
        page._trending_movies = [_movie(11)]
        page._trending_shows = []
        page._showing_trending = False

        entry = SimpleNamespace(get_text=lambda: "")
        captured = []

        def _grab_idle(cb=None, *a, **k):
            if cb is not None:
                captured.append((cb, a))
            return 0

        with mock.patch("gi.repository.GLib.idle_add",
                        side_effect=_grab_idle), \
                mock.patch("gi.repository.GLib.Thread.new"), \
                mock.patch.object(page, "_show_skeleton"):
            page._on_search_changed(entry)
            for cb, a in captured:
                cb(*a)

        self.assertTrue(page._showing_trending)
        child = page.movies_grid.get_first_child()
        self.assertIsNotNone(child)

    def test_nonempty_change_is_ignored_until_enter(self):
        page = _make(SearchPage, _Repo(), object(), None)
        entry = SimpleNamespace(get_text=lambda: "dune")
        gen_before = page._render_gen
        with mock.patch("gi.repository.GLib.idle_add") as idle, \
                mock.patch("gi.repository.GLib.Thread.new"):
            page._on_search_changed(entry)
        idle.assert_not_called()
        self.assertEqual(page._render_gen, gen_before)


if __name__ == "__main__":
    unittest.main()
