"""Local SQLite implementation of UserMediaRepository.

All user state (watched, watchlist, ratings, collection) is stored in
a single SQLite database file.  Methods are synchronous and safe for
use from background threads (check_same_thread=False, WAL mode).
"""

import sqlite3
import threading
import time

from ...domain.exceptions import RepositoryError
from ...domain.models import Stats


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
        conn = self._ensure_conn()
        now = int(time.time())
        conn.execute(
            "INSERT OR REPLACE INTO watched_items "
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

    def get_watched_list(self, media_type: str | None = None) -> list[dict]:
        """Return all watched items as dicts with tmdb_id, title, etc."""
        conn = self._ensure_conn()
        query = (
            "SELECT w.tmdb_id, w.media_type, w.show_tmdb_id, w.season_number, "
            "w.episode_number, w.watched_at, "
            "m.title, m.year, m.poster_url "
            "FROM watched_items w "
            "LEFT JOIN media_items m ON w.tmdb_id = m.tmdb_id "
        )
        params: tuple = ()
        if media_type == "movie":
            query += "WHERE w.media_type = ?"
            params = ("movie",)
        elif media_type == "show":
            query += "WHERE w.media_type IN ('show', 'episode')"
        query += " ORDER BY w.watched_at DESC"
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

    def get_watched_show_ids(self) -> set[int]:
        """Return unique show_tmdb_ids that have at least one watched episode."""
        conn = self._ensure_conn()
        rows = conn.execute(
            "SELECT DISTINCT show_tmdb_id FROM watched_items "
            "WHERE show_tmdb_id IS NOT NULL"
        ).fetchall()
        return {r[0] for r in rows}

    def get_watched_episodes_for_show(self, show_tmdb_id: int) -> set[tuple]:
        """Return set of (season_number, episode_number) for watched episodes."""
        conn = self._ensure_conn()
        rows = conn.execute(
            "SELECT season_number, episode_number FROM watched_items "
            "WHERE show_tmdb_id = ? AND media_type = 'episode'",
            (show_tmdb_id,),
        ).fetchall()
        return {(r[0], r[1]) for r in rows}

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

    def remove_from_watchlist(self, tmdb_id: int, media_type: str) -> None:
        conn = self._ensure_conn()
        conn.execute(
            "DELETE FROM watchlist_items WHERE tmdb_id = ? AND media_type = ?",
            (tmdb_id, media_type),
        )
        conn.commit()

    def get_watchlist(self, media_type: str | None = None) -> list[dict]:
        """Return watchlist items as dicts (joined with media_items for metadata)."""
        conn = self._ensure_conn()
        query = (
            "SELECT wl.tmdb_id, wl.media_type, wl.added_at, "
            "m.title, m.year, m.poster_url, m.runtime "
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

    def get_watchlist_ids(self) -> set[int]:
        """Return a set of tmdb_ids currently in the watchlist."""
        conn = self._ensure_conn()
        rows = conn.execute(
            "SELECT tmdb_id FROM watchlist_items"
        ).fetchall()
        return {r[0] for r in rows}

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
            "SELECT r.tmdb_id, r.media_type, r.rating, r.rated_at, "
            "m.title, m.year, m.poster_url "
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

    def add_to_collection(self, tmdb_id: int, media_type: str) -> None:
        conn = self._ensure_conn()
        now = int(time.time())
        conn.execute(
            "INSERT OR REPLACE INTO collection_items "
            "(tmdb_id, media_type, collected_at) VALUES (?, ?, ?)",
            (tmdb_id, media_type, now),
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
            "SELECT c.tmdb_id, c.media_type, c.collected_at, "
            "m.title, m.year, m.poster_url "
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

    def get_stats(self) -> Stats:
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
