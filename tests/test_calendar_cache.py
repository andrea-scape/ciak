"""Calendar same-day session cache: a second _load on the same day must
render from cache instead of re-running the watchlist fan-out."""

import sys
import types
import unittest
import datetime
from types import SimpleNamespace
from unittest import mock

if "src.config" not in sys.modules:
    _cfg = types.ModuleType("src.config")
    _cfg.APP_ID = "io.github.andrea_scape.ciak.Devel"
    _cfg.APP_VERSION = "0.0.0-test"
    _cfg.APP_NAME = "Ciak"
    sys.modules["src.config"] = _cfg

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, GLib

from src.ui.calendar_page import CalendarPage


class _Repo:
    def get_watchlist(self, media_type):
        return []

    def get_watchlist_with_dates(self, media_type):
        return []


class _Win:
    settings = SimpleNamespace(
        get_boolean=lambda k: False, get_string=lambda k: "")


def _make_page():
    with mock.patch("gi.repository.GLib.Thread.new"), \
            mock.patch.object(CalendarPage, "_build_nav", lambda self: None):
        page = CalendarPage(_Win(), _Repo(), object(), None)
    return page


class CalendarCacheTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not Gtk.is_initialized():
            Gtk.init()

    def test_same_day_load_uses_cache_not_thread(self):
        page = _make_page()
        page._day_cache = {
            "day": datetime.date.today(),
            "airings": {},
            "upcoming": [],
            "shows_map": {},
        }
        idle_calls = []
        with mock.patch("gi.repository.GLib.Thread.new") as tn, \
                mock.patch("gi.repository.GLib.idle_add",
                           side_effect=lambda cb, *a:
                               idle_calls.append((cb, a)) or 7):
            page._load()
        tn.assert_not_called()  # no fan-out thread spawned
        self.assertEqual(idle_calls[0][0], page._render)

    def test_stale_cache_refetches(self):
        page = _make_page()
        page._day_cache = {
            "day": datetime.date.today() - datetime.timedelta(days=2),
            "airings": {}, "upcoming": [], "shows_map": {},
        }
        with mock.patch("threading.Thread") as th, \
                mock.patch("gi.repository.GLib.idle_add"):
            page._load()
        self.assertTrue(th.called)


if __name__ == "__main__":
    unittest.main()
