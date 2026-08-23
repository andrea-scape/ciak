"""Profile gallery: poster grid of reviewed titles with accent stars."""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, GLib

from .profile_base import ProfileBase
from .media_card import config_grid, make_media_card
from .poster import create_poster, load_poster
from .anim import CONTENT_MS, CONTENT_PX, rise_fade_in


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
        if not getattr(self, "_unified_load", False):
            rise_fade_in(reviewed_items, CONTENT_MS, CONTENT_PX)

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
                    "poster": None,
                    "first_id": None,
                },
            )
            group["count"] += 1
            # deterministic stand-in logo: lowest tmdb_id, independent of
            # watch order; replaced by the collection's actual first part
            # once metadata arrives (_apply_saga_totals)
            rid = row.get("tmdb_id")
            if group["first_id"] is None or (
                rid is not None and rid < group["first_id"]
            ):
                group["first_id"] = rid
                group["poster"] = row.get("poster_url")
        if not groups:
            return

        self.sagas_section.set_visible(True)
        self._saga_refs = []
        self._clear_reviewed(self.sagas_flowbox)
        for cid in sorted(groups, key=lambda c: groups[c]["name"].lower()):
            self._append_saga(cid, groups[cid])

        gen = self._reload_gen
        GLib.Thread.new("saga-totals", self._fetch_saga_totals, gen, list(groups.keys()))

    def _make_saga_tick(self):
        tick = Gtk.Image.new_from_icon_name("object-select-symbolic")
        tick.set_pixel_size(12)
        tick.set_valign(Gtk.Align.CENTER)
        tick.add_css_class("saga-check")
        tick.set_visible(False)
        return tick

    def _open_saga(self, cid, name):
        if self.main_page is not None:
            self.main_page.show_collection(cid, name)

    def _append_saga(self, cid, group):
        button = Gtk.Button()
        button.add_css_class("flat")
        button.add_css_class("saga-button")
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        thumb_pic = self._add_saga_thumb(box, group, center=True)
        text_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        text_box.set_valign(Gtk.Align.CENTER)
        name = Gtk.Label(label=group["name"])
        name.add_css_class("heading")
        name.set_wrap(True)
        name.set_max_width_chars(24)
        name.set_halign(Gtk.Align.START)
        text_box.append(name)
        count_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        count_row.set_halign(Gtk.Align.START)
        tick = self._make_saga_tick()
        count_row.append(tick)
        count = Gtk.Label(label=f"{group['count']} watched")
        count.add_css_class("caption")
        count.add_css_class("dim-label")
        count_row.append(count)
        text_box.append(count_row)
        box.append(text_box)
        button.set_child(box)
        button.set_tooltip_text(group["name"])
        if self.main_page is not None:
            button.connect(
                "clicked",
                lambda _b, cid=cid, name=group["name"]: self._open_saga(cid, name),
            )
        self.sagas_flowbox.append(button)
        self._saga_refs.append({
            "cid": cid, "watched": group["count"],
            "set_count": count.set_text, "tick": tick,
            "thumb_pic": thumb_pic,
        })

    def _add_saga_thumb(self, parent, group, center=False, size=(48, 72)):
        """Append a poster thumbnail (or folder-icon fallback) to a
        horizontal container; returns the picture for later logo upgrades."""
        if group["poster"]:
            thumb, thumb_pic = create_poster(*size, css_class="saga-poster")
            load_poster(group["poster"], thumb_pic)
            if center:
                thumb.set_valign(Gtk.Align.CENTER)
            parent.append(thumb)
            return thumb_pic
        icon = Gtk.Image.new_from_icon_name("folder-symbolic")
        icon.set_pixel_size(30)
        icon.add_css_class("dim-label")
        if center:
            icon.set_valign(Gtk.Align.CENTER)
        parent.append(icon)
        return None

    def _fetch_saga_totals(self, gen, cids):
        totals = {}
        posters = {}
        for cid in cids:
            try:
                col = self.metadata_service.get_collection(cid)
            except Exception:
                continue
            if col is not None and getattr(col, "parts", None):
                totals[cid] = len(col.parts)
                url = getattr(col.parts[0], "poster_url", None)
                if url:
                    posters[cid] = url
        if totals:
            GLib.idle_add(self._apply_saga_totals, gen, totals, posters)
        return False

    def _apply_saga_totals(self, gen, totals, posters=None):
        if gen != self._reload_gen:
            return False
        for ref in getattr(self, "_saga_refs", []):
            total = totals.get(ref["cid"])
            if not total:
                continue
            ref["set_count"](f"{ref['watched']} of {total} watched")
            if ref["watched"] >= total:
                ref["tick"].set_visible(True)
            url = (posters or {}).get(ref["cid"])
            if ref["thumb_pic"] is not None and url:
                load_poster(url, ref["thumb_pic"])
        return False


ProfilePage = ProfileGallery
