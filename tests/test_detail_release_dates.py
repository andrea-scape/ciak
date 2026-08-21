import unittest
from unittest import mock

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw

from src.domain.models import Movie, Show, Season, Episode
from src.data.tmdb.service import TmdbMetadataService
from src.ui.detail_page import DetailPage


def make_page(media_type="movie", item=None):
    item = item or Movie(tmdb_id=42, title="Test Movie")
    return DetailPage(object(), object(), object(), media_type, item)


class MovieReleaseDateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            Gtk.init()
            Adw.init()
        except TypeError:
            pass

    def test_movie_meta_shows_full_release_date(self):
        # The hero meta line shows the precise release date instead of just
        # the year.
        page = make_page()
        movie = Movie(
            tmdb_id=42, title="Test Movie", year=2026, release_date="2026-12-01"
        )
        page.populate_hero(
            {"detail": movie, "watchlist_ids": set(), "watched_ids": set(), "rating": 0}
        )
        self.assertIn("Dec 1, 2026", page.meta_label.get_text())

    def test_movie_meta_falls_back_to_year_without_date(self):
        page = make_page()
        movie = Movie(tmdb_id=42, title="Test Movie", year=1999)
        page.populate_hero(
            {"detail": movie, "watchlist_ids": set(), "watched_ids": set(), "rating": 0}
        )
        self.assertIn("1999", page.meta_label.get_text())

    def test_future_movie_actions_disabled(self):
        # A movie that has not been released yet cannot be marked as seen,
        # so both the watched and the rate button (rating implies watched)
        # are insensitive.  The watchlist stays clickable so unreleased
        # titles can be added and removed freely.
        page = make_page()
        movie = Movie(tmdb_id=42, title="Test Movie", release_date="2099-01-01")
        page.populate_hero(
            {"detail": movie, "watchlist_ids": set(), "watched_ids": set(), "rating": 0}
        )
        self.assertFalse(page.watched_btn.get_sensitive())
        self.assertFalse(page.rate_btn.get_sensitive())
        self.assertTrue(page.watchlist_btn.get_sensitive())

    def test_future_movie_watchlist_toggle_removes_row(self):
        class RecordingRepo:
            def __init__(self):
                self.removed = []
                self.added = []

            def remove_from_watchlist(self, tmdb_id, media_type):
                self.removed.append((tmdb_id, media_type))

            def add_to_watchlist(self, tmdb_id, media_type):
                self.added.append((tmdb_id, media_type))

        repo = RecordingRepo()
        item = Movie(tmdb_id=42, title="Test Movie", release_date="2099-01-01")
        page = DetailPage(object(), repo, object(), "movie", item)
        movie = Movie(tmdb_id=42, title="Test Movie", release_date="2099-01-01")
        page.populate_hero(
            {"detail": movie, "watchlist_ids": {42}, "watched_ids": set(), "rating": 0}
        )
        self.assertTrue(page._in_watchlist)
        page._do_toggle_watchlist(page.watchlist_btn)
        self.assertEqual(repo.removed, [(42, "movie")])
        self.assertEqual(repo.added, [])

    def test_released_movie_actions_enabled(self):
        page = make_page()
        movie = Movie(tmdb_id=42, title="Test Movie", release_date="2020-01-01")
        page.populate_hero(
            {"detail": movie, "watchlist_ids": set(), "watched_ids": set(), "rating": 0}
        )
        self.assertTrue(page.watched_btn.get_sensitive())
        self.assertTrue(page.rate_btn.get_sensitive())

    def test_missing_release_date_actions_enabled(self):
        # No date known -> fail open, do not lock the user out.
        page = make_page()
        movie = Movie(tmdb_id=42, title="Test Movie")
        page.populate_hero(
            {"detail": movie, "watchlist_ids": set(), "watched_ids": set(), "rating": 0}
        )
        self.assertTrue(page.watched_btn.get_sensitive())
        self.assertTrue(page.rate_btn.get_sensitive())

    def test_toggle_watched_noop_for_future_movie(self):
        page = make_page()
        page._detail = Movie(tmdb_id=42, title="Test Movie", release_date="2099-01-01")
        with mock.patch("gi.repository.GLib.Thread.new") as thread:
            page._toggle_watched(page.watched_btn)
        thread.assert_not_called()


class ShowPremiereTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            Gtk.init()
            Adw.init()
        except TypeError:
            pass

    def _make_page(self, repo):
        item = Show(tmdb_id=7, title="Test Show")
        return DetailPage(object(), repo, object(), "show", item)

    def _hero(self, show, seasons, season_episodes):
        return {
            "detail": show,
            "seasons": seasons,
            "season_episodes": season_episodes,
            "watchlist_ids": set(),
            "watched_ids": set(),
            "rating": 0,
        }

    def test_show_without_aired_episodes_disables_watched(self):
        # A show that has not premiered yet has nothing to mark as seen.
        repo = mock.Mock()
        repo.get_watched_episodes_for_show.return_value = set()
        page = self._make_page(repo)
        season = Season(tmdb_id=1, show_tmdb_id=7, season_number=1, episode_count=1)
        ep = Episode(
            tmdb_id=99, show_tmdb_id=7, season_number=1, episode_number=1,
            title="Pilot", air_date="2099-01-01",
        )
        season.episodes = [ep]
        page.populate_hero(self._hero(page.item, [season], {1: [ep]}))
        self.assertFalse(page.watched_btn.get_sensitive())

    def test_show_with_aired_episode_watched_enabled(self):
        repo = mock.Mock()
        repo.get_watched_episodes_for_show.return_value = set()
        page = self._make_page(repo)
        season = Season(tmdb_id=1, show_tmdb_id=7, season_number=1, episode_count=1)
        ep = Episode(
            tmdb_id=99, show_tmdb_id=7, season_number=1, episode_number=1,
            title="Pilot", air_date="2020-01-01",
        )
        season.episodes = [ep]
        page.populate_hero(self._hero(page.item, [season], {1: [ep]}))
        self.assertTrue(page.watched_btn.get_sensitive())


class EpisodeAiringLabelTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            Gtk.init()
            Adw.init()
        except TypeError:
            pass

    def test_not_yet_aired_episode_row_shows_airing_date(self):
        page = make_page()
        page._season_expanders = []
        season = Season(tmdb_id=1, show_tmdb_id=7, season_number=1)
        expander = Adw.ExpanderRow()
        state = {"ep_checks": [], "season_number": 1, "season": season}
        ep = Episode(
            tmdb_id=99, show_tmdb_id=7, season_number=1, episode_number=2,
            title="Chapter", air_date="2026-12-01",
        )
        page._populate_season_episodes(season, expander, state, [ep])
        row = state["ep_checks"][0][1].get_ancestor(Gtk.ListBoxRow)
        self.assertIn("airing Dec 1, 2026", row.get_subtitle())

    def test_aired_episode_row_keeps_title_only(self):
        page = make_page()
        page._season_expanders = []
        season = Season(tmdb_id=1, show_tmdb_id=7, season_number=1)
        expander = Adw.ExpanderRow()
        state = {"ep_checks": [], "season_number": 1, "season": season}
        ep = Episode(
            tmdb_id=99, show_tmdb_id=7, season_number=1, episode_number=2,
            title="Chapter", air_date="2020-01-01",
        )
        page._populate_season_episodes(season, expander, state, [ep])
        row = state["ep_checks"][0][1].get_ancestor(Gtk.ListBoxRow)
        self.assertEqual(row.get_subtitle(), "Chapter")


class ServiceReleaseDateTest(unittest.TestCase):
    def test_raw_to_movie_parses_release_date(self):
        client = mock.Mock()
        client._image_url.side_effect = lambda path, size=None: path
        service = TmdbMetadataService(client, object())
        movie = service._raw_to_movie(
            {"id": 1, "title": "X", "release_date": "2026-12-01"}
        )
        self.assertEqual(movie.release_date, "2026-12-01")
        self.assertEqual(movie.year, 2026)


if __name__ == "__main__":
    unittest.main()