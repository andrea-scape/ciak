"""Calendar: monthly grid of upcoming episode air dates for watchlist shows."""

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Pango", "1.0")
from gi.repository import Gtk, GLib, Pango
import datetime
import calendar as cal_mod
import threading

from ..domain.exceptions import NetworkError
from ..domain.models import Episode
from .poster import create_poster, load_poster

MONTHS_SHORT = [
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
]
MONTHS_FULL = [
    "January", "February", "March", "April",
    "May", "June", "July", "August",
    "September", "October", "November", "December",
]
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


class CalendarPage(Gtk.Box):
    def __init__(self, win, user_repo, metadata_service, main_page=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.win = win
        self.user_repo = user_repo
        self.metadata_service = metadata_service
        self.main_page = main_page

        self._airings = {}
        self._upcoming = []
        self._shows = {}
        self._fetch_gen = 0

        today = datetime.date.today()
        self._year = today.year
        self._month = today.month

        self._build_nav()
        self._build_body()
        self._load()

    # ------------------------------------------------------------------
    # Navigation header
    # ------------------------------------------------------------------

    def _build_nav(self):
        nav = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        nav.set_halign(Gtk.Align.CENTER)
        nav.set_margin_top(12)
        nav.set_margin_bottom(6)

        self._prev_btn = Gtk.Button(icon_name="go-previous-symbolic")
        self._prev_btn.add_css_class("flat")
        self._prev_btn.connect("clicked", lambda b: self._change_month(-1))

        self._prev_label = Gtk.Label()
        self._prev_label.add_css_class("cal-nav-label")

        month_col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        month_col.set_halign(Gtk.Align.CENTER)
        month_col.set_valign(Gtk.Align.CENTER)
        self._year_label = Gtk.Label()
        self._year_label.add_css_class("cal-year-label")
        self._month_label = Gtk.Label()
        self._month_label.add_css_class("cal-month-label")
        month_col.append(self._year_label)
        month_col.append(self._month_label)

        self._next_label = Gtk.Label()
        self._next_label.add_css_class("cal-nav-label")

        self._next_btn = Gtk.Button(icon_name="go-next-symbolic")
        self._next_btn.add_css_class("flat")
        self._next_btn.connect("clicked", lambda b: self._change_month(1))

        self._upcoming_btn = Gtk.Button(label="Upcoming")
        self._upcoming_btn.add_css_class("flat")
        self._upcoming_btn.add_css_class("cal-upcoming-btn")
        self._upcoming_btn.connect("clicked", self._on_upcoming_clicked)

        self._today_btn = Gtk.Button(label="Today")
        self._today_btn.add_css_class("flat")
        self._today_btn.add_css_class("cal-today-btn")
        self._today_btn.connect("clicked", self._on_today_clicked)
        self._today_btn.set_visible(False)

        nav.append(self._prev_btn)
        nav.append(self._prev_label)
        nav.append(month_col)
        nav.append(self._next_label)
        nav.append(self._next_btn)
        nav.append(self._today_btn)
        nav.append(self._upcoming_btn)

        self._build_popover()
        self.append(nav)
        self._update_nav_labels()

    def _build_popover(self):
        self._popover = Gtk.Popover()
        self._popover.set_position(Gtk.PositionType.BOTTOM)
        self._popover.add_css_class("cal-upcoming-popover")

        self._popover_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self._popover_box.set_margin_top(12)
        self._popover_box.set_margin_bottom(12)
        self._popover_box.set_margin_start(12)
        self._popover_box.set_margin_end(12)

        title = Gtk.Label()
        title.set_markup("<b>Upcoming Episodes</b>")
        title.set_halign(Gtk.Align.START)
        self._popover_box.append(title)

        self._popover_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self._popover_box.append(self._popover_list)

        self._popover.set_child(self._popover_box)
        self._popover.set_parent(self._upcoming_btn)

    def _on_upcoming_clicked(self, btn):
        self._popover.popup()

    def _on_today_clicked(self, _btn):
        today = datetime.date.today()
        if self._year == today.year and self._month == today.month:
            return
        self._year = today.year
        self._month = today.month
        self._update_nav_labels()
        self._render_month()

    # ------------------------------------------------------------------
    # Body: calendar grid
    # ------------------------------------------------------------------

    def _build_body(self):
        sw = Gtk.ScrolledWindow()
        sw.set_hexpand(True)
        sw.set_vexpand(True)
        sw.set_margin_start(24)
        sw.set_margin_end(24)
        sw.set_margin_top(12)
        sw.set_margin_bottom(24)

        self._grid = Gtk.Grid()
        self._grid.set_column_spacing(0)
        self._grid.set_row_spacing(0)
        self._grid.set_column_homogeneous(True)
        self._grid.set_row_homogeneous(False)
        sw.set_child(self._grid)
        self.append(sw)

    # ------------------------------------------------------------------
    # Data pipeline
    # ------------------------------------------------------------------

    def _load(self):
        self._fetch_gen += 1
        gen = self._fetch_gen
        threading.Thread(target=self._fetch_data, args=(gen,), daemon=True).start()

    def _fetch_data(self, gen):
        shows = self.user_repo.get_watchlist("show")
        if not shows:
            GLib.idle_add(self._render_empty, gen)
            return

        today = datetime.date.today()

        def _fetch_one(s):
            tmdb_id = s["tmdb_id"]
            airings = {}
            upcoming = []
            try:
                show = self.metadata_service.get_show(tmdb_id)
                episodes = self.metadata_service.get_latest_season_episodes(tmdb_id)
            except NetworkError:
                return tmdb_id, None, airings, upcoming

            for ep in episodes:
                if not ep.air_date:
                    continue
                try:
                    date = datetime.date.fromisoformat(ep.air_date)
                except (ValueError, TypeError):
                    continue

                key = ep.air_date
                if key not in airings:
                    airings[key] = []
                airings[key].append((show, ep))

                if date >= today:
                    upcoming.append((date, show, ep))

            if show.next_episode_air_date and show.next_episode_season is not None and show.next_episode_number is not None:
                try:
                    next_date = datetime.date.fromisoformat(show.next_episode_air_date)
                except (ValueError, TypeError):
                    next_date = None
                if next_date is not None and next_date >= today:
                    already_seen = any(
                        e.season_number == show.next_episode_season and e.episode_number == show.next_episode_number
                        for e in episodes
                    )
                    if not already_seen:
                        next_ep = Episode(
                            tmdb_id=0,
                            show_tmdb_id=tmdb_id,
                            season_number=show.next_episode_season,
                            episode_number=show.next_episode_number,
                            title=show.next_episode_name or "",
                            air_date=show.next_episode_air_date,
                            poster_url=show.next_episode_still,
                        )
                        key = show.next_episode_air_date
                        if key not in airings:
                            airings[key] = []
                        airings[key].append((show, next_ep))
                        upcoming.append((next_date, show, next_ep))
            return tmdb_id, show, airings, upcoming

        from ..threads import submit as _submit_worker

        futures = [_submit_worker(_fetch_one, s) for s in shows]
        airings = {}
        upcoming = []
        shows_map = {}
        for fut in futures:
            tmdb_id, show, sub_airings, sub_upcoming = fut.result()
            if show is None:
                continue
            shows_map[tmdb_id] = show
            for key, entries in sub_airings.items():
                airings.setdefault(key, []).extend(entries)
            upcoming.extend(sub_upcoming)

        upcoming.sort(key=lambda x: x[0])
        upcoming = upcoming[:15]

        GLib.idle_add(self._render, gen, airings, upcoming, shows_map)

    def _render_empty(self, gen):
        if gen != self._fetch_gen:
            return False
        self._airings = {}
        self._upcoming = []
        self._shows = {}
        self._render_month()
        return False

    def _render(self, gen, airings, upcoming, shows_map):
        if gen != self._fetch_gen:
            return False
        self._airings = airings
        self._upcoming = upcoming
        self._shows = shows_map
        self._render_month()
        return False

    # ------------------------------------------------------------------
    # Month rendering
    # ------------------------------------------------------------------

    def _render_month(self):
        while True:
            child = self._grid.get_first_child()
            if child is None:
                break
            self._grid.remove(child)

        while True:
            child = self._popover_list.get_first_child()
            if child is None:
                break
            self._popover_list.remove(child)

        for i, name in enumerate(WEEKDAYS):
            lbl = Gtk.Label(label=name)
            lbl.add_css_class("cal-weekday")
            lbl.set_halign(Gtk.Align.START)
            lbl.set_valign(Gtk.Align.CENTER)
            lbl.set_margin_start(12)
            lbl.set_margin_top(8)
            lbl.set_margin_bottom(8)
            self._grid.attach(lbl, i, 0, 1, 1)

        c = cal_mod.Calendar(cal_mod.MONDAY)
        weeks = c.monthdayscalendar(self._year, self._month)
        today = datetime.date.today()

        for row_idx, week in enumerate(weeks):
            for col_idx, day in enumerate(week):
                if day == 0:
                    continue
                date = datetime.date(self._year, self._month, day)
                date_str = date.isoformat()
                airing_list = self._airings.get(date_str, [])
                cell = self._build_day_cell(day, airing_list)
                if date == today:
                    cell.add_css_class("cal-today")
                self._grid.attach(cell, col_idx, row_idx + 1, 1, 1)

        count = len(self._upcoming)
        self._upcoming_btn.set_label(f"Upcoming ({count})" if count else "Upcoming")

        if self._upcoming:
            for date, show, ep in self._upcoming:
                card = self._build_upcoming_card(date, show, ep)
                self._popover_list.append(card)
        else:
            none_lbl = Gtk.Label()
            none_lbl.set_markup('<span alpha="55%">Nothing upcoming</span>')
            none_lbl.set_halign(Gtk.Align.START)
            none_lbl.set_margin_start(4)
            self._popover_list.append(none_lbl)

    def _build_day_cell(self, day, airing_list):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.add_css_class("cal-day")
        box.set_size_request(-1, 200)

        if airing_list:
            box.add_css_class("cal-day-has-shows")
            gesture = Gtk.GestureClick()
            gesture.connect("pressed", self._on_day_pressed, airing_list, day)
            box.add_controller(gesture)

        num = Gtk.Label(label=str(day))
        num.set_halign(Gtk.Align.START)
        num.set_valign(Gtk.Align.START)
        num.set_margin_start(8)
        num.set_margin_top(8)
        num.add_css_class("cal-day-num")
        box.append(num)

        if airing_list:
            content_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            content_box.set_margin_start(8)
            content_box.set_margin_end(8)
            content_box.set_margin_bottom(8)
            content_box.set_halign(Gtk.Align.CENTER)
            content_box.set_valign(Gtk.Align.END)
            content_box.set_vexpand(True)

            if len(airing_list) == 1:
                show, ep = airing_list[0]
                poster_btn = Gtk.Button()
                poster_btn.add_css_class("flat")
                poster_btn.add_css_class("cal-poster-btn")
                box_, picture = create_poster(84, 126, "cal-poster")
                poster_btn.set_child(box_)
                load_poster(show.poster_url, picture)
                poster_btn.connect("clicked", self._on_show_clicked, show)
                content_box.append(poster_btn)
            else:
                chip = Gtk.Label(label=f"{len(airing_list)}+")
                chip.add_css_class("cal-count-chip")
                content_box.append(chip)

            box.append(content_box)

        return box

    def _on_day_pressed(self, gesture, n_press, x, y, airing_list, day):
        popover = Gtk.Popover()
        popover.set_position(Gtk.PositionType.BOTTOM)
        popover.add_css_class("cal-day-popover")

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_margin_top(12)
        box.set_margin_bottom(12)
        box.set_margin_start(12)
        box.set_margin_end(12)

        date = datetime.date(self._year, self._month, day)
        header = Gtk.Label()
        header.set_markup(f"<b>{WEEKDAYS[date.weekday()]}, {MONTHS_SHORT[date.month-1]} {day}</b>")
        header.set_halign(Gtk.Align.START)
        box.append(header)

        for show, ep in airing_list:
            card = self._build_upcoming_card(date, show, ep)
            box.append(card)

        popover.set_child(box)
        popover.set_parent(gesture.get_widget())
        popover.popup()

    def _build_upcoming_card(self, date, show, ep):
        btn = Gtk.Button()
        btn.add_css_class("flat")
        btn.add_css_class("cal-upcoming")

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)

        date_str = f"{MONTHS_SHORT[date.month - 1]} {date.day}"
        date_lbl = Gtk.Label(label=date_str)
        date_lbl.set_halign(Gtk.Align.START)
        date_lbl.add_css_class("cal-upcoming-date")
        vbox.append(date_lbl)

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        title_lbl = Gtk.Label(label=show.title)
        title_lbl.set_halign(Gtk.Align.START)
        title_lbl.set_hexpand(True)
        title_lbl.set_ellipsize(Pango.EllipsizeMode.END)
        title_lbl.set_xalign(0)
        row.append(title_lbl)

        ep_str = f"S{ep.season_number}E{ep.episode_number:02d}"
        ep_lbl = Gtk.Label(label=ep_str)
        ep_lbl.add_css_class("cal-upcoming-ep")
        row.append(ep_lbl)

        vbox.append(row)
        btn.set_child(vbox)
        btn.connect("clicked", self._on_show_clicked, show)
        return btn

    def _on_show_clicked(self, btn, show):
        if self.main_page:
            self.main_page.show_detail("show", show)

    # ------------------------------------------------------------------
    # Month navigation
    # ------------------------------------------------------------------

    def _change_month(self, delta):
        new_month = self._month + delta
        new_year = self._year
        if new_month < 1:
            new_month = 12
            new_year -= 1
        elif new_month > 12:
            new_month = 1
            new_year += 1
        self._year = new_year
        self._month = new_month
        self._update_nav_labels()
        self._render_month()

    def _update_nav_labels(self):
        self._year_label.set_markup(
            f'<span size="small" alpha="60%">{self._year}</span>'
        )
        self._month_label.set_markup(
            f'<span size="x-large" weight="bold">{MONTHS_FULL[self._month - 1]}</span>'
        )

        prev_m = self._month - 1
        if prev_m < 1:
            prev_m = 12
        self._prev_label.set_markup(
            f'<span alpha="60%">{MONTHS_SHORT[prev_m - 1]}</span>'
        )

        next_m = self._month + 1
        if next_m > 12:
            next_m = 1
        self._next_label.set_markup(
            f'<span alpha="60%">{MONTHS_SHORT[next_m - 1]}</span>'
        )

        today = datetime.date.today()
        is_current = (self._year == today.year and self._month == today.month)
        if hasattr(self, "_today_btn") and self._today_btn is not None:
            self._today_btn.set_visible(not is_current)

    def _set_mode(self, mode):
        pass
