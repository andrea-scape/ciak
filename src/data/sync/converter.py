"""Convert between local DB rows and SyncItem lists for two-way sync."""

import logging
import time

from .base import SyncCategory, SyncItem

_log = logging.getLogger(__name__)


def local_to_sync_items(repo) -> list[SyncItem]:
    """Read all local user data and convert to SyncItems for push."""
    items: list[SyncItem] = []

    for row in repo.get_watchlist():
        extra = {}
        if row.get("is_anime"):
            extra["source_bucket"] = "anime"
        items.append(SyncItem(
            tmdb_id=row["tmdb_id"],
            media_type=row.get("media_type", "movie"),
            category=SyncCategory.WATCHLIST,
            added_at=row.get("added_at"),
            extra=extra,
        ))

    for row in repo.get_watched_list():
        media_type = row.get("media_type", "movie")
        extra = {}
        if row.get("is_anime"):
            extra["source_bucket"] = "anime"
        if media_type == "episode":
            extra["episode_tmdb_id"] = row["tmdb_id"]
            extra["season"] = row.get("season_number")
            extra["episode"] = row.get("episode_number")
            items.append(SyncItem(
                tmdb_id=row.get("show_tmdb_id", row["tmdb_id"]),
                media_type="show",
                category=SyncCategory.WATCHED,
                watched_at=row.get("watched_at"),
                extra=extra,
            ))
        else:
            items.append(SyncItem(
                tmdb_id=row["tmdb_id"],
                media_type=media_type,
                category=SyncCategory.WATCHED,
                watched_at=row.get("watched_at"),
                extra=extra,
            ))

    for row in repo.get_ratings():
        extra = {}
        if row.get("is_anime"):
            extra["source_bucket"] = "anime"
        items.append(SyncItem(
            tmdb_id=row["tmdb_id"],
            media_type=row.get("media_type", "movie"),
            category=SyncCategory.RATINGS,
            rating=row.get("rating"),
            added_at=row.get("rated_at"),
            extra=extra,
        ))

    for row in repo.get_collection():
        extra = {}
        if row.get("is_anime"):
            extra["source_bucket"] = "anime"
        items.append(SyncItem(
            tmdb_id=row["tmdb_id"],
            media_type=row.get("media_type", "movie"),
            category=SyncCategory.COLLECTION,
            added_at=row.get("collected_at"),
            extra=extra,
        ))

    return items


def import_remote_items(repo, items: list[SyncItem]) -> int:
    """Import remote SyncItems into the local database. Returns count imported."""
    watchlist_rows = []
    watched_rows = []
    rating_rows = []
    skipped = 0

    for item in items:
        try:
            is_anime = 1 if item.extra.get("source_bucket") == "anime" else 0

            if item.category == SyncCategory.WATCHLIST:
                watchlist_rows.append({
                    "tmdb_id": item.tmdb_id,
                    "media_type": item.media_type,
                    "added_at": item.added_at,
                    "title": item.title,
                    "year": item.year,
                    "is_anime": is_anime,
                })

            elif item.category == SyncCategory.WATCHED:
                show_tmdb_id = item.extra.get("show_tmdb_id")
                season = item.extra.get("season")
                episode = item.extra.get("episode")
                if item.media_type == "show" and season is not None and episode is not None:
                    watched_rows.append({
                        "tmdb_id": item.tmdb_id,
                        "media_type": "episode",
                        "show_tmdb_id": show_tmdb_id or item.tmdb_id,
                        "season_number": season,
                        "episode_number": episode,
                        "watched_at": item.watched_at or int(time.time()),
                        "title": item.title,
                        "year": item.year,
                        "is_anime": is_anime,
                    })
                else:
                    watched_rows.append({
                        "tmdb_id": item.tmdb_id,
                        "media_type": item.media_type,
                        "watched_at": item.watched_at or int(time.time()),
                        "title": item.title,
                        "year": item.year,
                        "is_anime": is_anime,
                    })

            elif item.category == SyncCategory.RATINGS and item.rating is not None:
                rating_rows.append({
                    "tmdb_id": item.tmdb_id,
                    "media_type": item.media_type,
                    "rating": item.rating,
                    "rated_at": item.added_at or int(time.time()),
                    "title": item.title,
                    "year": item.year,
                    "is_anime": is_anime,
                })

            elif item.category == SyncCategory.COLLECTION:
                repo.add_to_collection(item.tmdb_id, item.media_type, is_anime=is_anime)
        except Exception:
            _log.exception("Failed to process sync item tmdb_id=%s cat=%s",
                           item.tmdb_id, item.category)
            skipped += 1
            continue

    _log.debug("Import routing: %d watchlist, %d watched, %d ratings, %d skipped",
               len(watchlist_rows), len(watched_rows), len(rating_rows), skipped)

    count = 0
    if watchlist_rows:
        try:
            count += repo.import_watchlist(watchlist_rows)
        except Exception:
            _log.exception("Failed to import %d watchlist rows", len(watchlist_rows))
    if watched_rows:
        try:
            count += repo.import_watched(watched_rows)
        except Exception:
            _log.exception("Failed to import %d watched rows", len(watched_rows))
    if rating_rows:
        try:
            count += repo.import_ratings(rating_rows)
        except Exception:
            _log.exception("Failed to import %d rating rows", len(rating_rows))

    return count


def compute_push_deletions(repo, backend: str) -> dict:
    """Compare current local items with last-pushed state.

    Returns {"watchlist": [...], "ratings": [...]} of SyncItems to remove
    from the remote backend.
    """
    local_watchlist = {
        (r["tmdb_id"], r.get("media_type", "movie"))
        for r in repo.get_watchlist()
    }
    local_ratings = {
        (r["tmdb_id"], r.get("media_type", "movie"))
        for r in repo.get_ratings()
    }

    try:
        pushed_watchlist, pushed_ratings = repo.get_pushed_items(backend)
    except (AttributeError, TypeError, ValueError):
        pushed_watchlist, pushed_ratings = set(), set()

    removals = {
        "watchlist": [
            SyncItem(tmdb_id=tmdb_id, media_type=mt, category=SyncCategory.WATCHLIST)
            for tmdb_id, mt in sorted(pushed_watchlist - local_watchlist)
        ],
        "ratings": [
            SyncItem(tmdb_id=tmdb_id, media_type=mt, category=SyncCategory.RATINGS)
            for tmdb_id, mt in sorted(pushed_ratings - local_ratings)
        ],
    }
    return removals


def compute_pull_deletions(repo, remote_ids: dict) -> list[tuple[int, str]]:
    """Compare Simkl IDs-only response against local DB.

    Returns list of (tmdb_id, media_type) tuples to remove locally
    because they are no longer in Simkl's library.
    """
    remote_show_ids = set()
    remote_movie_ids = set()
    for item in remote_ids.get("shows", []):
        tmdb = item.get("ids", {}).get("tmdb")
        if tmdb is not None:
            remote_show_ids.add(tmdb)
    for item in remote_ids.get("movies", []):
        tmdb = item.get("ids", {}).get("tmdb")
        if tmdb is not None:
            remote_movie_ids.add(tmdb)
    for item in remote_ids.get("anime", []):
        tmdb = item.get("ids", {}).get("tmdb")
        if tmdb is not None:
            remote_show_ids.add(tmdb)

    removals = []
    seen = set()
    for row in repo.get_watchlist():
        tmdb_id = row["tmdb_id"]
        mt = row.get("media_type", "movie")
        remote_ids_set = remote_show_ids if mt == "show" else remote_movie_ids
        if tmdb_id not in remote_ids_set and (tmdb_id, mt) not in seen:
            removals.append((tmdb_id, mt))
            seen.add((tmdb_id, mt))
    for row in repo.get_ratings():
        tmdb_id = row["tmdb_id"]
        mt = row.get("media_type", "movie")
        remote_ids_set = remote_show_ids if mt == "show" else remote_movie_ids
        if tmdb_id not in remote_ids_set and (tmdb_id, mt) not in seen:
            removals.append((tmdb_id, mt))
            seen.add((tmdb_id, mt))
    return removals
