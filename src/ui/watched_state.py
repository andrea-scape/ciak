"""Shared watched-state helpers used by card grids."""

import datetime
import time

from ..domain.exceptions import NetworkError

# Memoized fully-watched results, keyed by (show_id[, data_version]).
# Entries expire after this many seconds; a repository data_version change
# (watch/unwatch/watchlist mutation) also invalidates them immediately.
_FULLY_WATCHED_TTL = 300
_fully_watched_memo: dict = {}


_VERDICT_MAX_AGE_S = 6 * 3600

_ENDED_STATUSES = {"ended", "canceled", "cancelled"}

# Bump to invalidate all cached watched_verdicts rows when the
# computation logic changes.  The fingerprint is "v{n}n{count}";
# changing _LOGIC_VERSION makes every old row miss automatically.
_LOGIC_VERSION = "v2"

# Default window (days) for hiding caught-up ongoing shows.
# Overridden by "hide-shows-upcoming-window" setting when enabled.
_HIDE_WINDOW_DAYS_DEFAULT = 14


# Memo for should_hide_show verdicts; the underlying pieces (caught-up
# verdict, media cache) are durable, so this only smooths repeated
# repopulates within a session.
_SHOW_HIDE_MEMO_TTL = 300
_show_hide_memo: dict = {}


def should_hide_show(user_repo, metadata_service, show_id, window_days=None) -> bool:
    """True when a caught-up show should hide from the watchlist: fully
    current AND (show ended/canceled OR nothing airing within the
    upcoming window). The expensive caught-up check is memoized and
    persisted (see is_show_caught_up); get_show comes from the media
    cache, so this is ~0ms once either has been fetched."""
    window = window_days or _HIDE_WINDOW_DAYS_DEFAULT
    key = (show_id, window)
    now = time.monotonic()
    hit = _show_hide_memo.get(key)
    if hit is not None and now < hit[0]:
        return hit[1]
    if not is_show_caught_up(user_repo, metadata_service, show_id):
        result = False
    else:
        try:
            result = _window_status_rule(metadata_service, show_id, window)
        except NetworkError:
            result = False
    _show_hide_memo[key] = (now + _SHOW_HIDE_MEMO_TTL, result)
    return result


def _window_status_rule(metadata_service, show_id, window_days):
    """The non-watched half of the caught-up hide rule: hide once the
    show has ended/canceled or its next episode airs beyond the window.
    Callers catch NetworkError."""
    show = metadata_service.get_show(show_id)
    status = (getattr(show, "status", None) or "").strip().lower()
    if status in _ENDED_STATUSES:
        return True
    next_air = getattr(show, "next_episode_air_date", None)
    if not next_air:
        return True
    try:
        air = datetime.date.fromisoformat(next_air)
        return air > datetime.date.today() + datetime.timedelta(days=window_days)
    except ValueError:
        return True


def should_hide_show_ids(user_repo, metadata_service, candidate_ids,
                         window_days=None, on_result=None):
    """Show ids that should be hidden from the watchlist, fanned out
    across the fetch pool so one slow show can't stall the rest."""
    return _fan_out_checks(
        user_repo, metadata_service, candidate_ids,
        (lambda user_repo, metadata_service, sid:
         should_hide_show(user_repo, metadata_service, sid, window_days)),
        on_result=on_result,
    )


def _verdict_max_age(user_repo, show_id):
    """Ended/canceled shows get a permanent verdict (no expiry); anything
    else revalidates after the normal window."""
    getter = getattr(user_repo, "get_show_status", None)
    if getter is None:
        return _VERDICT_MAX_AGE_S
    try:
        status = getter(show_id)
    except Exception:
        return _VERDICT_MAX_AGE_S
    if status and str(status).strip().lower() in _ENDED_STATUSES:
        return None
    return _VERDICT_MAX_AGE_S


def _fingerprint(user_repo, show_id):
    """Cheap local inputs: the watched-episode count. Any watch/unwatch
    for this show changes it, self-invalidating the stored verdict."""
    return "{}n{}".format(_LOGIC_VERSION,
                          len(user_repo.get_watched_episodes_for_show(show_id)))


def _persisted_verdict(user_repo, kind, show_id):
    getter = getattr(user_repo, "get_watched_verdict", None)
    if getter is None:
        return None
    try:
        fp = _fingerprint(user_repo, show_id)
        return getter(show_id, kind, fp,
                      max_age_s=_verdict_max_age(user_repo, show_id))
    except Exception:
        return None


def _store_persisted_verdict(user_repo, kind, show_id, result):
    storer = getattr(user_repo, "store_watched_verdict", None)
    if storer is None:
        return
    try:
        storer(show_id, kind, result,
               _fingerprint(user_repo, show_id))
    except Exception:
        pass


def is_show_fully_watched(user_repo, metadata_service, show_id):
    version = getattr(user_repo, "data_version", None)
    key = show_id if version is None else (show_id, version)
    hit = _fully_watched_memo.get(key)
    now = time.monotonic()
    if hit is not None and now < hit[0]:
        return hit[1]
    persisted = _persisted_verdict(user_repo, "fully_watched", show_id)
    if persisted is not None:
        if version is not None:
            _fully_watched_memo[key] = (now + _FULLY_WATCHED_TTL, persisted)
        return persisted
    try:
        result = _compute_is_show_fully_watched(user_repo, metadata_service, show_id)
    except NetworkError:
        # Transient failure: answer "no" for this pass but never cache it,
        # so a later load retries instead of being stuck for the TTL.
        return False
    if version is not None:
        _fully_watched_memo[key] = (now + _FULLY_WATCHED_TTL, result)
        _store_persisted_verdict(user_repo, "fully_watched", show_id, result)
    return result


def _compute_is_show_fully_watched(user_repo, metadata_service, show_id):
    """True if every aired episode is watched AND no future episodes are
    scheduled AND the show status indicates it has ended or been canceled.
    A returning series you're caught up on is *not* fully watched — it
    stays in the watchlist while new episodes keep coming."""
    watched = user_repo.get_watched_episodes_for_show(show_id)
    if not watched:
        return False

    # Network failures propagate so the memoized wrapper can tell them
    # apart from a genuine "no" and avoid caching a transient miss.
    seasons = metadata_service.get_show_seasons(show_id)

    today = datetime.date.today()
    has_future = False
    for season in seasons:
        if season.season_number <= 0:
            continue
        episodes = metadata_service.get_season_episodes(
            show_id, season.season_number
        )

        for ep in episodes:
            if ep.air_date:
                try:
                    air = datetime.date.fromisoformat(ep.air_date)
                except ValueError:
                    continue
                if air > today:
                    has_future = True
                    continue
            else:
                # TMDB often hands back episodes with no air date yet
                # (announced / unscheduled). They haven't aired, so they
                # can't be required for a watched check.
                continue
            key = (ep.season_number, ep.episode_number)
            if key not in watched:
                return False

    if has_future:
        return False

    show = metadata_service.get_show(show_id)
    # Ongoing shows are never "fully watched" — keep in watchlist
    status = getattr(show, "status", None)
    if status is None or status.strip().lower() not in _ENDED_STATUSES:
        return False
    if getattr(show, "next_episode_air_date", None):
        return False

    return True


def _fan_out_checks(user_repo, metadata_service, candidate_ids, check_fn,
                    on_result=None):
    """Run check_fn(user_repo, metadata_service, show_id) for every
    candidate in the shared fetch pool and return the ids that passed.

    on_result, when given, is invoked as (show_id, passed) from a worker
    thread as soon as each future resolves — callers must marshal UI work
    back to the main thread themselves."""
    from concurrent.futures import as_completed

    import os

    from ..threads import submit as _submit_worker

    if candidate_ids is None:
        candidate_ids = user_repo.get_watched_show_ids()
    else:
        # Callers derive candidates from locally watched data already;
        # intersecting again silently dropped ids on keying mismatches.
        candidate_ids = set(candidate_ids)
    if not candidate_ids:
        return set()

    def _check(show_id):
        try:
            return show_id if check_fn(
                user_repo, metadata_service, show_id
            ) else None
        except Exception as exc:
            if os.environ.get("CIK_DEBUG"):
                print(f"[watched] check failed for {show_id}: {exc!r}")
            return None

    futures = {_submit_worker(_check, sid): sid for sid in candidate_ids}
    found = set()
    rejected = []
    for fut in as_completed(futures):
        sid = futures[fut]
        try:
            res = fut.result()
        except Exception:
            res = None
        passed = res is not None
        if passed:
            found.add(res)
        else:
            rejected.append(sid)
        if on_result is not None:
            try:
                on_result(sid, passed)
            except Exception:
                pass
    if os.environ.get("CIK_DEBUG"):
        print(f"[watched] checked={len(candidate_ids)} passed={len(found)} "
              f"failed={len(rejected)}")
    return found


def fully_watched_show_ids(user_repo, metadata_service, candidate_ids=None,
                           on_result=None):
    """Return show ids where every aired episode is watched AND no future
    episodes are scheduled. See _fan_out_checks for on_result semantics."""
    return _fan_out_checks(
        user_repo, metadata_service, candidate_ids,
        is_show_fully_watched, on_result=on_result,
    )


# Caught-up memo: green check = "seen everything that aired", even for
# series with future episodes still scheduled.
_caught_up_memo: dict = {}


def is_show_caught_up(user_repo, metadata_service, show_id):
    version = getattr(user_repo, "data_version", None)
    key = show_id if version is None else (show_id, version)
    hit = _caught_up_memo.get(key)
    now = time.monotonic()
    if hit is not None and now < hit[0]:
        return hit[1]
    persisted = _persisted_verdict(user_repo, "caught_up", show_id)
    if persisted is not None:
        if version is not None:
            _caught_up_memo[key] = (now + 300, persisted)
        return persisted
    try:
        result = _compute_is_show_caught_up(user_repo, metadata_service, show_id)
    except NetworkError:
        # Transient failure: answer "no" for this pass but never cache it,
        # so a later load retries instead of being stuck for the TTL.
        return False
    if version is not None:
        _caught_up_memo[key] = (now + 300, result)
        _store_persisted_verdict(user_repo, "caught_up", show_id, result)
    return result


def _compute_is_show_caught_up(user_repo, metadata_service, show_id):
    """True if every AIRED episode is watched. Future episodes and ongoing
    status are ignored — a returning series you're current on counts."""
    watched = user_repo.get_watched_episodes_for_show(show_id)
    if not watched:
        return False

    # Network failures propagate so the memoized wrapper can tell them
    # apart from a genuine "no" and avoid caching a transient miss.
    seasons = metadata_service.get_show_seasons(show_id)

    today = datetime.date.today()
    for season in seasons:
        if season.season_number <= 0:
            continue
        episodes = metadata_service.get_season_episodes(
            show_id, season.season_number
        )

        for ep in episodes:
            aired = False
            if ep.air_date:
                try:
                    aired = datetime.date.fromisoformat(ep.air_date) <= today
                except ValueError:
                    aired = False
            if not aired:
                # Undated episodes haven't aired yet — TMDB hands them
                # back for announced seasons all the time.
                continue
            key = (ep.season_number, ep.episode_number)
            if key not in watched:
                return False

    return True


def caught_up_show_ids(user_repo, metadata_service, candidate_ids=None,
                       on_result=None):
    """Return show ids where every AIRED episode is watched (ongoing
    series count once you're current). See _fan_out_checks for the
    incremental on_result callback."""
    return _fan_out_checks(
        user_repo, metadata_service, candidate_ids,
        is_show_caught_up, on_result=on_result,
    )
