import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gtk, Adw, GLib, Gdk, GdkPixbuf
import urllib.request
import urllib.error
import tempfile
import os
import threading
from .. import poster_cache
from .anim import fade_in
from .painting import FixedPaintable

POSTER_SLOTS = threading.BoundedSemaphore(6)


def create_poster(width, height, css_class="poster-image"):
    box = Gtk.Box()
    box.set_size_request(width, height)
    box.set_halign(Gtk.Align.CENTER)
    box.set_valign(Gtk.Align.CENTER)
    box.set_hexpand(False)
    box.set_vexpand(False)
    box.set_overflow(Gtk.Overflow.HIDDEN)
    box.add_css_class(css_class)

    paintable = FixedPaintable(width, height)
    picture = Gtk.Picture()
    picture.set_paintable(paintable)
    picture.set_content_fit(Gtk.ContentFit.COVER)
    picture.set_halign(Gtk.Align.FILL)
    picture.set_valign(Gtk.Align.FILL)
    picture.set_hexpand(True)
    picture.set_vexpand(True)
    picture.set_can_shrink(True)
    picture._fixed_paintable = paintable
    box.append(picture)
    picture.set_opacity(0.0)
    return box, picture


def create_avatar(size, css_class="cast-avatar"):
    """Fixed-size square avatar. Uses a FixedPaintable so the widget never
    grows to the loaded image's natural aspect ratio (which would turn the
    border-radius circle into an oval)."""
    box = Gtk.Box()
    box.set_size_request(size, size)
    box.set_halign(Gtk.Align.CENTER)
    box.set_valign(Gtk.Align.CENTER)
    box.set_hexpand(False)
    box.set_vexpand(False)
    box.set_overflow(Gtk.Overflow.HIDDEN)
    box.add_css_class(css_class)

    paintable = FixedPaintable(size, size)
    picture = Gtk.Picture()
    picture.set_paintable(paintable)
    picture.set_content_fit(Gtk.ContentFit.COVER)
    picture.set_can_shrink(False)
    picture.set_size_request(size, size)
    picture.set_halign(Gtk.Align.FILL)
    picture.set_valign(Gtk.Align.FILL)
    picture.set_hexpand(True)
    picture.set_vexpand(True)
    box.append(picture)
    picture.set_opacity(0.0)
    return box, paintable, picture


def load_poster(url, picture, on_load=None, delay_ms=0):
    cached = poster_cache.get(url)
    if cached:
        GLib.Thread.new("poster-cache", _decode_file, cached, picture, on_load, delay_ms)
        return
    GLib.Thread.new("poster-" + url[-12:], _download, url, picture, on_load, delay_ms)


def load_avatar(url, paintable, picture, on_load=None, delay_ms=0):
    """Load a photo into a FixedPaintable-based avatar. Keeps the fixed
    intrinsic size; only swaps the texture."""
    cached = poster_cache.get(url)
    if cached:
        GLib.Thread.new("avatar-cache", _download_paintable, cached, paintable, picture, on_load, delay_ms)
        return
    GLib.Thread.new("avatar-" + url[-12:], _fetch_paintable, url, paintable, picture, on_load, delay_ms)


def _fetch_paintable(url, paintable, picture, on_load, delay_ms):
    tmp_path = None
    with POSTER_SLOTS:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Ciak/1.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = resp.read()
            poster_cache.put(url, data)
            tmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
            tmp.write(data)
            tmp.close()
            tmp_path = tmp.name
        except (urllib.error.URLError, OSError, ValueError):
            pass
    if tmp_path:
        _download_paintable(tmp_path, paintable, picture, on_load, delay_ms)


def _download_paintable(path, paintable, picture, on_load, delay_ms):
    with POSTER_SLOTS:
        try:
            pixbuf = GdkPixbuf.Pixbuf.new_from_file(path)
            GLib.idle_add(_apply_paintable, paintable, picture, pixbuf, on_load, delay_ms)
        except GLib.Error:
            pass
    try:
        os.unlink(path)
    except OSError:
        pass


def _apply_paintable(paintable, picture, pixbuf, on_load, delay_ms):
    try:
        texture = Gdk.Texture.new_for_pixbuf(pixbuf)
        paintable.set_texture(texture)
        if delay_ms > 0:
            GLib.timeout_add(delay_ms, _delayed_fade, picture, on_load)
        else:
            fade_in(picture, 300)
            if on_load:
                on_load()
    except GLib.Error:
        pass
    return False


def _download(url, picture, on_load, delay_ms):
    tmp_path = None
    with POSTER_SLOTS:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Ciak/1.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = resp.read()
            poster_cache.put(url, data)
            tmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
            tmp.write(data)
            tmp.close()
            tmp_path = tmp.name
        except (urllib.error.URLError, OSError, ValueError):
            pass
    if tmp_path:
        _decode_file(tmp_path, picture, on_load, delay_ms)


def _decode_file(path, picture, on_load, delay_ms):
    with POSTER_SLOTS:
        try:
            pixbuf = GdkPixbuf.Pixbuf.new_from_file(path)
            GLib.idle_add(_apply_pixbuf, picture, pixbuf, on_load, delay_ms)
        except GLib.Error:
            pass
    try:
        os.unlink(path)
    except OSError:
        pass


def _apply_pixbuf(picture, pixbuf, on_load, delay_ms):
    try:
        texture = Gdk.Texture.new_for_pixbuf(pixbuf)
        fixed = getattr(picture, "_fixed_paintable", None)
        if fixed is not None:
            fixed.set_texture(texture)
        else:
            picture.set_paintable(texture)
        if delay_ms > 0:
            GLib.timeout_add(delay_ms, _delayed_fade, picture, on_load)
        else:
            fade_in(picture, 300)
            if on_load:
                on_load()
    except GLib.Error:
        pass
    return False


def _delayed_fade(picture, on_load):
    fade_in(picture, 300)
    if on_load:
        on_load()
    return False
