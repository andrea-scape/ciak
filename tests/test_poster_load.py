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
        poster._pending_loads = 0

    def test_duplicate_request_joins_in_flight(self):
        # Two cards asking for the same uncached URL share one download.
        with mock.patch.object(self.poster.poster_cache, "get", return_value=None), \
             mock.patch.object(self.poster.threads, "submit_poster") as submit:
            self.poster.load_poster(URL, _picture())
            self.poster.load_poster(URL, _picture())
        self.assertEqual(submit.call_count, 1)
        self.assertEqual(
            len(self.poster._INFLIGHT[self.poster._mem_key(URL, 160, 240)]), 2)

    def test_mem_cache_hit_skips_disk_and_pool(self):
        pixbuf = object()
        self.poster._MEM_PIXBUF[self.poster._mem_key(URL, 160, 240)] = pixbuf
        idle_calls = []
        with mock.patch.object(self.poster.poster_cache, "get") as cg, \
             mock.patch.object(self.poster.threads, "submit_poster") as submit, \
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
                pic = _picture()
                mw, mh = self.poster._bucket_for(pic)
                self.poster._decode_cached(
                    URL, self.poster._mem_key(URL, mw, mh),
                    path, pic, None, 0, mw, mh)
            dec.assert_called_once()
            self.assertEqual(dec.call_args.args[0], path)
            self.assertTrue(os.path.isfile(path), "cache file must persist")
            self.assertIsNotNone([
                v for k, v in self.poster._MEM_PIXBUF.items()
                if k.startswith(URL + '@')][0])

    def test_fetch_worker_fans_out_result_to_waiters(self):
        self.poster._INFLIGHT[self.poster._mem_key(URL, 160, 240)] = [
            (_picture(), None, 0), (_picture(), None, 0)]
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
            self.poster._fetch_worker(
                URL, self.poster._mem_key(URL, 160, 240), 160, 240)

        dl.assert_called_once_with(URL)
        self.assertEqual(len(idle_calls), 2)
        for call in idle_calls:
            # queued callback is the settle wrapper around the applier
            self.assertIs(call[0], self.poster._apply_and_settle)
            self.assertIs(call[1], self.poster._apply_pixbuf)
            self.assertIs(call[3], pixbuf)
        self.assertIsNotNone([
            v for k, v in self.poster._MEM_PIXBUF.items()
            if k.startswith(URL + '@')][0])
        self.assertNotIn(URL, self.poster._INFLIGHT)

    def test_fetch_worker_download_failure_uses_placeholders(self):
        self.poster._INFLIGHT[self.poster._mem_key(URL, 160, 240)] = [(_picture(), None, 0)]
        applied = []
        idle_calls = []
        with mock.patch.object(
            self.poster, "_download_bytes", return_value=None
        ), mock.patch.object(
            self.poster.poster_cache, "put"
        ), mock.patch.object(
            self.poster, "_apply_placeholder",
            side_effect=lambda *a: applied.append(1)
        ) as placeholder, mock.patch.object(
            GLib, "idle_add",
            side_effect=lambda cb, *a, **k: idle_calls.append((cb, a)) or 7
        ):
            self.poster._fetch_worker(
                URL, self.poster._mem_key(URL, 160, 240), 160, 240)
            self.assertEqual(placeholder.call_count, 0)  # queued, not applied
            cb, args = idle_calls[0]
            self.assertEqual(cb, self.poster._apply_and_settle)
            cb(*args)
        self.assertEqual(placeholder.call_count, 1)



class MemCacheLRUTest(unittest.TestCase):
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
        self.poster._MEM_PIXBUF.clear()

    def test_lru_touch_protects_hot_entries(self):
        for i in range(self.poster._MEM_MAX):
            self.poster._mem_put(f"u{i}", object())
        # touch the oldest entry — it must survive the next insert
        self.poster.get_mem_pixbuf("u0")
        self.poster._mem_put("new", object())
        self.assertIn("u0", self.poster._MEM_PIXBUF)
        self.assertNotIn("u1", self.poster._MEM_PIXBUF)

    def test_fifo_without_touch_evicts_oldest(self):
        for i in range(self.poster._MEM_MAX):
            self.poster._mem_put(f"u{i}", object())
        self.poster._mem_put("new", object())
        self.assertNotIn("u0", self.poster._MEM_PIXBUF)




class PendingLoadsTest(unittest.TestCase):
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
        self.poster._MEM_PIXBUF.clear()
        self.poster._INFLIGHT.clear()
        self.poster._pending_loads = 0

    def test_network_path_counts_until_application(self):
        idle_calls = []
        with mock.patch.object(self.poster.poster_cache, "get", return_value=None), \
                mock.patch.object(self.poster.threads, "submit_poster") as submit, \
                mock.patch.object(GLib, "idle_add",
                                  side_effect=lambda cb, *a, **k:
                                  idle_calls.append((cb, a)) or 7):
            self.poster.load_poster(URL, _picture())
            self.poster.load_poster(URL, _picture())
            self.assertEqual(self.poster.pending_loads(), 1)

            worker = submit.call_args[0][0]
            with mock.patch.object(self.poster, "_download_bytes", return_value=b"d"), \
                    mock.patch.object(self.poster, "_decode_bytes", return_value=object()), \
                    mock.patch.object(self.poster.poster_cache, "put"):
                worker(URL, self.poster._mem_key(URL, 160, 240), 160, 240)
            # settled only AFTER the queued applications complete
            self.assertEqual(self.poster.pending_loads(), 1)
            for call in idle_calls:
                self.assertIs(call[0], self.poster._apply_and_settle)

        self.poster._load_finished()  # simulate final application
        self.assertEqual(self.poster.pending_loads(), 0)

    def test_download_failure_still_settles_after_placeholder(self):
        applied = []
        idle_calls = []
        with mock.patch.object(self.poster.poster_cache, "get", return_value=None), \
                mock.patch.object(self.poster.threads, "submit_poster") as submit, \
                mock.patch.object(self.poster, "_apply_placeholder",
                                  side_effect=lambda *a: applied.append(1)), \
                mock.patch.object(self.poster.poster_cache, "put"), \
                mock.patch.object(GLib, "idle_add",
                                  side_effect=lambda cb, *a, **k:
                                  idle_calls.append((cb, a)) or 7):
            self.poster.load_poster(URL, _picture())
            worker = submit.call_args[0][0]
            with mock.patch.object(self.poster, "_download_bytes", return_value=None):
                worker(URL, self.poster._mem_key(URL, 160, 240), 160, 240)
        self.assertEqual(self.poster.pending_loads(), 1)
        cb, args = idle_calls[0]
        cb(*args)
        self.assertEqual(applied, [1])
        self.assertEqual(self.poster.pending_loads(), 0)

    def test_decode_cached_counts_until_decoded(self):
        idle_calls = []
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "p.jpg")
            open(path, "wb").close()
            with mock.patch.object(self.poster.poster_cache, "get", return_value=path), \
                    mock.patch.object(self.poster.threads, "submit_poster") as submit:
                self.poster.load_poster(URL, _picture())
                self.assertEqual(self.poster.pending_loads(), 1)
                decode = submit.call_args[0][0]
                with mock.patch.object(self.poster, "_decode_file_pixbuf",
                                       return_value=object()), \
                        mock.patch.object(GLib, "idle_add",
                                          side_effect=lambda cb, *a, **k:
                                          idle_calls.append((cb, a)) or 9):
                    decode(URL, self.poster._mem_key(URL, 160, 240),
                           path, _picture(), None, 0, 160, 240)
            self.assertEqual(self.poster.pending_loads(), 1)
            self.assertIs(idle_calls[0][0], self.poster._apply_and_settle)

        self.poster._load_finished()  # simulate final application
        self.assertEqual(self.poster.pending_loads(), 0)

    def test_memory_hit_never_counts(self):
        self.poster._MEM_PIXBUF[self.poster._mem_key(URL, 160, 240)] = object()
        with mock.patch.object(self.poster.poster_cache, "get") as cg:
            self.poster.load_poster(URL, _picture())
        cg.assert_not_called()
        self.assertEqual(self.poster.pending_loads(), 0)


if __name__ == "__main__":
    unittest.main()
