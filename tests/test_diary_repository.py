"""Diary view repository layer: day grouping, per-day minutes, notes."""

import os
import sqlite3
import sys
import tempfile
import types
import unittest
from datetime import datetime, timezone

if "src.config" not in sys.modules:
    _cfg = types.ModuleType("src.config")
    _cfg.APP_ID = "io.github.andrea_scape.ciak.Devel"
    _cfg.APP_VERSION = "0.0.0-test"
    _cfg.APP_NAME = "Ciak"
    sys.modules["src.config"] = _cfg

from src.data.local.repository import LocalMediaRepository


def _ts(day_iso, hour=20):
    dt = datetime.fromisoformat(f"{day_iso}T{hour}:00:00").replace(
        tzinfo=datetime.now().astimezone().tzinfo
    )
    return int(dt.timestamp())


MOVIE_A, MOVIE_B, SHOW_1, SHOW_2 = 101, 102, 201, 202
EP_1_1, EP_1_2, EP_1_3, EP_2_1 = 301, 302, 303, 304
DAY_1, DAY_2 = "2026-08-14", "2026-08-15"


def _seed(repo):
    conn = repo._ensure_conn()
    conn.executemany(
        "INSERT INTO media_items (tmdb_id, media_type, title, year, "
        "poster_url, runtime, cached_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (MOVIE_A, "movie", "Movie A", 2020, "a.jpg", 110, 1, 1),
            (MOVIE_B, "movie", "Movie B", 2021, "b.jpg", 90, 1, 1),
            (SHOW_1, "show", "Show One", 2019, "s1.jpg", 45, 1, 1),
            # SHOW_2 intentionally left without a cached runtime.
            (SHOW_2, "show", "Show Two", 2022, "s2.jpg", None, 1, 1),
        ],
    )
    conn.executemany(
        "INSERT INTO episodes (show_tmdb_id, season_number, episode_number, "
        "tmdb_id, title, runtime, cached_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            (SHOW_1, 1, 1, EP_1_1, "Pilot", 40, 1),
            (SHOW_1, 1, 2, EP_1_2, "Second", 50, 1),
            (SHOW_1, 1, 3, EP_1_3, "Third", None, 1),
            (SHOW_2, 1, 1, EP_2_1, "Alone", None, 1),
        ],
    )
    conn.executemany(
        "INSERT INTO watched_items (tmdb_id, media_type, show_tmdb_id, "
        "season_number, episode_number, watched_at) VALUES (?, ?, ?, ?, ?, ?)",
        [
            # Day 1: Movie A (110 min) + Show One E1+E2 (40+50 exact).
            (MOVIE_A, "movie", None, None, None, _ts(DAY_1)),
            (EP_1_1, "episode", SHOW_1, 1, 1, _ts(DAY_1, 21)),
            (EP_1_2, "episode", SHOW_1, 1, 2, _ts(DAY_1, 22)),
            # Day 2: Movie B (90) + Show One E3 (missing runtime -> show
            # fallback 45) + Show Two E1 (missing runtime, show runtime is
            # NULL -> 0 contribution).
            (MOVIE_B, "movie", None, None, None, _ts(DAY_2)),
            (EP_1_3, "episode", SHOW_1, 1, 3, _ts(DAY_2, 21)),
            (EP_2_1, "episode", SHOW_2, 1, 1, _ts(DAY_2, 22)),
        ],
    )
    conn.execute(
        "INSERT INTO ratings (tmdb_id, media_type, rating, rated_at) "
        "VALUES (?, ?, ?, ?)",
        (MOVIE_A, "movie", 4, 1),
    )
    conn.execute(
        "INSERT INTO ratings (tmdb_id, media_type, rating, rated_at) "
        "VALUES (?, ?, ?, ?)",
        (SHOW_1, "show", 5, 1),
    )
    conn.commit()


def _make_repo():
    tmp = tempfile.TemporaryDirectory()
    repo = LocalMediaRepository(os.path.join(tmp.name, "db.sqlite"))
    repo.initialize()
    _seed(repo)
    return repo, tmp


class DiarySchemaTest(unittest.TestCase):
    def test_notes_column_and_version(self):
        with tempfile.TemporaryDirectory() as d:
            repo = LocalMediaRepository(os.path.join(d, "db.sqlite"))
            repo.initialize()
            cols = {r[1] for r in repo._ensure_conn().execute(
                "PRAGMA table_info(watched_items)")}
            self.assertIn("notes", cols)
            version = repo._ensure_conn().execute(
                "SELECT MAX(version) FROM schema_version").fetchone()[0]
            self.assertGreaterEqual(version, 4)


class DiaryDaysTest(unittest.TestCase):
    def test_groups_by_local_day_newest_first(self):
        repo, tmp = _make_repo()
        try:
            days = repo.get_diary_days()
            self.assertEqual([d["day"] for d in days], [DAY_2, DAY_1])
            by_day = {d["day"]: d for d in days}
            # Day 1: movie 110 + episodes 40+50 exact.
            self.assertEqual(by_day[DAY_1]["item_count"], 3)
            self.assertEqual(by_day[DAY_1]["minutes"], 200)
            # Day 2: movie 90 + fallback 45 for Show One E3 + 0 for the
            # runtime-less show.
            self.assertEqual(by_day[DAY_2]["item_count"], 3)
            self.assertEqual(by_day[DAY_2]["minutes"], 135)
        finally:
            tmp.cleanup()

    def test_empty_history(self):
        with tempfile.TemporaryDirectory() as d:
            repo = LocalMediaRepository(os.path.join(d, "db.sqlite"))
            repo.initialize()
            self.assertEqual(repo.get_diary_days(), [])


class DiaryEntriesTest(unittest.TestCase):
    def test_movies_carry_title_poster_rating(self):
        repo, tmp = _make_repo()
        try:
            movies = [e for e in repo.get_diary_entries(DAY_1)
                      if e["media_type"] == "movie"]
            self.assertEqual(len(movies), 1)
            m = movies[0]
            self.assertEqual(m["title"], "Movie A")
            self.assertEqual(m["poster_url"], "a.jpg")
            self.assertEqual(m["rating"], 4)
            self.assertIsNone(m["notes"])
        finally:
            tmp.cleanup()

    def test_episodes_consolidated_per_show_with_range(self):
        repo, tmp = _make_repo()
        try:
            shows = [e for e in repo.get_diary_entries(DAY_1)
                     if e["media_type"] != "movie"]
            self.assertEqual(len(shows), 1)
            s = shows[0]
            self.assertEqual(s["tmdb_id"], SHOW_1)
            self.assertEqual(s["title"], "Show One")
            self.assertEqual(s["rating"], 5)
            self.assertEqual(s["season_number"], 1)
            self.assertEqual(s["end_episode_number"], 2)
        finally:
            tmp.cleanup()


class SessionNotesTest(unittest.TestCase):
    def test_movie_note_roundtrip(self):
        repo, tmp = _make_repo()
        try:
            repo.set_session_notes(MOVIE_A, False, DAY_1, "rewatch, loved it")
            movies = [e for e in repo.get_diary_entries(DAY_1)
                      if e["media_type"] == "movie"]
            self.assertEqual(movies[0]["notes"], "rewatch, loved it")
        finally:
            tmp.cleanup()

    def test_show_note_applies_to_whole_session(self):
        repo, tmp = _make_repo()
        try:
            repo.set_session_notes(SHOW_1, True, DAY_1, "binge night")
            shows = [e for e in repo.get_diary_entries(DAY_1)
                     if e["media_type"] != "movie"]
            self.assertEqual(shows[0]["notes"], "binge night")
            # Both episode rows of that day were annotated.
            n = repo._ensure_conn().execute(
                "SELECT COUNT(*) FROM watched_items "
                "WHERE show_tmdb_id = ? AND notes IS NOT NULL",
                (SHOW_1,),
            ).fetchone()[0]
            self.assertEqual(n, 2)
            # Other days untouched.
            other = repo.get_diary_entries(DAY_2)
            self.assertTrue(all(e["notes"] is None for e in other))
        finally:
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()


class SessionRescheduleTest(unittest.TestCase):
    def test_moves_whole_session_preserving_times(self):
        repo, tmp = _make_repo()
        try:
            moved = repo.reschedule_session(SHOW_1, True, DAY_1, "2026-08-20")
            self.assertEqual(moved, 2)
            conn = repo._ensure_conn()
            days = [r[0] for r in conn.execute(
                "SELECT DISTINCT strftime('%Y-%m-%d', watched_at,"
                " 'unixepoch', 'localtime') FROM watched_items "
                "WHERE show_tmdb_id = ?", (SHOW_1,))]
            # Day 2's separate Show One session must remain.
            self.assertEqual(sorted(days), ["2026-08-15", "2026-08-20"])
            # Intra-day ordering preserved (21h < 22h).
            hours = [r[0] for r in conn.execute(
                "SELECT strftime('%H', watched_at, 'unixepoch',"
                " 'localtime') FROM watched_items WHERE show_tmdb_id = ?"
                " ORDER BY watched_at", (SHOW_1,))]
            # Third row is Day 2's untouched session at 21h.
            self.assertEqual(hours, ["21", "21", "22"])
        finally:
            tmp.cleanup()

    def test_movie_reschedule(self):
        repo, tmp = _make_repo()
        try:
            self.assertEqual(
                repo.reschedule_session(MOVIE_A, False, DAY_1, "2026-09-01"), 1)
            entries = repo.get_diary_entries("2026-09-01")
            self.assertEqual(
                [e["title"] for e in entries], ["Movie A"])
        finally:
            tmp.cleanup()


class SessionDeleteTest(unittest.TestCase):
    def test_deletes_only_target_session(self):
        repo, tmp = _make_repo()
        try:
            removed = repo.delete_session(SHOW_1, True, DAY_1)
            self.assertEqual(removed, 2)
            remaining = repo.get_diary_entries(DAY_1)
            self.assertFalse(
                any(e["media_type"] != "movie" for e in remaining))
            # Day 2 untouched.
            self.assertTrue(repo.get_diary_entries(DAY_2))
        finally:
            tmp.cleanup()


class RewatchCountTest(unittest.TestCase):
    def test_multi_session_show_counts(self):
        repo, tmp = _make_repo()
        try:
            # Second FROM-style session on another day.
            conn = repo._ensure_conn()
            conn.execute(
                "INSERT INTO watched_items (tmdb_id, media_type,"
                " show_tmdb_id, season_number, episode_number, watched_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (EP_1_3 + 1000, "episode", SHOW_1, 1, 3,
                 _ts("2026-08-20", 21)),
            )
            conn.commit()
            entries = repo.get_diary_entries(DAY_1)
            show = next(e for e in entries if e["media_type"] == "show")
            # Sessions on Day 1, Day 2 and the inserted one.
            self.assertEqual(show["rewatch_count"], 3)
            movie = next(e for e in entries if e["media_type"] == "movie")
            self.assertEqual(movie["rewatch_count"], 1)
        finally:
            tmp.cleanup()

    def test_single_session_defaults_to_one(self):
        repo, tmp = _make_repo()
        try:
            for e in repo.get_diary_entries(DAY_2):
                self.assertGreaterEqual(e["rewatch_count"], 1)
        finally:
            tmp.cleanup()
