"""History: watchlist-style gallery of watched movies and watched episodes."""

import datetime
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from types import SimpleNamespace

from gi.repository import Gtk, Adw

from .watchlist_page import WatchlistPage


def group_episodes_by_day(rows):
    """Group watched episode rows by (show, local day) and build one card
    per group spanning the first to the last episode watched that day.
    Rows with no episode info (whole-show marks) pass through unchanged.
    """
    groups = {}
    for row in rows:
        show_id = row["show_tmdb_id"] or row["tmdb_id"]
        day = datetime.date.fromtimestamp(row["watched_at"])
        groups.setdefault((show_id, day), []).append(row)

    cards = []
    for (show_id, day), eps in groups.items():
        eps = sorted(eps, key=lambda e: (
            e["season_number"] or 0, e["episode_number"] or 0
        ))
        first = eps[0]
        last = eps[-1]
        cards.append(SimpleNamespace(
            tmdb_id=show_id,
            title=first["title"] or "Unknown",
            year=first.get("year"),
            poster_url=first.get("poster_url"),
            media_type="show",
            watched_at=max(e["watched_at"] for e in eps),
            season_number=first["season_number"],
            episode_number=first["episode_number"],
            end_season_number=last["season_number"],
            end_episode_number=last["episode_number"],
        ))
    return cards


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
            shows = group_episodes_by_day(self.user_repo.get_watched_list("show"))
        else:
            shows = []

        return movies, shows
