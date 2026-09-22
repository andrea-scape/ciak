import tests.testsupport  # noqa: F401 (injects the src.config stub)
import unittest
from types import SimpleNamespace
from unittest import mock

from gi.repository import GLib

from src.ui.scroll_restore import clamp_value
from src.ui.diary_page import _month_label


class ClampValueTest(unittest.TestCase):
    def test_within_range_unchanged(self):
        self.assertEqual(clamp_value(120.0, 1000.0, 200.0), 120.0)

    def test_negative_becomes_zero(self):
        self.assertEqual(clamp_value(-5.0, 1000.0, 200.0), 0.0)

    def test_clamped_to_upper_minus_page(self):
        self.assertEqual(clamp_value(5000.0, 1000.0, 200.0), 800.0)

    def test_empty_content_stays_zero(self):
        self.assertEqual(clamp_value(50.0, 200.0, 200.0), 0.0)

    def test_zero_upper_safe(self):
        self.assertEqual(clamp_value(10.0, 0.0, 0.0), 0.0)


class MonthLabelTest(unittest.TestCase):
    def test_normal_month(self):
        self.assertEqual(_month_label("2026-03"), "March 2026")

    def test_january_indexing(self):
        self.assertEqual(_month_label("2024-01"), "January 2024")

    def test_december(self):
        self.assertEqual(_month_label("2025-12"), "December 2025")

    def test_invalid_month_falls_back(self):
        self.assertEqual(_month_label("2026-99"), "2026-99")

    def test_garbage_falls_back(self):
        self.assertEqual(_month_label("junk"), "junk")

    def test_none_is_empty(self):
        self.assertEqual(_month_label(None), "")


class _SearchEntry:
    def __init__(self, text):
        self._text = text

    def get_text(self):
        return self._text


class DiaryFilterDebounceTest(unittest.TestCase):
    """search-changed fires per keystroke; the full diary rebuild must be
    deferred and re-armed so a burst of keys schedules one reload."""

    @staticmethod
    def _page():
        from src.ui.diary_page import DiaryPage

        p = SimpleNamespace(
            _query="", _user_nav=False, _filter_ready_id=None,
            _load=mock.Mock(),
        )
        p._schedule_filter_load = DiaryPage._schedule_filter_load.__get__(p)
        p._run_filter_load = DiaryPage._run_filter_load.__get__(p)
        return p

    def test_keystrokes_arm_one_debounced_reload(self):
        from src.ui.diary_page import DiaryPage

        page = self._page()
        with mock.patch.object(GLib, "timeout_add", return_value=99) as ta, \
                mock.patch.object(GLib, "source_remove") as sr:
            DiaryPage._on_search_changed(page, _SearchEntry("o"))
            DiaryPage._on_search_changed(page, _SearchEntry("op"))
            DiaryPage._on_search_changed(page, _SearchEntry("opp"))
        self.assertEqual(sr.call_count, 2)  # keys 2 and 3 cancel the prior arm
        sr.assert_called_with(99)
        self.assertEqual(ta.call_count, 3)  # every key re-arms; one survives
        self.assertEqual(ta.call_args.args[0], 250)
        page._load.assert_not_called()  # deferred, not inline

    def test_chip_toggle_shares_the_debounce(self):
        from src.ui.diary_page import DiaryPage

        page = self._page()
        with mock.patch.object(GLib, "timeout_add", return_value=99) as ta:
            DiaryPage._on_genres_changed(page, None)
        ta.assert_called_once()
        self.assertEqual(ta.call_args.args[0], 250)
        page._load.assert_not_called()

    def test_fired_timer_runs_the_reload(self):
        from src.ui.diary_page import DiaryPage

        page = self._page()
        page._filter_ready_id = 99
        ret = DiaryPage._run_filter_load(page)
        page._load.assert_called_once()
        self.assertEqual(page._filter_ready_id, None)
        self.assertEqual(ret, GLib.SOURCE_REMOVE)


if __name__ == "__main__":
    unittest.main()
