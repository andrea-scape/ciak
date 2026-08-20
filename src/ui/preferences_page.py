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

        startup = Adw.PreferencesGroup()
        startup.set_title("Startup")
        page.add(startup)

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
        shortcut_label.add_css_class("dim-label")
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

        self._build_integrations_group(page)
        self._build_import_export_group(page)
        self._build_cache_group(page)
        self._build_data_management_group(page)
        self._build_setup_group(page)

    def _build_integrations_group(self, page):
        integrations_group = Adw.PreferencesGroup()
        integrations_group.set_title("Integrations")
        integrations_group.set_description(
            "The TMDB API key is required — without it Ciak cannot "
            "search or match titles"
        )
        page.add(integrations_group)

        key_row = Adw.PasswordEntryRow()
        key_row.set_title("TMDB API Key")
        current_key = self._settings.get_string("tmdb-api-key")
        key_row.set_text(current_key)
        key_row.connect("changed", self._on_tmdb_key_changed)
        integrations_group.add(key_row)

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
        integrations_group.add(link_row)

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
        self.clear_cache_size_label.add_css_class("dim-label")
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

    def _on_tmdb_key_changed(self, row):
        self._settings.set_string("tmdb-api-key", row.get_text())

    def _on_relaunch_onboarding_clicked(self, _row):
        self._settings.set_boolean("onboarding-completed", False)
        self.close()
        app = self.win.get_application()
        if app is not None:
            app.relaunch_onboarding()

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
        version.add_css_class("dim-label")
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
