"""Local SQLite implementation of UserMediaRepository.

All user state (watched, watchlist, ratings, collection) is stored in
a single SQLite database file.  Methods are synchronous and safe for
use from background threads (check_same_thread=False, WAL mode).
"""

import logging
import sqlite3
import threading
import time

from ...domain.exceptions import RepositoryError
from ...domain.models import Stats

_log = logging.getLogger(__name__)


class LocalMediaRepository:
    """Persistent local store for user media state.

    Instantiated with a database path (Flatpak-safe via GLib.user_data_dir).
    All mutations are committed immediately.

    Thread-safety: each thread lazily opens its own connection (WAL allows
    concurrent readers + a single writer serialized by busy_timeout), so
    worker threads and the main thread never share a connection object.
    """

    def __init__(self, db_path: str):
        self._db_path = db_path
        self._local = threading.local()
        self._conns: list[sqlite3.Connection] = []
        self._conns_lock = threading.Lock()
        # Incremented by every mutation that affects derived views
        # (watchlist, watched state); lets pages invalidate caches cheaply.
        self._data_version = 0

    @property
    def data_version(self) -> int:
        return self._data_version

    def _bump_data_version(self) -> None:
        self._data_version += 1

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    def _ensure_conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self._db_path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            # journal_mode=WAL is persistent and set during initialize();
            # worker connections only re-apply per-connection pragmas.
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA foreign_keys=ON")
            self._local.conn = conn
            with self._conns_lock:
                self._conns.append(conn)
        return conn

    def initialize(self) -> None:
        """Create tables and apply migrations.  Call once at startup."""
        from .schema import initialize as init_schema

        init_schema(self._ensure_conn())

    # ------------------------------------------------------------------
    # Watched
    # ------------------------------------------------------------------

    def mark_watched(
        self,
        tmdb_id: int,
        media_type: str,
        show_tmdb_id: int | None = None,
        season_number: int | None = None,
        episode_number: int | None = None,
    ) -> None:
        # Season 0 (specials) does not exist app-wide: never store it.
        if show_tmdb_id is not None and (season_number or 0) <= 0:
            return
        conn = self._ensure_conn()
        now = int(time.time())
        # Delete any existing row for the same logical key first.  The PK
        # includes nullable episode columns, and SQLite's NULL != NULL
        # semantics let INSERT OR REPLACE clone movie/show rows, so a plain
        # replace cannot be relied on to dedupe.
        conn.execute(
            "DELETE FROM watched_items "
            "WHERE tmdb_id = ? AND media_type = ? "
            "AND COALESCE(show_tmdb_id, -1) = COALESCE(?, -1) "
            "AND COALESCE(season_number, -1) = COALESCE(?, -1) "
            "AND COALESCE(episode_number, -1) = COALESCE(?, -1)",
            (tmdb_id, media_type, show_tmdb_id, season_number, episode_number),
        )
        conn.execute(
            "INSERT INTO watched_items "
            "(tmdb_id, media_type, show_tmdb_id, season_number, episode_number, watched_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                tmdb_id,
                media_type if show_tmdb_id is None else "episode",
                show_tmdb_id,
                season_number,
                episode_number,
                now,
            ),
        )
        conn.commit()
        self._bump_data_version()

    def mark_unwatched(
        self,
        tmdb_id: int,
        media_type: str,
        show_tmdb_id: int | None = None,
        season_number: int | None = None,
        episode_number: int | None = None,
    ) -> None:
        conn = self._ensure_conn()
        conn.execute(
            "DELETE FROM watched_items "
            "WHERE tmdb_id = ? AND COALESCE(show_tmdb_id, -1) = COALESCE(?, -1) "
            "AND COALESCE(season_number, -1) = COALESCE(?, -1) "
            "AND COALESCE(episode_number, -1) = COALESCE(?, -1)",
            (tmdb_id, show_tmdb_id, season_number, episode_number),
        )
        conn.commit()
        self._bump_data_version()

    def is_watched(
        self,
        tmdb_id: int,
        media_type: str,
        show_tmdb_id: int | None = None,
        season_number: int | None = None,
        episode_number: int | None = None,
    ) -> bool:
        conn = self._ensure_conn()
        row = conn.execute(
            "SELECT 1 FROM watched_items "
            "WHERE tmdb_id = ? AND COALESCE(show_tmdb_id, -1) = COALESCE(?, -1) "
            "AND COALESCE(season_number, -1) = COALESCE(?, -1) "
            "AND COALESCE(episode_number, -1) = COALESCE(?, -1)",
            (tmdb_id, show_tmdb_id, season_number, episode_number),
        ).fetchone()
        return row is not None

    def get_watched_at(
        self,
        tmdb_id: int,
        media_type: str,
        show_tmdb_id: int | None = None,
        season_number: int | None = None,
        episode_number: int | None = None,
    ) -> int | None:
        """Watched timestamp for one item, or None when unwatched."""
        conn = self._ensure_conn()
        row = conn.execute(
            "SELECT watched_at FROM watched_items "
            "WHERE tmdb_id = ? AND media_type = ? "
            "AND COALESCE(show_tmdb_id, -1) = COALESCE(?, -1) "
            "AND COALESCE(season_number, -1) = COALESCE(?, -1) "
            "AND COALESCE(episode_number, -1) = COALESCE(?, -1) "
            "LIMIT 1",
            (tmdb_id, media_type, show_tmdb_id, season_number, episode_number),
        ).fetchone()
        return row[0] if row else None

    def get_watched_list(self, media_type: str | None = None) -> list[dict]:
        """Return all watched items as dicts with tmdb_id, title, etc.

        Grouped by the fully-populated logical key so legacy rows produced
        before the dedupe migration (or by any path relying on NULL PK
        columns) never render as duplicate cards.
        """
        conn = self._ensure_conn()
        query = (
            "SELECT w.tmdb_id, w.media_type, w.show_tmdb_id, w.season_number, "
            "w.episode_number, MAX(w.watched_at) AS watched_at, w.is_anime, "
            "m.title, m.year, m.poster_url, m.imdb_id, m.runtime, "
            "m.collection_id, m.collection_name, m.genres "
            "FROM watched_items w "
            "LEFT JOIN media_items m ON m.tmdb_id = COALESCE(w.show_tmdb_id, w.tmdb_id) "
        )
        params: tuple = ()
        if media_type == "movie":
            query += "WHERE w.media_type = ?"
            params = ("movie",)
        elif media_type == "show":
            query += "WHERE w.media_type IN ('show', 'episode')"
        query += (
            " GROUP BY w.tmdb_id, w.media_type, "
            "COALESCE(w.show_tmdb_id, -1), "
            "COALESCE(w.season_number, -1), "
            "COALESCE(w.episode_number, -1)"
        )
        query += " ORDER BY watched_at DESC"
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    def get_watched_ids(self, media_type: str | None = None) -> set[int]:
        """Return a set of tmdb_ids that have been watched."""
        conn = self._ensure_conn()
        if media_type == "movie":
            rows = conn.execute(
                "SELECT DISTINCT tmdb_id FROM watched_items WHERE media_type='movie'"
            ).fetchall()
        elif media_type == "show":
            rows = conn.execute(
                "SELECT DISTINCT tmdb_id FROM watched_items WHERE media_type IN ('show','episode')"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT DISTINCT tmdb_id FROM watched_items"
            ).fetchall()
        return {r[0] for r in rows}

    def is_whole_show_watched(self, show_tmdb_id: int) -> bool:
        """True when a whole-show mark exists for this id.

        Covers both modern rows (show_tmdb_id set) and legacy/imported
        rows where media_type='show' carries the id in tmdb_id. A
        wholesale mark means the user declared the show seen."""
        conn = self._ensure_conn()
        row = conn.execute(
            "SELECT 1 FROM watched_items "
            "WHERE media_type = 'show' "
            "AND COALESCE(show_tmdb_id, tmdb_id) = ? LIMIT 1",
            (show_tmdb_id,),
        ).fetchone()
        return row is not None

    def purge_season_zero_watched(self, show_tmdb_id: int) -> None:
        """Delete stray specials (season 0) watched rows for one show."""
        conn = self._ensure_conn()
        conn.execute(
            "DELETE FROM watched_items "
            "WHERE media_type = 'episode' AND show_tmdb_id = ? "
            "AND COALESCE(season_number, 0) <= 0",
            (show_tmdb_id,),
        )
        conn.commit()

    def get_show_status(self, show_tmdb_id: int):
        """Return the cached TMDB status ("Ended", "Returning Series", …)
        for a show, or None when unknown."""
        conn = self._ensure_conn()
        row = conn.execute(
            "SELECT status FROM media_items WHERE tmdb_id = ?",
            (show_tmdb_id,),
        ).fetchone()
        return row[0] if row is not None else None

    def get_watched_show_ids(self) -> set[int]:
        """Return unique show ids with at least one watched episode.

        Falls back to tmdb_id for whole-show rows (media_type='show')
        whose show_tmdb_id is NULL — legacy/imported entries must stay
        eligible for watched checks."""
        conn = self._ensure_conn()
        rows = conn.execute(
            "SELECT DISTINCT COALESCE("
            "  show_tmdb_id,"
            "  CASE WHEN media_type = 'show' THEN tmdb_id END"
            ") FROM watched_items "
            "WHERE show_tmdb_id IS NOT NULL "
            "   OR (media_type = 'show' AND tmdb_id IS NOT NULL)"
        ).fetchall()
        return {r[0] for r in rows if r[0] is not None}

    def get_watched_episodes_for_show(self, show_tmdb_id: int) -> set[tuple]:
        """Return set of (season_number, episode_number) for watched episodes."""
        conn = self._ensure_conn()
        rows = conn.execute(
            "SELECT season_number, episode_number FROM watched_items "
            "WHERE show_tmdb_id = ? AND media_type = 'episode'",
            (show_tmdb_id,),
        ).fetchall()
        return {(r[0], r[1]) for r in rows}

    def get_watched_episode_dates(
        self, show_tmdb_id: int
    ) -> dict[tuple[int, int], int]:
        """Map {(season_number, episode_number): watched_at} for a show."""
        conn = self._ensure_conn()
        rows = conn.execute(
            "SELECT season_number, episode_number, watched_at "
            "FROM watched_items "
            "WHERE show_tmdb_id = ? AND media_type = 'episode'",
            (show_tmdb_id,),
        ).fetchall()
        return {(r[0], r[1]): r[2] for r in rows}

    def get_media_item(self, tmdb_id: int) -> dict | None:
        """Return title, year, poster_url for a tmdb_id from the media cache."""
        conn = self._ensure_conn()
        row = conn.execute(
            "SELECT title, year, poster_url FROM media_items WHERE tmdb_id = ?",
            (tmdb_id,),
        ).fetchone()
        return dict(row) if row else None

    def get_latest_watched_at_for_show(self, show_tmdb_id: int) -> int:
        """Return the most recent watched_at timestamp for a show's episodes."""
        conn = self._ensure_conn()
        row = conn.execute(
            "SELECT MAX(watched_at) FROM watched_items "
            "WHERE show_tmdb_id = ? AND media_type = 'episode'",
            (show_tmdb_id,),
        ).fetchone()
        return row[0] if row and row[0] else 0

    # ------------------------------------------------------------------
    # Diary
    # ------------------------------------------------------------------

    _DAY = "strftime('%Y-%m-%d', w.watched_at, 'unixepoch', 'localtime')"

    def get_diary_days(self, media_type: str | None = None) -> list[dict]:
        """One row per local day that has watched items, newest first.

        Minutes follow the same policy as the profile stats: movies use
        their cached runtime; episodes use exact cached runtimes, with any
        gap filled by the show's per-episode runtime.

        When *media_type* is ``"movie"`` or ``"show"``, only rows of that
        type are counted — giving correct per-day totals for the diary filter.
        """
        conn = self._ensure_conn()
        branches: list[str] = []
        if media_type in (None, "movie"):
            branches.append(f"""
            SELECT {self._DAY} AS day,
                   COUNT(*) AS item_count,
                   COALESCE(SUM(m.runtime), 0) AS minutes
            FROM watched_items w
            LEFT JOIN media_items m ON m.tmdb_id = w.tmdb_id
            WHERE w.media_type = 'movie'
            GROUP BY day""")
        if media_type in (None, "show"):
            branches.append(f"""
            SELECT {self._DAY} AS day,
                   COUNT(*) AS item_count,
                   COALESCE(SUM(e.runtime), 0) AS minutes
            FROM watched_items w
            JOIN episodes e
              ON e.show_tmdb_id = w.show_tmdb_id
             AND e.season_number = w.season_number
             AND e.episode_number = w.episode_number
             AND e.runtime IS NOT NULL
            WHERE w.media_type = 'episode'
            GROUP BY day""")
            branches.append(f"""
            SELECT {self._DAY} AS day,
                   COUNT(*) AS item_count,
                   COUNT(*) * COALESCE(
                       (SELECT runtime FROM media_items
                        WHERE tmdb_id = w.show_tmdb_id), 0) AS minutes
            FROM watched_items w
            LEFT JOIN episodes e
              ON e.show_tmdb_id = w.show_tmdb_id
             AND e.season_number = w.season_number
             AND e.episode_number = w.episode_number
            WHERE w.media_type = 'episode' AND e.runtime IS NULL
            GROUP BY day, w.show_tmdb_id""")
        sql = f"""
        SELECT day, SUM(item_count) AS item_count, SUM(minutes) AS minutes
        FROM ({" UNION ALL ".join(branches)})
        GROUP BY day ORDER BY day DESC
        """
        return [dict(r) for r in conn.execute(sql)]

    def get_diary_entries(self, day: str) -> list[dict]:
        """All diary entries for one ISO day: movies plus shows consolidated
        to one entry per show watched that day."""
        conn = self._ensure_conn()

        def _day_filter(media_types: tuple[str, ...], extra: str = "") -> str:
            marks = ",".join("?" * len(media_types))
            return (
                f"WHERE w.media_type IN ({marks}) "
                f"AND strftime('%Y-%m-%d', w.watched_at,"
                f" 'unixepoch', 'localtime') = ?{extra}"
            )

        movies = conn.execute(
            "SELECT w.tmdb_id, 'movie' AS media_type, m.title, m.year, "
            "m.poster_url, r.rating, w.notes, w.watched_at, "
            "NULL AS season_number, NULL AS episode_number, "
            "NULL AS end_season_number, NULL AS end_episode_number, "
            "NULL AS show_tmdb_id "
            "FROM watched_items w "
            "LEFT JOIN media_items m ON m.tmdb_id = w.tmdb_id "
            "LEFT JOIN ratings r ON r.tmdb_id = w.tmdb_id "
            "AND r.media_type = 'movie' "
            + _day_filter(("movie",)),
            ("movie", day),
        ).fetchall()

        ep_rows = conn.execute(
            "SELECT w.tmdb_id, w.media_type, w.show_tmdb_id, "
            "w.season_number, w.episode_number, w.notes, w.watched_at, "
            "COALESCE(m.title, '') AS title, m.year, m.poster_url, "
            "r.rating "
            "FROM watched_items w "
            "LEFT JOIN media_items m ON m.tmdb_id = COALESCE("
            "w.show_tmdb_id, w.tmdb_id) "
            "LEFT JOIN ratings r ON r.tmdb_id = COALESCE("
            "w.show_tmdb_id, w.tmdb_id) AND r.media_type = 'show' "
            + _day_filter(("show", "episode")),
            ("show", "episode", day),
        ).fetchall()

        entries = [dict(r) for r in movies]

        # Consolidate episode/show rows into one card per show-day, same
        # rule as the history grid.
        groups: dict[int, list] = {}
        for row in ep_rows:
            groups.setdefault(row["show_tmdb_id"] or row["tmdb_id"], []).append(
                row
            )
        movie_counts = self._session_counts(
            [e["tmdb_id"] for e in entries], is_show=False)
        show_counts = self._session_counts(list(groups), is_show=True)
        for show_id, rows in groups.items():
            rows.sort(key=lambda e: (
                e["season_number"] or 0, e["episode_number"] or 0
            ))
            first, last = rows[0], rows[-1]
            # Session-level note: every row of the group carries the same
            # text, so take the first non-null rather than concatenating.
            notes = next((r["notes"] for r in rows if r["notes"]), None)
            entries.append({
                "tmdb_id": show_id,
                "media_type": "show",
                "title": first["title"] or "Unknown",
                "year": first["year"],
                "poster_url": first["poster_url"],
                "rating": first["rating"],
                "notes": notes,
                "watched_at": max(r["watched_at"] for r in rows),
                "season_number": first["season_number"],
                "episode_number": first["episode_number"],
                "end_season_number": last["season_number"],
                "end_episode_number": last["episode_number"],
                "show_tmdb_id": show_id,
                "rewatch_count": max(1, int(show_counts.get(show_id, 1))),
            })

        for e in entries:
            e.setdefault("rewatch_count", 0)
        for e in entries:
            if e["media_type"] == "movie":
                e["rewatch_count"] = max(1, int(movie_counts.get(e["tmdb_id"], 1)))

        entries.sort(key=lambda e: (e.get("title") or "").lower())
        return entries

    def _session_where(self, tmdb_id: int, is_show: bool) -> tuple[str, tuple]:
        """WHERE fragment selecting every watched row of one session kind."""
        if is_show:
            return (
                "media_type IN ('show', 'episode') AND show_tmdb_id = ?",
                (tmdb_id,),
            )
        return ("media_type = 'movie' AND tmdb_id = ?", (tmdb_id,))

    def set_session_notes(
        self, tmdb_id: int, is_show: bool, day: str, text: str | None
    ) -> None:
        """Store a note on every watched row of one diary session.

        A movie session is the single movie row of that day; a show session
        covers all episode (and whole-show) rows of that show on that day.
        """
        conn = self._ensure_conn()
        where, params = self._session_where(tmdb_id, is_show)
        conn.execute(
            f"UPDATE watched_items SET notes = ? WHERE {where} "
            f"AND strftime('%Y-%m-%d', watched_at, 'unixepoch', 'localtime') = ?",
            (text,) + params + (day,),
        )
        conn.commit()

    def reschedule_session(
        self, tmdb_id: int, is_show: bool, old_day: str, new_day: str
    ) -> int:
        """Move one session to another local day, keeping each row's
        time-of-day so intra-day ordering survives. Returns rows moved."""
        import datetime as _dt

        delta = int(
            (
                _dt.datetime.fromisoformat(new_day)
                - _dt.datetime.fromisoformat(old_day)
            ).total_seconds()
        )
        if delta == 0:
            return 0
        conn = self._ensure_conn()
        where, params = self._session_where(tmdb_id, is_show)
        cur = conn.execute(
            f"UPDATE watched_items SET watched_at = watched_at + ? "
            f"WHERE {where} "
            f"AND strftime('%Y-%m-%d', watched_at, 'unixepoch',"
            f" 'localtime') = ?",
            (delta,) + params + (old_day,),
        )
        conn.commit()
        return cur.rowcount

    def delete_session(self, tmdb_id: int, is_show: bool, day: str) -> int:
        """Remove every watched row of one session. Returns rows removed."""
        conn = self._ensure_conn()
        where, params = self._session_where(tmdb_id, is_show)
        cur = conn.execute(
            f"DELETE FROM watched_items WHERE {where} "
            f"AND strftime('%Y-%m-%d', watched_at, 'unixepoch', 'localtime') = ?",
            params + (day,),
        )
        conn.commit()
        return cur.rowcount

    def _session_counts(self, ids: list[int], is_show: bool) -> dict[int, int]:
        """Sessions per id across all days (distinct local days)."""
        if not ids:
            return {}
        conn = self._ensure_conn()
        marks = ",".join("?" * len(ids))
        col, types = (
            ("show_tmdb_id", "('show', 'episode')")
            if is_show else ("tmdb_id", "('movie')")
        )
        sql = (
            f"SELECT {col} AS k, COUNT(DISTINCT strftime("
            f"'%Y-%m-%d', watched_at, 'unixepoch', 'localtime')) AS c "
            f"FROM watched_items WHERE media_type IN {types} "
            f"AND {col} IN ({marks}) GROUP BY k"
        )
        return {r["k"]: r["c"] for r in conn.execute(sql, ids)}

    # ------------------------------------------------------------------
    # Watchlist
    # ------------------------------------------------------------------

    def add_to_watchlist(self, tmdb_id: int, media_type: str) -> None:
        conn = self._ensure_conn()
        now = int(time.time())
        conn.execute(
            "INSERT OR REPLACE INTO watchlist_items (tmdb_id, media_type, added_at) "
            "VALUES (?, ?, ?)",
            (tmdb_id, media_type, now),
        )
        conn.commit()
        self._bump_data_version()

    def remove_from_watchlist(self, tmdb_id: int, media_type: str) -> None:
        conn = self._ensure_conn()
        conn.execute(
            "DELETE FROM watchlist_items WHERE tmdb_id = ? AND media_type = ?",
            (tmdb_id, media_type),
        )
        conn.commit()
        self._bump_data_version()

    def get_watchlist(self, media_type: str | None = None) -> list[dict]:
        """Return watchlist items as dicts (joined with media_items for metadata)."""
        conn = self._ensure_conn()
        query = (
            "SELECT wl.tmdb_id, wl.media_type, wl.added_at, wl.is_anime, "
            "m.title, m.year, m.poster_url, m.runtime, m.imdb_id, m.genres "
            "FROM watchlist_items wl "
            "LEFT JOIN media_items m ON wl.tmdb_id = m.tmdb_id"
        )
        params: tuple = ()
        if media_type:
            query += " WHERE wl.media_type = ?"
            params = (media_type,)
        query += " ORDER BY wl.added_at DESC"
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    def get_currently_watching_shows(self) -> list[dict]:
        """Return shows with watched episodes that aren't on the watchlist.

        These are shows the user is actively watching but haven't explicitly
        added to the watchlist (e.g. imported from Trakt watched history
        without a corresponding watchlist entry).  Returns dicts with the
        same shape as get_watchlist() for compatibility."""
        conn = self._ensure_conn()
        rows = conn.execute(
            "SELECT DISTINCT w.show_tmdb_id AS tmdb_id, "
            "'show' AS media_type, "
            "m.title, m.year, m.poster_url, m.runtime, m.imdb_id, m.genres "
            "FROM watched_items w "
            "JOIN media_items m ON w.show_tmdb_id = m.tmdb_id "
            "WHERE w.show_tmdb_id IS NOT NULL "
            "AND w.media_type = 'episode' "
            "AND w.show_tmdb_id NOT IN "
            "  (SELECT tmdb_id FROM watchlist_items)"
        ).fetchall()
        return [dict(r) for r in rows]

    def get_watchlist_with_dates(self, media_type: str) -> list[dict]:
        """Watchlist items with release_date from the cache, for the calendar."""
        conn = self._ensure_conn()
        query = (
            "SELECT wl.tmdb_id, wl.media_type, wl.added_at, "
            "m.title, m.year, m.poster_url, m.release_date "
            "FROM watchlist_items wl "
            "LEFT JOIN media_items m ON wl.tmdb_id = m.tmdb_id "
            "WHERE wl.media_type = ?"
        )
        rows = conn.execute(query, (media_type,)).fetchall()
        return [dict(r) for r in rows]

    def get_watchlist_ids(self) -> set[int]:
        """Return a set of tmdb_ids currently in the watchlist."""
        conn = self._ensure_conn()
        rows = conn.execute(
            "SELECT tmdb_id FROM watchlist_items"
        ).fetchall()
        return {r[0] for r in rows}

    # ------------------------------------------------------------------
    # Notification overrides
    # ------------------------------------------------------------------

    def get_notification_overrides(self) -> set[tuple[int, str]]:
        """Media with a notification override row (mutes or picks,
        depending on the notification-scope setting)."""
        conn = self._ensure_conn()
        rows = conn.execute(
            "SELECT tmdb_id, media_type FROM notify_selection"
        ).fetchall()
        return {(r[0], r[1]) for r in rows}

    def add_notification_override(self, tmdb_id: int, media_type: str) -> None:
        conn = self._ensure_conn()
        conn.execute(
            "INSERT OR IGNORE INTO notify_selection (tmdb_id, media_type) "
            "VALUES (?, ?)",
            (tmdb_id, media_type),
        )
        conn.commit()

    def remove_notification_override(self, tmdb_id: int, media_type: str) -> None:
        conn = self._ensure_conn()
        conn.execute(
            "DELETE FROM notify_selection WHERE tmdb_id = ? AND media_type = ?",
            (tmdb_id, media_type),
        )
        conn.commit()

    def has_notified(self, key: str) -> bool:
        conn = self._ensure_conn()
        row = conn.execute(
            "SELECT 1 FROM notified_airings WHERE key = ? LIMIT 1", (key,)
        ).fetchone()
        return row is not None

    def mark_notified(self, key: str) -> None:
        conn = self._ensure_conn()
        conn.execute(
            "INSERT OR REPLACE INTO notified_airings (key, notified_at) "
            "VALUES (?, ?)",
            (key, int(time.time())),
        )
        conn.commit()

    # ------------------------------------------------------------------
    # Ratings
    # ------------------------------------------------------------------

    def rate_item(self, tmdb_id: int, media_type: str, rating: int) -> None:
        if not (1 <= rating <= 10):
            raise ValueError("Rating must be between 1 and 5")
        conn = self._ensure_conn()
        now = int(time.time())
        conn.execute(
            "INSERT OR REPLACE INTO ratings (tmdb_id, media_type, rating, rated_at) "
            "VALUES (?, ?, ?, ?)",
            (tmdb_id, media_type, rating, now),
        )
        conn.commit()

    def remove_rating(self, tmdb_id: int, media_type: str) -> None:
        conn = self._ensure_conn()
        conn.execute(
            "DELETE FROM ratings WHERE tmdb_id = ? AND media_type = ?",
            (tmdb_id, media_type),
        )
        conn.commit()

    def get_ratings(self, media_type: str | None = None) -> list[dict]:
        conn = self._ensure_conn()
        query = (
            "SELECT r.tmdb_id, r.media_type, r.rating, r.rated_at, r.is_anime, "
            "m.title, m.year, m.poster_url, m.imdb_id "
            "FROM ratings r "
            "LEFT JOIN media_items m ON r.tmdb_id = m.tmdb_id"
        )
        params: tuple = ()
        if media_type:
            query += " WHERE r.media_type = ?"
            params = (media_type,)
        query += " ORDER BY r.rated_at DESC"
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Collection
    # ------------------------------------------------------------------

    def add_to_collection(self, tmdb_id: int, media_type: str, *, is_anime: int = 0) -> None:
        conn = self._ensure_conn()
        now = int(time.time())
        conn.execute(
            "INSERT OR REPLACE INTO collection_items "
            "(tmdb_id, media_type, collected_at, is_anime) VALUES (?, ?, ?, ?)",
            (tmdb_id, media_type, now, is_anime),
        )
        conn.commit()

    def remove_from_collection(self, tmdb_id: int, media_type: str) -> None:
        conn = self._ensure_conn()
        conn.execute(
            "DELETE FROM collection_items WHERE tmdb_id = ? AND media_type = ?",
            (tmdb_id, media_type),
        )
        conn.commit()

    def get_collection(self, media_type: str | None = None) -> list[dict]:
        conn = self._ensure_conn()
        query = (
            "SELECT c.tmdb_id, c.media_type, c.collected_at, c.is_anime, "
            "m.title, m.year, m.poster_url, m.imdb_id "
            "FROM collection_items c "
            "LEFT JOIN media_items m ON c.tmdb_id = m.tmdb_id"
        )
        params: tuple = ()
        if media_type:
            query += " WHERE c.media_type = ?"
            params = (media_type,)
        query += " ORDER BY c.collected_at DESC"
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    def get_export_data(self) -> "ExportData":
        """Gather all user data into an ExportData container for export."""
        from ..export import ExportData
        return ExportData(
            watched=self.get_watched_list(),
            watchlist=self.get_watchlist(),
            ratings=self.get_ratings(),
            collection=self.get_collection(),
        )

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Watched-verdict persistence (caught-up / fully-watched)
    # ------------------------------------------------------------------

    def get_watched_verdict(self, show_tmdb_id: int, kind: str,
                            fingerprint: str, max_age_s: float | None = 21600.0):
        """Return the persisted verdict for this show/kind when a matching
        fingerprint exists and is fresh enough, else None.

        max_age_s=None means the verdict never expires — used for ended
        shows whose aired set can no longer change."""
        import time as _time
        params: list = [show_tmdb_id, kind, fingerprint]
        query = ("SELECT verdict FROM watched_verdicts "
                 "WHERE show_tmdb_id = ? AND kind = ? AND fingerprint = ?")
        if max_age_s is not None:
            query += " AND computed_at > ?"
            params.append(_time.time() - max_age_s)
        row = self._ensure_conn().execute(query, tuple(params)).fetchone()
        return bool(row[0]) if row is not None else None

    def store_watched_verdict(self, show_tmdb_id: int, kind: str,
                              verdict: bool, fingerprint: str) -> None:
        """Persist a computed verdict. Deliberately does NOT bump
        data_version — the fingerprint already tracks watched changes."""
        import time as _time
        conn = self._ensure_conn()
        conn.execute(
            "INSERT OR REPLACE INTO watched_verdicts "
            "(show_tmdb_id, kind, verdict, fingerprint, computed_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (show_tmdb_id, kind, int(bool(verdict)), fingerprint,
             _time.time()),
        )
        conn.commit()

    def _memo_by_version(self, key, fn):
        """Cache fn() per data_version: repeat calls with unchanged data
        skip the work entirely; any mutation invalidates automatically."""
        cache = getattr(self, "_version_memo", None)
        if cache is None:
            cache = self._version_memo = {}
        version = self._data_version
        hit = cache.get(key)
        if hit is not None and hit[0] == version:
            return hit[1]
        value = fn()
        cache[key] = (version, value)
        return value

    def get_stats(self) -> Stats:
        return self._memo_by_version("stats", self._compute_stats)

    def _compute_stats(self) -> Stats:
        conn = self._ensure_conn()
        return Stats(
            movies_watched=conn.execute(
                "SELECT COUNT(*) FROM watched_items WHERE media_type='movie'"
            ).fetchone()[0],
            shows_watched=conn.execute(
                "SELECT COUNT(DISTINCT show_tmdb_id) FROM watched_items "
                "WHERE show_tmdb_id IS NOT NULL"
            ).fetchone()[0],
            episodes_watched=conn.execute(
                "SELECT COUNT(*) FROM watched_items WHERE media_type='episode'"
            ).fetchone()[0],
            watchlist_items=conn.execute(
                "SELECT COUNT(*) FROM watchlist_items"
            ).fetchone()[0],
            ratings=conn.execute(
                "SELECT COUNT(*) FROM ratings"
            ).fetchone()[0],
            collection_items=conn.execute(
                "SELECT COUNT(*) FROM collection_items"
            ).fetchone()[0],
        )

    def get_watchlist_stats(self) -> dict:
        return self._memo_by_version(
            "watchlist_stats", self._compute_watchlist_stats)

    def _compute_watchlist_stats(self) -> dict:
        conn = self._ensure_conn()
        movie_count = conn.execute(
            "SELECT COUNT(*) FROM watchlist_items WHERE media_type='movie'"
        ).fetchone()[0]
        show_count = conn.execute(
            "SELECT COUNT(*) FROM watchlist_items WHERE media_type='show'"
        ).fetchone()[0]
        movies_runtime = conn.execute(
            "SELECT COALESCE(SUM(m.runtime), 0) FROM watchlist_items wl "
            "JOIN media_items m ON m.tmdb_id = wl.tmdb_id "
            "WHERE wl.media_type='movie'"
        ).fetchone()[0]

        # Shows: prefer actual cached episode runtimes; fill any gap with
        # (episode_count x show per-episode runtime) per season.
        shows_runtime = 0
        for row in conn.execute(
            "SELECT wl.tmdb_id, COALESCE(m.runtime, 0) AS rt "
            "FROM watchlist_items wl "
            "LEFT JOIN media_items m ON m.tmdb_id = wl.tmdb_id "
            "WHERE wl.media_type='show'"
        ):
            show_id, show_rt = row["tmdb_id"], row["rt"]
            actual = conn.execute(
                "SELECT COALESCE(SUM(runtime), 0) FROM episodes "
                "WHERE show_tmdb_id = ? AND season_number > 0 "
                "AND runtime IS NOT NULL",
                (show_id,),
            ).fetchone()[0]
            actual_cnt = conn.execute(
                "SELECT COUNT(*) FROM episodes "
                "WHERE show_tmdb_id = ? AND season_number > 0 "
                "AND runtime IS NOT NULL",
                (show_id,),
            ).fetchone()[0]
            est_cnt = conn.execute(
                "SELECT COALESCE(SUM(episode_count), 0) FROM seasons "
                "WHERE show_tmdb_id = ? AND season_number > 0",
                (show_id,),
            ).fetchone()[0]
            missing = max(0, est_cnt - actual_cnt)
            shows_runtime += int(actual or 0) + missing * int(show_rt or 0)

        total_runtime = int(movies_runtime or 0) + int(shows_runtime or 0)
        total_episodes = conn.execute(
            "SELECT COALESCE(SUM(s.episode_count), 0) "
            "FROM watchlist_items wl "
            "JOIN seasons s ON s.show_tmdb_id = wl.tmdb_id "
            "WHERE wl.media_type='show' AND s.season_number > 0"
        ).fetchone()[0]
        watched_episodes = conn.execute(
            "SELECT COUNT(*) FROM watched_items WHERE media_type='episode' "
            "AND show_tmdb_id IN "
            "(SELECT tmdb_id FROM watchlist_items WHERE media_type='show')"
        ).fetchone()[0]
        watched_movies = {
            r[0]
            for r in conn.execute(
                "SELECT tmdb_id FROM watched_items WHERE media_type='movie'"
            ).fetchall()
        }
        watched_shows = {
            r[0]
            for r in conn.execute(
                "SELECT tmdb_id FROM watched_items WHERE media_type='show' "
                "AND show_tmdb_id IS NULL"
            ).fetchall()
        }
        movie_ids = {
            r[0]
            for r in conn.execute(
                "SELECT tmdb_id FROM watchlist_items WHERE media_type='movie'"
            ).fetchall()
        }
        show_ids = {
            r[0]
            for r in conn.execute(
                "SELECT tmdb_id FROM watchlist_items WHERE media_type='show'"
            ).fetchall()
        }
        to_watch = len(movie_ids - watched_movies) + len(show_ids - watched_shows)
        return {
            "movie_count": movie_count,
            "show_count": show_count,
            "total_runtime": total_runtime,
            "to_watch": to_watch,
            "episodes_to_watch": max(0, total_episodes - watched_episodes),
        }

    def get_watched_runtime(self) -> int:
        """Total minutes watched.

        Movies use their media runtime; episodes use actual cached runtimes.
        Watched episodes whose runtime is not cached are estimated using the
        show's per-episode runtime (media_items.runtime) as a fallback.
        """
        return self._memo_by_version(
            "watched_runtime", self._compute_watched_runtime)

    def _compute_watched_runtime(self) -> int:
        conn = self._ensure_conn()
        movies = conn.execute(
            "SELECT COALESCE(SUM(m.runtime), 0) "
            "FROM watched_items w "
            "LEFT JOIN media_items m ON w.tmdb_id = m.tmdb_id "
            "WHERE w.media_type='movie'"
        ).fetchone()[0]
        episodes = conn.execute(
            "SELECT COALESCE(SUM(e.runtime), 0) "
            "FROM watched_items w "
            "JOIN episodes e ON e.tmdb_id = w.tmdb_id "
            "WHERE w.media_type='episode'"
        ).fetchone()[0]

        # Fallback: watched episodes without a cached runtime.
        missing_minutes = 0
        for row in conn.execute(
            "SELECT w.show_tmdb_id, "
            "COUNT(*) AS watched_cnt, "
            "SUM(CASE WHEN e.runtime IS NOT NULL THEN 1 ELSE 0 END) AS cached_cnt "
            "FROM watched_items w "
            "LEFT JOIN episodes e ON e.tmdb_id = w.tmdb_id "
            "WHERE w.media_type='episode' AND w.show_tmdb_id IS NOT NULL "
            "GROUP BY w.show_tmdb_id"
        ):
            missing = int(row["watched_cnt"]) - int(row["cached_cnt"])
            if missing <= 0:
                continue
            rt = conn.execute(
                "SELECT COALESCE(runtime, 0) FROM media_items WHERE tmdb_id = ?",
                (row["show_tmdb_id"],),
            ).fetchone()
            show_rt = rt[0] if rt else 0
            missing_minutes += missing * int(show_rt or 0)

        return int(movies or 0) + int(episodes or 0) + missing_minutes

    # ------------------------------------------------------------------
    # Import (bulk)
    # ------------------------------------------------------------------

    def find_media_by_imdb_id(self, imdb_id: str) -> dict | None:
        """Return a media cache row matched by IMDb id, if any."""
        conn = self._ensure_conn()
        row = conn.execute(
            "SELECT tmdb_id, media_type, title, year, imdb_id "
            "FROM media_items WHERE lower(imdb_id) = lower(?)",
            (imdb_id,),
        ).fetchone()
        return dict(row) if row else None

    def find_media_by_title_year(
        self, title: str, year: int | None
    ) -> dict | None:
        """Return a media cache row matched by normalized title and year."""
        if not title:
            return None
        conn = self._ensure_conn()
        row = conn.execute(
            "SELECT tmdb_id, media_type, title, year, imdb_id "
            "FROM media_items WHERE lower(title) = lower(?) AND year = ?",
            (title, int(year)) if year is not None else (title, year),
        ).fetchone()
        return dict(row) if row else None

    def get_existing_ids(self, table: str) -> set[int]:
        """Return the set of tmdb_ids already present in a user-data table."""
        conn = self._ensure_conn()
        rows = conn.execute(
            f"SELECT DISTINCT tmdb_id FROM {table}"
        ).fetchall()
        return {int(r[0]) for r in rows}

    def get_media_missing_posters(
        self,
    ) -> list[tuple[int, str, str | None, int | None]]:
        """Return (tmdb_id, media_type, title, year) for media without a poster.

        Items whose fetch was attempted within the last day are excluded so
        unresolvable IDs (junk from Simkl, unreleased movies) aren't
        re-fetched on every sync.
        """
        conn = self._ensure_conn()
        rows = conn.execute(
            "SELECT tmdb_id, media_type, title, year FROM media_items "
            "WHERE (poster_url IS NULL OR poster_url = '') "
            "  AND COALESCE(poster_attempted_at, 0) "
            "      < strftime('%s', 'now') - 86400"
        ).fetchall()
        return [
            (int(r[0]), str(r[1]), r[2], int(r[3]) if r[3] else None)
            for r in rows
        ]

    def mark_poster_attempted(self, tmdb_id: int) -> None:
        """Remember a failed poster fetch so backfill skips it for a day."""
        conn = self._ensure_conn()
        conn.execute(
            "UPDATE media_items SET poster_attempted_at = strftime('%s', 'now') "
            "WHERE tmdb_id = ?",
            (tmdb_id,),
        )
        conn.commit()

    def poster_missing(self, tmdb_id: int) -> bool:
        """True when the item still has no poster URL."""
        conn = self._ensure_conn()
        row = conn.execute(
            "SELECT poster_url FROM media_items WHERE tmdb_id = ?",
            (tmdb_id,),
        ).fetchone()
        return not (row and row[0])

    def merge_orphaned_media(self) -> int:
        """Merge duplicate media_items rows (same title+year, different tmdb_id).

        When Simkl cross-maps anime IDs, a new media_items row is created
        alongside the original.  This finds pairs where one has a poster
        and the other doesn't, redirects references to the good row, and
        deletes the orphan.

        Returns number of orphans merged.
        """
        conn = self._ensure_conn()
        orphans = conn.execute(
            "SELECT o.tmdb_id, g.tmdb_id "
            "FROM media_items o "
            "JOIN media_items g "
            "  ON o.title = g.title "
            "  AND COALESCE(o.year, 0) = COALESCE(g.year, 0) "
            "  AND o.tmdb_id != g.tmdb_id "
            "WHERE (o.poster_url IS NULL OR o.poster_url = '') "
            "  AND g.poster_url IS NOT NULL AND g.poster_url != '' "
            "  AND o.title IS NOT NULL AND o.title != ''"
        ).fetchall()
        count = 0
        for orphan_id, good_id in orphans:
            for table in ("watchlist_items", "watched_items", "ratings"):
                conn.execute(
                    f"UPDATE {table} SET tmdb_id = ? WHERE tmdb_id = ?",
                    (good_id, orphan_id),
                )
            conn.execute(
                "DELETE FROM media_items WHERE tmdb_id = ?", (orphan_id,),
            )
            count += 1
        conn.commit()
        return count

    def _upsert_media_meta(
        self, conn, tmdb_id: int, media_type: str, title: str,
        year: int | None, imdb_id: str | None
    ) -> None:
        if not (title or "").strip():
            exists = conn.execute(
                "SELECT 1 FROM media_items WHERE tmdb_id = ?", (tmdb_id,)
            ).fetchone()
            if exists is None:
                return
        now = int(time.time())
        conn.execute(
            "INSERT INTO media_items "
            "(tmdb_id, media_type, title, year, imdb_id, cached_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(tmdb_id) DO UPDATE SET "
            "media_type=excluded.media_type, "
            "title=COALESCE(NULLIF(excluded.title, ''), media_items.title), "
            "year=COALESCE(excluded.year, media_items.year), "
            "imdb_id=COALESCE(media_items.imdb_id, excluded.imdb_id), "
            "updated_at=excluded.updated_at",
            (
                tmdb_id,
                media_type,
                title,
                year,
                imdb_id or None,
                now,
                now,
            ),
        )

    def import_watched(self, rows: list[dict]) -> int:
        """Bulk-insert watched items with their original timestamps."""
        conn = self._ensure_conn()
        count = 0
        with conn:
            for row in rows:
                if not row.get("tmdb_id"):
                    continue
                tmdb_id = int(row["tmdb_id"])
                media_type = row.get("media_type") or "movie"
                title = row.get("title") or ""
                year = row.get("year")
                imdb_id = row.get("imdb_id")
                watched_at = int(row.get("watched_at") or int(time.time()))
                show_tmdb_id = row.get("show_tmdb_id")
                season_number = row.get("season_number")
                episode_number = row.get("episode_number")
                # Season 0 (specials) does not exist app-wide: skip silently.
                if media_type == "episode" and (season_number or 0) <= 0:
                    continue
                # Episodes can't live in media_items (only movie/show), so
                # cache the parent show there instead.
                if media_type == "episode" and show_tmdb_id:
                    self._upsert_media_meta(
                        conn, show_tmdb_id, "show", title, year, imdb_id
                    )
                else:
                    self._upsert_media_meta(
                        conn, tmdb_id, media_type, title, year, imdb_id
                    )
                conn.execute(
                    "INSERT OR REPLACE INTO watched_items "
                    "(tmdb_id, media_type, show_tmdb_id, season_number, "
                    "episode_number, watched_at, is_anime) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        tmdb_id,
                        media_type,
                        show_tmdb_id,
                        season_number,
                        episode_number,
                        watched_at,
                        int(row.get("is_anime") or 0),
                    ),
                )
                count += 1
        self._bump_data_version()
        return count

    def ensure_watched_shows_on_watchlist(self, watched_rows: list[dict]) -> int:
        """Auto-add shows with watched episodes to the watchlist if missing.

        This ensures shows the user is actively watching always appear
        in the watchlist, even if Trakt didn't include them in the
        watchlist export.  Returns the number of shows added."""
        conn = self._ensure_conn()
        # Collect unique show IDs from the watched rows
        show_ids: set[int] = set()
        for row in watched_rows:
            media_type = row.get("media_type") or "movie"
            if media_type == "episode" and row.get("show_tmdb_id"):
                show_ids.add(int(row["show_tmdb_id"]))
            elif media_type == "show":
                show_ids.add(int(row["tmdb_id"]))
        if not show_ids:
            return 0
        # Find which shows are already on the watchlist
        existing = {
            r[0] for r in conn.execute(
                "SELECT tmdb_id FROM watchlist_items WHERE media_type = 'show'"
            ).fetchall()
        }
        to_add = show_ids - existing
        if not to_add:
            return 0
        now = int(time.time())
        count = 0
        with conn:
            for show_id in to_add:
                conn.execute(
                    "INSERT OR IGNORE INTO watchlist_items "
                    "(tmdb_id, media_type, added_at, is_anime) "
                    "VALUES (?, 'show', ?, 0)",
                    (show_id, now),
                )
                count += 1
        return count

    def import_watchlist(self, rows: list[dict]) -> int:
        """Bulk-insert watchlist items, preserving original timestamps."""
        conn = self._ensure_conn()
        count = 0
        with conn:
            for row in rows:
                if not row.get("tmdb_id"):
                    continue
                tmdb_id = int(row["tmdb_id"])
                media_type = row.get("media_type") or "movie"
                title = row.get("title") or ""
                year = row.get("year")
                imdb_id = row.get("imdb_id")
                added_at = row.get("added_at") or int(time.time())
                self._upsert_media_meta(
                    conn, tmdb_id, media_type, title, year, imdb_id
                )
                conn.execute(
                    "INSERT INTO watchlist_items (tmdb_id, media_type, added_at, is_anime) "
                    "VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(tmdb_id, media_type) DO UPDATE SET "
                    "added_at = COALESCE(excluded.added_at, watchlist_items.added_at), "
                    "is_anime = excluded.is_anime",
                    (tmdb_id, media_type, added_at, int(row.get("is_anime") or 0)),
                )
                count += 1
        self._bump_data_version()
        return count

    def import_ratings(self, rows: list[dict]) -> int:
        """Bulk-insert ratings with their original timestamps/values."""
        conn = self._ensure_conn()
        count = 0
        with conn:
            for row in rows:
                if not row.get("tmdb_id"):
                    continue
                tmdb_id = int(row["tmdb_id"])
                media_type = row.get("media_type") or "movie"
                title = row.get("title") or ""
                year = row.get("year")
                imdb_id = row.get("imdb_id")
                rating = int(row["rating"])
                if not (1 <= rating <= 10):
                    continue
                # The ratings table stores a 1-5 star scale (migration v2
                # halved legacy 1-10 values), so halve imported values.
                stored_rating = max(1, round(rating / 2.0))
                rated_at = int(row.get("rated_at") or int(time.time()))
                self._upsert_media_meta(
                    conn, tmdb_id, media_type, title, year, imdb_id
                )
                conn.execute(
                    "INSERT OR REPLACE INTO ratings "
                    "(tmdb_id, media_type, rating, rated_at, is_anime) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (tmdb_id, media_type, stored_rating, rated_at, int(row.get("is_anime") or 0)),
                )
                count += 1
        return count

    # ------------------------------------------------------------------
    # Anime provenance sync (from TMDB genre data in cache)
    # ------------------------------------------------------------------

    def update_is_anime_from_cache(self, cache) -> int:
        """Update is_anime on all user-data tables using TMDB genre IDs from cache.

        Genre ID 16 = Animation.  Returns the number of rows updated.
        TMDB genre data is the authoritative source for anime status —
        Simkl bucket assignments are just hints.
        """
        TMDB_ANIME_GENRE = 16
        conn = self._ensure_conn()
        updated = 0
        # Collect all unique tmdb_ids across the four user-data tables.
        tmdb_ids: set[int] = set()
        for tbl in ("watchlist_items", "watched_items", "ratings", "collection_items"):
            for row in conn.execute(f"SELECT DISTINCT tmdb_id FROM {tbl}"):
                tmdb_ids.add(row[0])
        if not tmdb_ids:
            return 0
        # Batch lookup genre_ids from cache and update each table.
        genre_map: dict[int, bool] = {}
        for tmdb_id in tmdb_ids:
            genre_ids = cache.get_genre_ids(tmdb_id)
            if genre_ids is not None:
                genre_map[tmdb_id] = TMDB_ANIME_GENRE in genre_ids
        with conn:
            for tbl in ("watchlist_items", "watched_items", "ratings", "collection_items"):
                for row in conn.execute(f"SELECT tmdb_id, is_anime FROM {tbl}"):
                    tmdb_id, current = row[0], row[1]
                    if tmdb_id not in genre_map:
                        continue  # No genre data → keep current value
                    desired = 1 if genre_map[tmdb_id] else 0
                    if current != desired:
                        conn.execute(
                            f"UPDATE {tbl} SET is_anime = ? WHERE tmdb_id = ?",
                            (desired, tmdb_id),
                        )
                        updated += 1
        if updated:
            _log.info("Updated is_anime on %d rows from TMDB genre data", updated)
        return updated

    # ------------------------------------------------------------------
    # Sync push state tracking
    # ------------------------------------------------------------------

    def update_pushed_items(self, backend: str, items) -> None:
        """Record that items were successfully pushed to a backend."""
        import time as _time
        conn = self._ensure_conn()
        now = int(_time.time())
        with conn:
            for item in items:
                key = f"{item.category.value}:{item.tmdb_id}:{item.media_type}"
                conn.execute(
                    "INSERT INTO sync_state (item_key, backend, pushed_at, synced) "
                    "VALUES (?, ?, ?, 1) "
                    "ON CONFLICT(item_key, backend) DO UPDATE SET "
                    "pushed_at = excluded.pushed_at, synced = 1",
                    (key, backend, now),
                )

    def get_pushed_items(self, backend: str) -> tuple[set, set]:
        """Return sets of (tmdb_id, media_type) for last-pushed watchlist and ratings."""
        conn = self._ensure_conn()
        watchlist = set()
        ratings = set()
        rows = conn.execute(
            "SELECT item_key FROM sync_state WHERE backend = ? AND pushed_at IS NOT NULL",
            (backend,),
        ).fetchall()
        for (key,) in rows:
            parts = key.split(":")
            if len(parts) == 3:
                cat, tmdb_id_str, mt = parts
                try:
                    tmdb_id = int(tmdb_id_str)
                except ValueError:
                    continue
                if cat == "watchlist":
                    watchlist.add((tmdb_id, mt))
                elif cat == "ratings":
                    ratings.add((tmdb_id, mt))
        return watchlist, ratings

    def clear_pushed_items(self, backend: str) -> None:
        """Clear pushed state for a backend (e.g., on disconnect)."""
        conn = self._ensure_conn()
        with conn:
            conn.execute(
                "UPDATE sync_state SET pushed_at = NULL WHERE backend = ?",
                (backend,),
            )

    def clear_pushed_state_for(self, backend: str, items: list) -> None:
        """Drop sync_state rows for items pushed-removed from the remote.

        Without this, a removed item stays in the "pushed" diff and the
        sync-conflict dialog would re-offer it on every sync.
        items: list of (tmdb_id, media_type) tuples.
        """
        conn = self._ensure_conn()
        with conn:
            for tmdb_id, media_type in items:
                conn.execute(
                    "DELETE FROM sync_state WHERE backend = ? AND item_key LIKE ?",
                    (backend, f"%:{tmdb_id}:{media_type}"),
                )

    def dismiss_mapping(self, sig_str: str) -> None:
        """Persist a dismissed mapping-decision signature."""
        conn = self._ensure_conn()
        with conn:
            conn.execute(
                "INSERT OR IGNORE INTO dismissed_mappings (sig, created_at) "
                "VALUES (?, ?)",
                (sig_str, int(__import__("time").time())),
            )

    def get_dismissed_mappings(self) -> set:
        """Return set of dismissed mapping-decision signature strings."""
        conn = self._ensure_conn()
        rows = conn.execute(
            "SELECT sig FROM dismissed_mappings"
        ).fetchall()
        return {r[0] for r in rows}

    def remove_local_items(self, items: list[tuple[int, str]]) -> int:
        """Remove items from local DB. Each item is (tmdb_id, media_type).
        Removes from watchlist, ratings, and watched_items."""
        conn = self._ensure_conn()
        count = 0
        with conn:
            for tmdb_id, media_type in items:
                conn.execute(
                    "DELETE FROM watchlist_items WHERE tmdb_id = ? AND media_type = ?",
                    (tmdb_id, media_type),
                )
                conn.execute(
                    "DELETE FROM ratings WHERE tmdb_id = ? AND media_type = ?",
                    (tmdb_id, media_type),
                )
                if media_type == "movie":
                    conn.execute(
                        "DELETE FROM watched_items WHERE tmdb_id = ? AND media_type = 'movie'",
                        (tmdb_id,),
                    )
                elif media_type == "show":
                    conn.execute(
                        "DELETE FROM watched_items WHERE show_tmdb_id = ? OR "
                        "(tmdb_id = ? AND media_type = 'show')",
                        (tmdb_id, tmdb_id),
                    )
                count += 1
        self._bump_data_version()
        return count

    # ------------------------------------------------------------------
    # Transaction snapshot / rollback
    # ------------------------------------------------------------------

    SYNC_SNAPSHOT_TABLES = (
        "media_items",
        "watched_items",
        "watchlist_items",
        "ratings",
        "collection_items",
        "watched_verdicts",
        "sync_state",
        "dismissed_mappings",
    )

    def snapshot_tables(self) -> dict[str, list[list]]:
        """Read all user-data rows, keyed by table name.

        Used by the sync engine to roll back a cancelled run.  Runs on the
        caller's connection (the sync thread) so it sees every write made
        during the run.
        """
        conn = self._ensure_conn()
        snapshot: dict[str, list[list]] = {}
        for table in self.SYNC_SNAPSHOT_TABLES:
            try:
                cols = [c["name"] for c in conn.execute(
                    f"PRAGMA table_info({table})")]
                rows = conn.execute(f"SELECT * FROM {table}").fetchall()
                snapshot[table] = {
                    "cols": cols,
                    "rows": [list(r) for r in rows],
                }
            except sqlite3.Error as e:
                _log.warning("snapshot_tables: skipping %s (%s)", table, e)
        return snapshot

    def restore_tables(self, snapshot: dict[str, list[list]]) -> None:
        """Restore user-data tables to a snapshot, in one transaction.

        Deletes existing rows for each table and re-inserts the snapshot
        rows.  Foreign keys are disabled for the duration so table-ordered
        deletes cannot fail on child references.
        """
        conn = self._ensure_conn()
        had_fk = bool(conn.execute("PRAGMA foreign_keys").fetchone()[0])
        try:
            conn.execute("PRAGMA foreign_keys=OFF")
            with conn:
                for table in self.SYNC_SNAPSHOT_TABLES:
                    entry = snapshot.get(table)
                    if not entry:
                        continue
                    cols = entry["cols"]
                    rows = entry["rows"]
                    conn.execute(f"DELETE FROM {table}")
                    if cols and rows:
                        placeholders = ",".join("?" for _ in cols)
                        colsql = ",".join(f'"{c}"' for c in cols)
                        # Preserve NULL vs omitted; only insert real values.
                        conn.executemany(
                            f"INSERT INTO {table} ({colsql}) "
                            f"VALUES ({placeholders})",
                            rows,
                        )
            self._bump_data_version()
        finally:
            if had_fk:
                conn.execute("PRAGMA foreign_keys=ON")

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
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
