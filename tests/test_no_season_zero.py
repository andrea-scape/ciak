import unittest
from types import SimpleNamespace

from src.data.tmdb.service import TmdbMetadataService


def _service():
    """Bare TmdbMetadataService with stubbed client/cache."""
    svc = object.__new__(TmdbMetadataService)
    svc._client = SimpleNamespace(_image_url=lambda p, *a: p)
    stored = {}
    svc._cache = SimpleNamespace(
        get_seasons=lambda _id: None,
        put_seasons=lambda _id, seasons: stored.update(seasons=seasons),
    )
    return svc, stored


class SeasonsFilterTest(unittest.TestCase):
    def test_get_show_seasons_drops_season_zero(self):
        svc, stored = _service()
        svc._client.get_tv = lambda _id: {
            "seasons": [
                {"id": 1, "season_number": 0, "name": "Specials"},
                {"id": 2, "season_number": 1, "name": "Season 1"},
                {"id": 3, "season_number": 2, "name": "Season 2"},
            ]
        }
        seasons = svc.get_show_seasons(7)
        self.assertEqual([s.season_number for s in seasons], [1, 2])
        self.assertEqual(
            [s.season_number for s in stored["seasons"]], [1, 2]
        )

    def test_cached_list_containing_specials_is_filtered(self):
        svc = object.__new__(TmdbMetadataService)
        svc._cache = SimpleNamespace(
            get_seasons=lambda _id: [
                SimpleNamespace(season_number=0),
                SimpleNamespace(season_number=1),
            ]
        )

        def _boom(_id):
            raise AssertionError("network hit despite cache")

        svc._client = SimpleNamespace(get_tv=_boom, _image_url=lambda p, *a: p)
        seasons = svc.get_show_seasons(7)
        self.assertEqual([s.season_number for s in seasons], [1])

    def test_cache_with_only_specials_refetches(self):
        svc, stored = _service()
        svc._cache.get_seasons = lambda _id: [SimpleNamespace(season_number=0)]
        svc._client.get_tv = lambda _id: {
            "seasons": [{"id": 2, "season_number": 1, "name": "Season 1"}]
        }
        seasons = svc.get_show_seasons(7)
        self.assertEqual([s.season_number for s in seasons], [1])


class NextEpisodeGuardTest(unittest.TestCase):
    def _show(self, next_ep):
        svc = object.__new__(TmdbMetadataService)
        svc._client = SimpleNamespace(_image_url=lambda p, *a: p)
        return svc._raw_to_show(
            {
                "id": 5,
                "name": "S",
                "first_air_date": "2026-01-02",
                "genres": [],
                "episode_run_time": [],
                "next_episode_to_air": next_ep,
            }
        )

    def test_specials_next_episode_is_nulled(self):
        show = self._show(
            {
                "season_number": 0,
                "episode_number": 4,
                "air_date": "2026-02-01",
                "name": "Special",
                "still_path": "/x.jpg",
            }
        )
        self.assertIsNone(show.next_episode_season)
        self.assertIsNone(show.next_episode_number)
        self.assertIsNone(show.next_episode_air_date)

    def test_regular_next_episode_is_kept(self):
        show = self._show(
            {
                "season_number": 2,
                "episode_number": 4,
                "air_date": "2026-02-01",
                "name": "Ep",
                "still_path": "/x.jpg",
            }
        )
        self.assertEqual(show.next_episode_season, 2)
        self.assertEqual(show.next_episode_number, 4)


if __name__ == "__main__":
    unittest.main()
