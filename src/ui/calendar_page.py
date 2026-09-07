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
from .media_card import card_poster_url
from .poster import create_poster, load_poster
from . import scroll_restore
from . import datefmt

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
        self._all_airings = {}
        self._upcoming = []
        self._all_upcoming = []
        self._shows = {}
        self._fetch_gen = 0
        self._filter = "all"

        from . import page_reveal
        from . import poster

        today = datetime.date.today()
        self._year = today.year
        self._month = today.month

        self._build_nav()
        self._build_body()
        self._reveal_page = page_reveal.arm_launch_reveal(
            self, settle_fn=poster.pending_loads)
        # Defer the first fetch so it never competes with the page switch.
        page_reveal.defer_initial_work(self._load)

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
        title.set_markup("<b>Upcoming</b>")
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
        sw.set_margin_top(12)
        self.scrolled = sw

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
        # Refreshes keep the viewport; the first load starts at the top.
        self._restore_y = (
            scroll_restore.capture(self.scrolled)
            if scroll_restore.had_content(self.scrolled) else 0.0
        )
        # Same-day cache: revisits render instantly, no fan-out.
        cached = getattr(self, "_day_cache", None)
        if cached is not None and cached["day"] == datetime.date.today():
            GLib.idle_add(self._render, gen, cached["airings"],
                          cached["upcoming"], cached["shows_map"])
            return
        threading.Thread(target=self._fetch_data, args=(gen,), daemon=True).start()

    def _fetch_data(self, gen):
        shows = self.user_repo.get_watchlist("show")
        movies = self.user_repo.get_watchlist_with_dates("movie")
        if not shows and not movies:
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

        for m in movies:
            rd = m.get("release_date")
            if not rd:
                continue
            try:
                date = datetime.date.fromisoformat(rd)
            except (ValueError, TypeError):
                continue
            if date < today:
                continue
            key = rd
            entry = (m, None)
            if key not in airings:
                airings[key] = []
            airings[key].append(entry)
            upcoming.append((date, m, None))

        upcoming.sort(key=lambda x: x[0])
        upcoming = upcoming[:15]

        self._day_cache = {
            "day": datetime.date.today(),
            "airings": airings,
            "upcoming": upcoming,
            "shows_map": shows_map,
        }
        GLib.idle_add(self._render, gen, airings, upcoming, shows_map)

    def _render_empty(self, gen):
        if gen != self._fetch_gen:
            return False
        self._all_airings = {}
        self._all_upcoming = {}
        self._airings = {}
        self._upcoming = []
        self._shows = {}
        # Friendly explanation instead of a bare, empty month grid.
        banner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        banner.set_halign(Gtk.Align.CENTER)
        banner.set_margin_top(18)
        icon = Gtk.Image(icon_name="alarm-symbolic")
        icon.set_pixel_size(40)
        icon.add_css_class("dimmed")
        banner.append(icon)
        lbl = Gtk.Label(
            label="Add TV shows or movies to your watchlist "
                  "and their airings will show up here.")
        lbl.add_css_class("dimmed")
        banner.append(lbl)
        # Between the top nav bar and the month grid.
        first = self.get_first_child()
        if first is not None:
            self.insert_child_after(banner, first)
        else:
            self.append(banner)
        self._render_month()
        self._reveal_page()
        return False

    def _render(self, gen, airings, upcoming, shows_map):
        if gen != self._fetch_gen:
            return False
        self._all_airings = airings
        self._all_upcoming = upcoming
        self._shows = shows_map
        self._apply_filter()
        self._render_month()
        self._reveal_page()
        if getattr(self, "_restore_y", 0.0) > 0.0:
            scroll_restore.restore(self.scrolled, self._restore_y)
            self._restore_y = 0.0
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
                media_type = "movie" if ep is None else "show"
                card = self._build_upcoming_card(date, show, ep, media_type)
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
            summary_parts = []
            for show, ep in airing_list[:4]:
                title = (show["title"] if isinstance(show, dict)
                         else show.title)
                if ep is not None:
                    summary_parts.append(
                        f"{title} · S{(ep.season_number or 0):02d}"
                        f"E{(ep.episode_number or 0):02d}")
                else:
                    summary_parts.append(title)
            summary = "\n".join(summary_parts)
            if len(airing_list) > 4:
                summary += "\n…"
            box.set_tooltip_text(summary)

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
            content_box.set_valign(Gtk.Align.CENTER)
            content_box.set_vexpand(True)

            if len(airing_list) == 1:
                show, ep = airing_list[0]
                poster_btn = Gtk.Button()
                poster_btn.add_css_class("flat")
                poster_btn.add_css_class("cal-poster-btn")
                box_, picture = create_poster(84, 126, "cal-poster")
                poster_btn.set_child(box_)
                poster_url = (show.get("poster_url")
                              if isinstance(show, dict)
                              else show.poster_url)
                load_poster(card_poster_url(poster_url), picture)
                media_type = ("movie" if ep is None
                              else "show")
                poster_btn.connect(
                    "clicked", self._on_item_clicked, show, media_type)
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
        amer = datefmt.is_american(getattr(self.win, "settings", None))
        header = Gtk.Label()
        header.set_markup(f"<b>{datefmt.format_weekday_date(date, amer)}</b>")
        header.set_halign(Gtk.Align.START)
        box.append(header)

        for show, ep in airing_list:
            media_type = "movie" if ep is None else "show"
            card = self._build_upcoming_card(date, show, ep, media_type)
            box.append(card)

        popover.set_child(box)
        popover.set_parent(gesture.get_widget())
        popover.popup()

    def _build_upcoming_card(self, date, show, ep, media_type="show"):
        btn = Gtk.Button()
        btn.add_css_class("flat")
        btn.add_css_class("cal-upcoming")

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)

        date_str = datefmt.format_day_short(
            date, datefmt.is_american(getattr(self.win, "settings", None)))
        date_lbl = Gtk.Label(label=date_str)
        date_lbl.set_halign(Gtk.Align.START)
        date_lbl.add_css_class("cal-upcoming-date")
        vbox.append(date_lbl)

        title = (show["title"] if isinstance(show, dict)
                 else show.title)
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        title_lbl = Gtk.Label(label=title)
        title_lbl.set_halign(Gtk.Align.START)
        title_lbl.set_hexpand(True)
        title_lbl.set_ellipsize(Pango.EllipsizeMode.END)
        title_lbl.set_xalign(0)
        row.append(title_lbl)

        if ep is not None:
            ep_str = f"S{ep.season_number}E{ep.episode_number:02d}"
            ep_lbl = Gtk.Label(label=ep_str)
            ep_lbl.add_css_class("cal-upcoming-ep")
            row.append(ep_lbl)
        else:
            type_lbl = Gtk.Label(label="Movie")
            type_lbl.add_css_class("cal-upcoming-ep")
            row.append(type_lbl)

        vbox.append(row)
        btn.set_child(vbox)
        btn.connect("clicked", self._on_item_clicked, show, media_type)
        return btn

    def _on_item_clicked(self, btn, show, media_type="show"):
        if self.main_page:
            self.main_page.show_detail(media_type, show)

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

    @property
    def _mode(self):
        return self._filter

    def _set_mode(self, mode):
        mapped = {"all": "all", "movies": "movie", "shows": "show"}.get(mode)
        if mapped is None or mapped == self._filter:
            return
        self._filter = mapped
        self._apply_filter()
        self._render_month()

    def _apply_filter(self):
        if self._filter == "all":
            self._airings = self._all_airings
            self._upcoming = self._all_upcoming
            return
        self._airings = {}
        for date_str, entries in self._all_airings.items():
            filtered = [
                (s, e) for s, e in entries
                if self._entry_type(s, e) == self._filter
            ]
            if filtered:
                self._airings[date_str] = filtered
        self._upcoming = [
            (d, s, e) for d, s, e in self._all_upcoming
            if self._entry_type(s, e) == self._filter
        ]

    @staticmethod
    def _entry_type(show, ep):
        if ep is None:
            return "movie"
        if isinstance(show, dict):
            return show.get("media_type", "show")
        return getattr(show, "media_type", "show")
