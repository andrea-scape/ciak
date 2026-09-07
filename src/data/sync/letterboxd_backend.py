"""Letterboxd sync backend: experimental web login + scraping.

This backend is fragile — Letterboxd uses Cloudflare, CSRF tokens change
per session, and endpoints may change without notice. Use at your own risk.
"""

import logging
import re
import time

import httpx

from .base import SyncBackend, SyncCapabilities, SyncCategory, SyncItem, SyncResult
from .credentials import (
    delete_service,
    load_credential,
    store_credential,
)

_log = logging.getLogger(__name__)

_BASE = "https://letterboxd.com"
_TIMEOUT = 20.0


class LetterboxdSyncBackend(SyncBackend):
    name = "letterboxd"
    display_name = "Letterboxd"
    icon_name = "text-x-generic-symbolic"
    capabilities = SyncCapabilities(watchlist=True, watched=False, ratings=True, collection=False)

    def __init__(self):
        self._client = httpx.Client(timeout=_TIMEOUT, follow_redirects=True)

    def close(self):
        self._client.close()

    def _cookies(self) -> dict:
        cookie_jar = self._client.cookies
        return {
            name: value
            for name, value in cookie_jar.items()
            if "letterboxd" in name.lower() or "csrf" in name.lower()
        }

    # ---- Auth flow ----

    def _get_csrf(self) -> str | None:
        try:
            resp = self._client.get(f"{_BASE}/sign-in/")
            html = resp.text
            match = re.search(r'name="__csrf"\s+value="([^"]+)"', html)
            if match:
                return match.group(1)
        except Exception:
            _log.exception("Failed to get CSRF token")
        return None

    def authenticate(self, window) -> bool:
        """Run web login flow. Synchronous — caller should run in thread."""
        try:
            username = load_credential("letterboxd", "username")
            password = load_credential("letterboxd", "password")
            if not username or not password:
                return False

            csrf = self._get_csrf()
            if not csrf:
                return False

            resp = self._client.post(
                f"{_BASE}/user/login.do",
                data={
                    "__csrf": csrf,
                    "next": "",
                    "username": username,
                    "password": password,
                    "remember": "true",
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )

            # Check for session cookie
            cookies = resp.cookies
            for name in cookies:
                if "signed.in" in name.lower():
                    store_credential("letterboxd", "session_cookie", cookies[name])
                    return True
            return False
        except Exception:
            _log.exception("Letterboxd auth failed")
            return False

    def is_authenticated(self) -> bool:
        return load_credential("letterboxd", "session_cookie") is not None

    def disconnect(self):
        delete_service("letterboxd")

    # ---- Pull ----

    def _username(self) -> str | None:
        return load_credential("letterboxd", "username")

    def _scrape_page(self, url: str) -> list[dict]:
        """Scrape a Letterboxd page for film entries."""
        entries = []
        resp = self._client.get(url, headers={
            "Cookie": f"letterboxd.signed.in.as="
                      f"{load_credential('letterboxd', 'session_cookie') or ''}",
        })
        if resp.status_code != 200:
            return entries
        for match in re.finditer(
            r'data-film-slug="([^"]+)"', resp.text
        ):
            entries.append({"slug": match.group(1)})
        return entries

    def _scrape_ratings(self, url: str) -> list[dict]:
        """Scrape rating entries from a Letterboxd ratings page."""
        entries = []
        resp = self._client.get(url, headers={
            "Cookie": f"letterboxd.signed.in.as="
                      f"{load_credential('letterboxd', 'session_cookie') or ''}",
        })
        if resp.status_code != 200:
            return entries
        for match in re.finditer(
            r'data-film-slug="([^"]+)"[^>]*class="[^"]*\brated-(\d+)\b',
            resp.text,
        ):
            slug = match.group(1)
            rating_raw = int(match.group(2))
            rating = max(1, min(5, (rating_raw + 1) // 2))
            entries.append({"slug": slug, "rating": rating})
        return entries

    def _resolve_tmdb_id(self, slug: str) -> int | None:
        """Resolve a Letterboxd film slug to a TMDB ID."""
        resp = self._client.get(f"{_BASE}/film/{slug}/")
        match = re.search(r'data-tmdb-id="(\d+)"', resp.text)
        if match:
            return int(match.group(1))
        match2 = re.search(r'themoviedb\.org/movie/(\d+)', resp.text)
        if match2:
            return int(match2.group(1))
        match3 = re.search(r'themoviedb\.org/tv/(\d+)', resp.text)
        if match3:
            return int(match3.group(1))
        return None

    def pull(self) -> list[SyncItem]:
        items: list[SyncItem] = []
        username = self._username()
        if not username:
            return items

        # Watchlist
        page = 1
        while True:
            entries = self._scrape_page(
                f"{_BASE}/{username}/watchlist/page/{page}/"
            )
            if not entries:
                break
            for entry in entries:
                tmdb_id = self._resolve_tmdb_id(entry["slug"])
                if tmdb_id:
                    items.append(SyncItem(
                        tmdb_id=tmdb_id, media_type="movie",
                        category=SyncCategory.WATCHLIST,
                        remote_id=entry["slug"],
                    ))
            page += 1

        # Ratings
        page = 1
        while True:
            entries = self._scrape_ratings(
                f"{_BASE}/{username}/films/ratings/page/{page}/"
            )
            if not entries:
                break
            for entry in entries:
                tmdb_id = self._resolve_tmdb_id(entry["slug"])
                if tmdb_id:
                    items.append(SyncItem(
                        tmdb_id=tmdb_id, media_type="movie",
                        category=SyncCategory.RATINGS,
                        rating=entry["rating"],
                        remote_id=entry["slug"],
                    ))
            page += 1

        return items

    # ---- Push ----

    def push(self, items: list[SyncItem]) -> SyncResult:
        result = SyncResult()
        session_cookie = load_credential("letterboxd", "session_cookie")
        if not session_cookie:
            result.errors.append("Not authenticated")
            return result

        for item in items:
            try:
                slug = item.remote_id
                if not slug:
                    continue

                if item.category == SyncCategory.RATINGS and item.rating is not None:
                    self._client.post(
                        f"{_BASE}/api/v0/production-log-entries",
                        json={
                            "film": {"slug": slug},
                            "rating": {"value": float(item.rating)},
                            "containsSpoilers": False,
                        },
                        headers={
                            "Content-Type": "application/json",
                            "Cookie": f"letterboxd.signed.in.as={session_cookie}",
                        },
                    )
                    result.pushed += 1

                elif item.category == SyncCategory.WATCHLIST:
                    self._client.post(
                        f"{_BASE}/ajax/s/{slug}/watchlist/",
                        headers={
                            "Accept": "application/json",
                            "Cookie": f"letterboxd.signed.in.as={session_cookie}",
                        },
                    )
                    result.pushed += 1

                time.sleep(1)  # Rate limit courtesy
            except Exception as e:
                result.errors.append(f"{item.tmdb_id}: {e}")

        return result
