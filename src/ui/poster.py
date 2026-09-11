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
from collections import OrderedDict
import cairo
from .. import poster_cache
from .. import threads
from .anim import CONTENT_MS, fade_in
from .painting import FixedPaintable

POSTER_SLOTS = threading.BoundedSemaphore(8)

# Decode ceiling fallback; real targets come from each picture's
# device size (_bucket_for) so big surfaces stay sharp.
_DECODE_MAX_W, _DECODE_MAX_H = 320, 480

# In-memory texture cache: url -> Gdk.Texture (LRU-ordered). Every
# picture widget showing the same URL shares one GPU-resident copy, so
# grids re-using a poster never re-upload or re-decode it.
_MEM_PIXBUF: "OrderedDict[str, object]" = OrderedDict()
_MEM_MAX = 64


def _bucket_for(picture):
    """Device-pixel decode target for this picture: CSS size x scale."""
    paintable = getattr(picture, "_fixed_paintable", None)
    w = int(getattr(paintable, "_width", 160) or 160)
    h = int(getattr(paintable, "_height", 240) or 240)
    try:
        scale = max(1, int(picture.get_scale_factor() or 1))
    except Exception:
        scale = 1
    return min(w * scale, 1024), min(h * scale, 1536)


def _mem_key(url, max_w, max_h):
    """Cache key bucketed by decode size so a grid texture and a detail
    texture of the same poster never collide."""
    return f"{url}@{max_w}x{max_h}"
# url -> pending (picture, on_load, delay_ms) waiting on one download.
_INFLIGHT: dict[str, list] = {}

# Number of async poster loads (network fetch or disk decode) started but
# not yet applied. Lets pages hold their launch reveal until all posters
# are ready.
_pending_loads = 0


def _load_started():
    global _pending_loads
    _pending_loads += 1


def _load_finished():
    global _pending_loads
    _pending_loads = max(0, _pending_loads - 1)


def pending_loads() -> int:
    """Outstanding async poster loads (0 when everything settled)."""
    return _pending_loads


def _mem_put(url, pixbuf):
    _MEM_PIXBUF[url] = pixbuf
    _MEM_PIXBUF.move_to_end(url)
    while len(_MEM_PIXBUF) > _MEM_MAX:
        _MEM_PIXBUF.pop(next(iter(_MEM_PIXBUF)))


def get_mem_pixbuf(url):
    """Return the in-memory decoded pixbuf for url, or None. Safe from any
    thread (GIL-held dict access) — lets other load paths share this cache.
    Touches the entry so hot posters are evicted last (LRU)."""
    pixbuf = _MEM_PIXBUF.get(url)
    if pixbuf is not None:
        _MEM_PIXBUF.move_to_end(url)
    return pixbuf


def put_mem_pixbuf(url, pixbuf):
    """Store a decoded pixbuf in the shared in-memory poster cache."""
    _mem_put(url, pixbuf)


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
        priority=GLib.PRIORITY_HIGH_IDLE,
    )


def load_poster(url, picture, on_load=None, delay_ms=0):
    picture._placeholder_icon = "poster"
    paintable = getattr(picture, "_fixed_paintable", None)
    if not url or paintable is None:
        if paintable is not None:
            _apply_placeholder(paintable, picture, on_load, delay_ms)
        return
    max_w, max_h = _bucket_for(picture)
    key = _mem_key(url, max_w, max_h)
    image = _MEM_PIXBUF.get(key)
    if image is not None:
        _MEM_PIXBUF.move_to_end(key)
        GLib.idle_add(_apply_pixbuf, picture, image, on_load, delay_ms, False, priority=GLib.PRIORITY_HIGH_IDLE)
        return
    cached = poster_cache.get(url)
    if cached:
        _load_started()
        threads.submit_poster(_decode_cached, url, key, cached, picture,
                              on_load, delay_ms, max_w, max_h)
        return
    # Dedup: one download per URL+size; latecomers wait for its result.
    if key in _INFLIGHT:
        _INFLIGHT[key].append((picture, on_load, delay_ms))
        return
    _INFLIGHT[key] = [(picture, on_load, delay_ms)]
    _load_started()
    threads.submit_poster(_fetch_worker, url, key, max_w, max_h)


def load_avatar(url, paintable, picture, on_load=None, delay_ms=0):
    """Load a photo into a FixedPaintable-based avatar. Keeps the fixed
    intrinsic size; only swaps the texture."""
    picture._placeholder_icon = "avatar"
    if not url:
        _apply_placeholder(paintable, picture, on_load, delay_ms)
        return
    cached = poster_cache.get(url)
    if cached:
        GLib.Thread.new("avatar-cache", _download_paintable, cached, paintable, picture, on_load, delay_ms, False)
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
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
    else:
        _apply_placeholder(paintable, picture, on_load, delay_ms)


def _download_paintable(path, paintable, picture, on_load, delay_ms, animate=False):
    success = False
    with POSTER_SLOTS:
        try:
            pixbuf = GdkPixbuf.Pixbuf.new_from_file(path)
            GLib.idle_add(_apply_paintable, paintable, picture, pixbuf, on_load, delay_ms, animate, priority=GLib.PRIORITY_HIGH_IDLE)
            success = True
        except GLib.Error:
            pass
    if not success:
        _apply_placeholder(paintable, picture, on_load, delay_ms)


def _apply_paintable(paintable, picture, pixbuf, on_load, delay_ms, animate=False):
    try:
        texture = _make_texture(pixbuf)
        paintable.set_texture(texture)
        if animate:
            if delay_ms > 0:
                GLib.timeout_add(delay_ms, _delayed_fade, picture, on_load)
            else:
                fade_in(picture, CONTENT_MS)
                if on_load:
                    on_load()
        else:
            picture.set_opacity(1.0)
            if on_load:
                on_load()
    except GLib.Error:
        pass
    return False


def _apply_and_settle(cb, *args):
    """Run a queued application callback, then mark that poster load as
    settled (pixels/placeholder actually applied)."""
    cb(*args)
    _load_finished()
    return False


def _fetch_worker(url, key, max_w=_DECODE_MAX_W, max_h=_DECODE_MAX_H):
    """Download+decode one poster in the pool; fan out to in-flight waiters."""
    waiters = _INFLIGHT.pop(key, [])
    data = _download_bytes(url)
    pixbuf = (_decode_bytes(data, max_w, max_h)
              if data is not None else None)
    image = pixbuf
    if pixbuf is not None:
        # Immutable value object — safe to create off the main thread;
        # every picture widget then shares this one GPU-resident copy.
        try:
            image = _make_texture(pixbuf)
        except (GLib.Error, TypeError, ValueError):
            image = pixbuf
        _mem_put(key, image)
        # Stamp a pre-scaled thumbnail so later starts skip the
        # full-size decode entirely. Best-effort; never fatal.
        try:
            _ok, tb = pixbuf.save_to_bufferv("jpeg", ["quality"], ["75"])
            poster_cache.put_scaled(url, max_w, max_h, tb)
        except (GLib.Error, OSError, ValueError):
            pass
    for picture, on_load, delay_ms in waiters:
        if image is not None:
            GLib.idle_add(_apply_and_settle, _apply_pixbuf, picture, image,
                          on_load, delay_ms, priority=GLib.PRIORITY_HIGH_IDLE)
        else:
            paintable = getattr(picture, "_fixed_paintable", None)
            if paintable is not None:
                GLib.idle_add(_apply_and_settle, _apply_placeholder, paintable,
                              picture, on_load, delay_ms,
                              priority=GLib.PRIORITY_HIGH_IDLE)
            else:
                _load_finished()  # nothing to apply; settle immediately

def _download_bytes(url):
    with POSTER_SLOTS:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Ciak/1.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = resp.read()
            poster_cache.put(url, data)
            return data
        except (urllib.error.URLError, OSError, ValueError):
            return None


def _decode_bytes(data, max_w=_DECODE_MAX_W, max_h=_DECODE_MAX_H):
    tmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
    try:
        tmp.write(data)
        tmp.close()
        return _decode_file_pixbuf(tmp.name, max_w, max_h)
    except (GLib.Error, OSError, ValueError):
        return None
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass


def _decode_cached(url, key, path, picture, on_load, delay_ms,
                   max_w=_DECODE_MAX_W, max_h=_DECODE_MAX_H):
    """Decode a disk-cached poster; the cache file is never deleted."""
    pixbuf = None
    thumb = poster_cache.get_scaled(url, max_w, max_h)
    if thumb is not None:
        pixbuf = _decode_file_pixbuf(thumb, max_w, max_h)
    if pixbuf is None:
        pixbuf = _decode_file_pixbuf(path, max_w, max_h)
    if pixbuf is not None:
        try:
            image = _make_texture(pixbuf)
        except (GLib.Error, TypeError, ValueError):
            image = pixbuf
        if thumb is None:
            # Stamp a pre-scaled thumbnail so later starts skip the
            # full-size decode entirely. Best-effort; never fatal.
            try:
                _ok, tb = pixbuf.save_to_bufferv("jpeg", ["quality"], ["75"])
                poster_cache.put_scaled(url, max_w, max_h, tb)
            except (GLib.Error, OSError, ValueError):
                pass
        _mem_put(key, image)
        GLib.idle_add(_apply_and_settle, _apply_pixbuf, picture, image,
                      on_load, delay_ms, False, priority=GLib.PRIORITY_HIGH_IDLE)
    else:
        paintable = getattr(picture, "_fixed_paintable", None)
        if paintable is not None:
            GLib.idle_add(_apply_and_settle, _apply_placeholder, paintable,
                          picture, on_load, delay_ms,
                          priority=GLib.PRIORITY_HIGH_IDLE)
        else:
            _load_finished()  # nothing to apply; settle immediately


# Posters are displayed at <=160x240 (2x for hidpi); decoding at this
# ceiling instead of full resolution cuts CPU and memory per load.
def _decode_file_pixbuf(path, max_w=_DECODE_MAX_W, max_h=_DECODE_MAX_H):
    with POSTER_SLOTS:
        try:
            return GdkPixbuf.Pixbuf.new_from_file_at_size(
                path, max_w, max_h
            )
        except GLib.Error:
            return None


def _make_texture(pixbuf):
    """Wrap a pixbuf as a Gdk.MemoryTexture. GdkPixbuf stores premultiplied
    RGBA, so alpha pixbufs map to R8G8B8A8_PREMULTIPLIED; opaque ones upload
    as R8G8B8. Zero-copy vs the old PNG encode/decode roundtrip."""
    fmt = (
        Gdk.MemoryFormat.R8G8B8A8_PREMULTIPLIED
        if pixbuf.get_has_alpha()
        else Gdk.MemoryFormat.R8G8B8
    )
    return Gdk.MemoryTexture.new(
        pixbuf.get_width(),
        pixbuf.get_height(),
        fmt,
        GLib.Bytes.new(pixbuf.get_pixels()),
        pixbuf.get_rowstride(),
    )


def _apply_pixbuf(picture, pixbuf, on_load, delay_ms, animate=False):
    try:
        from gi.repository import Gdk as _Gdk
        if isinstance(pixbuf, _Gdk.Texture):
            texture = pixbuf  # shared cached copy — no conversion
        else:
            texture = _make_texture(pixbuf)
        fixed = getattr(picture, "_fixed_paintable", None)
        if fixed is not None:
            fixed.set_texture(texture)
        else:
            picture.set_paintable(texture)
        if animate:
            if delay_ms > 0:
                GLib.timeout_add(delay_ms, _delayed_fade, picture, on_load)
            else:
                fade_in(picture, CONTENT_MS)
                if on_load:
                    on_load()
        else:
            picture.set_opacity(1.0)
            if on_load:
                on_load()
    except GLib.Error:
        pass
    return False


def _delayed_fade(picture, on_load):
    fade_in(picture, CONTENT_MS)
    if on_load:
        on_load()
    return False
