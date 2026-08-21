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

    def _populate_sagas(self, watched):
        self.sagas_section.set_visible(False)
        groups = {}
        for row in watched:
            cid = row.get("collection_id")
            if not cid:
                continue
            group = groups.setdefault(
                cid,
                {
                    "name": row.get("collection_name") or "Collection",
                    "count": 0,
                    "poster": row.get("poster_url"),
                },
            )
            group["count"] += 1
        if not groups:
            return

        self.sagas_section.set_visible(True)
        self._clear_reviewed(self.sagas_flowbox)

        for cid in sorted(groups, key=lambda c: groups[c]["name"].lower()):
            group = groups[cid]
            button = Gtk.Button()
            button.add_css_class("flat")
            button.add_css_class("saga-card")
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            box.set_halign(Gtk.Align.CENTER)
            box.set_margin_top(14)
            box.set_margin_bottom(14)
            icon = Gtk.Image.new_from_icon_name("folder-symbolic")
            icon.set_pixel_size(30)
            icon.add_css_class("dim-label")
            box.append(icon)
            name = Gtk.Label(label=group["name"])
            name.add_css_class("heading")
            name.set_wrap(True)
            name.set_max_width_chars(20)
            box.append(name)
            count = Gtk.Label(label=f"{group['count']} watched")
            count.add_css_class("caption")
            count.add_css_class("dim-label")
            box.append(count)
            button.set_child(box)
            if self.main_page is not None:
                button.connect(
                    "clicked",
                    lambda _b, cid=cid, name=group["name"]: self.main_page.show_collection(cid, name),
                )
            self.sagas_flowbox.append(button)


ProfilePage = ProfileGallery
