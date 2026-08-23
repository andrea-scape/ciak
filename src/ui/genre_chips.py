"""Reusable genre filter chip row for poster grid pages."""

import json

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw


TMDB_GENRE_NAMES = {
    28: "Action", 12: "Adventure", 16: "Animation", 35: "Comedy",
    80: "Crime", 99: "Documentary", 18: "Drama", 10751: "Family",
    14: "Fantasy", 36: "History", 27: "Horror", 10402: "Music",
    9648: "Mystery", 10749: "Romance", 878: "Science Fiction",
    10770: "TV Movie", 53: "Thriller", 10752: "War", 37: "Western",
    10759: "Action & Adventure", 10762: "Kids", 10763: "News",
    10764: "Reality", 10765: "Sci-Fi & Fantasy", 10766: "Soap",
    10767: "Talk", 10768: "War & Politics",
}


def item_genre_names(item):
    """Genre names for an item: cached pages carry a JSON string of names,
    live search results carry numeric TMDB genre_ids."""
    raw = getattr(item, "genres", None)
    if raw:
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except ValueError:
                return []
        return [g for g in raw if isinstance(g, str)]
    ids = getattr(item, "genre_ids", None) or []
    return [TMDB_GENRE_NAMES[i] for i in ids if i in TMDB_GENRE_NAMES]


def matches_all(item, selected):
    """AND semantics: every selected genre must be present."""
    if not selected:
        return True
    return set(selected) <= set(item_genre_names(item))


class GenreChipsRow(Adw.WrapBox):
    """Wrapping row of pill toggles; on_changed fires after any toggle."""

    def __init__(self, on_changed=None):
        super().__init__(child_spacing=6, line_spacing=6)
        self._on_changed = on_changed
        self._selected = set()
        self._pool = None
        self.set_visible(False)

    def show_placeholder(self, count=6):
        """Visible pulsing pills while the real genre pool loads. The next
        real set_genres() call replaces them."""
        child = self.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            self.remove(child)
            child = nxt
        for _ in range(count):
            pill = Gtk.Label(label="")
            pill.set_size_request(64, 26)
            pill.add_css_class("chip-toggle")
            pill.add_css_class("skeleton-pulse")
            self.append(pill)
        self._pool = None
        self.set_visible(True)

    def set_genres(self, names):
        pool = sorted(set(names))
        if pool == self._pool:
            return
        self._pool = pool
        self._selected &= set(pool)
        child = self.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            self.remove(child)
            child = nxt
        for name in pool:
            btn = Gtk.ToggleButton(label=name)
            btn.add_css_class("chip-toggle")
            if name in self._selected:
                btn.set_active(True)
            btn.connect("toggled", self._on_toggled, name)
            self.append(btn)
        self.set_visible(len(pool) > 1)

    def _on_toggled(self, btn, name):
        if btn.get_active():
            self._selected.add(name)
        else:
            self._selected.discard(name)
        if self._on_changed is not None:
            self._on_changed()

    @property
    def selected(self):
        return set(self._selected)
