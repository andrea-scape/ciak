"""Simkl sync backend: full watchlist + watched + ratings + collection.

Uses Simkl's free API with PIN-based device authentication.
Client ID is free at https://simkl.com/settings/developer/.
"""

import logging
import time

import httpx

from .base import SyncBackend, SyncCapabilities, SyncCategory, SyncItem, SyncResult
from .credentials import (
    delete_service,
    load_credential,
    store_credential,
)

_log = logging.getLogger(__name__)

SIMKL_BASE = "https://api.simkl.com"
_TIMEOUT = 20.0

# Simkl rating scale: 1-10 integer.  Ciak uses 1-5.

SIMKL_TO_CIAK = {1: 1, 2: 1, 3: 2, 4: 2, 5: 3, 6: 3, 7: 4, 8: 4, 9: 5, 10: 5}
CIAK_TO_SIMKL = {1: 1, 2: 3, 3: 5, 4: 7, 5: 9}


def _normalize_rating(simkl_rating: int) -> int:
    return SIMKL_TO_CIAK.get(simkl_rating, 3)


def _denormalize_rating(ciak_rating: int) -> int:
    return CIAK_TO_SIMKL.get(ciak_rating, 5)


class SimklSyncBackend(SyncBackend):
    name = "simkl"
    display_name = "Simkl"
    icon_name = "tv-symbolic"
    capabilities = SyncCapabilities(
        watchlist=True, watched=True, ratings=True, collection=True
    )

    def __init__(self, client_id: str):
        self._client_id = client_id
        self._client = httpx.Client(timeout=_TIMEOUT, follow_redirects=True)
        self.skipped: list = []  # SyncItems w/o TMDB id, read by the engine

    def close(self):
        self._client.close()

    def _headers(self, with_auth: bool = True) -> dict:
        h = {
            "User-Agent": "Ciak/1.0",
        }
        if with_auth:
            token = load_credential("simkl", "access_token")
            if token:
                h["Authorization"] = f"Bearer {token}"
        return h

    def _base_params(self, **extra) -> dict:
        """Standard query params required by every Simkl API call."""
        params = {
            "client_id": self._client_id,
            "app-name": "Ciak",
            "app-version": "1.0",
        }
        params.update(extra)
        return params

    # ---- Auth flow ----

    def get_user_code(self) -> dict:
        """Step 1: get a user code for PIN auth."""
        resp = self._client.get(
            f"{SIMKL_BASE}/oauth/pin",
            params=self._base_params(),
        )
        resp.raise_for_status()
        return resp.json()

    def poll_code(self, user_code: str) -> dict:
        """Step 3: poll for approval."""
        resp = self._client.get(
            f"{SIMKL_BASE}/oauth/pin/{user_code}",
            params=self._base_params(),
            headers=self._headers(with_auth=False),
        )
        resp.raise_for_status()
        return resp.json()

    def authenticate(self, window) -> bool:
        """Run the PIN auth flow. Synchronous — caller should run in thread."""
        try:
            code_data = self.get_user_code()
            user_code = code_data["user_code"]
            interval = code_data.get("interval", 5)
            expires_in = code_data.get("expires_in", 600)

            import subprocess
            subprocess.Popen(
                ["xdg-open", "https://simkl.com/pin"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

            deadline = time.time() + expires_in
            while time.time() < deadline:
                time.sleep(interval)
                result = self.poll_code(user_code)
                if result.get("result") == "OK":
                    token = result["access_token"]
                    store_credential("simkl", "access_token", token)
                    store_credential("simkl", "client_id", self._client_id)
                    return True
            return False
        except Exception:
            _log.exception("Simkl auth failed")
            return False

    def request_code(self) -> dict:
        """Step 1: get a user code for PIN auth.
        Returns {"user_code": "...", "interval": 5, "expires_in": 600}."""
        data = self.get_user_code()
        _log.info("Simkl code requested: %s", data.get("user_code"))
        return data

    def poll_and_finalize(self, user_code: str, interval: int, expires_in: int) -> bool:
        """Poll until approved or timeout. Stores credentials on success."""
        deadline = time.time() + expires_in
        while time.time() < deadline:
            time.sleep(interval)
            try:
                result = self.poll_code(user_code)
            except Exception as exc:
                _log.warning("Simkl poll error: %s", exc)
                continue
            _log.debug("Simkl poll: %s", result.get("result"))
            if result.get("result") == "OK":
                if "access_token" not in result:
                    _log.warning("Simkl poll got OK without access_token (code expired/consumed)")
                    return False
                store_credential("simkl", "access_token", result["access_token"])
                store_credential("simkl", "client_id", self._client_id)
                _log.info("Simkl auth OK, token stored")
                return True
        _log.info("Simkl poll timed out after %ds", expires_in)
        return False

    def is_authenticated(self) -> bool:
        return load_credential("simkl", "access_token") is not None

    def disconnect(self):
        delete_service("simkl")

    # ---- Pull ----

    def _skip_unmapped(self, title, year, media_type, simkl_id=None) -> None:
        """Queue an item with no TMDB identity for the mapping wizard.

        The item is recorded as a watchlist entry.  Once the user picks a
        mapping the engine imports it and pushes the corrected ID to
        Simkl; the next pull then returns the full listing (including
        any watched episodes and ratings) under the now-valid mapping.
        """
        self.skipped.append(SyncItem(
            tmdb_id=None, media_type=media_type,
            category=SyncCategory.WATCHLIST,
            title=title or "Unknown",
            year=year, remote_id=simkl_id,
        ))

    def pull(self) -> list[SyncItem]:
        items: list[SyncItem] = []
        self.skipped = []
        data = self._client.get(
            f"{SIMKL_BASE}/sync/all-items",
            params=self._base_params(extended="full",
                    episode_watched_at="yes"),
            headers=self._headers(),
        ).json()

        _log.debug("Simkl /sync/all-items: %d shows, %d movies, %d anime",
                    len(data.get("shows", [])), len(data.get("movies", [])),
                    len(data.get("anime", [])))

        # Track TMDB IDs from the shows array so the anime loop can
        # skip duplicates — Simkl's shows array is canonical.
        seen_tmdb_ids: set[int] = set()

        # Watchlist: plantowatch status
        for show in data.get("shows", []):
            show_obj = show.get("show", {})
            tmdb_id = show_obj.get("ids", {}).get("tmdb")
            if not tmdb_id:
                _log.warning("Skipping show without TMDB ID: %r", show_obj.get("title"))
                self._skip_unmapped(
                    title=show_obj.get("title"), year=show_obj.get("year"),
                    media_type="show",
                    simkl_id=show_obj.get("ids", {}).get("simkl"),
                )
                continue
            s_title = show_obj.get("title")
            s_year = show_obj.get("year")
            status = show.get("status", "")
            _log.info("Simkl show entry: title=%r tmdb=%s year=%s status=%r",
                      s_title, tmdb_id, s_year, status)
            if status in ("plantowatch", "watching", "hold"):
                items.append(SyncItem(
                    tmdb_id=tmdb_id, media_type="show",
                    category=SyncCategory.WATCHLIST,
                    title=s_title, year=s_year,
                ))
            # Watched — always check episodes regardless of show status;
            # on Simkl a show can be in watchlist AND have watched episodes.
            for season in show.get("seasons", []):
                for ep in season.get("episodes", []):
                    ts = ep.get("watched_at")
                    if not ts:
                        continue
                    watched_at = _parse_ts(ts)
                    items.append(SyncItem(
                        tmdb_id=tmdb_id, media_type="show",
                        category=SyncCategory.WATCHED,
                        watched_at=watched_at,
                        title=s_title, year=s_year,
                        extra={"season": season["number"],
                               "episode": ep["number"]},
                    ))
            # Ratings
            rating = show.get("user_rating")
            if rating:
                items.append(SyncItem(
                    tmdb_id=tmdb_id, media_type="show",
                    category=SyncCategory.RATINGS,
                    rating=_normalize_rating(int(rating)),
                    title=s_title, year=s_year,
                ))
            seen_tmdb_ids.add(tmdb_id)

        # Anime — same structure as shows, TMDB cross-mapped
        # Simkl's all-items does NOT include tmdb in anime ids — must resolve via detail endpoint
        for anime in data.get("anime", []):
            show_obj = anime.get("show", {})
            tmdb_id = show_obj.get("ids", {}).get("tmdb")
            a_title = show_obj.get("title", "Unknown")
            a_year = show_obj.get("year")
            _log.debug(
                "Anime all-items IDs: title=%s, ids_keys=%s, tmdb=%s",
                a_title, list(show_obj.get("ids", {}).keys()), tmdb_id,
            )
            _log.info("Simkl anime entry: title=%r tmdb=%s year=%s",
                      a_title, tmdb_id, a_year)
            if not tmdb_id:
                simkl_id = show_obj.get("ids", {}).get("simkl")
                if simkl_id:
                    try:
                        resp = self._client.get(
                            f"{SIMKL_BASE}/anime/{simkl_id}",
                            params=self._base_params(),
                            headers=self._headers(with_auth=False),
                        )
                        if resp.status_code == 200:
                            detail = resp.json()
                            tmdb_id = detail.get("ids", {}).get("tmdb")
                            if not tmdb_id:
                                _log.debug(
                                    "Anime detail has no TMDB: %s (simkl=%s, ids=%s)",
                                    a_title, simkl_id,
                                    list(detail.get("ids", {}).keys()),
                                )
                        else:
                            _log.warning(
                                "Anime detail HTTP %d: %s (simkl=%s) — body=%s",
                                resp.status_code, a_title, simkl_id,
                                resp.text[:200],
                            )
                    except Exception as exc:
                        _log.warning(
                            "Failed to resolve TMDB for anime %s (simkl=%s): %s",
                            a_title, simkl_id, exc,
                        )
                if not tmdb_id:
                    _log.warning(
                        "Skipping anime without TMDB ID: %s (simkl=%s)",
                        show_obj.get("title"),
                        show_obj.get("ids", {}).get("simkl"),
                    )
                    self._skip_unmapped(
                        title=show_obj.get("title"),
                        year=show_obj.get("year"),
                        media_type="show",
                        simkl_id=show_obj.get("ids", {}).get("simkl"),
                    )
                    continue
            # Skip anime entries already seen in the shows array —
            # Simkl's shows array is canonical.  Dupes come from the
            # old push-to-anime mirror bug.
            if tmdb_id in seen_tmdb_ids:
                _log.debug(
                    "Skipping anime dupe already in shows: %s (tmdb=%s)",
                    a_title, tmdb_id,
                )
                continue
            a_title = show_obj.get("title")
            a_year = show_obj.get("year")
            status = anime.get("status", "")
            if status in ("plantowatch", "watching", "hold"):
                items.append(SyncItem(
                    tmdb_id=tmdb_id, media_type="show",
                    category=SyncCategory.WATCHLIST,
                    title=a_title, year=a_year,
                    extra={"source_bucket": "anime"},
                ))
            # Watched — always check episodes regardless of show status
            for season in anime.get("seasons", []):
                for ep in season.get("episodes", []):
                    ts = ep.get("watched_at")
                    if not ts:
                        continue
                    watched_at = _parse_ts(ts)
                    items.append(SyncItem(
                        tmdb_id=tmdb_id, media_type="show",
                        category=SyncCategory.WATCHED,
                        watched_at=watched_at,
                        title=a_title, year=a_year,
                        extra={"season": season["number"],
                               "episode": ep["number"],
                               "source_bucket": "anime"},
                    ))
            rating = anime.get("user_rating")
            if rating:
                items.append(SyncItem(
                    tmdb_id=tmdb_id, media_type="show",
                    category=SyncCategory.RATINGS,
                    rating=_normalize_rating(int(rating)),
                    title=a_title, year=a_year,
                    extra={"source_bucket": "anime"},
                ))

        for movie in data.get("movies", []):
            movie_obj = movie.get("movie", {})
            tmdb_id = movie_obj.get("ids", {}).get("tmdb")
            if not tmdb_id:
                _log.warning("Skipping movie without TMDB ID: %r", movie_obj.get("title"))
                self._skip_unmapped(
                    title=movie_obj.get("title"), year=movie_obj.get("year"),
                    media_type="movie",
                    simkl_id=movie_obj.get("ids", {}).get("simkl"),
                )
                continue
            m_title = movie_obj.get("title")
            m_year = movie_obj.get("year")
            _log.info("Simkl movie entry: title=%r tmdb=%s year=%s",
                      m_title, tmdb_id, m_year)
            status = movie.get("status", "")
            if status in ("plantowatch", "watching", "hold"):
                items.append(SyncItem(
                    tmdb_id=tmdb_id, media_type="movie",
                    category=SyncCategory.WATCHLIST,
                    title=m_title, year=m_year,
                ))
            # Watched — always check regardless of status
            ts = movie.get("last_watched_at")
            if ts:
                watched_at = _parse_ts(ts)
                items.append(SyncItem(
                    tmdb_id=tmdb_id, media_type="movie",
                    category=SyncCategory.WATCHED,
                    watched_at=watched_at,
                    title=m_title, year=m_year,
                ))
            rating = movie.get("user_rating")
            if rating:
                items.append(SyncItem(
                    tmdb_id=tmdb_id, media_type="movie",
                    category=SyncCategory.RATINGS,
                    rating=_normalize_rating(int(rating)),
                    title=m_title, year=m_year,
                ))

        _log.debug("Simkl pull produced %d SyncItems", len(items))
        return items

    # ---- Push ----

    def push(self, items: list[SyncItem]) -> SyncResult:
        result = SyncResult()
        watchlist_shows = []
        watchlist_movies = []
        watchlist_anime = []
        watched_shows = []
        watched_movies = []
        watched_anime = []
        rating_shows = []
        rating_movies = []
        rating_anime = []

        for item in items:
            ids = {"tmdb": item.tmdb_id}
            if item.remote_id:
                ids["simkl"] = int(item.remote_id)

            is_anime = item.extra.get("source_bucket") == "anime"

            if item.category == SyncCategory.WATCHLIST:
                if is_anime:
                    watchlist_anime.append({"ids": ids, "to": "plantowatch"})
                else:
                    target = watchlist_shows if item.media_type == "show" else watchlist_movies
                    target.append({"ids": ids, "to": "plantowatch"})
            elif item.category == SyncCategory.WATCHED:
                if item.media_type == "show":
                    season_num = item.extra.get("season", 1)
                    ep_num = item.extra.get("episode", 1)
                    ts = time.strftime("%Y-%m-%d", time.gmtime(item.watched_at)) if item.watched_at else None
                    entry = {"ids": ids, "seasons": [{"number": season_num,
                            "episodes": [{"number": ep_num, "watched_at": ts}]}]}
                    if is_anime:
                        watched_anime.append(entry)
                    else:
                        watched_shows.append(entry)
                else:
                    ts = time.strftime("%Y-%m-%d", time.gmtime(item.watched_at)) if item.watched_at else None
                    watched_movies.append({"ids": ids, "watched_at": ts})
            elif item.category == SyncCategory.RATINGS and item.rating is not None:
                if is_anime:
                    rating_anime.append({"ids": ids, "rating": _denormalize_rating(item.rating)})
                else:
                    target = rating_shows if item.media_type == "show" else rating_movies
                    target.append({"ids": ids, "rating": _denormalize_rating(item.rating)})

        try:
            if watchlist_shows or watchlist_anime or watchlist_movies:
                body = {}
                if watchlist_shows:
                    body["shows"] = watchlist_shows
                if watchlist_anime:
                    body["anime"] = watchlist_anime
                if watchlist_movies:
                    body["movies"] = watchlist_movies
                resp = self._client.post(
                    f"{SIMKL_BASE}/sync/add-to-list",
                    json=body,
                    params=self._base_params(),
                    headers=self._headers(),
                )
                resp.raise_for_status()
                result.pushed += len(watchlist_shows) + len(watchlist_anime) + len(watchlist_movies)

            if watched_shows or watched_anime or watched_movies:
                body = {}
                if watched_shows:
                    body["shows"] = watched_shows
                if watched_anime:
                    body["anime"] = watched_anime
                if watched_movies:
                    body["movies"] = watched_movies
                resp = self._client.post(
                    f"{SIMKL_BASE}/sync/history",
                    json=body,
                    params=self._base_params(),
                    headers=self._headers(),
                )
                resp.raise_for_status()
                result.pushed += len(watched_shows) + len(watched_anime) + len(watched_movies)

            if rating_shows or rating_anime or rating_movies:
                body = {}
                if rating_shows:
                    body["shows"] = rating_shows
                if rating_anime:
                    body["anime"] = rating_anime
                if rating_movies:
                    body["movies"] = rating_movies
                resp = self._client.post(
                    f"{SIMKL_BASE}/sync/ratings",
                    json=body,
                    params=self._base_params(),
                    headers=self._headers(),
                )
                resp.raise_for_status()
                result.pushed += len(rating_shows) + len(rating_anime) + len(rating_movies)

        except httpx.HTTPStatusError as e:
            result.errors.append(f"HTTP {e.response.status_code}")
        except Exception as e:
            result.errors.append(str(e))

        return result

    # ---- Deletion support ----

    def get_activities(self) -> dict:
        """GET /sync/activities — cheapest check for changes."""
        resp = self._client.get(
            f"{SIMKL_BASE}/sync/activities",
            params=self._base_params(),
            headers=self._headers(),
        )
        resp.raise_for_status()
        return resp.json()

    def pull_ids_only(self) -> dict:
        """GET /sync/all-items with extended=ids_only — minimal payload for deletion detection."""
        resp = self._client.get(
            f"{SIMKL_BASE}/sync/all-items",
            params=self._base_params(extended="ids_only",
                    episode_watched_at="yes"),
            headers=self._headers(),
        )
        resp.raise_for_status()
        return resp.json()

    def remove_from_history(self, items: list[SyncItem]) -> SyncResult:
        """POST /sync/history/remove — removes items from library entirely.

        Sending a show/movie with no seasons or episodes removes the whole
        library entry: the watchlist status, watch history, and rating in one
        call.  Anime must be folded into the ``shows`` array (a top-level
        ``anime`` array is ignored on this endpoint).
        """
        shows = []
        movies = []
        for item in items:
            ids = {"tmdb": item.tmdb_id}
            is_anime = item.extra.get("source_bucket") == "anime"
            if is_anime or item.media_type == "show":
                shows.append({"ids": ids})
            else:
                movies.append({"ids": ids})
        body = {}
        if shows:
            body["shows"] = shows
        if movies:
            body["movies"] = movies
        if not body:
            return SyncResult()
        try:
            resp = self._client.post(
                f"{SIMKL_BASE}/sync/history/remove",
                json=body,
                params=self._base_params(),
                headers=self._headers(),
            )
            resp.raise_for_status()
            result = SyncResult()
            deleted = resp.json().get("deleted", {})
            result.pushed = deleted.get("shows", 0) + deleted.get("movies", 0)
            return result
        except httpx.HTTPStatusError as e:
            return SyncResult(errors=[f"HTTP {e.response.status_code}"])
        except Exception as e:
            return SyncResult(errors=[str(e)])

    def remove_all(self, progress_cb=None) -> SyncResult:
        """Wipe every item from the remote account (watchlist, history, ratings).

        Enumerates the full library via ``ids_only`` and removes it in batches
        through ``/sync/history/remove`` (which clears the watchlist entry,
        watch history, and rating in a single call per item).  Simkl throttles
        writes to ~1 POST/sec, so batches are sent sequentially with a small
        delay.  ``progress_cb(removed, total)`` is invoked after each batch.
        """
        result = SyncResult()
        try:
            remote_ids = self.pull_ids_only()
        except Exception as e:
            return SyncResult(errors=[f"Failed to load account items: {e}"])

        shows = []
        movies = []
        for item in remote_ids.get("shows", []):
            tmdb = item.get("ids", {}).get("tmdb")
            if tmdb is not None:
                shows.append({"ids": {"tmdb": tmdb}})
        for item in remote_ids.get("movies", []):
            tmdb = item.get("ids", {}).get("tmdb")
            if tmdb is not None:
                movies.append({"ids": {"tmdb": tmdb}})
        for item in remote_ids.get("anime", []):
            tmdb = item.get("ids", {}).get("tmdb")
            if tmdb is not None:
                shows.append({"ids": {"tmdb": tmdb}})

        total = len(shows) + len(movies)
        if total == 0:
            return result

        BATCH = 100

        def _send(bucket, key):
            for i in range(0, len(bucket), BATCH):
                chunk = bucket[i:i + BATCH]
                body = {key: chunk}
                try:
                    resp = self._client.post(
                        f"{SIMKL_BASE}/sync/history/remove",
                        json=body,
                        params=self._base_params(),
                        headers=self._headers(),
                    )
                    resp.raise_for_status()
                    deleted = resp.json().get("deleted", {}) or {}
                    n = deleted.get("shows", 0) + deleted.get("movies", 0)
                    result.pushed += n if n else len(chunk)
                except httpx.HTTPStatusError as e:
                    result.errors.append(f"HTTP {e.response.status_code}")
                except Exception as e:
                    result.errors.append(str(e))
                if progress_cb:
                    progress_cb(result.pushed, total)
                time.sleep(1.2)

        _send(shows, "shows")
        _send(movies, "movies")
        return result


def _parse_ts(ts_str: str) -> int | None:
    """Parse ISO-8601 timestamp to epoch seconds."""
    if not ts_str:
        return None
    try:
        from datetime import datetime, timezone
        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        return int(dt.timestamp())
    except Exception:
        return None
