import tests.testsupport  # noqa: F401 (injects the src.config stub)
import os
import types
import unittest


import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw

from src.ui.diary_page import (
    apply_stars_to_label, MonthDivider, DiaryPage,
)


def _css():
    path = os.path.join(os.path.dirname(__file__), "..", "src", "style.css")
    with open(path) as f:
        return f.read()


class DiaryStarsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            Gtk.init()
            Adw.init()
        except TypeError:
            pass

    def test_stars_label_carries_diary_stars_class(self):
        label = Gtk.Label()
        apply_stars_to_label(label, 3)
        self.assertIn("diary-stars", label.get_css_classes())
        self.assertIn("\u2605", label.get_text())

    def test_unrated_entry_shows_empty_stars(self):
        label = Gtk.Label()
        apply_stars_to_label(label, None)
        self.assertEqual(label.get_text(), "\u2606" * 5)
        self.assertTrue(label.get_visible())


class MonthDividerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            Gtk.init()
            Adw.init()
        except TypeError:
            pass

    def test_name_label_uses_month_label_class(self):
        div = MonthDivider("September 2026", 12, 120, month_key="2026-09")
        name = div.get_first_child()
        self.assertIn("diary-month-label", name.get_css_classes())
        self.assertEqual(name.get_text(), "September 2026")

    def test_divider_has_hard_height(self):
        div = MonthDivider("September 2026", 12, 120, month_key="2026-09")
        self.assertEqual(div.get_size_request(), (-1, 44))


class DiaryCssTest(unittest.TestCase):
    def test_month_label_has_no_letter_spacing(self):
        block = _css().split(".diary-month-label", 1)[1].split("}", 1)[0]
        self.assertNotIn("letter-spacing", block)
        self.assertNotIn("padding-top", block)
        self.assertIn("min-height: 24px", block)
        self.assertIn("line-height: 1.5", block)

    def test_diary_stars_use_accent_bg(self):
        block = _css().split(".diary-stars", 1)[1].split("}", 1)[0]
        self.assertIn("var(--accent-bg-color)", block)


class DiaryGenreFilterTest(unittest.TestCase):
    def _page(self, selected):
        page = super(DiaryPage, DiaryPage).__new__(DiaryPage)
        page._filter = "all"
        page._query = ""
        page.genre_chips = types.SimpleNamespace(selected=set(selected))
        return page

    def test_genre_gate_filters_entries(self):
        page = self._page(["Drama"])
        self.assertTrue(page._entry_matches({
            "media_type": "movie", "title": "A",
            "genres": '["Drama","Thriller"]'}))
        self.assertFalse(page._entry_matches({
            "media_type": "movie", "title": "A",
            "genres": '["Comedy"]'}))
        self.assertFalse(page._entry_matches({
            "media_type": "movie", "title": "A", "genres": None}))

    def test_all_selected_genres_must_match(self):
        page = self._page(["Drama", "Thriller"])
        self.assertTrue(page._entry_matches({
            "media_type": "show", "title": "B",
            "genres": '["Thriller","Drama"]'}))
        self.assertFalse(page._entry_matches({
            "media_type": "show", "title": "B",
            "genres": '["Drama"]'}))

    def test_empty_selection_matches_everything(self):
        page = self._page([])
        self.assertTrue(page._entry_matches({
            "media_type": "movie", "title": "C",
            "genres": '["Comedy"]'}))


if __name__ == "__main__":
    unittest.main()