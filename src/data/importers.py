"""Parsers for importing user data from Trakt/Letterboxd/IMDb CSV and JSON.

Each parser normalizes rows into a common :class:`ImportItem`. A
:class:`Matcher` then resolves items to TMDB ids so they can be written
back into the local database.
"""

from __future__ import annotations

import csv
import json
import os
import re
import zipfile
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
    tmdb_id: int | None = None
    show_tmdb_id: int | None = None
    season_number: int | None = None
    episode_number: int | None = None


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
# Trakt native JSON export
# ----------------------------------------------------------------------

_HISTORY_FILE_RE = re.compile(r"^watched-history-\d+\.json$")

_KNOWN_TRAKT_FILES = {
    "watched-history-": "watched",
    "watched-movies.json": "watched",
    "watched-shows.json": "watched",
    "ratings-movies.json": "ratings",
    "ratings-shows.json": "ratings",
    "ratings-seasons.json": "ratings",
    "lists-watchlist.json": "watchlist",
}


def _trakt_json_files(path: str):
    """Yield (basename, parsed JSON) for each .json inside an export.

    `path` may be a .zip archive or an extracted directory.
    """
    if os.path.isdir(path):
        try:
            names = sorted(
                name for name in os.listdir(path)
                if os.path.isfile(os.path.join(path, name))
                and name.endswith(".json")
            )
        except OSError as exc:
            raise ImportParseError(f"Cannot read directory: {exc}") from exc
        for name in names:
            try:
                with open(os.path.join(path, name), "r", encoding="utf-8") as f:
                    yield name, json.load(f)
            except OSError as exc:
                raise ImportParseError(f"Cannot read file: {exc}") from exc
            except json.JSONDecodeError as exc:
                raise ImportParseError(f"{name} is not valid JSON: {exc}") from exc
        return

    entries: list[tuple[str, object]] = []
    try:
        with zipfile.ZipFile(path) as archive:
            for info in archive.infolist():
                name = os.path.basename(info.filename)
                if info.is_dir() or not name.endswith(".json"):
                    continue
                try:
                    with archive.open(info) as f:
                        data = json.load(f)
                except json.JSONDecodeError as exc:
                    raise ImportParseError(
                        f"{name} is not valid JSON: {exc}"
                    ) from exc
                entries.append((name, data))
    except OSError as exc:
        raise ImportParseError(f"Cannot read archive: {exc}") from exc
    except zipfile.BadZipFile as exc:
        raise ImportParseError(f"Not a valid zip archive: {exc}") from exc
    yield from entries


def _trakt_media(data: dict) -> dict:
    """Extract the movie/show object from a Trakt list entry."""
    for key in ("movie", "show"):
        value = data.get(key)
        if isinstance(value, dict):
            return value
    return {}


def _trakt_ids(media: dict) -> dict:
    return media.get("ids") if isinstance(media.get("ids"), dict) else {}


class TraktExport:
    """Parse Trakt's native JSON export (a .zip or extracted directory).

    Prefers the per-episode `watched-history-*.json` files for the watched
    target, and reads ratings/watchlist from their own files. Every entry
    carries TMDB ids directly, so no matching lookup is ever needed.
    """

    @classmethod
    def parse(cls, path: str) -> list[ImportItem]:
        items: list[ImportItem] = []
        file_watched: list[ImportItem] = []
        seen_known = False
        seen_history = False
        season_ratings: list[dict] = []
        explicit_show_ratings: set[int] = set()

        for name, data in _trakt_json_files(path):
            if not isinstance(data, list):
                continue
            if _HISTORY_FILE_RE.match(name):
                seen_known = True
                seen_history = True
                for entry in data:
                    if isinstance(entry, dict):
                        items.append(cls._history_entry(entry))
            elif name == "watched-movies.json":
                seen_known = True
                for entry in data:
                    if isinstance(entry, dict):
                        item = cls._media_item(entry, "movie", "watched")
                        if item:
                            file_watched.append(item)
            elif name == "watched-shows.json":
                seen_known = True
                for entry in data:
                    if isinstance(entry, dict):
                        item = cls._media_item(entry, "show", "watched")
                        if item:
                            file_watched.append(item)
            elif name == "ratings-movies.json":
                seen_known = True
                for entry in data:
                    if isinstance(entry, dict):
                        item = cls._media_item(entry, "movie", "ratings")
                        if item:
                            items.append(item)
            elif name == "ratings-shows.json":
                seen_known = True
                for entry in data:
                    if not isinstance(entry, dict):
                        continue
                    item = cls._media_item(entry, "show", "ratings")
                    if item:
                        if item.tmdb_id:
                            explicit_show_ratings.add(item.tmdb_id)
                        items.append(item)
            elif name == "ratings-seasons.json":
                seen_known = True
                season_ratings.extend(
                    entry for entry in data if isinstance(entry, dict)
                )
            elif name == "lists-watchlist.json":
                seen_known = True
                for entry in data:
                    if isinstance(entry, dict):
                        item = cls._media_item(entry, None, "watchlist")
                        if item:
                            items.append(item)

        if not seen_known:
            raise ImportParseError(
                "This file does not look like a Trakt export "
                "(no watched-history, ratings or watchlist files found)"
            )

        # watched-history-* is the precise per-episode source of truth;
        # watched-movies.json / watched-shows.json only add value when the
        # history files are absent.
        if not seen_history:
            items.extend(file_watched)

        items.extend(cls._promote_season_ratings(
            season_ratings, explicit_show_ratings
        ))
        return items

    @staticmethod
    def _history_entry(entry: dict) -> ImportItem:
        entry_type = entry.get("type")
        watched_date = _parse_date(entry.get("watched_at") or None)
        if entry_type == "episode":
            episode = entry.get("episode") or {}
            show = entry.get("show") or {}
            ids = _trakt_ids(episode)
            show_ids = _trakt_ids(show)
            return ImportItem(
                title=_clean_title(show.get("title")),
                year=_parse_int(show.get("year")),
                imdb_id=show_ids.get("imdb"),
                media_type="episode",
                watched_date=watched_date,
                target="watched",
                source=_clean_title(episode.get("title")),
                tmdb_id=_parse_int(ids.get("tmdb")),
                show_tmdb_id=_parse_int(show_ids.get("tmdb")),
                season_number=_parse_int(episode.get("season")),
                episode_number=_parse_int(episode.get("number")),
            )
        media = entry.get("movie") or {}
        ids = _trakt_ids(media)
        return ImportItem(
            title=_clean_title(media.get("title")),
            year=_parse_int(media.get("year")),
            imdb_id=ids.get("imdb"),
            media_type="movie",
            watched_date=watched_date,
            target="watched",
            source=_clean_title(media.get("title")),
            tmdb_id=_parse_int(ids.get("tmdb")),
        )

    @classmethod
    def _media_item(
        cls, entry: dict, media_type: str | None, target: str
    ) -> ImportItem | None:
        media = _trakt_media(entry)
        if not media:
            return None
        ids = _trakt_ids(media)
        title = _clean_title(media.get("title"))
        if not title:
            return None
        rating = _parse_rating(entry.get("rating"))
        date_key = {
            "watched": "last_watched_at",
            "ratings": "rated_at",
            "watchlist": "listed_at",
        }.get(target, "listed_at")
        return ImportItem(
            title=title,
            year=_parse_int(media.get("year")),
            imdb_id=ids.get("imdb"),
            media_type=media_type or _normalize_media_type(
                media.get("media_type")
            ),
            rating=rating,
            watched_date=_parse_date(entry.get(date_key) or None),
            target=target,
            source=title,
            tmdb_id=_parse_int(ids.get("tmdb")),
        )

    @staticmethod
    def _promote_season_ratings(
        season_ratings: list[dict], explicit: set[int]
    ) -> list[ImportItem]:
        """Promote each show's most-recently-rated season to a show rating."""
        by_show: dict[int, dict] = {}
        for entry in season_ratings:
            media = _trakt_media(entry)
            show_tmdb = _parse_int(_trakt_ids(media).get("tmdb"))
            if not show_tmdb:
                continue
            rated_at = str(entry.get("rated_at") or "")
            current = by_show.get(show_tmdb)
            if current is None or rated_at > str(current.get("rated_at") or ""):
                by_show[show_tmdb] = entry
        items: list[ImportItem] = []
        for show_tmdb, entry in by_show.items():
            if show_tmdb in explicit:
                continue
            media = _trakt_media(entry)
            ids = _trakt_ids(media)
            title = _clean_title(media.get("title"))
            if not title:
                continue
            items.append(ImportItem(
                title=title,
                year=_parse_int(media.get("year")),
                imdb_id=ids.get("imdb"),
                media_type="show",
                rating=_parse_rating(entry.get("rating")),
                watched_date=_parse_date(entry.get("rated_at") or None),
                target="ratings",
                source=title,
                tmdb_id=show_tmdb,
            ))
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

        # Trakt exports carry TMDB ids directly; skip all lookups.
        if item.tmdb_id:
            resolved = {
                "tmdb_id": item.tmdb_id,
                "media_type": item.media_type or "movie",
                "title": item.title,
                "year": item.year,
            }

        if resolved is None:
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
    if path.lower().endswith(".zip") or os.path.isdir(path):
        return TraktExport
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