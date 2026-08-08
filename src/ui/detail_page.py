import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, GLib, Pango, Gdk
import threading
import datetime
import urllib.parse
import sqlite3
from ..domain.models import Movie, Show
from ..domain.exceptions import NetworkError
from .poster import create_poster, create_avatar, load_poster, load_avatar, POSTER_SLOTS
from .painting import FixedPaintable, _load_texture_sync
from .anim import fade_in


class DetailPage(Gtk.Box):
    def __init__(self, win, user_repo, metadata_service, media_type, item, main_page=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.add_css_class("detail-page")
        self.win = win
        self.user_repo = user_repo
        self.metadata_service = metadata_service
        self.media_type = media_type
        self.item = item
        self.main_page = main_page
        self._cancelled = False
        self._in_watchlist = False
        self._is_watched = False
        self._watched_episodes = set()
        self._watched_seasons = set()

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.append(scrolled)

        clamp = Adw.Clamp()
        clamp.set_maximum_size(900)
        scrolled.set_child(clamp)

        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        clamp.set_child(content_box)

        top_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=32)
        top_box.set_margin_start(16)
        top_box.set_margin_end(16)
        top_box.set_margin_top(16)
        top_box.set_margin_bottom(16)
        content_box.append(top_box)

        self.progress_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.progress_box.set_margin_start(16)
        self.progress_box.set_margin_end(16)
        self.progress_box.set_margin_top(8)
        self.progress_box.set_visible(False)
        content_box.append(self.progress_box)

        self.poster_box, self.poster_area = create_poster(240, 360, "detail-poster")
        top_box.append(self.poster_box)

        info_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        info_box.set_valign(Gtk.Align.START)
        top_box.append(info_box)

        self.title_label = Gtk.Label()
        self.title_label.add_css_class("title-1")
        self.title_label.set_wrap(True)
        self.title_label.set_halign(Gtk.Align.START)
        self.title_label.set_xalign(0)
        info_box.append(self.title_label)

        self.tagline_label = Gtk.Label()
        self.tagline_label.add_css_class("tagline")
        self.tagline_label.set_halign(Gtk.Align.START)
        self.tagline_label.set_xalign(0)
        self.tagline_label.set_visible(False)
        info_box.append(self.tagline_label)

        self.meta_label = Gtk.Label()
        self.meta_label.add_css_class("body")
        self.meta_label.set_halign(Gtk.Align.START)
        self.meta_label.set_xalign(0)
        info_box.append(self.meta_label)

        self.status_label = Gtk.Label()
        self.status_label.add_css_class("dim-label")
        self.status_label.set_halign(Gtk.Align.START)
        self.status_label.set_xalign(0)
        info_box.append(self.status_label)

        action_box = Gtk.Box(spacing=8)
        action_box.set_margin_top(8)
        info_box.append(action_box)

        self.watchlist_btn = Gtk.Button()
        self.watchlist_btn.add_css_class("suggested-action")
        self.watchlist_btn.add_css_class("pill")
        self.watchlist_btn.add_css_class("hero-btn")
        wl_box = Gtk.Box(spacing=6)
        self.watchlist_icon = Gtk.Image(icon_name="view-grid-symbolic")
        wl_box.append(self.watchlist_icon)
        self.watchlist_label = Gtk.Label(label="Add to Watchlist")
        wl_box.append(self.watchlist_label)
        self.watchlist_btn.set_child(wl_box)
        self.watchlist_btn.connect("clicked", self._toggle_watchlist)
        action_box.append(self.watchlist_btn)

        self.watched_btn = Gtk.Button()
        self.watched_btn.add_css_class("pill")
        self.watched_btn.add_css_class("hero-btn")
        w_box = Gtk.Box(spacing=6)
        self.watched_icon = Gtk.Image(icon_name="object-select-symbolic")
        w_box.append(self.watched_icon)
        self.watched_label = Gtk.Label(label="Mark Watched")
        w_box.append(self.watched_label)
        self.watched_btn.set_child(w_box)
        self.watched_btn.connect("clicked", self._toggle_watched)
        action_box.append(self.watched_btn)

        rate_btn = Gtk.Button()
        rate_btn.add_css_class("pill")
        rate_btn.add_css_class("hero-btn")
        r_box = Gtk.Box(spacing=6)
        r_box.append(Gtk.Image(icon_name="starred-symbolic"))
        r_box.append(Gtk.Label(label="Rate"))
        rate_btn.set_child(r_box)
        rate_btn.connect("clicked", self._rate_item)
        action_box.append(rate_btn)

        self.trailer_btn = Gtk.Button()
        self.trailer_btn.add_css_class("pill")
        self.trailer_btn.add_css_class("hero-btn")
        self.trailer_btn.add_css_class("trailer-btn")
        t_box = Gtk.Box(spacing=6)
        t_box.append(Gtk.Image(icon_name="media-playback-start-symbolic"))
        self.trailer_label = Gtk.Label(label="Trailer")
        t_box.append(self.trailer_label)
        self.trailer_btn.set_child(t_box)
        self.trailer_btn.connect("clicked", self._open_trailer)
        action_box.append(self.trailer_btn)

        self._trailer_title = ""
        self._trailer_year = None

        self.genres_box = Gtk.FlowBox()
        self.genres_box.set_margin_top(8)
        self.genres_box.set_halign(Gtk.Align.START)
        info_box.append(self.genres_box)

        self.overview_label = Gtk.Label()
        self.overview_label.set_wrap(True)
        self.overview_label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.overview_label.set_halign(Gtk.Align.FILL)
        self.overview_label.set_hexpand(True)
        self.overview_label.set_xalign(0)
        self.overview_label.set_margin_top(8)
        info_box.append(self.overview_label)

        self.episodes_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.episodes_box.set_margin_start(16)
        self.episodes_box.set_margin_end(16)
        self.episodes_box.set_margin_top(8)
        content_box.append(self.episodes_box)

        separator = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        separator.set_margin_top(16)
        separator.set_margin_bottom(8)
        content_box.append(separator)

        self.related_section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.related_section.set_margin_start(16)
        self.related_section.set_margin_end(16)
        self.related_section.set_margin_top(12)
        self.related_section.set_margin_bottom(16)
        self.related_section.set_visible(False)
        self.related_title = Gtk.Label(label="You May Also Like", halign=Gtk.Align.START)
        self.related_title.add_css_class("title-4")
        self.related_section.append(self.related_title)

        self.related_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.related_box.add_css_class("card-section")
        self.related_revealer = Gtk.Revealer()
        self.related_revealer.set_transition_type(Gtk.RevealerTransitionType.CROSSFADE)
        self.related_revealer.set_transition_duration(400)
        self.related_revealer.set_child(self._create_related_skeleton())
        self.related_box.append(self.related_revealer)
        self.related_section.append(self.related_box)

        self.cast_section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.cast_section.set_margin_start(16)
        self.cast_section.set_margin_end(16)
        self.cast_section.set_margin_top(28)
        self.cast_section.set_margin_bottom(16)
        self.cast_section.set_visible(False)
        self.cast_title = Gtk.Label(label="Cast & Crew", halign=Gtk.Align.START)
        self.cast_title.add_css_class("title-4")
        self.cast_section.append(self.cast_title)

        self.cast_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.cast_box.add_css_class("card-section")
        self.cast_revealer = Gtk.Revealer()
        self.cast_revealer.set_transition_type(Gtk.RevealerTransitionType.CROSSFADE)
        self.cast_revealer.set_transition_duration(400)
        self.cast_revealer.set_child(self._create_cast_skeleton())
        self.cast_box.append(self.cast_revealer)
        self.cast_section.append(self.cast_box)

        content_box.append(self.cast_section)
        content_box.append(self.related_section)

    def populate_hero(self, data, poster_pixbuf=None):
        """Populate the main detail content (hero). Related/cast load separately."""
        if self.media_type == "movie":
            self._populate_movie_hero(
                data.get("detail"),
                data.get("watchlist_ids", set()),
                data.get("watched_ids", set()),
                poster_pixbuf=poster_pixbuf,
            )
        else:
            self._populate_show_hero(
                data.get("detail"),
                data.get("seasons", []),
                data.get("season_episodes") or {},
                data.get("watchlist_ids", set()),
                data.get("watched_ids", set()),
                poster_pixbuf=poster_pixbuf,
            )
        self.related_section.set_visible(True)
        self.cast_section.set_visible(True)

    def populate_related(self, related):
        """Populate the related titles section."""
        GLib.idle_add(self._populate_related, related)

    def populate_cast(self, cast):
        """Populate the cast & crew section."""
        GLib.idle_add(self._populate_cast, cast)

    @staticmethod
    def _format_votes(votes):
        if votes is None:
            return ""
        if votes >= 1000:
            return f"{votes / 1000:.1f}k votes"
        return f"{votes} votes"

    def _create_related_skeleton(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        flow = Gtk.FlowBox()
        flow.set_selection_mode(Gtk.SelectionMode.NONE)
        flow.set_homogeneous(True)
        flow.set_column_spacing(20)
        flow.set_row_spacing(28)
        flow.set_min_children_per_line(5)
        flow.set_max_children_per_line(5)
        flow.set_valign(Gtk.Align.START)
        box.append(flow)

        for _ in range(5):
            card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            card.set_size_request(140, -1)

            frame = Gtk.Frame()
            frame.add_css_class("movie-poster-frame")
            frame.add_css_class("skeleton")
            frame.set_halign(Gtk.Align.CENTER)
            frame.set_size_request(140, 210)
            card.append(frame)

            line = Gtk.Box()
            line.add_css_class("skeleton")
            line.set_size_request(120, 16)
            line.set_margin_start(8)
            line.set_margin_end(8)
            line.set_margin_top(8)
            card.append(line)

            line2 = Gtk.Box()
            line2.add_css_class("skeleton")
            line2.set_size_request(80, 12)
            line2.set_margin_start(8)
            line2.set_margin_end(8)
            line2.set_margin_top(4)
            card.append(line2)

            flow.append(card)
        return box

    def _create_cast_skeleton(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        flow = Gtk.FlowBox()
        flow.set_selection_mode(Gtk.SelectionMode.NONE)
        flow.set_homogeneous(True)
        flow.set_column_spacing(12)
        flow.set_row_spacing(12)
        flow.set_min_children_per_line(2)
        flow.set_max_children_per_line(6)
        flow.set_valign(Gtk.Align.START)
        box.append(flow)

        for _ in range(6):
            card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            card.set_halign(Gtk.Align.CENTER)
            card.set_size_request(96, -1)

            avatar = Gtk.Box()
            avatar.add_css_class("skeleton")
            avatar.set_size_request(96, 96)
            avatar.set_halign(Gtk.Align.CENTER)
            avatar.set_overflow(Gtk.Overflow.HIDDEN)
            avatar.add_css_class("cast-avatar")
            card.append(avatar)

            line = Gtk.Box()
            line.add_css_class("skeleton")
            line.set_size_request(80, 14)
            line.set_margin_top(4)
            card.append(line)

            flow.append(card)
        return box

    def _fetch(self):
        try:
            results = {}
            if self.media_type == "movie":
                def fetch_one(key, fn):
                    try:
                        results[key] = fn()
                    except Exception:
                        results[key] = None if key in ("detail", "progress") else (set() if "ids" in key else [])

                threads = []
                for key, fn in [
                    ("detail", lambda: self.metadata_service.get_movie(self.item.tmdb_id)),
                    ("related", lambda: self.metadata_service.get_related_movies(self.item.tmdb_id)),
                    ("cast", lambda: self.metadata_service.get_movie_cast(self.item.tmdb_id)),
                    ("watchlist_ids", lambda: self.user_repo.get_watchlist_ids()),
                    ("watched_ids", lambda: self.user_repo.get_watched_ids("movie")),
                ]:
                    t = threading.Thread(target=fetch_one, args=(key, fn))
                    t.start()
                    threads.append(t)
                for t in threads:
                    t.join()

                detail = results.get("detail")
                if not detail:
                    GLib.idle_add(self._show_error, "Failed to load movie details")
                    return

                GLib.idle_add(self._populate_movie_hero, detail,
                              results.get("watchlist_ids", set()),
                              results.get("watched_ids", set()))
                GLib.idle_add(self._populate_related, results.get("related", []))
                GLib.idle_add(self._populate_cast, results.get("cast", []))
            else:
                def fetch_one(key, fn):
                    try:
                        results[key] = fn()
                    except Exception:
                        results[key] = None if key in ("detail", "progress") else (set() if "ids" in key else [])

                threads = []
                for key, fn in [
                    ("detail", lambda: self.metadata_service.get_show(self.item.tmdb_id)),
                    ("seasons", lambda: self.metadata_service.get_show_seasons(self.item.tmdb_id)),
                    ("related", lambda: self.metadata_service.get_related_shows(self.item.tmdb_id)),
                    ("cast", lambda: self.metadata_service.get_show_cast(self.item.tmdb_id)),
                    ("progress", lambda: None),
                    ("watchlist_ids", lambda: self.user_repo.get_watchlist_ids()),
                    ("watched_ids", lambda: self.user_repo.get_watched_ids("show")),
                ]:
                    t = threading.Thread(target=fetch_one, args=(key, fn))
                    t.start()
                    threads.append(t)
                for t in threads:
                    t.join()

                detail = results.get("detail")
                if not detail:
                    GLib.idle_add(self._show_error, "Failed to load show details")
                    return

                GLib.idle_add(self._populate_show_hero, detail,
                              results.get("seasons", []),
                              {},
                              results.get("watchlist_ids", set()),
                              results.get("watched_ids", set()))
                GLib.idle_add(self._populate_related, results.get("related", []))
                GLib.idle_add(self._populate_cast, results.get("cast", []))
        except NetworkError as e:
            GLib.idle_add(self._show_error, str(e))

    def _populate_movie_hero(self, movie, watchlist_ids, watched_ids,
                               poster_pixbuf=None):
        self._trailer_title = movie.title or ""
        self._trailer_year = movie.year

        self.title_label.set_text(movie.title or "")

        if movie.tagline:
            self.tagline_label.set_text(f'"{movie.tagline}"')
            self.tagline_label.set_visible(True)

        year_str = str(movie.year) if movie.year else ""
        runtime_str = f"⏱ {movie.runtime} min" if movie.runtime else ""
        rating_str = f"★ {movie.rating / 2:.1f}/5" if movie.rating else ""
        votes_str = self._format_votes(movie.votes) if movie.votes else ""
        cert_str = movie.certification if movie.certification else ""
        meta_parts = [p for p in [year_str, runtime_str, cert_str, rating_str, votes_str] if p]
        self.meta_label.set_text("  ·  ".join(meta_parts))

        if movie.overview:
            self.overview_label.set_text(movie.overview)
        else:
            self.overview_label.set_visible(False)

        if movie.genres:
            for genre in movie.genres:
                chip = Gtk.Label(label=genre.title())
                chip.add_css_class("chip")
                self.genres_box.append(chip)

        if poster_pixbuf:
            try:
                texture = Gdk.Texture.new_for_pixbuf(poster_pixbuf)
                self.poster_area.set_paintable(texture)
                self.poster_area.set_opacity(1.0)
            except GLib.Error:
                if movie.poster_url:
                    load_poster(movie.poster_url, self.poster_area)
        elif movie.poster_url:
            load_poster(movie.poster_url, self.poster_area)

        self.episodes_box.set_visible(False)

        self._in_watchlist = self.item.tmdb_id in watchlist_ids
        self._set_watchlist_ui()
        self._is_watched = self.item.tmdb_id in watched_ids
        self._set_watched_ui()
        return False

    def _populate_show_hero(self, show, seasons, season_episodes, watchlist_ids, watched_ids,
                             poster_pixbuf=None):
        self._trailer_title = show.title or ""
        self._trailer_year = show.year

        self.title_label.set_text(show.title or "")

        year_str = str(show.year) if show.year else ""
        runtime_str = f"⏱ {show.runtime} min" if show.runtime else ""
        rating_str = f"★ {show.rating / 2:.1f}/5" if show.rating else ""
        votes_str = self._format_votes(show.votes) if show.votes else ""
        cert_str = show.certification if show.certification else ""
        meta_parts = [p for p in [year_str, runtime_str, cert_str, rating_str, votes_str] if p]
        self.meta_label.set_text("  ·  ".join(meta_parts))

        status_str = show.status or ""
        self.status_label.set_text(status_str)
        self.status_label.set_visible(bool(status_str))

        if show.overview:
            self.overview_label.set_text(show.overview)
        else:
            self.overview_label.set_visible(False)

        if show.genres:
            for genre in show.genres:
                chip = Gtk.Label(label=genre.title())
                chip.add_css_class("chip")
                self.genres_box.append(chip)

        if poster_pixbuf:
            try:
                texture = Gdk.Texture.new_for_pixbuf(poster_pixbuf)
                self.poster_area.set_paintable(texture)
                self.poster_area.set_opacity(1.0)
            except GLib.Error:
                if show.poster_url:
                    load_poster(show.poster_url, self.poster_area)
        elif show.poster_url:
            load_poster(show.poster_url, self.poster_area)

        self._in_watchlist = self.item.tmdb_id in watchlist_ids
        self._set_watchlist_ui()

        self._season_episodes = season_episodes or {}
        self._watched_episodes = self.user_repo.get_watched_episodes_for_show(
            self.item.tmdb_id
        )
        self._watched_seasons = {
            s.season_number
            for s in seasons
            if self._season_fully_watched(s.season_number)
        }
        self._recompute_is_watched()

        seasons_list = Gtk.ListBox()
        seasons_list.add_css_class("boxed-list")
        seasons_list.set_selection_mode(Gtk.SelectionMode.NONE)
        seasons_list.set_margin_top(8)
        self.episodes_box.append(seasons_list)
        self._season_expanders = []

        for season in seasons:
            season.episodes = self._season_episodes.get(season.season_number, [])
            expander = Adw.ExpanderRow()
            expander.set_title(f"Season {season.season_number}")
            expander.set_subtitle(f"{season.episode_count} episodes")
            expander.set_expanded(False)
            self._season_expanders.append(expander)

            state = {"loaded": False, "ep_checks": [], "season_number": season.season_number, "season": season}
            expander._season_state = state

            season_check = Gtk.CheckButton()
            season_check.set_valign(Gtk.Align.CENTER)
            season_check.set_tooltip_text("Mark whole season as watched")
            season_check.set_active(season.season_number in self._watched_seasons)
            season_check.connect("toggled", self._on_season_toggled, season, state)
            expander.add_suffix(season_check)
            state["season_check"] = season_check

            expander.connect("notify::expanded", self._on_season_expanded, season, state)
            seasons_list.append(expander)
        return False

    def _season_fully_watched(self, season_number):
        """True if every aired episode of the season is in the watched set."""
        episodes = self._season_episodes.get(season_number, [])
        if not episodes:
            for expander in getattr(self, "_season_expanders", []):
                state = getattr(expander, "_season_state", None)
                if state and state.get("season_number") == season_number:
                    season = state.get("season")
                    if season is not None:
                        episodes = season.episodes
                    break
        aired = [ep for ep in episodes if self._is_aired(ep)]
        if not aired:
            return False
        return all(
            (ep.season_number, ep.episode_number) in self._watched_episodes
            for ep in aired
        )

    def _recompute_is_watched(self):
        """Derive the show-level watched state from episode state.
        Movies keep the simple marker toggle."""
        if self.media_type != "show":
            self._is_watched = not self._is_watched
            self._set_watched_ui()
            return
        season_episodes = getattr(self, "_season_episodes", {})
        if not season_episodes:
            self._is_watched = False
            self._set_watched_ui()
            return
        aired_any = False
        fully = True
        for season_number, episodes in season_episodes.items():
            if season_number <= 0:
                continue
            for ep in episodes:
                if not self._is_aired(ep):
                    continue
                aired_any = True
                if (ep.season_number, ep.episode_number) not in self._watched_episodes:
                    fully = False
                    break
            if not fully:
                break
        self._is_watched = fully and aired_any
        self._set_watched_ui()

    def _sync_season_check(self, season_number):
        """Update a season checkbox from the current watched state."""
        for expander in self._season_expanders:
            state = getattr(expander, "_season_state", None)
            if not state or state.get("season_number") != season_number:
                continue
            check = state.get("season_check")
            if check is None:
                return
            fully = self._season_fully_watched(season_number)
            check.handler_block_by_func(self._on_season_toggled)
            check.set_active(fully)
            check.handler_unblock_by_func(self._on_season_toggled)
            if fully:
                self._watched_seasons.add(season_number)
            else:
                self._watched_seasons.discard(season_number)
            return

    def _on_season_expanded(self, expander, _param, season, state):
        if not expander.get_expanded():
            return False
        if state.get("loaded"):
            return False
        state["loaded"] = True
        GLib.Thread.new(
            "season-episodes",
            self._load_season_episodes,
            season,
            expander,
            state,
        )
        return False

    def _load_season_episodes(self, season, expander, state):
        try:
            episodes = self.metadata_service.get_season_episodes(
                self.item.tmdb_id, season.season_number
            )
            season.episodes = episodes
        except NetworkError:
            episodes = []
        GLib.idle_add(self._populate_season_episodes, season, expander, state, episodes)

    @staticmethod
    def _is_aired(ep) -> bool:
        if not ep.air_date:
            return True
        try:
            return datetime.date.fromisoformat(ep.air_date) <= datetime.date.today()
        except ValueError:
            return True

    def _populate_season_episodes(self, season, expander, state, episodes):
        ep_checks = state["ep_checks"]
        for ep in episodes:
            aired = self._is_aired(ep)
            ep_row = Adw.ActionRow(
                title=f"S{ep.season_number:02d}E{ep.episode_number:02d}",
                subtitle=ep.title if aired else f"{ep.title} · (not yet aired)"
                if ep.title else "(not yet aired)",
            )
            check = Gtk.CheckButton()
            check.set_valign(Gtk.Align.CENTER)
            check.set_active(
                aired
                and (ep.season_number, ep.episode_number) in self._watched_episodes
            )
            if not aired:
                check.set_sensitive(False)
                check.set_tooltip_text("Episode has not aired yet")
            check.connect("toggled", self._on_episode_toggled, ep)
            ep_row.add_suffix(check)
            ep_row.set_activatable_widget(check)
            if not aired:
                ep_row.add_css_class("dim-label")
            expander.add_row(ep_row)
            ep_checks.append((ep, check))
        self._sync_season_check(season.season_number)
        return False

    def _on_season_toggled(self, check, season, state):
        wanted = check.get_active()
        check.set_sensitive(False)
        GLib.Thread.new(
            "season-watched",
            self._do_toggle_season,
            season,
            wanted,
            check,
            state,
        )

    def _do_toggle_season(self, season, wanted, check, state):
        try:
            if not season.episodes:
                season.episodes = self.metadata_service.get_season_episodes(
                    self.item.tmdb_id, season.season_number
                )
            for ep in season.episodes:
                if wanted and not self._is_aired(ep):
                    continue
                if wanted:
                    self.user_repo.mark_watched(
                        tmdb_id=ep.tmdb_id,
                        media_type="episode",
                        show_tmdb_id=ep.show_tmdb_id,
                        season_number=ep.season_number,
                        episode_number=ep.episode_number,
                    )
                else:
                    self.user_repo.mark_unwatched(
                        tmdb_id=ep.tmdb_id,
                        media_type="episode",
                        show_tmdb_id=ep.show_tmdb_id,
                        season_number=ep.season_number,
                        episode_number=ep.episode_number,
                    )
            for ep in season.episodes:
                key = (ep.season_number, ep.episode_number)
                if wanted:
                    if self._is_aired(ep):
                        self._watched_episodes.add(key)
                else:
                    self._watched_episodes.discard(key)
            if wanted:
                self._watched_seasons.add(season.season_number)
            else:
                self._watched_seasons.discard(season.season_number)
            GLib.idle_add(
                self._season_toggle_done, check, wanted, state["ep_checks"], None,
                season.season_number,
            )
        except (sqlite3.Error, NetworkError) as e:
            GLib.idle_add(
                self._season_toggle_done, check, wanted, state["ep_checks"], e,
                season.season_number,
            )

    def _season_toggle_done(self, check, wanted, ep_checks, error, season_number):
        check.set_sensitive(True)
        for ep, ep_check in ep_checks:
            if wanted and not self._is_aired(ep):
                continue
            ep_check.handler_block_by_func(self._on_episode_toggled)
            ep_check.set_active(wanted)
            ep_check.handler_unblock_by_func(self._on_episode_toggled)
        if error is not None:
            check.handler_block_by_func(self._on_season_toggled)
            check.set_active(not wanted)
            check.handler_unblock_by_func(self._on_season_toggled)
        else:
            self._sync_season_check(season_number)
            self._recompute_is_watched()
            self._invalidate_library_pages()
        return False

    def _on_episode_toggled(self, check, ep):
        wanted = check.get_active()
        check.set_sensitive(False)
        GLib.Thread.new("episode-watched", self._do_toggle_episode, ep, wanted, check)

    def _do_toggle_episode(self, ep, wanted, check):
        key = (ep.season_number, ep.episode_number)
        try:
            if wanted:
                self.user_repo.mark_watched(
                    tmdb_id=ep.tmdb_id,
                    media_type="episode",
                    show_tmdb_id=ep.show_tmdb_id,
                    season_number=ep.season_number,
                    episode_number=ep.episode_number,
                )
            else:
                self.user_repo.mark_unwatched(
                    tmdb_id=ep.tmdb_id,
                    media_type="episode",
                    show_tmdb_id=ep.show_tmdb_id,
                    season_number=ep.season_number,
                    episode_number=ep.episode_number,
                )
            if wanted:
                self._watched_episodes.add(key)
            else:
                self._watched_episodes.discard(key)
            GLib.idle_add(self._episode_toggle_done, check, None, ep)
        except sqlite3.Error as e:
            GLib.idle_add(self._episode_toggle_done, check, e, ep)

    def _episode_toggle_done(self, check, error, ep):
        check.set_sensitive(True)
        if error is not None:
            check.handler_block_by_func(self._on_episode_toggled)
            check.set_active(not check.get_active())
            check.handler_unblock_by_func(self._on_episode_toggled)
        else:
            self._sync_season_check(ep.season_number)
            self._recompute_is_watched()
            self._invalidate_library_pages()
        return False

    def _populate_related(self, items):
        if not items:
            self.related_section.set_visible(False)
            return

        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.related_revealer.set_child(inner)

        flow = Gtk.FlowBox()
        flow.set_selection_mode(Gtk.SelectionMode.NONE)
        flow.set_homogeneous(True)
        flow.set_column_spacing(20)
        flow.set_row_spacing(28)
        flow.set_min_children_per_line(5)
        flow.set_max_children_per_line(5)
        flow.set_valign(Gtk.Align.START)
        inner.append(flow)

        for item in items[:10]:
            mt = "movie" if isinstance(item, Movie) else "show"

            button = Gtk.Button()
            button.add_css_class("movie-card-button")
            button.set_halign(Gtk.Align.CENTER)
            button.set_valign(Gtk.Align.START)
            button.connect("clicked", self._on_related_click, item, mt)

            card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            card.add_css_class("movie-card")
            card.set_size_request(140, -1)

            frame = Gtk.Frame()
            frame.add_css_class("movie-poster-frame")
            frame.set_halign(Gtk.Align.CENTER)
            frame.set_valign(Gtk.Align.START)

            paintable = FixedPaintable(140, 210)
            picture = Gtk.Picture()
            picture.set_paintable(paintable)
            picture.set_content_fit(Gtk.ContentFit.COVER)
            picture.set_can_shrink(False)
            picture.set_overflow(Gtk.Overflow.HIDDEN)
            picture.set_size_request(140, 210)
            picture.add_css_class("movie-poster")
            frame.set_child(picture)
            card.append(frame)

            info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            info.set_margin_start(8)
            info.set_margin_end(8)
            info.set_margin_bottom(12)

            title_label = Gtk.Label(label=item.title)
            title_label.add_css_class("heading")
            title_label.set_xalign(0)
            title_label.set_valign(Gtk.Align.START)
            title_label.set_ellipsize(Pango.EllipsizeMode.END)
            title_label.set_max_width_chars(16)
            info.append(title_label)

            if item.year:
                year_label = Gtk.Label(label=str(item.year))
                year_label.add_css_class("caption")
                year_label.add_css_class("dim-label")
                year_label.set_xalign(0)
                info.append(year_label)

            type_label = Gtk.Label(
                label="TV Show" if mt == "show" else "Movie"
            )
            type_label.add_css_class("caption")
            type_label.add_css_class("dim-label")
            type_label.set_xalign(0)
            info.append(type_label)

            card.append(info)
            button.set_child(card)
            flow.append(button)

            if item.poster_url:
                GLib.Thread.new(
                    "related-poster",
                    self._load_and_apply_poster,
                    item.poster_url,
                    paintable,
                    picture,
                )

        self.related_section.set_visible(True)
        self.related_revealer.set_reveal_child(True)
        return False

    def _load_and_apply_poster(self, url, paintable, picture):
        with POSTER_SLOTS:
            try:
                pixbuf = _load_texture_sync(url)
                if pixbuf:
                    texture = Gdk.Texture.new_for_pixbuf(pixbuf)
                    GLib.idle_add(self._apply_poster_texture, paintable, picture, texture)
            except GLib.Error:
                pass

    def _apply_poster_texture(self, paintable, picture, texture):
        paintable.set_texture(texture)
        fade_in(picture, 300)
        return False

    def _on_related_click(self, card, item, mtype):
        card.remove_css_class("pressed")
        if self.main_page:
            self.main_page.show_detail(mtype, item)

    def _populate_cast(self, members):
        if not members:
            self.cast_section.set_visible(False)
            return

        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        inner.set_hexpand(True)
        self.cast_revealer.set_child(inner)

        grid = Gtk.Grid()
        grid.set_column_spacing(12)
        grid.set_row_spacing(12)
        grid.set_column_homogeneous(True)
        grid.set_halign(Gtk.Align.FILL)
        grid.set_hexpand(True)
        inner.append(grid)

        COLS = 5

        for idx, m in enumerate(members):
            col = idx % COLS
            row = idx // COLS

            card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            card.set_size_request(96, 184)
            card.set_halign(Gtk.Align.CENTER)
            card.set_valign(Gtk.Align.START)
            card.set_hexpand(True)
            card.add_css_class("cast-card")

            avatar_box, avatar_paintable, avatar = create_avatar(96)
            card.append(avatar_box)

            info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            info.set_margin_start(4)
            info.set_margin_end(4)
            info.set_margin_bottom(8)
            info.set_vexpand(True)
            info.set_size_request(-1, 72)

            display_name = m.name.replace(" ", "\n")
            name_label = Gtk.Label(label=display_name)
            name_label.set_ellipsize(Pango.EllipsizeMode.END)
            name_label.set_max_width_chars(14)
            name_label.set_lines(2)
            name_label.set_size_request(-1, 36)
            name_label.set_xalign(0)
            name_label.set_valign(Gtk.Align.START)
            name_label.add_css_class("heading")
            info.append(name_label)

            if m.character:
                char_label = Gtk.Label(label=m.character)
                char_label.set_ellipsize(Pango.EllipsizeMode.END)
                char_label.set_max_width_chars(14)
                char_label.set_xalign(0)
                char_label.set_valign(Gtk.Align.START)
                char_label.add_css_class("caption")
                char_label.add_css_class("dim-label")
                info.append(char_label)

            card.append(info)
            grid.attach(card, col, row, 1, 1)

            if m.photo_url:
                load_avatar(m.photo_url, avatar_paintable, avatar)

        self.cast_section.set_visible(True)
        self.cast_revealer.set_reveal_child(True)
        return False

    def cancel(self):
        self._cancelled = True

    def _set_watchlist_ui(self):
        if self._in_watchlist:
            self.watchlist_icon.set_from_icon_name("list-remove-symbolic")
            self.watchlist_label.set_text("Remove from Watchlist")
        else:
            self.watchlist_icon.set_from_icon_name("view-grid-symbolic")
            self.watchlist_label.set_text("Add to Watchlist")

    def _toggle_watchlist(self, btn):
        btn.set_sensitive(False)
        GLib.Thread.new("watchlist-toggle", self._do_toggle_watchlist, btn)

    def _do_toggle_watchlist(self, btn):
        try:
            if self._in_watchlist:
                self.user_repo.remove_from_watchlist(self.item.tmdb_id, self.media_type)
            else:
                self.user_repo.add_to_watchlist(self.item.tmdb_id, self.media_type)
            GLib.idle_add(self._watchlist_done, btn)
        except sqlite3.Error:
            GLib.idle_add(btn.set_sensitive, True)

    def _watchlist_done(self, btn):
        self._in_watchlist = not self._in_watchlist
        self._set_watchlist_ui()
        btn.set_sensitive(True)
        if self.main_page is not None:
            self.main_page.invalidate_page("watchlist")
            self.main_page.invalidate_page("profile")
        return False

    def _set_watched_ui(self):
        if self._is_watched:
            self.watched_label.set_text("Watched")
            self.watched_btn.add_css_class("watched-active")
        else:
            self.watched_label.set_text("Mark Watched")
            self.watched_btn.remove_css_class("watched-active")

    def _toggle_watched(self, btn):
        btn.set_sensitive(False)
        GLib.Thread.new("toggle-watched", self._do_toggle_watched, btn)

    def _do_toggle_watched(self, btn):
        try:
            if self._is_watched:
                self._do_unmark_watched()
            else:
                self._do_mark_watched()
            GLib.idle_add(self._watch_done, btn)
        except sqlite3.Error:
            GLib.idle_add(btn.set_sensitive, True)

    def _do_mark_watched(self):
        if self.media_type == "show":
            self._mark_all_watched_show()
        else:
            self.user_repo.mark_watched(self.item.tmdb_id, self.media_type)

    def _do_unmark_watched(self):
        if self.media_type == "show":
            self._mark_all_unwatched_show()
        else:
            self.user_repo.mark_unwatched(self.item.tmdb_id, self.media_type)

    def _mark_all_watched_show(self):
        """Mark the show and every aired episode as watched."""
        self.user_repo.mark_watched(self.item.tmdb_id, "show")
        seasons = self.metadata_service.get_show_seasons(self.item.tmdb_id)
        for season in seasons:
            episodes = self.metadata_service.get_season_episodes(
                self.item.tmdb_id, season.season_number
            )
            for ep in episodes:
                if not self._is_aired(ep):
                    continue
                self.user_repo.mark_watched(
                    tmdb_id=ep.tmdb_id,
                    media_type="episode",
                    show_tmdb_id=ep.show_tmdb_id,
                    season_number=ep.season_number,
                    episode_number=ep.episode_number,
                )
                self._watched_episodes.add((ep.season_number, ep.episode_number))
            self._watched_seasons.add(season.season_number)

    def _mark_all_unwatched_show(self):
        """Unmark the show and every episode."""
        self.user_repo.mark_unwatched(self.item.tmdb_id, "show")
        seasons = self.metadata_service.get_show_seasons(self.item.tmdb_id)
        for season in seasons:
            episodes = self.metadata_service.get_season_episodes(
                self.item.tmdb_id, season.season_number
            )
            for ep in episodes:
                self.user_repo.mark_unwatched(
                    tmdb_id=ep.tmdb_id,
                    media_type="episode",
                    show_tmdb_id=ep.show_tmdb_id,
                    season_number=ep.season_number,
                    episode_number=ep.episode_number,
                )
                self._watched_episodes.discard((ep.season_number, ep.episode_number))
            self._watched_seasons.discard(season.season_number)

    def _invalidate_library_pages(self):
        if self.main_page is not None:
            self.main_page.invalidate_page("history")
            self.main_page.invalidate_page("watchlist")
            self.main_page.invalidate_page("profile")

    def _watch_done(self, btn):
        self._recompute_is_watched()
        btn.set_sensitive(True)
        if self.media_type == "show":
            for expander in getattr(self, "_season_expanders", []):
                state = getattr(expander, "_season_state", None)
                if not state:
                    continue
                season_check = state.get("season_check")
                if season_check is not None:
                    season_check.handler_block_by_func(self._on_season_toggled)
                    season_check.set_active(self._is_watched)
                    season_check.handler_unblock_by_func(self._on_season_toggled)
                for ep, ep_check in state.get("ep_checks", []):
                    if not self._is_watched or self._is_aired(ep):
                        ep_check.handler_block_by_func(self._on_episode_toggled)
                        ep_check.set_active(self._is_watched)
                        ep_check.handler_unblock_by_func(self._on_episode_toggled)
        self._invalidate_library_pages()
        return False

    def _rate_item(self, btn):
        def _on_saved():
            if self.main_page is not None:
                self.main_page.invalidate_page("profile")

        dialog = RatingDialog(
            self.user_repo,
            self.media_type,
            self.item.tmdb_id,
            title=self.item.title,
            on_saved=_on_saved,
        )
        dialog.present(self.get_native())
        dialog.attach_click_away()

    def _open_trailer(self, btn):
        query = self._trailer_title
        if self._trailer_year:
            query += f" {self._trailer_year}"
        query += " official trailer"
        url = f"https://www.youtube.com/results?search_query={urllib.parse.quote_plus(query)}"
        Gio.App_info_launch_default_for_uri(url, None)

    def _show_error(self, msg):
        self.title_label.set_text(f"Error: {msg}")
        return False


class RatingDialog(Adw.Dialog):
    """Star-based rating dialog with hover preview.

    Pre-fills the existing rating when present. "Submit Rating" saves and
    closes; "Clear Rating" removes the rating and stays open.
    """

    def __init__(self, user_repo, media_type, tmdb_id, title="", on_saved=None):
        super().__init__()
        self._repo = user_repo
        self._media_type = media_type
        self._tmdb_id = tmdb_id
        self._title = title
        self._on_saved = on_saved
        self._rating = 0
        self._preview = 0
        self._stars = []

        self.set_title("Rate")
        self.set_content_width(340)
        # Floating dialog: clicking outside or pressing Escape always
        # dismisses it and discards any unsubmitted star selection.
        self.set_presentation_mode(Adw.DialogPresentationMode.FLOATING)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        box.set_margin_top(24)
        box.set_margin_bottom(24)
        box.set_margin_start(24)
        box.set_margin_end(24)

        if title:
            title_lbl = Gtk.Label(label=title)
            title_lbl.add_css_class("title-4")
            title_lbl.set_halign(Gtk.Align.CENTER)
            box.append(title_lbl)

        self.star_row = Gtk.Box(spacing=6)
        self.star_row.set_halign(Gtk.Align.CENTER)
        for i in range(1, 6):
            btn = Gtk.Button()
            btn.set_valign(Gtk.Align.CENTER)
            btn.add_css_class("flat")
            btn.add_css_class("rating-star")
            btn.set_label("\u2605")
            btn.set_size_request(40, 40)
            btn.connect("clicked", self._on_star_clicked, i)
            motion = Gtk.EventControllerMotion()
            motion.connect("enter", self._on_star_enter, i)
            motion.connect("leave", self._on_star_leave)
            btn.add_controller(motion)
            self.star_row.append(btn)
            self._stars.append(btn)
        box.append(self.star_row)

        self.rating_label = Gtk.Label(label="No rating yet")
        self.rating_label.add_css_class("dim-label")
        self.rating_label.set_halign(Gtk.Align.CENTER)
        box.append(self.rating_label)

        btn_row = Gtk.Box(spacing=8)
        btn_row.set_halign(Gtk.Align.CENTER)

        self.clear_btn = Gtk.Button(label="Clear Rating")
        self.clear_btn.add_css_class("destructive-action")
        self.clear_btn.connect("clicked", self._clear_rating)
        btn_row.append(self.clear_btn)

        self.save_btn = Gtk.Button(label="Submit Rating")
        self.save_btn.add_css_class("suggested-action")
        self.save_btn.connect("clicked", self._submit)
        btn_row.append(self.save_btn)

        box.append(btn_row)

        self.set_child(box)

        self._rating = self._get_existing_rating()
        self._preview = 0
        self._refresh_stars()
        self._refresh_label()
        self.save_btn.set_sensitive(False)
        self.clear_btn.set_sensitive(self._rating > 0)

    def attach_click_away(self):
        """Close the dialog when the user clicks outside it.

        Adw.Dialog's floating sheet does not dismiss on backdrop click, so
        we listen for presses with a bubble-phase gesture attached to the
        dialog widget itself (which fills the window) and close when the
        click lands outside the dialog's content.
        """
        gesture = Gtk.GestureClick()
        gesture.set_propagation_phase(Gtk.PropagationPhase.BUBBLE)
        gesture.connect("pressed", self._on_dialog_pressed)
        self.add_controller(gesture)
        self._click_away_gesture = gesture
        self.connect("closed", self._on_closed_cleanup, gesture)

    def _on_dialog_pressed(self, gesture, _n_press, x, y):
        child = self.get_child()
        if child is None:
            self.close()
            return
        try:
            picked = self.pick(x, y, Gtk.PickFlags.DEFAULT)
        except GLib.Error:
            return
        if picked is None:
            self.close()
            return
        inside = child is picked or child.is_ancestor(picked)
        if not inside:
            self.close()

    def _on_closed_cleanup(self, _dialog, gesture):
        self.remove_controller(gesture)

    def _get_existing_rating(self):
        try:
            for r in self._repo.get_ratings(self._media_type):
                if r["tmdb_id"] == self._tmdb_id:
                    return int(r["rating"])
        except (KeyError, ValueError, sqlite3.Error):
            pass
        return 0

    def _on_star_enter(self, _controller, _x, _y, i):
        self._preview = i
        self._refresh_stars()

    def _on_star_leave(self, _controller, _x, _y):
        self._preview = 0
        self._refresh_stars()

    def _on_star_clicked(self, _btn, i):
        self._rating = i
        self._preview = 0
        self._refresh_stars()
        self._refresh_label()
        self.save_btn.set_sensitive(True)

    def _refresh_stars(self):
        active = self._preview or self._rating
        for idx, btn in enumerate(self._stars, start=1):
            if idx <= active:
                btn.add_css_class("star-filled")
                btn.remove_css_class("star-empty")
            else:
                btn.remove_css_class("star-filled")
                btn.add_css_class("star-empty")

    def _refresh_label(self):
        if self._rating > 0:
            self.rating_label.set_text(f"Your rating: {self._rating}/5")
        else:
            self.rating_label.set_text("No rating yet")

    def _submit(self, btn):
        if self._rating <= 0:
            return
        self._repo.rate_item(self._tmdb_id, self._media_type, self._rating)
        if self._on_saved:
            self._on_saved()
        self.close()

    def _clear_rating(self, btn):
        self._repo.remove_rating(self._tmdb_id, self._media_type)
        if self._on_saved:
            self._on_saved()
        self._rating = 0
        self._preview = 0
        self._refresh_stars()
        self._refresh_label()
        self.save_btn.set_sensitive(False)
        self.clear_btn.set_sensitive(False)
