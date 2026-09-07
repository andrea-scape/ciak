"""History: watchlist-style gallery of watched movies and watched episodes."""

import datetime
import os
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from types import SimpleNamespace

from gi.repository import GLib, Gtk, Adw

from .watchlist_page import WatchlistPage
from . import watched_state


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
    upcoming_enabled = False

    def __init__(self, win, user_repo, metadata_service, main_page=None):
        self._added_attr = "watched_at"
        self._sort_labels = ["Recently Watched", "Release Date"]
        self._empty_label = "No history found"
        self._search_placeholder = "History"
        super().__init__(win, user_repo, metadata_service, main_page)
        self._hide_caught_up = False

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

    def _early_badges(self, movies, shows):
        """Every history movie is watched by definition — known instantly."""
        return frozenset(m.tmdb_id for m in movies)

    def _late_badges(self, movies, shows):
        """Show cards earn the check once every aired episode is seen.
        Results stream back per show so each badge appears as soon as its
        check resolves instead of waiting for the whole batch; computed
        off-thread so rendering never waits."""
        show_ids = {s.tmdb_id for s in shows}
        if os.environ.get("CIK_DEBUG"):
            print(f"[history] requesting watched checks for {len(show_ids)} shows")
        passed = set()

        def _on_result(show_id, caught_up):
            if caught_up:
                passed.add(show_id)
                GLib.idle_add(
                    self._apply_late_badges, self._reload_token, {show_id}
                )

        watched_state.caught_up_show_ids(
            self.user_repo, self.metadata_service, show_ids,
            on_result=_on_result,
        )
        if os.environ.get("CIK_DEBUG"):
            print(f"[history] badges: requested={len(show_ids)} "
                  f"passed={len(passed)}")
        return frozenset()
