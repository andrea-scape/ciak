"""Database schema definition and migration support.

A single SQLite database file holds all persistent state: user data
(watched, watchlist, ratings, collection) and a TMDB metadata cache.
WAL journal mode is enabled for concurrent read performance.
"""

import logging

DB_VERSION = 12
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

# Persistent watched-verdicts: caught-up / fully-watched answers cached
# across sessions, keyed by a cheap local fingerprint (watched-episode
# count) so any watch/unwatch self-invalidates. IF NOT EXISTS keeps old
# databases compatible without a migration step.
_WATCHED_VERDICTS = _table(
    """
    CREATE TABLE IF NOT EXISTS watched_verdicts (
        show_tmdb_id INTEGER NOT NULL,
        kind         TEXT    NOT NULL CHECK (kind IN ('caught_up', 'fully_watched')),
        verdict      INTEGER NOT NULL,
        fingerprint  TEXT    NOT NULL,
        computed_at  REAL    NOT NULL,
        PRIMARY KEY (show_tmdb_id, kind)
    )
    """
)

# Per-media notification overrides. Semantics depend on the
# notification-scope setting: in 'all' mode rows are MUTED media
# (watchlist minus this set), in 'selected' mode rows are the PICKED
# media (this set intersect watchlist).
_NOTIFY_SELECTION = _table(
    """
    CREATE TABLE IF NOT EXISTS notify_selection (
        tmdb_id    INTEGER NOT NULL,
        media_type TEXT    NOT NULL CHECK (media_type IN ('movie', 'show')),
        PRIMARY KEY (tmdb_id, media_type)
    )
    """
)

# Already-fired notifications so each airing/release notifies at most
# once. Key is "show:{id}:{season}:{episode}" or "movie:{id}".
_NOTIFIED_AIRINGS = _table(
    """
    CREATE TABLE IF NOT EXISTS notified_airings (
        key         TEXT    PRIMARY KEY,
        notified_at INTEGER NOT NULL
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
    if current < 4:
        # Diary view: optional per-watch-session note.
        conn.execute("ALTER TABLE watched_items ADD COLUMN notes TEXT")
        conn.execute(
            "INSERT OR REPLACE INTO schema_version (version, applied) VALUES (?, ?)",
            (4, 1),
        )
    if current < 5:
        # Season 0 (specials) is treated as nonexistent app-wide; drop any
        # imported watched rows for specials so history/diary never see them.
        conn.execute(
            "DELETE FROM watched_items WHERE media_type = 'episode'"
            " AND COALESCE(season_number, 0) <= 0"
        )
        conn.execute(
            "INSERT OR REPLACE INTO schema_version (version, applied) VALUES (?, ?)",
            (5, 1),
        )
    if current < 7:
        # Extend sync_state for multi-backend: add backend column,
        # rename trakt_id → remote_id, add unique index.
        cols = {row[1] for row in conn.execute("PRAGMA table_info(sync_state)")}
        if "backend" not in cols:
            conn.execute(
                "ALTER TABLE sync_state ADD COLUMN backend TEXT NOT NULL DEFAULT 'default'"
            )
        if "trakt_id" in cols and "remote_id" not in cols:
            conn.execute("ALTER TABLE sync_state RENAME COLUMN trakt_id TO remote_id")
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_sync_state_item_backend "
            "ON sync_state(item_key, backend)"
        )
        conn.execute(
            "INSERT OR REPLACE INTO schema_version (version, applied) VALUES (?, ?)",
            (7, 1),
        )
    if current < 8:
        # Add pushed_at column to sync_state for tracking what was last
        # pushed to each backend (enables deletion diff).
        cols = {row[1] for row in conn.execute("PRAGMA table_info(sync_state)")}
        if "pushed_at" not in cols:
            conn.execute(
                "ALTER TABLE sync_state ADD COLUMN pushed_at INTEGER"
            )
        conn.execute(
            "INSERT OR REPLACE INTO schema_version (version, applied) VALUES (?, ?)",
            (8, 1),
        )
    if current < 9:
        # Track when a metadata backfill fetch was last attempted for an
        # item, so items that can't be resolved (junk IDs, unreleased
        # movies) aren't re-fetched on every sync.
        cols = {row[1] for row in conn.execute("PRAGMA table_info(media_items)")}
        if "poster_attempted_at" not in cols:
            conn.execute(
                "ALTER TABLE media_items ADD COLUMN poster_attempted_at INTEGER"
            )
        conn.execute(
            "INSERT OR REPLACE INTO schema_version (version, applied) VALUES (?, ?)",
            (9, 1),
        )
    if current < 10:
        conn.execute(
            "INSERT OR REPLACE INTO schema_version (version, applied) VALUES (?, ?)",
            (10, 1),
        )
    if current < 11:
        # Dismissed mapping decisions: items the user explicitly said don't
        # exist on TMDB.  Permanently skipped until Simkl fixes the mapping.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS dismissed_mappings (
                sig        TEXT PRIMARY KEY,
                created_at INTEGER NOT NULL
            )
            """
        )
        conn.execute(
            "INSERT OR REPLACE INTO schema_version (version, applied) VALUES (?, ?)",
            (11, 1),
        )
    if current < 12:
        # Track anime provenance: only push items that originated from
        # Simkl's anime bucket back to that bucket, preventing the
        # push-to-anime mirror cycle that causes phantom re-imports.
        for tbl in ("watchlist_items", "watched_items", "ratings", "collection_items"):
            cols = {row[1] for row in conn.execute(f"PRAGMA table_info({tbl})")}
            if "is_anime" not in cols:
                conn.execute(f"ALTER TABLE {tbl} ADD COLUMN is_anime INTEGER NOT NULL DEFAULT 0")
        conn.execute(
            "INSERT OR REPLACE INTO schema_version (version, applied) VALUES (?, ?)",
            (12, 1),
        )
    if current < 13:
        # The "Keep on Simkl" concept is gone: no title may exist on Simkl
        # that isn't also in Ciak.  Drop the orphaned suppression table.
        conn.execute("DROP TABLE IF EXISTS deletion_suppressions")
        conn.execute(
            "INSERT OR REPLACE INTO schema_version (version, applied) VALUES (?, ?)",
            (13, 1),
        )
