"""Diary: Letterboxd-style timeline of watch history grouped by local day.

Each day gets a header (date, title count, total time) and a horizontally
scrollable row of poster cards. Cards reuse the watchlist design and can
carry a per-session note edited through an Adw.Dialog.
"""

import datetime
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from types import SimpleNamespace

from gi.repository import Gdk, GLib, Gtk, Adw, Pango

from .media_card import make_media_card, card_poster_url, PAGE_GUTTER_PX
from .poster import FixedPaintable, load_poster
from . import page_reveal
from . import poster as poster_mod
from . import scroll_restore
from . import datefmt


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def stars_for_rating(rating):
    """Ratings are stored locally on a 1-5 scale; round half up and clamp."""
    if rating is None:
        return None
    return max(1, min(5, int(rating + 0.5)))


def star_glyphs(n):
    filled, empty = "\u2605", "\u2606"
    return filled * n + empty * (5 - n)


def format_minutes(total):
    total = int(total or 0)
    hours, minutes = divmod(total, 60)
    if hours == 0:
        return f"{minutes}m"
    if minutes == 0:
        return f"{hours}h"
    return f"{hours}h {minutes:02d}m"


_WEEKDAYS = [
    "Monday", "Tuesday", "Wednesday", "Thursday",
    "Friday", "Saturday", "Sunday",
]
_MONTHS = [
    "January", "February", "March", "April", "May", "June", "July",
    "August", "September", "October", "November", "December",
]


def format_day_header(day_iso, ref=None, american=False):
    """Locale-independent date header: Today/Yesterday, weekday-only within
    the past week, full date beyond. `ref` overrides today (tests)."""
    d = datetime.date.fromisoformat(day_iso)
    today = ref or datetime.date.today()
    if d == today:
        return "Today"
    if d == today - datetime.timedelta(days=1):
        return "Yesterday"
    age = (today - d).days
    if 0 < age <= 6:
        return _WEEKDAYS[d.weekday()]
    return datefmt.format_date_long(d, american)


def build_month_summaries(days):
    """Map each month key to (label, titles, minutes).

    `days` is the desc-ordered get_diary_days() output; a block runs until
    the month of the next older day changes.  Keys are month strings like
    ``"2026-03"`` so that any day in the month can look up its summary.
    """
    out: dict[str, tuple] = {}
    if not days:
        return out
    n = len(days)
    i = 0
    while i < n:
        month = days[i]["day"][:7]
        j = i
        while j < n and days[j]["day"][:7] == month:
            j += 1
        block = days[i:j]
        label = f"{_MONTHS[int(month[5:]) - 1]} {month[:4]}"
        out[month] = (
            label,
            sum(b["item_count"] for b in block),
            sum(b["minutes"] for b in block),
        )
        i = j
    return out


def note_snippet(text, maxlen=40):
    if not text:
        return None
    text = " ".join(text.split())
    if len(text) <= maxlen:
        return text
    return text[:maxlen].rstrip() + "\u2026"


def _month_label(month_key):
    """'2026-03' -> 'March 2026' (pure, tested)."""
    try:
        return f"{_MONTHS[int(month_key[5:]) - 1]} {month_key[:4]}"
    except (ValueError, IndexError, TypeError):
        return month_key or ""


# ---------------------------------------------------------------------------
# Widgets
# ---------------------------------------------------------------------------

class NoteDialog:
    """Floating Adw.Dialog editing the note of one diary session.

    Mirrors the rating dialog: no title bar or system buttons — Cancel,
    Save, Escape or clicking away all dismiss it (clicking away discards
    the draft, same as the rating dialog).
    """

    def __init__(self, entry, on_saved):
        self._entry = entry
        self._on_saved = on_saved
        dialog = Adw.Dialog(title="Session note")
        dialog.set_content_width(380)
        dialog.set_presentation_mode(Adw.DialogPresentationMode.FLOATING)
        self._dialog = dialog

        self._buffer = Gtk.TextBuffer()
        if entry.notes:
            self._buffer.set_text(entry.notes)
        view = Gtk.TextView(
            buffer=self._buffer,
            wrap_mode=Gtk.WrapMode.WORD_CHAR,
            top_margin=8, bottom_margin=8,
            left_margin=8, right_margin=8,
        )
        view.set_size_request(-1, 140)
        frame = Gtk.Frame(child=view)

        hint = Gtk.Label(
            label="A private note about this watch session.",
            halign=Gtk.Align.START,
        )
        hint.add_css_class("dimmed")
        hint.add_css_class("caption")

        cancel = Gtk.Button(label="Cancel")
        cancel.add_css_class("flat")
        cancel.set_halign(Gtk.Align.START)
        cancel.connect("clicked", lambda *_: self._dialog.close())
        save = Gtk.Button(label="Save")
        save.add_css_class("suggested-action")
        save.set_halign(Gtk.Align.END)
        save.set_hexpand(True)
        save.connect("clicked", self._on_save)

        actions = Gtk.Box(spacing=8)
        actions.append(cancel)
        actions.append(save)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_margin_top(18)
        box.set_margin_bottom(16)
        box.set_margin_start(16)
        box.set_margin_end(16)
        box.append(hint)
        box.append(frame)
        box.append(actions)
        dialog.set_child(box)

    def present(self, parent):
        self._dialog.present(parent)
        self._attach_click_away()

    def _attach_click_away(self):
        """Close when the user clicks outside the floating card (same
        mechanics as the rating dialog)."""
        gesture = Gtk.GestureClick()
        gesture.set_propagation_phase(Gtk.PropagationPhase.BUBBLE)
        gesture.connect("pressed", self._on_dialog_pressed)
        self._dialog.add_controller(gesture)
        self._gesture = gesture
        self._dialog.connect(
            "closed", self._on_closed_cleanup, gesture)

    def _on_dialog_pressed(self, gesture, _n_press, x, y):
        dialog = self._dialog
        child = dialog.get_child()
        if child is None:
            dialog.close()
            return
        try:
            picked = dialog.pick(x, y, Gtk.PickFlags.DEFAULT)
        except GLib.Error:
            return
        if picked is None:
            dialog.close()
            return
        inside = picked is child or picked.is_ancestor(child)
        if not inside:
            dialog.close()

    def _on_closed_cleanup(self, _dialog, gesture):
        self._dialog.remove_controller(gesture)

    def _on_save(self, *_):
        text = self._buffer.get_text(
            self._buffer.get_start_iter(),
            self._buffer.get_end_iter(),
            False,
        ).strip() or None
        self._entry.notes = text
        self._on_saved(self._entry)
        self._dialog.close()


class EditDateDialog:
    """Adw.Dialog picking a new day for one watch session."""

    def __init__(self, entry, on_moved):
        self._entry = entry
        self._on_moved = on_moved
        self._dialog = Adw.Dialog(title="Edit watch date")

        header = Adw.HeaderBar()
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda *_: self._dialog.close())
        header.pack_start(cancel)
        save = Gtk.Button(label="Move")
        save.add_css_class("suggested-action")
        save.add_css_class("flat")
        save.connect("clicked", self._on_move)
        header.pack_end(save)

        current = datetime.date.fromtimestamp(entry.watched_at)
        self._calendar = Gtk.Calendar()
        self._calendar.select_month(current.month - 1, current.year)
        self._calendar.select_day(current.day)

        title = Gtk.Label(
            label=f"{entry.title} \u00b7 {current.isoformat()}",
        )
        title.add_css_class("dimmed")
        title.add_css_class("caption")
        title.set_margin_top(6)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.append(header)
        box.append(title)
        box.append(self._calendar)
        box.set_margin_bottom(12)
        box.set_margin_start(12)
        box.set_margin_end(12)

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(box)
        toolbar.set_content(Gtk.Box())
        self._dialog.set_child(toolbar)

    def present(self, parent):
        self._dialog.present(parent)

    def _on_move(self, *_):
        d = self._calendar.get_date()
        self._on_moved(
            self._entry,
            datetime.date(d.get_year(), d.get_month(), d.get_day_of_month()),
        )
        self._dialog.close()


class MonthDivider(Gtk.Box):
    """Slim separator row between month blocks with monthly totals."""

    def __init__(self, label, titles, minutes, month_key=None):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.add_css_class("diary-month-divider")
        self.month_key = month_key

        name = Gtk.Label(label=label)
        name.add_css_class("diary-month-label")
        name.set_halign(Gtk.Align.START)
        name.set_hexpand(True)

        meta = Gtk.Label(
            label=f"{titles} {'title' if titles == 1 else 'titles'}"
            f" \u00b7 {format_minutes(minutes)}",
        )
        meta.add_css_class("dimmed")
        meta.add_css_class("caption")
        meta.set_halign(Gtk.Align.END)

        self.append(name)
        self.append(meta)


class DayGroup(Gtk.Box):
    """One date section: header row plus entries in poster or list mode."""

    def __init__(self, main_page, day_info, entries, repo, mode="posters",
                 american=False):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.add_css_class("diary-day")
        self._main_page = main_page
        self._repo = repo
        self._mode = mode
        self._american = american
        self._day = day_info["day"]
        self._cards = {}

        title = Gtk.Label(
            label=format_day_header(day_info["day"], american=self._american))
        title.add_css_class("diary-date-header")
        title.set_halign(Gtk.Align.START)
        title.set_hexpand(True)

        count = day_info["item_count"]
        meta_text = f"{count} {'title' if count == 1 else 'titles'}"
        minutes = format_minutes(day_info["minutes"])
        meta = Gtk.Label(label=f"{meta_text} \u00b7 {minutes}")
        meta.add_css_class("dimmed")
        meta.add_css_class("caption")
        meta.set_halign(Gtk.Align.END)

        header = Gtk.Box(spacing=8)
        header.set_margin_bottom(6)
        header.append(title)
        header.append(meta)
        self.append(header)

        if mode == "list":
            body = Gtk.Box(
                orientation=Gtk.Orientation.VERTICAL, spacing=4,
            )
            body.add_css_class("diary-list-rows")
            for entry in entries:
                body.append(self._build_list_row(entry))
            self.append(body)
        else:
            body = Adw.WrapBox(
                child_spacing=20, line_spacing=14,
                natural_line_length=6 * 160 + 5 * 20,
            )
            # Never absorb surplus viewport height — otherwise a short
            # list stretches entries instead of leaving space below.
            body.set_valign(Gtk.Align.START)
            body.add_css_class("diary-row")
            body.set_margin_bottom(4)
            for entry in entries:
                body.append(self._build_poster_card(entry))
            self.append(body)

    # -- poster mode ---------------------------------------------------

    def _build_poster_card(self, entry):
        item = SimpleNamespace(**entry)
        stars_label = Gtk.Label()
        note_btn = self._make_note_button()

        footer = Gtk.Box(spacing=6, halign=Gtk.Align.CENTER)
        footer.add_css_class("diary-card-footer")
        footer.append(stars_label)
        footer.append(note_btn)

        snippet = Gtk.Label()
        snippet.add_css_class("diary-note-snippet")
        snippet.set_ellipsize(Pango.EllipsizeMode.END)
        snippet.set_width_chars(22)
        snippet.set_max_width_chars(22)
        snippet.set_justify(Gtk.Justification.CENTER)

        # The interactive strip sits below the card button, not inside
        # it: a nested button would leak its clicks to the card's own
        # activation and open the details page.
        wrap = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        wrap.add_css_class("diary-entry")
        wrap.set_valign(Gtk.Align.START)
        wrap.append(make_media_card(
            item, main_page=self._main_page, watched=True,
        ))
        wrap.append(snippet)
        wrap.append(footer)
        self._attach_session_menu(wrap, item)
        self._apply_entry_state(item, stars_label, note_btn, snippet)
        self._cards[id(item)] = (item, stars_label, note_btn, snippet)
        return wrap

    # -- list mode -----------------------------------------------------

    def _build_list_row(self, entry):
        item = SimpleNamespace(**entry)
        stars_label = Gtk.Label()
        note_btn = self._make_note_button()

        paintable = FixedPaintable(60, 90)
        thumb = Gtk.Picture()
        thumb.set_paintable(paintable)
        # load_poster's contract: it looks the paintable up on the
        # picture itself (same registration as make_media_card).
        thumb._fixed_paintable = paintable
        thumb.set_size_request(60, 90)
        thumb.set_content_fit(Gtk.ContentFit.COVER)
        thumb.add_css_class("diary-thumb")
        load_poster(card_poster_url(item.poster_url), thumb)

        info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        name = Gtk.Label(label=item.title, halign=Gtk.Align.START)
        name.add_css_class("heading")
        name.set_ellipsize(Pango.EllipsizeMode.END)
        sub = Gtk.Label(
            label=self._row_subtitle(item), halign=Gtk.Align.START,
        )
        sub.add_css_class("dimmed")
        sub.add_css_class("caption")
        sub.set_ellipsize(Pango.EllipsizeMode.END)
        info.append(name)
        info.append(sub)
        info.set_halign(Gtk.Align.START)
        info.set_hexpand(True)
        info.set_valign(Gtk.Align.CENTER)

        end = Gtk.Box(spacing=8)
        end.set_valign(Gtk.Align.CENTER)
        end.append(stars_label)

        body_btn = Gtk.Button()
        body_btn.add_css_class("flat")
        body_btn.add_css_class("diary-list-row")
        body = Gtk.Box(spacing=12, margin_start=8, margin_end=8,
                       margin_top=6, margin_bottom=6)
        body.append(thumb)
        body.append(info)
        body.append(end)
        body_btn.set_child(body)
        body_btn.connect("clicked", self._open_details, item)

        wrap = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        wrap.add_css_class("diary-entry")
        wrap.set_valign(Gtk.Align.START)
        body_btn.set_hexpand(True)
        wrap.append(body_btn)
        wrap.append(note_btn)
        note_btn.set_valign(Gtk.Align.CENTER)
        self._attach_session_menu(body_btn, item)
        self._apply_entry_state(item, stars_label, note_btn, None)
        self._cards[id(item)] = (item, stars_label, note_btn, None)
        return wrap

    def _row_subtitle(self, item):
        parts = []
        if getattr(item, "year", None):
            parts.append(str(item.year))
        if item.media_type != "movie" and item.season_number is not None:
            start = f"S{item.season_number:02d}E{item.episode_number:02d}"
            end_s = getattr(item, "end_season_number", None)
            end_e = getattr(item, "end_episode_number", None)
            if end_s is not None and (end_s, end_e) != (
                item.season_number, item.episode_number
            ):
                start += f" to S{end_s:02d}E{end_e:02d}"
            parts.append(start)
        return " \u00b7 ".join(parts)

    def _open_details(self, _btn, item):
        if self._main_page is not None:
            self._main_page.show_detail(item.media_type, item)

    # -- shared pieces ---------------------------------------------------

    def _make_note_button(self):
        note_btn = Gtk.Button()
        note_btn.add_css_class("flat")
        note_btn.add_css_class("diary-note-btn")
        note_btn.set_icon_name("document-edit-symbolic")
        note_btn.set_tooltip_text("Edit session note")
        note_btn.update_property(
            [Gtk.AccessibleProperty.LABEL], ["Edit session note"])
        note_btn.connect("clicked", self._on_note_clicked)
        return note_btn

    def _attach_session_menu(self, widget, item):
        ctrl = Gtk.GestureClick(button=3)
        ctrl.connect("pressed", self._on_secondary, item, widget)
        widget.add_controller(ctrl)
        lp = Gtk.GestureLongPress()
        lp.connect("pressed", self._on_secondary, item, widget)
        widget.add_controller(lp)
        # Keyboard parity: Shift+F10 opens the same session menu.
        sc = Gtk.ShortcutController()

        def _run(_widget=None, _param=None):
            self._open_menu(widget, item)
            return True

        sc.add_shortcut(Gtk.Shortcut(
            trigger=Gtk.KeyvalTrigger.new(
                Gdk.KEY_F10, Gdk.ModifierType.SHIFT_MASK),
            action=Gtk.CallbackAction.new(_run),
        ))
        widget.add_controller(sc)

    def _on_secondary(self, ctrl, *rest):
        item, widget = rest[-2], rest[-1]
        self._open_menu(widget, item)

    def _open_menu(self, widget, item):
        pop = Gtk.Popover()
        pop.set_parent(widget)
        box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=4,
            margin_start=6, margin_end=6, margin_top=6, margin_bottom=6,
        )
        edit = Gtk.Button(label="Edit watch date\u2026")
        edit.add_css_class("flat")
        edit.connect("clicked", lambda *_: (pop.popdown(),
                                            self._open_date_editor(item)))
        remove = Gtk.Button(label="Remove from history")
        remove.add_css_class("flat")
        remove.add_css_class("destructive-action")
        remove.connect("clicked", lambda *_: (pop.popdown(),
                                              self._confirm_remove(item)))
        box.append(edit)
        box.append(remove)
        pop.set_child(box)
        rect = Gdk.Rectangle()
        rect.x = widget.get_allocated_width() // 2
        rect.y = 0
        pop.set_pointing_to(rect)
        pop.popup()

    def _open_date_editor(self, item):
        def _moved(entry, new_date):
            old_day = datetime.date.fromtimestamp(
                entry.watched_at).isoformat()
            self._repo.reschedule_session(
                entry.tmdb_id,
                entry.media_type != "movie",
                old_day,
                new_date.isoformat(),
            )
            self._notify_changed()

        EditDateDialog(item, _moved).present(self._main_page or self)

    def _confirm_remove(self, item):
        def _on_answer(dialog, answer):
            if answer == "remove":
                self._repo.delete_session(
                    item.tmdb_id,
                    item.media_type != "movie",
                    datetime.date.fromtimestamp(
                        item.watched_at).isoformat(),
                )
                self._notify_changed()

        dialog = Adw.AlertDialog(
            heading="Remove from history?",
            body=f"\u201c{item.title}\u201d will no longer count as "
                 "watched on this day.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("remove", "Remove")
        dialog.set_response_appearance(
            "remove", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.connect("response", _on_answer)
        dialog.present(self._main_page or self)

    def _notify_changed(self):
        if self._main_page is None:
            return
        for page in ("history", "watchlist", "profile"):
            self._main_page.invalidate_page(page)
        self._main_page.invalidate_page("diary", reload_now=True)

    def _apply_entry_state(self, entry, stars_label, note_btn, snippet):
        stars = stars_for_rating(entry.rating)
        stars_label.set_text(star_glyphs(stars) if stars else "")
        stars_label.set_visible(bool(entry.rating))

        has_note = bool(entry.notes)
        if has_note:
            note_btn.add_css_class("has-note")
            note_btn.set_tooltip_text(f"Note: {entry.notes[:80]}")
        else:
            note_btn.remove_css_class("has-note")
            note_btn.set_tooltip_text("Add session note")

        if snippet is not None:
            text = note_snippet(entry.notes)
            snippet.set_text(text or "")
            snippet.set_visible(bool(text))

    def _on_note_clicked(self, btn):
        entry = self._entry_for_widget(btn)
        if entry is None:
            return
        card = self._cards.get(id(entry))
        if card is None:
            return
        _, stars_label, note_btn, snippet = card

        def _saved(updated):
            self._persist(updated)
            self._apply_entry_state(
                updated, stars_label, note_btn, snippet)

        NoteDialog(entry, _saved).present(self._main_page or self)

    def _entry_for_widget(self, widget):
        for entry, card in self._cards.items():
            if card[1] is widget or card[2] is widget:
                return card[0]
        return None

    def _persist(self, entry):
        if not hasattr(self._repo, "set_session_notes"):
            return
        self._repo.set_session_notes(
            entry.tmdb_id,
            entry.media_type != "movie",
            datetime.date.fromtimestamp(entry.watched_at).isoformat(),
            entry.notes,
        )


class DiaryPage(Adw.Bin):
    """Timeline of watched items grouped by local day, newest first."""

    PAGE_SIZE = 30

    def __init__(self, win, user_repo, metadata_service, main_page=None):
        super().__init__()
        self._win = win
        self._repo = user_repo
        self._main_page = main_page
        self._days = []
        self._built = 0
        self._appending = False
        self._load_token = 0
        self._filter = "all"
        self._query = ""
        # Scroll-preservation / navigation bookkeeping.
        self._dividers = []          # MonthDivider widgets, in order
        self._groups = []            # (day_iso, DayGroup) pairs, in order
        self._anchor_day = None      # day to re-anchor after a rebuild
        self._restore_y = 0.0
        self._user_nav = False       # filter/mode/search change: start at top
        self._active_month = None
        self._scroll_anim = 0
        self._tail_row = None

        self._list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        self._list.add_css_class("diary-list")
        self.add_css_class("diary-page")

        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        # Natural height only: without this the scrolled viewport stretches
        # the column when content is short, cascading surplus into the list
        # as random large gaps between day groups.
        column.set_valign(Gtk.Align.START)
        column.set_margin_top(24)
        column.set_margin_bottom(36)
        column.set_margin_start(PAGE_GUTTER_PX)
        column.set_margin_end(PAGE_GUTTER_PX)
        column.append(self._build_controls_row())
        column.append(self._list)

        self._scroll = Gtk.ScrolledWindow()
        self._scroll.set_policy(
            Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self._scroll.set_overlay_scrolling(False)
        clamp = Adw.Clamp(maximum_size=1400, tightening_threshold=900)
        clamp.set_child(column)
        self._scroll.set_child(clamp)
        self._scroll.set_vexpand(True)

        # Floating layers over the timeline: pinned current-month chip
        # (top) and the back-to-today pill (bottom).
        overlay = Gtk.Overlay()
        overlay.set_child(self._scroll)

        self._month_chip = Gtk.Label(label="")
        self._month_chip.add_css_class("diary-month-chip")
        chip_rev = Gtk.Revealer(
            transition_type=Gtk.RevealerTransitionType.CROSSFADE,
            transition_duration=180,
            valign=Gtk.Align.START, halign=Gtk.Align.CENTER,
            margin_top=6,
        )
        chip_rev.set_child(self._month_chip)
        self._chip_revealer = chip_rev
        overlay.add_overlay(chip_rev)

        pill_btn = Gtk.Button()
        pill_btn.set_icon_name("go-up-symbolic")
        pill_btn.set_tooltip_text("Back to today")
        pill_btn.update_property(
            [Gtk.AccessibleProperty.LABEL], ["Back to today"])
        pill_btn.add_css_class("diary-back-pill")
        pill_btn.set_valign(Gtk.Align.END)
        pill_btn.set_halign(Gtk.Align.CENTER)
        pill_btn.set_margin_bottom(16)
        pill_btn.connect("clicked", self._on_back_today)
        self._pill_revealer = Gtk.Revealer(
            transition_type=Gtk.RevealerTransitionType.CROSSFADE,
            transition_duration=180,
            valign=Gtk.Align.END, halign=Gtk.Align.CENTER,
            margin_bottom=16,
        )
        self._pill_revealer.set_child(pill_btn)
        overlay.add_overlay(self._pill_revealer)

        self._empty = Adw.StatusPage(
            icon_name="document-open-recent-symbolic",
            title="No diary entries yet",
            description="Watch something and it will show up here.",
        )

        self._overlay = overlay

        # Unified launch reveal, same as every other page.
        base_reveal = page_reveal.arm_launch_reveal(
            column, settle_fn=poster_mod.pending_loads)
        self._revealed = False

        def _reveal_page():
            self._revealed = True
            base_reveal()

        self._reveal_page = _reveal_page

        adj = self._scroll.get_vadjustment()
        adj.connect("notify::value", self._on_scroll)
        adj.connect("changed", self._on_scroll)
        self.set_child(self._empty)

        # Fresh pages are never in main_page's stale set, so nothing else
        # would ever trigger the first render — same as the grid pages.
        page_reveal.defer_initial_work(self._initial_load)

    # -- controls row ------------------------------------------------------

    def _build_controls_row(self):
        row = Gtk.Box(spacing=12)

        mode = self._settings_get("diary-view-mode", "posters")
        self._mode_toggle = Adw.ToggleGroup()
        self._mode_toggle.add(Adw.Toggle(
            name="posters", icon_name="view-grid-symbolic"))
        self._mode_toggle.add(Adw.Toggle(
            name="list", icon_name="view-list-symbolic"))
        self._mode_toggle.set_active_name(
            mode if mode in ("posters", "list") else "posters")
        self._mode_toggle.connect(
            "notify::active-name", self._on_mode_changed)

        self._search = Gtk.SearchEntry()
        self._search.set_placeholder_text("Filter titles\u2026")
        self._search.set_hexpand(True)
        self._search.connect("search-changed", self._on_search_changed)

        # Jump-to-month: label follows the month currently in view.
        jump_box = Gtk.Box(spacing=6)
        self._jump_label = Gtk.Label(label="Jump")
        jump_arrow = Gtk.Image(icon_name="pan-down-symbolic")
        jump_box.append(self._jump_label)
        jump_box.append(jump_arrow)
        jump = Gtk.MenuButton()
        jump.set_child(jump_box)
        jump.add_css_class("flat")
        jump.set_tooltip_text("Jump to month")
        jump.update_property([Gtk.AccessibleProperty.LABEL], ["Jump to month"])
        jump_pop = Gtk.Popover()
        jump_pop.connect("notify::visible", self._on_jump_popover_visible)
        jump.set_popover(jump_pop)
        jump.connect("notify::active", self._on_jump_toggled)
        self._jump_btn = jump

        row.append(self._search)
        row.append(self._mode_toggle)
        row.append(jump)
        return row

    def _settings_get(self, key, default):
        if self._win is not None and hasattr(self._win, "settings"):
            return self._win.settings.get_string(key)
        return default

    def _settings_set(self, key, value):
        if self._win is not None and hasattr(self._win, "settings"):
            self._win.settings.set_string(key, value)

    def _on_mode_changed(self, toggle, *_):
        self._settings_set("diary-view-mode", toggle.get_active_name())
        self._user_nav = True
        self._load()

    @property
    def _mode(self):
        """Headerbar filter protocol: main_page reads this to sync the
        shared All/Movies/Shows toggle when the page is shown. Global
        modes are plural; diary's internal filter (and media_type) are
        singular, so translate both ways."""
        return {"all": "all", "movie": "movies",
                "show": "shows"}[self._filter]

    def _set_mode(self, mode):
        mapped = {"all": "all", "movies": "movie", "shows": "show"}.get(mode)
        if mapped is None or mapped == self._filter:
            return
        self._filter = mapped
        self._user_nav = True
        self._load()

    def _on_search_changed(self, entry):
        self._query = entry.get_text().strip().casefold()
        self._user_nav = True
        self._load()

    def _visible_mode(self):
        name = self._mode_toggle.get_active_name()
        return name if name in ("posters", "list") else "posters"

    # -- loading ---------------------------------------------------------

    def _initial_load(self):
        GLib.timeout_add(70, self._load)

    def _load(self, *_):
        """Full rebuild; also the refresh entry point used by main_page."""
        self._load_token += 1
        token = self._load_token
        # Data-driven refreshes re-anchor to the day the user was looking
        # at; explicit view changes (filters/mode/search) start at the top.
        if self._list.get_first_child() is not None and not self._user_nav:
            self._restore_y = self._scroll.get_vadjustment().get_value()
            self._anchor_day = self._top_visible_day()
        else:
            self._restore_y = 0.0
            self._anchor_day = None
        self._user_nav = False
        while (child := self._list.get_first_child()) is not None:
            self._list.remove(child)
        self._dividers.clear()
        self._groups.clear()
        self._remove_tail()
        self._active_month = None
        self._last_rendered_month = None
        self._days = self._repo.get_diary_days(
            media_type=self._filter
            if self._filter in ("movie", "show") else None)
        self._built = 0
        if not self._days:
            self._empty.set_title("No diary entries yet")
            self._empty.set_description(
                "Watch something and it will show up here.")
            self.set_child(self._empty)
            return
        self.set_child(self._overlay)
        self._summaries = build_month_summaries(self._days)
        GLib.idle_add(lambda: token == self._load_token and (
            self._append_batch() or False
        ))

    def _entry_matches(self, entry):
        if self._filter != "all" and entry["media_type"] != self._filter:
            return False
        if self._query and self._query not in (
            entry.get("title") or ""
        ).casefold():
            return False
        return True

    def _append_batch(self):
        batch = self._days[self._built:self._built + self.PAGE_SIZE]
        mode = self._visible_mode()
        self._remove_tail()
        for day_info in batch:
            entries = [
                e for e in self._repo.get_diary_entries(day_info["day"])
                if self._entry_matches(e)
            ]
            if not entries:
                continue
            month = day_info["day"][:7]
            if month != self._last_rendered_month:
                summary = self._summaries.get(month)
                if summary:
                    div = MonthDivider(
                        *summary, month_key=month)
                    self._dividers.append(div)
                    self._list.append(div)
                    self._last_rendered_month = month
            group = DayGroup(
                self._main_page, day_info, entries, self._repo, mode=mode,
                american=datefmt.is_american(
                    getattr(self._win, "settings", None)),
            )
            self._groups.append((day_info["day"], group))
            self._list.append(group)
        self._built += len(batch)
        self._appending = False
        if self._built < len(self._days):
            adj = self._scroll.get_vadjustment()
            if adj.get_upper() <= adj.get_page_size() * 3.0:
                # Viewport not filled yet — chain the next batch directly.
                # Relying on the adjustment's changed signal breaks when a
                # filtered batch adds no widgets (bounds never change) and
                # the tail spinner would sit there forever.
                GLib.idle_add(self._append_batch)
            else:
                self._append_tail()
        if not self._list.get_first_child():
            self._list.append(self._build_no_matches())
        else:
            self._settle_after_batch()
        return False

    # -- incremental-build plumbing ---------------------------------------

    def _settle_after_batch(self):
        """After a batch lands: re-anchor to the preserved day, fall back
        to the plain captured offset, then run the one-shot reveal."""
        if getattr(self, "_revealed", True) is False:
            pass
        self._reveal_page()
        anchor = self._anchor_day
        if anchor is not None:
            for day, group in self._groups:
                if day == anchor:
                    y = self._widget_top(group)
                    if y is not None:
                        adj = self._scroll.get_vadjustment()
                        adj.set_value(max(y - 6.0, 0.0))
                    self._anchor_day = None
                    self._restore_y = 0.0
                    return
            if self._built >= len(self._days):
                # Anchor never matched anything built — give up cleanly.
                self._anchor_day = None
            return
        if self._restore_y > 0.0:
            scroll_restore.restore(self._scroll, self._restore_y)
            self._restore_y = 0.0

    def _widget_top(self, widget):
        """Y offset of widget within the scrollable list, or None when the
        allocation isn't valid yet."""
        try:
            alloc = widget.get_allocation()
            if alloc.width <= 1 and alloc.height <= 1:
                return None
            xy = widget.translate_coordinates(self._list, 0, 0)
            return float(xy[1]) if xy is not None else None
        except Exception:
            return None

    def _top_visible_day(self):
        """Day whose group straddles the viewport top (anchor target)."""
        value = self._scroll.get_vadjustment().get_value()
        best = None
        for day, group in self._groups:
            top = self._widget_top(group)
            if top is None or top + group.get_allocated_height() > value:
                best = day
                if top is not None and top <= value:
                    break
        return best

    def _update_tail(self):
        if self._tail_row is not None:
            self._remove_tail()

    def _append_tail(self):
        row = Gtk.Box(halign=Gtk.Align.CENTER, margin_top=10)
        row.add_css_class("diary-tail")
        spin = Adw.Spinner()
        spin.set_size_request(20, 20)
        row.append(spin)
        self._tail_row = row
        self._list.append(row)

    def _remove_tail(self):
        row = self._tail_row
        if row is not None and row.get_parent() is not None:
            row.get_parent().remove(row)
        self._tail_row = None

    def _build_no_matches(self):
        """Centered explanation plus an escape hatch from over-tight
        filters."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_halign(Gtk.Align.CENTER)
        box.set_valign(Gtk.Align.CENTER)
        box.set_margin_top(48)
        icon = Gtk.Image(icon_name="funnel-symbolic")
        icon.set_pixel_size(36)
        icon.add_css_class("dimmed")
        lbl = Gtk.Label(label="No matches for this filter.")
        lbl.add_css_class("dimmed")
        clear = Gtk.Button(label="Clear filters")
        clear.add_css_class("flat")
        clear.connect("clicked", self._on_clear_filters)
        box.append(icon)
        box.append(lbl)
        box.append(clear)
        return box

    def _on_clear_filters(self, *_):
        self._search.set_text("")
        self._query = ""
        self._user_nav = True
        if self._main_page is not None and hasattr(
                self._main_page, "set_global_mode"):
            self._main_page.set_global_mode("all")
        elif self._filter != "all":
            self._filter = "all"
            self._load()

    # -- floating chrome ---------------------------------------------------

    def _on_back_today(self, *_):
        self._animate_scroll_to(0.0)

    def _animate_scroll_to(self, target):
        """Short ease-out scroll so jumps feel physical, not teleporty."""
        if self._scroll_anim:
            GLib.source_remove(self._scroll_anim)
            self._scroll_anim = 0
        adj = self._scroll.get_vadjustment()
        start = adj.get_value()
        distance = start - target
        if abs(distance) < 2.0:
            adj.set_value(target)
            return
        duration_ms = 280.0
        deadline = GLib.get_monotonic_time() / 1000.0 + duration_ms

        def _step():
            now = GLib.get_monotonic_time() / 1000.0
            # Elapsed fraction: 0 at the first tick, 1 at the deadline.
            # (deadline - now) alone would count DOWN and run the tween
            # in reverse, ending back where the scroll started.
            t = max(0.0, min(1.0, 1.0 - (deadline - now) / duration_ms))
            eased = 1.0 - (1.0 - t) ** 3
            adj.set_value(start - distance * eased)
            if t >= 1.0:
                self._scroll_anim = 0
                return False
            return True

        self._scroll_anim = GLib.timeout_add(16, _step)

    def _active_month_for_value(self, value):
        """Month whose divider sits at/above the viewport top, or None."""
        active = None
        for div in self._dividers:
            top = self._widget_top(div)
            if top is None:
                continue
            if top <= value + 1.0:
                active = (div.month_key, top)
        return active

    # -- jump-to-month -----------------------------------------------------

    def _on_jump_toggled(self, btn, _pspec):
        if btn.get_active():
            self._fill_jump_popover()

    def _on_jump_popover_visible(self, pop, _pspec):
        if pop.get_visible():
            self._fill_jump_popover()

    def _months_available(self):
        seen = []
        for info in self._days:
            key = info["day"][:7]
            if key not in seen:
                seen.append(key)
        return seen

    def _fill_jump_popover(self):
        pop = self._jump_btn.get_popover()
        pop.set_child(None)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2,
                      margin_start=6, margin_end=6,
                      margin_top=6, margin_bottom=6)
        months = self._months_available()
        if not months:
            lbl = Gtk.Label(label="No history yet")
            lbl.add_css_class("dimmed")
            box.append(lbl)
        current = self._jump_label.get_text()
        for key in months:
            label = _month_label(key)
            count = sum(
                1 for i in self._days if i["day"][:7] == key)
            row = Gtk.Button(halign=Gtk.Align.FILL)
            row.add_css_class("flat")
            row_box = Gtk.Box(spacing=8)
            name = Gtk.Label(label=label, halign=Gtk.Align.START)
            name.set_hexpand(True)
            meta = Gtk.Label(label=str(count))
            meta.add_css_class("dimmed")
            meta.add_css_class("caption")
            row_box.append(name)
            row_box.append(meta)
            row.set_child(row_box)
            if label == current:
                # Not suggested-action: .flat overrides its background in
                # libadwaita's stylesheet, leaving white-on-white text.
                row.add_css_class("diary-jump-current")
            row.connect("clicked", lambda _b, k=key: (
                self._jump_btn.popdown(), self._jump_to(k)))
            box.append(row)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_max_content_height(320)
        scroll.set_propagate_natural_height(True)
        scroll.set_child(box)
        pop.set_child(scroll)

    def _jump_to(self, month_key):
        token = self._load_token
        target_idx = next(
            (i for i, info in enumerate(self._days)
             if info["day"][:7] == month_key),
            None)
        if target_idx is None:
            return
        # Fast-forward the incremental build until the month exists.
        guard = 0
        while guard < 400:
            guard += 1
            if any(d.month_key == month_key for d in self._dividers):
                break
            if token != self._load_token:
                return
            self._appending = False
            self._append_batch()
        div = next(
            (d for d in self._dividers if d.month_key == month_key), None)
        if div is None:
            return
        y = self._widget_top(div)

        def _apply():
            yy = self._widget_top(div) if y is None else y
            if yy is not None:
                self._scroll.get_vadjustment().set_value(max(yy - 6.0, 0.0))
            elif token == self._load_token:
                GLib.timeout_add(50, lambda: (
                    self._retry_jump(div, token) or False))
            return False

        GLib.idle_add(_apply)

    def _retry_jump(self, div, token):
        if token != self._load_token:
            return False
        y = self._widget_top(div)
        if y is not None:
            self._scroll.get_vadjustment().set_value(max(y - 6.0, 0.0))
        return False

    def _on_scroll(self, *_):
        if self._built >= len(self._days) or self._appending:
            self._update_chrome()
            return
        adj = self._scroll.get_vadjustment()
        upper = max(adj.get_upper(), 1.0)
        page = max(adj.get_page_size(), 1.0)
        if adj.get_value() >= upper - page * 2.0:
            self._appending = True
            GLib.idle_add(self._append_batch)
        self._update_chrome()

    def _update_chrome(self):
        """Pill visibility + pinned month chip + jump-button label."""
        adj = self._scroll.get_vadjustment()
        page = max(adj.get_page_size(), 1.0)
        deep = adj.get_value() > page * 1.5
        if self._pill_revealer.get_reveal_child() != deep:
            self._pill_revealer.set_reveal_child(deep)

        active = self._active_month_for_value(adj.get_value())
        first_div = self._dividers[0] if self._dividers else None
        first_top = self._widget_top(first_div) if first_div else None
        show_chip = (
            active is not None
            and first_top is not None
            and adj.get_value() > first_top + 4.0
        )
        if show_chip:
            key = active[0]
            if key != self._active_month:
                self._active_month = key
                self._month_chip.set_text(_month_label(key))
        if self._chip_revealer.get_reveal_child() != show_chip:
            self._chip_revealer.set_reveal_child(show_chip)

        label = _month_label(active[0]) if active else (
            _month_label(self._days[0]["day"][:7])
            if self._days else "Jump")
        if label != self._jump_label.get_text():
            self._jump_label.set_text(label)
