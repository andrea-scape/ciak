"""TMDB sync backend: account-based watchlist + ratings sync.

Uses the TMDB v3 API with session-based authentication (not API key).
The bundled API key covers metadata; account sync requires user authorization.
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

TMDB_BASE = "https://api.themoviedb.org/3"
_TIMEOUT = 20.0

# TMDB rating scale is 0.5-10.0 in 0.5 increments.
# We normalize to Ciak's 1-5 integer scale.

TMDB_TO_CIAK = {
    (0.0, 1.0): 1,
    (1.0, 2.5): 2,
    (2.5, 4.0): 3,
    (4.0, 5.5): 4,
    (5.5, 10.1): 5,
}

CIAK_TO_TMDB = {1: 1.0, 2: 3.0, 3: 5.0, 4: 7.0, 5: 9.0}


def _normalize_rating(tmdb_rating: float) -> int:
    for (lo, hi), ciak_val in TMDB_TO_CIAK.items():
        if lo <= tmdb_rating < hi:
            return ciak_val
    return 5


def _denormalize_rating(ciak_rating: int) -> float:
    return CIAK_TO_TMDB.get(ciak_rating, 5.0)


def _parse_year(date_str: str | None) -> int | None:
    if not date_str or len(date_str) < 4:
        return None
    try:
        return int(date_str[:4])
    except (ValueError, TypeError):
        return None


class TmdbSyncBackend(SyncBackend):
    name = "tmdb"
    display_name = "TMDB"
    icon_name = "video-x-generic-symbolic"
    capabilities = SyncCapabilities(watchlist=True, watched=False, ratings=True, collection=False)

    def __init__(self, api_key: str):
        self._api_key = api_key
        self._client = httpx.Client(timeout=_TIMEOUT)

    def close(self):
        self._client.close()

    def _authed_get(self, path: str, params: dict | None = None) -> dict:
        session_id = load_credential("tmdb", "session_id")
        p = {"api_key": self._api_key}
        if session_id:
            p["session_id"] = session_id
        if params:
            p.update(params)
        resp = self._client.get(f"{TMDB_BASE}{path}", params=p)
        resp.raise_for_status()
        return resp.json()

    def _authed_post(self, path: str, body: dict) -> dict:
        session_id = load_credential("tmdb", "session_id")
        p = {"api_key": self._api_key}
        if session_id:
            p["session_id"] = session_id
        resp = self._client.post(f"{TMDB_BASE}{path}", params=p, json=body)
        resp.raise_for_status()
        return resp.json()

    # ---- Auth flow ----

    def create_request_token(self) -> str:
        """Step 1: get a request token."""
        resp = self._client.get(
            f"{TMDB_BASE}/authentication/token/new",
            params={"api_key": self._api_key},
        )
        resp.raise_for_status()
        data = resp.json()
        if not data.get("success"):
            raise RuntimeError("Failed to create request token")
        return data["request_token"]

    def create_session(self, request_token: str) -> str:
        """Step 3: exchange approved token for session."""
        resp = self._client.post(
            f"{TMDB_BASE}/authentication/session/new",
            params={"api_key": self._api_key},
            json={"request_token": request_token},
        )
        resp.raise_for_status()
        data = resp.json()
        if not data.get("success"):
            raise RuntimeError("Failed to create session")
        return data["session_id"]

    def _fetch_account_id(self) -> int:
        data = self._authed_get("/account/0")
        return data["id"]

    def authenticate(self, window) -> bool:
        """Run the 3-step token auth flow. Returns True on success.

        This is a synchronous method — caller should run in a thread.
        """
        try:
            token = self.create_request_token()
            import subprocess
            subprocess.Popen(
                ["xdg-open", f"https://www.themoviedb.org/authenticate/{token}"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            # Give user time to approve, then poll for session
            import time
            time.sleep(15)
            session_id = self.create_session(token)
            store_credential("tmdb", "session_id", session_id)
            account_id = self._fetch_account_id()
            store_credential("tmdb", "account_id", str(account_id))
            return True
        except Exception:
            _log.exception("TMDB auth failed")
            return False

    def request_token(self) -> str:
        """Step 1: get a request token. Returns the token string."""
        return self.create_request_token()

    def finalize_session(self, request_token: str) -> bool:
        """Exchange approved token for session. Stores credentials on success."""
        try:
            session_id = self.create_session(request_token)
            store_credential("tmdb", "session_id", session_id)
            account_id = self._fetch_account_id()
            store_credential("tmdb", "account_id", str(account_id))
            return True
        except Exception:
            _log.exception("TMDB session finalization failed")
            return False

    def is_authenticated(self) -> bool:
        return load_credential("tmdb", "session_id") is not None

    def disconnect(self):
        delete_service("tmdb")

    # ---- Pull ----

    def _pull_paginated(self, path: str) -> list[dict]:
        page = 1
        results = []
        while True:
            data = self._authed_get(path, {"page": page})
            results.extend(data.get("results", []))
            if page >= data.get("total_pages", 1):
                break
            page += 1
        return results

    def pull(self) -> list[SyncItem]:
        items: list[SyncItem] = []

        # Watchlist - movies
        for m in self._pull_paginated("/account/0/watchlist/movies"):
            items.append(SyncItem(
                tmdb_id=m["id"],
                media_type="movie",
                category=SyncCategory.WATCHLIST,
                added_at=int(time.time()),
                title=m.get("title"),
                year=_parse_year(m.get("release_date")),
            ))

        # Watchlist - TV
        for m in self._pull_paginated("/account/0/watchlist/tv"):
            items.append(SyncItem(
                tmdb_id=m["id"],
                media_type="show",
                category=SyncCategory.WATCHLIST,
                added_at=int(time.time()),
                title=m.get("name"),
                year=_parse_year(m.get("first_air_date")),
            ))

        # Ratings - movies
        for m in self._pull_paginated("/account/0/rated/movies"):
            items.append(SyncItem(
                tmdb_id=m["id"],
                media_type="movie",
                category=SyncCategory.RATINGS,
                rating=_normalize_rating(m.get("rating", 0)),
                added_at=int(time.time()),
                title=m.get("title"),
                year=_parse_year(m.get("release_date")),
            ))

        # Ratings - TV
        for m in self._pull_paginated("/account/0/rated/tv"):
            items.append(SyncItem(
                tmdb_id=m["id"],
                media_type="show",
                category=SyncCategory.RATINGS,
                rating=_normalize_rating(m.get("rating", 0)),
                added_at=int(time.time()),
                title=m.get("name"),
                year=_parse_year(m.get("first_air_date")),
            ))

        return items

    # ---- Push ----

    def push(self, items: list[SyncItem]) -> SyncResult:
        result = SyncResult()
        for item in items:
            try:
                media_type_str = "movie" if item.media_type == "movie" else "tv"
                if item.category == SyncCategory.WATCHLIST:
                    self._authed_post(
                        "/account/0/watchlist",
                        {
                            "media_type": media_type_str,
                            "media_id": item.tmdb_id,
                            "watchlist": True,
                        },
                    )
                    result.pushed += 1
                elif item.category == SyncCategory.RATINGS and item.rating is not None:
                    path = f"/{media_type_str}/{item.tmdb_id}/rating"
                    self._authed_post(path, {"value": _denormalize_rating(item.rating)})
                    result.pushed += 1
            except httpx.HTTPStatusError as e:
                result.errors.append(f"{item.tmdb_id}: {e.response.status_code}")
            except Exception as e:
                result.errors.append(f"{item.tmdb_id}: {e}")
        return result
