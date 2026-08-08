"""History: watchlist-style gallery of watched movies and fully watched shows."""

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from types import SimpleNamespace

from gi.repository import Gtk, Adw

from .watchlist_page import WatchlistPage


class HistoryPage(WatchlistPage):
    def __init__(self, win, user_repo, metadata_service, main_page=None):
        self._added_attr = "watched_at"
        self._sort_labels = ["Recently Watched", "Release Date"]
        self._empty_label = "No history found"
        super().__init__(win, user_repo, metadata_service, main_page)

    def _get_items(self, mode):
        if mode in ("all", "movies"):
            movies = self._dicts_to_items(self.user_repo.get_watched_list("movie"))
        else:
            movies = []

        if mode in ("all", "shows"):
            shows = self._get_fully_watched_shows()
        else:
            shows = []

        return movies, shows

    def _get_fully_watched_shows(self):
        """Return shows where every aired episode is watched."""
        fully_watched = []
        for show_id in self._get_fully_watched_show_ids():
            meta = self.user_repo.get_media_item(show_id)
            if not meta:
                continue
            fully_watched.append(SimpleNamespace(
                tmdb_id=show_id,
                title=meta["title"],
                year=meta.get("year"),
                poster_url=meta.get("poster_url"),
                media_type="show",
                watched_at=self.user_repo.get_latest_watched_at_for_show(show_id),
            ))
        return fully_watched
