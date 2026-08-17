"""Profile gallery: poster grid of reviewed titles with accent stars."""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, GLib

from .profile_base import ProfileBase
from .media_card import config_grid, make_media_card
from .anim import stagger_fade_in


def _make_stars(rating):
    filled = "\u2605" * rating
    empty = "\u2606" * (5 - rating)
    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
    box.set_halign(Gtk.Align.CENTER)
    f = Gtk.Label(label=filled)
    f.add_css_class("star-filled")
    box.append(f)
    e = Gtk.Label(label=empty)
    e.add_css_class("star-empty")
    box.append(e)
    return box


class ProfileGallery(ProfileBase):
    """Poster grid for reviewed with accent stars."""

    def _build_reviewed(self):
        section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        section.set_halign(Gtk.Align.CENTER)
        title_label = Gtk.Label(label="Reviewed")
        title_label.add_css_class("title-4")
        title_label.set_halign(Gtk.Align.START)
        section.append(title_label)

        self.reviewed_flowbox = Gtk.FlowBox()
        config_grid(self.reviewed_flowbox)
        section.append(self.reviewed_flowbox)
        return section

    def _populate_reviewed(self, rated):
        if not rated:
            self.reviewed_section.set_visible(False)
            return
        self.reviewed_section.set_visible(True)
        self._clear_reviewed(self.reviewed_flowbox)

        reviewed_items = []
        for item in rated:
            card = make_media_card(item, self.main_page, footer=_make_stars(item.rating))
            self.reviewed_flowbox.append(card)
            reviewed_items.append(card)
        stagger_fade_in(reviewed_items, delay_ms=30, duration_ms=250, after_ms=80)

    def _clear_reviewed(self, container):
        child = container.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            container.remove(child)
            child = nxt


ProfilePage = ProfileGallery
