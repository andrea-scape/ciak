import gi

import os

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, GLib

from ..domain.exceptions import NetworkError
from . import watched_state
from .genre_chips import GenreChipsRow, item_genre_names, matches_all
from .media_card import add_watched_badge, config_grid, make_media_card, PAGE_GUTTER_PX
from . import page_reveal
from . import poster
from .anim import (
    CONTENT_MS,
    CONTENT_PX,
    animations_enabled,
    fade_out_group,
    rise_fade_in,
)
from .shared_widgets import make_error_row


class SearchPage(Adw.Bin):
    """Search page with watchlist-style card results."""

    def __init__(self, win, user_repo, metadata_service, main_page=None):
        super().__init__()
        self.win = win
        self.user_repo = user_repo
        self.metadata_service = metadata_service
        self.main_page = main_page
        self.add_css_class("ciak-dashboard")
        base_reveal = page_reveal.arm_launch_reveal(
            self, settle_fn=poster.pending_loads)
        self._revealed = False

        def _reveal_page():
            self._revealed = True
            base_reveal()

        self._reveal_page = _reveal_page

        self._mode = "all"
        self._query = ""
        self._searching = False
        self._reload_pending = False
        self._render_gen = 0
        self._trending_loaded = False
        self._trending_movies = []
        self._trending_shows = []
        self._showing_trending = False

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_vexpand(True)

        clamp = Adw.Clamp()
        clamp.set_maximum_size(1400)
        clamp.set_tightening_threshold(900)

        self.dashboard_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=28)
        self.dashboard_box.set_margin_start(PAGE_GUTTER_PX)
        self.dashboard_box.set_margin_end(PAGE_GUTTER_PX)
        self.dashboard_box.set_margin_top(24)
        self.dashboard_box.set_margin_bottom(36)

        # Search bar + filters
        search_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        search_box.set_valign(Gtk.Align.CENTER)

        self.search_entry = Gtk.SearchEntry()
        self.search_entry.set_placeholder_text("Search movies & shows...")
        self.search_entry.set_hexpand(True)
        self.search_entry.connect("activate", self._on_search)
        # Clearing the entry returns to the trending grid; non-empty
        # text still waits for Enter.
        self.search_entry.connect("search-changed", self._on_search_changed)
        self.search_entry.connect("stop-search", self._on_stop_search)
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

        self.genre_chips = GenreChipsRow(on_changed=self._on_genres_changed)
        chips_holder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        chips_holder.set_size_request(-1, 44)
        chips_holder.append(self.genre_chips)
        self.dashboard_box.append(chips_holder)
        # Pulsing pills from the first frame; replaced by real genres.
        self.genre_chips.show_placeholder()

        # Results sections
        self.movies_section = self._build_section("Movies")
        self.movies_grid = self.movies_section[1]
        self.shows_section = self._build_section("Shows")
        self.shows_grid = self.shows_section[1]

        if self._shows_first():
            self.dashboard_box.append(self.shows_section[0])
            self.dashboard_box.append(self.movies_section[0])
        else:
            self.dashboard_box.append(self.movies_section[0])
            self.dashboard_box.append(self.shows_section[0])

        self._fixed_children = (
            search_box,
            chips_holder,
            self.genre_chips,
            self.movies_section[0],
            self.shows_section[0],
        )

        clamp.set_child(self.dashboard_box)
        scrolled.set_child(clamp)
        self.set_child(scrolled)

    def _shows_first(self):
        """True when TV Shows should be listed before Movies."""
        settings = getattr(self.win, "settings", None)
        if settings is None:
            return False
        try:
            return bool(settings.get_boolean("swap-sections"))
        except Exception:
            return False

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
        if mode not in ("all", "movies", "shows"):
            return
        self._mode = mode
        self._sync_toggle_buttons()
        if self._query and not self._reload_pending:
            self._reload_pending = True
            self._fade_out_then_search()
        elif self._trending_loaded:
            self._populate_trending(
                self._render_gen, self._trending_movies, self._trending_shows
            )

    def _sync_toggle_buttons(self):
        """Mirror self._mode onto the on-page toggle buttons without
        re-triggering the toggled handlers."""
        for btn in (self.all_toggle, self.movie_toggle, self.show_toggle):
            btn.handler_block_by_func(self._on_filter_toggled)
        if self._mode == "all":
            self.all_toggle.set_active(True)
        elif self._mode == "movies":
            self.movie_toggle.set_active(True)
        else:
            self.show_toggle.set_active(True)
        for btn in (self.all_toggle, self.movie_toggle, self.show_toggle):
            btn.handler_unblock_by_func(self._on_filter_toggled)

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
            page_reveal.defer_initial_work(self._fetch_trending)

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
        # Same badge treatment as real searches: movie pills known before
        # the first paint, show checks stream in afterwards.
        watched_movie_ids = self.user_repo.get_watched_ids("movie")
        self._last_watched_movie_ids = watched_movie_ids
        GLib.idle_add(self._populate_trending, gen, movies, shows,
                      watched_movie_ids)
        fully_shows = watched_state.caught_up_show_ids(
            self.user_repo, self.metadata_service,
            {s.tmdb_id for s in shows},
        )
        if fully_shows:
            GLib.idle_add(self._apply_late_badges, fully_shows)

    def _populate_trending(self, gen, movies, shows,
                           watched_movie_ids=frozenset()):
        if gen != self._render_gen:
            return False
        self._showing_trending = True
        if self._mode == "movies":
            shows = []
        elif self._mode == "shows":
            movies = []
        self._populate(movies, shows, watched_movie_ids)
        return False

    def _on_search_changed(self, entry):
        # Only the empty case acts here: clearing the entry returns to
        # the trending grid. Non-empty queries wait for Enter (activate).
        if not entry.get_text().strip():
            self._restore_trending()

    def _on_stop_search(self, entry):
        # ✕ / Esc — entry is already empty when this fires.
        self._restore_trending()

    def _restore_trending(self):
        """Empty search bar → original state: the trending grid."""
        if getattr(self, "_showing_trending", False):
            return
        if getattr(self, "_reload_pending", False):
            return
        # Invalidate any in-flight search so its results can't land.
        self._searching = False
        self._query = ""
        self._render_gen += 1
        gen = self._render_gen
        if not self._trending_movies:
            self._trending_loaded = False
            self.play_entrance()
            return
        self._clear()
        self._show_skeleton(4)
        watched_movie_ids = self.user_repo.get_watched_ids("movie")
        self._last_watched_movie_ids = watched_movie_ids
        GLib.idle_add(self._populate_trending, gen,
                      list(self._trending_movies),
                      list(self._trending_shows), watched_movie_ids)
        GLib.Thread.new("trending-badges", self._refresh_trending_badges)

    def _refresh_trending_badges(self):
        """Re-run caught-up checks for the restored trending shows."""
        fully_shows = watched_state.caught_up_show_ids(
            self.user_repo, self.metadata_service,
            {s.tmdb_id for s in self._trending_shows},
        )
        if fully_shows:
            GLib.idle_add(self._apply_late_badges, fully_shows)
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
        if self.main_page is not None:
            self.main_page.set_global_mode(mode)
        else:
            self._set_mode(mode)

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
        self._showing_trending = False
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

            watched_movie_ids = self.user_repo.get_watched_ids("movie")
            self._last_watched_movie_ids = watched_movie_ids
            GLib.idle_add(self._populate, movies, shows,
                          watched_movie_ids, frozenset())
            fully_shows = watched_state.caught_up_show_ids(
                self.user_repo, self.metadata_service,
                {s.tmdb_id for s in shows},
            )
            if os.environ.get("CIK_DEBUG"):
                matched = sum(1 for m in movies
                              if m.tmdb_id in watched_movie_ids)
                print(f"[search] badges: movie_results_matched={matched} "
                      f"show_results={len(shows)} "
                      f"caught_up={len(fully_shows)}")
            if fully_shows:
                GLib.idle_add(self._apply_late_badges, fully_shows)
        except NetworkError as e:
            GLib.idle_add(self._show_error, str(e))
        finally:
            self._searching = False

    def _populate(self, movies, shows,
                  watched_movie_ids=frozenset(), fully_watched_shows=frozenset()):
        self._clear()
        self._result_movies = list(movies)
        self._result_shows = list(shows)
        pool = sorted({
            g for it in self._result_movies + self._result_shows
            for g in item_genre_names(it)
        })
        if os.environ.get("CIK_DEBUG"):
            print(f"[search] {len(pool)} genres for chips: {pool}")
        self.genre_chips.set_genres(iter(pool))
        selected = self.genre_chips.selected
        if selected:
            movies = [m for m in movies if matches_all(m, selected)]
            shows = [s for s in shows if matches_all(s, selected)]

        # Building every card in one pass spikes the main thread right in
        # the middle of the transition; spread it over idle ticks.
        queue = [("movie", m) for m in movies] + [("show", s) for s in shows]
        self._search_build_cards = []
        # Sections stay hidden while their grids fill; the finisher
        # shows exactly the non-empty ones together with their cards.
        self.movies_section[0].set_visible(False)
        self.shows_section[0].set_visible(False)
        self._pending_chunk = (self._render_gen, iter(queue),
                               watched_movie_ids, fully_watched_shows)
        self._pump_build()
        return False

    _CHUNK_SIZE = 12

    def _pump_build(self, schedule=True):
        """Append one batch of pending cards; reschedules itself while
        work remains. schedule=False drains synchronously (tests)."""
        pending = getattr(self, "_pending_chunk", None)
        if not pending:
            return False
        gen, queue_iter, watched_movie_ids, fully_watched_shows = pending
        if gen != self._render_gen:
            self._pending_chunk = None
            return False

        built = []
        for _ in range(self._CHUNK_SIZE):
            try:
                kind, item = next(queue_iter)
            except StopIteration:
                self._pending_chunk = None
                break
            if kind == "movie":
                card = make_media_card(
                    item, self.main_page,
                    watched=item.tmdb_id in watched_movie_ids)
                self.movies_grid.append(card)
            else:
                # LIVE badge set, not the populate-time snapshot.
                live_fully = frozenset(
                    getattr(self, "_last_fully_shows", frozenset()))
                card = make_media_card(
                    item, self.main_page,
                    watched=(item.tmdb_id in fully_watched_shows
                             or item.tmdb_id in live_fully))
                self.shows_grid.append(card)
            built.append(card)
            if self._revealed and animations_enabled():
                # Repopulate pass: hide AND offset before any frame
                # paints it — no flash, no jump.
                card._rise_orig_margin = card.get_margin_top()
                card.set_margin_top(card.get_margin_top() + CONTENT_PX)
                card.set_opacity(0.0)

        self._search_build_cards.extend(built)

        if self._pending_chunk is not None:
            if schedule:
                GLib.idle_add(self._pump_build, True)
            return False

        cards = self._search_build_cards
        # Show exactly the sections that have content — titles never
        # precede their first cards.
        sections = []
        for grid, section in ((self.movies_grid, self.movies_section),
                              (self.shows_grid, self.shows_section)):
            if grid.get_first_child() is not None:
                section[0].set_visible(True)
                sections.append(section[0])
            else:
                section[0].set_visible(False)

        if not cards:
            empty_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                                halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER, vexpand=True)
            empty_box.set_margin_top(48)
            icon = Gtk.Image(icon_name="system-search-symbolic", pixel_size=48)
            icon.add_css_class("dimmed")
            empty_box.append(icon)
            title = Gtk.Label(label="No results found")
            title.add_css_class("title-3")
            empty_box.append(title)
            subtitle = Gtk.Label(label="Try a different search term")
            subtitle.add_css_class("dimmed")
            empty_box.append(subtitle)
            self.dashboard_box.append(empty_box)
            rise_fade_in([empty_box], CONTENT_MS, CONTENT_PX)
        elif self._revealed:
            # repopulate (new query / filter): same content rise as the
            # other genre-chip pages, titles riding along
            rise_fade_in(sections + cards, CONTENT_MS, CONTENT_PX)
        # first load: no stagger — unified reveal covers it

        self._reveal_page()
        return False

    def _drain_build(self):
        """Synchronously finish any pending card building (tests)."""
        while getattr(self, "_pending_chunk", None) is not None:
            self._pump_build(schedule=False)

    def _apply_late_badges(self, fully_ids):
        """Retrofit watched badges onto rendered show cards."""
        prev = frozenset(getattr(self, "_last_fully_shows", frozenset()))
        self._last_fully_shows = prev | frozenset(fully_ids)
        for grid in (self.movies_grid, self.shows_grid):
            child = grid.get_first_child()
            while child:
                nxt = child.get_next_sibling()
                button = getattr(child, "get_child", lambda: None)()
                item = getattr(button, "_media_item", None) if button else None
                if (item is not None
                        and getattr(item, "media_type", "") == "show"
                        and item.tmdb_id in fully_ids):
                    add_watched_badge(button)
                child = nxt
        return False

    def _on_genres_changed(self):
        movies = getattr(self, "_result_movies", None)
        shows = getattr(self, "_result_shows", None)
        if movies is not None:
            self._populate(
                movies, shows,
                getattr(self, "_last_watched_movie_ids", frozenset()),
                getattr(self, "_last_fully_shows", frozenset()),
            )

    def _show_skeleton(self, count):
        for _ in range(count):
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            box.set_size_request(160, 240)
            box.add_css_class("skeleton-pulse")
            self.movies_grid.append(box)

    def _show_error(self, msg):
        self._clear()

        def _retry():
            if self._showing_trending or not self._query:
                self._trending_loaded = False
                self.play_entrance()
            else:
                self._run_search(self._query)

        error_row = make_error_row(
            f"Error: {msg}",
            on_retry=_retry,
        )
        self.dashboard_box.append(error_row)
        rise_fade_in([error_row], CONTENT_MS, CONTENT_PX)
        self._reveal_page()
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
        while child:
            nxt = child.get_next_sibling()
            if child not in self._fixed_children:
                self.dashboard_box.remove(child)
            child = nxt
