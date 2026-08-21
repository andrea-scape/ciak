import os
import sqlite3
import tempfile
import unittest

from src.data.local.repository import LocalMediaRepository


class MarkWatchetIdempotencyTest(unittest.TestCase):
    def _repo(self, directory):
        db = os.path.join(directory, "db.sqlite")
        repo = LocalMediaRepository(db)
        repo.initialize()
        return repo, db

    def test_mark_watched_twice_movie_yields_single_row(self):
        # The watched_items PK contains nullable columns, so SQLite's
        # NULL != NULL semantics make INSERT OR REPLACE unable to dedupe
        # movie/watchlist-level keys.  mark_watched must be idempotent.
        with tempfile.TemporaryDirectory() as d:
            repo, db = self._repo(d)
            repo.mark_watched(42, "movie")
            repo.mark_watched(42, "movie")
            conn = sqlite3.connect(db)
            try:
                rows = conn.execute(
                    "SELECT COUNT(*) FROM watched_items WHERE tmdb_id=42"
                ).fetchone()[0]
            finally:
                conn.close()
            self.assertEqual(rows, 1)

    def test_mark_watched_twice_show_yields_single_row(self):
        with tempfile.TemporaryDirectory() as d:
            repo, db = self._repo(d)
            repo.mark_watched(7, "show")
            repo.mark_watched(7, "show")
            conn = sqlite3.connect(db)
            try:
                rows = conn.execute(
                    "SELECT COUNT(*) FROM watched_items WHERE tmdb_id=7 AND media_type='show'"
                ).fetchone()[0]
            finally:
                conn.close()
            self.assertEqual(rows, 1)

    def test_mark_watched_twice_episode_yields_single_row(self):
        with tempfile.TemporaryDirectory() as d:
            repo, db = self._repo(d)
            repo.mark_watched(900, "episode", show_tmdb_id=7, season_number=1, episode_number=1)
            repo.mark_watched(900, "episode", show_tmdb_id=7, season_number=1, episode_number=1)
            conn = sqlite3.connect(db)
            try:
                rows = conn.execute(
                    "SELECT COUNT(*) FROM watched_items WHERE tmdb_id=900"
                ).fetchone()[0]
            finally:
                conn.close()
            self.assertEqual(rows, 1)

    def test_re_mark_refreshes_timestamp(self):
        with tempfile.TemporaryDirectory() as d:
            repo, db = self._repo(d)
            repo.mark_watched(42, "movie")
            conn = sqlite3.connect(db)
            try:
                first = conn.execute(
                    "SELECT watched_at FROM watched_items WHERE tmdb_id=42"
                ).fetchone()[0]
            finally:
                conn.close()
            repo.mark_watched(42, "movie")
            conn = sqlite3.connect(db)
            try:
                second = conn.execute(
                    "SELECT watched_at FROM watched_items WHERE tmdb_id=42"
                ).fetchone()[0]
            finally:
                conn.close()
            self.assertTrue(second >= first)

    def test_get_watched_list_dedupes_movies(self):
        # Even a legacy database with duplicate rows must render one card.
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "db.sqlite")
            self._legacy_db_with_dupes(db, movie_dupes=True)
            repo = LocalMediaRepository(db)
            movies = repo.get_watched_list("movie")
            self.assertEqual(len(movies), 1)
            self.assertEqual(movies[0]["tmdb_id"], 42)
            self.assertEqual(movies[0]["watched_at"], 2000)

    def _legacy_db_with_dupes(self, db_path, movie_dupes=False):
        """Pre-v3 schema where NULL episode columns let INSERT OR REPLACE
        clone movie/show rows.  No unique index exists yet."""
        conn = sqlite3.connect(db_path)
        conn.executescript(
            """
            CREATE TABLE watched_items (
                tmdb_id        INTEGER NOT NULL,
                media_type     TEXT    NOT NULL CHECK (media_type IN ('movie', 'show', 'episode')),
                show_tmdb_id   INTEGER,
                season_number  INTEGER,
                episode_number INTEGER,
                watched_at     INTEGER NOT NULL,
                PRIMARY KEY (tmdb_id, media_type, show_tmdb_id, season_number, episode_number)
            );
            CREATE TABLE media_items (
                tmdb_id        INTEGER PRIMARY KEY,
                media_type     TEXT    NOT NULL,
                title          TEXT    NOT NULL,
                year           INTEGER,
                overview       TEXT,
                runtime        INTEGER,
                rating         REAL,
                votes          INTEGER,
                poster_url     TEXT,
                backdrop_url   TEXT,
                imdb_id        TEXT,
                genres         TEXT,
                collection_id  INTEGER,
                collection_name TEXT,
                tagline        TEXT,
                certification  TEXT,
                status         TEXT,
                cached_at      INTEGER NOT NULL,
                updated_at     INTEGER NOT NULL
            );
            """
        )
        if movie_dupes:
            conn.execute(
                "INSERT INTO media_items (tmdb_id, media_type, title, cached_at, updated_at) "
                "VALUES (42, 'movie', 'Test', 1, 1)"
            )
            conn.execute(
                "INSERT INTO watched_items (tmdb_id, media_type, watched_at) VALUES "
                "(42, 'movie', 1000), (42, 'movie', 2000)"
            )
        conn.commit()
        conn.close()


class WatchedEpisodeJoinTest(unittest.TestCase):
    """get_watched_list must resolve title/poster for episode rows via the
    parent show's cached media item."""

    def _repo(self, directory):
        db = os.path.join(directory, "db.sqlite")
        repo = LocalMediaRepository(db)
        repo.initialize()
        return repo, db

    def _seed_show_and_episode(self, repo, db):
        repo.mark_watched(7, "show")
        conn = sqlite3.connect(db)
        try:
            conn.execute(
                "INSERT INTO media_items "
                "(tmdb_id, media_type, title, year, poster_url, cached_at, updated_at) "
                "VALUES (7, 'show', 'Lost', 2004, 'http://poster/lost.jpg', 1, 1)"
            )
            conn.commit()
        finally:
            conn.close()
        repo.mark_watched(900, "episode", show_tmdb_id=7, season_number=1, episode_number=1)

    def test_get_watched_list_show_resolves_episode_title_and_poster(self):
        with tempfile.TemporaryDirectory() as d:
            repo, db = self._repo(d)
            self._seed_show_and_episode(repo, db)
            rows = repo.get_watched_list("show")
            episode = next(r for r in rows if r["media_type"] == "episode")
            self.assertEqual(episode["show_tmdb_id"], 7)
            self.assertEqual(episode["season_number"], 1)
            self.assertEqual(episode["episode_number"], 1)
            self.assertEqual(episode["title"], "Lost")
            self.assertEqual(episode["year"], 2004)
            self.assertEqual(episode["poster_url"], "http://poster/lost.jpg")

    def test_get_watched_list_movie_join_unchanged(self):
        with tempfile.TemporaryDirectory() as d:
            repo, db = self._repo(d)
            conn = sqlite3.connect(db)
            try:
                conn.execute(
                    "INSERT INTO media_items "
                    "(tmdb_id, media_type, title, year, poster_url, cached_at, updated_at) "
                    "VALUES (42, 'movie', 'Jaws', 1975, 'http://poster/jaws.jpg', 1, 1)"
                )
                conn.commit()
            finally:
                conn.close()
            repo.mark_watched(42, "movie")
            rows = repo.get_watched_list("movie")
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["title"], "Jaws")
            self.assertEqual(rows[0]["poster_url"], "http://poster/jaws.jpg")
            self.assertIsNone(rows[0]["show_tmdb_id"])


class WatchedMigrationTest(unittest.TestCase):
    def _seed_v2_db(self, db_path):
        """Build a database that looks like schema version 2 with dupes.

        Movie/show rows store NULL in the episode columns, and NULL != NULL
        lets INSERT OR REPLACE clone them.  Episode rows keep a populated
        key so they dedupe normally.
        """
        conn = sqlite3.connect(db_path)
        conn.executescript(
            """
            CREATE TABLE watched_items (
                tmdb_id        INTEGER NOT NULL,
                media_type     TEXT    NOT NULL CHECK (media_type IN ('movie', 'show', 'episode')),
                show_tmdb_id   INTEGER,
                season_number  INTEGER,
                episode_number INTEGER,
                watched_at     INTEGER NOT NULL,
                PRIMARY KEY (tmdb_id, media_type, show_tmdb_id, season_number, episode_number)
            );
            CREATE TABLE schema_version (
                version  INTEGER PRIMARY KEY,
                applied  INTEGER NOT NULL
            );
            CREATE TABLE media_items (
                tmdb_id        INTEGER PRIMARY KEY,
                media_type     TEXT    NOT NULL,
                title          TEXT    NOT NULL,
                year           INTEGER,
                overview       TEXT,
                runtime        INTEGER,
                rating         REAL,
                votes          INTEGER,
                poster_url     TEXT,
                backdrop_url   TEXT,
                imdb_id        TEXT,
                genres         TEXT,
                collection_id  INTEGER,
                collection_name TEXT,
                tagline        TEXT,
                certification  TEXT,
                status         TEXT,
                cached_at      INTEGER NOT NULL,
                updated_at     INTEGER NOT NULL
            );
            INSERT INTO schema_version (version, applied) VALUES (2, 1);
            INSERT INTO watched_items (tmdb_id, media_type, watched_at) VALUES
                (42, 'movie', 1000), (42, 'movie', 2000);
            INSERT INTO watched_items (tmdb_id, media_type, show_tmdb_id, season_number, episode_number, watched_at)
                VALUES (900, 'episode', 7, 1, 1, 600);
            """
        )
        conn.commit()
        conn.close()

    def test_migration_v3_purges_duplicate_watched_rows(self):
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "db.sqlite")
            self._seed_v2_db(db)
            repo = LocalMediaRepository(db)
            repo.initialize()
            conn = sqlite3.connect(db)
            try:
                rows = conn.execute(
                    "SELECT tmdb_id, watched_at FROM watched_items WHERE tmdb_id=42"
                ).fetchall()
                self.assertEqual(rows, [(42, 2000)])
                # The episode row keyed by (7, 1, 1) must survive untouched.
                self.assertEqual(
                    [(900, 600)],
                    conn.execute(
                        "SELECT tmdb_id, watched_at FROM watched_items WHERE tmdb_id=900"
                    ).fetchall(),
                )
            finally:
                conn.close()

    def test_migration_v3_is_idempotent(self):
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "db.sqlite")
            self._seed_v2_db(db)
            repo = LocalMediaRepository(db)
            repo.initialize()
            repo.initialize()  # running again must not crash
            conn = sqlite3.connect(db)
            try:
                rows = conn.execute("SELECT COUNT(*) FROM watched_items WHERE tmdb_id=42").fetchone()[0]
            finally:
                conn.close()
            self.assertEqual(rows, 1)

    def test_migration_v3_creates_unique_index_matching_deletes(self):
        # mark_unwatched deletes by the COALESCE(-1) key; the unique index
        # must use the exact same key so deletes hit all clones.
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "db.sqlite")
            self._seed_v2_db(db)
            repo = LocalMediaRepository(db)
            repo.initialize()
            conn = sqlite3.connect(db)
            try:
                idx = conn.execute(
                    "SELECT sql FROM sqlite_master WHERE type='index' AND name='idx_watched_items_unique'"
                ).fetchone()
                self.assertIsNotNone(idx)
                self.assertIn("COALESCE(show_tmdb_id, -1)", idx[0])
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()