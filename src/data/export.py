"""Export user data to Trakt CSV, Letterboxd CSV, IMDb CSV, or JSON."""

from __future__ import annotations

import csv
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class ExportData:
    """Container for all user data to export."""
    watched: list[dict] = field(default_factory=list)
    watchlist: list[dict] = field(default_factory=list)
    ratings: list[dict] = field(default_factory=list)
    collection: list[dict] = field(default_factory=list)


def _ts_to_date(ts: int | None) -> str:
    """Convert a Unix timestamp to 'YYYY-MM-DD' string."""
    if ts is None:
        return ""
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


def _ts_to_datetime(ts: int | None) -> str:
    """Convert a Unix timestamp to ISO 8601 datetime string."""
    if ts is None:
        return ""
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


# ── Trakt CSV ────────────────────────────────────────────────────────

_TRAKT_HEADERS = [
    "Title", "Year", "IMDB ID", "Type", "Rating", "Watched Date",
    "Stopped Date", "Listing",
]


def _trakt_row(item: dict, listing: str) -> dict:
    """Map a watched/rated/watchlist item to Trakt CSV columns."""
    return {
        "Title": item.get("title", ""),
        "Year": item.get("year", ""),
        "IMDB ID": item.get("imdb_id", ""),
        "Type": item.get("media_type", ""),
        "Rating": item.get("rating", ""),
        "Watched Date": _ts_to_date(item.get("watched_at")),
        "Stopped Date": "",
        "Listing": listing,
    }


def _rating_map(ratings: list[dict]) -> dict[tuple, dict]:
    """Index ratings by (tmdb_id, media_type) for merging into watched rows."""
    return {(r.get("tmdb_id"), r.get("media_type")): r for r in ratings}


def write_trakt_csv(data: ExportData | list[dict], path: str) -> int:
    """Write data in Trakt-compatible CSV format.

    Accepts either an ExportData instance (exports all categories) or a
    flat list of watched items. Returns the number of rows written.
    """
    rows: list[dict] = []
    if isinstance(data, ExportData):
        ratings_by_key = _rating_map(data.ratings)
        for item in data.watched:
            row = _trakt_row(item, "Watched Movies")
            rating = ratings_by_key.pop(
                (item.get("tmdb_id"), item.get("media_type")), {}
            ).get("rating")
            if rating is not None:
                row["Rating"] = rating
            rows.append(row)
        for item in data.watchlist:
            rows.append(_trakt_row(item, "Watchlist"))
        for item in ratings_by_key.values():
            rows.append(_trakt_row(item, "Ratings"))
        for item in data.collection:
            rows.append(_trakt_row(item, "Collection"))
    else:
        for item in data:
            rows.append(_trakt_row(item, "Watched Movies"))

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=_TRAKT_HEADERS)
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


# ── Letterboxd CSV ───────────────────────────────────────────────────

_LETTERBOXD_HEADERS = [
    "Date", "Name", "Year", "Letterboxd URI", "Rating",
    "Rewatch", "Tags", "WatchedDate",
]


def _letterboxd_row(item: dict) -> dict:
    """Map a watched item to Letterboxd CSV columns."""
    return {
        "Date": _ts_to_date(item.get("rated_at") or item.get("watched_at")),
        "Name": item.get("title", ""),
        "Year": item.get("year", ""),
        "Letterboxd URI": "",
        "Rating": _rating_to_letterboxd(item.get("rating")),
        "Rewatch": "",
        "Tags": "",
        "WatchedDate": _ts_to_date(item.get("watched_at")),
    }


def _rating_to_letterboxd(rating: int | None) -> str:
    """Convert rating to Letterboxd half-star scale string."""
    if rating is None:
        return ""
    return str(rating / 2)


def write_letterboxd_csv(data: ExportData | list[dict], path: str) -> int:
    """Write watched/rated/watchlist items in Letterboxd CSV format.

    Accepts either an ExportData instance (exports watched items, and any
    items that are rated or on the watchlist) or a flat list of watched
    items. Returns the number of rows written.
    """
    rows: list[dict] = []
    if isinstance(data, ExportData):
        ratings_by_key = _rating_map(data.ratings)
        for item in data.watched:
            row = _letterboxd_row(item)
            rating = ratings_by_key.pop(
                (item.get("tmdb_id"), item.get("media_type")), {}
            ).get("rating")
            if rating is not None:
                row["Rating"] = _rating_to_letterboxd(rating)
            rows.append(row)
        for item in ratings_by_key.values():
            rows.append(_letterboxd_row(item))
        for item in data.watchlist:
            rows.append(_letterboxd_row(item))
    else:
        for item in data:
            rows.append(_letterboxd_row(item))

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=_LETTERBOXD_HEADERS)
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


# ── IMDb CSV ─────────────────────────────────────────────────────────

_IMDB_HEADERS = [
    "Position", "Const", "Type", "Title", "Original Title",
    "TV Series", "Year", "Runtime (mins)", "Genres", "Rating",
    "Votes", "Resume", "URL",
]


def _imdb_row(item: dict, position: int) -> dict:
    """Map an item to IMDb CSV columns."""
    return {
        "Position": position,
        "Const": item.get("imdb_id", ""),
        "Type": item.get("media_type", ""),
        "Title": item.get("title", ""),
        "Original Title": "",
        "TV Series": "Yes" if item.get("media_type") == "show" else "",
        "Year": item.get("year", ""),
        "Runtime (mins)": item.get("runtime", ""),
        "Genres": "",
        "Rating": item.get("rating", ""),
        "Votes": item.get("votes", ""),
        "Resume": "",
        "URL": f"https://www.imdb.com/title/{item['imdb_id']}/" if item.get("imdb_id") else "",
    }


def write_imdb_csv(data: ExportData | list[dict], path: str) -> int:
    """Write data in IMDb-compatible CSV format. Returns row count."""
    rows: list[dict] = []
    position = 1
    if isinstance(data, ExportData):
        ratings_by_key = _rating_map(data.ratings)
        for item in data.watched:
            rating = ratings_by_key.pop(
                (item.get("tmdb_id"), item.get("media_type")), {}
            ).get("rating")
            merged = dict(item)
            if rating is not None:
                merged["rating"] = rating
            rows.append(_imdb_row(merged, position))
            position += 1
        for item in data.watchlist:
            rows.append(_imdb_row(item, position))
            position += 1
        for item in ratings_by_key.values():
            rows.append(_imdb_row(item, position))
            position += 1
        for item in data.collection:
            rows.append(_imdb_row(item, position))
            position += 1
    else:
        for item in data:
            rows.append(_imdb_row(item, position))
            position += 1

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=_IMDB_HEADERS)
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


# ── JSON (full structured dump) ──────────────────────────────────────

def _serialize_item(item: dict) -> dict:
    """Convert an item dict for JSON serialization (timestamps to ISO)."""
    out = dict(item)
    for key in ("watched_at", "rated_at", "added_at", "collected_at"):
        if key in out and out[key] is not None:
            out[key] = _ts_to_datetime(out[key])
    return out


def write_json(data: ExportData, path: str) -> int:
    """Write all user data as a structured JSON file. Returns item count."""
    payload = {
        "exported_at": _ts_to_datetime(int(time.time())),
        "watched": [_serialize_item(i) for i in data.watched],
        "watchlist": [_serialize_item(i) for i in data.watchlist],
        "ratings": [_serialize_item(i) for i in data.ratings],
        "collection": [_serialize_item(i) for i in data.collection],
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    return sum(
        len(v) for v in (data.watched, data.watchlist, data.ratings, data.collection)
    )
