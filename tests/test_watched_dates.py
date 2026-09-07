import os
import sqlite3
import tempfile
import time
import unittest

from src.data.local.repository import LocalMediaRepository


class WatchedDatesTest(unittest.TestCase):
    def _repo(self, directory):
        db = os.path.join(directory, "db.sqlite")
        repo = LocalMediaRepository(db)
        repo.initialize()
        return repo, db

    def _insert_episode(self, db, tmdb_id, season, episode, watched_at):
        conn = sqlite3.connect(db)
        try:
            conn.execute(
                "INSERT INTO watched_items "
                "(tmdb_id, media_type, show_tmdb_id, season_number, "
                "episode_number, watched_at) VALUES (?, 'episode', 7, ?, ?, ?)",
                (tmdb_id, season, episode, watched_at),
            )
            conn.commit()
        finally:
            conn.close()

    def test_get_watched_at_movie_returns_timestamp_and_none_for_other(self):
        with tempfile.TemporaryDirectory() as d:
            repo, db = self._repo(d)
            repo.mark_watched(42, "movie")
            ts = repo.get_watched_at(42, "movie")
            self.assertIsInstance(ts, int)
            self.assertAlmostEqual(ts, int(time.time()), delta=60)
            self.assertIsNone(repo.get_watched_at(43, "movie"))

    def test_get_watched_episode_dates_maps_inserted_rows(self):
        with tempfile.TemporaryDirectory() as d:
            repo, db = self._repo(d)
            self._insert_episode(db, 900, 1, 1, 1700000000)
            self._insert_episode(db, 901, 2, 5, 1800000000)
            dates = repo.get_watched_episode_dates(7)
            self.assertEqual(
                dates,
                {(1, 1): 1700000000, (2, 5): 1800000000},
            )
            self.assertEqual(repo.get_watched_episode_dates(9999), {})

    def test_get_watched_at_matches_episode_triple_ts(self):
        with tempfile.TemporaryDirectory() as d:
            repo, db = self._repo(d)
            self._insert_episode(db, 900, 3, 4, 1750000123)
            ts = repo.get_watched_at(
                900, "episode", show_tmdb_id=7, season_number=3,
                episode_number=4,
            )
            self.assertEqual(ts, 1750000123)

    def test_mark_watched_skips_season_zero(self):
        with tempfile.TemporaryDirectory() as d:
            repo, db = self._repo(d)
            repo.mark_watched(
                950, "movie", show_tmdb_id=7, season_number=0, episode_number=3
            )
            self.assertEqual(repo.get_watched_episode_dates(7), {})

    def test_mark_watched_keeps_season_one(self):
        with tempfile.TemporaryDirectory() as d:
            repo, db = self._repo(d)
            repo.mark_watched(
                951, "movie", show_tmdb_id=7, season_number=1, episode_number=2
            )
            self.assertIn((1, 2), repo.get_watched_episode_dates(7))

    def test_import_watched_skips_season_zero(self):
        with tempfile.TemporaryDirectory() as d:
            repo, db = self._repo(d)
            count = repo.import_watched([
                {
                    "tmdb_id": 960,
                    "media_type": "episode",
                    "title": "Special",
                    "show_tmdb_id": 7,
                    "season_number": 0,
                    "episode_number": 1,
                    "watched_at": 1700000000,
                },
                {
                    "tmdb_id": 961,
                    "media_type": "episode",
                    "title": "Ep",
                    "show_tmdb_id": 7,
                    "season_number": 1,
                    "episode_number": 1,
                    "watched_at": 1700000100,
                },
            ])
            self.assertEqual(count, 1)
            self.assertEqual(repo.get_watched_episode_dates(7), {(1, 1): 1700000100})

    def test_migration_purges_existing_season_zero_rows(self):
        from src.data.local import schema

        with tempfile.TemporaryDirectory() as d:
            repo, db = self._repo(d)
            self._insert_episode(db, 970, 0, 3, 1700000000)
            self._insert_episode(db, 971, 1, 3, 1700000001)
            # Roll the schema back to v4 so initialize() re-applies v5.
            conn = sqlite3.connect(db)
            try:
                conn.execute("DELETE FROM schema_version WHERE version >= 5")
                conn.commit()
            finally:
                conn.close()
            conn = sqlite3.connect(db)
            try:
                schema.initialize(conn)
                remaining = conn.execute(
                    "SELECT COUNT(*) FROM watched_items "
                    "WHERE media_type = 'episode' AND COALESCE(season_number, 0) <= 0"
                ).fetchone()[0]
            finally:
                conn.close()
            self.assertEqual(remaining, 0)


if __name__ == "__main__":
    unittest.main()
