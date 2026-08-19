import unittest
from unittest import mock

import urllib.error
import urllib.request

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import GLib, Gtk, Adw

from src.ui import painting
from src.ui import poster


class PaintingFallbackTest(unittest.TestCase):
    def test_load_texture_sync_returns_none_on_fetch_failure(self):
        # Regression: a failed poster download must degrade to None (grey
        # placeholder), not raise NameError. Before the GLib import fix this
        # raised "NameError: name 'GLib' is not defined" because the except
        # clause referenced GLib.Error without importing GLib.
        with mock.patch.object(painting.poster_cache, "get", return_value=None), \
             mock.patch("src.ui.painting.urllib.request.urlopen",
                        side_effect=urllib.error.URLError("offline")):
            self.assertIsNone(
                painting._load_texture_sync("https://example.com/poster.jpg")
            )

    def test_load_texture_sync_returns_none_when_cached_decode_fails(self):
        # Same guarantee when decoding a cached file fails (e.g. a transient
        # image-loader failure) instead of when downloading fails.
        with mock.patch.object(painting.poster_cache, "get",
                               return_value="/nonexistent/poster.jpg"), \
             mock.patch("src.ui.painting.GdkPixbuf.Pixbuf.new_from_file",
                        side_effect=GLib.Error("boom")):
            self.assertIsNone(
                painting._load_texture_sync("https://example.com/poster.jpg")
            )


class PlaceholderPixbufTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            Gtk.init()
            Adw.init()
        except TypeError:
            pass

    def test_poster_placeholder_pixbuf_matches_paintable_size(self):
        paintable = painting.FixedPaintable(200, 300)
        pb = poster._placeholder_pixbuf(paintable, "poster")
        self.assertEqual(pb.get_width(), 200)
        self.assertEqual(pb.get_height(), 300)

    def test_avatar_placeholder_pixbuf_matches_paintable_size(self):
        paintable = painting.FixedPaintable(96, 96)
        pb = poster._placeholder_pixbuf(paintable, "avatar")
        self.assertEqual(pb.get_width(), 96)
        self.assertEqual(pb.get_height(), 96)

    def test_placeholder_pixbuf_is_not_flat_petrol(self):
        # Regression: the placeholder used to be a solid flat fill; the
        # replacement must actually vary (gradient/icon) across the surface.
        paintable = painting.FixedPaintable(100, 100)
        pb = poster._placeholder_pixbuf(paintable, "poster")
        data = pb.get_pixels()
        stride = pb.get_rowstride()
        offsets = [2 * stride + 2 * 3, 98 * stride + 2 * 3,
                   2 * stride + 98 * 3, 50 * stride + 50 * 3]
        samples = [bytes(data[o:o + 3]) for o in offsets]
        self.assertGreater(len(set(samples)), 1)


if __name__ == "__main__":
    unittest.main()
