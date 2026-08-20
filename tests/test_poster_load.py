"""Poster loading: in-flight dedup, in-memory decode cache, worker pool."""

import os
import tempfile
import types
import unittest
from unittest import mock

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gtk, Adw, GLib


URL = "https://image.tmdb.org/t/p/w185/abc.jpg"


def _picture():
    return types.SimpleNamespace(_placeholder_icon="poster", _fixed_paintable=object())


class LoadPosterTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            Gtk.init()
            Adw.init()
        except TypeError:
            pass

    def setUp(self):
        from src.ui import poster

        self.poster = poster
        poster._MEM_PIXBUF.clear()
        poster._INFLIGHT.clear()

    def test_duplicate_request_joins_in_flight(self):
        # Two cards asking for the same uncached URL share one download.
        with mock.patch.object(self.poster.poster_cache, "get", return_value=None), \
             mock.patch.object(self.poster.threads, "submit") as submit:
            self.poster.load_poster(URL, _picture())
            self.poster.load_poster(URL, _picture())
        self.assertEqual(submit.call_count, 1)
        self.assertEqual(len(self.poster._INFLIGHT[URL]), 2)

    def test_mem_cache_hit_skips_disk_and_pool(self):
        pixbuf = object()
        self.poster._MEM_PIXBUF[URL] = pixbuf
        idle_calls = []
        with mock.patch.object(self.poster.poster_cache, "get") as cg, \
             mock.patch.object(self.poster.threads, "submit") as submit, \
             mock.patch.object(GLib, "idle_add", side_effect=lambda *a, **k: idle_calls.append(a)):
            self.poster.load_poster(URL, _picture())
        cg.assert_not_called()
        submit.assert_not_called()
        self.assertIs(idle_calls[0][0], self.poster._apply_pixbuf)
        self.assertIs(idle_calls[0][2], pixbuf)

    def test_decode_cached_never_deletes_cache_file(self):
        # The old _decode_file unlinked its input, destroying the disk cache
        # on first decode. _decode_cached must leave the file intact.
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "p.jpg")
            with open(path, "wb") as f:
                f.write(b"jpg")
            pixbuf = object()
            idle_calls = []
            with mock.patch.object(
                self.poster, "_decode_file_pixbuf", return_value=pixbuf
            ) as dec, mock.patch.object(
                GLib, "idle_add", side_effect=lambda *a, **k: idle_calls.append(a)
            ):
                self.poster._decode_cached(URL, path, _picture(), None, 0)
            dec.assert_called_once_with(path)
            self.assertTrue(os.path.isfile(path), "cache file must persist")
            self.assertIs(self.poster._MEM_PIXBUF[URL], pixbuf)

    def test_fetch_worker_fans_out_result_to_waiters(self):
        self.poster._INFLIGHT[URL] = [(_picture(), None, 0), (_picture(), None, 0)]
        pixbuf = mock.Mock()
        idle_calls = []
        with mock.patch.object(
            self.poster, "_download_bytes", return_value=b"data"
        ) as dl, mock.patch.object(
            self.poster.poster_cache, "put", return_value="/tmp/cached.jpg"
        ), mock.patch.object(
            self.poster, "_decode_bytes", return_value=pixbuf
        ), mock.patch.object(
            GLib, "idle_add", side_effect=lambda *a, **k: idle_calls.append(a)
        ):
            self.poster._fetch_worker(URL)

        dl.assert_called_once_with(URL)
        self.assertEqual(len(idle_calls), 2)
        for call in idle_calls:
            self.assertEqual(call[0], self.poster._apply_pixbuf)
            self.assertIs(call[2], pixbuf)
        self.assertIs(self.poster._MEM_PIXBUF[URL], pixbuf)
        self.assertNotIn(URL, self.poster._INFLIGHT)

    def test_fetch_worker_download_failure_uses_placeholders(self):
        self.poster._INFLIGHT[URL] = [(_picture(), None, 0)]
        applied = []
        with mock.patch.object(
            self.poster, "_download_bytes", return_value=None
        ), mock.patch.object(
            self.poster.poster_cache, "put"
        ), mock.patch.object(
            self.poster, "_apply_placeholder"
        ) as placeholder:
            self.poster._fetch_worker(URL)
        self.assertEqual(placeholder.call_count, 1)


if __name__ == "__main__":
    unittest.main()