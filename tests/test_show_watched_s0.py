import os
import sqlite3
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from src.data.local.repository import LocalMediaRepository
from src.ui import watched_state


def _meta(seasons=(0, 1), aired=(1, 2), next_air=None):
    """Stub metadata service: seasons incl. optional S0, all eps aired."""
    return SimpleNamespace(
        get_show_seasons=lambda _id: [
            SimpleNamespace(season_number=n) for n in seasons
        ],
        get_season_episodes=lambda _id, sn: (
            []
            if sn <= 0
            else [
                SimpleNamespace(
                    tmdb_id=9000 + sn * 10 + n,
                    show_tmdb_id=_id,
                    season_number=sn,
                    episode_number=n,
                    air_date="2025-01-01",
                )
                for n in aired
            ]
        ),
        get_show=lambda _id: SimpleNamespace(
            next_episode_air_date=next_air, status="Ended"
        ),
    )


class FullyWatchedIgnoresSeasonZeroTest(unittest.TestCase):
    def setUp(self):
        watched_state._fully_watched_memo.clear()

    def _repo(self, directory):
        db = os.path.join(directory, "db.sqlite")
        repo = LocalMediaRepository(db)
        repo.initialize()
        return repo, db

    def _stray_s0_row(self, db):
        conn = sqlite3.connect(db)
        try:
            conn.execute(
                "INSERT INTO watched_items "
                "(tmdb_id, media_type, show_tmdb_id, season_number, "
                "episode_number, watched_at) VALUES (?, 'episode', 7, 0, 9, ?)",
                (990, 1700000000),
            )
            conn.commit()
        finally:
            conn.close()

    def test_all_real_seasons_watched_with_stray_special_is_fully_watched(self):
        with tempfile.TemporaryDirectory() as d:
            repo, db = self._repo(d)
            repo.mark_watched(901, "movie", show_tmdb_id=7,
                              season_number=1, episode_number=1)
            repo.mark_watched(902, "movie", show_tmdb_id=7,
                              season_number=1, episode_number=2)
            self._stray_s0_row(db)
            self.assertTrue(
                watched_state.is_show_fully_watched(repo, _meta(), 7)
            )

    def test_missing_real_episode_is_not_fully_watched(self):
        with tempfile.TemporaryDirectory() as d:
            repo, db = self._repo(d)
            repo.mark_watched(901, "movie", show_tmdb_id=7,
                              season_number=1, episode_number=1)
            self._stray_s0_row(db)
            self.assertFalse(
                watched_state.is_show_fully_watched(repo, _meta(), 7)
            )

    def test_whole_show_marker_does_not_short_circuit(self):
        with tempfile.TemporaryDirectory() as d:
            repo, _db = self._repo(d)
            repo.mark_watched(7, "show")
            self.assertFalse(
                watched_state.is_show_fully_watched(
                    repo, _meta(aired=()), 7
                )
            )


class PurgeSeasonZeroTest(unittest.TestCase):
    def test_purge_removes_only_specials_rows_for_show(self):
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "db.sqlite")
            repo = LocalMediaRepository(db)
            repo.initialize()
            conn = sqlite3.connect(db)
            try:
                conn.executemany(
                    "INSERT INTO watched_items "
                    "(tmdb_id, media_type, show_tmdb_id, season_number, "
                    "episode_number, watched_at) VALUES (?, 'episode', ?, ?, ?, ?)",
                    [
                        (800, 7, 0, 3, 1700000000),   # stray special
                        (801, 7, 1, 1, 1700000001),   # real episode
                        (802, 8, 0, 4, 1700000002),   # other show's special
                    ],
                )
                conn.commit()
            finally:
                conn.close()
            repo.purge_season_zero_watched(7)
            dates = repo.get_watched_episode_dates(7)
            self.assertEqual(dates, {(1, 1): 1700000001})


class _MarkerRepo:
    """Stub exposing just what the recompute/unmark paths touch."""

    def __init__(self, whole_watched):
        self._whole = whole_watched
        self.purged = None

    def is_whole_show_watched(self, show_id):
        return self._whole

    def get_watched_at(self, *a, **k):
        return None

    def get_latest_watched_at_for_show(self, *a, **k):
        return None

    def mark_unwatched(self, *a, **k):
        pass

    def purge_season_zero_watched(self, show_id):
        self.purged = show_id


class RecomputeFallbackTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import gi

        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Gtk, Adw

        try:
            Gtk.init()
            Adw.init()
        except TypeError:
            pass

    def _show_page(self, repo):
        from src.domain.models import Show
        from src.ui.detail_page import DetailPage

        item = Show(tmdb_id=7, title="S")
        page = DetailPage(object(), repo, object(), "show", item)
        page._season_episodes = {}
        page._watched_episodes = set()
        return page

    def test_empty_season_data_trusts_whole_show_mark(self):
        # Bulk-mark wrote its rows; even with no season data fetched the
        # check must stay green.
        page = self._show_page(_MarkerRepo(True))
        page._recompute_is_watched()
        self.assertTrue(page._is_watched)

    def test_empty_season_data_without_mark_stays_unwatched(self):
        page = self._show_page(_MarkerRepo(False))
        page._recompute_is_watched()
        self.assertFalse(page._is_watched)

    def test_stray_special_tuple_does_not_break_recompute(self):
        repo = _MarkerRepo(False)
        page = self._show_page(repo)
        page._season_episodes = {
            1: [SimpleNamespace(season_number=1, episode_number=1,
                                air_date="2025-01-01")],
        }
        page._watched_episodes = {(1, 1), (0, 9)}
        page._recompute_is_watched()
        self.assertTrue(page._is_watched)

    def test_bulk_unmark_sweeps_special_rows(self):
        repo = _MarkerRepo(True)
        page = self._show_page(repo)
        page.metadata_service = _meta()
        page._watched_episodes = {(1, 1), (1, 2)}
        page._watched_seasons = {1}
        with mock.patch.object(
            page.metadata_service, "get_show_seasons",
            return_value=[SimpleNamespace(season_number=1)],
        ):
            page._mark_all_unwatched_show()
        self.assertEqual(repo.purged, 7)


if __name__ == "__main__":
    unittest.main()
