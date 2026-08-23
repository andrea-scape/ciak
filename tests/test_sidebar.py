"""Sidebar: section labels, avatar, width pinning."""

import sys
import types
import unittest
from unittest import mock

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gio", "2.0")
from gi.repository import Gtk, Adw, Gio

if "src.config" not in sys.modules:
    _cfg = types.ModuleType("src.config")
    _cfg.APP_ID = "io.github.andrea_scape.ciak.Devel"
    _cfg.APP_VERSION = "0.0.0-test"
    _cfg.APP_NAME = "Ciak"
    sys.modules["src.config"] = _cfg
else:
    _cfg = sys.modules["src.config"]

from src.ui.main_page import MainPage
from src.window import MainWindow


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


class _FakeRepo:
    def get_watchlist_ids(self):
        return set()
    def get_watched_ids(self, media_type):
        return set()
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


def _find_by_type(widget, cls):
    child = widget.get_first_child()
    while child:
        if isinstance(child, cls):
            return child
        result = _find_by_type(child, cls)
        if result is not None:
            return result
        child = child.get_next_sibling()
    return None


def _find_label_with_text(widget, text):
    child = widget.get_first_child()
    while child:
        if isinstance(child, Gtk.Label) and child.get_text() == text:
            return child
        result = _find_label_with_text(child, text)
        if result is not None:
            return result
        child = child.get_next_sibling()
    return None


class SidebarSectionLabelsTest(unittest.TestCase):
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

    def _make(self):
        with mock.patch("gi.repository.GLib.Thread.new"):
            w = MainWindow(user_repo=_FakeRepo(), metadata_service=object())
            w.settings = _Settings(_cfg.APP_ID)
            mp = MainPage(w, _FakeRepo(), object())
            w.set_page(mp)
        return mp

    def test_library_label_present(self):
        mp = self._make()
        label = _find_label_with_text(mp._sidebar_page, "Library")
        self.assertIsNotNone(label)
        self.assertTrue(label.has_css_class("caption"))
        self.assertTrue(label.has_css_class("dim-label"))

    def test_you_label_present(self):
        mp = self._make()
        label = _find_label_with_text(mp._sidebar_page, "You")
        self.assertIsNotNone(label)
        self.assertTrue(label.has_css_class("caption"))

    def test_no_bare_separator(self):
        mp = self._make()
        sep = _find_by_type(mp._sidebar_page, Gtk.Separator)
        self.assertIsNone(sep)

    def test_profile_row_has_avatar_and_no_subtitle(self):
        mp = self._make()
        row = mp.profile_list_box.get_first_child()
        self.assertIsNotNone(row)
        self.assertIsNone(row.get_next_sibling())
        avatar = _find_by_type(row, Adw.Avatar)
        self.assertIsNotNone(avatar)
        self.assertFalse(row.get_subtitle())

    def test_sidebar_width_pinned(self):
        mp = self._make()
        sv = mp._split_view
        self.assertEqual(sv.get_min_sidebar_width(), 220)
        self.assertEqual(sv.get_max_sidebar_width(), 220)


if __name__ == "__main__":
    unittest.main()
