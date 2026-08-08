import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Gtk, Gdk, GdkPixbuf, Graphene, GObject, GLib

from .. import poster_cache
import urllib.request
import urllib.error
import tempfile
import os


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
        return self._width / self._height if self._height else 0

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
    try:
        cached = poster_cache.get(url)
        if cached:
            return GdkPixbuf.Pixbuf.new_from_file(cached)
        req = urllib.request.Request(url, headers={"User-Agent": "Ciak/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = resp.read()
        poster_cache.put(url, data)
        tmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
        tmp.write(data)
        tmp.close()
        pixbuf = GdkPixbuf.Pixbuf.new_from_file(tmp.name)
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
        return pixbuf
    except (urllib.error.URLError, OSError, ValueError, GLib.Error):
        return None
