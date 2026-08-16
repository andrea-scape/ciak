import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gtk, GLib, Gdk, Pango

from .painting import FixedPaintable, _load_texture_sync
from .poster import POSTER_SLOTS
from .anim import fade_in


POSTER_W = 160
POSTER_H = 240
TITLE_MAX_CHARS = 18


def config_grid(grid):
    """Configure a Gtk.FlowBox for the watchlist/search card layout."""
    grid.set_valign(Gtk.Align.START)
    grid.set_homogeneous(True)
    grid.set_column_spacing(20)
    grid.set_row_spacing(28)
    grid.set_min_children_per_line(2)
    grid.set_max_children_per_line(6)
    grid.set_selection_mode(Gtk.SelectionMode.NONE)


def make_media_card(item, main_page=None, footer=None):
    """Build a poster card matching the watchlist design.
    If footer is provided, it is appended inside the clickable button area."""
    button = Gtk.Button()
    button.add_css_class("flat")
    button.add_css_class("movie-card-button")
    button.set_halign(Gtk.Align.CENTER)
    button.set_valign(Gtk.Align.START)

    def _on_activated(_btn):
        if main_page is not None:
            main_page.show_detail(item.media_type, item)

    button.connect("clicked", _on_activated)

    card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    card.add_css_class("movie-card")
    card.set_size_request(POSTER_W, -1)
    card.set_halign(Gtk.Align.CENTER)

    frame = Gtk.Frame()
    frame.add_css_class("movie-poster-frame")
    frame.set_halign(Gtk.Align.CENTER)
    frame.set_valign(Gtk.Align.START)

    paintable = FixedPaintable(POSTER_W, POSTER_H)
    picture = Gtk.Picture()
    picture.set_paintable(paintable)
    picture.set_content_fit(Gtk.ContentFit.COVER)
    picture.set_can_shrink(False)
    picture.set_overflow(Gtk.Overflow.HIDDEN)
    picture.set_size_request(POSTER_W, POSTER_H)
    picture.add_css_class("movie-poster")
    frame.set_child(picture)
    card.append(frame)

    info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    info.set_margin_bottom(12)
    info.set_vexpand(True)

    title = Gtk.Label(label=item.title)
    title.add_css_class("heading")
    title.set_xalign(0)
    title.set_valign(Gtk.Align.START)
    title.set_ellipsize(Pango.EllipsizeMode.END)
    title.set_width_chars(TITLE_MAX_CHARS)
    title.set_max_width_chars(TITLE_MAX_CHARS)
    info.append(title)

    if item.year:
        year = Gtk.Label(label=str(item.year))
        year.add_css_class("caption")
        year.add_css_class("dim-label")
        year.set_xalign(0)
        info.append(year)

    mtype = Gtk.Label(
        label="TV Show" if item.media_type == "show" else "Movie"
    )
    mtype.add_css_class("caption")
    mtype.add_css_class("dim-label")
    mtype.set_xalign(0)
    info.append(mtype)

    spacer = Gtk.Box()
    spacer.set_vexpand(True)
    info.append(spacer)

    card.append(info)

    if footer is not None:
        footer_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        footer_box.set_halign(Gtk.Align.CENTER)
        footer_box.set_margin_bottom(4)
        footer_box.append(footer)
        card.append(footer_box)
    button.set_child(card)

    button._paintable = paintable
    button._picture = picture
    if item.poster_url:
        GLib.Thread.new(
            "poster-card",
            _load_and_apply,
            item.poster_url,
            paintable,
            picture,
        )

    return button


def _load_and_apply(url, paintable, picture):
    with POSTER_SLOTS:
        pixbuf = _load_texture_sync(url)
        if pixbuf:
            texture = Gdk.Texture.new_for_pixbuf(pixbuf)
            GLib.idle_add(_apply_texture, paintable, picture, texture)


def _apply_texture(paintable, picture, texture):
    paintable.set_texture(texture)
    fade_in(picture, 300)
    return False
