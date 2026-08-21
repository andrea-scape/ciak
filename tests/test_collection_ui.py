"""Collections UI: detail chip, watched badge, collection page, navigation."""

import sys
import types
import unittest
from unittest import mock

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gio", "2.0")
gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gtk, Adw, Gio

if "src.config" not in sys.modules:
    _cfg = types.ModuleType("src.config")
    _cfg.APP_ID = "io.github.andrea_scape.ciak.Devel"
    _cfg.APP_VERSION = "0.0.0-test"
    _cfg.APP_NAME = "Ciak"
    sys.modules["src.config"] = _cfg
else:
    _cfg = sys.modules["src.config"]

from src.domain.models import Collection, Movie
from src.window import MainWindow
from src.ui.main_page import MainPage
from src.ui.detail_page import DetailPage
from src.ui.media_card import make_media_card
from src.ui.collection_page import CollectionPage


class _Settings:
    def __init__(self, app_id, values=None):
        self._app_id = app_id
        self._values = dict(values or {})
        self._bools = {
            "window-maximized": False,
            "disable-animations": False,
            "show-sidebar": True,
            "remember-content-filter": False,
        }
        self._bools.update(self._values.get("bools", {}))
        self._strings = {"default-page": "watchlist", "content-filter-mode": "all"}
        self._strings.update(self._values.get("strings", {}))
        self._ints = {"window-width": 1200, "window-height": 800}

    def get_boolean(self, key):
        return self._bools.get(key, False)

    def get_string(self, key):
        return self._strings.get(key, "")

    def get_int(self, key):
        return self._ints.get(key, 0)

    def set_boolean(self, key, value):
        self._bools[key] = value

    def set_string(self, key, value):
        self._strings[key] = value

    def set_int(self, key, value):
        self._ints[key] = value

    def bind(self, key, widget, prop, flags):
        pass

    def connect(self, key, callback):
        return 0


class FakeUserRepo:
    def __init__(self, watched=None):
        self._watched = set(watched or {})

    def get_watchlist_ids(self):
        return set()

    def get_watched_ids(self, media_type):
        return set(self._watched)

    def get_ratings(self, media_type):
        return []

    def get_watched_episodes_for_show(self, tmdb_id):
        return set()

    def get_watched_show_ids(self):
        return set()

    def get_watchlist(self, mode=None):
        return []

    def get_media_item(self, tmdb_id):
        return None


class FakeMetadata:
    def __init__(self, collection=None):
        self._collection = collection

    def get_movie(self, tmdb_id):
        return Movie(tmdb_id=tmdb_id, title="Test", runtime=100)

    def get_related_movies(self, tmdb_id):
        return []

    def get_movie_cast(self, tmdb_id):
        return []

    def get_show(self, tmdb_id):
        return None

    def get_show_seasons(self, tmdb_id):
        return []

    def get_related_shows(self, tmdb_id):
        return []

    def get_show_cast(self, tmdb_id):
        return []

    def get_collection(self, collection_id):
        return self._collection


def _recursive_find(widget, predicate):
    if predicate(widget):
        return widget
    child = widget.get_first_child()
    while child:
        result = _recursive_find(child, predicate)
        if result is not None:
            return result
        child = child.get_next_sibling()
    return None


def _watched_badge(button):
    def is_badge(w):
        return (
            isinstance(w, Gtk.Box)
            or isinstance(w, Gtk.Image)
        ) and "watched-badge" in w.get_css_classes()
    return _recursive_find(button, is_badge)


class CollectionUITest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            Gtk.init()
            Adw.init()
        except TypeError:
            pass
        cls._orig_settings = Gio.Settings.new
        Gio.Settings.new = _Settings

    @classmethod
    def tearDownClass(cls):
        Gio.Settings.new = cls._orig_settings

    # ---- detail page chip ----

    def _detail_with_chip(self, collection_id, collection_name):
        class FakeMP:
            def __init__(self):
                self.calls = []

            def show_collection(self, cid, name):
                self.calls.append((cid, name))

        item = Movie(tmdb_id=42, title="Test Movie")
        fake_mp = FakeMP()
        page = DetailPage(object(), object(), object(), "movie", item, fake_mp)
        movie = Movie(
            tmdb_id=42, title="Test Movie",
            collection_id=collection_id, collection_name=collection_name,
            genres=["Action"],
        )
        page.populate_hero(
            {"detail": movie, "watchlist_ids": set(), "watched_ids": set(), "rating": 0},
            None,
        )
        return page, fake_mp

    def _find_chip(self, page):
        def is_collection_chip(w):
            return (
                isinstance(w, Gtk.Button)
                and "collection-chip" in w.get_css_classes()
            )
        chip = _recursive_find(page.collection_box, is_collection_chip)
        if chip is None:
            return None
        label = _recursive_find(
            chip, lambda w: isinstance(w, Gtk.Label) and w.get_text().startswith("Part of:")
        )
        return label

    def test_chip_shown_when_collection_id_set(self):
        page, _ = self._detail_with_chip(1241, "Harry Potter")
        chip = self._find_chip(page)
        self.assertIsNotNone(chip)
        self.assertEqual(chip.get_text(), "Part of: Harry Potter")

    def test_chip_halign_start(self):
        page, _ = self._detail_with_chip(1241, "Harry Potter")
        chip = _recursive_find(
            page.collection_box,
            lambda w: isinstance(w, Gtk.Button) and "collection-chip" in w.get_css_classes(),
        )
        self.assertIsNotNone(chip)
        self.assertEqual(chip.get_halign(), Gtk.Align.START)

    def test_no_chip_without_collection(self):
        page, _ = self._detail_with_chip(None, None)
        self.assertIsNone(self._find_chip(page))

    def test_chip_click_calls_show_collection(self):
        page, fake_mp = self._detail_with_chip(1241, "Harry Potter")
        chip = _recursive_find(
            page.collection_box,
            lambda w: isinstance(w, Gtk.Button) and "collection-chip" in w.get_css_classes(),
        )
        chip.emit("clicked")
        self.assertEqual(fake_mp.calls, [(1241, "Harry Potter")])

    def test_stale_collection_name_fetched_in_prefetch_hero(self):
        class NamedFake(FakeMetadata):
            def __init__(self):
                super().__init__(
                    Collection(collection_id=1241, name="Harry Potter", parts=[])
                )

            def get_movie(self, tmdb_id):
                return Movie(
                    tmdb_id=tmdb_id, title="X", runtime=100, collection_id=1241,
                )

        metadata = NamedFake()
        window = MainWindow(user_repo=FakeUserRepo(), metadata_service=metadata)
        window.settings = _Settings(_cfg.APP_ID)
        mp = MainPage(window, FakeUserRepo(), metadata)
        window.set_page(mp)
        item = Movie(tmdb_id=42, title="X")
        detail_page = DetailPage(window, FakeUserRepo(), metadata, "movie", item, mp)
        mp._prefetch_hero("movie", item, detail_page)
        self.assertEqual(detail_page._detail.collection_name, "Harry Potter")

    # ---- watched badge on cards ----

    def test_watched_badge_added_when_watched(self):
        movie = Movie(tmdb_id=1, title="One")
        button = make_media_card(movie, watched=True)
        self.assertIsNotNone(_watched_badge(button))

    def test_no_watched_badge_by_default(self):
        movie = Movie(tmdb_id=1, title="One")
        button = make_media_card(movie)
        self.assertIsNone(_watched_badge(button))

    # ---- collection page grid ----

    def _make_collection_page(self, collection):
        metadata = FakeMetadata(collection=collection)
        window = MainWindow(user_repo=FakeUserRepo(), metadata_service=metadata)
        window.settings = _Settings(_cfg.APP_ID)
        main_page = MainPage(window, FakeUserRepo(), metadata)
        window.set_page(main_page)
        with mock.patch.object(CollectionPage, "_fetch", lambda self: None):
            page = CollectionPage(
                window, FakeUserRepo(watched={1}), metadata, main_page,
                collection_id=1241, name="HP",
            )
        return page

    def test_collection_page_populates_grid_and_stats(self):
        collection = Collection(
            collection_id=1241, name="Harry Potter", overview="Wizards.",
            parts=[
                Movie(tmdb_id=1, title="One", year=2001),
                Movie(tmdb_id=2, title="Two", year=2002),
                Movie(tmdb_id=3, title="Three", year=2003),
            ],
        )
        page = self._make_collection_page(collection)
        self.assertEqual(page.title_label.get_text(), "HP")
        self.assertFalse(page.overview_label.get_visible())
        page._populate(page._render_gen, collection)
        self.assertEqual(page.title_label.get_text(), "Harry Potter")
        self.assertTrue(page.overview_label.get_visible())
        self.assertEqual(page.stats_label.get_text(), "1 of 3 watched · 2 remaining")
        # 3 grid children
        count = 0
        child = page.grid.get_first_child()
        while child:
            count += 1
            child = child.get_next_sibling()
        self.assertEqual(count, 3)

    def test_collection_page_error_state(self):
        collection = Collection(collection_id=1241, name="HP", parts=[])
        page = self._make_collection_page(collection)
        page._populate(page._render_gen, collection)
        self.assertIsNotNone(page._error_label)

    def test_collection_page_progress_fraction(self):
        collection = Collection(
            collection_id=1241, name="HP",
            parts=[
                Movie(tmdb_id=1, title="One", year=2001),
                Movie(tmdb_id=2, title="Two", year=2002),
                Movie(tmdb_id=3, title="Three", year=2003),
            ],
        )
        page = self._make_collection_page(collection)
        page._populate(page._render_gen, collection)
        self.assertAlmostEqual(page.progress.get_fraction(), 1 / 3)

    def test_collection_page_marks_next_unwatched(self):
        collection = Collection(
            collection_id=1241, name="HP",
            parts=[
                Movie(tmdb_id=1, title="One", year=2001),
                Movie(tmdb_id=2, title="Two", year=2002),
                Movie(tmdb_id=3, title="Three", year=2003),
            ],
        )
        page = self._make_collection_page(collection)
        page._populate(page._render_gen, collection)
        tag = _recursive_find(
            page.grid,
            lambda w: isinstance(w, Gtk.Label) and w.get_text() == "Up next",
        )
        self.assertIsNotNone(tag)
        button = tag
        while button is not None and not isinstance(button, Gtk.Button):
            button = button.get_parent()
        self.assertIsNotNone(button)
        self.assertIsNotNone(_recursive_find(button, lambda w: isinstance(w, Gtk.Label) and w.get_text() == "Two"))

    def test_refresh_bumps_generation_and_refetches(self):
        collection = Collection(
            collection_id=1241, name="HP",
            parts=[Movie(tmdb_id=1, title="One", year=2001)],
        )
        page = self._make_collection_page(collection)
        gen_before = page._render_gen
        with mock.patch.object(page, "_fetch") as fetch:
            page.refresh()
        self.assertEqual(page._render_gen, gen_before + 1)
        fetch.assert_called_once()

    def test_repopulate_after_watched_change_updates_badge_and_progress(self):
        collection = Collection(
            collection_id=1241, name="HP",
            parts=[
                Movie(tmdb_id=1, title="One", year=2001),
                Movie(tmdb_id=2, title="Two", year=2002),
            ],
        )
        page = self._make_collection_page(collection)
        page.user_repo._watched.add(2)
        page._populate(page._render_gen, collection)
        self.assertAlmostEqual(page.progress.get_fraction(), 1.0)

        badges = 0
        child = page.grid.get_first_child()
        while child:
            card = child
            while card is not None and not isinstance(card, Gtk.Button):
                card = card.get_child()
            if card is not None and _watched_badge(card) is not None:
                badges += 1
            child = child.get_next_sibling()
        self.assertEqual(badges, 2)

    # ---- main page navigation ----

    def _make_main_page(self):
        metadata = FakeMetadata(collection=None)
        window = MainWindow(user_repo=FakeUserRepo(), metadata_service=metadata)
        window.settings = _Settings(_cfg.APP_ID)
        main_page = MainPage(window, FakeUserRepo(), metadata)
        window.set_page(main_page)
        return window, main_page

    def test_show_collection_pushes_collection_page(self):
        _, mp = self._make_main_page()
        mp.show_collection(1241, "HP")
        self.assertEqual(mp.content_stack.get_visible_child_name(), "collection")
        self.assertEqual(mp._current_page, "collection")

    def test_go_back_from_collection_returns_to_detail(self):
        _, mp = self._make_main_page()
        item = Movie(tmdb_id=42, title="Test Movie")
        mp.show_detail("movie", item)
        detail_slot = mp._current_detail_name
        self.assertEqual(mp.content_stack.get_visible_child_name(), detail_slot)
        mp.show_collection(1241, "HP")
        self.assertEqual(mp.content_stack.get_visible_child_name(), "collection")
        mp.go_back()
        self.assertEqual(mp.content_stack.get_visible_child_name(), detail_slot)
        self.assertEqual(mp._current_page, "detail")

    def test_go_back_from_collection_without_detail_returns_main(self):
        _, mp = self._make_main_page()
        mp.show_collection(1241, "HP")
        mp.go_back()
        self.assertEqual(mp._current_page, "watchlist")

    def test_go_back_to_collection_from_detail(self):
        _, mp = self._make_main_page()
        mp.show_collection(1241, "HP")
        item = Movie(tmdb_id=42, title="Test Movie")
        mp.show_detail("movie", item)
        self.assertEqual(mp._current_page, "detail")
        self.assertEqual(mp._previous_main_page, "collection")
        mp.go_back()
        self.assertEqual(mp.content_stack.get_visible_child_name(), "collection")
        self.assertEqual(mp._current_page, "collection")

    def test_go_back_collection_pruned_detail_fallback(self):
        _, mp = self._make_main_page()
        item = Movie(tmdb_id=42, title="Test Movie")
        mp.show_detail("movie", item)
        detail_slot = mp._current_detail_name
        mp.show_collection(1241, "HP")
        # Manually remove the originating detail child from the stack to simulate pruning
        child = mp.content_stack.get_child_by_name(detail_slot)
        if child:
            mp.content_stack.remove(child)
        mp.go_back()
        # Should fall back safely without crashing
        self.assertNotEqual(mp.content_stack.get_visible_child_name(), detail_slot)


if __name__ == "__main__":
    unittest.main()