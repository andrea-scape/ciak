"""Search results must yield genre names for the chip row.

TMDB search endpoints return only numeric `genre_ids` (the full named
`genres` list exists on detail responses), so live search items rely on
the id→name mapping in genre_chips. If that chain breaks the chip row
silently renders nothing.
"""

import sys
import types
import unittest

if "src.config" not in sys.modules:
    _cfg = types.ModuleType("src.config")
    _cfg.APP_ID = "io.github.andrea_scape.ciak.Devel"
    _cfg.APP_VERSION = "0.0.0-test"
    _cfg.APP_NAME = "Ciak"
    sys.modules["src.config"] = _cfg

from src.data.tmdb.service import TmdbMetadataService
from src.ui.genre_chips import GenreChipsRow, item_genre_names, matches_all


class _FakeClient:
    def _image_url(self, path, *size):
        return path


def _service():
    # Skip __init__: only the pure raw→model mapping is under test.
    svc = object.__new__(TmdbMetadataService)
    svc._client = _FakeClient()
    return svc


_MOVIE_SEARCH_RAW = {
    "id": 11,
    "title": "Star Wars",
    "release_date": "1977-05-25",
    "poster_path": "/x.jpg",
    "overview": "…",
    # exactly what /search/movie returns: ids only, no names
    "genre_ids": [28, 12, 878],
}

_SHOW_SEARCH_RAW = {
    "id": 1399,
    "name": "Game of Thrones",
    "first_air_date": "2011-04-17",
    "poster_path": "/y.jpg",
    "overview": "…",
    "genre_ids": [18, 10765],
}


class SearchGenrePipelineTest(unittest.TestCase):
    def test_movie_search_raw_yields_genre_names(self):
        movie = _service()._raw_to_movie(dict(_MOVIE_SEARCH_RAW))
        names = item_genre_names(movie)
        self.assertIn("Action", names)
        self.assertIn("Adventure", names)
        self.assertIn("Science Fiction", names)

    def test_show_search_raw_yields_genre_names(self):
        show = _service()._raw_to_show(dict(_SHOW_SEARCH_RAW))
        names = item_genre_names(show)
        self.assertIn("Drama", names)
        self.assertIn("Sci-Fi & Fantasy", names)

    def test_search_results_feed_chip_pool(self):
        svc = _service()
        movies = [svc._raw_to_movie(dict(_MOVIE_SEARCH_RAW))]
        shows = [svc._raw_to_show(dict(_SHOW_SEARCH_RAW))]
        pool = sorted({
            g for it in movies + shows for g in item_genre_names(it)
        })
        row = GenreChipsRow()
        row.set_genres(iter(pool))
        self.assertTrue(row.get_visible())
        self.assertGreaterEqual(
            len(pool), 4,
            f"chip pool unexpectedly small: {pool}")

    def test_filtering_by_searched_genre_matches_item(self):
        svc = _service()
        movie = svc._raw_to_movie(dict(_MOVIE_SEARCH_RAW))
        self.assertTrue(matches_all(movie, {"Action", "Adventure"}))
        self.assertFalse(matches_all(movie, {"Horror"}))


if __name__ == "__main__":
    unittest.main()
