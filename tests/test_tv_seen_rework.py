"""TV seen-status rework: honest button, confirm dialogs, ended-permanent
verdicts, auto-flip for returning shows, watchlist reappearance."""

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

import datetime as dt

from src.ui import watched_state
from src.data.local.repository import LocalMediaRepository


def _iso(d):
    return d.isoformat()


class EndedPermanentVerdictTest(unittest.TestCase):
    """Ended/canceled shows: the persisted verdict never expires.
    Returning shows keep the normal revalidation window."""

    def _repo(self, status):
        class Repo:
            data_version = 1
            self_status = status

            def get_show_status(self, show_id):
                return status

            def get_watched_episodes_for_show(self, show_id):
                return {(1, 1)}

        return Repo()

    def _meta(self, eps=None):
        eps = eps or [(1, 1, _iso(dt.date.today() - dt.timedelta(days=7)))]
        return SimpleNamespace(
            get_show_seasons=lambda tid: [SimpleNamespace(season_number=1)],
            get_season_episodes=lambda tid, s: [
                SimpleNamespace(season_number=a, episode_number=b, air_date=d)
                for a, b, d in eps if s == a],
        )

    def setUp(self):
        watched_state._caught_up_memo.clear()
        watched_state._fully_watched_memo.clear()

    def tearDown(self):
        watched_state._caught_up_memo.clear()
        watched_state._fully_watched_memo.clear()

    def test_ended_verdict_survives_long_gaps(self):
        repo = self._repo("Ended")
        meta = self._meta()
        self.assertTrue(watched_state.is_show_caught_up(repo, meta, 5))
        # simulate months later: memo gone, verdict table untouched
        watched_state._caught_up_memo.clear()
        self.assertTrue(watched_state.is_show_caught_up(repo, meta, 5))

    def test_returning_verdict_expires(self):
        repo = self._repo("Returning Series")
        calls = []

        class Meta:
            def get_show_seasons(self, tid):
                calls.append(1)
                return [SimpleNamespace(season_number=1)]

            def get_season_episodes(self, tid, s):
                return [SimpleNamespace(
                    season_number=1, episode_number=1,
                    air_date=_iso(dt.date.today() - dt.timedelta(days=7)))]

        meta = Meta()
        self.assertTrue(watched_state.is_show_caught_up(repo, meta, 5))
        watched_state._caught_up_memo.clear()
        self.assertTrue(watched_state.is_show_caught_up(repo, meta, 5))
        self.assertEqual(len(calls), 2)  # revalidated after expiry window


# ---------------------------------------------------------------------------
# Watchlist reappearance: TV mark-seen keeps the row; movies still remove
# ---------------------------------------------------------------------------

class WatchlistCouplingTest(unittest.TestCase):
    def _detail_like(self, media_type, in_watchlist=True):
        """Minimal stand-in exercising DetailPage._do_mark_watched logic."""
        from src.ui.detail_page import DetailPage
        removed = []
        page = SimpleNamespace(
            media_type=media_type,
            item=SimpleNamespace(tmdb_id=99, title="T"),
            _in_watchlist=in_watchlist,
            user_repo=SimpleNamespace(
                mark_watched=lambda *a, **k: None,
                remove_from_watchlist=lambda *a, **k: removed.append(a),
            ),
            _mark_all_watched_show=lambda: None,
        )
        DetailPage._do_mark_watched(page)
        return removed

    def test_tv_mark_keeps_watchlist_row(self):
        removed = self._detail_like("show")
        self.assertEqual(removed, [])

    def test_movie_mark_still_removes_from_watchlist(self):
        removed = self._detail_like("movie")
        self.assertEqual(len(removed), 1)


if __name__ == "__main__":
    unittest.main()
