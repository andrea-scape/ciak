import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gtk, Adw, GLib, Gio, GdkPixbuf
import getpass
import threading
import urllib.request
import urllib.error
import tempfile
import os

from ..domain.exceptions import NetworkError
from .. import config
from .. import poster_cache
from .search_page import SearchPage
from .watchlist_page import WatchlistPage
from .history_page import HistoryPage
from .calendar_page import CalendarPage
from .profile_page import ProfilePage
from .detail_page import DetailPage
from .preferences_page import PreferencesPage
from .anim import fade_out_group, fade_in_group


PAGE_TITLES = {
    "search": "Search",
    "watchlist": "Watchlist",
    "history": "History",
    "calendar": "Calendar",
    "profile": getpass.getuser() or "Profile",
}

PAGES_WITH_TOGGLE = {
    "watchlist",
    "history",
}


class MainPage(Adw.Bin):
    def __init__(self, win, user_repo, metadata_service):
        super().__init__()
        self.win = win
        self.user_repo = user_repo
        self.metadata_service = metadata_service

        split_view = Adw.OverlaySplitView()
        split_view.set_collapsed(False)
        split_view.set_pin_sidebar(True)
        split_view.set_min_sidebar_width(144)
        split_view.set_max_sidebar_width(208)

        self._sidebar_page = self._build_sidebar_page()
        split_view.set_sidebar(self._sidebar_page)

        content_page = self._build_content_page()
        split_view.set_content(content_page)

        self.set_child(split_view)

        self._split_view = split_view
        if not hasattr(self.win, "settings"):
            self.win.settings = Gio.Settings.new(config.APP_ID)
        self._setup_sidebar_visibility()
        self._setup_window_actions()

        self._pages = {}
        self._stale_pages = set()
        self._current_page = None
        self._previous_page = None
        self._previous_main_page = None
        self._global_mode = "all"
        self._detail_page = None
        self._detail_open = False
        self._current_detail_name = "detail_0"
        self._pending_detail_removal_id = None
        self._pending_headerbar_sync_id = None
        self._headerbar_generation = 0

        default_page = self.win.settings.get_string("default-page")
        if default_page not in PAGE_TITLES:
            default_page = "watchlist"
        self._select_page(default_page)

    def _setup_window_actions(self):
        preferences_action = Gio.SimpleAction.new("preferences", None)
        preferences_action.connect("activate", self._on_preferences_activated)
        self.win.add_action(preferences_action)

        toggle_sidebar_action = Gio.SimpleAction.new("toggle-sidebar", None)
        toggle_sidebar_action.connect("activate", self._on_toggle_sidebar_menu)
        self.win.add_action(toggle_sidebar_action)

        about_action = Gio.SimpleAction.new("about", None)
        about_action.connect("activate", self._on_about_activated)
        self.win.add_action(about_action)

        open_watchlist_action = Gio.SimpleAction.new("open-watchlist", None)
        open_watchlist_action.connect("activate", self._on_open_watchlist_activated)
        self.win.add_action(open_watchlist_action)

        open_search_action = Gio.SimpleAction.new("open-search", None)
        open_search_action.connect("activate", self._on_open_search_activated)
        self.win.add_action(open_search_action)

        self._shortcut_actions = {
            "preferences": "<Control>comma",
            "toggle-sidebar": "<Control>s",
            "open-watchlist": "<Control>w",
            "open-search": "<Control>f",
        }
        self._shortcut_settings_keys = {
            "preferences": "shortcut-preferences-enabled",
            "toggle-sidebar": "shortcut-sidebar-enabled",
            "open-watchlist": "shortcut-watchlist-enabled",
            "open-search": "shortcut-search-enabled",
        }
        self._update_accels()
        self._win_settings_changed_id_accels = []
        for action_name, key in self._shortcut_settings_keys.items():
            sig_id = self.win.settings.connect(
                f"changed::{key}",
                lambda s, k: self._update_accels(),
            )
            self._win_settings_changed_id_accels.append(sig_id)

    def _update_accels(self):
        app = self.win.get_application()
        if app is None:
            return
        for action_name, accel in self._shortcut_actions.items():
            key = self._shortcut_settings_keys[action_name]
            if self.win.settings.get_boolean(key):
                app.set_accels_for_action(f"win.{action_name}", [accel])
            else:
                app.set_accels_for_action(f"win.{action_name}", [])

    def _finish_back_state(self, target, delay=150):
        self._current_page = target
        self._previous_page = "detail"

        if target == "search":
            page = self._pages.get("search")
            if page:
                page.play_entrance()
        self._select_sidebar_row(target)

        self._cancel_headerbar_sync()
        self._pending_headerbar_sync_id = GLib.timeout_add(
            delay, self._sync_headerbar, target
        )

    def _close_detail(self):
        detail = self._detail_page
        if detail is None:
            return
        self._detail_page = None
        self._detail_open = False
        if hasattr(detail, "cancel"):
            detail.cancel()
        parent = detail.get_parent()
        if parent is not None:
            parent.remove(detail)

    def _navigate_to(self, page_id):
        self._close_detail()
        self._select_page(page_id)

    def _on_open_watchlist_activated(self, _action, _param):
        self._navigate_to("watchlist")

    def _on_open_search_activated(self, _action, _param):
        self._navigate_to("search")

    def _present_preferences(self, page_name=None):
        dialog = getattr(self, "_prefs_dialog", None)
        if dialog is not None and dialog.get_visible():
            if page_name:
                dialog.set_visible_page_name(page_name)
            dialog.present(self.win)
            return
        dialog = PreferencesPage(self.win)
        self._prefs_dialog = dialog
        dialog.connect("closed", self._on_prefs_dialog_closed)
        if page_name:
            dialog.set_visible_page_name(page_name)
        dialog.present(self.win)

    def _on_prefs_dialog_closed(self, dialog):
        if getattr(self, "_prefs_dialog", None) is dialog:
            self._prefs_dialog = None

    def _on_preferences_activated(self, _action, _param):
        self._present_preferences()

    def _on_about_activated(self, _action, _param):
        self._present_preferences("about")

    def _on_toggle_sidebar_menu(self, _action, _param):
        self._apply_sidebar_visibility(not self._sidebar_visible)
        self.win.settings.set_boolean("show-sidebar", self._sidebar_visible)

    def _setup_sidebar_visibility(self):
        mode = self.win.settings.get_string("sidebar-default-mode")
        if mode == "collapse":
            self._sidebar_visible = False
        else:
            self._sidebar_visible = self.win.settings.get_boolean("show-sidebar")

        self._sidebar_action = Gio.SimpleAction.new_stateful(
            "show-sidebar",
            None,
            GLib.Variant.new_boolean(self._sidebar_visible),
        )
        self._sidebar_action.connect("activate", self._on_toggle_sidebar)
        self.win.add_action(self._sidebar_action)

        self._apply_sidebar_visibility(self._sidebar_visible)
        self._win_settings_changed_id = self.win.settings.connect(
            "changed::show-sidebar", self._on_settings_sidebar_changed
        )
        self.win.settings.connect(
            "changed::sidebar-default-mode",
            lambda s, k: self._on_default_mode_changed(),
        )

    def _on_default_mode_changed(self):
        mode = self.win.settings.get_string("sidebar-default-mode")
        if mode == "collapse":
            self._apply_sidebar_visibility(False)
        else:
            saved = self.win.settings.get_boolean("show-sidebar")
            self._apply_sidebar_visibility(saved)

    def _on_settings_sidebar_changed(self, settings, key):
        visible = settings.get_boolean(key)
        self._sidebar_action.set_state(GLib.Variant.new_boolean(visible))
        self._apply_sidebar_visibility(visible)

    def _on_toggle_sidebar(self, action, _param):
        current = action.get_state().get_boolean()
        visible = not current
        action.set_state(GLib.Variant.new_boolean(visible))
        self.win.settings.set_boolean("show-sidebar", visible)
        self._apply_sidebar_visibility(visible)

    def _apply_sidebar_visibility(self, visible):
        self._sidebar_visible = visible
        self._split_view.set_show_sidebar(visible)
        self._show_sidebar_btn.set_visible(not visible)

    def _build_sidebar_page(self):
        sidebar_tv = Adw.ToolbarView()

        sidebar_header = Adw.HeaderBar()

        self._hide_sidebar_btn = Gtk.Button(icon_name="sidebar-show-symbolic")
        self._hide_sidebar_btn.set_tooltip_text("Hide sidebar")
        self._hide_sidebar_btn.set_action_name("win.show-sidebar")
        sidebar_header.pack_end(self._hide_sidebar_btn)

        app_label = Gtk.Label(label="Ciak")
        app_label.add_css_class("title-2")
        sidebar_header.set_title_widget(app_label)

        menu_model = Gio.Menu()
        menu_model.append("Hide Sidebar", "win.toggle-sidebar")
        menu_model.append("Preferences", "win.preferences")
        menu_model.append("About Ciak", "win.about")
        menu_btn = Gtk.MenuButton(icon_name="open-menu-symbolic")
        menu_btn.set_menu_model(menu_model)
        sidebar_header.pack_start(menu_btn)

        sidebar_tv.add_top_bar(sidebar_header)

        flatpak_id = os.environ.get("FLATPAK_ID", config.APP_ID)
        if flatpak_id.endswith(".Devel"):
            dev_icon = Gtk.Image(icon_name="utilities-terminal-symbolic")
            dev_icon.add_css_class("dev-chip-icon")
            dev_label = Gtk.Label(label="Development")
            dev_label.add_css_class("dev-chip")
            dev_pill = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            dev_pill.set_halign(Gtk.Align.CENTER)
            dev_pill.append(dev_icon)
            dev_pill.append(dev_label)
            dev_pill.add_css_class("dev-chip-pill")
            dev_chip_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            dev_chip_box.append(dev_pill)
            sidebar_tv.add_bottom_bar(dev_chip_box)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        sidebar_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        self.list_box = Gtk.ListBox()
        self.list_box.add_css_class("navigation-sidebar")
        self.list_box.set_selection_mode(Gtk.SelectionMode.SINGLE)

        items = [
            ("search", "Search &amp; Discover", "system-search-symbolic"),
            ("watchlist", "Watchlist", "view-grid-symbolic"),
            ("history", "History", "document-open-recent-symbolic"),
            ("calendar", "Calendar", "x-office-calendar-symbolic"),
        ]

        for page_id, label, icon_name in items:
            row = Adw.ActionRow(title=label)
            row.add_prefix(Gtk.Image.new_from_icon_name(icon_name))
            row._page_id = page_id
            self.list_box.append(row)

        self.list_box.connect("row-selected", self._on_row_selected)
        sidebar_box.append(self.list_box)

        sidebar_box.append(Gtk.Separator(margin_top=6, margin_bottom=6))

        self.profile_list_box = Gtk.ListBox()
        self.profile_list_box.add_css_class("navigation-sidebar")
        self.profile_list_box.set_selection_mode(Gtk.SelectionMode.SINGLE)

        profile_items = [
            ("profile", "Gallery", "avatar-default-symbolic"),
        ]

        username = getpass.getuser()
        for page_id, label, icon_name in profile_items:
            row = Adw.ActionRow(title=username if username else "Profile")
            row.add_prefix(Gtk.Image.new_from_icon_name(icon_name))
            row._page_id = page_id
            self.profile_list_box.append(row)

        self.profile_list_box.connect("row-selected", self._on_row_selected)
        sidebar_box.append(self.profile_list_box)

        scroll.set_child(sidebar_box)
        sidebar_tv.set_content(scroll)

        sidebar_page = Adw.NavigationPage(title="Sidebar")
        sidebar_page.set_child(sidebar_tv)
        return sidebar_page

    def _build_content_page(self):
        content_tv = Adw.ToolbarView()
        content_tv.set_top_bar_style(Adw.ToolbarStyle.FLAT)

        self.content_header = Adw.HeaderBar()

        self._show_sidebar_btn = Gtk.ToggleButton(
            icon_name="sidebar-show-symbolic"
        )
        self._show_sidebar_btn.set_tooltip_text("Show sidebar")
        self._show_sidebar_btn.set_action_name("win.show-sidebar")
        self.content_header.pack_start(self._show_sidebar_btn)

        self.back_btn = Gtk.Button(icon_name="go-previous-symbolic")
        self.back_btn.connect("clicked", lambda b: self.go_back())
        self.back_btn.set_visible(False)
        self.content_header.pack_start(self.back_btn)

        self.content_title_widget = Adw.WindowTitle(title="", subtitle="")
        self.content_header.set_title_widget(self.content_title_widget)

        self.toggle_box = Gtk.Box(spacing=3)

        self.all_toggle = Gtk.ToggleButton()
        self.all_toggle.add_css_class("view-pill")
        all_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        all_box.append(Gtk.Image.new_from_icon_name("view-paged-symbolic"))
        all_box.append(Gtk.Label(label="All"))
        self.all_toggle.set_child(all_box)

        self.movie_toggle = Gtk.ToggleButton()
        self.movie_toggle.add_css_class("view-pill")
        movie_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        movie_box.append(Gtk.Image.new_from_icon_name("video-x-generic-symbolic"))
        movie_box.append(Gtk.Label(label="Movies"))
        self.movie_toggle.set_child(movie_box)
        self.movie_toggle.set_group(self.all_toggle)
        self.movie_toggle.set_active(True)

        self.show_toggle = Gtk.ToggleButton()
        self.show_toggle.add_css_class("view-pill")
        show_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        show_box.append(Gtk.Image.new_from_icon_name("tv-symbolic"))
        show_box.append(Gtk.Label(label="Shows"))
        self.show_toggle.set_child(show_box)
        self.show_toggle.set_group(self.all_toggle)

        self.all_toggle.connect("toggled", self._on_toggle_changed)
        self.movie_toggle.connect("toggled", self._on_toggle_changed)
        self.show_toggle.connect("toggled", self._on_toggle_changed)
        self.toggle_box.append(self.all_toggle)
        self.toggle_box.append(self.movie_toggle)
        self.toggle_box.append(self.show_toggle)
        self.content_header.pack_end(self.toggle_box)
        self.toggle_box.set_visible(False)

        content_tv.add_top_bar(self.content_header)

        self.content_stack = Gtk.Stack()
        self.content_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.content_stack.set_transition_duration(150)
        self.content_stack.set_hexpand(True)
        self.content_stack.set_vexpand(True)
        content_tv.set_content(self.content_stack)

        content_page = Adw.NavigationPage(title="Content")
        content_page.set_child(content_tv)
        return content_page

    def _on_toggle_changed(self, btn):
        if self.all_toggle.get_active():
            mode = "all"
        elif self.movie_toggle.get_active():
            mode = "movies"
        else:
            mode = "shows"
        self._global_mode = mode
        for page in self._pages.values():
            if hasattr(page, "_set_mode"):
                page._set_mode(mode)

    def _sync_toggle(self, mode):
        self.all_toggle.handler_block_by_func(self._on_toggle_changed)
        self.movie_toggle.handler_block_by_func(self._on_toggle_changed)
        self.show_toggle.handler_block_by_func(self._on_toggle_changed)
        if mode == "all":
            self.all_toggle.set_active(True)
        elif mode == "movies":
            self.movie_toggle.set_active(True)
        else:
            self.show_toggle.set_active(True)
        self.all_toggle.handler_unblock_by_func(self._on_toggle_changed)
        self.movie_toggle.handler_unblock_by_func(self._on_toggle_changed)
        self.show_toggle.handler_unblock_by_func(self._on_toggle_changed)

    def _on_row_selected(self, list_box, row):
        if row is None:
            return
        if list_box is self.list_box:
            self.profile_list_box.unselect_all()
        else:
            self.list_box.unselect_all()
        self._select_page(row._page_id)

    def _cancel_headerbar_sync(self):
        if self._pending_headerbar_sync_id:
            GLib.source_remove(self._pending_headerbar_sync_id)
            self._pending_headerbar_sync_id = None

    def _sync_headerbar(self, page_id):
        self._pending_headerbar_sync_id = None
        self._headerbar_generation += 1
        gen = self._headerbar_generation

        widgets = [
            self.content_title_widget,
            self.back_btn,
            self.toggle_box,
        ]
        currently_visible = [w for w in widgets if w.get_visible()]

        def _apply_and_fade_in():
            if gen != self._headerbar_generation:
                return

            self.content_title_widget.set_title(PAGE_TITLES.get(page_id, page_id))
            self.content_title_widget.set_subtitle("")
            self.content_header.set_title_widget(self.content_title_widget)

            if page_id == "search":
                self.back_btn.set_visible(False)
                self.toggle_box.set_visible(False)
            else:
                self.back_btn.set_visible(False)
                has_toggle = page_id in PAGES_WITH_TOGGLE
                self.toggle_box.set_visible(has_toggle)
                if has_toggle:
                    page = self._pages.get(page_id)
                    if page and hasattr(page, "_mode"):
                        self._sync_toggle(page._mode)

            newly_visible = [w for w in widgets if w.get_visible()]
            fade_in_group(newly_visible, 200)

        if currently_visible:
            fade_out_group(currently_visible, 150, _apply_and_fade_in)
        else:
            _apply_and_fade_in()
        return False

    def _sync_headerbar_detail(self, title):
        self._pending_headerbar_sync_id = None
        self._headerbar_generation += 1
        gen = self._headerbar_generation

        widgets = [
            self.content_title_widget,
            self.back_btn,
            self.toggle_box,
        ]
        currently_visible = [w for w in widgets if w.get_visible()]

        def _apply_and_fade_in():
            if gen != self._headerbar_generation:
                return

            self.content_title_widget.set_title(title)
            self.content_title_widget.set_subtitle("")
            self.content_header.set_title_widget(self.content_title_widget)
            self.toggle_box.set_visible(False)
            self.back_btn.set_visible(True)

            newly_visible = [w for w in widgets if w.get_visible()]
            fade_in_group(newly_visible, 200)

        if currently_visible:
            fade_out_group(currently_visible, 150, _apply_and_fade_in)
        else:
            _apply_and_fade_in()
        return False

    def invalidate_page(self, page_id):
        self._stale_pages.add(page_id)

    def _refresh_if_stale(self, page_id):
        if page_id not in self._stale_pages:
            return
        self._stale_pages.discard(page_id)
        page = self._pages.get(page_id)
        if page is not None and hasattr(page, "_load"):
            page._load()

    def _select_page(self, page_id):
        self._close_detail()
        if page_id not in self._pages:
            self._pages[page_id] = self._create_page(page_id)
            self.content_stack.add_titled(self._pages[page_id], page_id, PAGE_TITLES.get(page_id, page_id))

        self.content_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.content_stack.set_transition_duration(300)
        self.content_stack.set_visible_child_name(page_id)
        self._refresh_if_stale(page_id)

        self._previous_page = self._current_page
        self._current_page = page_id
        if page_id != "detail":
            self._previous_main_page = page_id

        if page_id == "search":
            page = self._pages.get(page_id)
            if page:
                page.play_entrance()
        self._select_sidebar_row(page_id)

        self._cancel_headerbar_sync()
        duration = self.content_stack.get_transition_duration()
        self._pending_headerbar_sync_id = GLib.timeout_add(
            duration, self._sync_headerbar, page_id
        )

    def show_detail(self, media_type, item):
        if self._pending_detail_removal_id:
            GLib.source_remove(self._pending_detail_removal_id)
            self._pending_detail_removal_id = None

        new_name = "detail_1" if self._current_detail_name == "detail_0" else "detail_0"

        stale = self.content_stack.get_child_by_name(new_name)
        if stale:
            if hasattr(stale, "cancel"):
                stale.cancel()
            self.content_stack.remove(stale)

        detail = DetailPage(self.win, self.user_repo, self.metadata_service, media_type, item, self)
        self._detail_page = detail
        self._detail_open = True
        self.content_stack.add_named(detail, new_name)

        self.content_stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT)
        self.content_stack.set_transition_duration(400)
        self.content_stack.set_visible_child_name(new_name)
        self._current_detail_name = new_name

        if self._current_page != "detail":
            self._previous_page = self._current_page
            self._previous_main_page = self._current_page
        self._current_page = "detail"

        self.list_box.unselect_all()
        self.profile_list_box.unselect_all()

        self._cancel_headerbar_sync()
        self._pending_headerbar_sync_id = GLib.timeout_add(
            400, self._sync_headerbar_detail, item.title
        )

        outgoing = self.content_stack.get_child_by_name(
            "detail_1" if new_name == "detail_0" else "detail_0"
        )
        if outgoing:
            if hasattr(outgoing, "cancel"):
                outgoing.cancel()
            self._pending_detail_removal_id = GLib.timeout_add(
                500, self._drop_detail, outgoing
            )

        GLib.Thread.new("detail-prefetch", self._prefetch_detail,
                         media_type, item, detail)

    def _drop_detail(self, detail):
        parent = detail.get_parent()
        if parent:
            parent.remove(detail)
        self._pending_detail_removal_id = None
        return False

    def _prefetch_detail(self, media_type, item, detail_page):
        try:
            hero = {}
            lazy = {}

            if media_type == "movie":
                hero_keys = [
                    ("detail", lambda: self.metadata_service.get_movie(item.tmdb_id)),
                    ("watchlist_ids", lambda: self.user_repo.get_watchlist_ids()),
                    ("watched_ids", lambda: self.user_repo.get_watched_ids("movie")),
                    ("rating", lambda: detail_page._get_my_rating()),
                ]
                lazy_keys = [
                    ("related", lambda: self.metadata_service.get_related_movies(item.tmdb_id)),
                    ("cast", lambda: self.metadata_service.get_movie_cast(item.tmdb_id)),
                ]
            else:
                hero_keys = [
                    ("detail", lambda: self.metadata_service.get_show(item.tmdb_id)),
                    ("seasons", lambda: self.metadata_service.get_show_seasons(item.tmdb_id)),
                    ("progress", lambda: None),
                    ("watchlist_ids", lambda: self.user_repo.get_watchlist_ids()),
                    ("watched_ids", lambda: self.user_repo.get_watched_ids("show")),
                    ("rating", lambda: detail_page._get_my_rating()),
                ]
                lazy_keys = [
                    ("related", lambda: self.metadata_service.get_related_shows(item.tmdb_id)),
                    ("cast", lambda: self.metadata_service.get_show_cast(item.tmdb_id)),
                ]

            self._run_fetch_group(hero, hero_keys)
            detail = hero.get("detail")
            if not detail:
                GLib.idle_add(detail_page._show_error, "Failed to load details. Check your connection.")
                return

            if media_type == "show":
                season_episodes = {}
                from ..threads import submit as _submit_worker

                def _fetch_season(season):
                    if season.season_number <= 0:
                        return season.season_number, []
                    try:
                        return season.season_number, (
                            self.metadata_service.get_season_episodes(
                                item.tmdb_id, season.season_number
                            )
                        )
                    except NetworkError:
                        return season.season_number, []

                futures = [
                    _submit_worker(_fetch_season, season)
                    for season in (hero.get("seasons") or [])
                ]
                for fut in futures:
                    num, eps = fut.result()
                    season_episodes[num] = eps
                hero["season_episodes"] = season_episodes

            poster_pixbuf = None
            if detail.poster_url:
                poster_pixbuf = self._download_texture(detail.poster_url)

            GLib.idle_add(detail_page.populate_hero, hero, poster_pixbuf)

            self._run_fetch_group(lazy, lazy_keys)
            detail_page.populate_related(lazy.get("related", []))
            detail_page.populate_cast(lazy.get("cast", []))

        except NetworkError as e:
            GLib.idle_add(detail_page._show_error, str(e))

    def _run_fetch_group(self, data, key_fns):
        def fetch_one(key, fn):
            try:
                data[key] = fn()
            except Exception:
                data[key] = None if key in ("detail", "progress") else (set() if "ids" in key else [])

        threads = []
        for key, fn in key_fns:
            t = threading.Thread(target=fetch_one, args=(key, fn))
            t.start()
            threads.append(t)
        for t in threads:
            t.join()

    def _download_texture(self, url):
        try:
            cached = poster_cache.get(url)
            if cached:
                pixbuf = GdkPixbuf.Pixbuf.new_from_file(cached)
                return pixbuf
            req = urllib.request.Request(url, headers={"User-Agent": "Ciak/1.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                raw = resp.read()
            poster_cache.put(url, raw)
            tmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
            tmp.write(raw)
            tmp.close()
            pixbuf = GdkPixbuf.Pixbuf.new_from_file(tmp.name)
            try:
                os.unlink(tmp.name)
            except OSError:
                pass
            return pixbuf
        except (urllib.error.URLError, OSError, ValueError, GLib.Error):
            return None

    def go_back(self):
        old = self.content_stack.get_child_by_name(self._current_detail_name)
        if old and hasattr(old, "cancel"):
            old.cancel()
        self.content_stack.set_transition_type(Gtk.StackTransitionType.SLIDE_RIGHT)
        self.content_stack.set_transition_duration(400)

        target = self._previous_main_page or "watchlist"
        if target in self._pages:
            self.content_stack.set_visible_child_name(target)
            self._refresh_if_stale(target)
            self._detail_open = False
            self._detail_page = None
            self._finish_back_state(target, delay=400)
        else:
            self._select_page("watchlist")

        if old:
            GLib.timeout_add(350, self._safe_remove, old)

    def _safe_remove(self, child):
        if child.get_parent() is self.content_stack:
            self.content_stack.remove(child)
        return False

    def _select_sidebar_row(self, page_id):
        for row in self.list_box:
            if hasattr(row, '_page_id') and row._page_id == page_id:
                self.list_box.select_row(row)
                return
        for row in self.profile_list_box:
            if hasattr(row, '_page_id') and row._page_id == page_id:
                self.profile_list_box.select_row(row)
                return

    def _create_page(self, page_id):
        creators = {
            "search": lambda: SearchPage(self.win, self.user_repo, self.metadata_service, self),
            "watchlist": lambda: WatchlistPage(self.win, self.user_repo, self.metadata_service, self),
            "history": lambda: HistoryPage(self.win, self.user_repo, self.metadata_service, self),
            "calendar": lambda: CalendarPage(self.win, self.user_repo, self.metadata_service, self),
            "profile": lambda: ProfilePage(self.win, self.user_repo, self.metadata_service, self),
        }
        creator = creators.get(page_id)
        if creator:
            page = creator()
            if hasattr(page, "_set_mode"):
                page._set_mode(self._global_mode)
            return page
        return Gtk.Label(label=page_id)
