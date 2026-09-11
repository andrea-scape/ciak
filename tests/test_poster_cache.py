"""poster_cache: source files and pre-scaled thumbnails share one dir."""

import hashlib
import os
import tempfile
import unittest
from unittest import mock

from src import poster_cache

URL = "https://example.invalid/p/w500/x.jpg"


class PosterCacheTest(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        cachedir = os.path.join(self._td.name, "posters")
        patch_dir = mock.patch.object(poster_cache, "_CACHE_DIR", cachedir)
        patch_dir.start()
        self.addCleanup(patch_dir.stop)
        patch_max = mock.patch.object(
            poster_cache, "_max_cache_bytes", return_value=1024 ** 3
        )
        patch_max.start()
        self.addCleanup(patch_max.stop)
        patch_prune = mock.patch.object(poster_cache, "_last_prune", 0.0)
        patch_prune.start()
        self.addCleanup(patch_prune.stop)

    def test_scaled_roundtrip_and_key_shape(self):
        poster_cache.put_scaled(URL, 160, 240, b"thumb-bytes")
        path = poster_cache.get_scaled(URL, 160, 240)
        self.assertIsNotNone(path)
        with open(path, "rb") as f:
            self.assertEqual(f.read(), b"thumb-bytes")
        expected = hashlib.sha256(URL.encode()).hexdigest() + "@160x240.jpg"
        self.assertEqual(os.path.basename(path), expected)

    def test_scaled_miss_returns_none(self):
        self.assertIsNone(poster_cache.get_scaled(URL, 160, 240))

    def test_scaled_does_not_collide_with_source(self):
        poster_cache.put(URL, b"source")
        poster_cache.put_scaled(URL, 160, 240, b"small")
        self.assertEqual(
            os.path.basename(poster_cache.get(URL)),
            hashlib.sha256(URL.encode()).hexdigest() + ".jpg",
        )
        self.assertIsNone(poster_cache.get_scaled(URL, 161, 241))

    def test_invalidate_removes_base_and_scaled_siblings(self):
        poster_cache.put(URL, b"jpg")
        poster_cache.put_scaled(URL, 160, 240, b"a")
        poster_cache.put_scaled(URL, 320, 480, b"b")
        poster_cache.invalidate(URL)
        self.assertIsNone(poster_cache.get(URL))
        self.assertIsNone(poster_cache.get_scaled(URL, 160, 240))
        self.assertIsNone(poster_cache.get_scaled(URL, 320, 480))

    def test_get_size_counts_scaled_entries(self):
        poster_cache.put(URL, b"12345")
        poster_cache.put_scaled(URL, 160, 240, b"123")
        self.assertEqual(poster_cache.get_size(), 8)

    def test_clear_wipes_scaled_entries(self):
        poster_cache.put_scaled(URL, 160, 240, b"x")
        poster_cache.clear()
        self.assertIsNone(poster_cache.get_scaled(URL, 160, 240))


if __name__ == "__main__":
    unittest.main()