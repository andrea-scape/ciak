"""Verification of remote SyncItems against TMDB before local import.

Remote backends like Simkl sometimes return entries with wrong TMDB IDs
or corrupt titles (anime cross-mapping errors).  Items are imported only
when they can be verified against TMDB's authoritative record; items that
fail verification are repaired via a strict title search or skipped and
reported so nothing is silently lost and no junk is silently added.
"""

import logging
import re
import unicodedata

_log = logging.getLogger(__name__)


def _normalize_title(value: str | None) -> str:
    """Lowercase and strip diacritics only — preserve punctuation, spaces, and leading 'the'."""
    if not value:
        return ""
    text = unicodedata.normalize("NFKD", value)
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).casefold()
    return text


def titles_match(a: str | None, b: str | None) -> bool:
    """Compare two titles for exact match after diacritics/case normalization.

    Returns True only when the normalized titles are identical.
    Visually different titles (different punctuation, leading 'the', etc.)
    will return False so they are flagged for user review.
    """
    ca = _normalize_title(a)
    cb = _normalize_title(b)
    if not ca or not cb:
        return False
    return ca == cb


def strict_search(service, title: str, year: int | None, media_type: str):
    """Search TMDB and return a hit only when title AND year clearly match.

    Guards against search_best's "first result" fallback picking an
    unrelated show.  When a match is found it is cached so its poster is
    immediately available to the importer.
    """
    if not title:
        return None
    try:
        result = service.search_best(title, year, media_type)
    except Exception:
        _log.debug("Title search failed for %r (year=%s)", title, year)
        return None
    if result is None:
        return None
    if not titles_match(getattr(result, "title", ""), title):
        return None
    if year and getattr(result, "year", None) and result.year != year:
        return None
    cache = getattr(service, "_cache", None)
    if cache is not None and hasattr(cache, "put_media"):
        try:
            cache.put_media(result)
        except Exception:
            pass
    return result


def verify_items(service, items: list) -> tuple[list, list[dict]]:
    """Verify each SyncItem against TMDB before import.

    Returns (kept_items, warnings) where kept_items are safe to import
    (ID lookup matched, or the mapping was repaired through a confident
    title+year search) and warnings are structured mapping decisions the
    user must resolve: each is a dict with the original SyncItem, the
    claimed TMDB ID, the Simkl title, and what TMDB says about that ID
    (or None when the ID is dead / there is no ID to look up).
    """
    if service is None:
        return items, []
    kept: list = []
    warnings: list[dict] = []
    for item in items:
        label = item.title or f"{item.media_type} #{item.tmdb_id}"
        skip_fallback = False
        try:
            if item.media_type == "show":
                tm = service.get_show(item.tmdb_id, refresh=True)
            else:
                tm = service.get_movie(item.tmdb_id, refresh=True)
        except Exception:
            tm = None

        # ── Anime cross-type ID check ──────────────────────────
        # Simkl's anime bucket mixes movies and shows.  When the
        # primary lookup fails, try the other type by ID.
        is_anime = item.extra.get("source_bucket") == "anime"
        tmdb_title = getattr(tm, "title", None)
        _log.info("verify_item: %r tmdb=%s tmdb_title=%r is_anime=%s",
                  label, item.tmdb_id, tmdb_title, is_anime)
        if is_anime and tm is None:
            try:
                other_type = ("movie" if item.media_type == "show"
                              else "show")
                if other_type == "movie":
                    tm_other = service.get_movie(
                        item.tmdb_id, refresh=True)
                else:
                    tm_other = service.get_show(
                        item.tmdb_id, refresh=True)
            except Exception:
                tm_other = None
            if (tm_other is not None
                    and titles_match(getattr(tm_other, "title", ""),
                                     item.title)):
                _log.info(
                    "Anime cross-type ID repair %r: %s is a %s (%s)",
                    label, item.tmdb_id, other_type,
                    getattr(tm_other, "title", ""),
                )
                item.media_type = other_type
                item.title = getattr(tm_other, "title", item.title)
                item.year = (getattr(tm_other, "year", item.year)
                             or item.year)
                kept.append(item)
                continue

        # Direct ID lookup succeeded and title matches → trusted.
        if tm is not None and titles_match(getattr(tm, "title", ""),
                                           item.title):
            _log.info("verify PASS (title match): %r tmdb=%s -> kept",
                      label, item.tmdb_id)
            kept.append(item)
            continue

        # ── Anime cross-type title search ──────────────────────
        # When both ID lookups fail, search both types by title+year.
        # Only one type matches → repair.  Both match → user decides.
        if is_anime and item.title and item.year:
            movie_hit = strict_search(
                service, item.title, item.year, "movie")
            show_hit = strict_search(
                service, item.title, item.year, "show")
            if movie_hit and not show_hit:
                _log.info(
                    "Anime cross-type title repair %r: movie %s (%s)",
                    label, movie_hit.tmdb_id, movie_hit.title,
                )
                item.media_type = "movie"
                item.tmdb_id = movie_hit.tmdb_id
                item.title = movie_hit.title
                item.year = movie_hit.year or item.year
                kept.append(item)
                continue
            if show_hit and not movie_hit:
                _log.info(
                    "Anime cross-type title repair %r: show %s (%s)",
                    label, show_hit.tmdb_id, show_hit.title,
                )
                item.media_type = "show"
                item.tmdb_id = show_hit.tmdb_id
                item.title = show_hit.title
                item.year = show_hit.year or item.year
                kept.append(item)
                continue
            if movie_hit and show_hit:
                _log.info(
                    "Anime cross-type ambiguous %r: both movie %s "
                    "and show %s match",
                    label, movie_hit.tmdb_id, show_hit.tmdb_id,
                )
                # Fall through to warnings — user decides.
                skip_fallback = True

        # ID resolves but the recorded title differs (or ID is dead).
        # Only auto-repair when the ID is dead (tm is None) and we have a
        # source year to disambiguate; otherwise the Simkl title itself
        # may be wrong (e.g. "Dr. Wonder's Workshop" for Vampire Hunter D)
        # and the repair would silently import the wrong movie.
        # Anime items that were ambiguous in cross-type search skip this.
        repaired = None
        if not skip_fallback and tm is None and item.year and item.title:
            repaired = strict_search(
                service, item.title, item.year, item.media_type)
        if repaired is not None:
            _log.info(
                "Repaired %r: tmdb %s -> %s (%s)",
                label, item.tmdb_id, repaired.tmdb_id, repaired.title,
            )
            item.tmdb_id = repaired.tmdb_id
            kept.append(item)
            continue

        # Title mismatch or dead ID + no year → user decides.
        _log.info("verify FAIL: %r tmdb=%s simkl_title=%r tmdb_title=%r -> wizard",
                  label, item.tmdb_id, item.title, getattr(tm, "title", None))

        # Never silently drop an unverifiable item: make the user decide
        # how it should be mapped.  tmdb_title is None when the claimed ID
        # does not exist on TMDB (dead) or there was no ID at all.
        warnings.append({
            "item": item,
            "claimed_tmdb_id": item.tmdb_id,
            "simkl_title": item.title,
            "tmdb_title": getattr(tm, "title", None),
            "media_type": item.media_type,
            "year": item.year,
        })
        _log.warning(
            "Unverifiable mapping, queued for user decision: %r "
            "(claimed tmdb=%s, tmdb says %r)",
            label, item.tmdb_id,
            getattr(tm, "title", None) or "<no such entry>",
        )
    if warnings:
        _log.info("Verification: %d kept, %d need mapping",
                  len(kept), len(warnings))
    return kept, warnings