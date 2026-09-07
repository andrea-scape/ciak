import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, GLib, Adw

from ..domain.exceptions import NetworkError
from .media_card import make_media_card, config_grid, PAGE_GUTTER_PX
from . import scroll_restore
from .anim import CONTENT_MS, CONTENT_PX, rise_fade_in
from .shared_widgets import make_error_row


class CollectionPage(Adw.Bin):
    """Show every movie in a TMDB collection (saga/franchise) as a grid.

    Fetched in a worker thread, marshalled back to the UI thread via
    GLib.idle_add.  Only reached from the detail page's "Part of:" chip.
    """

    def __init__(self, win, user_repo, metadata_service, main_page=None,
                 collection_id=None, name=None):
        super().__init__()
        self.win = win
        self.user_repo = user_repo
        self.metadata_service = metadata_service
        self.main_page = main_page
        self.collection_id = collection_id
        self.fallback_name = name or "Collection"
        self.add_css_class("ciak-dashboard")

        self._render_gen = 0
        self._cancelled = False
        self._error_label = None

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_vexpand(True)
        self.scrolled = scrolled

        clamp = Adw.Clamp()
        clamp.set_maximum_size(1400)
        clamp.set_tightening_threshold(900)

        self.content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)
        self.content_box.set_margin_start(PAGE_GUTTER_PX)
        self.content_box.set_margin_end(PAGE_GUTTER_PX)
        self.content_box.set_margin_top(24)
        self.content_box.set_margin_bottom(36)

        self.title_label = Gtk.Label(label=self.fallback_name)
        self.title_label.add_css_class("title-1")
        self.title_label.set_xalign(0)
        self.title_label.set_wrap(True)
        self.content_box.append(self.title_label)

        self.overview_label = Gtk.Label()
        self.overview_label.add_css_class("body")
        self.overview_label.add_css_class("dimmed")
        self.overview_label.set_xalign(0)
        self.overview_label.set_wrap(True)
        self.overview_label.set_visible(False)
        self.content_box.append(self.overview_label)

        self.stats_group = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.stats_group.set_margin_top(12)
        self.stats_group.set_margin_bottom(24)
        self.stats_group.set_visible(False)
        self.content_box.append(self.stats_group)

        self.stats_label = Gtk.Label()
        self.stats_label.add_css_class("caption")
        self.stats_label.add_css_class("collection-stats")
        self.stats_label.set_xalign(0)
        self.stats_group.append(self.stats_label)

        self.progress = Gtk.ProgressBar()
        self.progress.set_halign(Gtk.Align.FILL)
        self.progress.add_css_class("collection-progress")
        self.progress.set_visible(False)
        self.stats_group.append(self.progress)

        self.grid = Gtk.FlowBox()
        config_grid(self.grid)
        self.grid.set_max_children_per_line(6)
        self.content_box.append(self.grid)

        # Add loading skeletons to grid
        self._skeletons = []
        for _ in range(6):
            placeholder = Gtk.Box()
            placeholder.set_size_request(200, 300)
            placeholder.add_css_class("skeleton-pulse")
            self.grid.append(placeholder)
            self._skeletons.append(placeholder)

        clamp.set_child(self.content_box)
        scrolled.set_child(clamp)
        self.set_child(scrolled)

        GLib.Thread.new("collection", self._fetch)

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def _fetch(self):
        token = self._render_gen
        try:
            collection = self.metadata_service.get_collection(self.collection_id)
        except NetworkError as e:
            GLib.idle_add(self._show_error, str(e))
            return
        GLib.idle_add(self._populate, token, collection)

    def _populate(self, token, collection):
        if token != self._render_gen or self._cancelled:
            return False
        for s in self._skeletons:
            self.grid.remove(s)
        self._skeletons.clear()
        # Refreshes (watched badges, stats) keep the viewport; first
        # render starts at the top.
        restore_y = (
            scroll_restore.capture(self.scrolled)
            if scroll_restore.had_content(self.scrolled) else 0.0
        )
        self._clear()

        if collection is None:
            self._show_error("Collection not found or unavailable.")
            return False

        if not collection.parts:
            empty = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            empty.set_halign(Gtk.Align.CENTER)
            empty.set_valign(Gtk.Align.CENTER)
            empty.set_vexpand(True)
            icon = Gtk.Image(icon_name="folder-videos-symbolic")
            icon.set_pixel_size(48)
            icon.add_css_class("dimmed")
            empty.append(icon)
            lbl = Gtk.Label(label="No titles in this collection yet")
            lbl.add_css_class("dimmed")
            empty.append(lbl)
            self.content_box.append(empty)
            return False

        watched = self.user_repo.get_watched_ids("movie")
        self.title_label.set_text(collection.name or self.fallback_name)
        if collection.overview:
            self.overview_label.set_text(collection.overview)
            self.overview_label.set_visible(True)

        total = len(collection.parts)
        watched_n = sum(1 for m in collection.parts if m.tmdb_id in watched)
        remaining = total - watched_n
        self.stats_label.set_text(
            f"{watched_n} of {total} watched · {remaining} remaining"
        )
        self.stats_label.set_visible(True)
        self.stats_group.set_visible(True)
        if total:
            self.progress.set_fraction(watched_n / total)
            self.progress.set_visible(True)

        next_up = next(
            (m for m in collection.parts if m.tmdb_id not in watched), None
        )
        cards = []
        for item in collection.parts:
            footer = None
            if item is next_up:
                footer = Gtk.Label(label="Up next")
                footer.add_css_class("next-up-tag")
                footer.add_css_class("caption")
            card = make_media_card(
                item, self.main_page,
                watched=item.tmdb_id in watched,
                footer=footer,
            )
            self.grid.append(card)
            cards.append(card)

        rise_fade_in(cards, CONTENT_MS, CONTENT_PX)
        if restore_y > 0.0:
            scroll_restore.restore(self.scrolled, restore_y)
        return False

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _show_error(self, msg):
        self._clear()
        self._error_label = make_error_row(
            f"Error: {msg}",
            on_retry=lambda: self._fetch(),
        )
        self.content_box.append(self._error_label)
        return False

    def _clear(self):
        child = self.grid.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            self.grid.remove(child)
            child = nxt
        if self._error_label is not None:
            parent = self._error_label.get_parent()
            if parent is not None:
                parent.remove(self._error_label)
            self._error_label = None

    def refresh(self):
        """Re-fetch and re-render (watched badges, stats, progress)."""
        self._render_gen += 1
        self._fetch()

    def cancel(self):
        self._cancelled = True