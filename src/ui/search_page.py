import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, GLib

from ..domain.exceptions import NetworkError
from .media_card import config_grid, make_media_card
from .anim import fade_in, fade_out_group, stagger_fade_in


class SearchPage(Adw.Bin):
    """Search page with watchlist-style card results."""

    def __init__(self, win, user_repo, metadata_service, main_page=None):
        super().__init__()
        self.win = win
        self.user_repo = user_repo
        self.metadata_service = metadata_service
        self.main_page = main_page
        self.add_css_class("ciak-dashboard")

        self._mode = "all"
        self._query = ""
        self._searching = False
        self._reload_pending = False
        self._render_gen = 0
        self._trending_loaded = False
        self._trending_movies = []
        self._trending_shows = []

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_vexpand(True)

        clamp = Adw.Clamp()
        clamp.set_maximum_size(1400)
        clamp.set_tightening_threshold(900)

        self.dashboard_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=28)
        self.dashboard_box.set_margin_start(28)
        self.dashboard_box.set_margin_end(28)
        self.dashboard_box.set_margin_top(24)
        self.dashboard_box.set_margin_bottom(36)

        # Search bar + filters
        search_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        search_box.set_valign(Gtk.Align.CENTER)

        self.search_entry = Gtk.SearchEntry()
        self.search_entry.set_placeholder_text("Search movies & shows...")
        self.search_entry.set_hexpand(True)
        self.search_entry.connect("activate", self._on_search)
        search_box.append(self.search_entry)

        self.all_toggle = Gtk.ToggleButton()
        self.all_toggle.add_css_class("view-pill")
        all_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        all_box.append(Gtk.Image.new_from_icon_name("view-paged-symbolic"))
        all_box.append(Gtk.Label(label="All"))
        self.all_toggle.set_child(all_box)
        self.all_toggle.set_active(True)

        self.movie_toggle = Gtk.ToggleButton()
        self.movie_toggle.add_css_class("view-pill")
        movie_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        movie_box.append(Gtk.Image.new_from_icon_name("video-x-generic-symbolic"))
        movie_box.append(Gtk.Label(label="Movies"))
        self.movie_toggle.set_child(movie_box)
        self.movie_toggle.set_group(self.all_toggle)

        self.show_toggle = Gtk.ToggleButton()
        self.show_toggle.add_css_class("view-pill")
        show_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        show_box.append(Gtk.Image.new_from_icon_name("tv-symbolic"))
        show_box.append(Gtk.Label(label="Shows"))
        self.show_toggle.set_child(show_box)
        self.show_toggle.set_group(self.all_toggle)

        self.all_toggle.connect("toggled", self._on_filter_toggled)
        self.movie_toggle.connect("toggled", self._on_filter_toggled)
        self.show_toggle.connect("toggled", self._on_filter_toggled)

        filter_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=3)
        filter_box.append(self.all_toggle)
        filter_box.append(self.movie_toggle)
        filter_box.append(self.show_toggle)
        search_box.append(filter_box)
        self.dashboard_box.append(search_box)

        # Results sections
        self.movies_section = self._build_section("Movies")
        self.movies_grid = self.movies_section[1]
        self.dashboard_box.append(self.movies_section[0])

        self.shows_section = self._build_section("Shows")
        self.shows_grid = self.shows_section[1]
        self.dashboard_box.append(self.shows_section[0])

        clamp.set_child(self.dashboard_box)
        scrolled.set_child(clamp)
        self.set_child(scrolled)

    def _build_section(self, title):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        lbl = Gtk.Label(label=title)
        lbl.add_css_class("title-4")
        lbl.set_xalign(0)
        box.append(lbl)
        grid = Gtk.FlowBox()
        config_grid(grid)
        box.append(grid)
        return box, grid

    def _set_mode(self, mode):
        if mode == self._mode:
            return
        self._mode = mode
        if self._query and not self._reload_pending:
            self._reload_pending = True
            self._fade_out_then_search()
        elif self._trending_loaded:
            self._populate_trending(
                self._render_gen, self._trending_movies, self._trending_shows
            )

    def _fade_out_then_search(self):
        widgets = []
        for grid in (self.movies_grid, self.shows_grid):
            child = grid.get_first_child()
            while child:
                widgets.append(child)
                child = child.get_next_sibling()

        def _done():
            self._reload_pending = False
            self._run_search(self._query)

        fade_out_group(widgets, 120, _done)

    def play_entrance(self):
        self.search_entry.grab_focus()
        if not self._trending_loaded:
            self._trending_loaded = True
            self._show_skeleton(4)
            GLib.Thread.new("search-trending", self._fetch_trending)

    def _fetch_trending(self):
        gen = self._render_gen
        try:
            movies = self.metadata_service.get_trending("movie")[:8]
            shows = self.metadata_service.get_trending("tv")[:8]
        except NetworkError:
            try:
                movies = self.metadata_service.get_recent_movies()[:8]
                shows = self.metadata_service.get_recent_shows()[:8]
            except NetworkError as e:
                GLib.idle_add(self._show_error, str(e))
                return

        if self.win.settings.get_boolean("hide-adult-content"):
            movies = [m for m in movies if not getattr(m, "adult", False)]
            shows = [s for s in shows if not getattr(s, "adult", False)]

        self._trending_movies = movies
        self._trending_shows = shows
        GLib.idle_add(self._populate_trending, gen, movies, shows)

    def _populate_trending(self, gen, movies, shows):
        if gen != self._render_gen:
            return False
        if self._mode == "movies":
            shows = []
        elif self._mode == "shows":
            movies = []
        self._populate(movies, shows)
        return False

    def _on_filter_toggled(self, btn):
        if not btn.get_active():
            return
        if self.all_toggle.get_active():
            mode = "all"
        elif self.movie_toggle.get_active():
            mode = "movies"
        else:
            mode = "shows"
        self._mode = mode
        query = self.search_entry.get_text().strip()
        if query and not self._reload_pending:
            self._reload_pending = True
            self._fade_out_then_search()
        elif self._trending_loaded:
            self._populate_trending(
                self._render_gen, self._trending_movies, self._trending_shows
            )

    def _on_search(self, entry):
        query = entry.get_text().strip()
        if not query:
            return
        self._run_search(query)

    def _run_search(self, query):
        if self._searching:
            return
        self._searching = True
        self._query = query
        self._render_gen += 1
        self._clear()
        self._show_skeleton(4)
        GLib.Thread.new("search", self._fetch, query)

    def _fetch(self, query):
        try:
            mode = self._mode
            if mode == "all":
                from ..threads import submit as _submit_worker

                m_fut = _submit_worker(self.metadata_service.search_movies, query)
                s_fut = _submit_worker(self.metadata_service.search_shows, query)
                try:
                    movies = m_fut.result()
                except NetworkError:
                    movies = []
                try:
                    shows = s_fut.result()
                except NetworkError:
                    shows = []
            elif mode == "movies":
                movies = self.metadata_service.search_movies(query)
                shows = []
            else:
                movies = []
                shows = self.metadata_service.search_shows(query)

            if self.win.settings.get_boolean("hide-adult-content"):
                movies = [m for m in movies if not getattr(m, "adult", False)]
                shows = [s for s in shows if not getattr(s, "adult", False)]

            GLib.idle_add(self._populate, movies, shows)
        except NetworkError as e:
            GLib.idle_add(self._show_error, str(e))
        finally:
            self._searching = False

    def _populate(self, movies, shows):
        self._clear()

        cards = []
        for item in movies:
            card = make_media_card(item, self.main_page)
            cards.append(card)
            self.movies_grid.append(card)
        for item in shows:
            card = make_media_card(item, self.main_page)
            cards.append(card)
            self.shows_grid.append(card)

        self.movies_section[0].set_visible(self._mode in ("all", "movies") and bool(movies))
        self.shows_section[0].set_visible(self._mode in ("all", "shows") and bool(shows))

        if not movies and not shows:
            empty = Gtk.Label(label="No results found", margin_top=24)
            empty.add_css_class("dim-label")
            empty.set_xalign(0)
            self.dashboard_box.append(empty)
        else:
            stagger_fade_in(
                cards,
                delay_ms=30,
                duration_ms=250,
                after_ms=60,
                max_children=24,
            )
        return False

    def _show_skeleton(self, count):
        for _ in range(count):
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            box.set_size_request(160, 240)
            box.add_css_class("skeleton-pulse")
            self.movies_grid.append(box)

    def _show_error(self, msg):
        self._clear()
        lbl = Gtk.Label(label=f"Error: {msg}", margin_top=24)
        self.dashboard_box.append(lbl)
        return False

    def _clear(self):
        for grid in (self.movies_grid, self.shows_grid):
            child = grid.get_first_child()
            while child:
                nxt = child.get_next_sibling()
                grid.remove(child)
                child = nxt
        # remove old empty/error labels from dashboard_box
        child = self.dashboard_box.get_first_child()
        # keep first 3 children: search_box, movies_section, shows_section
        keep = 3
        idx = 0
        while child:
            nxt = child.get_next_sibling()
            if idx >= keep:
                self.dashboard_box.remove(child)
            child = nxt
            idx += 1
