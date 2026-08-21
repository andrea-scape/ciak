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


class ProfileSagasTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            Gtk.init()
            Adw.init()
        except TypeError:
            pass

    def test_hidden_when_no_collections_watched(self):
        page = make_profile()
        page._populate_sagas([])
        self.assertFalse(page.sagas_section.get_visible())

    def test_groups_watched_movies_into_one_card(self):
        page = make_profile()
        watched = [
            {"tmdb_id": 1, "collection_id": 1241, "collection_name": "HP", "poster_url": None},
            {"tmdb_id": 2, "collection_id": 1241, "collection_name": "HP", "poster_url": None},
            {"tmdb_id": 3, "collection_id": None, "poster_url": None},
        ]
        page._populate_sagas(watched)
        self.assertTrue(page.sagas_section.get_visible())
        boxes = page.sagas_flowbox.observe_children()
        self.assertEqual(len(boxes), 1)

        def has_text(w, text):
            return isinstance(w, Gtk.Label) and w.get_text() == text

        self.assertIsNotNone(_find(page, "HP"))
        self.assertIsNotNone(_find(page, "2 watched"))

    def test_main_page_gets_collection_on_click(self):
        recorded = []

        class FakeMainPage:
            def show_collection(self, cid, name):
                recorded.append((cid, name))

        with mock.patch("gi.repository.GLib.Thread.new"):
            page = ProfileGallery(object(), mock.Mock(), object(), FakeMainPage())
        watched = [
            {"tmdb_id": 1, "collection_id": 1241, "collection_name": "HP", "poster_url": None},
        ]
        page._populate_sagas(watched)
        child = page.sagas_flowbox.observe_children()[0]
        child.get_child().emit("clicked")
        self.assertEqual(recorded, [(1241, "HP")])


def _find(page, text):
    def matches(w):
        return isinstance(w, Gtk.Label) and w.get_text() == text

    def walk(w):
        if matches(w):
            return w
        child = w.get_first_child()
        while child:
            found = walk(child)
            if found is not None:
                return found
            child = child.get_next_sibling()
        return None

    return walk(page.sagas_section)


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