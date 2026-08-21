"""Navigation stack: back button returns through detail/collection/page history."""

import sys
import types
import unittest
from unittest import mock

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gio", "2.0")
gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gtk, Adw, Gio

if "src.config" not in sys.modules:
    _cfg = types.ModuleType("src.config")
    _cfg.APP_ID = "io.github.andrea_scape.ciak.Devel"
    _cfg.APP_VERSION = "0.0.0-test"
    _cfg.APP_NAME = "Ciak"
    sys.modules["src.config"] = _cfg
else:
    _cfg = sys.modules["src.config"]

from src.domain.models import Movie
from src.window import MainWindow
from src.ui.main_page import MainPage


class _Settings:
    def __init__(self, app_id, values=None):
        self._app_id = app_id
        self._values = dict(values or {})
        self._bools = {
            "window-maximized": False,
            "disable-animations": False,
            "show-sidebar": True,
            "remember-content-filter": False,
        }
        self._bools.update(self._values.get("bools", {}))
        self._strings = {"default-page": "watchlist", "content-filter-mode": "all"}
        self._strings.update(self._values.get("strings", {}))
        self._ints = {"window-width": 1200, "window-height": 800}

    def get_boolean(self, key):
        return self._bools.get(key, False)

    def get_string(self, key):
        return self._strings.get(key, "")

    def get_int(self, key):
        return self._ints.get(key, 0)

    def set_boolean(self, key, value):
        self._bools[key] = value

    def set_string(self, key, value):
        self._strings[key] = value

    def set_int(self, key, value):
        self._ints[key] = value

    def bind(self, key, widget, prop, flags):
        pass

    def connect(self, key, callback):
        return 0


class FakeUserRepo:
    def __init__(self, watched=None):
        self._watched = set(watched or {})

    def get_watchlist_ids(self):
        return set()

    def get_watched_ids(self, media_type):
        return set(self._watched)

    def get_ratings(self, media_type):
        return []

    def get_watched_episodes_for_show(self, tmdb_id):
        return set()

    def get_watched_show_ids(self):
        return set()

    def get_watchlist(self, mode=None):
        return []

    def get_media_item(self, tmdb_id):
        return None


class FakeMetadata:
    def __init__(self, collection=None):
        self._collection = collection

    def get_movie(self, tmdb_id):
        return Movie(tmdb_id=tmdb_id, title="Test", runtime=100)

    def get_related_movies(self, tmdb_id):
        return []

    def get_movie_cast(self, tmdb_id):
        return []

    def get_show(self, tmdb_id):
        return None

    def get_show_seasons(self, tmdb_id):
        return []

    def get_related_shows(self, tmdb_id):
        return []

    def get_show_cast(self, tmdb_id):
        return []

    def get_collection(self, collection_id):
        return self._collection


class TimeoutQueue:
    """Replacement for GLib.timeout_add/source_remove with manual flushing."""

    def __init__(self):
        self._next_id = 1
        self._items = {}

    def timeout_add(self, interval, cb, *args):
        iid = self._next_id
        self._next_id += 1
        self._items[iid] = (interval, cb, args)
        return iid

    def source_remove(self, iid):
        self._items.pop(iid, None)

    def flush(self):
        pending = list(self._items.items())
        self._items.clear()
        for _iid, (_interval, cb, args) in pending:
            if cb(*args) is True:
                self.timeout_add(_interval, cb, *args)


class NavStackTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            Gtk.init()
            Adw.init()
        except TypeError:
            pass
        cls._orig_settings = Gio.Settings.new
        Gio.Settings.new = _Settings

    @classmethod
    def tearDownClass(cls):
        Gio.Settings.new = cls._orig_settings

    def setUp(self):
        self.timeouts = TimeoutQueue()
        patcher_timeout = mock.patch(
            "src.ui.main_page.GLib.timeout_add", self.timeouts.timeout_add
        )
        patcher_remove = mock.patch(
            "src.ui.main_page.GLib.source_remove", self.timeouts.source_remove
        )
        patcher_thread = mock.patch("gi.repository.GLib.Thread.new")
        for p in (patcher_timeout, patcher_remove, patcher_thread):
            p.start()
            self.addCleanup(p.stop)
        metadata = FakeMetadata(collection=None)
        window = MainWindow(user_repo=FakeUserRepo(), metadata_service=metadata)
        window.settings = _Settings(_cfg.APP_ID)
        self.mp = MainPage(window, FakeUserRepo(), metadata)
        window.set_page(self.mp)

    def _detail_children(self):
        names = []
        for name in ("detail_0", "detail_1"):
            if self.mp.content_stack.get_child_by_name(name) is not None:
                names.append(name)
        return names

    # T1
    def test_back_through_two_details_then_watchlist(self):
        mp = self.mp
        a = Movie(tmdb_id=1, title="Movie A")
        b = Movie(tmdb_id=2, title="Movie B")
        mp.show_detail("movie", a)
        slot_a = mp._current_detail_name
        mp.show_detail("movie", b)
        slot_b = mp._current_detail_name
        self.assertNotEqual(slot_a, slot_b)
        self.assertEqual(mp.content_stack.get_visible_child_name(), slot_b)
        mp.go_back()
        self.assertEqual(mp.content_stack.get_visible_child_name(), slot_a)
        self.assertEqual(mp._current_page, "detail")
        mp.go_back()
        self.assertEqual(mp.content_stack.get_visible_child_name(), "watchlist")
        self.assertEqual(mp._current_page, "watchlist")

    # T2
    def test_detail_collection_detail_back_walks_stack(self):
        mp = self.mp
        d = Movie(tmdb_id=10, title="D Movie")
        m = Movie(tmdb_id=20, title="M Movie")
        mp.show_detail("movie", d)
        mp.show_collection(1241, "HP")
        self.assertEqual(mp.content_stack.get_visible_child_name(), "collection")
        mp.show_detail("movie", m)
        self.assertEqual(mp.content_stack.get_visible_child_name(), mp._current_detail_name)
        mp.go_back()
        self.assertEqual(mp.content_stack.get_visible_child_name(), "collection")
        self.assertEqual(mp._current_page, "collection")
        mp.go_back()
        self.assertEqual(mp.content_stack.get_visible_child_name(), mp._current_detail_name)
        self.assertEqual(mp._detail_title, "D Movie")
        self.assertEqual(mp._current_page, "detail")
        mp.go_back()
        self.assertEqual(mp.content_stack.get_visible_child_name(), "watchlist")
        self.assertEqual(mp._current_page, "watchlist")

    # T3
    def test_back_from_detail_returns_to_sidebar_page(self):
        mp = self.mp
        mp._select_page("history")
        self.assertEqual(mp.content_stack.get_visible_child_name(), "history")
        x = Movie(tmdb_id=30, title="X Movie")
        mp.show_detail("movie", x)
        self.assertEqual(mp._current_page, "detail")
        mp.go_back()
        self.assertEqual(mp.content_stack.get_visible_child_name(), "history")
        self.assertEqual(mp._current_page, "history")
        mp.go_back()
        self.assertEqual(mp.content_stack.get_visible_child_name(), "watchlist")
        self.assertEqual(mp._current_page, "watchlist")

    # T4
    def test_double_show_detail_ignored(self):
        mp = self.mp
        a = Movie(tmdb_id=1, title="Movie A")
        mp.show_detail("movie", a)
        stack_before = list(mp._nav_stack)
        visible_before = mp.content_stack.get_visible_child_name()
        mp.show_detail("movie", Movie(tmdb_id=1, title="Movie A"))
        self.assertEqual(stack_before, mp._nav_stack)
        self.assertEqual(visible_before, mp.content_stack.get_visible_child_name())
        detail_entries = [e for e in mp._nav_stack if e[0] == "detail"]
        self.assertEqual(len(detail_entries), 1)

    # T5
    def test_flushed_navigation_leaves_no_orphans(self):
        mp = self.mp
        d = Movie(tmdb_id=10, title="D Movie")
        m = Movie(tmdb_id=20, title="M Movie")
        mp.show_detail("movie", d)
        self.timeouts.flush()
        mp.show_collection(1241, "HP")
        self.timeouts.flush()
        mp.show_detail("movie", m)
        self.timeouts.flush()
        mp.go_back()
        self.assertEqual(mp.content_stack.get_visible_child_name(), "collection")
        self.timeouts.flush()
        mp.go_back()
        self.assertEqual(mp._current_page, "detail")
        self.assertEqual(mp._detail_title, "D Movie")
        self.timeouts.flush()
        mp.go_back()
        self.timeouts.flush()
        self.assertEqual(mp._current_page, "watchlist")
        self.assertEqual(self._detail_children(), [])
        self.assertIsNone(mp.content_stack.get_child_by_name("collection"))

    # T6
    def test_back_to_dirty_collection_refreshes(self):
        mp = self.mp
        mp.show_detail("movie", Movie(tmdb_id=10, title="D Movie"))
        mp.show_collection(1241, "HP")
        page = mp._collection_page
        mp.show_detail("movie", Movie(tmdb_id=20, title="M Movie"))
        with mock.patch.object(page, "refresh") as refresh:
            mp.invalidate_page("collection")
            mp.go_back()
            refresh.assert_called_once()

    # T7
    def test_back_to_clean_collection_skips_refresh(self):
        mp = self.mp
        mp.show_detail("movie", Movie(tmdb_id=10, title="D Movie"))
        mp.show_collection(1241, "HP")
        page = mp._collection_page
        mp.show_detail("movie", Movie(tmdb_id=20, title="M Movie"))
        with mock.patch.object(page, "refresh") as refresh:
            mp.go_back()
            refresh.assert_not_called()


if __name__ == "__main__":
    unittest.main()
