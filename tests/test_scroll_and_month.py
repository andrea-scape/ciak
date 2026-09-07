import sys
import types
import unittest

if "src.config" not in sys.modules:
    _cfg = types.ModuleType("src.config")
    _cfg.APP_ID = "io.github.andrea_scape.ciak.Devel"
    _cfg.APP_VERSION = "0.0.0-test"
    _cfg.APP_NAME = "Ciak"
    sys.modules["src.config"] = _cfg

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


if __name__ == "__main__":
    unittest.main()
