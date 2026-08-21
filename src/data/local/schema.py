"""Database schema definition and migration support.

A single SQLite database file holds all persistent state: user data
(watched, watchlist, ratings, collection) and a TMDB metadata cache.
WAL journal mode is enabled for concurrent read performance.
"""

import logging

DB_VERSION = 3
PRAGMAS = [
    "PRAGMA journal_mode=WAL",
    "PRAGMA foreign_keys=ON",
    "PRAGMA synchronous=NORMAL",
]

_TABLES: list[str] = []


def _table(sql: str) -> str:
    _TABLES.append(sql)
    return sql


_MEDIA_ITEMS = _table(
    """
    CREATE TABLE IF NOT EXISTS media_items (
        tmdb_id        INTEGER PRIMARY KEY,
        media_type     TEXT    NOT NULL CHECK (media_type IN ('movie', 'show')),
        title          TEXT    NOT NULL,
        year           INTEGER,
        overview       TEXT,
        runtime        INTEGER,
        rating         REAL,
        votes          INTEGER,
        poster_url     TEXT,
        backdrop_url   TEXT,
        imdb_id        TEXT,
        genres         TEXT,   -- JSON array
        genre_ids      TEXT,   -- JSON array
        collection_id  INTEGER,
        collection_name TEXT,
        tagline        TEXT,
        certification  TEXT,
        status         TEXT,   -- "returning", "ended", etc. (shows only)
        cached_at      INTEGER NOT NULL,
        updated_at     INTEGER NOT NULL
    )
    """
)

_SEASONS = _table(
    """
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
    """
)

_EPISODES = _table(
    """
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
    """
)

_WATCHED = _table(
    """
    CREATE TABLE IF NOT EXISTS watched_items (
        tmdb_id        INTEGER NOT NULL,
        media_type     TEXT    NOT NULL CHECK (media_type IN ('movie', 'show', 'episode')),
        show_tmdb_id   INTEGER,
        season_number  INTEGER,
        episode_number INTEGER,
        watched_at     INTEGER NOT NULL,
        PRIMARY KEY (tmdb_id, media_type, show_tmdb_id, season_number, episode_number)
    )
    """
)

_WATCHLIST = _table(
    """
    CREATE TABLE IF NOT EXISTS watchlist_items (
        tmdb_id        INTEGER NOT NULL,
        media_type     TEXT    NOT NULL CHECK (media_type IN ('movie', 'show')),
        added_at       INTEGER NOT NULL,
        PRIMARY KEY (tmdb_id, media_type)
    )
    """
)

_RATINGS = _table(
    """
    CREATE TABLE IF NOT EXISTS ratings (
        tmdb_id        INTEGER NOT NULL,
        media_type     TEXT    NOT NULL CHECK (media_type IN ('movie', 'show')),
        rating         INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
        rated_at       INTEGER NOT NULL,
        PRIMARY KEY (tmdb_id, media_type)
    )
    """
)

_COLLECTION = _table(
    """
    CREATE TABLE IF NOT EXISTS collection_items (
        tmdb_id        INTEGER NOT NULL,
        media_type     TEXT    NOT NULL CHECK (media_type IN ('movie', 'show')),
        collected_at   INTEGER NOT NULL,
        PRIMARY KEY (tmdb_id, media_type)
    )
    """
)

_SYNC = _table(
    """
    CREATE TABLE IF NOT EXISTS sync_state (
        item_key        TEXT PRIMARY KEY,
        trakt_id        INTEGER,
        last_modified   INTEGER,
        synced          INTEGER DEFAULT 0,
        dirty           INTEGER DEFAULT 0
    )
    """
)

_VERSION = _table(
    """
    CREATE TABLE IF NOT EXISTS schema_version (
        version  INTEGER PRIMARY KEY,
        applied  INTEGER NOT NULL
    )
    """
)


def initialize(conn) -> None:
    """Create all tables and apply pending migrations."""
    for pragma in PRAGMAS:
        conn.execute(pragma)
    for sql in _TABLES:
        conn.execute(sql)
    _migrate(conn)
    conn.commit()


def _migrate(conn) -> None:
    current = conn.execute(
        "SELECT COALESCE(MAX(version), 0) FROM schema_version"
    ).fetchone()[0]
    if current < 1:
        conn.execute(
            "INSERT OR REPLACE INTO schema_version (version, applied) VALUES (?, ?)",
            (1, 1),
        )
        current = 1
    if current < 2:
        conn.execute(
            "UPDATE ratings SET rating = MAX(1, CAST(ROUND(rating / 2.0) AS INTEGER))"
        )
        conn.execute(
            "INSERT OR REPLACE INTO schema_version (version, applied) VALUES (?, ?)",
            (2, 1),
        )
    if current < 3:
        # watched_items has a composite PK that includes nullable episode
        # columns; for movies/shows these are all NULL, and SQLite treats
        # NULL != NULL, so INSERT OR REPLACE could not dedupe.  Collapse
        # existing duplicates (keep the newest watched_at) and enforce
        # uniqueness over the fully populated key going forward.
        conn.execute(
            "DELETE FROM watched_items WHERE rowid NOT IN ("
            "SELECT rowid FROM ("
            "  SELECT rowid, ROW_NUMBER() OVER ("
            "    PARTITION BY tmdb_id, media_type,"
            "      COALESCE(show_tmdb_id, -1),"
            "      COALESCE(season_number, -1),"
            "      COALESCE(episode_number, -1)"
            "    ORDER BY watched_at DESC, rowid DESC"
            "  ) AS rn FROM watched_items"
            ") WHERE rn = 1"
            ")"
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_watched_items_unique ON watched_items ("
            "tmdb_id, media_type,"
            "COALESCE(show_tmdb_id, -1),"
            "COALESCE(season_number, -1),"
            "COALESCE(episode_number, -1)"
            ")"
        )
        conn.execute(
            "INSERT OR REPLACE INTO schema_version (version, applied) VALUES (?, ?)",
            (3, 1),
        )
