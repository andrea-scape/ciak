"""Hide-unreleased preference: future-dated movies and not-yet-premiered
shows disappear from the Watchlist when the setting is on; unknown dates
never hide a title."""

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
from src.data.tmdb.service import TmdbMetadataService

FUTURE = (datetime.date.today() + datetime.timedelta(days=30)).isoformat()
PAST = (datetime.date.today() - datetime.timedelta(days=30)).isoformat()


class _Settings:
    def __init__(self, hide):
        self.hide = hide

    def get_boolean(self, key):
        return self.hide if key == "hide-unreleased" else False


class _Repo:
    def __init__(self, rows_movie, rows_show):
        self._rows_movie = rows_movie
        self._rows_show = rows_show

    def get_watchlist(self, media_type):
        return list(self._rows_movie if media_type == "movie"
                    else self._rows_show)

    def get_watched_ids(self, media_type):
        return set()

    def get_watched_show_ids(self):
        return set()

    def get_watched_episodes_for_show(self, show_id):
        return set()

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


def _row(tmdb_id, title="T"):
    return dict(tmdb_id=tmdb_id, title=title, year=2026,
                poster_url=None, runtime=None, imdb_id=None)


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
