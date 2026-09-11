import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, GLib, Gio

import threading

from .. import poster_cache
from .. import config
from ..theme import apply_theme
from .export_dialog import show_export_dialog
from .import_dialog import show_import_dialog
from ..data.sync.credentials import load_credential, store_credential, delete_service
from ..data.sync.tmdb_backend import TmdbSyncBackend
from ..data.sync.simkl_backend import SimklSyncBackend
from ..data.sync.letterboxd_backend import LetterboxdSyncBackend

THEME_VALUES = ["light", "dark", "default"]
_REGION_LABELS = ["auto", "US", "GB", "IT", "DE", "FR", "ES", "CA", "AU"]

DEFAULT_PAGES = [
    ("watchlist", "Watchlist"),
    ("search", "Search"),
    ("history", "History"),
    ("calendar", "Calendar"),
    ("profile", "Profile"),
]


class PreferencesPage(Adw.PreferencesDialog):
    """Tabbed preferences dialog."""

    def __init__(self, win, main_page=None):
        super().__init__()
        self.win = win
        self._settings = win.settings
        self._main_page = main_page

        self.set_title("Preferences")
        self.set_presentation_mode(Adw.DialogPresentationMode.FLOATING)
        self.set_content_height(700)
        self.set_content_width(760)

        self._build_pages()
        self._update_cache_size()
        self.set_search_enabled(True)
        self.connect("closed", self._on_closed)

    def _on_closed(self, _dialog):
        self.pop_subpage()
        self.set_search_enabled(False)

    def _build_pages(self):
        self._build_general_page()
        self._build_sync_page()
        self._build_shortcuts_page()
        self._build_advanced_page()
        self._build_about_page()

    #
    # General
    #

    def _build_general_page(self):
        page = Adw.PreferencesPage()
        page.set_name("general")
        page.set_title("General")
        page.set_icon_name("emblem-system-symbolic")
        self.add(page)

        page.add(self._build_support_banner())

        appearance = Adw.PreferencesGroup()
        appearance.set_title("Appearance")
        page.add(appearance)

        theme_row = Adw.ComboRow()
        theme_row.set_title("Theme")
        theme_row.set_subtitle("Force light or dark, or follow the system")
        theme_model = Gtk.StringList()
        for label in ("Light", "Dark", "Follow System"):
            theme_model.append(label)
        theme_row.set_model(theme_model)
        current = self._settings.get_string("theme")
        theme_row.set_selected(
            THEME_VALUES.index(current) if current in THEME_VALUES else 2
        )
        theme_row.connect("notify::selected", self._on_theme_changed)
        appearance.add(theme_row)

        sidebar_row = Adw.ComboRow()
        sidebar_row.set_title("Sidebar")
        sidebar_row.set_subtitle("Choose what happens on startup")
        sidebar_model = Gtk.StringList()
        for label in ("Always collapse", "Remember last state"):
            sidebar_model.append(label)
        sidebar_row.set_model(sidebar_model)
        if self._settings.get_string("sidebar-default-mode") == "collapse":
            sidebar_row.set_selected(0)
        else:
            sidebar_row.set_selected(1)
        sidebar_row.connect("notify::selected", self._on_sidebar_mode_changed)
        appearance.add(sidebar_row)

        disable_anim_row = Adw.SwitchRow()
        disable_anim_row.set_title("Disable Animations")
        disable_anim_row.set_subtitle(
            "Turn off transition and fade animations across the app"
        )
        self._settings.bind(
            "disable-animations",
            disable_anim_row,
            "active",
            Gio.SettingsBindFlags.DEFAULT,
        )
        appearance.add(disable_anim_row)

        american_dates_row = Adw.SwitchRow()
        american_dates_row.set_title("American Date Format")
        american_dates_row.set_subtitle(
            "Show the month before the day (Aug 12 instead of 12 Aug)"
        )
        self._settings.bind(
            "american-date-format",
            american_dates_row,
            "active",
            Gio.SettingsBindFlags.DEFAULT,
        )
        appearance.add(american_dates_row)

        startup = Adw.PreferencesGroup()
        startup.set_title("Startup")
        page.add(startup)

        notifications_group = Adw.PreferencesGroup()
        notifications_group.set_title("Notifications")
        notifications_group.set_description(
            "Desktop notifications when media on your watchlist airs or releases"
        )
        page.add(notifications_group)

        notify_row = Adw.SwitchRow()
        notify_row.set_title("Airing Notifications")
        notify_row.set_subtitle(
            "Notify me when watchlist media airs (app must be running)"
        )
        self._settings.bind(
            "airing-notifications",
            notify_row,
            "active",
            Gio.SettingsBindFlags.DEFAULT,
        )
        notifications_group.add(notify_row)

        scope_row = Adw.ComboRow()
        scope_row.set_title("Notify About")
        scope_model = Gtk.StringList()
        for label in ("Everything on my watchlist", "Only selected media"):
            scope_model.append(label)
        scope_row.set_model(scope_model)
        scope_row.set_selected(
            1 if self._settings.get_string("notification-scope") == "selected" else 0
        )
        scope_row.connect("notify::selected", self._on_notify_scope_changed)
        self._settings.bind(
            "airing-notifications",
            scope_row,
            "sensitive",
            Gio.SettingsBindFlags.DEFAULT,
        )
        notifications_group.add(scope_row)

        content_group = Adw.PreferencesGroup()
        content_group.set_title("Content")
        page.add(content_group)

        adult_row = Adw.SwitchRow()
        adult_row.set_title("Hide Adult Content")
        adult_row.set_subtitle("Exclude adult content from search results")
        self._settings.bind(
            "hide-adult-content",
            adult_row,
            "active",
            Gio.SettingsBindFlags.DEFAULT,
        )
        content_group.add(adult_row)

        unreleased_row = Adw.SwitchRow()
        unreleased_row.set_title("Hide Unreleased Titles")
        unreleased_row.set_subtitle(
            "Don't show movies and shows that haven't premiered yet in the Watchlist")
        self._settings.bind(
            "hide-unreleased",
            unreleased_row,
            "active",
            Gio.SettingsBindFlags.DEFAULT,
        )
        content_group.add(unreleased_row)

        # Hide shows with no upcoming episodes
        self._hide_shows_no_upcoming_row = Adw.SwitchRow()
        self._hide_shows_no_upcoming_row.set_title(
            "Hide TV Shows with No Upcoming Episodes")
        self._hide_shows_no_upcoming_row.set_subtitle(
            "Hide shows you've caught up on when no new episode is scheduled within the next few days. "
            "They reappear automatically when a new episode is announced.")
        self._settings.bind(
            "hide-shows-no-upcoming-episodes",
            self._hide_shows_no_upcoming_row,
            "active",
            Gio.SettingsBindFlags.DEFAULT,
        )
        self._hide_shows_no_upcoming_row.connect(
            "notify::active", self._on_hide_shows_no_upcoming_toggled
        )
        content_group.add(self._hide_shows_no_upcoming_row)

        # Window days spin row
        self._hide_shows_window_adjustment = Gtk.Adjustment.new(
            self._settings.get_int("hide-shows-upcoming-window"), 1, 90, 1, 7, 0
        )
        self._hide_shows_window_row = Adw.SpinRow.new(
            self._hide_shows_window_adjustment, 0.0, 0
        )
        self._hide_shows_window_row.set_title("Upcoming Episode Window (Days)")
        self._hide_shows_window_row.set_subtitle(
            "Number of days to look ahead for upcoming episodes")
        self._hide_shows_window_row.connect(
            "notify::value", self._on_hide_shows_window_changed
        )
        # Disable when main toggle is off
        self._hide_shows_window_row.set_sensitive(
            self._settings.get_boolean("hide-shows-no-upcoming-episodes")
        )
        content_group.add(self._hide_shows_window_row)

        show_streaming_row = Adw.SwitchRow()
        show_streaming_row.set_title("Show Streaming Availability")
        show_streaming_row.set_subtitle("Display where movies and TV shows can be streamed, rented, or bought")
        self._settings.bind(
            "show-streaming-availability",
            show_streaming_row,
            "active",
            Gio.SettingsBindFlags.DEFAULT,
        )
        content_group.add(show_streaming_row)

        remember_filter_row = Adw.SwitchRow()
        remember_filter_row.set_title("Remember Content Filter")
        remember_filter_row.set_subtitle("Restore the last ALL / Movies / Shows filter on startup")
        self._settings.bind(
            "remember-content-filter",
            remember_filter_row,
            "active",
            Gio.SettingsBindFlags.DEFAULT,
        )
        content_group.add(remember_filter_row)

        swap_sections_row = Adw.SwitchRow()
        swap_sections_row.set_title("Swap Movies and TV Shows")
        swap_sections_row.set_subtitle(
            "List TV Shows before Movies on watchlist, search and history. Restarts the app."
        )
        swap_sections_row.set_active(
            self._settings.get_boolean("swap-sections")
        )
        swap_sections_row.connect(
            "notify::active", self._on_swap_sections_changed
        )
        content_group.add(swap_sections_row)

        region_row = Adw.ComboRow()
        region_row.set_title("Streaming Region")
        region_row.set_subtitle("Where to check availability (stream, rent, buy)")
        region_model = Gtk.StringList()
        region_model.append("Auto")
        region_model.append("United States (US)")
        region_model.append("United Kingdom (UK)")
        region_model.append("Italy (IT)")
        region_model.append("Germany (DE)")
        region_model.append("France (FR)")
        region_model.append("Spain (ES)")
        region_model.append("Canada (CA)")
        region_model.append("Australia (AU)")
        region_row.set_model(region_model)
        current_region = self._settings.get_string("streaming-region")
        region_row.set_selected(
            _REGION_LABELS.index(current_region)
            if current_region in _REGION_LABELS
            else 0
        )
        region_row.connect("notify::selected", self._on_region_changed)
        content_group.add(region_row)

        default_row = Adw.ComboRow()
        default_row.set_title("Default Page")
        default_row.set_subtitle("Page shown when the app starts")
        default_model = Gtk.StringList()
        for _page_id, label in DEFAULT_PAGES:
            default_model.append(label)
        default_row.set_model(default_model)
        page_ids = [page_id for page_id, _label in DEFAULT_PAGES]
        current_page = self._settings.get_string("default-page")
        default_row.set_selected(
            page_ids.index(current_page) if current_page in page_ids else 0
        )
        default_row.connect("notify::selected", self._on_default_page_changed)
        startup.add(default_row)

    def _build_support_banner(self):
        group = Adw.PreferencesGroup()
        group.add_css_class("card")
        group.add_css_class("support-group")

        overlay = Gtk.Overlay()

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        box.set_hexpand(True)

        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        col.set_hexpand(True)
        title = Gtk.Label(label="Support Ciak")
        title.set_halign(Gtk.Align.START)
        title.add_css_class("heading")
        col.append(title)

        body = Gtk.Label(
            label=(
                "Ciak is free and open-source, and will stay that way. "
                "If it helps you decide what to watch, buy me a coffee and "
                "keep the development brewing."
            )
        )
        body.set_halign(Gtk.Align.START)
        body.set_xalign(0)
        body.set_yalign(0)
        body.set_wrap(True)
        body.add_css_class("caption")
        col.append(body)

        box.append(col)

        donate_btn = Gtk.Button(label="Donate")
        donate_btn.set_valign(Gtk.Align.CENTER)
        donate_btn.set_halign(Gtk.Align.END)
        donate_btn.connect("clicked", self._on_donate_clicked)
        box.append(donate_btn)

        overlay.set_child(box)

        group.add(overlay)
        return group

    def _on_donate_clicked(self, _btn):
        Gio.app_info_launch_default_for_uri("https://buymeacoffee.com/ascape", None)

    #
    # API & Sync
    #

    def _build_sync_page(self):
        page = Adw.PreferencesPage()
        page.set_name("sync")
        page.set_title("API & Sync")
        page.set_icon_name("view-refresh-symbolic")
        self.add(page)

        self._build_tmdb_api_group(page)
        self._build_cloud_sync_group(page)
        self._build_danger_zone_group(page)

    def _build_tmdb_api_group(self, page):
        api_group = Adw.PreferencesGroup()
        api_group.set_title("TMDB API")
        api_group.set_description(
            "The TMDB API key is required — without it Ciak cannot "
            "search or match titles"
        )
        page.add(api_group)

        key_row = Adw.PasswordEntryRow()
        key_row.set_title("TMDB API Key")
        current_key = self._settings.get_string("tmdb-api-key")
        key_row.set_text(current_key)
        key_row.connect("changed", self._on_tmdb_key_changed)
        api_group.add(key_row)

        link_row = Adw.ActionRow()
        link_row.set_title("Get your API key at themoviedb.org")
        link_row.set_activatable(True)
        link_row.connect(
            "activated",
            lambda _r: Gio.app_info_launch_default_for_uri(
                "https://www.themoviedb.org/settings/api", None
            ),
        )
        link_row.add_suffix(
            Gtk.Image.new_from_icon_name("web-browser-symbolic")
        )
        api_group.add(link_row)

    def _build_cloud_sync_group(self, page):
        sync_group = Adw.PreferencesGroup()
        sync_group.set_title("Cloud Sync")
        sync_group.set_description("Keep your lists and history the same everywhere")
        page.add(sync_group)

        beta_pill = Gtk.Label(label="BETA")
        beta_pill.add_css_class("beta-chip-pill")
        beta_pill.add_css_class("beta-chip")
        sync_group.set_header_suffix(beta_pill)
        self._sync_beta_pill = beta_pill

        sync_master_row = Adw.SwitchRow()
        sync_master_row.set_title("Enable Cloud Sync")
        sync_master_row.set_subtitle("Turn syncing on or off, and manage the services below")
        self._settings.bind(
            "sync-enabled",
            sync_master_row,
            "active",
            Gio.SettingsBindFlags.DEFAULT,
        )
        sync_group.add(sync_master_row)

        self._sync_auto_row = Adw.SwitchRow()
        self._sync_auto_row.set_title("Automatic sync")
        self._sync_auto_row.set_subtitle(
            "Automatically keep things up to date on a schedule."
        )
        self._settings.bind(
            "sync-auto",
            self._sync_auto_row,
            "active",
            Gio.SettingsBindFlags.DEFAULT,
        )
        self._settings.bind(
            "sync-enabled",
            self._sync_auto_row,
            "sensitive",
            Gio.SettingsBindFlags.DEFAULT,
        )
        sync_group.add(self._sync_auto_row)

        self._sync_tmdb_row = Adw.ActionRow()
        self._sync_tmdb_row.set_title("TMDB")
        self._sync_tmdb_row.set_subtitle("Your watchlist and star ratings")
        self._sync_tmdb_row.set_activatable(True)
        self._sync_tmdb_row.connect("activated", self._on_tmdb_sync_clicked)
        self._sync_tmdb_switch = Gtk.Switch()
        self._sync_tmdb_switch.set_valign(Gtk.Align.CENTER)
        self._settings.bind(
            "sync-tmdb-enabled",
            self._sync_tmdb_switch,
            "active",
            Gio.SettingsBindFlags.DEFAULT,
        )
        self._settings.bind(
            "sync-enabled",
            self._sync_tmdb_switch,
            "sensitive",
            Gio.SettingsBindFlags.DEFAULT,
        )
        self._sync_tmdb_row.add_suffix(self._sync_tmdb_switch)
        sync_group.add(self._sync_tmdb_row)

        self._sync_simkl_row = Adw.ActionRow()
        self._sync_simkl_row.set_title("Simkl")
        self._sync_simkl_row.set_subtitle("Your watchlist, watch history, ratings, and saved titles")
        self._sync_simkl_row.set_activatable(True)
        self._sync_simkl_row.connect("activated", self._on_simkl_sync_clicked)
        self._sync_simkl_switch = Gtk.Switch()
        self._sync_simkl_switch.set_valign(Gtk.Align.CENTER)
        self._settings.bind(
            "sync-simkl-enabled",
            self._sync_simkl_switch,
            "active",
            Gio.SettingsBindFlags.DEFAULT,
        )
        self._settings.bind(
            "sync-enabled",
            self._sync_simkl_switch,
            "sensitive",
            Gio.SettingsBindFlags.DEFAULT,
        )
        self._sync_simkl_row.add_suffix(self._sync_simkl_switch)
        sync_group.add(self._sync_simkl_row)

        self._sync_letterboxd_row = Adw.ActionRow()
        self._sync_letterboxd_row.set_title("Letterboxd")
        self._sync_letterboxd_row.set_subtitle("Still being tested. Your watchlist and star ratings")
        self._sync_letterboxd_row.set_activatable(True)
        self._sync_letterboxd_row.connect("activated", self._on_letterboxd_sync_clicked)
        self._sync_lb_switch = Gtk.Switch()
        self._sync_lb_switch.set_valign(Gtk.Align.CENTER)
        self._settings.bind(
            "sync-letterboxd-enabled",
            self._sync_lb_switch,
            "active",
            Gio.SettingsBindFlags.DEFAULT,
        )
        self._settings.bind(
            "sync-enabled",
            self._sync_lb_switch,
            "sensitive",
            Gio.SettingsBindFlags.DEFAULT,
        )
        self._sync_letterboxd_row.add_suffix(self._sync_lb_switch)
        sync_group.add(self._sync_letterboxd_row)

        # Last sync label
        self._sync_last_label = Gtk.Label()
        self._sync_last_label.add_css_class("caption")
        self._sync_last_label.add_css_class("dimmed")
        self._sync_last_label.set_halign(Gtk.Align.START)
        self._sync_last_label.set_margin_top(4)
        sync_group.add(self._sync_last_label)
        self._update_sync_last_label()

        # Connect switch handlers after GSettings bind to avoid
        # firing on the initial value.
        self._sync_handlers_connected = False
        self._sync_tmdb_switch.connect("notify::active", self._on_sync_switch_toggled, "tmdb")
        self._sync_simkl_switch.connect("notify::active", self._on_sync_switch_toggled, "simkl")
        self._sync_lb_switch.connect("notify::active", self._on_sync_switch_toggled, "letterboxd")
        GLib.idle_add(lambda: setattr(self, "_sync_handlers_connected", True))

    def _build_danger_zone_group(self, page):
        danger_group = Adw.PreferencesGroup()
        danger_group.set_title("Danger Zone")
        danger_group.set_description(
            "Recreate a backup from scratch. Your current Ciak data "
            "becomes the source of truth.")
        page.add(danger_group)

        reset_row = Adw.ActionRow(
            title="Reset Simkl backup",
            subtitle="Erase everything on Simkl, then re-upload your "
                     "current Ciak data.",
        )
        reset_row.set_activatable(True)
        self._reset_btn = Gtk.Button(label="Reset")
        self._reset_btn.add_css_class("destructive-action")
        self._reset_btn.set_valign(Gtk.Align.CENTER)
        reset_row.add_suffix(self._reset_btn)
        reset_row.connect("activated", lambda _r: self._confirm_reset_backup())
        self._reset_btn.connect("clicked",
                                lambda _b: self._confirm_reset_backup())
        danger_group.add(reset_row)
        self._reset_row = reset_row
        self._refresh_reset_row()

    def _confirm_reset_backup(self):
        dialog = Adw.AlertDialog(
            heading="Reset Simkl backup?",
            body="This permanently erases everything on your Simkl account "
                 "(watchlist, watch history, ratings) and re-uploads your "
                 "current Ciak data as the source of truth. This can't be "
                 "undone.\n\nConsider exporting your data first.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("reset", "Reset")
        dialog.set_response_appearance(
            "reset", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def _on_answer(_d, response):
            if response != "reset":
                return
            try:
                app = self.win.get_application() if hasattr(self, "win") else None
                engine = getattr(app, "_sync_engine", None) if app else None
                if engine is None:
                    return
                if not engine.reset_remote("simkl"):
                    self.add_toast(
                        Adw.Toast.new("Reset unavailable right now"))
            except Exception:
                import logging
                logging.getLogger(__name__).exception("Simkl reset failed")

        dialog.connect("response", _on_answer)
        dialog.present(self)

    def _refresh_reset_row(self):
        row = getattr(self, "_reset_row", None)
        if row is None:
            return
        try:
            app = self.win.get_application() if hasattr(self, "win") else None
            engine = getattr(app, "_sync_engine", None) if app else None
            auth = load_credential("simkl", "access_token") is not None
            enabled = self._settings.get_boolean("sync-simkl-enabled")
            idle = engine is None or engine.status.state in (
                "idle", "done", "error", "cancelled")
            sensitive = bool(auth and enabled and idle)
            row.set_sensitive(sensitive)
            if getattr(self, "_reset_btn", None) is not None:
                self._reset_btn.set_sensitive(sensitive)
        except Exception:
            row.set_sensitive(False)
            if getattr(self, "_reset_btn", None) is not None:
                self._reset_btn.set_sensitive(False)

    #
    # Keyboard Shortcuts
    #

    def _build_shortcuts_page(self):
        page = Adw.PreferencesPage()
        page.set_name("shortcuts")
        page.set_title("Shortcuts")
        page.set_icon_name("input-keyboard-symbolic")
        self.add(page)

        group = Adw.PreferencesGroup()
        group.set_title("Keyboard Shortcuts")
        group.add_css_class("inline")
        page.add(group)

        self._add_shortcut_row(group, "Go to Watchlist", "<Control>w", "shortcut-watchlist-enabled")
        self._add_shortcut_row(group, "Go to Search", "<Control>f", "shortcut-search-enabled")
        self._add_shortcut_row(group, "Open Preferences", "<Control>comma", "shortcut-preferences-enabled")
        self._add_shortcut_row(group, "Toggle Sidebar", "<Control>s", "shortcut-sidebar-enabled")

    def _add_shortcut_row(self, group, title, accelerator, settings_key):
        row = Adw.ActionRow(title=title)

        display = accelerator.replace("<Control>", "Ctrl + ").replace("comma", ",").title()
        shortcut_label = Gtk.Label(label=display)
        shortcut_label.set_xalign(1.0)
        shortcut_label.add_css_class("dimmed")
        row.add_suffix(shortcut_label)

        switch = Gtk.Switch()
        switch.set_valign(Gtk.Align.CENTER)
        self._settings.bind(
            settings_key,
            switch,
            "active",
            Gio.SettingsBindFlags.DEFAULT,
        )
        row.add_suffix(switch)

        group.add(row)

    #
    # Advanced
    #

    def _build_advanced_page(self):
        page = Adw.PreferencesPage()
        page.set_name("advanced")
        page.set_title("Advanced")
        page.set_icon_name("applications-science-symbolic")
        self.add(page)

        self.advanced_banner = Adw.Banner.new(
            "A setting has changed that requires a restart to take effect"
        )
        self.advanced_banner.set_button_label("Got it!")
        self.advanced_banner.connect(
            "button-clicked", lambda banner: banner.set_revealed(False)
        )
        page.set_banner(self.advanced_banner)

        self._build_import_export_group(page)
        self._build_cache_group(page)
        self._build_data_management_group(page)
        self._build_setup_group(page)

    def _build_import_export_group(self, page):
        import_export_group = Adw.PreferencesGroup()
        import_export_group.set_title("Import and Export")
        import_export_group.set_description("Move your library data in and out of Ciak")
        page.add(import_export_group)

        export_row = Adw.ActionRow()
        export_row.set_title("Export Data")
        export_row.set_subtitle("Your library as a CSV or JSON file")
        export_row.set_activatable(False)
        export_btn = Gtk.Button(icon_name="document-send-symbolic")
        export_btn.set_tooltip_text("Export")
        export_btn.set_valign(Gtk.Align.CENTER)
        export_btn.add_css_class("flat")
        export_btn.connect("clicked", self._on_export_clicked)
        export_row.add_suffix(export_btn)
        import_export_group.add(export_row)

        import_row = Adw.ActionRow()
        import_row.set_title("Import Data")
        import_row.set_subtitle("CSV or JSON from Trakt, Letterboxd, IMDb")
        import_row.set_activatable(False)
        import_btn = Gtk.Button(icon_name="document-open-symbolic")
        import_btn.set_tooltip_text("Import")
        import_btn.set_valign(Gtk.Align.CENTER)
        import_btn.add_css_class("flat")
        import_btn.connect("clicked", self._on_import_clicked)
        import_row.add_suffix(import_btn)
        import_export_group.add(import_row)

    def _build_cache_group(self, page):
        cache_group = Adw.PreferencesGroup()
        cache_group.set_title("Poster Cache")
        cache_group.set_description("Poster images stored on disk")
        page.add(cache_group)

        self.clear_cache_row = Adw.ActionRow()
        self.clear_cache_row.set_title("Clear Cache")
        self.clear_cache_row.set_subtitle(
            "Removes downloaded poster images only. Your watchlist, history, "
            "and ratings are kept. Posters re-download on next view."
        )
        self.clear_cache_row.set_activatable(False)
        self.clear_cache_size_label = Gtk.Label()
        self.clear_cache_size_label.add_css_class("dimmed")
        self.clear_cache_row.add_suffix(self.clear_cache_size_label)
        clear_btn = Gtk.Button(icon_name="user-trash-symbolic")
        clear_btn.set_tooltip_text("Clear")
        clear_btn.set_valign(Gtk.Align.CENTER)
        clear_btn.add_css_class("flat")
        clear_btn.connect("clicked", self._on_clear_cache_clicked)
        self.clear_cache_row.add_suffix(clear_btn)
        cache_group.add(self.clear_cache_row)

        backfill_row = Adw.ActionRow()
        backfill_row.set_title("Re-fetch Missing Posters")
        backfill_row.set_subtitle(
            "Download posters for any media items currently missing artwork"
        )
        backfill_row.set_activatable(False)
        backfill_btn = Gtk.Button(label="Re-fetch")
        backfill_btn.set_valign(Gtk.Align.CENTER)
        backfill_btn.add_css_class("flat")
        backfill_btn.connect("clicked", self._on_backfill_clicked)
        backfill_row.add_suffix(backfill_btn)
        cache_group.add(backfill_row)

        self.cache_size_adjustment = Gtk.Adjustment.new(
            self._settings.get_int("cache-max-size-mb"), 50, 4096, 50, 500, 0
        )
        cache_size_row = Adw.SpinRow.new(self.cache_size_adjustment, 0.0, 0)
        cache_size_row.set_title("Max Cache Size")
        cache_size_row.set_subtitle(
            "Maximum disk space used by cached images (in MB)"
        )
        cache_size_row.connect("notify::value", self._on_cache_size_changed)
        cache_group.add(cache_size_row)

        clear_exit_row = Adw.SwitchRow()
        clear_exit_row.set_title("Clear Cache on Exit")
        self._settings.bind(
            "clear-cache-on-exit",
            clear_exit_row,
            "active",
            Gio.SettingsBindFlags.DEFAULT,
        )
        cache_group.add(clear_exit_row)

    def _build_data_management_group(self, page):
        data_management_group = Adw.PreferencesGroup()
        data_management_group.set_title("Local Data")
        data_management_group.set_description(
            "Erasing the local database is permanent and cannot be undone"
        )
        page.add(data_management_group)

        delete_db_row = Adw.ActionRow()
        delete_db_row.set_title("Delete Local Database")
        delete_db_row.set_subtitle(
            "Permanently deletes ALL local data: metadata cache, posters, "
            "watchlist, watched history, ratings, and collection. "
            "Not recoverable: no cloud sync."
        )
        delete_db_row.set_activatable(False)
        delete_btn = Gtk.Button(icon_name="user-trash-symbolic")
        delete_btn.set_tooltip_text("Delete")
        delete_btn.set_valign(Gtk.Align.CENTER)
        delete_btn.add_css_class("destructive-action")
        delete_btn.connect("clicked", self._on_delete_db_clicked)
        delete_db_row.add_suffix(delete_btn)
        data_management_group.add(delete_db_row)

    def _build_setup_group(self, page):
        setup_group = Adw.PreferencesGroup()
        setup_group.set_title("Setup")
        page.add(setup_group)

        relaunch_row = Adw.ActionRow()
        relaunch_row.set_title("Relaunch Onboarding")
        relaunch_row.set_subtitle("Show the first-run setup wizard again")
        relaunch_row.set_activatable(True)
        relaunch_row.connect("activated", self._on_relaunch_onboarding_clicked)
        relaunch_row.add_suffix(
            Gtk.Image.new_from_icon_name("view-refresh-symbolic")
        )
        setup_group.add(relaunch_row)

    def _on_cache_size_changed(self, row, _gparam):
        self._settings.set_int("cache-max-size-mb", int(row.get_value()))
        self.advanced_banner.set_revealed(True)

    def _on_clear_cache_clicked(self, _btn):
        dialog = Adw.AlertDialog.new(
            "Clear Cache?",
            f"Clear cached poster images? ({self._format_size(poster_cache.get_size())} currently stored)",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("clear", "Clear")
        dialog.set_response_appearance("clear", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", self._on_clear_cache_response)
        dialog.present(self)

    def _on_clear_cache_response(self, dialog, response):
        if response == "clear":
            poster_cache.clear()
            self._update_cache_size()
            self.add_toast(Adw.Toast.new("Cache cleared"))

    def _on_backfill_clicked(self, btn):
        from ..main import CiakApp
        from .import_dialog import backfill_missing_posters
        from .. import threads
        app = CiakApp.get_default()
        if not app or not app._user_repo or not app._metadata_service:
            self.add_toast(Adw.Toast.new("Service not available"))
            return
        btn.set_sensitive(False)
        btn.set_label("Fetching…")

        def _work():
            try:
                backfill_missing_posters(app._user_repo, app._metadata_service)
            except Exception:
                pass

        def _done(future):
            btn.set_sensitive(True)
            btn.set_label("Re-fetch")
            self.add_toast(Adw.Toast.new("Poster backfill complete"))
            if self._main_page:
                for page_id in ("watchlist", "history", "calendar", "profile"):
                    self._main_page.invalidate_page(page_id, reload_now=True)

        future = threads.submit(_work)
        future.add_done_callback(lambda f: GLib.idle_add(_done, f))

    def _on_delete_db_clicked(self, _btn):
        dialog = Adw.AlertDialog.new(
            "Delete Local Database?",
            "This permanently erases ALL local data: the metadata cache, "
            "poster images, watchlist, watched history, ratings, and "
            "collection. This action cannot be undone.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("delete", "Delete")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", self._on_delete_db_response)
        dialog.present(self)

    def _on_delete_db_response(self, dialog, response):
        if response == "delete":
            self.close()
            app = self.win.get_application()
            if app is not None:
                app.delete_local_database()
            else:
                poster_cache.clear()
            self.add_toast(Adw.Toast.new("Local database deleted"))

    def _update_cache_size(self):
        self.clear_cache_size_label.set_text("…")

        def _work():
            size = poster_cache.get_size()
            GLib.idle_add(
                self.clear_cache_size_label.set_text, self._format_size(size)
            )

        threading.Thread(target=_work, daemon=True).start()

    @staticmethod
    def _format_size(num_bytes):
        if num_bytes <= 0:
            return "-"
        size = float(num_bytes)
        for unit in ("B", "KB", "MB", "GB"):
            if size < 1024:
                if unit == "B":
                    return f"{int(size)} {unit}"
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} TB"

    #
    # Handlers
    #

    def _on_export_clicked(self, _btn):
        from ..main import CiakApp
        app = CiakApp.get_default()
        show_export_dialog(self, app._user_repo)

    def _on_import_clicked(self, _btn):
        from ..main import CiakApp
        app = CiakApp.get_default()
        show_import_dialog(
            self, app._user_repo, app._metadata_service, self._main_page
        )

    def _on_region_changed(self, row, _gparam):
        idx = row.get_selected()
        if 0 <= idx < len(_REGION_LABELS):
            self._settings.set_string("streaming-region", _REGION_LABELS[idx])

    def _on_sidebar_mode_changed(self, row, _gparam):
        value = "collapse" if row.get_selected() == 0 else "remember"
        self._settings.set_string("sidebar-default-mode", value)

    def _on_theme_changed(self, row, _gparam):
        value = THEME_VALUES[row.get_selected()]
        self._settings.set_string("theme", value)
        apply_theme(self._settings)

    def _on_default_page_changed(self, row, _gparam):
        page_id = DEFAULT_PAGES[row.get_selected()][0]
        self._settings.set_string("default-page", page_id)

    def _on_notify_scope_changed(self, row, _gparam):
        value = "selected" if row.get_selected() == 1 else "all"
        self._settings.set_string("notification-scope", value)

    def _on_tmdb_key_changed(self, row):
        self._settings.set_string("tmdb-api-key", row.get_text())

    def _on_swap_sections_changed(self, row, _pspec):
        self._settings.set_boolean("swap-sections", row.get_active())
        self._restart_app()

    def _on_hide_shows_no_upcoming_toggled(self, row, _gparam):
        self._settings.set_boolean(
            "hide-shows-no-upcoming-episodes", row.get_active()
        )
        self._hide_shows_window_row.set_sensitive(row.get_active())

    def _on_hide_shows_window_changed(self, row, _gparam):
        self._settings.set_int("hide-shows-upcoming-window", int(row.get_value()))

    def _restart_app(self):
        if getattr(self, "_restarting", False):
            return
        self._restarting = True
        try:
            GLib.spawn_command_line_async(
                "sh -c 'sleep 0.4; exec /app/bin/ciak'"
            )
        except GLib.Error:
            pass
        app = self.win.get_application()
        if app is not None:
            app.quit()

    def _on_relaunch_onboarding_clicked(self, _row):
        self._settings.set_boolean("onboarding-completed", False)
        self.close()
        app = self.win.get_application()
        if app is not None:
            app.relaunch_onboarding()

    # ------------------------------------------------------------------
    # Cloud Sync — Auth dialogs
    # ------------------------------------------------------------------

    def _repo(self):
        app = self.win.get_application() if hasattr(self.win, "get_application") else None
        return getattr(app, "_user_repo", None) if app else None

    def _on_tmdb_sync_clicked(self, _row):
        if load_credential("tmdb", "session_id"):
            self._show_disconnect_dialog("tmdb", "TMDB", "Watchlist + ratings")
            return
        self._show_connect_dialog_tmdb()

    def _on_simkl_sync_clicked(self, _row):
        if load_credential("simkl", "access_token"):
            self._show_disconnect_dialog(
                "simkl", "Simkl",
                "Full sync — watchlist, watched, ratings, collection",
            )
            return
        self._show_connect_dialog_simkl()

    def _on_letterboxd_sync_clicked(self, _row):
        if load_credential("letterboxd", "session_cookie"):
            self._show_disconnect_dialog(
                "letterboxd", "Letterboxd",
                "Experimental — watchlist + ratings",
            )
            return
        self._show_connect_dialog_letterboxd()

    def _on_sync_switch_toggled(self, switch, _pspec, backend_name):
        if not getattr(self, "_sync_handlers_connected", False):
            return
        if not switch.get_active():
            delete_service(backend_name)
            self._update_sync_subtitle(backend_name)
            self._auto_disable_master_if_empty()
            self._refresh_sidebar_services()
            return
        row = getattr(self, f"_sync_{backend_name}_row")
        if backend_name == "tmdb":
            self._on_tmdb_sync_clicked(row)
        elif backend_name == "simkl":
            self._on_simkl_sync_clicked(row)
        elif backend_name == "letterboxd":
            self._on_letterboxd_sync_clicked(row)

    def _update_sync_subtitle(self, backend_name):
        row = getattr(self, f"_sync_{backend_name}_row")
        if backend_name == "tmdb":
            if load_credential("tmdb", "session_id"):
                row.set_subtitle("Connected")
                row.add_css_class("accent")
            else:
                row.set_subtitle("Watchlist + ratings")
                row.remove_css_class("accent")
        elif backend_name == "simkl":
            if load_credential("simkl", "access_token"):
                row.set_subtitle("Connected")
                row.add_css_class("accent")
            else:
                row.set_subtitle("Full sync — watchlist, watched, ratings, collection")
                row.remove_css_class("accent")
        elif backend_name == "letterboxd":
            if load_credential("letterboxd", "session_cookie"):
                row.set_subtitle("Connected")
                row.add_css_class("accent")
            else:
                row.set_subtitle("Experimental — watchlist + ratings")
                row.remove_css_class("accent")

    def _update_sync_last_label(self):
        import time as _time
        ts = self._settings.get_int64("sync-last-sync")
        if ts == 0:
            self._sync_last_label.set_label("Never synced")
        else:
            diff = int(_time.time()) - ts
            if diff < 60:
                self._sync_last_label.set_label("Synced just now")
            elif diff < 3600:
                mins = diff // 60
                self._sync_last_label.set_label(
                    f"Synced {mins} minute{'s' if mins != 1 else ''} ago"
                )
            elif diff < 86400:
                hours = diff // 3600
                self._sync_last_label.set_label(
                    f"Synced {hours} hour{'s' if hours != 1 else ''} ago"
                )
            else:
                 days = diff // 86400
                 self._sync_last_label.set_label(
                     f"Synced {days} day{'s' if days != 1 else ''} ago"
                 )
        self._refresh_reset_row()

    def _show_disconnect_dialog(self, backend_name, display_name, original_subtitle):
        dialog = Adw.AlertDialog.new(
            f"Disconnect {display_name}?",
            f"This will stop syncing with {display_name}.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("disconnect", "Disconnect")
        dialog.set_response_appearance(
            "disconnect", Adw.ResponseAppearance.DESTRUCTIVE
        )
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def _on_response(_d, response):
            if response == "disconnect":
                delete_service(backend_name)
                self._update_sync_subtitle(backend_name)
                self.add_toast(Adw.Toast.new(f"{display_name} disconnected"))
                # Turn off the switch without re-triggering auth
                sw = getattr(self, f"_sync_{backend_name}_switch", None)
                if sw is not None:
                    sw.handler_block_by_func(self._on_sync_switch_toggled)
                    sw.set_active(False)
                    sw.handler_unblock_by_func(self._on_sync_switch_toggled)
                self._auto_disable_master_if_empty()
                self._refresh_sidebar_services()

        dialog.connect("response", _on_response)
        dialog.present(self)

    def _trigger_sync(self):
        import logging
        _log = logging.getLogger(__name__)
        try:
            app = self.win.get_application() if hasattr(self, "win") else None
            if not app or not hasattr(app, "_sync_engine"):
                return
            engine = app._sync_engine
            if engine is None:
                return
            main_page = getattr(self.win, "_page", None)
            if main_page is not None and hasattr(main_page, "_show_sync_deletion_dialog"):
                deletions = engine.check_push_deletions()
                if deletions:
                    main_page._show_sync_deletion_dialog(deletions)
                    return
            engine.sync_now()
        except Exception:
            _log.exception("Preferences sync trigger failed")

    def _refresh_sidebar_services(self):
        app = self.win.get_application() if hasattr(self, "win") else None
        if app and hasattr(self.win, "_main_page"):
            self.win._main_page.update_connected_services()

    def _ensure_sync_enabled(self):
        if not self._settings.get_boolean("sync-enabled"):
            self._settings.set_boolean("sync-enabled", True)

    def _enable_per_backend(self, backend_name):
        """Enable the per-backend GSettings key and switch after auth success."""
        self._settings.set_boolean(f"sync-{backend_name}-enabled", True)
        sw = getattr(self, f"_sync_{backend_name}_switch", None)
        if sw is not None:
            sw.handler_block_by_func(self._on_sync_switch_toggled)
            sw.set_active(True)
            sw.handler_unblock_by_func(self._on_sync_switch_toggled)

    def _any_backend_connected(self):
        return any(
            load_credential(name, key) is not None
            for name, key in [
                ("tmdb", "session_id"),
                ("simkl", "access_token"),
                ("letterboxd", "session_cookie"),
            ]
        )

    def _auto_disable_master_if_empty(self):
        if not self._any_backend_connected():
            self._settings.set_boolean("sync-enabled", False)

    def _show_sync_error(self, message):
        if not hasattr(self, "_sync_dialog") or self._sync_dialog is None:
            return
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_valign(Gtk.Align.CENTER)
        box.set_margin_top(24)
        box.set_margin_bottom(24)
        box.set_margin_start(24)
        box.set_margin_end(24)

        err_icon = Gtk.Image(icon_name="dialog-warning-symbolic")
        err_icon.set_pixel_size(48)
        err_icon.add_css_class("dimmed")
        box.append(err_icon)

        lbl = Gtk.Label(label=message)
        lbl.set_wrap(True)
        lbl.set_justify(Gtk.Justification.CENTER)
        box.append(lbl)

        btn_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        btn_box.set_halign(Gtk.Align.CENTER)

        retry_btn = Gtk.Button(label="Try Again")
        retry_btn.add_css_class("suggested-action")
        retry_btn.connect("clicked", lambda _b: self._sync_dialog.close())
        btn_box.append(retry_btn)

        cancel_btn = Gtk.Button(label="Cancel")
        cancel_btn.add_css_class("flat")
        cancel_btn.connect("clicked", lambda _b: self._sync_dialog.close())
        btn_box.append(cancel_btn)

        box.append(btn_box)

        self._sync_dialog.set_child(box)

    # ---- TMDB auth dialog ----

    def _show_connect_dialog_tmdb(self):
        self._sync_dialog = Adw.Dialog()
        self._sync_dialog.set_title("Connect to TMDB")
        self._sync_dialog.set_content_width(400)
        self._sync_dialog.set_presentation_mode(
            Adw.DialogPresentationMode.FLOATING
        )

        # Phase 1: loading
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_valign(Gtk.Align.CENTER)
        box.set_margin_top(24)
        box.set_margin_bottom(24)
        box.set_margin_start(24)
        box.set_margin_end(24)

        step_lbl = Gtk.Label(label="Step 1 of 2")
        step_lbl.add_css_class("caption")
        step_lbl.add_css_class("dimmed")
        box.append(step_lbl)

        spinner = Adw.Spinner()
        spinner.set_size_request(48, 48)
        box.append(spinner)
        lbl = Gtk.Label(label="Requesting token from TMDB…")
        lbl.add_css_class("dimmed")
        box.append(lbl)
        self._sync_dialog.set_child(box)
        self._sync_dialog.present(self)

        api_key = (
            self._settings.get_string("tmdb-api-key")
            or config.DEFAULT_TMDB_API_KEY
        )
        self._tmdb_backend = TmdbSyncBackend(api_key)

        def _work():
            return self._tmdb_backend.request_token()

        def _on_code(future):
            GLib.idle_add(self._on_tmdb_token_received, future)

        from .. import threads
        threads.submit(_work).add_done_callback(_on_code)

    def _on_tmdb_token_received(self, future):
        try:
            token = future.result()
        except Exception:
            self._show_sync_error("Could not connect to TMDB")
            return

        # Phase 2: token display with auto-poll
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_valign(Gtk.Align.CENTER)
        box.set_margin_top(24)
        box.set_margin_bottom(24)
        box.set_margin_start(24)
        box.set_margin_end(24)

        step_lbl = Gtk.Label(label="Step 2 of 2")
        step_lbl.add_css_class("caption")
        step_lbl.add_css_class("dimmed")
        box.append(step_lbl)

        token_lbl = Gtk.Label(label=token)
        token_lbl.add_css_class("title-4")
        token_lbl.set_selectable(True)
        box.append(token_lbl)

        hint = Gtk.Label(
            label="A browser window will open.\n"
            "Approve the request to continue."
        )
        hint.add_css_class("dimmed")
        hint.set_justify(Gtk.Justification.CENTER)
        hint.set_wrap(True)
        box.append(hint)

        # Auto-polling indicator
        waiting_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        waiting_box.set_halign(Gtk.Align.CENTER)
        small_spinner = Adw.Spinner()
        small_spinner.set_size_request(20, 20)
        waiting_box.append(small_spinner)
        wait_lbl = Gtk.Label(label="Waiting for approval…")
        wait_lbl.add_css_class("dimmed")
        waiting_box.append(wait_lbl)
        box.append(waiting_box)

        self._sync_dialog.set_child(box)

        # Auto-open browser and start polling
        Gio.app_info_launch_default_for_uri(
            f"https://www.themoviedb.org/authenticate/{token}", None
        )

        def _poll():
            return self._tmdb_backend.finalize_session(token)

        def _on_poll(future):
            GLib.idle_add(self._on_tmdb_auth_done, future)

        from .. import threads
        threads.submit(_poll).add_done_callback(_on_poll)

    def _on_tmdb_auth_done(self, future):
        success = future.result()
        if success:
            self._sync_dialog.close()
            self.add_toast(Adw.Toast.new("TMDB connected"))
            self._update_sync_subtitle("tmdb")
            self._enable_per_backend("tmdb")
            self._ensure_sync_enabled()
            self._refresh_sidebar_services()
            self._trigger_sync()
        else:
            self._show_sync_error("TMDB connection failed. Try again.")

    # ---- Simkl auth dialog ----

    def _show_connect_dialog_simkl(self):
        self._sync_dialog = Adw.Dialog()
        self._sync_dialog.set_title("Connect to Simkl")
        self._sync_dialog.set_content_width(400)
        self._sync_dialog.set_presentation_mode(
            Adw.DialogPresentationMode.FLOATING
        )

        # Phase 1: loading
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_valign(Gtk.Align.CENTER)
        box.set_margin_top(24)
        box.set_margin_bottom(24)
        box.set_margin_start(24)
        box.set_margin_end(24)

        step_lbl = Gtk.Label(label="Step 1 of 2")
        step_lbl.add_css_class("caption")
        step_lbl.add_css_class("dimmed")
        box.append(step_lbl)

        spinner = Adw.Spinner()
        spinner.set_size_request(48, 48)
        box.append(spinner)
        lbl = Gtk.Label(label="Requesting code from Simkl…")
        lbl.add_css_class("dimmed")
        box.append(lbl)
        self._sync_dialog.set_child(box)
        self._sync_dialog.present(self)

        client_id = (
            load_credential("simkl", "client_id")
            or config.DEFAULT_SIMKL_CLIENT_ID
        )
        self._simkl_backend = SimklSyncBackend(client_id)

        def _work():
            return self._simkl_backend.request_code()

        def _on_code(future):
            GLib.idle_add(self._on_simkl_code_received, future)

        from .. import threads
        threads.submit(_work).add_done_callback(_on_code)

    def _on_simkl_code_received(self, future):
        try:
            code_data = future.result()
        except Exception:
            self._show_sync_error("Could not connect to Simkl")
            return

        user_code = code_data["user_code"]
        interval = code_data.get("interval", 5)
        expires_in = code_data.get("expires_in", 600)

        # Phase 2: code display
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_valign(Gtk.Align.CENTER)
        box.set_margin_top(24)
        box.set_margin_bottom(24)
        box.set_margin_start(24)
        box.set_margin_end(24)

        step_lbl = Gtk.Label(label="Step 2 of 2")
        step_lbl.add_css_class("caption")
        step_lbl.add_css_class("dimmed")
        box.append(step_lbl)

        code_lbl = Gtk.Label(label=user_code)
        code_lbl.add_css_class("title-1")
        code_lbl.set_selectable(True)
        box.append(code_lbl)

        hint = Gtk.Label(
            label="Go to simkl.com/pin\nand enter this code"
        )
        hint.add_css_class("dimmed")
        hint.set_justify(Gtk.Justification.CENTER)
        hint.set_wrap(True)
        box.append(hint)

        open_btn = Gtk.Button(label="Open simkl.com/pin")
        open_btn.add_css_class("flat")
        open_btn.set_halign(Gtk.Align.CENTER)
        open_btn.connect(
            "clicked",
            lambda _b: Gio.app_info_launch_default_for_uri(
                "https://simkl.com/pin", None
            ),
        )
        box.append(open_btn)

        waiting_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        waiting_row.set_halign(Gtk.Align.CENTER)
        small_spinner = Adw.Spinner()
        small_spinner.set_size_request(20, 20)
        waiting_row.append(small_spinner)
        wait_lbl = Gtk.Label(label="Waiting for approval…")
        wait_lbl.add_css_class("dimmed")
        waiting_row.append(wait_lbl)
        box.append(waiting_row)

        self._sync_dialog.set_child(box)

        def _poll():
            return self._simkl_backend.poll_and_finalize(
                user_code, interval, expires_in
            )

        def _on_poll(future):
            GLib.idle_add(self._on_simkl_auth_done, future)

        from .. import threads
        threads.submit(_poll).add_done_callback(_on_poll)

    def _on_simkl_auth_done(self, future):
        try:
            success = future.result()
        except Exception as exc:
            self._show_sync_error(f"Simkl connection failed: {exc}")
            return
        if success:
            self._sync_dialog.close()
            self.add_toast(Adw.Toast.new("Simkl connected"))
            self._update_sync_subtitle("simkl")
            self._enable_per_backend("simkl")
            self._ensure_sync_enabled()
            self._refresh_sidebar_services()
            self._trigger_sync()
        else:
            self._show_sync_error("Simkl approval timed out. Try again.")

    # ---- Letterboxd auth dialog ----

    def _show_connect_dialog_letterboxd(self):
        dialog = Adw.AlertDialog.new(
            "Connect to Letterboxd",
            "Experimental — may break if Letterboxd changes their site.\n"
            "Enter your Letterboxd credentials.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("connect", "Connect")
        dialog.set_response_appearance(
            "connect", Adw.ResponseAppearance.SUGGESTED
        )

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        username_entry = Gtk.Entry()
        username_entry.set_placeholder_text("Username")
        password_entry = Gtk.PasswordEntry()
        password_entry.set_placeholder_text("Password")
        password_entry.set_show_peek_icon(True)
        box.append(username_entry)
        box.append(password_entry)
        dialog.set_extra_child(box)

        def _on_connect(_d, response):
            if response != "connect":
                return
            user = username_entry.get_text().strip()
            pwd = password_entry.get_text()
            if not user or not pwd:
                self.add_toast(Adw.Toast.new("Username and password required"))
                return
            store_credential("letterboxd", "username", user)
            store_credential("letterboxd", "password", pwd)
            self._start_letterboxd_auth(dialog)

        dialog.connect("response", _on_connect)
        dialog.present(self)

    def _start_letterboxd_auth(self, dialog):
        def _work():
            backend = LetterboxdSyncBackend()
            return backend.authenticate(None)

        def _done(future):
            success = future.result()
            if success:
                dialog.close()
                self.add_toast(Adw.Toast.new("Letterboxd connected"))
                self._update_sync_subtitle("letterboxd")
                self._enable_per_backend("letterboxd")
                self._ensure_sync_enabled()
                self._refresh_sidebar_services()
                self._trigger_sync()
            else:
                self.add_toast(Adw.Toast.new("Letterboxd login failed"))

        from .. import threads
        threads.submit(_work).add_done_callback(
            lambda f: GLib.idle_add(_done, f)
        )

    #
    # About
    #

    def _build_about_page(self):
        page = Adw.PreferencesPage()
        page.set_name("about")
        page.set_title("About")
        page.set_icon_name("help-about-symbolic")
        self.add(page)

        header_group = Adw.PreferencesGroup()
        page.add(header_group)

        header = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        header.set_margin_top(28)
        header.set_margin_bottom(28)
        header.set_margin_start(16)
        header.set_margin_end(16)

        icon = Gtk.Image(icon_name=config.APP_ID)
        icon.set_pixel_size(128)
        icon.set_halign(Gtk.Align.CENTER)
        icon.add_css_class("about-icon")
        header.append(icon)

        name = Gtk.Label(label="Ciak")
        name.add_css_class("title-1")
        name.set_halign(Gtk.Align.CENTER)
        name.set_margin_top(12)
        header.append(name)

        version = Gtk.Label(label=f"Version {config.APP_VERSION}")
        version.add_css_class("dimmed")
        version.set_halign(Gtk.Align.CENTER)
        header.append(version)

        header_group.add(header)

        info_group = Adw.PreferencesGroup()
        page.add(info_group)

        description_row = Adw.ActionRow()
        description_row.set_title("A GTK app to track movies and TV shows")
        info_group.add(description_row)

        developer_row = Adw.ActionRow()
        developer_row.set_title("Developer")
        developer_row.set_subtitle("Andrea Scaperrotta")
        info_group.add(developer_row)

        donate_row = Adw.ActionRow()
        donate_row.set_title("Donate")
        donate_row.set_activatable(True)
        donate_row.connect(
            "activated",
            lambda _r: Gio.app_info_launch_default_for_uri(
                "https://buymeacoffee.com/ascape", None
            ),
        )
        donate_row.add_suffix(
            Gtk.Image.new_from_icon_name("web-browser-symbolic")
        )
        info_group.add(donate_row)

        website_row = Adw.ActionRow()
        website_row.set_title("Website")
        website_row.set_activatable(True)
        website_row.connect(
            "activated",
            lambda _r: Gio.app_info_launch_default_for_uri(
                "https://github.com/andrea-scape/ciak", None
            ),
        )
        website_row.add_suffix(
            Gtk.Image.new_from_icon_name("web-browser-symbolic")
        )
        info_group.add(website_row)

        powered_row = Adw.ActionRow()
        powered_row.set_title("Powered by TMDB")
        powered_row.set_activatable(True)
        powered_row.connect(
            "activated",
            lambda _r: Gio.app_info_launch_default_for_uri(
                "https://www.themoviedb.org", None
            ),
        )
        powered_row.add_suffix(
            Gtk.Image.new_from_icon_name("web-browser-symbolic")
        )
        info_group.add(powered_row)
