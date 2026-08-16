import os
import sqlite3
import tempfile
import unittest

from src.data.local.cache import MetadataCache


class CacheMigrationTest(unittest.TestCase):
    def _old_schema_db(self, db_path):
        conn = sqlite3.connect(db_path)
        conn.execute(
            """
            CREATE TABLE media_items (
                tmdb_id    INTEGER PRIMARY KEY,
                media_type TEXT    NOT NULL,
                title      TEXT    NOT NULL,
                year       INTEGER,
                cached_at  INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            )
            """
        )
        conn.execute(
            "INSERT INTO media_items (tmdb_id, media_type, title, year, "
            "cached_at, updated_at) VALUES (1, 'movie', 'Old', 2020, "
            "9999999999, 9999999999)"
        )
        conn.commit()
        conn.close()

    def test_migration_adds_release_date_without_purging_rows(self):
        # media_items is shared with the user repository, so a cache schema
        # migration must stay additive and never drop rows.
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "db.sqlite")
            self._old_schema_db(db)
            cache = MetadataCache(db)
            cols = {
                r[1]
                for r in cache._primary_conn.execute(
                    "PRAGMA table_info(media_items)"
                ).fetchall()
            }
            self.assertIn("release_date", cols)
            rows = cache._primary_conn.execute(
                "SELECT tmdb_id, release_date FROM media_items"
            ).fetchall()
            self.assertEqual([(1, None)], [(r["tmdb_id"], r["release_date"]) for r in rows])


if __name__ == "__main__":
    unittest.main()