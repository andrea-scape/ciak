"""Performance caching layers: persistent watched-verdicts (A), trending
disk cache (B), session search LRU (C), profile stats memoization (D)."""

import os
import sqlite3
import sys
import tempfile
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

import datetime

from src.data.local.cache import MetadataCache
from src.data.local.repository import LocalMediaRepository
from src.data.tmdb.service import TmdbMetadataService
from src.ui import watched_state


def _iso(d):
    return d.isoformat()


# ---------------------------------------------------------------------------
# A · persistent watched-verdicts
# ---------------------------------------------------------------------------

class VerdictRepoRoundtripTest(unittest.TestCase):
    def test_roundtrip_and_fingerprint_mismatch(self):
        with tempfile.TemporaryDirectory() as d:
            repo = LocalMediaRepository(os.path.join(d, "db.sqlite"))
            repo.initialize()
            self.assertIsNone(
                repo.get_watched_verdict(7, "caught_up", "n1"))
            repo.store_watched_verdict(7, "caught_up", True, "n1")
            self.assertTrue(repo.get_watched_verdict(7, "caught_up", "n1"))
            # different fingerprint (watched count changed) → miss
            self.assertIsNone(repo.get_watched_verdict(7, "caught_up", "n2"))


class PersistedVerdictTest(unittest.TestCase):
    def _repo(self):
        class Repo:
            data_version = 1

            def __init__(self):
                self._count = 1
                self._verdicts = {}

            def get_watched_episodes_for_show(self, show_id):
                return {(1, i) for i in range(1, self._count + 1)}

            def get_watched_verdict(self, sid, kind, fp, max_age_s=None):
                return self._verdicts.get((sid, kind, fp))

            def store_watched_verdict(self, sid, kind, verdict, fp):
                self._verdicts[(sid, kind, fp)] = verdict

        return Repo()

    def _meta(self, eps):
        today = datetime.date.today()
        return _FakeMeta([(1, 1, _iso(today - datetime.timedelta(days=7)))])

    def test_second_session_answers_without_metadata(self):
        watched_state._caught_up_memo.clear()
        repo = self._repo()
        eps = [(1, 1, _iso(datetime.date.today() - datetime.timedelta(days=7)))]

        class Meta(_FakeMeta):
            pass

        meta = _FakeMeta(eps)
        self.assertTrue(watched_state.is_show_caught_up(repo, meta, 5))
        # simulate an app restart: memo gone, metadata unreachable
        watched_state._caught_up_memo.clear()

        class DeadMeta:
            def get_show_seasons(self, tmdb_id):
                raise AssertionError("metadata must not be needed")

        self.assertTrue(watched_state.is_show_caught_up(repo, DeadMeta(), 5))
        watched_state._caught_up_memo.clear()

    def test_new_watched_episode_invalidates_fingerprint(self):
        watched_state._caught_up_memo.clear()
        repo = self._repo()
        today = datetime.date.today()
        eps = [(1, 1, _iso(today - datetime.timedelta(days=7)))]
        self.assertTrue(watched_state.is_show_caught_up(repo, _FakeMeta(eps), 5))
        self.assertIn(
            "v2n1", [fp for (_s, _k, fp) in repo._verdicts],
            "first verdict should persist under the initial fingerprint")

        # User watches another episode: watched count changes (fingerprint)
        # and data_version bumps (in-memory memo key) — production does
        # both together on every mutation.
        repo._count = 2
        repo.data_version = 2
        eps2 = eps + [(1, 2, _iso(today - datetime.timedelta(days=6)))]
        self.assertTrue(watched_state.is_show_caught_up(repo, _FakeMeta(eps2), 5))
        self.assertIn(
            "v2n2", [fp for (_s, _k, fp) in repo._verdicts],
            "mutated state must recompute and store a new fingerprint")
        watched_state._caught_up_memo.clear()


class _FakeMeta:
    """Minimal metadata double matching the caught-up compute path."""

    def __init__(self, episodes):
        self._episodes = episodes

    def get_show_seasons(self, tmdb_id):
        seasons = sorted({s for s, _e, _d in self._episodes})
        return [SimpleNamespace(season_number=s) for s in seasons if s > 0]

    def get_season_episodes(self, tmdb_id, season_number):
        return [
            SimpleNamespace(season_number=s, episode_number=e, air_date=d)
            for s, e, d in self._episodes
            if s == season_number
        ]


# ---------------------------------------------------------------------------
# B · trending disk cache
# ---------------------------------------------------------------------------

class TrendingDiskCacheTest(unittest.TestCase):
    def test_roundtrip_within_ttl_and_expiry(self):
        with tempfile.TemporaryDirectory() as d:
            cache = MetadataCache(os.path.join(d, "db.sqlite"))
            payload = [{"id": 1, "title": "X"}]
            cache.put_trending_payloads("trending_all", payload)
            self.assertEqual(
                cache.get_trending_payloads("trending_all"), payload)
            # ttl 0 → instantly stale
            self.assertIsNone(
                cache.get_trending_payloads("trending_all", ttl_seconds=0))


class ServiceTrendingCacheTest(unittest.TestCase):
    def _service(self):
        client = mock.MagicMock()
        cache = mock.MagicMock()
        cache.get_trending_payloads.return_value = None
        svc = TmdbMetadataService(client, cache)
        return svc, client, cache

    def test_trending_payload_cached_after_fetch(self):
        svc, client, cache = self._service()
        client.get_trending.return_value = {
            "results": [{"id": 9, "title": "T",
                         "media_type": "movie", "genre_ids": []}],
        }
        svc.get_trending("all")
        cache.put_trending_payloads.assert_called_once()
        key, items = cache.put_trending_payloads.call_args.args
        self.assertEqual(key, "trending_all")

    def test_fresh_cache_skips_network(self):
        svc, client, cache = self._service()
        cache.get_trending_payloads.return_value = [
            {"id": 9, "title": "T", "media_type": "movie", "genre_ids": [28]},
        ]
        movies = svc.get_trending("movie")
        client.get_trending.assert_not_called()
        self.assertEqual(len(movies), 1)


# ---------------------------------------------------------------------------
# C · session search-query LRU
# ---------------------------------------------------------------------------

class SearchLruTest(unittest.TestCase):
    def _svc(self, results):
        client = mock.MagicMock()
        client.search_movie.return_value = {"results": results}
        cache = mock.MagicMock()
        return TmdbMetadataService(client, cache), client

    def test_repeat_query_hits_lru_once(self):
        svc, client = self._svc([{"id": 1, "title": "A", "genre_ids": []}])
        r1 = svc.search_movies("Dune")
        r2 = svc.search_movies("dune ")  # same normalized query
        client.search_movie.assert_called_once()
        self.assertIs(r1, r2)

    def test_cap_evicts_oldest(self):
        svc, client = self._svc([{"id": 1, "title": "A", "genre_ids": []}])
        svc._SEARCH_LRU_CAP = 2
        svc.search_movies("a")
        svc.search_movies("b")
        first_result = svc._search_lru[("movie", "a")]
        svc.search_movies("c")  # evicts "a"
        self.assertNotIn(("movie", "a"), svc._search_lru)
        self.assertIsNot(svc._lru_get(("movie", "a")), first_result)


# ---------------------------------------------------------------------------
# D · profile stats memoization
# ---------------------------------------------------------------------------

class StatsMemoTest(unittest.TestCase):
    def test_stats_memoized_until_version_bump(self):
        with tempfile.TemporaryDirectory() as d:
            repo = LocalMediaRepository(os.path.join(d, "db.sqlite"))
            repo.initialize()
            s1 = repo.get_stats()
            s2 = repo.get_stats()
            self.assertIs(s1, s2)
            repo.mark_watched(42, "movie")  # bumps data_version
            s3 = repo.get_stats()
            self.assertIsNot(s1, s3)

    def test_runtime_memoized(self):
        with tempfile.TemporaryDirectory() as d:
            repo = LocalMediaRepository(os.path.join(d, "db.sqlite"))
            repo.initialize()
            r1 = repo.get_watched_runtime()
            r2 = repo.get_watched_runtime()
            self.assertIs(r1, r2)
            self.assertIsInstance(r1, int)


if __name__ == "__main__":
    unittest.main()
