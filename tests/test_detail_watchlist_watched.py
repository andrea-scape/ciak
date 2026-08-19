import unittest
from unittest import mock

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw

from src.domain.models import Movie
from src.ui.detail_page import DetailPage


def make_page(repo):
    item = Movie(tmdb_id=42, title="Test Movie", release_date="2020-01-01")
    return DetailPage(object(), repo, object(), "movie", item)


class TwoWayExclusivityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            Gtk.init()
            Adw.init()
        except TypeError:
            pass

    def test_watched_item_keeps_watchlist_button_enabled(self):
        # Mutual exclusivity is resolved at click time, not by disabling:
        # a watched title can still be offered "Add to Watchlist".
        page = make_page(mock.Mock())
        page._is_watched = True
        page._in_watchlist = False
        page._update_action_sensitivity()
        self.assertTrue(page.watchlist_btn.get_sensitive())
        self.assertTrue(page.watched_btn.get_sensitive())

    def test_watchlist_item_keeps_watched_button_enabled(self):
        # A watchlisted title must still be markable as watched.
        page = make_page(mock.Mock())
        page._is_watched = False
        page._in_watchlist = True
        page._update_action_sensitivity()
        self.assertTrue(page.watched_btn.get_sensitive())
        self.assertTrue(page.watchlist_btn.get_sensitive())

    def test_marking_watched_removes_from_watchlist(self):
        repo = mock.Mock()
        page = make_page(repo)
        page._is_watched = False
        page._in_watchlist = True
        page._do_mark_watched()
        repo.remove_from_watchlist.assert_called_once_with(42, "movie")
        self.assertFalse(page._in_watchlist)

    def test_rating_implied_watched_removes_from_watchlist(self):
        # Rating implies "seen", so it must clear the watchlist too.
        repo = mock.Mock()
        page = make_page(repo)
        page._in_watchlist = True
        page._do_rate_mark_watched()
        repo.remove_from_watchlist.assert_called_once_with(42, "movie")
        self.assertFalse(page._in_watchlist)

    def test_adding_to_watchlist_unmarks_watched(self):
        repo = mock.Mock()
        page = make_page(repo)
        page._in_watchlist = False
        page._is_watched = True
        with mock.patch("src.ui.detail_page.GLib.idle_add"):
            page._do_toggle_watchlist(page.watchlist_btn)
        repo.add_to_watchlist.assert_called_once_with(42, "movie")
        repo.mark_unwatched.assert_called_once_with(42, "movie")
        self.assertFalse(page._is_watched)

    def test_watchlist_done_refreshes_watched_ui(self):
        repo = mock.Mock()
        page = make_page(repo)
        page._in_watchlist = True
        page._is_watched = False
        with mock.patch("src.ui.detail_page.GLib.idle_add"), \
             mock.patch.object(page, "_set_watched_ui") as swu:
            page._do_toggle_watchlist(page.watchlist_btn)
            page._watchlist_done(page.watchlist_btn)
        swu.assert_called_once()

    def test_watch_done_refreshes_watchlist_ui(self):
        repo = mock.Mock()
        page = make_page(repo)
        page._is_watched = True
        with mock.patch.object(page, "_set_watchlist_ui") as swu:
            page._watch_done(page.watched_btn)
        swu.assert_called_once()

    def test_watchlist_done_invalidates_history_when_unmarked(self):
        # Adding a watched title to the watchlist unmarks it, which should
        # also drop it from the history page.
        repo = mock.Mock()
        main_page = mock.Mock()
        page = make_page(repo)
        page.main_page = main_page
        page._watchlist_done(page.watchlist_btn)
        names = [c.args[0] for c in main_page.invalidate_page.call_args_list]
        self.assertIn("history", names)

    def test_rate_mark_watched_skips_redundant_write(self):
        # Marking watched and then rating must not write the same row twice.
        repo = mock.Mock()
        page = make_page(repo)
        page._is_watched = True
        page._in_watchlist = False
        with mock.patch("src.ui.detail_page.GLib.idle_add"):
            page._do_rate_mark_watched()
        repo.mark_watched.assert_not_called()


if __name__ == "__main__":
    unittest.main()