"""Feature 4 UI: region detection + detail streaming section."""

import unittest
from unittest import mock

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gio", "2.0")
from gi.repository import Gtk, Adw, GLib

from src.domain.models import Movie, StreamingInfo, StreamingProvider
from src.ui.detail_page import DetailPage
from src.ui.region import (
    FALLBACK_REGION,
    detect_region_from_locale,
    streaming_region,
)


def make_page():
    item = Movie(tmdb_id=42, title="Test Movie")
    return DetailPage(object(), object(), object(), "movie", item)


def _info():
    return StreamingInfo(
        country_code="IT",
        flatrate=[StreamingProvider(8, "Netflix", "u/n.jpg", 0, "flatrate")],
        rent=[StreamingProvider(337, "Apple TV", "u/a.jpg", 1, "rent")],
        buy=[StreamingProvider(337, "Apple TV", "u/a.jpg", 1, "buy")],
        ads=[],
        free=[],
    )


class StreamingSectionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            Gtk.init()
            Adw.init()
        except TypeError:
            pass

    def test_section_hidden_when_info_none(self):
        page = make_page()
        page.populate_streaming(None)
        while GLib.MainContext.default().iteration(False):
            pass
        self.assertFalse(page.streaming_section.get_visible())

    def test_section_shows_groups_for_offerings(self):
        from src.ui.detail_page import Gtk as _gtk

        page = make_page()
        page.populate_streaming(_info())
        while GLib.MainContext.default().iteration(False):
            pass
        self.assertTrue(page.streaming_section.get_visible())
        inner = page.streaming_revealer.get_child()
        rows = [
            c for c in inner
            if isinstance(c, Gtk.Box) and c.get_margin_top() is not None
        ]
        label_texts = []
        for row in rows:
            labels = [
                c for c in row
                if isinstance(c, Gtk.Label) and "provider-group-label" in (
                    c.get_css_classes() or [])
            ]
            for label in labels:
                label_texts.append(label.get_text())
        self.assertEqual(sorted(label_texts), ["Rent / Buy", "Stream"])

    def test_section_uses_real_time_offerings(self):
        # "Stream" label maps to flatrate group.
        page = make_page()
        page.populate_streaming(_info())
        while GLib.MainContext.default().iteration(False):
            pass
        self.assertTrue(page.streaming_section.get_visible())

    def test_cancelled_hides_section(self):
        page = make_page()
        page._cancelled = True
        page.populate_streaming(_info())
        while GLib.MainContext.default().iteration(False):
            pass
        self.assertFalse(page.streaming_section.get_visible())


class RegionDetectionTest(unittest.TestCase):
    def test_fallback_when_no_locale(self):
        with mock.patch(
            "src.ui.region.GLib.get_language_names",
            return_value=[],
        ), mock.patch.dict(
            "os.environ", {"LANG": "", "LC_ALL": "", "LC_CTYPE": ""}, clear=True
        ), mock.patch(
            "src.ui.region.locale.getdefaultlocale",
            return_value=(None, None),
        ):
            self.assertEqual(detect_region_from_locale(), FALLBACK_REGION)

    def test_locale_with_country(self):
        with mock.patch.dict(
            "os.environ", {"LANG": "", "LC_ALL": "", "LC_CTYPE": ""}, clear=True
        ), mock.patch(
            "src.ui.region.locale.getdefaultlocale",
            return_value=("it_IT", "UTF-8"),
        ):
            self.assertEqual(detect_region_from_locale(), "IT")

    def test_glib_language_names_fallback(self):
        with mock.patch.dict(
            "os.environ", {"LANG": "", "LC_ALL": "", "LC_CTYPE": ""}, clear=True
        ), mock.patch(
            "src.ui.region.locale.getdefaultlocale",
            return_value=(None, None),
        ), mock.patch(
            "src.ui.region.GLib.get_language_names",
            return_value=["de_DE", "de"],
        ):
            self.assertEqual(detect_region_from_locale(), "DE")

    def test_preference_wins_over_locale(self):
        settings = mock.Mock()
        settings.get_string.return_value = "US"
        self.assertEqual(streaming_region(settings), "US")

    def test_auto_uses_locale(self):
        settings = mock.Mock()
        settings.get_string.return_value = "auto"
        with mock.patch.dict(
            "os.environ", {"LANG": "", "LC_ALL": "", "LC_CTYPE": ""}, clear=True
        ), mock.patch(
            "src.ui.region.locale.getdefaultlocale",
            return_value=("fr_FR", "UTF-8"),
        ):
            self.assertEqual(streaming_region(settings), "FR")

    def test_none_settings_uses_locale(self):
        with mock.patch.dict(
            "os.environ", {"LANG": "", "LC_ALL": "", "LC_CTYPE": ""}, clear=True
        ), mock.patch(
            "src.ui.region.locale.getdefaultlocale",
            return_value=("it_IT", "UTF-8"),
        ):
            self.assertEqual(streaming_region(None), "IT")


if __name__ == "__main__":
    unittest.main()