"""Genre chips: shared row widget, page integration, repo plumbing."""

import json
import os
import sys
import tempfile
import types
import unittest
from types import SimpleNamespace
from unittest import mock

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, Gio

if "src.config" not in sys.modules:
    _cfg = types.ModuleType("src.config")
    _cfg.APP_ID = "io.github.andrea_scape.ciak.Devel"
    _cfg.APP_VERSION = "0.0.0-test"
    _cfg.APP_NAME = "Ciak"
    sys.modules["src.config"] = _cfg
else:
    _cfg = sys.modules["src.config"]

from src.ui.genre_chips import (
    GenreChipsRow,
    item_genre_names,
    matches_all,
)
from src.ui.watchlist_page import WatchlistPage
from src.ui.history_page import HistoryPage
from src.ui.search_page import SearchPage
from src.data.local.repository import LocalMediaRepository


def setUpModule():
    # These tests assert end state (visibility, card contents), not motion.
    # With animations on, grid rebuilds defer behind a short fade.
    from src.ui import anim
    anim.set_animations_enabled(False)


def tearDownModule():
    from src.ui import anim
    anim.set_animations_enabled(True)


def _item(tmdb_id, title, genres=None, genre_ids=None):
    return SimpleNamespace(
        tmdb_id=tmdb_id, title=title, media_type="movie",
        year=2020, poster_url=None,
        genres=json.dumps(genres) if genres else None,
        genre_ids=list(genre_ids or []),
    )


def _toggles(row):
    out = []
    child = row.get_first_child()
    while child:
        out.append(child)
        child = child.get_next_sibling()
    return out


def _grid_count(grid):
    n = 0
    child = grid.get_first_child()
    while child:
        n += 1
        child = child.get_next_sibling()
    return n


def _has_watched_badge(card):
    def walk(w):
        if isinstance(w, Gtk.Box) and "watched-badge" in w.get_css_classes():
            return True
        c = w.get_first_child()
        while c:
            if walk(c):
                return True
            c = c.get_next_sibling()
        return False
    return walk(card)


def _first_grid_card(grid):
    child = grid.get_first_child()
    return child.get_child()


class GenreHelperTest(unittest.TestCase):
    def test_names_from_json_string(self):
        it = _item(1, "A", genres=["Action", "Drama"])
        self.assertEqual(item_genre_names(it), ["Action", "Drama"])

    def test_names_from_tmdb_ids(self):
        it = _item(2, "B", genre_ids=[28, 35])
        self.assertEqual(item_genre_names(it), ["Action", "Comedy"])

    def test_matches_all_requires_every_selected(self):
        it = _item(1, "A", genres=["Action", "Drama"])
        self.assertTrue(matches_all(it, {"Action"}))
        self.assertTrue(matches_all(it, {"Action", "Drama"}))
        self.assertFalse(matches_all(it, {"Action", "Comedy"}))
        self.assertTrue(matches_all(it, set()))

    def test_bad_json_returns_empty(self):
        it = SimpleNamespace(tmdb_id=1, title="X", media_type="movie",
                             genres="{oops", genre_ids=[])
        self.assertEqual(item_genre_names(it), [])


class ChipsRowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            Gtk.init()
            Adw.init()
        except TypeError:
            pass

    def test_hidden_until_two_or_more_genres(self):
        row = GenreChipsRow()
        row.set_genres(["Action"])
        self.assertFalse(row.get_visible())
        row.set_genres(["Action", "Drama"])
        self.assertTrue(row.get_visible())

    def test_pool_unchanged_skips_rebuild(self):
        row = GenreChipsRow()
        row.set_genres(["Action", "Drama"])
        first = row.get_first_child()
        row.set_genres(["Drama", "Action"])
        self.assertIs(row.get_first_child(), first)

    def test_selection_and_callback(self):
        fired = []
        row = GenreChipsRow(on_changed=lambda: fired.append(set(row.selected)))
        row.set_genres(["Action", "Drama", "Comedy"])
        btns = {btn.get_label(): btn for btn in _toggles(row)}
        btns["Action"].set_active(True)
        btns["Drama"].set_active(True)
        self.assertEqual(row.selected, {"Action", "Drama"})
        self.assertEqual(len(fired), 2)

    def test_selection_pruned_when_pool_shrinks(self):
        row = GenreChipsRow()
        row.set_genres(["Action", "Drama"])
        for btn in _toggles(row):
            if btn.get_label() == "Drama":
                btn.set_active(True)
        self.assertEqual(row.selected, {"Drama"})
        row.set_genres(["Action"])
        self.assertEqual(row.selected, set())


class _FakeRepo:
    def __init__(self, watchlist=None, watched=None):
        self._watchlist = watchlist or []
        self._watched = watched or set()

    def get_watchlist_ids(self):
        return {r["tmdb_id"] for r in self._watchlist}

    def get_watched_ids(self, media_type):
        return set(self._watched)

    def get_ratings(self, media_type):
        return []

    def get_watched_episodes_for_show(self, tmdb_id):
        return set()

    def get_watched_show_ids(self):
        return set()

    def get_watchlist(self, mode=None):
        return self._watchlist

    def get_media_item(self, tmdb_id):
        return None


def _watchlist_row(item):
    return dict(
        tmdb_id=item.tmdb_id, media_type="movie", added_at=1,
        title=item.title, year=2020, poster_url=None, runtime=None,
        imdb_id=None, genres=item.genres,
    )


class WatchlistChipsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            Gtk.init()
            Adw.init()
        except TypeError:
            pass

    def _page(self, items):
        repo = _FakeRepo(watchlist=[_watchlist_row(i) for i in items])
        with mock.patch("gi.repository.GLib.Thread.new"):
            page = WatchlistPage(object(), repo, object(), None)
        return page

    def test_chips_filter_with_and_semantics(self):
        a = _item(1, "Alpha", genres=["Action", "Drama"])
        b = _item(2, "Beta", genres=["Action"])
        c = _item(3, "Gamma", genres=["Comedy"])
        page = self._page([a, b, c])
        page._populate(page._reload_token, [a, b, c], [])
        self.assertTrue(page.genre_chips.get_visible())
        self.assertEqual(_grid_count(page.movies_grid), 3)
        for btn in _toggles(page.genre_chips):
            if btn.get_label() in ("Action", "Drama"):
                btn.set_active(True)
        self.assertEqual(_grid_count(page.movies_grid), 1)
        for btn in _toggles(page.genre_chips):
            btn.set_active(False)
        self.assertEqual(_grid_count(page.movies_grid), 3)

    def test_single_genre_hides_chips(self):
        a = _item(1, "Alpha", genres=["Action"])
        b = _item(2, "Beta", genres=["Action"])
        page = self._page([a, b])
        page._populate(page._reload_token, [a, b], [])
        self.assertFalse(page.genre_chips.get_visible())


class HistoryChipsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            Gtk.init()
            Adw.init()
        except TypeError:
            pass

    def test_history_inherits_genre_filtering(self):
        rows = [
            dict(tmdb_id=1, media_type="movie", show_tmdb_id=None,
                 season_number=None, episode_number=None, watched_at=100,
                 title="Alpha", year=2020, poster_url=None, imdb_id=None,
                 runtime=None, collection_id=None, collection_name=None,
                 genres=json.dumps(["Action", "Drama"])),
            dict(tmdb_id=2, media_type="movie", show_tmdb_id=None,
                 season_number=None, episode_number=None, watched_at=90,
                 title="Beta", year=2021, poster_url=None, imdb_id=None,
                 runtime=None, collection_id=None, collection_name=None,
                 genres=json.dumps(["Comedy"])),
        ]
        repo = _FakeRepo()

        def get_watched_list(media_type=None):
            return rows if media_type in (None, "movie") else []

        repo.get_watched_list = get_watched_list
        with mock.patch("gi.repository.GLib.Thread.new"):
            page = HistoryPage(object(), repo, object(), None)
        items = page._dicts_to_items(rows)
        page._populate(page._reload_token, items, [])
        self.assertTrue(page.genre_chips.get_visible())
        for btn in _toggles(page.genre_chips):
            if btn.get_label() == "Drama":
                btn.set_active(True)
        self.assertEqual(_grid_count(page.movies_grid), 1)

    def test_history_movies_get_watched_badges(self):
        rows = [
            dict(tmdb_id=1, media_type="movie", show_tmdb_id=None,
                 season_number=None, episode_number=None, watched_at=100,
                 title="Alpha", year=2020, poster_url=None, imdb_id=None,
                 runtime=None, collection_id=None, collection_name=None,
                 genres=None),
            dict(tmdb_id=2, media_type="movie", show_tmdb_id=None,
                 season_number=None, episode_number=None, watched_at=90,
                 title="Beta", year=2021, poster_url=None, imdb_id=None,
                 runtime=None, collection_id=None, collection_name=None,
                 genres=None),
        ]
        repo = _FakeRepo()

        def get_watched_list(media_type=None):
            return rows if media_type in (None, "movie") else []

        repo.get_watched_list = get_watched_list
        with mock.patch("gi.repository.GLib.Thread.new"):
            page = HistoryPage(object(), repo, object(), None)
        movies, shows = page._get_items("all")
        watched_ids = page._early_badges(movies, shows)
        self.assertEqual(watched_ids, {1, 2})
        page._populate(page._reload_token, movies, shows,
                       watched_ids, frozenset())
        child = page.movies_grid.get_first_child()
        while child:
            self.assertTrue(_has_watched_badge(child.get_child()))
            child = child.get_next_sibling()


class SearchChipsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            Gtk.init()
            Adw.init()
        except TypeError:
            pass

    def test_search_results_filtered_by_chips(self):
        a = _item(1, "Alpha", genre_ids=[28, 18])
        b = _item(2, "Beta", genre_ids=[28])
        with mock.patch("gi.repository.GLib.Thread.new"):
            page = SearchPage(object(), _FakeRepo(), object(), None)
        page._populate([a, b], [])
        self.assertTrue(page.genre_chips.get_visible())
        self.assertEqual(_grid_count(page.movies_grid), 2)
        for btn in _toggles(page.genre_chips):
            if btn.get_label() == "Drama":
                btn.set_active(True)
        self.assertEqual(_grid_count(page.movies_grid), 1)

    def test_clear_keeps_result_sections_attached(self):
        show = SimpleNamespace(
            tmdb_id=9, title="Show", media_type="tv",
            year=2021, poster_url=None, genres=None, genre_ids=[18],
        )
        movie = _item(8, "Flick", genre_ids=[28])
        with mock.patch("gi.repository.GLib.Thread.new"):
            page = SearchPage(object(), _FakeRepo(), object(), None)
        page._populate([movie], [show])
        page._clear()
        children = []
        child = page.dashboard_box.get_first_child()
        while child:
            children.append(child)
            child = child.get_next_sibling()
        self.assertIn(page.movies_section[0], children)
        self.assertIn(page.shows_section[0], children)

    def test_watched_movie_shows_badge_in_search(self):
        a = _item(1, "Alpha")
        b = _item(2, "Beta")
        with mock.patch("gi.repository.GLib.Thread.new"):
            page = SearchPage(object(), _FakeRepo(), object(), None)
        page._populate([a, b], [], frozenset({1}), frozenset())
        self.assertTrue(_has_watched_badge(_first_grid_card(page.movies_grid)))
        second = page.movies_grid.get_first_child().get_next_sibling()
        self.assertFalse(_has_watched_badge(second.get_child()))


class RepoGenresTest(unittest.TestCase):
    def test_watchlist_and_watched_expose_genres(self):
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "db.sqlite")
            repo = LocalMediaRepository(db)
            repo.initialize()
            conn = repo._ensure_conn()
            conn.execute(
                "INSERT INTO media_items (tmdb_id, media_type, title, year, "
                "cached_at, updated_at, genres) "
                "VALUES (1, 'movie', 'A', 2020, 1, 1, '[\"Action\"]')"
            )
            conn.commit()
            repo.add_to_watchlist(1, "movie")
            repo.mark_watched(1, "movie")
            wl = repo.get_watchlist("movie")
            self.assertEqual(json.loads(wl[0]["genres"]), ["Action"])
            w = repo.get_watched_list("movie")
            self.assertEqual(json.loads(w[0]["genres"]), ["Action"])




class PlaceholderChipsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            Gtk.init()
            Adw.init()
        except TypeError:
            pass

    def _pill_count(self, row):
        n = 0
        child = row.get_first_child()
        while child:
            if (isinstance(child, Gtk.Label)
                    and "skeleton-pulse" in child.get_css_classes()):
                n += 1
            child = child.get_next_sibling()
        return n

    def _toggle_count(self, row):
        return sum(1 for b in _toggles(row))

    def test_placeholder_visible_with_pills(self):
        from src.ui.genre_chips import GenreChipsRow
        row = GenreChipsRow()
        self.assertFalse(row.get_visible())
        row.show_placeholder()
        self.assertTrue(row.get_visible())
        self.assertEqual(self._pill_count(row), 6)

    def test_set_genres_replaces_placeholders(self):
        from src.ui.genre_chips import GenreChipsRow
        row = GenreChipsRow()
        row.show_placeholder()
        row.set_genres(["Action", "Drama"])
        self.assertEqual(self._pill_count(row), 0)
        self.assertEqual(self._toggle_count(row), 2)

    def test_search_page_shows_placeholder_at_construction(self):
        with mock.patch("gi.repository.GLib.Thread.new"):
            page = SearchPage(object(), _FakeRepo(), object(), None)
        self.assertTrue(page.genre_chips.get_visible())
        self.assertGreater(self._pill_count(page.genre_chips), 0)


if __name__ == "__main__":
    unittest.main()


class ChipsAttachmentTest(unittest.TestCase):
    """The chip row must stay attached to the page after a search's
    _clear(): it lives in chips_holder, which used to be stripped from
    the dashboard because it wasn't listed as a fixed child — leaving
    the filters invisible while the detached object still 'worked'."""

    @classmethod
    def setUpClass(cls):
        try:
            Gtk.init()
            Adw.init()
        except TypeError:
            pass

    def _dashboard_children(self, page):
        out = []
        child = page.dashboard_box.get_first_child()
        while child:
            out.append(child)
            child = child.get_next_sibling()
        return out

    def test_chip_holder_survives_clear(self):
        with mock.patch("gi.repository.GLib.Thread.new"):
            page = SearchPage(object(), _FakeRepo(), object(), None)
        holder = page.genre_chips.get_parent()
        self.assertIsNotNone(holder)
        self.assertIn(holder, self._dashboard_children(page))

        page._populate([_item(1, "A", genre_ids=[28])], [])
        page._clear()

        children = self._dashboard_children(page)
        self.assertIn(holder, children)
        self.assertIs(page.genre_chips.get_parent(), holder)

    def test_chips_visible_after_second_search(self):
        with mock.patch("gi.repository.GLib.Thread.new"):
            page = SearchPage(object(), _FakeRepo(), object(), None)
        page._populate([_item(1, "A", genre_ids=[28]), _item(2, "B", genre_ids=[18])], [])
        page._drain_build()
        page._clear()
        # second search repopulates; row must still be shown and attached
        page._populate([_item(3, "C", genre_ids=[28]), _item(4, "D", genre_ids=[35])], [])
        page._drain_build()
        self.assertTrue(page.genre_chips.get_visible())
        self.assertIs(page.genre_chips.get_parent(), page.genre_chips.get_parent())
        holder = page.genre_chips.get_parent()
        self.assertIn(holder, self._dashboard_children(page))
