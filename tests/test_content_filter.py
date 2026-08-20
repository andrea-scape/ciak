"""Content filter (ALL/Movies/Shows): global sync + persistence."""

import sys
import types
import unittest

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

from src.domain.models import Movie
from src.window import MainWindow
from src.ui.main_page import MainPage
from src.ui.search_page import SearchPage


class _Settings:
    def __init__(self, app_id, values=None):
        self._app_id = app_id
        self._values = dict(values or {})
        self._bools = {
            "window-maximized": False,
            "disable-animations": False,
            "show-sidebar": True,
            "show-stats": True,
            "remember-content-filter": False,
        }
        self._bools.update(self._values.get("bools", {}))
        self._strings = {
            "default-page": "watchlist",
            "sidebar-default-mode": "remember",
            "content-filter-mode": "all",
        }
        self._strings.update(self._values.get("strings", {}))
        self._ints = {"window-width": 1200, "window-height": 800}
        self._ints.update(self._values.get("ints", {}))
        self.writes = []

    def get_boolean(self, key):
        return self._bools.get(key, False)

    def get_string(self, key):
        return self._strings.get(key, "")

    def get_int(self, key):
        return self._ints.get(key, 0)

    def set_boolean(self, key, value):
        self.writes.append(("ssl", key, value))
        self._bools[key] = value

    def set_string(self, key, value):
        self.writes.append(("str", key, value))
        self._strings[key] = value

    def set_int(self, key, value):
        self._ints[key] = value
        self.writes.append(("int", key, value))

    def bind(self, key, widget, prop, flags):
        self.writes.append(("bind", key))

    def connect(self, key, callback):
        return 0


class FakeUserRepo:
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


class FakeMetadata:
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


class ContentFilterTest(unittest.TestCase):
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

    def _make_main_page(self, values=None):
        win = MainWindow(user_repo=FakeUserRepo(), metadata_service=FakeMetadata())
        win.settings = _Settings(_cfg.APP_ID, values)
        mp = MainPage(win, FakeUserRepo(), FakeMetadata())
        win.set_page(mp)
        return win, mp

    def test_startup_applies_saved_mode_when_remembering(self):
        win, mp = self._make_main_page(
            values={
                "bools": {"remember-content-filter": True},
                "strings": {"content-filter-mode": "shows"},
            }
        )
        self.assertEqual(mp._global_mode, "shows")
        self.assertTrue(mp.show_toggle.get_active())
        self.assertFalse(mp.movie_toggle.get_active())
        page = mp._pages.get("watchlist")
        if page is not None:
            self.assertEqual(page._mode, "shows")

    def test_startup_defaults_all_when_not_remembering(self):
        win, mp = self._make_main_page(
            values={
                "bools": {"remember-content-filter": False},
                "strings": {"content-filter-mode": "movies"},
            }
        )
        self.assertEqual(mp._global_mode, "all")
        self.assertTrue(mp.all_toggle.get_active())

    def test_set_global_mode_broadcasts_and_persists(self):
        win, mp = self._make_main_page(
            values={"bools": {"remember-content-filter": True}}
        )
        mp.set_global_mode("movies")
        self.assertEqual(mp._global_mode, "movies")
        self.assertTrue(mp.movie_toggle.get_active())
        self.assertIn(("str", "content-filter-mode", "movies"), win.settings.writes)

    def test_set_global_mode_does_not_persist_when_off(self):
        win, mp = self._make_main_page(
            values={"bools": {"remember-content-filter": False}}
        )
        mp.set_global_mode("movies")
        self.assertEqual(mp._global_mode, "movies")
        self.assertNotIn(("str", "content-filter-mode", "movies"), win.settings.writes)

    def test_invalid_mode_ignored(self):
        win, mp = self._make_main_page()
        mp.set_global_mode("bogus")
        self.assertEqual(mp._global_mode, "all")

    def _assert_single_active(self, mp, active_name):
        active = [
            name
            for name, btn in (
                ("all", mp.all_toggle),
                ("movies", mp.movie_toggle),
                ("shows", mp.show_toggle),
            )
            if btn.get_active()
        ]
        self.assertEqual(active, [active_name])
        self.assertEqual(mp._global_mode, active_name)

    def test_clicking_each_toggle_keeps_single_active(self):
        # GTK emits toggled for the deactivated sibling first, leaving a
        # window where no button is active; the handler must ignore that and
        # never light up a second toggle.
        win, mp = self._make_main_page()
        mp.movie_toggle.set_active(True)
        self._assert_single_active(mp, "movies")
        mp.show_toggle.set_active(True)
        self._assert_single_active(mp, "shows")
        mp.all_toggle.set_active(True)
        self._assert_single_active(mp, "all")
        mp.movie_toggle.set_active(True)
        self._assert_single_active(mp, "movies")


class SearchSyncTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            Gtk.init()
            Adw.init()
        except TypeError:
            pass

    def _make_page(self, main_page=None):
        return SearchPage(_Settings(_cfg.APP_ID), FakeUserRepo(), FakeMetadata(), main_page)

    def test_set_mode_syncs_toggle_buttons(self):
        page = self._make_page()
        page._set_mode("movies")
        self.assertTrue(page.movie_toggle.get_active())
        self.assertFalse(page.all_toggle.get_active())
        self.assertFalse(page.show_toggle.get_active())
        page._set_mode("shows")
        self.assertTrue(page.show_toggle.get_active())
        self.assertFalse(page.movie_toggle.get_active())
        page._set_mode("all")
        self.assertTrue(page.all_toggle.get_active())

    def test_filter_toggle_routes_to_main_page(self):
        class FakeMain:
            def __init__(self):
                self.calls = []

            def set_global_mode(self, mode):
                self.calls.append(mode)

        fake = FakeMain()
        page = self._make_page(main_page=fake)
        page.movie_toggle.set_active(True)
        self.assertEqual(fake.calls, ["movies"])

    def test_filter_toggle_falls_backto_local_mode(self):
        page = self._make_page(main_page=None)
        page.movie_toggle.set_active(True)
        self.assertEqual(page._mode, "movies")


class PreferencesFilterTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            Gtk.init()
            Adw.init()
        except TypeError:
            pass

    def test_remember_filter_switch_is_bound(self):
        from src.ui.preferences_page import PreferencesPage

        settings = _Settings(_cfg.APP_ID)
        win = types.SimpleNamespace(settings=settings)
        PreferencesPage(win, main_page=None)
        self.assertIn(("bind", "remember-content-filter"), settings.writes)