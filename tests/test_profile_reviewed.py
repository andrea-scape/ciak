import unittest
from unittest import mock
from types import SimpleNamespace

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw

from src.ui.profile_page import ProfileGallery


def make_profile():
    with mock.patch("gi.repository.GLib.Thread.new"):
        page = ProfileGallery(object(), mock.Mock(), object(), object())
    return page


class ProfileReviewedTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            Gtk.init()
            Adw.init()
        except TypeError:
            pass

    def test_empty_ratings_hide_reviewed_section(self):
        page = make_profile()
        page._populate_reviewed([])
        self.assertFalse(page.reviewed_section.get_visible())

    def test_ratings_show_reviewed_section(self):
        page = make_profile()
        rated = [
            SimpleNamespace(
                tmdb_id=1, media_type="movie", title="X", year=2020,
                poster_url=None, rating=4,
            )
        ]
        page._populate_reviewed(rated)
        self.assertTrue(page.reviewed_section.get_visible())


class ProfileAvatarTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            Gtk.init()
            Adw.init()
        except TypeError:
            pass

    def test_avatar_shows_first_letter_of_username(self):
        with mock.patch("getpass.getuser", return_value="ascape"), \
                mock.patch("gi.repository.GLib.Thread.new"):
            page = ProfileGallery(object(), mock.Mock(), object(), object())
        self.assertIsInstance(page.profile_avatar, Gtk.DrawingArea)
        self.assertEqual(page.profile_avatar._initial_char, "A")
        self.assertTrue(page.profile_avatar.has_css_class("profile-initial"))


if __name__ == "__main__":
    unittest.main()