"""Airing notification checks.

Runs while the app is open: once deferred at startup, then every 30
minutes. Compares watchlist airings and movie release dates against a
horizon so every episode/release notifies at most once (dedup table),
catching up on what aired while the app was closed within a bounded
window so first-enable never floods. A standalone daemon that notifies
while the app is closed is planned separately.

Scope semantics ('notification-scope' GSettings key):
  'all'      -> whole watchlist EXCEPT muted media (notify_selection)
  'selected' -> ONLY picked media (notify_selection) ∩ watchlist
"""

import datetime
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import GLib, Gio

from .. import threads
from . import datefmt

CHECK_INTERVAL_S = 1800          # 30 min between checks
CATCHUP_WINDOW_S = 48 * 3600     # never look back further than this


def _parse_date(iso):
    try:
        return datetime.date.fromisoformat(str(iso)[:10])
    except (TypeError, ValueError):
        return None


def find_due_airings(
    user_repo,
    metadata_service,
    settings,
    today=None,
    now=None,
):
    """Pure check logic: returns a list of due-notification dicts.

    Each dict: {key, title, body}. Network errors propagate to the
    caller (the scheduled check treats them as transient).
    """
    if not settings.get_boolean("airing-notifications"):
        return []

    today = today or datetime.date.today()
    now = int(now if now is not None else time.time())
    last_check = settings.get_int64("notifications-last-check")
    horizon_ts = max(last_check, now - CATCHUP_WINDOW_S)
    horizon_date = datetime.date.fromtimestamp(horizon_ts)

    scope = settings.get_string("notification-scope")
    overrides = user_repo.get_notification_overrides()

    def _included(tmdb_id, media_type):
        pair = (tmdb_id, media_type)
        if scope == "selected":
            return pair in overrides
        return pair not in overrides

    def _in_window(d):
        return d is not None and horizon_date <= d <= today

    due = []

    # Movies: release date inside the horizon window.
    for m in user_repo.get_watchlist_with_dates("movie"):
        mid = m["tmdb_id"]
        if not _included(mid, "movie"):
            continue
        release = _parse_date(m.get("release_date"))
        if not _in_window(release):
            continue
        key = f"movie:{mid}"
        if user_repo.has_notified(key):
            continue
        if release == today:
            body = "Released today"
        else:
            body = f"Released {datefmt.format_iso(release.isoformat())}"
        due.append({"key": key, "title": m.get("title") or "Movie",
                    "body": body})

    # Shows: episodes of the latest season airing inside the window,
    # skipping already-watched ones.
    watched_cache = {}
    for s in user_repo.get_watchlist("show"):
        sid = s["tmdb_id"]
        if not _included(sid, "show"):
            continue
        try:
            episodes = metadata_service.get_latest_season_episodes(sid)
        except Exception:
            raise
        if not episodes:
            continue
        if sid not in watched_cache:
            watched_cache[sid] = user_repo.get_watched_episodes_for_show(sid)
        watched = watched_cache[sid]
        for ep in episodes:
            air = _parse_date(ep.air_date)
            if not _in_window(air):
                continue
            if (ep.season_number, ep.episode_number) in watched:
                continue
            key = f"show:{sid}:{ep.season_number}:{ep.episode_number}"
            if user_repo.has_notified(key):
                continue
            code = f"S{ep.season_number:02d}E{ep.episode_number:02d}"
            name = getattr(ep, "title", None)
            if air == today:
                body = f"{code} aired today"
            elif name:
                body = (
                    f"{code} · {name} "
                    f"({datefmt.format_iso(air.isoformat())})"
                )
            else:
                body = f"{code} · {datefmt.format_iso(air.isoformat())}"
            due.append({"key": key, "title": s.get("title") or "Show",
                        "body": body})

    return due


class AiringNotifier:
    """Schedules periodic checks and posts desktop notifications."""

    def __init__(self, win, user_repo, metadata_service):
        self.win = win
        self.user_repo = user_repo
        self.metadata_service = metadata_service
        self._timeout_id = 0

    # ------------------------------------------------------------------
    # Scheduling
    # ------------------------------------------------------------------

    def start(self):
        self.stop()
        self._timeout_id = GLib.timeout_add_seconds(
            CHECK_INTERVAL_S, self._on_timer
        )
        # First check runs shortly after startup, once the window is up.
        GLib.timeout_add_seconds(20, self._on_timer)
        return self

    def stop(self):
        if self._timeout_id:
            GLib.source_remove(self._timeout_id)
            self._timeout_id = 0

    def _on_timer(self, *_args):
        threads.submit(self._check_thread)
        return GLib.SOURCE_CONTINUE

    def trigger(self):
        """Run an out-of-band check (e.g. right after enabling)."""
        threads.submit(self._check_thread)

    # ------------------------------------------------------------------
    # Check execution
    # ------------------------------------------------------------------

    def _check_thread(self):
        settings = self._settings()
        if settings is None or not settings.get_boolean(
            "airing-notifications"
        ):
            return
        try:
            due = find_due_airings(
                self.user_repo, self.metadata_service, settings
            )
        except Exception:
            # Transient (network) failure: retry on the next tick without
            # advancing last-check so nothing is skipped silently.
            return
        GLib.idle_add(self._deliver, due, settings)

    def _deliver(self, due, settings):
        app = self.win.get_application()
        if app is not None:
            for item in due:
                self.user_repo.mark_notified(item["key"])
                app.send_notification(None, self._make_notification(item))
        settings.set_int64(
            "notifications-last-check", int(time.time())
        )
        return False

    def _make_notification(self, item):
        notif = Gio.Notification.new(item["title"])
        notif.set_body(item["body"])
        target = GLib.Variant("(ss)", self._target_of(item["key"]))
        notif.set_default_action_and_target("app.open-detail", target)
        notif.set_priority(Gio.NotificationPriority.NORMAL)
        return notif

    @staticmethod
    def _target_of(key):
        parts = key.split(":")
        if parts[0] == "movie":
            return ["movie", parts[1]]
        return ["show", parts[1]]

    def _settings(self):
        getter = getattr(self.win, "settings", None)
        return getter if getter is not None else None
