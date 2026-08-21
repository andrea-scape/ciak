"""Collections data layer: client endpoint, service conversion/cache,
cache table + migration."""

import os
import tempfile
import unittest
from unittest import mock

import httpx

from src.data.local.cache import MetadataCache
from src.data.tmdb.client import TmdbClient, TMDB_BASE
from src.data.tmdb.service import TmdbMetadataService
from src.domain.exceptions import NetworkError
from src.domain.models import Collection, Movie


RAW_COLLECTION = {
    "id": 1241,
    "name": "Harry Potter",
    "overview": "Wizards.",
    "poster_path": "/hp.jpg",
    "backdrop_path": "/hp-bd.jpg",
    "parts": [
        {"id": 2, "title": "Chamber", "release_date": "2002-11-15",
         "poster_path": "/c2.jpg", "overview": "", "vote_average": 0,
         "vote_count": 0},
        {"id": 1, "title": "Philosopher's Stone", "release_date": "2001-11-16",
         "poster_path": "/c1.jpg", "overview": "", "vote_average": 0,
         "vote_count": 0},
    ],
}


class CollectionClientTest(unittest.TestCase):
    def test_collection_path(self):
        client = TmdbClient(api_key="k")
        fake = mock.Mock()
        fake.get.return_value = mock.Mock()
        fake.get.return_value.json.return_value = RAW_COLLECTION
        client._http = mock.Mock(return_value=fake)
        data = client.get_collection(1241)
        fake.get.assert_called_once_with(
            f"{TMDB_BASE}/collection/1241",
            params=mock.ANY,
        )
        self.assertEqual(data["id"], 1241)
        self.assertEqual(len(data["parts"]), 2)


class CollectionServiceTest(unittest.TestCase):
    def _service(self, raw=None):
        client = mock.Mock()
        client._image_url.side_effect = lambda path, size=None: f"img/{size}{path}"
        client.get_collection.return_value = raw or RAW_COLLECTION
        cache = mock.Mock()
        cache.get_collection.return_value = None
        return TmdbMetadataService(client, cache), client

    def _collection(self):
        service, client = self._service()
        col = service.get_collection(1241)
        client.get_collection.assert_called_once_with(1241)
        return col

    def test_get_collection_builds_parts_and_caches(self):
        service, cache_client = self._service()
        col = service.get_collection(1241)
        self.assertIsInstance(col, Collection)
        self.assertEqual(col.name, "Harry Potter")
        self.assertEqual(col.overview, "Wizards.")
        self.assertEqual(col.collection_id, 1241)
        self.assertEqual(col.backdrop_path, "img/w780/hp-bd.jpg")
        self.assertEqual([m.tmdb_id for m in col.parts], [1, 2])
        self.assertEqual(
            [m.title for m in col.parts],
            ["Philosopher's Stone", "Chamber"],
        )
        service._cache.put_collection.assert_called_once_with(col)

    def test_cached_collection_returned(self):
        cached = Collection(collection_id=1241, name="HP", parts=[])
        client = mock.Mock()
        cache = mock.Mock()
        cache.get_collection.return_value = cached
        service = TmdbMetadataService(client, cache)
        col = service.get_collection(1241)
        client.get_collection.assert_not_called()
        self.assertIs(col, cached)

    def test_sorted_by_release_date_oldest_first(self):
        col = self._collection()
        dates = [m.release_date for m in col.parts]
        self.assertEqual(dates, sorted(dates))

    def test_network_error_raises(self):
        service, client = self._service()
        client.get_collection.side_effect = httpx.HTTPError("boom")
        with self.assertRaises(NetworkError):
            service.get_collection(1241)

    def test_raw_to_movie_maps_collection_name(self):
        service, _ = self._service()
        movie = service._raw_to_movie(
            {"id": 1, "title": "X", "belongs_to_collection": {"id": 1241, "name": "HP"}}
        )
        self.assertEqual(movie.collection_id, 1241)
        self.assertEqual(movie.collection_name, "HP")


class RelatedCollectionTest(unittest.TestCase):
    def _service(self, movie, collection):
        client = mock.Mock()
        cache = mock.Mock()
        cache.get_media.return_value = movie
        cache.get_collection.return_value = collection
        service = TmdbMetadataService(client, cache)
        return service, client

    def test_related_uses_cached_collection(self):
        parts = [
            Movie(tmdb_id=1, title="P1", year=2000),
            Movie(tmdb_id=2, title="P2", year=2001),
            Movie(tmdb_id=3, title="P3", year=2002),
        ]
        movie = Movie(
            tmdb_id=2, title="P2", year=2001, genre_ids=[],
            collection_id=1241,
        )
        collection = Collection(collection_id=1241, name="C", parts=parts)
        service, client = self._service(movie, collection)
        client.get_movie_similar.return_value = {
            "results": [{"id": 9, "title": "Other", "release_date": "1985-01-01",
                         "overview": "", "vote_average": 0, "vote_count": 0}]
        }
        related = service.get_related_movies(2)
        client.get_collection.assert_not_called()
        self.assertEqual([m.tmdb_id for m in related], [1, 3])

    def test_related_falls_back_to_similar_when_collection_fails(self):
        movie = Movie(tmdb_id=2, title="P2", year=2001, genre_ids=[], collection_id=1241)
        client = mock.Mock()
        cache = mock.Mock()
        cache.get_media.return_value = movie
        cache.get_collection.return_value = None
        client.get_collection.side_effect = httpx.HTTPError("boom")
        client.get_movie_similar.return_value = {
            "results": [{"id": 9, "title": "Other", "release_date": "1995-01-01",
                         "overview": "", "vote_average": 0, "vote_count": 0}]
        }
        service = TmdbMetadataService(client, cache)
        related = service.get_related_movies(2)
        self.assertEqual([m.tmdb_id for m in related], [9])


class CollectionCacheTest(unittest.TestCase):
    def _collection(self):
        return Collection(
            collection_id=1241,
            name="Harry Potter",
            overview="Wizards.",
            poster_path="/hp.jpg",
            backdrop_path="/hp-bd.jpg",
            parts=[
                Movie(tmdb_id=1, title="One", release_date="2001-11-16",
                      collection_id=1241, collection_name="Harry Potter"),
                Movie(tmdb_id=2, title="Two", release_date="2002-11-15"),
            ],
        )

    def test_put_get_collection_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "db.sqlite")
            cache = MetadataCache(db)
            col = self._collection()
            cache.put_collection(col)
            got = cache.get_collection(1241)
            self.assertIsNotNone(got)
            self.assertEqual(got.name, "Harry Potter")
            self.assertEqual(got.backdrop_path, "/hp-bd.jpg")
            self.assertEqual([m.tmdb_id for m in got.parts], [1, 2])
            self.assertEqual(got.parts[0].collection_name, "Harry Potter")

    def test_missing_returns_none(self):
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "db.sqlite")
            cache = MetadataCache(db)
            self.assertIsNone(cache.get_collection(1241))

    def test_expired_returns_none(self):
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "db.sqlite")
            cache = MetadataCache(db)
            cache.put_collection(self._collection())
            # A fresh cache with a negative TTL always treats rows as expired.
            stale = MetadataCache(db, ttl_seconds=-1)
            self.assertIsNone(stale.get_collection(1241))


if __name__ == "__main__":
    unittest.main()