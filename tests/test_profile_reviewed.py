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

    def test_sagas_grid_matches_reviewed_width_config(self):
        page = make_profile()
        self.assertEqual(
            page.sagas_flowbox.get_max_children_per_line(),
            page.reviewed_flowbox.get_max_children_per_line(),
        )
        self.assertEqual(
            page.sagas_flowbox.get_min_children_per_line(),
            page.reviewed_flowbox.get_min_children_per_line(),
        )

    def test_sagas_section_padding(self):
        page = make_profile()
        self.assertEqual(page.sagas_section.get_margin_top(), 24)
        self.assertEqual(page.sagas_section.get_margin_bottom(), 36)

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


def _find_check_image(page):
    def matches(w):
        return isinstance(w, Gtk.Image) and w.has_css_class("saga-check")

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


class SagaTotalsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            Gtk.init()
            Adw.init()
        except TypeError:
            pass

    def _watched(self):
        return [
            {"tmdb_id": 1, "collection_id": 1241, "collection_name": "HP",
             "poster_url": "http://img/1.jpg"},
            {"tmdb_id": 2, "collection_id": 1241, "collection_name": "HP",
             "poster_url": "http://img/2.jpg"},
        ]

    def test_partial_collection_shows_count_without_check(self):
        page = make_profile()
        page._populate_sagas(self._watched())
        page._apply_saga_totals(page._reload_gen, {1241: 3})
        self.assertIsNotNone(_find(page, "2 of 3 watched"))
        tick = _find_check_image(page)
        self.assertIsNotNone(tick)
        self.assertFalse(tick.get_visible())

    def test_complete_collection_shows_count_and_check(self):
        page = make_profile()
        page._populate_sagas(self._watched())
        page._apply_saga_totals(page._reload_gen, {1241: 2})
        self.assertIsNotNone(_find(page, "2 of 2 watched"))
        tick = _find_check_image(page)
        self.assertIsNotNone(tick)
        self.assertTrue(tick.get_visible())

    def test_stale_totals_are_ignored(self):
        page = make_profile()
        page._populate_sagas(self._watched())
        stale_gen = page._reload_gen - 1
        page._apply_saga_totals(stale_gen, {1241: 5})
        self.assertIsNotNone(_find(page, "2 watched"))

    def test_fetch_failure_keeps_fallback_text(self):
        page = make_profile()
        page._populate_sagas(self._watched())
        calls = []

        class FailingService:
            def get_collection(self, cid):
                calls.append(cid)
                raise RuntimeError("offline")

        page.metadata_service = FailingService()
        page._fetch_saga_totals(page._reload_gen, [1241])
        self.assertEqual(calls, [1241])
        self.assertIsNotNone(_find(page, "2 watched"))

    def test_logo_uses_lowest_tmdb_id_regardless_of_watch_order(self):
        page = make_profile()
        watched = [
            {"tmdb_id": 7, "collection_id": 1241, "collection_name": "HP",
             "poster_url": "http://img/7.jpg"},
            {"tmdb_id": 3, "collection_id": 1241, "collection_name": "HP",
             "poster_url": "http://img/3.jpg"},
        ]
        with mock.patch("src.ui.profile_page.load_poster") as lp:
            page._populate_sagas(watched)
        loaded = [c.args[0] for c in lp.call_args_list]
        self.assertIn("http://img/3.jpg", loaded)
        self.assertNotIn("http://img/7.jpg", loaded)

    def test_logo_upgrades_to_collection_first_part(self):
        page = make_profile()
        page._populate_sagas(self._watched())
        with mock.patch("src.ui.profile_page.load_poster") as lp:
            page._apply_saga_totals(
                page._reload_gen, {1241: 5}, {1241: "http://img/first.jpg"}
            )
        loaded = [c.args for c in lp.call_args_list]
        self.assertIn(("http://img/first.jpg", mock.ANY), loaded)

    def test_saga_buttons_have_no_card_chrome(self):
        page = make_profile()
        page._populate_sagas(self._watched())
        child = page.sagas_flowbox.observe_children()[0]
        button = child.get_child()
        self.assertIsInstance(button, Gtk.Button)
        self.assertFalse(button.has_css_class("saga-card"))
        self.assertTrue(button.has_css_class("saga-button"))


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