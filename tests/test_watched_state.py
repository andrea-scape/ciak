"""watched_state: still-airing shows are not 'fully watched'."""

import sys
import types
import unittest
from types import SimpleNamespace

if "src.config" not in sys.modules:
    _cfg = types.ModuleType("src.config")
    _cfg.APP_ID = "io.github.andrea_scape.ciak.Devel"
    _cfg.APP_VERSION = "0.0.0-test"
    _cfg.APP_NAME = "Ciak"
    sys.modules["src.config"] = _cfg

import datetime

from src.ui import watched_state


class _FakeRepo:
    def __init__(self, watched):
        self._watched = watched

    def get_watched_episodes_for_show(self, tmdb_id):
        return set(self._watched)


class _FakeMetadata:
    """episodes: list of (season, episode, iso_air_date);
    next_air: iso string or None"""

    def __init__(self, episodes, next_air=None, status="Ended"):
        self._episodes = episodes
        self._next_air = next_air
        self._status = status

    def get_show_seasons(self, tmdb_id):
        seasons = sorted({s for s, _e, _d in self._episodes})
        return [SimpleNamespace(season_number=s) for s in seasons if s > 0]

    def get_season_episodes(self, tmdb_id, season_number):
        return [
            SimpleNamespace(
                season_number=s, episode_number=e, air_date=d,
            )
            for s, e, d in self._episodes
            if s == season_number
        ]

    def get_show(self, tmdb_id):
        return SimpleNamespace(next_episode_air_date=self._next_air,
                               status=self._status)


def _iso(d):
    return d.isoformat()


class StillAiringShowTest(unittest.TestCase):
    def setUp(self):
        self.today = __import__("datetime").date.today()
        # S01E01 aired last week, fully watched
        self.aired = [
            (1, 1, _iso(self.today - __import__("datetime").timedelta(days=7))),
        ]
        self.watched = {(1, 1)}

    def test_returning_series_with_next_episode_not_fully_watched(self):
        meta = _FakeMetadata(self.aired,
                             next_air=_iso(self.today + __import__("datetime").timedelta(days=3)))
        result = watched_state.is_show_fully_watched(
            _FakeRepo(self.watched), meta, 95350)
        self.assertFalse(result)

    def test_future_scheduled_episode_not_fully_watched(self):
        eps = self.aired + [
            (1, 2, _iso(self.today + __import__("datetime").timedelta(days=2))),
        ]
        meta = _FakeMetadata(eps)
        result = watched_state.is_show_fully_watched(
            _FakeRepo(self.watched), meta, 95350)
        self.assertFalse(result)

    def test_ended_and_fully_watched_still_true(self):
        meta = _FakeMetadata(self.aired)
        result = watched_state.is_show_fully_watched(
            _FakeRepo(self.watched), meta, 95350)
        self.assertTrue(result)

    def test_unwatched_aired_episode_false(self):
        eps = self.aired + [
            (1, 2, _iso(self.today - __import__("datetime").timedelta(days=1))),
        ]
        meta = _FakeMetadata(eps)
        result = watched_state.is_show_fully_watched(
            _FakeRepo(self.watched), meta, 95350)
        self.assertFalse(result)




class MemoVersionTest(unittest.TestCase):
    def test_memo_serves_repeat_and_invalidates_on_version(self):
        calls = []

        class Repo:
            data_version = 0

            def get_watched_episodes_for_show(self, tmdb_id):
                return set()

        class Meta:
            def get_show_seasons(self, tmdb_id):
                return []

        repo = Repo()
        meta = Meta()
        orig = watched_state._compute_is_show_fully_watched

        def counting(ur, ms, show_id):
            calls.append(show_id)
            return False

        watched_state._compute_is_show_fully_watched = counting
        try:
            r1 = watched_state.is_show_fully_watched(repo, meta, 1)
            r2 = watched_state.is_show_fully_watched(repo, meta, 1)
            self.assertFalse(r1)
            self.assertFalse(r2)
            self.assertEqual(len(calls), 1)  # served from memo
            repo.data_version = 3  # mutation -> new key -> recompute
            watched_state.is_show_fully_watched(repo, meta, 1)
            self.assertEqual(len(calls), 2)
        finally:
            watched_state._compute_is_show_fully_watched = orig
            watched_state._fully_watched_memo.clear()


class CaughtUpTest(unittest.TestCase):
    def setUp(self):
        import datetime
        self.today = datetime.date.today()
        self.aired_week_ago = (1, 1, _iso(self.today - datetime.timedelta(days=7)))

    def _meta(self, episodes, next_air=None):
        return _FakeMetadata(episodes, next_air)

    def test_returning_series_current_on_is_caught_up(self):
        eps = [self.aired_week_ago,
               (1, 2, _iso(self.today + datetime.timedelta(days=3)))]
        meta = self._meta(eps, next_air=_iso(self.today + datetime.timedelta(days=3)))
        repo = _FakeRepo({(1, 1)})
        self.assertTrue(watched_state.is_show_caught_up(repo, meta, 1))

    def test_missed_aired_episode_not_caught_up(self):
        eps = [self.aired_week_ago,
               (1, 2, _iso(self.today - datetime.timedelta(days=1)))]
        meta = self._meta(eps)
        repo = _FakeRepo({(1, 1)})
        self.assertFalse(watched_state.is_show_caught_up(repo, meta, 1))

    def test_no_watched_episodes_false(self):
        meta = self._meta([self.aired_week_ago])
        repo = _FakeRepo(set())
        self.assertFalse(watched_state.is_show_caught_up(repo, meta, 1))

    def test_fully_watched_strict_rule_unchanged_for_watchlist(self):
        # caught-up-but-continuing is NOT "fully watched" (stays in watchlist)
        eps = [self.aired_week_ago,
               (1, 2, _iso(self.today + datetime.timedelta(days=3)))]
        meta = self._meta(eps, next_air=_iso(self.today + datetime.timedelta(days=3)))
        repo = _FakeRepo({(1, 1)})
        self.assertFalse(watched_state.is_show_fully_watched(repo, meta, 1))
        self.assertTrue(watched_state.is_show_caught_up(repo, meta, 1))


class TransientFailureTest(unittest.TestCase):
    """A network outage must answer 'no' but never poison the memo —
    otherwise the page stays badge-less until the TTL expires."""

    def _repo(self):
        class Repo:
            data_version = 1

            def get_watched_episodes_for_show(self, tmdb_id):
                return {(1, 1)}

        return Repo()

    def test_network_error_not_cached(self):
        from src.domain.exceptions import NetworkError

        watched_state._caught_up_memo.clear()
        calls = []

        class Meta:
            def get_show_seasons(self, tmdb_id):
                calls.append(tmdb_id)
                raise NetworkError("offline")

        meta = Meta()
        repo = self._repo()
        self.assertFalse(watched_state.is_show_caught_up(repo, meta, 7))
        self.assertFalse(watched_state.is_show_caught_up(repo, meta, 7))
        # Second call must recompute instead of serving the cached False.
        self.assertEqual(len(calls), 2)
        watched_state._caught_up_memo.clear()

    def test_recovery_after_outage_is_picked_up(self):
        from src.domain.exceptions import NetworkError

        watched_state._caught_up_memo.clear()
        today = datetime.date.today()

        class FlakyMeta(_FakeMetadata):
            def __init__(self, fail):
                super().__init__([(1, 1, _iso(today - datetime.timedelta(days=7)))])
                self.fail = fail

            def get_show_seasons(self, tmdb_id):
                if self.fail:
                    raise NetworkError("offline")
                return super().get_show_seasons(tmdb_id)

        repo = self._repo()
        meta = FlakyMeta(fail=True)
        self.assertFalse(watched_state.is_show_caught_up(repo, meta, 7))
        meta.fail = False
        self.assertTrue(watched_state.is_show_caught_up(repo, meta, 7))
        watched_state._caught_up_memo.clear()


class FanOutTest(unittest.TestCase):
    """caught_up_show_ids streams results per show and survives a broken
    candidate."""

    def _repo(self, show_ids):
        class Repo:
            data_version = None

            def get_watched_show_ids(self):
                return set(show_ids)

            def get_watched_episodes_for_show(self, tmdb_id):
                return {(1, 1)}

        return Repo()

    def test_on_result_fires_per_show(self):
        today = datetime.date.today()
        eps = [(1, 1, _iso(today - datetime.timedelta(days=7)))]
        meta = _FakeMetadata(eps)
        events = []
        result = watched_state.caught_up_show_ids(
            self._repo({3, 4}), meta, {3, 4},
            on_result=lambda sid, ok: events.append((sid, ok)),
        )
        self.assertEqual(result, {3, 4})
        self.assertEqual(sorted(events), [(3, True), (4, True)])

    def test_broken_show_does_not_kill_batch(self):
        eps = [(1, 1, _iso(datetime.date.today() - datetime.timedelta(days=7)))]

        class HalfBrokenMeta(_FakeMetadata):
            def get_show_seasons(self, tmdb_id):
                if tmdb_id == 1:
                    raise RuntimeError("boom")
                return super().get_show_seasons(tmdb_id)

        meta = HalfBrokenMeta(eps)
        events = []
        result = watched_state.caught_up_show_ids(
            self._repo({1, 2}), meta, {1, 2},
            on_result=lambda sid, ok: events.append((sid, ok)),
        )
        self.assertEqual(result, {2})
        self.assertEqual(sorted(events), [(1, False), (2, True)])


class UndatedEpisodeTest(unittest.TestCase):
    """TMDB returns air_date=None for announced/unscheduled episodes;
    they haven't aired, so they must never block a watched check."""

    def setUp(self):
        today = datetime.date.today()
        self.watched = {(1, 1)}
        self.eps = [
            (1, 1, _iso(today - datetime.timedelta(days=7))),
            (1, 2, None),   # announced, undated
            (1, 3, None),
            (2, 1, None),   # whole future season, undated
        ]

    def test_undated_episodes_do_not_block_caught_up(self):
        meta = _FakeMetadata(self.eps)
        self.assertTrue(watched_state.is_show_caught_up(
            _FakeRepo(self.watched), meta, 5))

    def test_undated_episodes_do_not_block_fully_watched(self):
        # no next_episode_air_date and nothing dated-but-unwatched
        meta = _FakeMetadata(self.eps)
        self.assertTrue(watched_state.is_show_fully_watched(
            _FakeRepo(self.watched), meta, 5))

    def test_dated_unwatched_still_blocks(self):
        today = datetime.date.today()
        eps = self.eps + [
            (1, 4, _iso(today - datetime.timedelta(days=1))),
        ]
        meta = _FakeMetadata(eps)
        self.assertFalse(watched_state.is_show_caught_up(
            _FakeRepo(self.watched), meta, 5))


class WholeShowMarkTest(unittest.TestCase):
    """A wholesale 'mark show watched' row in the DB no longer
    short-circuits the verdict.  Episode-level checks always run."""

    def _repo(self):
        class Repo:
            data_version = 1

            def is_whole_show_watched(self, show_id):
                return show_id == 42

            def get_watched_episodes_for_show(self, show_id):
                return set()

        return Repo()

    def test_whole_show_mark_without_episodes_is_not_caught_up(self):
        class Meta:
            def get_show_seasons(self, tmdb_id):
                return []

        self.assertFalse(watched_state.is_show_caught_up(
            self._repo(), Meta(), 42))

    def test_whole_show_mark_without_episodes_is_not_fully_watched(self):
        class Meta:
            def get_show_seasons(self, tmdb_id):
                return []

            def get_show(self, tmdb_id):
                return SimpleNamespace(status="Ended")

        self.assertFalse(watched_state.is_show_fully_watched(
            self._repo(), Meta(), 42))


class UnfilteredCandidatesTest(unittest.TestCase):
    """Caller-provided candidates must be checked as-is: the old
    intersection against get_watched_show_ids silently dropped ids on
    keying mismatches and produced zero badge requests."""

    def test_candidates_checked_even_if_repo_listing_differs(self):
        today = datetime.date.today()
        eps = [(1, 1, _iso(today - datetime.timedelta(days=7)))]

        class Repo:
            data_version = None

            def get_watched_show_ids(self):
                return set()  # listing disagrees on purpose

            def get_watched_episodes_for_show(self, tmdb_id):
                return {(1, 1)}

        meta = _FakeMetadata(eps)
        result = watched_state.caught_up_show_ids(Repo(), meta, {9})
        self.assertEqual(result, {9})


class ReturningShowStatusTest(unittest.TestCase):
    """Ongoing shows (status != ended/canceled) must never be 'fully
    watched' — they stay in the watchlist even when all aired
    episodes are marked."""

    def setUp(self):
        watched_state._fully_watched_memo.clear()
        self.today = datetime.date.today()

    def tearDown(self):
        watched_state._fully_watched_memo.clear()

    def test_ongoing_show_not_fully_watched(self):
        eps = [(1, 1, _iso(self.today - datetime.timedelta(days=7)))]
        meta = _FakeMetadata(eps, status="Returning Series")
        self.assertFalse(watched_state.is_show_fully_watched(
            _FakeRepo({(1, 1)}), meta, 10))

    def test_ended_show_fully_watched(self):
        eps = [(1, 1, _iso(self.today - datetime.timedelta(days=7)))]
        meta = _FakeMetadata(eps, status="Ended")
        self.assertTrue(watched_state.is_show_fully_watched(
            _FakeRepo({(1, 1)}), meta, 10))

    def test_unknown_status_not_fully_watched(self):
        eps = [(1, 1, _iso(self.today - datetime.timedelta(days=7)))]
        meta = _FakeMetadata(eps, status=None)
        self.assertFalse(watched_state.is_show_fully_watched(
            _FakeRepo({(1, 1)}), meta, 10))

    def test_whole_show_mark_on_ongoing_not_fully_watched(self):
        class Repo:
            data_version = 1

            def is_whole_show_watched(self, show_id):
                return True

            def get_watched_episodes_for_show(self, show_id):
                return set()

        class Meta:
            def get_show_seasons(self, tmdb_id):
                return []

            def get_show(self, tmdb_id):
                return SimpleNamespace(status="Returning Series")

        self.assertFalse(watched_state.is_show_fully_watched(
            Repo(), Meta(), 42))

    def test_whole_show_mark_on_ended_still_needs_episodes(self):
        class Repo:
            data_version = 1

            def is_whole_show_watched(self, show_id):
                return True

            def get_watched_episodes_for_show(self, show_id):
                return set()

        class Meta:
            def get_show_seasons(self, tmdb_id):
                return []

            def get_show(self, tmdb_id):
                return SimpleNamespace(status="Ended")

        self.assertFalse(watched_state.is_show_fully_watched(
            Repo(), Meta(), 42))


class ShouldHideShowTest(unittest.TestCase):
    """should_hide_show: caught-up shows hide when ended or with nothing
    airing inside the upcoming window, and the result memoizes."""

    def setUp(self):
        self.today = datetime.date.today()
        watched_state._show_hide_memo.clear()
        watched_state._caught_up_memo.clear()
        watched_state._fully_watched_memo.clear()

    def _repo(self, watched):
        class Repo:
            data_version = 0

            def get_watched_episodes_for_show(self, tmdb_id):
                return set(watched)

        return Repo()

    def _meta(self, episodes, status="Ended", next_air=None):
        return _FakeMetadata(episodes, next_air=next_air, status=status)

    def _aired(self):
        return [(1, 1, _iso(self.today - datetime.timedelta(days=7)))]

    def test_ended_caught_up_show_hides(self):
        self.assertTrue(watched_state.should_hide_show(
            self._repo({(1, 1)}), self._meta(self._aired(), status="Ended"), 5))

    def test_ongoing_with_upcoming_episode_in_window_kept(self):
        eps = self._aired() + [
            (1, 2, _iso(self.today + datetime.timedelta(days=3)))
        ]
        meta = self._meta(eps, status="Returning",
                          next_air=_iso(self.today + datetime.timedelta(days=3)))
        self.assertFalse(watched_state.should_hide_show(
            self._repo({(1, 1)}), meta, 5))

    def test_ongoing_with_next_episode_beyond_window_hides(self):
        meta = self._meta(self._aired(), status="Returning",
                          next_air=_iso(self.today + datetime.timedelta(days=30)))
        self.assertTrue(watched_state.should_hide_show(
            self._repo({(1, 1)}), meta, 5))

    def test_no_next_air_hides(self):
        meta = self._meta(self._aired(), status="Returning", next_air=None)
        self.assertTrue(watched_state.should_hide_show(
            self._repo({(1, 1)}), meta, 5))

    def test_not_caught_up_never_hides(self):
        eps = self._aired() + [
            (1, 2, _iso(self.today - datetime.timedelta(days=1)))
        ]
        self.assertFalse(watched_state.should_hide_show(
            self._repo({(1, 1)}), self._meta(eps, status="Ended"), 5))

    def test_window_override_respected(self):
        meta = self._meta(self._aired(), status="Returning",
                          next_air=_iso(self.today + datetime.timedelta(days=10)))
        self.assertFalse(watched_state.should_hide_show(
            self._repo({(1, 1)}), meta, 5, window_days=14))
        self.assertTrue(watched_state.should_hide_show(
            self._repo({(1, 1)}), meta, 5, window_days=5))

    def test_memoized_without_recompute(self):
        calls = []
        orig = watched_state._compute_is_show_caught_up

        def counting(_ur, _ms, show_id):
            calls.append(show_id)
            return True

        watched_state._compute_is_show_caught_up = counting
        try:
            r1 = watched_state.should_hide_show(
                self._repo({(1, 1)}), self._meta(self._aired()), 5)
            r2 = watched_state.should_hide_show(
                self._repo({(1, 1)}), self._meta(self._aired()), 5)
            self.assertTrue(r1)
            self.assertTrue(r2)
            self.assertEqual(len(calls), 1)
        finally:
            watched_state._compute_is_show_caught_up = orig
            watched_state._show_hide_memo.clear()


class ShouldHideShowIdsTest(unittest.TestCase):
    """should_hide_show_ids fans out and survives mixed verdicts."""

    def setUp(self):
        watched_state._show_hide_memo.clear()
        watched_state._caught_up_memo.clear()
        watched_state._fully_watched_memo.clear()

    def _repo(self, watched):
        today = datetime.date.today()

        class Repo:
            data_version = 0

            def get_watched_episodes_for_show(self, tmdb_id):
                return set(watched.get(tmdb_id, ()))

        return Repo()

    def test_fan_out_returns_only_hidden_ids(self):
        today = datetime.date.today()
        last_week = _iso(today - datetime.timedelta(days=7))
        close_air = _iso(today + datetime.timedelta(days=3))

        class Meta:
            def get_show_seasons(self, tmdb_id):
                if tmdb_id == 1:
                    return [SimpleNamespace(season_number=1)]
                return []

            def get_season_episodes(self, tmdb_id, season_number):
                return [SimpleNamespace(
                    season_number=1, episode_number=1, air_date=last_week)]

            def get_show(self, tmdb_id):
                if tmdb_id == 1:
                    return SimpleNamespace(status="Ended", next_episode_air_date=None)
                return SimpleNamespace(status="Returning",
                                       next_episode_air_date=close_air)

        repo = self._repo({1: {(1, 1)}, 2: {(1, 1)}})
        result = watched_state.should_hide_show_ids(
            repo, Meta(), [1, 2, 3], window_days=5)
        self.assertEqual(result, {1})


if __name__ == "__main__":
    unittest.main()
