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
from ..domain.models import Episode
from . import watched_state
from .genre_chips import GenreChipsRow, item_genre_names, matches_all
from .media_card import add_watched_badge, config_grid, make_media_card
from . import page_reveal
from . import poster
from .anim import (
    CONTENT_MS,
    CONTENT_PX,
    ENTRANCE_MS,
    animations_enabled,
    fade_in,
    fade_out_group,
    rise_fade_in,
)


POSTER_W = 160
POSTER_H = 240


def _config_grid(grid):
    config_grid(grid)


class WatchlistPage(Gtk.Box):
    """Dashboard with clamp, sort header, sectioned grids."""

    # The Upcoming section belongs to the watchlist only; subclasses
    # (History) opt out.
    upcoming_enabled = True

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
        self._search_placeholder = getattr(
            self, "_search_placeholder", "Watchlist")
        self.search_entry.set_placeholder_text(
            f"Search in {self._search_placeholder}…")
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
        self.sort_header = top_row

        self.genre_chips = GenreChipsRow(on_changed=self._on_genres_changed)
        # Reserved-height holder: when the chips populate (or hide), the
        # grids below never jump.
        chips_holder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        chips_holder.set_size_request(-1, 44)
        chips_holder.append(self.genre_chips)
        self.dashboard_box.append(chips_holder)

        if self.upcoming_enabled:
            self.upcoming_section = self._build_section("Upcoming")
            self.upcoming_grid = self.upcoming_section[1]
            self.upcoming_section[0].set_visible(False)
            # Refined entrance: the whole section slides up into place once
            # its data is ready.
            self.upcoming_revealer = Gtk.Revealer(
                transition_type=Gtk.RevealerTransitionType.SLIDE_UP,
                transition_duration=ENTRANCE_MS)
            self.upcoming_revealer.set_child(self.upcoming_section[0])
            self.dashboard_box.append(self.upcoming_revealer)
            self._upcoming_cache = None

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

        clamp.set_child(self.dashboard_box)
        scrolled.set_child(clamp)
        self.append(scrolled)

        # Single launch reveal: the whole dashboard starts hidden and fades
        # in as one unit on first populate (or via the safety timeout).
        base_reveal = page_reveal.arm_launch_reveal(
            self.dashboard_box, settle_fn=poster.pending_loads)
        self._revealed = False

        def _reveal_page():
            self._revealed = True
            base_reveal()

        self._reveal_page = _reveal_page
        # Timestamp of the last accepted load request, used to drop
        # duplicate reloads that land a few ms apart.
        self._last_load_request_ms = None

        # Defer the first load until the page transition has finished so
        # launch work never competes with navigation.
        GLib.idle_add(self._initial_load)

    def _initial_load(self):
        GLib.timeout_add(70, self._load)
        return False

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

    def _on_genres_changed(self):
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
            self._load(force=True)

        fade_out_group(widgets, 120, _done)

    # Two load requests can land a few ms apart when a page is created
    # while already stale: its own deferred first load plus the stale
    # refresh main_page queues after the transition. Running both tears
    # down freshly painted cards and replays the entrance animation, so
    # duplicates inside this window are dropped; explicit user actions
    # pass force=True.
    _LOAD_DEDUP_MS = 250

    def _load(self, force=False):
        now = GLib.get_monotonic_time() / 1000.0
        last = self._last_load_request_ms
        if not force and last is not None and now - last < self._LOAD_DEDUP_MS:
            return
        self._last_load_request_ms = now
        self._reload_token += 1
        token = self._reload_token
        mode = self._mode
        self._clear()
        self._items = []
        self._show_skeleton(4)
        GLib.Thread.new("watchlist-load", self._fetch, token, mode)
        if self.upcoming_enabled:
            cached = self._upcoming_cache
            if (cached is not None
                    and cached["day"] == datetime.date.today()
                    and cached["version"] == self._upcoming_version()):
                GLib.idle_add(self._populate_upcoming, token, cached["entries"])
            else:
                GLib.Thread.new("watchlist-upcoming", self._fetch_upcoming, token)

    def _show_skeleton(self, count):
        for _ in range(count):
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            box.set_size_request(POSTER_W, POSTER_H)
            box.add_css_class("skeleton-pulse")
            self.movies_grid.append(box)
            fade_in(box, 200)

    def _dicts_to_items(self, dicts):
        return [SimpleNamespace(**d) for d in dicts]

    def _is_show_fully_watched(self, show_id):
        return watched_state.is_show_fully_watched(
            self.user_repo, self.metadata_service, show_id
        )

    def _get_fully_watched_show_ids(self):
        return watched_state.fully_watched_show_ids(
            self.user_repo, self.metadata_service
        )

    # ------------------------------------------------------------------
    # Upcoming section: watchlist movies releasing within 5 days (released
    # included) plus the next unaired episode of each watchlist show airing
    # within 5 days. Mode buttons don't apply, but genre chips and the
    # search box filter it exactly like the grids; sorted nearest date
    # first.
    # ------------------------------------------------------------------

    UPCOMING_DAYS = 5

    def _upcoming_version(self):
        return getattr(self.user_repo, "data_version", None)

    def _fetch_upcoming(self, token):
        entries = self._compute_upcoming()
        self._upcoming_cache = {
            "day": datetime.date.today(),
            "version": self._upcoming_version(),
            "entries": entries,
        }
        GLib.idle_add(self._populate_upcoming, token, entries)

    def _compute_upcoming(self):
        from ..threads import submit as _submit_worker

        today = datetime.date.today()
        horizon = today + datetime.timedelta(days=self.UPCOMING_DAYS)

        wl_movies = self.user_repo.get_watchlist("movie")
        wl_shows = self.user_repo.get_watchlist("show")
        watched_movie_ids = self.user_repo.get_watched_ids("movie")

        entries = []

        def _movie_date(row):
            # Old releases can never be "upcoming" — skip the network call.
            year = row.get("year")
            if year is not None and year < today.year:
                return None
            try:
                movie = self.metadata_service.get_movie(row["tmdb_id"])
            except NetworkError:
                return None
            rd = getattr(movie, "release_date", None) if movie else None
            if not rd:
                return None
            try:
                d = datetime.date.fromisoformat(rd)
            except (ValueError, TypeError):
                return None
            return d if today <= d <= horizon else None

        def _next_episode(row):
            tmdb_id = row["tmdb_id"]
            watched_eps = self.user_repo.get_watched_episodes_for_show(tmdb_id)
            try:
                show = self.metadata_service.get_show(tmdb_id)
                episodes = self.metadata_service.get_latest_season_episodes(tmdb_id)
            except NetworkError:
                return None

            best = None  # (date, episode)
            for ep in episodes:
                if not ep.air_date:
                    continue
                if (ep.season_number, ep.episode_number) in watched_eps:
                    continue  # already seen — not upcoming for this user
                try:
                    d = datetime.date.fromisoformat(ep.air_date)
                except (ValueError, TypeError):
                    continue
                if today <= d <= horizon and (best is None or d < best[0]):
                    best = (d, ep)

            if (show.next_episode_air_date
                    and show.next_episode_season is not None
                    and show.next_episode_number is not None):
                try:
                    nd = datetime.date.fromisoformat(show.next_episode_air_date)
                except (ValueError, TypeError):
                    nd = None
                if nd is not None and today <= nd <= horizon:
                    dup = best is not None and (
                        best[1].season_number,
                        best[1].episode_number,
                    ) == (show.next_episode_season, show.next_episode_number)
                    seen = (show.next_episode_season,
                            show.next_episode_number) in watched_eps
                    if not dup and not seen and (best is None or nd < best[0]):
                        best = (
                            nd,
                            Episode(
                                tmdb_id=0,
                                show_tmdb_id=tmdb_id,
                                season_number=show.next_episode_season,
                                episode_number=show.next_episode_number,
                                title=show.next_episode_name or "",
                                air_date=show.next_episode_air_date,
                                poster_url=show.next_episode_still,
                            ),
                        )
            return best

        movie_futures = [_submit_worker(_movie_date, r) for r in wl_movies]
        show_futures = [_submit_worker(_next_episode, s) for s in wl_shows]

        for row, fut in zip(wl_movies, movie_futures):
            if row["tmdb_id"] in watched_movie_ids:
                continue
            d = fut.result()
            if d is not None:
                entries.append((d, 0, row))

        for row, fut in zip(wl_shows, show_futures):
            res = fut.result()
            if res is not None:
                d, ep = res
                entries.append((d, 1, (row, ep)))

        entries.sort(key=lambda e: (e[0], e[1]))
        return entries

    def _upcoming_pass(self, entry):
        """Build the card item for an upcoming entry and test it against
        the active genre chips and search-box query — the same two gates
        the Movies/Shows grids use. Returns (passes, item, sort_date, ep).
        """
        sort_date, kind, payload = entry
        if kind == 0:
            row = payload
            item = SimpleNamespace(**row)
            item.media_type = "movie"
            ep = None
        else:
            show_row, ep = payload
            item = SimpleNamespace(
                tmdb_id=show_row["tmdb_id"],
                title=show_row["title"],
                year=show_row.get("year"),
                poster_url=show_row.get("poster_url") or ep.poster_url,
                media_type="show",
                genres=show_row.get("genres"),
                genre_ids=[],
            )
        selected = self.genre_chips.selected
        if selected and not matches_all(item, selected):
            return False, item, sort_date, ep
        query = self._filter_query
        if query and query not in (item.title or "").lower():
            return False, item, sort_date, ep
        return True, item, sort_date, ep

    def _populate_upcoming(self, token, entries):
        if token != self._reload_token:
            return False
        child = self.upcoming_grid.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            self.upcoming_grid.remove(child)
            child = nxt

        if not entries:
            self.upcoming_revealer.set_reveal_child(False)
            self.upcoming_section[0].set_visible(False)
            return False

        passed = []
        for entry in entries:
            ok, item, sort_date, ep = self._upcoming_pass(entry)
            if ok:
                passed.append((item, sort_date, ep))

        if not passed:
            self.upcoming_revealer.set_reveal_child(False)
            self.upcoming_section[0].set_visible(False)
            return False

        cards = []
        for item, sort_date, ep in passed:
            if ep is None:
                card = make_media_card(
                    item, self.main_page,
                    subtitle=f"Movie - {sort_date:%a %-d %b}",
                )
            else:
                card = make_media_card(
                    item, self.main_page,
                    subtitle=(f"S{ep.season_number:02d}E{ep.episode_number:02d}"
                              f" · {sort_date:%a %-d %b}"),
                )
            self.upcoming_grid.append(card)
            cards.append(card)

        self.upcoming_section[0].set_visible(True)
        self.upcoming_revealer.set_reveal_child(True)
        # Same content motion as every other card group — no cascade.
        rise_fade_in(cards, CONTENT_MS, CONTENT_PX)
        return False

    def _hide_unreleased_enabled(self) -> bool:
        settings = getattr(self.win, "settings", None)
        if settings is None:
            return False
        try:
            return bool(settings.get_boolean("hide-unreleased"))
        except Exception:
            return False

    def _is_unreleased(self, tmdb_id: int, media_type: str) -> bool:
        """True only when metadata positively dates the premiere/release in
        the future. Unknown dates never hide a title."""
        try:
            if media_type == "movie":
                detail = self.metadata_service.get_movie(tmdb_id)
                date_iso = getattr(detail, "release_date", None)
            else:
                detail = self.metadata_service.get_show(tmdb_id)
                date_iso = getattr(detail, "first_air_date", None)
            if not date_iso:
                return False
            return datetime.date.fromisoformat(date_iso) > datetime.date.today()
        except NetworkError:
            return False

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

        hide_unreleased = self._hide_unreleased_enabled()
        if hide_unreleased:
            movies = [m for m in movies
                      if not self._is_unreleased(m.tmdb_id, "movie")]
            shows = [s for s in shows
                     if not self._is_unreleased(s.tmdb_id, "show")]

        movies = [i for i in movies if i.tmdb_id not in watched_movie_ids]
        shows = [i for i in shows if i.tmdb_id not in fully_watched_show_ids]
        return movies, shows

    def _early_badges(self, movies, shows):
        """Watched movie ids known instantly from local data."""
        return frozenset()

    def _late_badges(self, movies, shows):
        """Fully-watched show ids; may hit the network. Computed after the
        grid is already queued for rendering so it never blocks it."""
        return frozenset()

    def _apply_late_badges(self, token, fully_ids):
        """Retrofit watched badges onto rendered show cards."""
        if token != self._reload_token:
            return False
        self._last_fully_shows = (
            frozenset(getattr(self, "_last_fully_shows", frozenset())) | frozenset(fully_ids)
        )
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

    def _fetch(self, token, mode):
        try:
            movies, shows = self._get_items(mode)
            early_ids = self._early_badges(movies, shows)
            # Remember badge sets so filter/sort repopulates can rebuild
            # cards without losing their green ticks.
            self._last_early_ids = early_ids
            GLib.idle_add(self._populate, token, movies, shows,
                          early_ids, frozenset())
            late_ids = self._late_badges(movies, shows)
            if late_ids:
                GLib.idle_add(self._apply_late_badges, token, late_ids)
        except sqlite3.Error as e:
            GLib.idle_add(self._show_error, str(e))
        except Exception as e:
            # Never let a fetch thread die silently and leave the page on
            # its skeleton forever.
            print(f"[watchlist] load failed: {e!r}")
            GLib.idle_add(self._show_error, str(e))

    def _populate(self, token, movies, shows,
                  watched_movie_ids=frozenset(), fully_watched_shows=frozenset()):
        if token != self._reload_token:
            return False
        self._last_fully_shows = frozenset(fully_watched_shows)

        # Fade out previous cards instead of letting the new content
        # hard-swap over them — but skeletons (placeholder pulse) can be
        # cleared instantly; fading them first only delays the entrance.
        old = []
        old_are_skeletons = True
        for grid in (self.movies_grid, self.shows_grid):
            child = grid.get_first_child()
            while child:
                # FlowBox children are wrappers; the pulse class sits on
                # the widget we appended.
                inner = child.get_child() if hasattr(child, "get_child") else None
                classes = (inner or child).get_css_classes()
                if "skeleton-pulse" not in (classes or []):
                    old_are_skeletons = False
                old.append(child)
                child = child.get_next_sibling()

        def _build():
            nonlocal movies, shows
            if token != self._reload_token:
                return False
            self._clear()

            if self._show_sort:
                movies = self._sort_items(movies)
                shows = self._sort_items(shows)
            self._items = list(movies) + list(shows)
            selected = self.genre_chips.selected
            if selected:
                movies = [i for i in movies if matches_all(i, selected)]
                shows = [i for i in shows if matches_all(i, selected)]
            query = self._filter_query
            if query:
                movies = [i for i in movies if query in i.title.lower()]
                shows = [i for i in shows if query in i.title.lower()]

            # Building every card in one pass spikes the main thread right
            # in the middle of the page transition; spread it over idle
            # ticks so frames stay smooth. The reveal fires on the last
            # chunk.
            queue = [("movie", i) for i in movies] + [("show", i) for i in shows]
            # Sections stay hidden while their grids fill; the finisher
            # shows exactly the non-empty ones together with their cards.
            self.movies_section[0].set_visible(False)
            self.shows_section[0].set_visible(False)
            self._pending_chunk = (token, iter(queue),
                                   watched_movie_ids, fully_watched_shows)
            self._pump_build()
            return False

        if old and not old_are_skeletons:
            fade_out_group(old, 120, _build)
        else:
            _build()
        return False

    _CHUNK_SIZE = 12

    def _pump_build(self, schedule=True):
        """Append one batch of pending cards; reschedules itself while
        work remains. schedule=False drains synchronously (tests)."""
        pending = getattr(self, "_pending_chunk", None)
        if not pending:
            return False
        token, queue_iter, watched_movie_ids, fully_watched_shows = pending
        if token != self._reload_token:
            self._pending_chunk = None
            return False

        cards = getattr(self, "_build_cards", None)
        if cards is None:
            cards = self._build_cards = []

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
                # Read the LIVE badge set, not the snapshot captured at
                # populate time: checks that resolved while earlier chunks
                # were building must badge later-built cards too.
                live_fully = frozenset(
                    getattr(self, "_last_fully_shows", frozenset()))
                card = make_media_card(
                    item, self.main_page,
                    watched=(item.tmdb_id in fully_watched_shows
                             or item.tmdb_id in live_fully))
                self.shows_grid.append(card)
            built.append(card)
            if self._revealed and animations_enabled():
                # Repopulate pass: hide AND offset the card BEFORE any
                # frame can paint it, so the delayed rise-fade never
                # flashes or jumps.
                card._rise_orig_margin = card.get_margin_top()
                card.set_margin_top(card.get_margin_top() + CONTENT_PX)
                card.set_opacity(0.0)

        cards.extend(built)

        if self._pending_chunk is not None:
            if schedule:
                GLib.idle_add(self._pump_build, True)
            return False

        # Queue drained: chips, sections, optional rise-fade, reveal.
        self._build_cards = None
        self.genre_chips.set_genres(
            g for it in self._items for g in item_genre_names(it)
        )
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
            empty = Gtk.Label(label=self._empty_label, margin_top=8)
            empty.add_css_class("dim-label")
            empty.set_xalign(0)
            self._empty_label_widget = empty
            self.dashboard_box.append(empty)
            rise_fade_in([empty], CONTENT_MS, CONTENT_PX)
        elif self._revealed:
            # page already visible (filter/mode repopulate): one uniform
            # rise-fade on every page with genre chips; section titles
            # ride along so they land with their cards
            rise_fade_in(sections + cards, CONTENT_MS, CONTENT_PX)
        # first load: no stagger — the unified page reveal covers it

        self._reveal_page()
        self.movies_grid.queue_resize()
        self.shows_grid.queue_resize()
        return False

    def _drain_build(self):
        """Synchronously finish any pending card building (tests)."""
        while getattr(self, "_pending_chunk", None) is not None:
            self._pump_build(schedule=False)

    def _repopulate(self):
        """Re-sort and re-display items without re-fetching. Badge sets
        from the last fetch are reused so ticks survive filtering."""
        movies = [i for i in self._items if i.media_type == "movie"]
        shows = [i for i in self._items if i.media_type == "show"]
        self._populate(
            self._reload_token, movies, shows,
            getattr(self, "_last_early_ids", frozenset()),
            getattr(self, "_last_fully_shows", frozenset()),
        )
        # Upcoming reacts to the same genre/query filters — a same-day
        # cache means this is a pure display-level rebuild.
        cached = getattr(self, "_upcoming_cache", None)
        if (cached is not None
                and cached["day"] == datetime.date.today()):
            GLib.idle_add(
                self._populate_upcoming,
                self._reload_token, cached["entries"],
            )

    def _show_error(self, msg):
        self._clear()
        lbl = Gtk.Label(label=f"Error: {msg}", margin_top=24)
        self._empty_label_widget = lbl
        self.dashboard_box.append(lbl)
        rise_fade_in([lbl], CONTENT_MS, CONTENT_PX)
        self._reveal_page()
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
