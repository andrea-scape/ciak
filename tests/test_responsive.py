import sys
import types
import unittest

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gtk, Adw, Gio, Gdk, GdkPixbuf

# config.py is generated at build time; provide the bits used at import time.
_cfg = types.ModuleType("src.config")
_cfg.APP_ID = "io.github.andrea_scape.ciak.Devel"
sys.modules["src.config"] = _cfg

from src.domain.models import Movie
from src.window import MainWindow
from src.ui.main_page import MainPage


class _Settings:
    def __init__(self, app_id):
        self._app_id = app_id

    def get_int(self, key):
        return {"window-width": 1200, "window-height": 800}.get(key, 0)

    def get_boolean(self, key):
        return {
            "window-maximized": False,
            "disable-animations": False,
            "clear-cache-on-exit": False,
            "show-sidebar": True,
        }.get(key, False)

    def get_string(self, key):
        return {"default-page": "watchlist", "sidebar-default-mode": "remember"}.get(key, "")

    def set_int(self, key, value):
        pass

    def set_boolean(self, key, value):
        pass

    def set_string(self, key, value):
        pass

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

    def get_collection(self, collection_id):
        return None


def _make_window():
    return MainWindow(user_repo=FakeUserRepo(), metadata_service=FakeMetadata())


def _make_main_page():
    win = _make_window()
    mp = MainPage(win, FakeUserRepo(), FakeMetadata())
    win.set_page(mp)
    return win, mp


class ResponsiveWindowTest(unittest.TestCase):
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

    def test_window_actions_registered_once(self):
        # _setup_window_actions used to run twice, registering every GAction
        # twice and making GActionMap log a critical warning.  Each action
        # name must appear in the window exactly once.
        from unittest import mock

        win = _make_window()
        seen = []
        orig_add = win.add_action

        def spy(action):
            seen.append(action.get_name())
            orig_add(action)

        win.add_action = spy
        MainPage(win, FakeUserRepo(), FakeMetadata())
        dupes = {name for name in seen if seen.count(name) > 1}
        self.assertEqual(dupes, set())

    def test_window_minimum_is_750x750(self):
        # The floor is 750 wide and 750 tall.
        win = _make_window()
        self.assertEqual(win.get_size_request(), (750, 750))

    def test_hamburger_appears_after_overlay_auto_hide(self):
        # When the overlay sidebar hides itself (e.g. click-outside), the
        # show-sidebar button must reappear so it is always togglable.
        _win, mp = _make_main_page()
        mp._split_view.set_collapsed(True)
        mp._split_view.set_show_sidebar(True)
        mp._split_view.set_show_sidebar(False)
        self.assertTrue(mp._show_sidebar_btn.get_visible())

    def test_hamburger_hidden_while_sidebar_shown(self):
        _win, mp = _make_main_page()
        mp._split_view.set_collapsed(True)
        mp._split_view.set_show_sidebar(True)
        self.assertFalse(mp._show_sidebar_btn.get_visible())

    def test_single_click_opens_sidebar_after_narrow_hide(self):
        # The overlay sidebar hides itself when the window goes narrow; the
        # show-sidebar action state must stay in sync so a single activation
        # opens it (previously it took two clicks).
        _win, mp = _make_main_page()
        mp._on_sidebar_narrow()
        self.assertFalse(mp._sidebar_action.get_state().get_boolean())
        mp._sidebar_action.activate(None)
        self.assertTrue(mp._split_view.get_show_sidebar())

    def test_poster_stacks_on_top(self):
        # At small widths the hero poster moves above the info column.
        from src.ui.detail_page import DetailPage
        page = DetailPage(object(), FakeUserRepo(), FakeMetadata(), "movie", Movie(tmdb_id=1, title="T"))
        page._apply_poster_stacked()
        self.assertEqual(page.top_box.get_orientation(), Gtk.Orientation.VERTICAL)
        page._apply_poster_beside()
        self.assertEqual(page.top_box.get_orientation(), Gtk.Orientation.HORIZONTAL)

    def test_poster_keeps_aspect_ratio_when_stacked(self):
        # A large source image must not stretch the poster: it stays 320 wide
        # (2:3) even when the hero is stacked.
        from src.ui.detail_page import DetailPage
        page = DetailPage(object(), FakeUserRepo(), FakeMetadata(), "movie", Movie(tmdb_id=1, title="T"))
        pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, 500, 750)
        texture = Gdk.Texture.new_for_pixbuf(pixbuf)
        page.populate_hero(
            {
                "detail": Movie(tmdb_id=1, title="T", runtime=100),
                "watchlist_ids": set(),
                "watched_ids": set(),
                "rating": 0,
            },
            poster_texture=texture,
        )
        page._apply_poster_stacked()
        width = page.poster_box.measure(Gtk.Orientation.HORIZONTAL, -1)[1]
        self.assertEqual(width, 320)


if __name__ == "__main__":
    unittest.main()
