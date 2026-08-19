import datetime
import os
import sqlite3
import tempfile
import unittest
from types import SimpleNamespace

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw

from src.data.local.repository import LocalMediaRepository
from src.ui.history_page import group_episodes_by_day
from src.ui.media_card import media_type_label


def _ts(y, m, d, hh, mm):
    return int(datetime.datetime(y, m, d, hh, mm).timestamp())


def _seed(db, envs):
    """envs: list of dicts with tmdb_id, media_type, show_tmdb_id,
    season_number, episode_number, watched_at."""
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "INSERT INTO media_items "
            "(tmdb_id, media_type, title, year, poster_url, cached_at, updated_at) "
            "VALUES (7, 'show', 'Lost', 2004, 'http://poster/lost.jpg', 1, 1)"
        )
        conn.execute(
            "INSERT INTO media_items "
            "(tmdb_id, media_type, title, year, poster_url, cached_at, updated_at) "
            "VALUES (8, 'show', 'The Office', 2005, 'http://poster/office.jpg', 1, 1)"
        )
        for e in envs:
            conn.execute(
                "INSERT INTO watched_items "
                "(tmdb_id, media_type, show_tmdb_id, season_number, episode_number, watched_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (e["tmdb_id"], e["media_type"], e.get("show_tmdb_id"),
                 e.get("season_number"), e.get("episode_number"), e["watched_at"]),
            )
        conn.commit()
    finally:
        conn.close()


def _make_repo_with(db):
    repo = LocalMediaRepository(db)
    repo.initialize()
    return repo


class EpisodeGroupingTest(unittest.TestCase):
    def test_same_day_episodes_form_one_range_card(self):
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "db.sqlite")
            _make_repo_with(db)
            _seed(db, [
                {"tmdb_id": 900, "media_type": "episode", "show_tmdb_id": 7,
                 "season_number": 1, "episode_number": 1, "watched_at": _ts(2026, 1, 5, 10, 0)},
                {"tmdb_id": 901, "media_type": "episode", "show_tmdb_id": 7,
                 "season_number": 1, "episode_number": 2, "watched_at": _ts(2026, 1, 5, 11, 0)},
                {"tmdb_id": 902, "media_type": "episode", "show_tmdb_id": 7,
                 "season_number": 1, "episode_number": 3, "watched_at": _ts(2026, 1, 5, 12, 0)},
            ])
            repo = LocalMediaRepository(db)
            cards = group_episodes_by_day(repo.get_watched_list("show"))
            self.assertEqual(len(cards), 1)
            card = cards[0]
            self.assertEqual(card.tmdb_id, 7)
            self.assertEqual(card.media_type, "show")
            self.assertEqual(card.title, "Lost")
            self.assertEqual(card.poster_url, "http://poster/lost.jpg")
            self.assertEqual(card.season_number, 1)
            self.assertEqual(card.episode_number, 1)
            self.assertEqual(card.end_season_number, 1)
            self.assertEqual(card.end_episode_number, 3)
            self.assertEqual(card.watched_at, _ts(2026, 1, 5, 12, 0))

    def test_gaps_are_ignored_first_last_range(self):
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "db.sqlite")
            _make_repo_with(db)
            _seed(db, [
                {"tmdb_id": 900, "media_type": "episode", "show_tmdb_id": 7,
                 "season_number": 1, "episode_number": 1, "watched_at": _ts(2026, 1, 5, 10, 0)},
                {"tmdb_id": 901, "media_type": "episode", "show_tmdb_id": 7,
                 "season_number": 1, "episode_number": 2, "watched_at": _ts(2026, 1, 5, 11, 0)},
                {"tmdb_id": 905, "media_type": "episode", "show_tmdb_id": 7,
                 "season_number": 1, "episode_number": 5, "watched_at": _ts(2026, 1, 5, 12, 0)},
            ])
            repo = LocalMediaRepository(db)
            cards = group_episodes_by_day(repo.get_watched_list("show"))
            self.assertEqual(len(cards), 1)
            self.assertEqual(cards[0].end_episode_number, 5)
            self.assertEqual(cards[0].episode_number, 1)

    def test_two_days_yields_two_cards(self):
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "db.sqlite")
            _make_repo_with(db)
            _seed(db, [
                {"tmdb_id": 900, "media_type": "episode", "show_tmdb_id": 7,
                 "season_number": 1, "episode_number": 1, "watched_at": _ts(2026, 1, 5, 10, 0)},
                {"tmdb_id": 901, "media_type": "episode", "show_tmdb_id": 7,
                 "season_number": 1, "episode_number": 2, "watched_at": _ts(2026, 1, 6, 10, 0)},
            ])
            repo = LocalMediaRepository(db)
            cards = group_episodes_by_day(repo.get_watched_list("show"))
            self.assertEqual(len(cards), 2)

    def test_two_shows_same_day_yield_two_cards(self):
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "db.sqlite")
            _make_repo_with(db)
            _seed(db, [
                {"tmdb_id": 900, "media_type": "episode", "show_tmdb_id": 7,
                 "season_number": 1, "episode_number": 1, "watched_at": _ts(2026, 1, 5, 10, 0)},
                {"tmdb_id": 910, "media_type": "episode", "show_tmdb_id": 8,
                 "season_number": 2, "episode_number": 1, "watched_at": _ts(2026, 1, 5, 11, 0)},
            ])
            repo = LocalMediaRepository(db)
            cards = group_episodes_by_day(repo.get_watched_list("show"))
            self.assertEqual(len(cards), 2)
            titles = {c.title for c in cards}
            self.assertEqual(titles, {"Lost", "The Office"})

    def test_whole_show_row_yields_plain_card(self):
        # A movie-level / whole-show watched row has no episode info and
        # must still produce a single card.
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "db.sqlite")
            _make_repo_with(db)
            _seed(db, [
                {"tmdb_id": 7, "media_type": "show",
                 "watched_at": _ts(2026, 1, 5, 10, 0)},
            ])
            repo = LocalMediaRepository(db)
            cards = group_episodes_by_day(repo.get_watched_list("show"))
            self.assertEqual(len(cards), 1)
            self.assertEqual(cards[0].tmdb_id, 7)
            self.assertIsNone(getattr(cards[0], "season_number", None))


class MediaTypeLabelTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            Gtk.init()
            Adw.init()
        except TypeError:
            pass

    def test_movie(self):
        item = SimpleNamespace(media_type="movie")
        self.assertEqual(media_type_label(item), "Movie")

    def test_plain_show(self):
        item = SimpleNamespace(media_type="show", title="Lost")
        self.assertEqual(media_type_label(item), "TV Show")

    def test_single_episode(self):
        item = SimpleNamespace(media_type="show", season_number=1,
                               episode_number=1)
        self.assertEqual(media_type_label(item), "TV Show - S01E01")

    def test_range(self):
        item = SimpleNamespace(media_type="show", season_number=1,
                               episode_number=1, end_season_number=1,
                               end_episode_number=3)
        self.assertEqual(media_type_label(item), "TV Show - S01E01 to S01E03")

    def test_range_across_seasons(self):
        item = SimpleNamespace(media_type="show", season_number=1,
                               episode_number=24, end_season_number=2,
                               end_episode_number=1)
        self.assertEqual(media_type_label(item), "TV Show - S01E24 to S02E01")


if __name__ == "__main__":
    unittest.main()