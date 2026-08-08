import datetime
import sqlite3
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gtk, Adw, GLib

from types import SimpleNamespace

from ..domain.exceptions import NetworkError
from .media_card import config_grid, make_media_card
from .anim import fade_out_group, stagger_fade_in


POSTER_W = 160
POSTER_H = 240


def _config_grid(grid):
    config_grid(grid)


class WatchlistPage(Gtk.Box):
    """Dashboard with clamp, sort header, sectioned grids."""

    def __init__(self, win, user_repo, metadata_service, main_page=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.win = win
        self.user_repo = user_repo
        self.metadata_service = metadata_service
        self.main_page = main_page
        self.add_css_class("ciak-dashboard")

        self._mode = "all"
        self._items = []
        self._reload_pending = False
        self._reload_token = 0
        self._sort_by = "added"  # "added" or "release"
        self._show_sort = getattr(self, "_show_sort", True)
        self._added_attr = getattr(self, "_added_attr", "added_at")
        self._empty_label = getattr(self, "_empty_label", "Your watchlist is empty")
        self._sort_labels = getattr(
            self, "_sort_labels", ["Recently Added", "Release Date"]
        )

        self._filter_query = ""
        self._empty_label_widget = None

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_vexpand(True)
        scrolled.set_hexpand(True)

        clamp = Adw.Clamp()
        clamp.set_maximum_size(1400)
        clamp.set_tightening_threshold(900)

        self.dashboard_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=28)
        self.dashboard_box.set_margin_start(28)
        self.dashboard_box.set_margin_end(28)
        self.dashboard_box.set_margin_top(24)
        self.dashboard_box.set_margin_bottom(36)

        # Top row: filter search (left) + sort dropdown (right)
        top_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        top_row.set_halign(Gtk.Align.FILL)

        self.search_entry = Gtk.SearchEntry()
        self.search_entry.set_hexpand(True)
        self.search_entry.set_placeholder_text("Filter...")
        self.search_entry.connect("search-changed", self._on_search_changed)
        top_row.append(self.search_entry)

        if self._show_sort:
            sort_label = Gtk.Label(label="Sort by")
            sort_label.add_css_class("dim-label")
            sort_label.set_valign(Gtk.Align.CENTER)
            top_row.append(sort_label)

            sort_model = Gtk.StringList()
            for label in self._sort_labels:
                sort_model.append(label)
            self.sort_dropdown = Gtk.DropDown(model=sort_model)
            self.sort_dropdown.set_selected(0)
            self.sort_dropdown.add_css_class("sort-dropdown")
            self.sort_dropdown.connect("notify::selected", self._on_sort_changed)
            top_row.append(self.sort_dropdown)

        self.dashboard_box.append(top_row)

        self.movies_section = self._build_section("Movies")
        self.movies_grid = self.movies_section[1]
        self.dashboard_box.append(self.movies_section[0])

        self.shows_section = self._build_section("Shows")
        self.shows_grid = self.shows_section[1]
        self.dashboard_box.append(self.shows_section[0])

        clamp.set_child(self.dashboard_box)
        scrolled.set_child(clamp)
        self.append(scrolled)

        self._load()

    def _build_section(self, title):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        lbl = Gtk.Label(label=title)
        lbl.add_css_class("title-4")
        lbl.set_xalign(0)
        box.append(lbl)
        grid = Gtk.FlowBox()
        _config_grid(grid)
        box.append(grid)
        return box, grid

    def _on_search_changed(self, entry):
        self._filter_query = entry.get_text().strip().lower()
        self._repopulate()

    def _on_sort_changed(self, dropdown, _pspec):
        idx = dropdown.get_selected()
        new_sort = "release" if idx == 1 else "added"
        if new_sort == self._sort_by:
            return
        self._sort_by = new_sort
        if self._items:
            self._repopulate()

    def _sort_items(self, items):
        if self._sort_by == "release":
            return sorted(items, key=lambda i: (
                0 if i.year is not None else 1,
                -(i.year or 0)
            ))
        return sorted(items, key=lambda i: -(getattr(i, self._added_attr, 0) or 0))

    def _set_mode(self, mode):
        if mode == self._mode:
            return
        self._mode = mode
        if self._reload_pending:
            return
        self._reload_pending = True
        self._fade_out_then_load()

    def _fade_out_then_load(self):
        widgets = []
        for grid in (self.movies_grid, self.shows_grid):
            child = grid.get_first_child()
            while child:
                widgets.append(child)
                child = child.get_next_sibling()

        def _done():
            self._reload_pending = False
            self._load()

        fade_out_group(widgets, 120, _done)

    def _load(self):
        self._reload_token += 1
        token = self._reload_token
        mode = self._mode
        self._clear()
        self._items = []
        self._show_skeleton(4)
        GLib.Thread.new("watchlist-load", self._fetch, token, mode)

    def _show_skeleton(self, count):
        for _ in range(count):
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            box.set_size_request(POSTER_W, POSTER_H)
            box.add_css_class("skeleton-pulse")
            self.movies_grid.append(box)

    def _dicts_to_items(self, dicts):
        return [SimpleNamespace(**d) for d in dicts]

    def _is_show_fully_watched(self, show_id):
        watched = self.user_repo.get_watched_episodes_for_show(show_id)
        if not watched:
            return False

        try:
            seasons = self.metadata_service.get_show_seasons(show_id)
        except NetworkError:
            return False

        today = datetime.date.today()
        for season in seasons:
            if season.season_number <= 0:
                continue
            try:
                episodes = self.metadata_service.get_season_episodes(
                    show_id, season.season_number
                )
            except NetworkError:
                continue

            for ep in episodes:
                if ep.air_date:
                    try:
                        if datetime.date.fromisoformat(ep.air_date) > today:
                            continue
                    except ValueError:
                        continue
                key = (ep.season_number, ep.episode_number)
                if key not in watched:
                    return False

        return True

    def _get_fully_watched_show_ids(self):
        """Return set of show tmdb_ids where every aired episode is watched."""
        today = datetime.date.today()
        candidate_ids = self.user_repo.get_watched_show_ids()
        if not candidate_ids:
            return set()

        from ..threads import submit as _submit_worker

        def _check(show_id):
            try:
                return show_id if self._is_show_fully_watched(show_id) else None
            except (NetworkError, sqlite3.Error):
                return None

        futures = [_submit_worker(_check, sid) for sid in candidate_ids]
        return {sid for fut in futures if (sid := fut.result()) is not None}

    def _get_items(self, mode):
        """Return (movies, shows) for the current mode. Subclasses override."""
        watched_movie_ids = self.user_repo.get_watched_ids("movie")
        fully_watched_show_ids = self._get_fully_watched_show_ids()

        if mode == "all":
            movies = self._dicts_to_items(self.user_repo.get_watchlist("movie"))
            shows = self._dicts_to_items(self.user_repo.get_watchlist("show"))
        elif mode == "movies":
            movies = self._dicts_to_items(self.user_repo.get_watchlist("movie"))
            shows = []
        else:
            movies = []
            shows = self._dicts_to_items(self.user_repo.get_watchlist("show"))

        movies = [i for i in movies if i.tmdb_id not in watched_movie_ids]
        shows = [i for i in shows if i.tmdb_id not in fully_watched_show_ids]
        return movies, shows

    def _fetch(self, token, mode):
        try:
            movies, shows = self._get_items(mode)
            GLib.idle_add(self._populate, token, movies, shows)
        except sqlite3.Error as e:
            GLib.idle_add(self._show_error, str(e))

    def _populate(self, token, movies, shows):
        if token != self._reload_token:
            return False
        self._clear()

        if self._show_sort:
            movies = self._sort_items(movies)
            shows = self._sort_items(shows)
        self._items = list(movies) + list(shows)
        query = self._filter_query
        if query:
            movies = [i for i in movies if query in i.title.lower()]
            shows = [i for i in shows if query in i.title.lower()]
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
            empty = Gtk.Label(label=self._empty_label, margin_top=8)
            empty.add_css_class("dim-label")
            empty.set_xalign(0)
            self._empty_label_widget = empty
            self.dashboard_box.append(empty)
        else:
            stagger_fade_in(
                cards,
                delay_ms=30,
                duration_ms=250,
                after_ms=80,
                max_children=24,
            )
        self.movies_grid.queue_resize()
        self.shows_grid.queue_resize()
        return False

    def _repopulate(self):
        """Re-sort and re-display items without re-fetching."""
        movies = [i for i in self._items if i.media_type == "movie"]
        shows = [i for i in self._items if i.media_type == "show"]
        self._populate(self._reload_token, movies, shows)

    def _show_error(self, msg):
        self._clear()
        lbl = Gtk.Label(label=f"Error: {msg}", margin_top=24)
        self._empty_label_widget = lbl
        self.dashboard_box.append(lbl)
        return False

    def _clear(self):
        if self._empty_label_widget is not None:
            parent = self._empty_label_widget.get_parent()
            if parent is not None:
                parent.remove(self._empty_label_widget)
            self._empty_label_widget = None
        for grid in (self.movies_grid, self.shows_grid):
            child = grid.get_first_child()
            while child:
                nxt = child.get_next_sibling()
                grid.remove(child)
                child = nxt
