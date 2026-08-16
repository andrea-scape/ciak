"""Shared profile base: header, stat sections, reviewed hook."""

import getpass
import sqlite3
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, GLib
from types import SimpleNamespace

from .anim import stagger_fade_in


def _format_duration(total_minutes):
    if total_minutes <= 0:
        return "—"
    hours, minutes = divmod(int(total_minutes), 60)
    if hours == 0:
        return f"{minutes}m"
    if minutes == 0:
        return f"{hours}h"
    return f"{hours}h {minutes}m"


def _animate_stat_value(label, target):
    if target == 0:
        label.set_text("0")
        return
    duration_ms = 300
    steps = 20
    step_time = max(1, duration_ms // steps)
    current_step = [0]

    def animate():
        current_step[0] += 1
        progress = current_step[0] / steps
        value = int(target * progress)
        label.set_text(str(value))
        return current_step[0] < steps

    GLib.timeout_add(step_time, animate)


def _make_empty_label():
    empty = Gtk.Label(label="No ratings yet")
    empty.add_css_class("dim-label")
    empty.add_css_class("title-4")
    empty.set_halign(Gtk.Align.CENTER)
    empty.set_margin_top(16)
    empty.set_margin_bottom(16)
    return empty


class ProfileBase(Gtk.Box):
    """Shared header + Watched/Watchlist stats. Subclasses override
    _build_reviewed() and _populate_reviewed()."""

    def __init__(self, win, user_repo, metadata_service, main_page=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.win = win
        self.user_repo = user_repo
        self.metadata_service = metadata_service
        self.main_page = main_page
        self.set_valign(Gtk.Align.FILL)
        self.add_css_class("ciak-dashboard")

        self._labels = {}
        self._items = []
        self._reload_gen = 0

        clamp = Adw.Clamp(maximum_size=1400)
        clamp.set_tightening_threshold(900)
        clamp.set_margin_start(28)
        clamp.set_margin_end(28)
        clamp.set_margin_top(32)
        clamp.set_margin_bottom(32)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        self._add_header(content)

        content.append(self._build_section("Watched", [
            ("video-x-generic-symbolic", "Movies", "movies_label"),
            ("tv-symbolic", "Shows", "shows_label"),
            ("media-playback-start-symbolic", "Episodes", "episodes_label"),
            ("alarm-symbolic", "Watch time", "watched_time_label"),
        ]))

        content.append(self._spacer(8))

        content.append(self._build_section("Watchlist", [
            ("video-x-generic-symbolic", "Movies", "wl_movies_label"),
            ("tv-symbolic", "Shows", "wl_shows_label"),
            ("media-playback-start-symbolic", "Episodes to watch", "wl_episodes_label"),
            ("alarm-symbolic", "Watch time", "wl_runtime_label"),
        ]))

        content.append(self._spacer(48))

        self.reviewed_section = self._build_reviewed()
        content.append(self.reviewed_section)

        clamp.set_child(content)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_vexpand(True)
        scrolled.set_child(clamp)
        self.append(scrolled)
        self._load()

    def _load(self):
        self._reload_gen += 1
        gen = self._reload_gen
        GLib.Thread.new("profile", self._fetch, gen)

    def _spacer(self, h):
        s = Gtk.Box()
        s.set_size_request(-1, h)
        return s

    def _add_header(self, content):
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=18)
        header.set_halign(Gtk.Align.CENTER)
        header.set_margin_bottom(36)
        avatar = Adw.Avatar.new(84, None, True)
        avatar.set_text("U")
        avatar.add_css_class("profile-avatar")
        header.append(avatar)
        name_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        name_box.set_valign(Gtk.Align.CENTER)
        name = Gtk.Label(label=getpass.getuser() or "Profile")
        name.add_css_class("title-2")
        name.set_halign(Gtk.Align.START)
        name_box.append(name)
        subtitle = Gtk.Label(label="Offline Mode")
        subtitle.add_css_class("dim-label")
        subtitle.set_halign(Gtk.Align.START)
        name_box.append(subtitle)
        header.append(name_box)
        content.append(header)

    def _build_section(self, title, stat_defs):
        section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        section.set_halign(Gtk.Align.CENTER)
        title_label = Gtk.Label(label=title)
        title_label.add_css_class("title-4")
        title_label.set_halign(Gtk.Align.START)
        section.append(title_label)

        flowbox = Gtk.FlowBox()
        flowbox.set_valign(Gtk.Align.START)
        flowbox.set_homogeneous(True)
        flowbox.set_column_spacing(20)
        flowbox.set_row_spacing(28)
        flowbox.set_min_children_per_line(2)
        flowbox.set_max_children_per_line(4)
        flowbox.set_selection_mode(Gtk.SelectionMode.NONE)

        for icon_name, label_text, key in stat_defs:
            item = self._make_stat_card(icon_name, label_text)
            flowbox.append(item)
            self._items.append(item)
            val_label = item.get_first_child().get_next_sibling()
            self._labels[key] = val_label

        section.append(flowbox)
        return section

    def _make_stat_card(self, icon_name, label_text):
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        card.add_css_class("gallery-stat-card")

        icon = Gtk.Image(icon_name=icon_name)
        icon.set_pixel_size(24)
        icon.add_css_class("dim-label")
        card.append(icon)

        val = Gtk.Label(label="0")
        val.add_css_class("stat-value")
        val.add_css_class("title-3")
        card.append(val)

        lbl = Gtk.Label(label=label_text)
        lbl.add_css_class("stat-label")
        lbl.add_css_class("caption")
        card.append(lbl)

        return card

    def _fetch(self, gen):
        try:
            stats = self.user_repo.get_stats()
            wl_stats = self.user_repo.get_watchlist_stats()
            watched_runtime = self.user_repo.get_watched_runtime()
            ratings = self.user_repo.get_ratings()
            rated = [SimpleNamespace(**r) for r in ratings[:12]]
            GLib.idle_add(self._populate, gen, stats, wl_stats, watched_runtime, rated)
        except sqlite3.Error:
            pass

    def _populate(self, gen, stats, wl_stats, watched_runtime, rated):
        if gen != self._reload_gen:
            return False
        stagger_fade_in(self._items, delay_ms=30, duration_ms=250, after_ms=80)
        _animate_stat_value(self._labels["movies_label"], stats.movies_watched)
        _animate_stat_value(self._labels["shows_label"], stats.shows_watched)
        _animate_stat_value(self._labels["episodes_label"], stats.episodes_watched)
        _animate_stat_value(self._labels["wl_movies_label"], wl_stats["movie_count"])
        _animate_stat_value(self._labels["wl_shows_label"], wl_stats["show_count"])
        _animate_stat_value(self._labels["wl_episodes_label"], wl_stats["episodes_to_watch"])
        watched_time = _format_duration(watched_runtime)
        if watched_time != "—":
            watched_time = f"\u2248{watched_time}"
        self._labels["watched_time_label"].set_text(watched_time)
        watch_time = _format_duration(wl_stats["total_runtime"])
        if watch_time != "—":
            watch_time = f"\u2248{watch_time}"
        self._labels["wl_runtime_label"].set_text(watch_time)

        self._populate_reviewed(rated)
        return False

    def _build_reviewed(self):
        raise NotImplementedError

    def _populate_reviewed(self, rated):
        raise NotImplementedError
