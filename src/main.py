import os
import sqlite3
import sys
import time
import threading
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gtk, Adw, Gio, Gdk, GLib, Pango

from . import config
from . import poster_cache
from .theme import apply_theme
from .icon_theme import _icon_theme_available
from .domain.onboarding import needs_onboarding
from .data.local.repository import LocalMediaRepository
from .data.local.cache import MetadataCache
from .data.tmdb.client import TmdbClient
from .data.tmdb.service import TmdbMetadataService
from .data.sync.engine import SyncEngine
from .ui.main_page import MainPage
from .ui.poster import load_poster
from .window import MainWindow


def _load_css():
    css_path = os.path.join(os.path.dirname(__file__), "style.css")
    if os.path.exists(css_path):
        provider = Gtk.CssProvider()
        provider.load_from_path(css_path)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )
    else:
        print(f"warning: stylesheet not found at {css_path}")


def _ensure_icon_theme():
    settings = Gtk.Settings.get_default()
    if settings is None:
        return
    theme = settings.get_property("gtk-icon-theme-name") or ""
    if not theme:
        return
    icon_theme = Gtk.IconTheme.new()
    icon_theme.set_theme_name(theme)
    if not _icon_theme_available(theme, icon_theme.get_search_path()):
        settings.set_property("gtk-icon-theme-name", "Adwaita")


# Credential name -> GSettings key for per-backend sync
_CREDENTIAL_MAP = {
    "tmdb": ("tmdb", "session_id", "sync-tmdb-enabled"),
    "simkl": ("simkl", "access_token", "sync-simkl-enabled"),
    "letterboxd": ("letterboxd", "session_cookie", "sync-letterboxd-enabled"),
}


def _auto_enable_backends(settings, load_credential):
    """If keyring has credentials but GSettings is off, auto-enable."""
    for name, (service, key, gkey) in _CREDENTIAL_MAP.items():
        if load_credential(service, key) and not settings.get_boolean(gkey):
            settings.set_boolean(gkey, True)


class CiakApp(Adw.Application):
    def __init__(self):
        super().__init__(
            application_id=config.APP_ID,
            flags=Gio.ApplicationFlags.DEFAULT_FLAGS,
        )
        self.win = None
        self._user_repo = None
        self._metadata_service = None
        self._onboarding_win = None

    def do_activate(self):
        _load_css()
        _ensure_icon_theme()
        settings = Gio.Settings.new(config.APP_ID)
        apply_theme(settings)

        if self.win:
            self.win.present()
            return

        if needs_onboarding(settings.get_boolean("onboarding-completed")):
            if self._onboarding_win:
                self._onboarding_win.present()
                return
            self._show_onboarding(settings)
            return

        self._build_main_window()

    def _show_onboarding(self, settings):
        from .ui.onboarding import OnboardingWindow

        tmdb_client = TmdbClient(
            settings.get_string("tmdb-api-key"),
            hide_adult_fn=lambda: settings.get_boolean("hide-adult-content"),
        )
        self._onboarding_win = OnboardingWindow(
            settings,
            tmdb_client,
            on_finish=self._build_main_window,
            application=self,
        )
        self._onboarding_win.present()

    def _build_main_window(self):
        settings = Gio.Settings.new(config.APP_ID)

        self._user_repo = LocalMediaRepository(config.get_db_path())
        self._user_repo.initialize()

        api_key = settings.get_string("tmdb-api-key")
        tmdb_client = TmdbClient(
            api_key,
            hide_adult_fn=lambda: settings.get_boolean("hide-adult-content"),
        )
        try:
            cache = MetadataCache(
                config.get_db_path(),
                ttl_seconds=settings.get_int("metadata-cache-ttl"),
            )
        except sqlite3.DatabaseError:
            import logging as _log_mod
            _log_mod.getLogger(__name__).warning(
                "Corrupt DB at %s — recreating", config.get_db_path())
            self._user_repo.close()
            try:
                os.remove(config.get_db_path())
            except OSError:
                pass
            for suffix in ("-wal", "-shm"):
                try:
                    os.remove(config.get_db_path() + suffix)
                except OSError:
                    pass
            self._user_repo = LocalMediaRepository(config.get_db_path())
            self._user_repo.initialize()
            cache = MetadataCache(
                config.get_db_path(),
                ttl_seconds=settings.get_int("metadata-cache-ttl"),
            )
        self._metadata_service = TmdbMetadataService(tmdb_client, cache)

        self.win = MainWindow(
            application=self,
            user_repo=self._user_repo,
            metadata_service=self._metadata_service,
        )
        self.win.set_page(MainPage(
            self.win,
            user_repo=self._user_repo,
            metadata_service=self._metadata_service,
        ))
        self.win.present()

        from .ui.notifications import AiringNotifier

        self._notifier = AiringNotifier(
            self.win, self._user_repo, self._metadata_service
        ).start()

        self._init_sync_engine(settings)

        onboarding = self._onboarding_win
        self._onboarding_win = None
        if onboarding is not None:
            onboarding.close()

    def relaunch_onboarding(self):
        notifier = getattr(self, "_notifier", None)
        if notifier is not None:
            notifier.stop()
            self._notifier = None
        engine = getattr(self, "_sync_engine", None)
        if engine is not None:
            engine.stop()
            self._sync_engine = None
        if self.win is not None:
            self.win.close()
            self.win = None
        if self._user_repo is not None:
            self._user_repo.close()
            self._user_repo = None
        if self._metadata_service is not None:
            self._metadata_service.close()
            self._metadata_service = None
        self._show_onboarding(Gio.Settings.new(config.APP_ID))

    def delete_local_database(self):
        """Close all open handles, erase the SQLite database and poster
        cache, then rebuild the main window from scratch."""
        notifier = getattr(self, "_notifier", None)
        if notifier is not None:
            notifier.stop()
            self._notifier = None
        engine = getattr(self, "_sync_engine", None)
        if engine is not None:
            engine.stop()
            self._sync_engine = None
        if self.win is not None:
            self.win.close()
            self.win = None
        if self._user_repo is not None:
            self._user_repo.close()
            self._user_repo = None
        if self._metadata_service is not None:
            self._metadata_service.close()
            self._metadata_service = None
        try:
            os.remove(config.get_db_path())
        except OSError:
            pass
        poster_cache.clear()
        self._build_main_window()

    def _init_sync_engine(self, settings):
        from .data.sync.tmdb_backend import TmdbSyncBackend
        from .data.sync.simkl_backend import SimklSyncBackend
        from .data.sync.letterboxd_backend import LetterboxdSyncBackend
        from .data.sync.credentials import load_credential

        api_key = settings.get_string("tmdb-api-key") or config.DEFAULT_TMDB_API_KEY
        simkl_client_id = (
            load_credential("simkl", "client_id")
            or config.DEFAULT_SIMKL_CLIENT_ID
        )
        backends = [
            TmdbSyncBackend(api_key),
            SimklSyncBackend(simkl_client_id),
            LetterboxdSyncBackend(),
        ]

        _auto_enable_backends(settings, load_credential)

        def _on_sync_done(status):
            if not self.win:
                return
            self._update_sync_progress(status)
            page = getattr(self.win, '_page', None)
            if page is None or not hasattr(page, 'update_sidebar_sync'):
                return
            page.update_sidebar_sync(
                status.state, status.results,
                backfill_current=getattr(status, 'backfill_current', 0),
                backfill_total=getattr(status, 'backfill_total', 0),
                status_text=getattr(status, 'status_text', ''),
            )
            page.update_connected_services()

            # Update preferences page last sync label if open
            prefs = getattr(self.win, '_page', None)
            if prefs is not None:
                prefs = getattr(prefs, '_prefs_dialog', None)
            if prefs is not None and hasattr(prefs, '_update_sync_last_label'):
                prefs._update_sync_last_label()

            from gi.repository import Adw

            if status.state == "error":
                errors = []
                for name, res in status.results.items():
                    if res.errors:
                        errors.append(f"{name}: {res.errors[0]}")
                msg = "; ".join(errors) if errors else "Sync failed"
                toast = Adw.Toast.new(msg)
                toast.set_timeout(5)
                self.win._toast_overlay.add_toast(toast)

            elif status.state == "done":
                # Mapping decisions (startup/manual only) — never spam
                # automatic background syncs.
                ambiguous = getattr(status, 'ambiguous', []) or []
                if ambiguous and getattr(status, 'show_report', False):
                    page = getattr(self.win, '_page', None)
                    conflict_open = (page is not None
                                     and getattr(page, '_sync_dialog_open', False))
                    if not conflict_open:
                        self._maybe_show_mapping_wizard(ambiguous)
                # Deletion warning toast (shown alongside sidebar checkmark)
                if getattr(status, 'remote_removed', None):
                    count = len(status.remote_removed)
                    backends = {b for _, b in status.remote_removed}
                    names = ", ".join(sorted(backends))
                    toast = Adw.Toast.new(
                        f"Removed {count} item{'s' if count > 1 else ''} "
                        f"from {names} (history and ratings also cleared)"
                    )
                    toast.set_timeout(8)
                    self.win._toast_overlay.add_toast(toast)
                # Reload pages only when the sync actually changed local
                # data (new/pushed items, removals, or a poster backfill).
                # A zero-change background sync must not tear down and
                # rebuild every page a few seconds after launch.
                changed = (
                    status.pulled_added or status.pushed_added
                    or status.pushed_removed or status.pulled_removed
                    or status.remote_removed
                    or (status.backfill_total or 0) > 0
                )
                main = getattr(self.win, '_page', None)
                if changed and main is not None and hasattr(main, 'invalidate_page'):
                    for pid in ("watchlist", "history", "diary",
                                "calendar", "profile"):
                        main.invalidate_page(pid, reload_now=True)

            elif status.state == "cancelled":
                toast = Adw.Toast.new("Sync cancelled — changes rolled back")
                toast.set_timeout(5)
                self.win._toast_overlay.add_toast(toast)

        self._sync_engine = SyncEngine(
            settings, backends, repo=self._user_repo,
            metadata_service=self._metadata_service,
            on_done=_on_sync_done,
            on_deletions=self._on_sync_deletions,
        )
        self._sync_engine.start_auto_sync()

    def _on_sync_deletions(self, deletions: list):
        """Confirm dialog surfaced by the engine — on every sync trigger.

        The app asks a lightweight confirm: "Remove from Simkl too?" Where
        confirmed, removals are applied on the follow-up sync.  Guided so a
        second trigger can't stack a dialog while one is already open.
        """
        page = getattr(self.win, '_page', None)
        if page is None or not hasattr(page, '_show_sync_deletion_dialog'):
            return
        if getattr(page, '_sync_dialog_open', False):
            return
        page._show_sync_deletion_dialog(deletions)

    def show_sync_progress(self):
        """Open the live 'Sync in progress' popup (click on the sidebar row).

        Stays open after the run finishes (or fails) until the user closes
        it — the final summary is left visible.
        """
        dialog = getattr(self, '_sync_progress_dialog', None)
        if dialog is not None:
            dialog.present(self.win)
            return
        dialog = Adw.Dialog()
        dialog.set_title("Sync in progress")
        dialog.set_presentation_mode(Adw.DialogPresentationMode.FLOATING)
        dialog.set_size_request(380, -1)

        header = Adw.HeaderBar()
        header.add_css_class("flat")

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        body.set_margin_top(8)
        body.set_margin_bottom(16)
        body.set_margin_start(24)
        body.set_margin_end(24)

        # --- Phase checklist ---
        self._sync_phases = []
        phases_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self._sync_step_label = Gtk.Label(label="Step 1 of 4",
                                          xalign=0.0, halign=Gtk.Align.START)
        self._sync_step_label.add_css_class("title-3")
        phases_box.append(self._sync_step_label)
        checklist = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        for name in ("Check your services", "Import new items",
                     "Push your changes", "Remove deleted items"):
            spinner = Adw.Spinner()
            spinner.set_visible(False)
            check = Gtk.Image(icon_name="object-select-symbolic",
                              icon_size=Gtk.IconSize.NORMAL)
            check.set_visible(False)
            check.add_css_class("success")
            label = Gtk.Label(label=name, xalign=0.0, hexpand=True,
                              halign=Gtk.Align.FILL)
            row = Gtk.Box(spacing=8)
            row.append(spinner)
            row.append(check)
            row.append(label)
            checklist.append(row)
            self._sync_phases.append({
                "spinner": spinner, "check": check, "label": label,
            })
        phases_box.append(checklist)
        body.append(phases_box)

        self._sync_progress_label = Gtk.Label(label="Starting sync…")
        self._sync_progress_label.set_wrap(True)
        self._sync_progress_label.set_halign(Gtk.Align.START)
        body.append(self._sync_progress_label)

        self._sync_progress_counts = Gtk.Label(label="")
        self._sync_progress_counts.set_wrap(True)
        self._sync_progress_counts.set_halign(Gtk.Align.START)
        self._sync_progress_counts.add_css_class("dimmed")
        body.append(self._sync_progress_counts)

        self._sync_progress_bar_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self._sync_progress_bar_box.set_visible(False)
        self._sync_progress_bar = Gtk.ProgressBar()
        self._sync_progress_bar_box.append(self._sync_progress_bar)
        self._sync_progress_bar_label = Gtk.Label(label="")
        self._sync_progress_bar_label.set_halign(Gtk.Align.CENTER)
        self._sync_progress_bar_label.add_css_class("dimmed")
        self._sync_progress_bar_box.append(self._sync_progress_bar_label)
        body.append(self._sync_progress_bar_box)

        self._sync_elapsed = Gtk.Label(label="")
        self._sync_elapsed.set_halign(Gtk.Align.START)
        self._sync_elapsed.add_css_class("dimmed")
        self._sync_elapsed.add_css_class("caption")
        body.append(self._sync_elapsed)

        self._sync_errors_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self._sync_errors_box.set_visible(False)
        body.append(self._sync_errors_box)

        buttons = Gtk.Box(spacing=8)
        self._sync_cancel_btn = Gtk.Button(label="Cancel sync")
        self._sync_cancel_btn.add_css_class("destructive-action")
        self._sync_cancel_btn.set_visible(False)
        self._sync_cancel_btn.connect(
            "clicked", lambda _b: self._cancel_sync_from_progress())
        self._sync_retry_btn = Gtk.Button(label="Retry")
        self._sync_retry_btn.add_css_class("suggested-action")
        self._sync_retry_btn.set_visible(False)
        self._sync_retry_btn.connect(
            "clicked", lambda _b: self._retry_from_progress())
        buttons.append(self._sync_cancel_btn)
        buttons.append(self._sync_retry_btn)
        buttons.append(Gtk.Box(hexpand=True))
        body.append(buttons)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content.append(header)
        content.append(body)
        dialog.set_child(content)
        dialog.connect("closed", self._on_sync_progress_closed)
        dialog.present(self.win)

        self._sync_progress_dialog = dialog
        self._sync_progress_open = True
        self._sync_progress_started = None
        engine = getattr(self, '_sync_engine', None)
        if engine is not None:
            self._update_sync_progress(engine.status)

    def _update_sync_phases(self, state, status_text):
        """Set the phase checklist + 'Step N of 4' headline from status.

        Returns the 0-based active phase index, or -1 when finished/failed.
        """
        t = (status_text or "").lower()
        if "import" in t or "artwork" in t or "poster" in t:
            active = 1
        elif "push" in t:
            active = 2
        elif "remov" in t or "delet" in t or "checking" in t:
            active = 3
        else:
            active = 0  # starting / syncing around
        if state in ("done", "error", "cancelled"):
            active = -1  # finished / failed / cancelled

        # Headline: active step while running, final title when done.
        if state == "done":
            self._sync_step_label.set_text("Sync complete")
        elif state == "error":
            self._sync_step_label.set_text("Sync failed")
        elif state == "cancelled":
            self._sync_step_label.set_text("Sync cancelled")
        elif active >= 0:
            self._sync_step_label.set_text(f"Step {active + 1} of 4")

        for i, ph in enumerate(self._sync_phases):
            ph["spinner"].stop()
            ph["spinner"].set_visible(False)
            ph["check"].set_visible(False)
            ph["label"].remove_css_class("error")
            ph["label"].remove_css_class("success")
            if state == "error" and i == max(active, 0):
                ph["label"].add_css_class("error")
            elif state == "done" or state == "cancelled" or (active == -1) or i < active:
                ph["check"].set_visible(True)
                ph["label"].add_css_class("success")
            elif i == active:
                ph["spinner"].set_visible(True)
                ph["spinner"].start()
        return active

    def _on_sync_progress_closed(self, _dialog):
        self._sync_progress_open = False
        self._sync_progress_dialog = None

    def _retry_from_progress(self):
        """Re-run the sync directly from the failed progress popup."""
        if self._sync_progress_dialog is None:
            return
        self._sync_progress_dialog.close()
        engine = getattr(self, '_sync_engine', None)
        if engine is not None:
            engine.sync_now()

    def _cancel_sync_from_progress(self):
        """User pressed Cancel: request the running sync to stop & roll back."""
        self._sync_cancel_btn.set_label("Cancelling…")
        self._sync_cancel_btn.set_sensitive(False)
        engine = getattr(self, '_sync_engine', None)
        if engine is not None:
            engine.cancel_sync()

    def _update_sync_progress(self, status):
        if not getattr(self, '_sync_progress_open', False):
            return
        dialog = getattr(self, '_sync_progress_dialog', None)
        if dialog is None:
            return
        state = getattr(status, 'state', 'idle')
        if state in ("syncing", "enriching"):
            dialog.set_title("Sync in progress")
        elif state == "done":
            dialog.set_title("Sync complete")
        elif state == "error":
            dialog.set_title("Sync failed")
        elif state == "cancelled":
            dialog.set_title("Sync cancelled")

        # Cancel is only meaningful while a run is active.
        if state in ("syncing", "enriching"):
            self._sync_cancel_btn.set_visible(True)
            self._sync_cancel_btn.set_sensitive(True)
        else:
            self._sync_cancel_btn.set_visible(False)
            self._sync_cancel_btn.set_label("Cancel sync")

        status_text = getattr(status, 'status_text', '') or ''
        if not status_text:
            status_text = {
                "done": "Sync complete",
                "error": "Sync failed",
                "cancelled": "Sync cancelled",
                "idle": "Sync idle",
            }.get(state, "Syncing…")

        # Elapsed timer (frozen on done/error/cancelled).
        if state in ("syncing", "enriching"):
            if self._sync_progress_started is None:
                self._sync_progress_started = time.monotonic()
            elapsed = max(0, int(time.monotonic()
                                 - self._sync_progress_started))
            self._sync_elapsed.set_text(f"Elapsed {elapsed}s")
        elif state in ("done", "error", "cancelled"):
            self._sync_elapsed.set_label(
                f"Finished in {max(0, int(time.monotonic() - self._sync_progress_started))}s"
                if self._sync_progress_started else "")

        self._update_sync_phases(state, status_text)
        self._sync_progress_label.set_text(status_text)

        lines = []
        results = getattr(status, 'results', {}) or {}
        pulled_total = 0
        imported_total = 0
        for name, res in results.items():
            label = {"simkl": "Simkl", "tmdb": "TMDB",
                     "letterboxd": "Letterboxd"}.get(name, name)
            parts = []
            pulled = getattr(res, 'pulled', 0) or 0
            imported = getattr(res, 'pulled_added', pulled) or 0
            pulled_total += pulled
            imported_total += imported
            if pulled:
                parts.append(f"{pulled} imported")
            if getattr(res, 'pushed', 0):
                parts.append(f"{res.pushed} pushed")
            if parts:
                lines.append(f"{label}: {', '.join(parts)}")
        pushed_rem = getattr(status, 'pushed_removed', 0)
        pulled_rem = getattr(status, 'pulled_removed', 0)
        if pushed_rem:
            lines.append(f"Removed {pushed_rem} remotely")
        if pulled_rem:
            lines.append(f"Removed {pulled_rem} locally")

        # Reconciliation footer: what came down vs. what was added vs. what
        # still needs the user's input.
        ambiguous = getattr(status, 'ambiguous', []) or []
        if state == "done" and pulled_total:
            tail = [f"{pulled_total} pulled \u2192 {imported_total} imported"]
            if ambiguous:
                tail.append(f"{len(ambiguous)} need your input")
            lines.append("\u2022 ".join(tail))
        self._sync_progress_counts.set_text("\n".join(lines) if lines else "")

        # Error panel: every service error, not just the first one.
        if state == "error":
            err_lines = []
            for name, res in results.items():
                for err in getattr(res, 'errors', []) or []:
                    nicer = {"simkl": "Simkl", "tmdb": "TMDB",
                             "letterboxd": "Letterboxd"}.get(name, name)
                    err_lines.append(f"{nicer}: {err}")
            if err_lines:
                for i, txt in enumerate(err_lines):
                    lbl = Gtk.Label(
                        label=txt, wrap=True, xalign=0.0,
                        halign=Gtk.Align.FILL)
                    lbl.add_css_class("error")
                    lbl.add_css_class("caption")
                    self._sync_errors_box.append(lbl)
                self._sync_errors_box.set_visible(True)
            # Retry only when the user kicked off the run (manual/startup).
            self._sync_retry_btn.set_visible(
                bool(getattr(status, 'show_report', False)))

        # Artwork backfill progress
        in_backfill = ("artwork" in status_text.lower()
                       or "poster" in status_text.lower()
                       or state == "enriching")
        total = getattr(status, 'backfill_total', 0) or 0
        if in_backfill and total > 0:
            current = getattr(status, 'backfill_current', 0) or 0
            self._sync_progress_bar_box.set_visible(True)
            self._sync_progress_bar.set_fraction(
                min(1.0, current / total))
            self._sync_progress_bar_label.set_text(
                f"{current} of {total}")
        elif not in_backfill:
            self._sync_progress_bar_box.set_visible(False)

    # ------------------------------------------------------------------
    # Mapping wizard — "how should I map these items?"
    # ------------------------------------------------------------------
    def _maybe_show_mapping_wizard(self, entries: list[dict]):
        """Present one row per unverifiable Simkl item.

        Nothing is imported or skipped without the user's choice: each row
        gets a confirmed (or repaired) TMDB mapping, or is marked for
        manual fixing on Simkl itself.  Not shown again for a run once the
        user deals with all current entries — still-unresolved items
        reappear on the next startup/manual sync.
        """
        if not entries:
            return
        if getattr(self, '_wizard_open', False):
            return
        dialog = Adw.Dialog(title="A few items need matching")
        dialog.set_content_width(840)
        dialog.set_content_height(640)
        dialog.set_presentation_mode(Adw.DialogPresentationMode.FLOATING)

        header = Adw.HeaderBar()
        header.add_css_class("flat")

        explain = Gtk.Label(
            label="These items couldn\u2019t be matched automatically. "
                  "Pick the right one for each \u2014 or skip and try later.",
            wrap=True, xalign=0.0, halign=Gtk.Align.FILL,
        )
        explain.add_css_class("dimmed")
        explain.set_margin_bottom(8)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_max_content_height(540)
        scroller.set_vexpand(True)

        rows_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        scroller.set_child(rows_box)

        self._wizard_remaining = Gtk.Label(label="")
        self._wizard_remaining.set_halign(Gtk.Align.CENTER)
        self._wizard_remaining.add_css_class("caption")
        self._wizard_remaining.add_css_class("dimmed")

        bottom_sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        bottom_sep.set_margin_top(4)

        bottom = Gtk.Box(spacing=8)
        bottom.append(Gtk.Box(hexpand=True))
        close_btn = Gtk.Button(label="Close")
        close_btn.add_css_class("flat")
        close_btn.connect("clicked", lambda _b: dialog.close())
        bottom.append(close_btn)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        body.set_margin_top(8)
        body.set_margin_bottom(16)
        body.set_margin_start(20)
        body.set_margin_end(20)
        body.append(explain)
        body.append(scroller)
        body.append(bottom_sep)
        body.append(self._wizard_remaining)
        body.append(bottom)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content.append(header)
        content.append(body)
        dialog.set_child(content)

        engine = getattr(self, '_sync_engine', None)
        if engine is not None:
            engine._defer_surface = True
        self._wizard_dialog = dialog
        self._wizard_open = True

        remaining = {"n": len(entries)}

        def _on_removed(_row):
            remaining["n"] -= 1
            rows_box.remove(_row)
            self._wizard_remaining.set_text(
                f"{remaining['n']} item{'s' if remaining['n'] != 1 else ''} "
                "still need your input"
                if remaining["n"] else "Everything is mapped.")
            if remaining["n"] == 0:
                toast = Adw.Toast.new("Simkl mappings updated")
                toast.set_timeout(5)
                self.win._toast_overlay.add_toast(toast)
                dialog.close()

        def _on_closed(_d):
            self._wizard_open = False
            self._wizard_dialog = None
            self._wizard_row_widgets = {}
            if engine is not None:
                engine._defer_surface = False

        dialog.connect("closed", _on_closed)

        auto_searches = []
        for entry in entries:
            row_widget = self._wizard_row(entry, dialog, _on_removed)
            rows_box.append(row_widget)
            if not (entry.get("claimed_tmdb_id") is not None
                    and entry.get("tmdb_title")):
                do_search = getattr(row_widget, '_wizard_do_search', None)
                if do_search:
                    auto_searches.append(do_search)

        self._wizard_remaining.set_text(
            f"{remaining['n']} item{'s' if remaining['n'] != 1 else ''} "
            "still need your input")

        try:
            dialog.present(self.win)
        except Exception:
            import logging
            logging.getLogger(__name__).exception(
                "Failed to present mapping wizard")
            self._wizard_open = False
            self._wizard_dialog = None
            if engine is not None:
                engine._defer_surface = False
            return

        for _do in auto_searches:
            GLib.idle_add(_do)

    def _wizard_row(self, entry, dialog, on_removed):
        """Build the row UI for one mapping decision."""
        item = entry.get("item")
        title = (entry.get("simkl_title")
                 or getattr(item, "title", None) or "Unknown")
        year = entry.get("year")
        mt = entry.get("media_type", "movie")
        claimed = entry.get("claimed_tmdb_id")
        tmdb_title = entry.get("tmdb_title")
        confirmed = claimed is not None and tmdb_title

        row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        row.add_css_class("wizard-row")

        # ── header ──────────────────────────────────────────────
        head = Gtk.Box(spacing=8)
        icon = Gtk.Image(icon_name="tv-symbolic"
                         if mt == "show" else "video-symbolic",
                         icon_size=Gtk.IconSize.NORMAL)
        head.append(icon)
        title_lbl = Gtk.Label(
            label=f"{title}" + (f"  \u00b7  {year}" if year else ""),
            xalign=0.0, hexpand=True, halign=Gtk.Align.FILL, wrap=True)
        title_lbl.add_css_class("heading")
        head.append(title_lbl)
        row.append(head)

        # ── note ────────────────────────────────────────────────
        if confirmed:
            note = (f"This looks like \u201c{tmdb_title}\u201d "
                    "in your library.")
        elif claimed is not None:
            note = ("The previous match is no longer available. "
                    "Find the correct one below.")
        else:
            note = "We couldn\u2019t find a match automatically."

        note_lbl = Gtk.Label(label=note, wrap=True, xalign=0.0,
                             halign=Gtk.Align.FILL)
        note_lbl.add_css_class("dimmed")
        row.append(note_lbl)

        # ── search controls (shared) ───────────────────────────
        search_entry = Gtk.Entry()
        search_entry.set_placeholder_text("Search movies and shows\u2026")
        search_entry.set_text(title or "")
        search_entry.set_hexpand(True)

        search_btn = Gtk.Button(label="Search")

        spinner = Adw.Spinner()
        spinner.set_size_request(16, 16)
        spinner.set_visible(False)

        search_box = Gtk.Box(spacing=6)
        search_box.append(search_entry)
        search_box.append(search_btn)
        search_box.append(spinner)

        results_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        results_list.add_css_class("boxed-list")
        results_list.set_visible(False)
        if not hasattr(self, '_wizard_row_widgets'):
            self._wizard_row_widgets = {}
        self._wizard_row_widgets[id(results_list)] = {
            "spinner": spinner, "search_btn": search_btn}

        selected = {"id": None, "type": mt}

        apply_btn = Gtk.Button(label="Use this")
        apply_btn.add_css_class("suggested-action")
        apply_btn.set_sensitive(False)
        apply_btn.set_halign(Gtk.Align.END)
        apply_btn.connect(
            "clicked",
            lambda _b: (self._wizard_apply(
                entry, selected["id"], selected["type"],
                row, dialog, on_removed)
                if selected["id"] else None))

        def _on_selected(_l, r):
            if r is None:
                return
            selected["id"] = getattr(r, "tmdb_id", None)
            selected["type"] = getattr(r, "media_type", None) or mt
            apply_btn.set_sensitive(True)

        results_list.connect("row-selected", _on_selected)

        def _do_search(_w=None):
            query = search_entry.get_text().strip()
            if not query:
                return
            spinner.set_visible(True)
            search_btn.set_sensitive(False)
            self._wizard_search(
                dialog, query, mt, results_list, selected, apply_btn,
                entry.get("year"))

        search_btn.connect("clicked", _do_search)
        search_entry.connect("activate", _do_search)

        # ── layout ──────────────────────────────────────────────
        search_section = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=6)
        search_section.append(search_box)
        search_section.append(results_list)
        sec_actions = Gtk.Box(spacing=8)
        sec_actions.append(Gtk.Box(hexpand=True))
        sec_actions.append(apply_btn)
        search_section.append(sec_actions)

        if confirmed:
            confirm_btn = Gtk.Button(label="Confirm")
            confirm_btn.add_css_class("suggested-action")
            confirm_btn.set_halign(Gtk.Align.START)
            confirm_btn.connect(
                "clicked",
                lambda _b: self._wizard_apply(
                    entry, claimed, mt, row, dialog, on_removed))

            search_section.set_visible(False)

            toggle_btn = Gtk.Button(
                label="Find a different match\u2026")
            toggle_btn.add_css_class("flat")
            toggle_btn.add_css_class("wizard-link")
            toggle_btn.set_halign(Gtk.Align.START)

            def _toggle_search(_b):
                vis = not search_section.get_visible()
                search_section.set_visible(vis)
                toggle_btn.set_label(
                    "Hide search" if vis
                    else "Find a different match\u2026")

            toggle_btn.connect("clicked", _toggle_search)

            dismiss_btn = Gtk.Button(label="Not on TMDB \u2014 fix on Simkl")
            dismiss_btn.add_css_class("flat")
            dismiss_btn.add_css_class("wizard-link")
            dismiss_btn.set_halign(Gtk.Align.START)
            dismiss_btn.connect("clicked", lambda _b: self._wizard_dismiss(
                entry, row, dialog, on_removed))

            actions_box = Gtk.Box(spacing=12)
            actions_box.append(confirm_btn)
            actions_box.append(toggle_btn)
            actions_box.append(dismiss_btn)
            row.append(actions_box)
            row.append(search_section)
        else:
            dismiss_btn = Gtk.Button(label="Not on TMDB \u2014 fix on Simkl")
            dismiss_btn.add_css_class("flat")
            dismiss_btn.add_css_class("wizard-link")
            dismiss_btn.set_halign(Gtk.Align.START)
            dismiss_btn.connect("clicked", lambda _b: self._wizard_dismiss(
                entry, row, dialog, on_removed))
            row.append(search_section)
            row.append(dismiss_btn)

        row._wizard_do_search = _do_search
        return row

    def _wizard_search(self, dialog, query, media_type, results_list,
                       selected, apply_btn, year=None):
        """Run a TMDB search off the main thread and fill the row's list."""
        widgets = getattr(self, '_wizard_row_widgets', {}).get(
            id(results_list)) or {}
        spinner = widgets.get("spinner")
        search_btn = widgets.get("search_btn")

        if self._metadata_service is None:
            if spinner:
                spinner.set_visible(False)
            if search_btn:
                search_btn.set_sensitive(True)
            GLib.idle_add(self._wizard_search_failed, dialog,
                          "TMDB service not available", results_list)
            return

        def _run():
            try:
                movie_results = self._metadata_service.search_movies(query)
                show_results = self._metadata_service.search_shows(query)
                seen = set()
                results = []
                for r in (movie_results or []) + (show_results or []):
                    if r.tmdb_id not in seen:
                        seen.add(r.tmdb_id)
                        results.append(r)
            except Exception as exc:
                GLib.idle_add(self._wizard_search_failed, dialog,
                              str(exc), results_list)
                return
            GLib.idle_add(
                self._wizard_search_done, dialog, results_list,
                selected, apply_btn, results, query, year)

        threading.Thread(target=_run, daemon=True).start()

    def _wizard_search_done(self, dialog, results_list, selected,
                            apply_btn, results, query="", year=None):
        """Marshal search results back onto the main thread."""
        if getattr(self, '_wizard_dialog', None) is not dialog:
            return
        widgets = getattr(self, '_wizard_row_widgets', {}).get(
            id(results_list)) or {}
        spinner = widgets.get("spinner")
        search_btn = widgets.get("search_btn")
        if spinner:
                spinner.set_visible(False)
        if search_btn:
            search_btn.set_sensitive(True)
        apply_btn.set_sensitive(False)
        selected["id"] = None
        results_list.remove_all()

        # Sort: exact title match first, then containment, then API order.
        from .data.sync.verify import _normalize_title
        nq = _normalize_title(query)
        def _sort_key(r):
            nt = _normalize_title(getattr(r, "title", ""))
            if nt == nq:
                exact_year = (year is not None
                              and getattr(r, "year", None) == year)
                return (0, 0 if exact_year else 1)
            if nq and len(nq) >= 4 and (nq in nt or nt in nq):
                return (1, 0)
            return (2, 0)
        results.sort(key=_sort_key)

        for r in results[:12]:
            row = Gtk.ListBoxRow()
            box = Gtk.Box(spacing=10)
            box.set_hexpand(True)
            pic = Gtk.Picture()
            poster_url = getattr(r, "poster_url", None)
            if poster_url:
                pic.set_content_fit(Gtk.ContentFit.COVER)
                pic.set_size_request(48, 68)
                box.append(pic)
                load_poster(poster_url, pic)
            year_str = (f" \u00b7 {r.year}"
                        if getattr(r, "year", None) else "")
            txt = Gtk.Label(
                label=f"{r.title}{year_str}",
                xalign=0.0, halign=Gtk.Align.FILL)
            txt.set_ellipsize(Pango.EllipsizeMode.END)
            box.append(txt)
            row.set_child(box)
            row.tmdb_id = r.tmdb_id
            row.media_type = r.media_type
            results_list.append(row)
        results_list.set_visible(True)

    def _wizard_search_failed(self, dialog, message, results_list=None):
        if getattr(self, '_wizard_dialog', None) is not dialog:
            return
        if results_list is not None:
            widgets = getattr(self, '_wizard_row_widgets', {}).get(
                id(results_list)) or {}
            spinner = widgets.get("spinner")
            search_btn = widgets.get("search_btn")
            if spinner:
                spinner.set_visible(False)
            if search_btn:
                search_btn.set_sensitive(True)
        toast = Adw.Toast.new(f"TMDB search failed: {message}")
        toast.set_timeout(5)
        self.win._toast_overlay.add_toast(toast)

    def _wizard_apply(self, entry, tmdb_id, media_type, row, dialog, on_removed):
        """Resolve one mapping decision through the engine."""
        engine = getattr(self, '_sync_engine', None)
        if engine is None or not tmdb_id:
            return
        ok = engine.resolve_mapping(entry, tmdb_id, media_type)
        if not ok:
            toast = Adw.Toast.new("Couldn't apply that mapping \u2014 try again")
            toast.set_timeout(5)
            self.win._toast_overlay.add_toast(toast)
            return
        on_removed(row)

    def _wizard_dismiss(self, entry, row, dialog, on_removed):
        """Dismiss a mapping-decision entry as not existing on TMDB."""
        engine = getattr(self, '_sync_engine', None)
        if engine is None:
            return
        engine.dismiss_item(entry)
        on_removed(row)
        toast = Adw.Toast.new("Dismissed \u2014 won't appear again")
        toast.set_timeout(4)
        self.win._toast_overlay.add_toast(toast)


    def do_shutdown(self):
        notifier = getattr(self, "_notifier", None)
        if notifier is not None:
            notifier.stop()
        engine = getattr(self, "_sync_engine", None)
        if engine is not None:
            engine.stop()
        if self._onboarding_win is not None:
            try:
                self._onboarding_win.close()
            except GLib.Error:
                pass
        if self._user_repo is not None:
            self._user_repo.close()
        if self._metadata_service is not None:
            self._metadata_service.close()
        from .threads import shutdown as _shutdown_threads

        _shutdown_threads(wait=False)
        Adw.Application.do_shutdown(self)


def main():
    import logging
    logging.basicConfig(level=logging.INFO)
    app = CiakApp()
    return app.run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
