"""Duplicate page loads within a short window are dropped.

Page creation schedules a deferred first load while main_page may queue
a stale refresh at nearly the same moment. Running both tears down
freshly painted cards and replays the entrance animation.
"""

import sys
import types
import unittest
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


class _FakeMetadata:
    def get_movie(self, tmdb_id):
        raise AssertionError("not used")

    def get_show(self, tmdb_id):
        raise AssertionError("not used")


def _make_page():
    with mock.patch("gi.repository.GLib.Thread.new"):
        page = WatchlistPage(object(), _FakeRepo(), _FakeMetadata(), None)
    page.upcoming_enabled = False
    return page


class LoadDedupTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not Gtk.is_initialized():
            Gtk.init()

    def test_rapid_duplicate_load_spawns_one_fetch(self):
        page = _make_page()
        page._load()
        with mock.patch("gi.repository.GLib.Thread.new") as tn:
            page._load()
        self.assertEqual(tn.call_count, 0)

    def test_load_after_window_expires_runs_again(self):
        page = _make_page()
        page._load()
        # Pretend the last request happened 10s ago.
        page._last_load_request_ms -= 10_000
        with mock.patch("gi.repository.GLib.Thread.new") as tn:
            page._load()
        self.assertEqual(tn.call_count, 1)

    def test_force_bypasses_dedup(self):
        page = _make_page()
        page._load()
        with mock.patch("gi.repository.GLib.Thread.new") as tn:
            page._load(force=True)
        self.assertEqual(tn.call_count, 1)

    def test_mode_change_fade_uses_force(self):
        page = _make_page()
        page._load()
        # Simulate what _fade_out_then_load does after its fade completes.
        with mock.patch("gi.repository.GLib.Thread.new") as tn, \
                mock.patch.object(page, "_clear"), \
                mock.patch.object(page, "_show_skeleton"):
            page._fade_out_then_load()
            page._reload_pending = False
            page._load(force=True)
        self.assertEqual(tn.call_count, 1)


if __name__ == "__main__":
    unittest.main()


class LiveBadgeAtBuildTest(unittest.TestCase):
    """A check that resolves while chunks are still building must badge
    later-built cards: creation reads the live badge set, not the
    populate-time snapshot."""

    def test_later_chunk_card_gets_badge_from_live_set(self):
        from types import SimpleNamespace

        page = _make_page()
        page._revealed = True  # repopulate-style path
        page._last_fully_shows = frozenset()  # no verdicts yet

        show = SimpleNamespace(
            tmdb_id=77, title="Show", media_type="show", year=2024,
            poster_url=None, runtime=None, imdb_id=None, genres=None,
        )
        movies = [SimpleNamespace(
            tmdb_id=i, title=f"M{i}", media_type="movie", year=2024,
            poster_url=None, runtime=None, imdb_id=None, genres=None,
            added_at=i,
        ) for i in range(20)]
        items = movies + [show]

        # Feed the pump manually: first chunk builds movies only.
        queue = iter([("movie", m) for m in movies] + [("show", show)])
        page._pending_chunk = (page._reload_token, queue, frozenset(),
                               frozenset())
        with mock.patch("src.ui.watchlist_page.make_media_card",
                        wraps=__import__(
                            "src.ui.watchlist_page",
                            fromlist=["make_media_card"]).make_media_card):
            page._pump_build(schedule=False) is not None
            # verdict lands between chunks
            page._apply_late_badges(page._reload_token, {77})
            page._pump_build(schedule=False)

        btn = _grid_cards(page.shows_grid)[-1]
        from src.ui.media_card import _find_watched_badge
        self.assertIsNotNone(
            _find_watched_badge(btn),
            "show card built after verdict must carry the watched badge")


def _grid_cards(grid):
    out = []
    child = grid.get_first_child()
    while child:
        out.append(child.get_child())
        child = child.get_next_sibling()
    return out


class PreZeroOnRepopulateTest(unittest.TestCase):
    """Cards built during a repopulate pass must be hidden before any
    frame paints them — otherwise the delayed group fade flashes."""

    def _movie(self, i):
        from types import SimpleNamespace
        return SimpleNamespace(
            tmdb_id=i, title=f"M{i}", media_type="movie", year=2024,
            poster_url=None, runtime=None, imdb_id=None, genres=None,
            added_at=i,
        )

    def test_repopulate_cards_start_hidden(self):
        page = _make_page()
        page._revealed = True
        queue = iter([("movie", self._movie(1))])
        page._pending_chunk = (page._reload_token, queue, frozenset(),
                               frozenset())
        page._pump_build(schedule=False)
        card = page.movies_grid.get_first_child().get_child()
        self.assertEqual(card.get_opacity(), 0.0)
        # rise offset applied before any frame paints
        self.assertEqual(card.get_margin_top(), 8)

    def test_first_load_cards_stay_visible(self):
        page = _make_page()
        page._revealed = False
        queue = iter([("movie", self._movie(1))])
        page._pending_chunk = (page._reload_token, queue, frozenset(),
                               frozenset())
        page._pump_build(schedule=False)
        card = page.movies_grid.get_first_child().get_child()
        self.assertEqual(card.get_opacity(), 1.0)
        self.assertEqual(card.get_margin_top(), 0)

    def test_empty_section_stays_hidden_after_build(self):
        from types import SimpleNamespace

        page = _make_page()
        page._revealed = True
        movie = SimpleNamespace(
            tmdb_id=1, title="M", media_type="movie", year=2024,
            poster_url=None, runtime=None, imdb_id=None, genres=None,
            added_at=1,
        )
        queue = iter([("movie", movie)])
        page._pending_chunk = (page._reload_token, queue, frozenset(),
                               frozenset())
        page._pump_build(schedule=False)
        self.assertTrue(page.movies_section[0].get_visible())
        self.assertFalse(page.shows_section[0].get_visible())
