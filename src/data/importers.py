"""Parsers for importing user data from Trakt/Letterboxd/IMDb CSV and JSON.

Each parser normalizes rows into a common :class:`ImportItem`. A
:class:`Matcher` then resolves items to TMDB ids so they can be written
back into the local database.
"""

from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

_MEDIA_TYPE_ALIASES = {
    "movie": "movie",
    "film": "movie",
    "show": "show",
    "tv": "show",
    "series": "show",
    "episode": "episode",
    "tvepisode": "episode",
}


def _normalize_media_type(value: str | None) -> str | None:
    """Map a source media-type string to ciak's movie/show/episode."""
    if not value:
        return None
    return _MEDIA_TYPE_ALIASES.get(value.strip().lower())


def _parse_int(value) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _parse_rating(value, letterboxd: bool = False) -> int | None:
    """Parse a rating into ciak's 1-10 integer scale."""
    if value in (None, ""):
        return None
    try:
        rating = float(value)
    except (TypeError, ValueError):
        return None
    if letterboxd:
        rating = rating * 2
    rating = round(rating)
    if rating < 1:
        return None
    return min(rating, 10)


def _parse_date(value) -> str | None:
    """Parse a watched/rated date into 'YYYY-MM-DD'."""
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    date_match = re.match(r"(\d{4})-(\d{1,2})-(\d{1,2})", text)
    if date_match:
        y, m, d = (int(part) for part in date_match.groups())
        return f"{y:04d}-{m:02d}-{d:02d}"
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return dt.date().isoformat()
    except ValueError:
        return None


def date_to_ts(value: str | None) -> int | None:
    """Convert a 'YYYY-MM-DD' date to a UTC Unix timestamp."""
    if not value:
        return None
    try:
        dt = datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except ValueError:
        return None


def _split_tags(value) -> list[str]:
    if not value:
        return []
    return [t for t in re.split(r"[,\s]+", str(value).strip()) if t]


def _clean_title(value) -> str:
    if not value:
        return ""
    return str(value).strip()


@dataclass
class ImportItem:
    """A single normalized row read from an import source."""

    title: str = ""
    year: int | None = None
    imdb_id: str | None = None
    media_type: str | None = None
    rating: int | None = None
    watched_date: str | None = None
    tags: list[str] = field(default_factory=list)
    target: str = "watched"  # watched | watchlist | ratings | collection
    source: str = ""  # original title as shown in the source


class ImportParseError(Exception):
    """Raised when an import file cannot be parsed."""


# ----------------------------------------------------------------------
# CSV helpers
# ----------------------------------------------------------------------

def _read_csv_dicts(path: str) -> list[dict]:
    try:
        with open(path, "r", encoding="utf-8-sig", newline="") as f:
            return list(csv.DictReader(f))
    except OSError as exc:
        raise ImportParseError(f"Cannot read file: {exc}") from exc
    except UnicodeDecodeError as exc:
        raise ImportParseError(f"File is not valid UTF-8 text: {exc}") from exc


def _find_col(row: dict, *candidates: str) -> str:
    """Look up a value by case-insensitive header name."""
    lowered = {}
    for k, v in row.items():
        if not k:
            continue
        lowered[k.strip().lower()] = v or ""
    for candidate in candidates:
        key = candidate.strip().lower()
        if key in lowered:
            return lowered[key]
    return ""


# ----------------------------------------------------------------------
# Trakt CSV
# ----------------------------------------------------------------------

class TraktCSV:
    """Parse a Trakt-compatible CSV export (Title, Year, IMDB ID, ...)."""

    _TARGETS = {
        "watched movies": "watched",
        "watched": "watched",
        "watchlist": "watchlist",
        "ratings": "ratings",
        "collection": "collection",
    }

    @classmethod
    def parse(cls, path: str) -> list[ImportItem]:
        rows = _read_csv_dicts(path)
        items: list[ImportItem] = []
        for row in rows:
            title = _clean_title(_find_col(row, "Title"))
            if not title and not any(v for v in row.values()):
                continue
            listing = _find_col(row, "Listing").lower()
            target = cls._TARGETS.get(listing, "watched")
            items.append(
                ImportItem(
                    title=title,
                    year=_parse_int(_find_col(row, "Year")),
                    imdb_id=_find_col(row, "IMDB ID") or None,
                    media_type=_normalize_media_type(
                        _find_col(row, "Type")
                    ),
                    rating=_parse_rating(_find_col(row, "Rating")),
                    watched_date=_parse_date(_find_col(row, "Watched Date")),
                    target=target,
                    source=title,
                )
            )
        return items


# ----------------------------------------------------------------------
# Letterboxd CSV
# ----------------------------------------------------------------------

class LetterboxdCSV:
    """Parse a Letterboxd diary CSV (Date, Name, Year, Rating, ...)."""

    @classmethod
    def parse(cls, path: str) -> list[ImportItem]:
        rows = _read_csv_dicts(path)
        items: list[ImportItem] = []
        for row in rows:
            title = _clean_title(_find_col(row, "Name"))
            if not title and not any(v for v in row.values()):
                continue
            # Letterboxd diary rows carry a watch date; the watchlist import
            # has none, so a missing date implies a watchlist entry.
            watched_date = _parse_date(
                _find_col(row, "Date", "WatchedDate") or None
            )
            rating = _parse_rating(
                _find_col(row, "Rating"), letterboxd=True
            )
            items.append(
                ImportItem(
                    title=title,
                    year=_parse_int(_find_col(row, "Year")),
                    imdb_id=_letterboxd_uri_id(_find_col(row, "Letterboxd URI")),
                    media_type="movie",
                    rating=rating,
                    watched_date=watched_date,
                    tags=_split_tags(_find_col(row, "Tags")),
                    target="watched" if watched_date else "watchlist",
                    source=title,
                )
            )
        return items


def _letterboxd_uri_id(uri_value: str) -> str | None:
    """Extract an IMDb id embedded in a Letterboxd film URI if present."""
    uri = uri_value.strip()
    if not uri:
        return None
    match = re.search(r"tt\d+", uri)
    return match.group(0) if match else None


# ----------------------------------------------------------------------
# IMDb CSV
# ----------------------------------------------------------------------

class IMDbCSV:
    """Parse an IMDb list export CSV (Position, Const, Type, Title, ...)."""

    @classmethod
    def parse(cls, path: str) -> list[ImportItem]:
        rows = _read_csv_dicts(path)
        items: list[ImportItem] = []
        for row in rows:
            title = _clean_title(_find_col(row, "Title", "Original Title"))
            if not title and not any(v for v in row.values()):
                continue
            const = _find_col(row, "Const")
            items.append(
                ImportItem(
                    title=title,
                    year=_parse_int(_find_col(row, "Year")),
                    imdb_id=const or None,
                    media_type=_normalize_media_type(
                        _find_col(row, "Type")
                    ),
                    rating=_parse_rating(_find_col(row, "Rating")),
                    watched_date=_parse_date(_find_col(row, "Watched Date"))
                    or None,
                    target="watchlist",
                    source=title,
                )
            )
        return items


# ----------------------------------------------------------------------
# Generic JSON
# ----------------------------------------------------------------------

def _sniff(value: dict, *keys: str):
    for key in keys:
        if key in value and value[key] not in (None, ""):
            return value[key]
    return None


def _json_item_to_import(value: dict, default_target: str) -> ImportItem:
    title = str(_sniff(value, "title", "name") or "").strip()
    year = _parse_int(_sniff(value, "year", "release_date"))
    imdb_id = _sniff(value, "imdb_id", "imdbId") or None
    media_type = _normalize_media_type(_sniff(value, "media_type", "type"))
    rating = _parse_rating(_sniff(value, "rating"))
    watched_date = _parse_date(
        _sniff(value, "watched_date", "watched_at", "date", "added_at")
        or None
    )
    tags = _sniff(value, "tags") or []
    if isinstance(tags, str):
        tags = _split_tags(tags)
    explicit_target = str(_sniff(value, "target", "listing") or "").lower()
    target_map = {
        "watched": "watched",
        "watchlist": "watchlist",
        "ratings": "ratings",
        "collection": "collection",
    }
    return ImportItem(
        title=title,
        year=year,
        imdb_id=imdb_id,
        media_type=media_type,
        rating=rating,
        watched_date=watched_date,
        tags=tags if isinstance(tags, list) else [],
        target=target_map.get(explicit_target, default_target),
        source=title,
    )


class GenericJSON:
    """Parse generic JSON (an array of items, or a keyed export object)."""

    _CATEGORY_TARGETS = {
        "watched": "watched",
        "watchlist": "watchlist",
        "ratings": "ratings",
        "collection": "collection",
    }

    @classmethod
    def parse(cls, path: str) -> list[ImportItem]:
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except OSError as exc:
            raise ImportParseError(f"Cannot read file: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise ImportParseError(f"File is not valid JSON: {exc}") from exc

        items: list[ImportItem] = []
        if isinstance(data, list):
            for value in data:
                if isinstance(value, dict):
                    items.append(_json_item_to_import(value, "watched"))
        elif isinstance(data, dict):
            for category, raw_items in data.items():
                key = category.strip().lower().replace(" ", "")
                target = cls._CATEGORY_TARGETS.get(key, "watched")
                if not isinstance(raw_items, list):
                    continue
                for value in raw_items:
                    if isinstance(value, dict):
                        item = _json_item_to_import(value, target)
                        item.target = target
                        items.append(item)
        else:
            raise ImportParseError("JSON content is neither an object nor a list")
        return items


# ----------------------------------------------------------------------
# Title matching
# ----------------------------------------------------------------------

_PUNCT_RE = re.compile(r"[\W_]+")


def _norm_title(title: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace."""
    return _PUNCT_RE.sub(" ", title.lower()).strip().replace(" ", " ")


class Matcher:
    """Resolve ImportItems to TMDB ids (local cache first, then TMDB).

    Matching order: local media_items by imdb_id -> local by title+year
    -> TMDB /find by imdb_id -> TMDB search by title+year. An item whose
    tmdb_id already exists in the target table is reported as a duplicate.
    """

    def __init__(self, repository, metadata_service):
        self._repository = repository
        self._service = metadata_service
        self._cache_by_id: dict[str, dict] = {}
        self._cache_by_title: dict[tuple, dict] = {}

    def _existing_ids(self, target: str) -> set[int]:
        table = {
            "watched": "watched_items",
            "watchlist": "watchlist_items",
            "ratings": "ratings",
            "collection": "collection_items",
        }.get(target, "watched_items")
        try:
            return self._repository.get_existing_ids(table)
        except AttributeError:
            return set()

    def _local_by_imdb(self, imdb_id: str) -> dict | None:
        if not imdb_id:
            return None
        if imdb_id in self._cache_by_id:
            return self._cache_by_id[imdb_id]
        row = self._repository.find_media_by_imdb_id(imdb_id)
        self._cache_by_id[imdb_id] = row
        return row

    def _local_by_title_year(self, title: str, year: int | None) -> dict | None:
        key = (_norm_title(title), year)
        if key in self._cache_by_title:
            return self._cache_by_title[key]
        row = self._repository.find_media_by_title_year(title, year)
        self._cache_by_title[key] = row
        return row

    def _tmdb_by_imdb(self, imdb_id: str) -> dict | None:
        if not imdb_id:
            return None
        model = self._service.resolve_imdb(imdb_id)
        if model is None:
            return None
        return {
            "tmdb_id": model.tmdb_id,
            "media_type": model.media_type,
            "title": model.title,
            "year": model.year,
        }

    def _tmdb_search(self, title: str, year: int | None,
                     media_type: str | None) -> dict | None:
        model = self._service.search_best(title, year, media_type)
        if model is None:
            return None
        return {
            "tmdb_id": model.tmdb_id,
            "media_type": model.media_type,
            "title": model.title,
            "year": model.year,
        }

    def match(self, item: ImportItem) -> "MatchResult":
        resolved: dict | None = None

        row = self._local_by_imdb(item.imdb_id or "")
        if row:
            resolved = row
        if resolved is None:
            row = self._local_by_title_year(item.title, item.year)
            if row:
                resolved = row
        if resolved is None:
            resolved = self._tmdb_by_imdb(item.imdb_id or "")
        if resolved is None:
            resolved = self._tmdb_search(
                item.title, item.year, item.media_type
            )

        if resolved is None:
            return MatchResult("unmatched", None, None, item)
        return MatchResult(
            "duplicate" if resolved["tmdb_id"] in self._existing_ids(item.target)
            else "matched",
            resolved["tmdb_id"],
            resolved.get("media_type") or item.media_type,
            item,
        )


@dataclass
class MatchResult:
    """Outcome of matching one ImportItem to the local database."""

    status: str  # matched | unmatched | duplicate
    tmdb_id: int | None
    media_type: str | None
    item: ImportItem


def select_parser(path: str):
    """Pick the right parser class based on file extension and headers."""
    if path.lower().endswith(".json"):
        return GenericJSON

    headers: list[str] = []
    try:
        with open(path, "r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            headers = [
                h.strip().lower() for h in (reader.fieldnames or []) if h
            ]
    except (OSError, UnicodeDecodeError):
        return TraktCSV

    header_set = set(headers)
    if "const" in header_set or "position" in header_set:
        return IMDbCSV
    if "name" in header_set and ("date" in header_set or "year" in header_set):
        return LetterboxdCSV
    return TraktCSV