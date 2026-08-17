"""TTL-based local cache for TMDB metadata.

Reduces API calls by storing metadata locally and invalidating it
after a configurable time-to-live.  Metadata for media items, seasons,
and episodes is cached aggressively because it rarely changes.  Search
results, trending lists, and calendar data are NOT cached; they change
frequently and are always fetched fresh.
"""

import json
import os
import sqlite3
import threading
import time

from ...domain.models import Movie, Show, Season, Episode

_CACHE_TABLES: list[str] = []


def _cache_table(sql: str) -> str:
    _CACHE_TABLES.append(sql)
    return sql


_CACHE_MEDIA = _cache_table("""
    CREATE TABLE IF NOT EXISTS media_items (
        tmdb_id        INTEGER PRIMARY KEY,
        media_type     TEXT    NOT NULL CHECK (media_type IN ('movie', 'show')),
        title          TEXT    NOT NULL,
        year           INTEGER,
        release_date   TEXT,
        overview       TEXT,
        runtime        INTEGER,
        rating         REAL,
        votes          INTEGER,
        poster_url     TEXT,
        backdrop_url   TEXT,
        imdb_id        TEXT,
        genres         TEXT,
        genre_ids      TEXT,
        collection_id  INTEGER,
        tagline        TEXT,
        certification  TEXT,
        status         TEXT,
        next_episode_air_date  TEXT,
        next_episode_season    INTEGER,
        next_episode_number    INTEGER,
        next_episode_name      TEXT,
        next_episode_still     TEXT,
        budget                 INTEGER,
        revenue                INTEGER,
        creators               TEXT,
        cached_at      INTEGER NOT NULL,
        updated_at     INTEGER NOT NULL
    )
""")

_CACHE_SEASONS = _cache_table("""
    CREATE TABLE IF NOT EXISTS seasons (
        show_tmdb_id   INTEGER NOT NULL,
        season_number  INTEGER NOT NULL,
        tmdb_id        INTEGER NOT NULL,
        name           TEXT,
        overview       TEXT,
        poster_url     TEXT,
        episode_count  INTEGER DEFAULT 0,
        cached_at      INTEGER NOT NULL,
        PRIMARY KEY (show_tmdb_id, season_number)
    )
""")

_CACHE_EPISODES = _cache_table("""
    CREATE TABLE IF NOT EXISTS episodes (
        show_tmdb_id   INTEGER NOT NULL,
        season_number  INTEGER NOT NULL,
        episode_number INTEGER NOT NULL,
        tmdb_id        INTEGER NOT NULL,
        title          TEXT    NOT NULL,
        overview       TEXT,
        runtime        INTEGER,
        rating         REAL,
        air_date       TEXT,
        poster_url     TEXT,
        cached_at      INTEGER NOT NULL,
        PRIMARY KEY (show_tmdb_id, season_number, episode_number)
    )
""")


class MetadataCache:
    """Local SQLite cache for TMDB metadata with configurable TTL.

    Thread-safety: each thread lazily opens its own connection to the same
    WAL database, so concurrent fetches from worker threads never share a
    connection object.  Schema DDL + migrations run once in the constructor.
    """

    def __init__(self, db_path: str, ttl_seconds: int = 3600):
        self._db_path = db_path
        self._ttl = ttl_seconds
        self._local = threading.local()
        self._conns: list[sqlite3.Connection] = []
        self._conns_lock = threading.Lock()
        self._primary_conn = None
        self._ensure_conn()
        self._primary_conn = self._ensure_conn()
        self._primary_conn.execute("PRAGMA journal_mode=WAL")
        for sql in _CACHE_TABLES:
            self._primary_conn.execute(sql)
        self._migrate()
        self._primary_conn.commit()

    def _ensure_conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self._db_path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            # journal_mode=WAL is set once on the primary connection and is
            # persistent; per-connection pragmas only here.
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("PRAGMA synchronous=NORMAL")
            self._local.conn = conn
            with self._conns_lock:
                self._conns.append(conn)
        return conn

    # ------------------------------------------------------------------
    # Migration
    # ------------------------------------------------------------------

    def _migrate(self) -> None:
        """Add columns introduced after the table was first created."""
        conn = self._primary_conn
        cols = {r["name"] for r in conn.execute(
            "PRAGMA table_info(media_items)"
        ).fetchall()}
        if "release_date" not in cols:
            conn.execute(
                "ALTER TABLE media_items ADD COLUMN release_date TEXT"
            )
            conn.execute(
                "UPDATE media_items SET cached_at = 0"
            )
        elif conn.execute(
            "SELECT 1 FROM media_items WHERE release_date IS NULL LIMIT 1"
        ).fetchone():
            conn.execute(
                "UPDATE media_items SET cached_at = 0"
            )
        if "genre_ids" not in cols:
            conn.execute(
                "ALTER TABLE media_items ADD COLUMN genre_ids TEXT"
            )
        if "collection_id" not in cols:
            conn.execute(
                "ALTER TABLE media_items ADD COLUMN collection_id INTEGER"
            )
        if "next_episode_air_date" not in cols:
            conn.execute(
                "ALTER TABLE media_items ADD COLUMN next_episode_air_date TEXT"
            )
        if "next_episode_season" not in cols:
            conn.execute(
                "ALTER TABLE media_items ADD COLUMN next_episode_season INTEGER"
            )
        if "next_episode_number" not in cols:
            conn.execute(
                "ALTER TABLE media_items ADD COLUMN next_episode_number INTEGER"
            )
        if "next_episode_name" not in cols:
            conn.execute(
                "ALTER TABLE media_items ADD COLUMN next_episode_name TEXT"
            )
        if "next_episode_still" not in cols:
            conn.execute(
                "ALTER TABLE media_items ADD COLUMN next_episode_still TEXT"
            )
        if "budget" not in cols:
            conn.execute(
                "ALTER TABLE media_items ADD COLUMN budget INTEGER"
            )
        if "revenue" not in cols:
            conn.execute(
                "ALTER TABLE media_items ADD COLUMN revenue INTEGER"
            )
        if "creators" not in cols:
            conn.execute(
                "ALTER TABLE media_items ADD COLUMN creators TEXT"
            )

    # ------------------------------------------------------------------
    # Media (movies & shows)
    # ------------------------------------------------------------------

    def get_media(self, tmdb_id: int) -> Movie | Show | None:
        """Return cached media if within TTL, else None."""
        row = self._ensure_conn().execute(
            "SELECT * FROM media_items WHERE tmdb_id = ?", (tmdb_id,)
        ).fetchone()
        if row is None:
            return None
        if self._is_expired(row["cached_at"]):
            return None
        return self._row_to_media(row)

    def put_media(self, media: Movie | Show) -> None:
        """Store or update a media item in the cache."""
        now = int(time.time())
        fields = {
            "tmdb_id": media.tmdb_id,
            "media_type": media.media_type,
            "title": media.title,
            "year": media.year,
            "release_date": getattr(media, "release_date", None),
            "overview": media.overview,
            "runtime": getattr(media, "runtime", None),
            "rating": media.rating,
            "votes": media.votes,
            "poster_url": media.poster_url,
            "backdrop_url": media.backdrop_url,
            "imdb_id": media.imdb_id,
            "genres": json.dumps(media.genres or []),
            "genre_ids": json.dumps(media.genre_ids or []),
            "collection_id": getattr(media, "collection_id", None),
            "tagline": getattr(media, "tagline", None),
            "certification": getattr(media, "certification", None),
            "status": getattr(media, "status", None),
            "next_episode_air_date": getattr(media, "next_episode_air_date", None),
            "next_episode_season": getattr(media, "next_episode_season", None),
            "next_episode_number": getattr(media, "next_episode_number", None),
            "next_episode_name": getattr(media, "next_episode_name", None),
            "next_episode_still": getattr(media, "next_episode_still", None),
            "budget": getattr(media, "budget", None),
            "revenue": getattr(media, "revenue", None),
            "creators": json.dumps(getattr(media, "creators", []) or []),
            "cached_at": now,
            "updated_at": now,
        }
        self._ensure_conn().execute(
            """
            INSERT OR REPLACE INTO media_items
            (tmdb_id, media_type, title, year, release_date, overview,
             runtime, rating, votes, poster_url, backdrop_url, imdb_id,
             genres, genre_ids, collection_id, tagline, certification,
             status, next_episode_air_date, next_episode_season,
             next_episode_number, next_episode_name, next_episode_still,
             budget, revenue, creators, cached_at, updated_at)
            VALUES (:tmdb_id, :media_type, :title, :year, :release_date,
                    :overview, :runtime, :rating, :votes, :poster_url,
                    :backdrop_url, :imdb_id, :genres, :genre_ids,
                    :collection_id, :tagline, :certification, :status,
                    :next_episode_air_date, :next_episode_season,
                    :next_episode_number, :next_episode_name,
                    :next_episode_still, :budget, :revenue, :creators,
                    :cached_at, :updated_at)
            """,
            fields,
        )
        self._ensure_conn().commit()

    # ------------------------------------------------------------------
    # Seasons
    # ------------------------------------------------------------------

    def get_seasons(self, show_tmdb_id: int) -> list[Season] | None:
        """Return cached seasons if within TTL, else None."""
        rows = self._ensure_conn().execute(
            "SELECT * FROM seasons WHERE show_tmdb_id = ? ORDER BY season_number",
            (show_tmdb_id,),
        ).fetchall()
        if not rows:
            return None
        if any(self._is_expired(r["cached_at"]) for r in rows):
            return None
        return [self._row_to_season(r) for r in rows]

    def put_seasons(self, show_tmdb_id: int, seasons: list[Season]) -> None:
        """Store or update season data for a show.  Replaces all rows."""
        now = int(time.time())
        self._ensure_conn().execute(
            "DELETE FROM seasons WHERE show_tmdb_id = ?", (show_tmdb_id,)
        )
        for s in seasons:
            self._ensure_conn().execute(
                """
                INSERT INTO seasons
                (show_tmdb_id, season_number, tmdb_id, name, overview,
                 poster_url, episode_count, cached_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    show_tmdb_id,
                    s.season_number,
                    s.tmdb_id,
                    s.name,
                    s.overview,
                    s.poster_url,
                    s.episode_count,
                    now,
                ),
            )
        self._ensure_conn().commit()

    # ------------------------------------------------------------------
    # Episodes
    # ------------------------------------------------------------------

    def put_episodes(
        self, show_tmdb_id: int, season_number: int, episodes: list[Episode]
    ) -> None:
        """Store or update episodes for a single season.  Replaces all rows."""
        now = int(time.time())
        self._ensure_conn().execute(
            "DELETE FROM episodes WHERE show_tmdb_id = ? AND season_number = ?",
            (show_tmdb_id, season_number),
        )
        for ep in episodes:
            self._ensure_conn().execute(
                """
                INSERT INTO episodes
                (show_tmdb_id, season_number, episode_number, tmdb_id, title,
                 overview, runtime, rating, air_date, poster_url, cached_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    show_tmdb_id,
                    season_number,
                    ep.episode_number,
                    ep.tmdb_id,
                    ep.title,
                    ep.overview,
                    ep.runtime,
                    ep.rating,
                    ep.air_date,
                    ep.poster_url,
                    now,
                ),
            )
        self._ensure_conn().commit()

    def get_episodes(
        self, show_tmdb_id: int, season_number: int
    ) -> list[Episode] | None:
        """Return cached episodes if within TTL, else None."""
        rows = self._ensure_conn().execute(
            """SELECT * FROM episodes
               WHERE show_tmdb_id = ? AND season_number = ?
               ORDER BY episode_number""",
            (show_tmdb_id, season_number),
        ).fetchall()
        if not rows:
            return None
        if any(self._is_expired(r["cached_at"]) for r in rows):
            return None
        return [self._row_to_episode(r) for r in rows]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _is_expired(self, cached_at: int) -> bool:
        return (int(time.time()) - cached_at) > self._ttl

    @staticmethod
    def _row_to_media(row) -> Movie | Show:
        common = {
            "tmdb_id": row["tmdb_id"],
            "title": row["title"],
            "year": row["year"],
            "overview": row["overview"],
            "runtime": row["runtime"],
            "rating": row["rating"],
            "votes": row["votes"],
            "poster_url": row["poster_url"],
            "backdrop_url": row["backdrop_url"],
            "imdb_id": row["imdb_id"],
            "genres": json.loads(row["genres"] or "[]"),
            "genre_ids": json.loads(row["genre_ids"] or "[]"),
            "tagline": row["tagline"],
            "certification": row["certification"],
        }
        if row["media_type"] == "show":
            return Show(
                status=row["status"],
                next_episode_air_date=row["next_episode_air_date"],
                next_episode_season=row["next_episode_season"],
                next_episode_number=row["next_episode_number"],
                next_episode_name=row["next_episode_name"],
                next_episode_still=row["next_episode_still"],
                creators=json.loads(row["creators"] or "[]"),
                **common,
            )
        return Movie(
            collection_id=row["collection_id"],
            release_date=row["release_date"],
            budget=row["budget"],
            revenue=row["revenue"],
            **common,
        )

    @staticmethod
    def _row_to_season(row) -> Season:
        return Season(
            tmdb_id=row["tmdb_id"],
            show_tmdb_id=row["show_tmdb_id"],
            season_number=row["season_number"],
            name=row["name"],
            overview=row["overview"],
            poster_url=row["poster_url"],
            episode_count=row["episode_count"] or 0,
        )

    @staticmethod
    def _row_to_episode(row) -> Episode:
        return Episode(
            tmdb_id=row["tmdb_id"],
            show_tmdb_id=row["show_tmdb_id"],
            season_number=row["season_number"],
            episode_number=row["episode_number"],
            title=row["title"],
            overview=row["overview"],
            runtime=row["runtime"],
            rating=row["rating"],
            air_date=row["air_date"],
            poster_url=row["poster_url"],
        )

    def destroy(self) -> None:
        """Close the connection and delete the database file."""
        db_path = self._primary_conn.execute("PRAGMA database_list").fetchone()["file"]
        with self._conns_lock:
            conns = self._conns
            self._conns = []
        for conn in conns:
            try:
                conn.close()
            except sqlite3.Error:
                pass
        try:
            self._local.conn = None
        except AttributeError:
            pass
        try:
            os.remove(db_path)
        except OSError:
            pass
