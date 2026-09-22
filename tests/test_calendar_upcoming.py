"""Calendar 'Upcoming' control: a plain button that builds a fresh popover
per click like the day-cell popovers, listing every release of the shown
month inside a height-capped scroller."""
import tests.testsupport  # noqa: F401 (injects the src.config stub)

import datetime
import unittest
from types import SimpleNamespace
from unittest import mock


import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

from src.ui.calendar_page import CalendarPage

MONTH = {"year": 2026, "month": 9}


class _Repo:
    def get_watchlist(self, media_type):
        return []

    def get_watchlist_with_dates(self, media_type):
        return []


class _Win:
    settings = SimpleNamespace(
        get_boolean=lambda k: False, get_string=lambda k: "")


def _new_page():
    with mock.patch("threading.Thread"):
        page = CalendarPage(_Win(), _Repo(), object(), None)
    page._year = MONTH["year"]
    page._month = MONTH["month"]
    return page


def _kids(widget):
    kids = []
    child = widget.get_first_child()
    while child is not None:
        kids.append(child)
        child = child.get_next_sibling()
    return kids


def _popover_box(pop):
    scroller = pop.get_child()
    viewport = scroller.get_child()
    return viewport.get_child()


class CalendarUpcomingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not Gtk.is_initialized():
            Gtk.init()

    def test_upcoming_is_a_button(self):
        page = _new_page()
        self.assertIsInstance(page._upcoming_btn, Gtk.Button)
        self.assertIn("cal-upcoming-btn",
                      page._upcoming_btn.get_css_classes())

    def test_builder_returns_popover_parented_to_button(self):
        page = _new_page()
        page._airings = {"2026-09-20": [({"title": "Show"}, None)]}
        pop = page._build_month_popover(
            page._upcoming_btn, ref=datetime.date(2026, 9, 12))
        self.assertIsInstance(pop, Gtk.Popover)
        self.assertIs(pop.get_parent(), page._upcoming_btn)
        self.assertIn("cal-day-popover", pop.get_css_classes())
        scroller = pop.get_child()
        self.assertIsInstance(scroller, Gtk.ScrolledWindow)
        self.assertEqual(scroller.get_max_content_height(), 320)
        box = _popover_box(pop)
        self.assertIsInstance(box, Gtk.Box)
        self.assertEqual(
            len([k for k in _kids(box) if isinstance(k, Gtk.Button)]), 1)

    def test_popover_shows_only_future_releases(self):
        page = _new_page()
        page._airings = {
            "2026-09-01": [({"title": "Past"}, None)],
            "2026-09-20": [({"title": "Future"}, None)],
        }
        pop = page._build_month_popover(
            page._upcoming_btn, ref=datetime.date(2026, 9, 12))
        box = _popover_box(pop)
        cards = [k for k in _kids(box) if isinstance(k, Gtk.Button)]
        self.assertEqual(len(cards), 1)

    def test_builder_shows_empty_state(self):
        page = _new_page()
        page._airings = {}
        pop = page._build_month_popover(
            page._upcoming_btn, ref=datetime.date(2026, 9, 12))
        box = _popover_box(pop)
        last = box.get_last_child()
        self.assertIsNotNone(last)
        self.assertIn("Nothing upcoming", last.get_text())

    def test_label_counts_releases_from_today_in_month(self):
        page = _new_page()
        page._airings = {
            "2026-09-01": [({"title": "Past"}, None)],
            "2026-09-30": [({"title": "Future"}, None)],
        }
        self.assertEqual(
            page._count_upcoming_in_month(datetime.date(2026, 9, 12)), 1)

    def test_max_height_scales_with_window(self):
        page = _new_page()
        self.assertEqual(page._popover_max_height(), 320)
        root = SimpleNamespace(get_height=lambda: 800)
        with mock.patch.object(page, "get_root", return_value=root):
            self.assertEqual(page._popover_max_height(), 520)
        small = SimpleNamespace(get_height=lambda: 300)
        with mock.patch.object(page, "get_root", return_value=small):
            self.assertEqual(page._popover_max_height(), 320)  # floored


if __name__ == "__main__":
    unittest.main()