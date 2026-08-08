import unittest
from unittest import mock

import urllib.error
import urllib.request

from gi.repository import GLib

from src.ui import painting


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


if __name__ == "__main__":
    unittest.main()
