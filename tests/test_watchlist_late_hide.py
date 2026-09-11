"""Late hiding: network-gated filters (unreleased titles, caught-up shows
with nothing airing soon) run after the first render and slim the grids
instead of blocking the first paint."""

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

FUTURE = (datetime.date.today() + datetime.timedelta(days=30)).isoformat()
PAST = (datetime.date.today() - datetime.timedelta(days=30)).isoformat()


class _Settings:
    def __init__(self, unreleased=True, caught_up=True, window=14):
        self._u = unreleased
        self._c = caught_up
        self._w = window

    def get_boolean(self, key):
        return {"hide-unreleased": self._u,
                "hide-shows-no-upcoming-episodes": self._c}.get(key, False)

    def get_int(self, key):
        return self._w if key == "hide-shows-upcoming-window" else 0


class _Repo:
    def __init__(self, watched=None):
        self.watched = watched or {}

    def get_watchlist(self, media_type):
        return []

    def get_watched_ids(self, media_type):
        return set()

    def get_watched_show_ids(self):
        return set()

    def get_watched_episodes_for_show(self, tmdb_id):
        return set(self.watched.get(tmdb_id, ()))

    def get_currently_watching_shows(self):
        return []


class _LateMeta:
    """Per-id metadata: release/first-air dates, seasons/episodes, status."""

    def __init__(self, movie_dates=None, show_dates=None, caught_up_ids=()):
        self._movie_dates = movie_dates or {}
        self._show_dates = show_dates or {}
        self._caught_up = set(caught_up_ids)

    def get_movie(self, tmdb_id):
        return SimpleNamespace(release_date=self._movie_dates.get(tmdb_id))

    def get_show(self, tmdb_id):
        status = "Ended" if tmdb_id in self._caught_up else "Returning"
        return SimpleNamespace(status=status, next_episode_air_date=None,
                               first_air_date=self._show_dates.get(tmdb_id))

    def get_show_seasons(self, tmdb_id):
        if tmdb_id not in self._caught_up:
            return []
        return [SimpleNamespace(season_number=1)]

    def get_season_episodes(self, tmdb_id, season_number):
        last_week = (datetime.date.today()
                     - datetime.timedelta(days=7)).isoformat()
        return [SimpleNamespace(season_number=1, episode_number=1,
                                air_date=last_week)]


class _Win:
    def __init__(self, settings=None):
        self.settings = settings or _Settings()


def _make_page(meta, settings=None, watched=None):
    with mock.patch("gi.repository.GLib.Thread.new"):
        page = WatchlistPage(_Win(settings), _Repo(watched), meta, None)
    page.upcoming_enabled = False
    return page


def _item(i, kind):
    return SimpleNamespace(
        tmdb_id=i, title=f"{kind}{i}", media_type=kind, year=2024,
        poster_url=None, runtime=None, imdb_id=None, genres=None, added_at=i,
    )


def _seed(page, kinds):
    """Queue one item per kind and drain synchronously with sections in
    their post-render state."""
    page._revealed = True
    page._revealed_early = False
    page._shown_section_headers = set()
    page.movies_section[0].set_visible(False)
    page.shows_section[0].set_visible(False)
    queue = iter([(k, _item(i, k)) for i, k in enumerate(kinds)])
    page._pending_chunk = (page._reload_token, queue, frozenset(),
                           frozenset())
    page._pump_build(schedule=False)
    page._items = [_item(i, k) for i, k in enumerate(kinds)]


def _child_ids(grid):
    out = []
    child = grid.get_first_child()
    while child:
        button = child.get_child()
        out.append(button._media_item.tmdb_id)
        child = child.get_next_sibling()
    return out


class ComputeLateHiddenTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not Gtk.is_initialized():
            Gtk.init()

    def test_unreleased_and_caught_up_collected(self):
        meta = _LateMeta(movie_dates={1: FUTURE, 2: PAST},
                         show_dates={3: FUTURE},
                         caught_up_ids={9})
        page = _make_page(meta, watched={9: {(1, 1)}})
        movies = [_item(1, "movie"), _item(2, "movie")]
        shows = [_item(3, "show"), _item(9, "show"), _item(4, "show")]
        hidden_m, hidden_s = page._compute_late_hidden(movies, shows)
        self.assertEqual(hidden_m, {1})
        self.assertEqual(hidden_s, {3, 9})

    def test_filters_off_computes_nothing(self):
        page = _make_page(_LateMeta(), settings=_Settings(False, False))
        movies = [_item(1, "movie"), _item(2, "movie")]
        shows = [_item(3, "show"), _item(9, "show")]
        hidden_m, hidden_s = page._compute_late_hidden(movies, shows)
        self.assertEqual(hidden_m, set())
        self.assertEqual(hidden_s, set())


class ApplyLateHiddenTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not Gtk.is_initialized():
            Gtk.init()

    def test_partial_removal_syncs_items(self):
        page = _make_page(_LateMeta())
        _seed(page, ("movie", "movie", "show", "show"))
        page._apply_late_hidden(page._reload_token, {1}, {2})
        self.assertEqual(_child_ids(page.movies_grid), [0])
        self.assertEqual(_child_ids(page.shows_grid), [3])
        self.assertEqual([i.tmdb_id for i in page._items], [0, 3])

    def test_stale_token_is_noop(self):
        page = _make_page(_LateMeta())
        _seed(page, ("movie",))
        page._apply_late_hidden(page._reload_token + 1, {0}, frozenset())
        self.assertEqual(_child_ids(page.movies_grid), [0])

    def test_empty_section_hidden(self):
        page = _make_page(_LateMeta())
        _seed(page, ("show", "show"))
        self.assertTrue(page.shows_section[0].get_visible())
        page._apply_late_hidden(page._reload_token, frozenset(), {0, 1})
        self.assertEqual(_child_ids(page.shows_grid), [])
        self.assertFalse(page.shows_section[0].get_visible())
        self.assertEqual(page._items, [])


class FetchPipelineTest(unittest.TestCase):
    """_fetch renders from local data and only then runs the hide checks."""

    @classmethod
    def setUpClass(cls):
        if not Gtk.is_initialized():
            Gtk.init()

    def test_fetch_uses_unfiltered_and_schedules_late_hide(self):
        page = _make_page(_LateMeta())
        order = []
        page._get_items_unfiltered = lambda mode: (
            order.append("unf"), (["m"], ["s"]))[1]
        page._early_badges = lambda movies, shows: frozenset()
        page._late_badges = lambda movies, shows: frozenset()
        page._late_hidden = lambda token, movies, shows: order.append(
            ("late", token))
        with mock.patch("src.ui.watchlist_page.GLib.idle_add") as idle:
            page._fetch(7, "all")
        self.assertEqual(order, ["unf", ("late", 7)])
        self.assertEqual(idle.call_count, 1)
        callback = idle.call_args_list[0].args[0]
        self.assertIs(callback.__func__, WatchlistPage._populate)
        self.assertIs(callback.__self__, page)


if __name__ == "__main__":
    unittest.main()