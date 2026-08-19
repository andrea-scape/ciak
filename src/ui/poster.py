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
import math
import cairo
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


def _draw_clapperboard(cr, width, height):
    """Muted clapperboard glyph on a dark vertical gradient."""
    grad = cairo.LinearGradient(0, 0, 0, height)
    grad.add_color_stop_rgb(0, 0.16, 0.17, 0.20)
    grad.add_color_stop_rgb(1, 0.22, 0.24, 0.28)
    cr.set_source(grad)
    cr.rectangle(0, 0, width, height)
    cr.fill()

    m = min(width, height)
    bw, bh = m * 0.72, m * 0.5
    bx, by = (width - bw) / 2, (height - bh) / 2
    radius = m * 0.06

    cr.set_source_rgba(1, 1, 1, 0.28)
    cr.set_line_width(max(1.5, m * 0.045))
    cr.set_line_cap(cairo.LINE_CAP_ROUND)

    def rounded_rect(x, y, w, h, r):
        cr.save()
        cr.new_path()
        cr.move_to(x + r, y)
        cr.line_to(x + w - r, y)
        cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
        cr.line_to(x + w, y + h - r)
        cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
        cr.line_to(x + r, y + h)
        cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
        cr.line_to(x, y + r)
        cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
        cr.close_path()
        cr.restore()

    rounded_rect(bx, by, bw, bh, radius)
    cr.stroke()

    cr.set_line_width(max(1.2, m * 0.035))
    cr.move_to(bx + bw * 0.2, by + bh * 0.12)
    cr.line_to(bx + bw * 0.08, by + bh * 0.45)
    cr.line_to(bx + bw * 0.55, by + bh * 0.12)
    cr.stroke()

    cr.set_source_rgba(1, 1, 1, 0.42)
    sz = m * 0.055
    cx = width / 2
    for dx in (-1.15, 0, 1.15):
        cr.rectangle(cx + dx * sz * 1.6 - sz / 2, by + bh * 0.7 - sz / 2, sz, sz * 0.7)
        cr.fill()


def _draw_person(cr, width, height):
    """Accent radial gradient with a person silhouette."""
    r = min(width, height) / 2
    cx, cy = width / 2, height / 2
    grad = cairo.RadialGradient(cx - r * 0.3, cy - r * 0.3, 0, cx, cy, r)
    grad.add_color_stop_rgba(0, 0.21, 0.52, 0.89, 1.0)
    grad.add_color_stop_rgba(1, 0.21, 0.52, 0.89, 0.62)
    cr.set_source(grad)
    cr.arc(cx, cy, r, 0, 2 * math.pi)
    cr.fill()

    cr.set_source_rgba(1, 1, 1, 0.85)
    head_r = r * 0.42
    cr.arc(cx, cy - r * 0.42, head_r, 0, 2 * math.pi)
    cr.fill()

    cr.set_source_rgba(1, 1, 1, 0.55)
    cr.arc(cx, cy + r * 0.9, r * 1.15, math.pi, 2 * math.pi)
    cr.fill()


def _placeholder_pixbuf(paintable, icon="poster"):
    w = paintable._width
    h = paintable._height
    surface = cairo.ImageSurface(cairo.FORMAT_RGB24, w, h)
    cr = cairo.Context(surface)
    if icon == "avatar":
        _draw_person(cr, w, h)
    else:
        _draw_clapperboard(cr, w, h)
    data = surface.get_data()
    stride = surface.get_stride()
    row_bytes = w * 3
    out = bytearray(row_bytes * h)
    for y in range(h):
        src = y * stride
        dst = y * row_bytes
        for x in range(w):
            i = src + x * 4
            o = dst + x * 3
            out[o] = data[i + 2]
            out[o + 1] = data[i + 1]
            out[o + 2] = data[i]
    return GdkPixbuf.Pixbuf.new_from_bytes(
        GLib.Bytes.new(bytes(out)), GdkPixbuf.Colorspace.RGB, False, 8, w, h, row_bytes
    )


def _apply_placeholder(paintable, picture, on_load, delay_ms):
    icon = getattr(picture, "_placeholder_icon", "poster")
    GLib.idle_add(
        _apply_paintable, paintable, picture,
        _placeholder_pixbuf(paintable, icon), on_load, delay_ms,
    )


def load_poster(url, picture, on_load=None, delay_ms=0):
    picture._placeholder_icon = "poster"
    paintable = getattr(picture, "_fixed_paintable", None)
    if not url or paintable is None:
        if paintable is not None:
            _apply_placeholder(paintable, picture, on_load, delay_ms)
        return
    cached = poster_cache.get(url)
    if cached:
        GLib.Thread.new("poster-cache", _decode_file, cached, picture, on_load, delay_ms)
        return
    GLib.Thread.new("poster-" + url[-12:], _download, url, picture, on_load, delay_ms)


def load_avatar(url, paintable, picture, on_load=None, delay_ms=0):
    """Load a photo into a FixedPaintable-based avatar. Keeps the fixed
    intrinsic size; only swaps the texture."""
    picture._placeholder_icon = "avatar"
    if not url:
        _apply_placeholder(paintable, picture, on_load, delay_ms)
        return
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
    else:
        _apply_placeholder(paintable, picture, on_load, delay_ms)


def _download_paintable(path, paintable, picture, on_load, delay_ms):
    success = False
    with POSTER_SLOTS:
        try:
            pixbuf = GdkPixbuf.Pixbuf.new_from_file(path)
            GLib.idle_add(_apply_paintable, paintable, picture, pixbuf, on_load, delay_ms)
            success = True
        except GLib.Error:
            pass
    if not success:
        _apply_placeholder(paintable, picture, on_load, delay_ms)
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
    else:
        paintable = getattr(picture, "_fixed_paintable", None)
        if paintable is not None:
            _apply_placeholder(paintable, picture, on_load, delay_ms)


def _decode_file(path, picture, on_load, delay_ms):
    success = False
    with POSTER_SLOTS:
        try:
            pixbuf = GdkPixbuf.Pixbuf.new_from_file(path)
            GLib.idle_add(_apply_pixbuf, picture, pixbuf, on_load, delay_ms)
            success = True
        except GLib.Error:
            pass
    if not success:
        paintable = getattr(picture, "_fixed_paintable", None)
        if paintable is not None:
            _apply_placeholder(paintable, picture, on_load, delay_ms)
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
