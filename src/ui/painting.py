import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Gtk, Gdk, GdkPixbuf, Graphene, GObject, GLib

from .. import poster_cache


class FixedPaintable(GObject.Object, Gdk.Paintable):
    """Custom Paintable with fixed intrinsic size."""
    __gtype_name__ = "FixedPaintable"

    def __init__(self, width, height):
        super().__init__()
        self._width = width
        self._height = height
        self._texture = None

    def do_get_intrinsic_width(self):
        return self._width

    def do_get_intrinsic_height(self):
        return self._height

    def do_get_intrinsic_aspect_ratio(self):
        # Report no aspect ratio so the widget keeps a fixed intrinsic size
        # instead of scaling height with width (which pushed card titles
        # away from their posters).
        return 0.0

    def do_snapshot(self, snapshot, width, height):
        if self._texture:
            tex_w = self._texture.get_width()
            tex_h = self._texture.get_height()
            scale = max(width / tex_w, height / tex_h)
            sw = tex_w * scale
            sh = tex_h * scale
            rect = Graphene.Rect.alloc()
            rect.init((width - sw) / 2, (height - sh) / 2, sw, sh)
            snapshot.append_texture(self._texture, rect)

    def set_texture(self, texture):
        self._texture = texture
        self.invalidate_size()
        self.invalidate_contents()

    def resize(self, width, height):
        if self._width == width and self._height == height:
            return
        self._width = width
        self._height = height
        self.invalidate_size()


def _load_texture_sync(url):
    from . import poster

    cached = poster_cache.get(url)
    if cached:
        try:
            return GdkPixbuf.Pixbuf.new_from_file(cached)
        except (GLib.Error, OSError):
            pass
    data = poster._download_bytes(url)
    if data is None:
        return None
    return poster._decode_bytes(data)
