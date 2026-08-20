"""Feature 4: streaming availability data layer.

Client endpoints, domain models, service conversion + error handling, and
the watch_providers cache column.
"""

import os
import sqlite3
import tempfile
import unittest
from unittest import mock

import httpx

from src.data.local.cache import MetadataCache
from src.data.tmdb.client import TmdbClient, TMDB_BASE
from src.data.tmdb.service import TmdbMetadataService
from src.domain.exceptions import NetworkError
from src.domain.models import StreamingInfo, StreamingProvider


RAW_PROVIDERS = {
    "id": 550,
    "results": {
        "IT": {
            "link": "https://www.themoviedb.org/movie/550/watch",
            "flatrate": [
                {"provider_id": 8, "provider_name": "Netflix",
                 "logo_path": "/nn.jpg", "display_priority": 0},
            ],
            "ads": [
                {"provider_id": 300, "provider_name": "Pluto TV",
                 "logo_path": "/pl.jpg", "display_priority": 1},
            ],
        }
    },
}


class StreamingClientTest(unittest.TestCase):
    def test_movie_watch_providers_path(self):
        client = TmdbClient(api_key="k")
        fake = mock.Mock()
        fake.get.return_value = mock.Mock()
        fake.get.return_value.json.return_value = RAW_PROVIDERS
        client._http = mock.Mock(return_value=fake)
        data = client.get_movie_watch_providers(550)
        fake.get.assert_called_once_with(
            f"{TMDB_BASE}/movie/550/watch/providers",
            params=mock.ANY,
        )
        self.assertEqual(data["id"], 550)

    def test_tv_watch_providers_path(self):
        client = TmdbClient(api_key="k")
        fake = mock.Mock()
        fake.get.return_value = mock.Mock()
        fake.get.return_value.json.return_value = RAW_PROVIDERS
        client._http = mock.Mock(return_value=fake)
        data = client.get_tv_watch_providers(999)
        fake.get.assert_called_once_with(
            f"{TMDB_BASE}/tv/999/watch/providers",
            params=mock.ANY,
        )
        self.assertEqual(data["results"]["IT"]["flatrate"][0]["provider_id"], 8)


class StreamingServiceTest(unittest.TestCase):
    def _service(self, raw=None):
        client = mock.Mock()
        client._image_url.side_effect = lambda path, size=None: f"img/{size}{path}"
        client.get_movie_watch_providers.return_value = raw or RAW_PROVIDERS
        client.get_tv_watch_providers.return_value = raw or RAW_PROVIDERS
        cache = mock.Mock()
        cache.get_watch_providers.return_value = None
        return TmdbMetadataService(client, cache), client

    def test_get_movie_streaming_builds_providers(self):
        service, client = self._service()
        info = service.get_movie_streaming(550, "IT")
        client.get_movie_watch_providers.assert_called_once_with(550)
        self.assertIsInstance(info, StreamingInfo)
        self.assertEqual(info.country_code, "IT")
        self.assertEqual([p.provider_id for p in info.flatrate], [8])
        self.assertEqual(info.flatrate[0].logo_url, "img/w92/nn.jpg")
        self.assertEqual(info.flatrate[0].offering_type, "flatrate")
        self.assertEqual([p.provider_id for p in info.ads], [300])
        self.assertEqual(info.rent, [])
        self.assertEqual(info.buy, [])

    def test_get_show_streaming_uses_tv_endpoint(self):
        service, client = self._service()
        info = service.get_show_streaming(999, "IT")
        client.get_tv_watch_providers.assert_called_once_with(999)
        self.assertEqual(info.country_code, "IT")

    def test_missing_region_returns_none(self):
        service, _ = self._service({"id": 550, "results": {}})
        self.assertIsNone(service.get_movie_streaming(550, "IT"))

    def test_region_without_offerings_returns_empty_info(self):
        service, _ = self._service({"id": 550, "results": {"IT": {"link": "x"}}})
        info = service.get_movie_streaming(550, "IT")
        self.assertIsInstance(info, StreamingInfo)
        self.assertEqual(info.flatrate, [])
        self.assertEqual(info.ads, [])

    def test_network_error_raises(self):
        service, client = self._service()
        client.get_movie_watch_providers.side_effect = httpx.HTTPError("boom")
        with self.assertRaises(NetworkError):
            service.get_movie_streaming(550, "IT")

    def test_cached_info_is_returned(self):
        client = mock.Mock()
        cache = mock.Mock()
        cache.get_watch_providers.return_value = StreamingInfo(
            country_code="IT", flatrate=[], rent=[], buy=[], ads=[], free=[]
        )
        service = TmdbMetadataService(client, cache)
        info = service.get_movie_streaming(550, "IT")
        client.get_movie_watch_providers.assert_not_called()
        self.assertEqual(info.country_code, "IT")


class StreamingCacheTest(unittest.TestCase):
    def _db(self, d):
        db = os.path.join(d, "s.sqlite")
        conn = sqlite3.connect(db)
        conn.execute(
            """CREATE TABLE media_items (
                tmdb_id INTEGER PRIMARY KEY,
                media_type TEXT, title TEXT, year INTEGER, overview TEXT,
                runtime INTEGER, rating REAL, votes INTEGER, poster_url TEXT,
                backdrop_url TEXT, imdb_id TEXT, genres TEXT, genre_ids TEXT,
                cached_at INTEGER, updated_at INTEGER, release_date TEXT,
                collection_id INTEGER, next_episode_air_date TEXT,
                next_episode_season INTEGER, next_episode_number INTEGER,
                next_episode_name TEXT, next_episode_still TEXT,
                budget INTEGER, revenue INTEGER, creators TEXT)"""
        )
        conn.execute(
            """INSERT INTO media_items (tmdb_id, media_type, title, year,
               cached_at, updated_at) VALUES (550, 'movie', 'Fight Club',
               1999, 1, 1)"""
        )
        conn.commit()
        conn.close()
        return db

    def test_migration_adds_watch_providers_column(self):
        with tempfile.TemporaryDirectory() as d:
            db = self._db(d)
            cache = MetadataCache(db)

            cols = {r["name"] for r in cache._primary_conn.execute(
                "PRAGMA table_info(media_items)").fetchall()}
            self.assertIn("watch_providers", cols)
            self.assertIn("providers_cached_at", cols)
            row = cache._primary_conn.execute(
                "SELECT title FROM media_items WHERE tmdb_id=550").fetchone()
            self.assertEqual(row["title"], "Fight Club")

    def test_put_get_watch_providers_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            db = self._db(d)
            cache = MetadataCache(db)

            info = StreamingInfo(
                country_code="IT",
                flatrate=[StreamingProvider(8, "Netflix", "u", 0, "flatrate")],
                rent=[], buy=[], ads=[], free=[],
            )
            cache.put_watch_providers(550, info)
            got = cache.get_watch_providers(550, "IT")
            self.assertIsNotNone(got)
            self.assertEqual(got.country_code, "IT")
            self.assertEqual([p.provider_id for p in got.flatrate], [8])

    def test_missing_info_returns_none(self):
        with tempfile.TemporaryDirectory() as d:
            db = self._db(d)
            cache = MetadataCache(db)

            self.assertIsNone(cache.get_watch_providers(550, "IT"))


if __name__ == "__main__":
    unittest.main()