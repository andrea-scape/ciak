"""Diary view pure helpers: star conversion and header formatting."""

import datetime

import sys
import types
import unittest

if "src.config" not in sys.modules:
    _cfg = types.ModuleType("src.config")
    _cfg.APP_ID = "io.github.andrea_scape.ciak.Devel"
    _cfg.APP_VERSION = "0.0.0-test"
    _cfg.APP_NAME = "Ciak"
    sys.modules["src.config"] = _cfg

from src.ui.diary_page import (
    format_day_header,
    format_minutes,
    star_glyphs,
    stars_for_rating,
)


class StarsForRatingTest(unittest.TestCase):
    def test_local_scale_maps_directly(self):
        # Ratings are stored locally on a 1-5 scale.
        self.assertEqual(stars_for_rating(5), 5)
        self.assertEqual(stars_for_rating(4), 4)
        self.assertEqual(stars_for_rating(3), 3)

    def test_fractional_rounds_half_up(self):
        self.assertEqual(stars_for_rating(4.5), 5)
        self.assertEqual(stars_for_rating(2.4), 2)

    def test_clamped_to_valid_range(self):
        self.assertEqual(stars_for_rating(0), 1)
        self.assertEqual(stars_for_rating(99), 5)

    def test_none_passes_through(self):
        self.assertIsNone(stars_for_rating(None))


class StarGlyphsTest(unittest.TestCase):
    def test_filled_and_empty(self):
        self.assertEqual(star_glyphs(4), "\u2605\u2605\u2605\u2605\u2606")
        self.assertEqual(star_glyphs(5), "\u2605" * 5)
        self.assertEqual(star_glyphs(1), "\u2605" + "\u2606" * 4)


class FormatMinutesTest(unittest.TestCase):
    def test_hours_and_minutes(self):
        self.assertEqual(format_minutes(125), "2h 05m")
        self.assertEqual(format_minutes(60), "1h")
        self.assertEqual(format_minutes(59), "59m")
        self.assertEqual(format_minutes(0), "0m")


class FormatDayHeaderTest(unittest.TestCase):
    def test_full_weekday_month(self):
        self.assertEqual(
            format_day_header("2026-08-15"), "Saturday, 15 August 2026"
        )
        self.assertEqual(
            format_day_header("2026-01-01"), "Thursday, 1 January 2026"
        )

    def test_full_weekday_month_american(self):
        self.assertEqual(
            format_day_header("2026-08-15", american=True),
            "Saturday, August 15, 2026",
        )
        self.assertEqual(
            format_day_header("2026-01-01", american=True),
            "Thursday, January 1, 2026",
        )

    def test_today_and_yesterday(self):
        import datetime
        today = datetime.date.today()
        self.assertEqual(
            format_day_header(today.isoformat()), "Today"
        )
        yesterday = today - datetime.timedelta(days=1)
        self.assertEqual(
            format_day_header(yesterday.isoformat()), "Yesterday"
        )


if __name__ == "__main__":
    unittest.main()


class RelativeDayHeaderTest(unittest.TestCase):
    def test_within_week_shows_weekday_only(self):
        self.assertEqual(
            format_day_header("2026-08-19", ref=datetime.date(2026, 8, 23)),
            "Wednesday",
        )

    def test_older_shows_full_date(self):
        self.assertEqual(
            format_day_header("2026-08-15", ref=datetime.date(2026, 8, 23)),
            "Saturday, 15 August 2026",
        )

    def test_boundary_six_days_is_weekday(self):
        self.assertEqual(
            format_day_header("2026-08-17", ref=datetime.date(2026, 8, 23)),
            "Monday",
        )
        self.assertEqual(
            format_day_header("2026-08-16", ref=datetime.date(2026, 8, 23)),
            "Sunday, 16 August 2026",
        )


class MonthSummariesTest(unittest.TestCase):
    def test_flags_newest_day_of_each_month(self):
        from src.ui.diary_page import build_month_summaries
        days = [
            {"day": "2026-08-23", "item_count": 2, "minutes": 120},
            {"day": "2026-08-14", "item_count": 3, "minutes": 200},
            {"day": "2026-07-30", "item_count": 1, "minutes": 45},
        ]
        out = build_month_summaries(days)
        self.assertEqual(set(out), {"2026-08", "2026-07"})
        self.assertEqual(out["2026-08"], ("August 2026", 5, 320))
        self.assertEqual(out["2026-07"], ("July 2026", 1, 45))

    def test_empty(self):
        from src.ui.diary_page import build_month_summaries
        self.assertEqual(build_month_summaries([]), {})


class NoteSnippetTest(unittest.TestCase):
    def test_truncates_with_ellipsis(self):
        from src.ui.diary_page import note_snippet
        long = "x" * 60
        self.assertEqual(len(note_snippet(long)), 41)
        self.assertTrue(note_snippet(long).endswith("\u2026"))

    def test_short_passes_through(self):
        from src.ui.diary_page import note_snippet
        self.assertEqual(note_snippet("hi"), "hi")
        self.assertEqual(note_snippet(None), None)
