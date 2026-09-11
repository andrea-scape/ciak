"""Hide-unreleased preference: future-dated movies and not-yet-premiered
shows disappear from the Watchlist when the setting is on; unknown dates
never hide a title."""

import sys
import time
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
from src.data.tmdb.service import TmdbMetadataService

FUTURE = (datetime.date.today() + datetime.timedelta(days=30)).isoformat()
PAST = (datetime.date.today() - datetime.timedelta(days=30)).isoformat()


class _Settings:
    def __init__(self, hide):
        self.hide = hide

    def get_boolean(self, key):
        return self.hide if key == "hide-unreleased" else False


class _Repo:
    def __init__(self, rows_movie, rows_show, latest=None):
        self._rows_movie = rows_movie
        self._rows_show = rows_show
        self._latest = latest or {}

    def get_watchlist(self, media_type):
        return list(self._rows_movie if media_type == "movie"
                    else self._rows_show)

    def get_watched_ids(self, media_type):
        return set()

    def get_watched_show_ids(self):
        return set()

    def get_watched_episodes_for_show(self, show_id):
        return set()

    def get_latest_watched_at_for_show(self, show_id):
        return self._latest.get(show_id, 0)

    def get_currently_watching_shows(self):
        return []


class _Meta:
    """release/first_air dates per tmdb_id; missing id → no date."""

    def __init__(self, movie_dates=None, show_dates=None):
        self._m = movie_dates or {}
        self._s = show_dates or {}

    def get_movie(self, tmdb_id):
        return SimpleNamespace(release_date=self._m.get(tmdb_id))

    def get_show(self, tmdb_id):
        return SimpleNamespace(first_air_date=self._s.get(tmdb_id))


def _row(tmdb_id, title="T", added_at=0):
    return dict(tmdb_id=tmdb_id, title=title, year=2026,
                poster_url=None, runtime=None, imdb_id=None,
                added_at=added_at)


class HideUnreleasedTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not Gtk.is_initialized():
            Gtk.init()
            try:
                from gi.repository import Adw
                Adw.init()
            except TypeError:
                pass

    def _page(self, hide):
        repo = _Repo(
            rows_movie=[_row(1, "Future Movie"), _row(2, "Released Movie")],
            rows_show=[_row(3, "Future Show"), _row(4, "Airing Show")],
        )
        meta = _Meta(movie_dates={1: FUTURE, 2: PAST},
                     show_dates={3: FUTURE, 4: PAST})
        win = SimpleNamespace(settings=_Settings(hide))
        with mock.patch("gi.repository.GLib.Thread.new"):
            page = WatchlistPage(win, repo, meta, None)
        page.upcoming_enabled = False
        return page

    def _ids(self, page):
        movies, shows = page._get_items("all")
        return ([m.tmdb_id for m in movies], [s.tmdb_id for s in shows])

    def test_flag_on_hides_future_titles(self):
        page = self._page(hide=True)
        movies, shows = self._ids(page)
        self.assertEqual(movies, [2])
        self.assertEqual(shows, [4])

    def test_flag_off_shows_everything(self):
        page = self._page(hide=False)
        movies, shows = self._ids(page)
        self.assertEqual(movies, [1, 2])
        self.assertEqual(shows, [3, 4])

    def test_unknown_date_never_hides(self):
        repo = _Repo(rows_movie=[_row(9, "No Date")], rows_show=[])
        meta = _Meta()  # no dates at all
        win = SimpleNamespace(settings=_Settings(True))
        with mock.patch("gi.repository.GLib.Thread.new"):
            page = WatchlistPage(win, repo, meta, None)
        movies, _shows = page._get_items("all")
        self.assertEqual([m.tmdb_id for m in movies], [9])


class WatchlistOrderByActionTest(unittest.TestCase):
    """Watchlist sorts by the most recent action: watchlist add or an
    episode watch count equally."""

    @classmethod
    def setUpClass(cls):
        if not Gtk.is_initialized():
            Gtk.init()
            try:
                from gi.repository import Adw
                Adw.init()
            except TypeError:
                pass

    def _order(self, shows, latest=None):
        repo = _Repo(rows_movie=[], rows_show=shows, latest=latest)
        win = SimpleNamespace(settings=_Settings(False))
        with mock.patch("gi.repository.GLib.Thread.new"):
            page = WatchlistPage(win, repo, _Meta(), None)
            page._sort_by = "added"
        items = [SimpleNamespace(**dict(r, media_type="show"))
                 for r in shows]
        return [i.tmdb_id for i in page._sort_items(items)]

    def test_recent_episode_watch_beats_recent_add(self):
        now = int(time.time())
        shows = [
            _row(10, "Added yesterday", added_at=now - 86400),
            _row(11, "Added a month ago", added_at=now - 30 * 86400),
        ]
        ids = self._order(shows, latest={11: now})
        self.assertEqual(ids, [11, 10])

    def test_untouched_old_show_sinks_to_the_end(self):
        now = int(time.time())
        shows = [
            _row(20, "Fresh add", added_at=now),
            _row(21, "Old and untouched", added_at=now - 60 * 86400),
        ]
        ids = self._order(shows)
        self.assertEqual(ids, [20, 21])

    def test_movies_keep_added_order(self):
        now = int(time.time())
        repo = _Repo(
            rows_movie=[_row(30, "Old", added_at=now - 30 * 86400),
                        _row(31, "New", added_at=now)],
            rows_show=[],
        )
        win = SimpleNamespace(settings=_Settings(False))
        with mock.patch("gi.repository.GLib.Thread.new"):
            page = WatchlistPage(win, repo, _Meta(), None)
            page._sort_by = "added"
        items = [SimpleNamespace(**dict(r, media_type="movie"))
                 for r in repo._rows_movie]
        self.assertEqual([i.tmdb_id for i in page._sort_items(items)], [31, 30])


class FirstAirDateMappingTest(unittest.TestCase):
    def test_raw_to_show_carries_first_air_date(self):
        svc = object.__new__(TmdbMetadataService)
        client = SimpleNamespace(_image_url=lambda p, *a: p)
        svc._client = client
        show = svc._raw_to_show({
            "id": 5, "name": "S", "first_air_date": "2026-01-02",
            "genres": [], "episode_run_time": [],
        })
        self.assertEqual(show.first_air_date, "2026-01-02")


if __name__ == "__main__":
    unittest.main()
