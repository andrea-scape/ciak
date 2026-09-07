import datetime
import os
import tempfile
import time
import unittest
from types import SimpleNamespace

from src.data.local.repository import LocalMediaRepository
from src.ui.notifications import CATCHUP_WINDOW_S, find_due_airings


class _Settings:
    def __init__(self, enabled=True, scope="all", last=0):
        self.enabled = enabled
        self.scope = scope
        self.last = last

    def get_boolean(self, key):
        return self.enabled

    def get_string(self, key):
        return self.scope

    def get_int64(self, key):
        return self.last

    def set_int64(self, key, value):
        self.last = value


class _FakeRepo:
    def __init__(self, movies=(), shows=(), overrides=None,
                 watched=None, notified=()):
        self._movies = list(movies)
        self._shows = list(shows)
        self._overrides = set(overrides or ())
        self._watched = set(watched or ())
        self._notified = set(notified)

    def get_notification_overrides(self):
        return set(self._overrides)

    def get_watchlist_with_dates(self, media_type):
        assert media_type == "movie"
        return list(self._movies)

    def get_watchlist(self, media_type=None):
        return list(self._shows)

    def has_notified(self, key):
        return key in self._notified

    def mark_notified(self, key):
        self._notified.add(key)

    def get_watched_episodes_for_show(self, show_id):
        return set(self._watched)


class _FakeMeta:
    def __init__(self, episodes=()):
        self._episodes = list(episodes)

    def get_latest_season_episodes(self, show_id):
        return list(self._episodes)


def _movie(mid, title, release_iso):
    return {"tmdb_id": mid, "media_type": "movie", "title": title,
            "year": 2026, "poster_url": None, "release_date": release_iso}


def _show(sid, title):
    return {"tmdb_id": sid, "media_type": "show", "title": title}


def _ep(season=2, number=5, air_iso="2026-08-26", name="Cold Open"):
    return SimpleNamespace(season_number=season, episode_number=number,
                           air_date=air_iso, title=name)


TODAY = datetime.date(2026, 8, 26)
NOW = int(time.mktime(TODAY.timetuple())) + 43200


class FindDueAiringsTest(unittest.TestCase):
    def test_disabled_returns_nothing(self):
        due = find_due_airings(
            _FakeRepo(movies=[_movie(1, "Dune", "2026-08-26")]),
            _FakeMeta(), _Settings(enabled=False), today=TODAY, now=NOW)
        self.assertEqual(due, [])

    def test_movie_releasing_today_is_due(self):
        due = find_due_airings(
            _FakeRepo(movies=[_movie(1, "Dune", "2026-08-26")]),
            _FakeMeta(), _Settings(), today=TODAY, now=NOW)
        self.assertEqual(len(due), 1)
        self.assertEqual(due[0]["key"], "movie:1")
        self.assertEqual(due[0]["body"], "Released today")

    def test_episode_airing_today_is_due_with_code_and_name(self):
        due = find_due_airings(
            _FakeRepo(shows=[_show(7, "Lost")]),
            _FakeMeta(episodes=[_ep()]), _Settings(), today=TODAY, now=NOW)
        self.assertEqual(len(due), 1)
        self.assertEqual(due[0]["key"], "show:7:2:5")
        self.assertIn("S02E05 aired today", due[0]["body"])

    def test_scope_all_excludes_muted_media(self):
        repo = _FakeRepo(
            movies=[_movie(1, "Dune", "2026-08-26")],
            shows=[_show(7, "Lost")],
            overrides={(7, "show")},
        )
        meta = _FakeMeta(episodes=[_ep()])
        due = find_due_airings(repo, meta, _Settings(), today=TODAY, now=NOW)
        self.assertEqual([d["key"] for d in due], ["movie:1"])

    def test_scope_selected_includes_only_picked(self):
        repo = _FakeRepo(
            movies=[_movie(1, "Dune", "2026-08-26"),
                    _movie(2, "Other", "2026-08-26")],
            shows=[_show(7, "Lost"), _show(8, "Dark")],
            overrides={(2, "movie")},
        )
        meta = _FakeMeta(episodes=[_ep()])
        due = find_due_airings(repo, meta, _Settings(scope="selected"),
                               today=TODAY, now=NOW)
        self.assertEqual([d["key"] for d in due], ["movie:2"])

    def test_first_enable_does_not_flood_old_episodes(self):
        # No last-check: horizon is only the catch-up window behind now.
        old = "2020-01-01"
        recent_iso = str(TODAY - datetime.timedelta(days=2))
        due = find_due_airings(
            _FakeRepo(shows=[_show(7, "Lost")]),
            _FakeMeta(episodes=[
                _ep(number=1, air_iso=old),
                _ep(number=2, air_iso=recent_iso),
                _ep(number=3, air_iso="2026-08-26"),
            ]),
            _Settings(), today=TODAY, now=NOW)
        numbers = {d["key"].split(":")[-1] for d in due}
        self.assertEqual(numbers, {"2", "3"})

    def test_already_watched_episode_skipped(self):
        due = find_due_airings(
            _FakeRepo(shows=[_show(7, "Lost")], watched={(2, 5)}),
            _FakeMeta(episodes=[_ep()]), _Settings(), today=TODAY, now=NOW)
        self.assertEqual(due, [])

    def test_dedup_via_notified_table(self):
        repo = _FakeRepo(shows=[_show(7, "Lost")], notified=("show:7:2:5",))
        due = find_due_airings(
            repo, _FakeMeta(episodes=[_ep()]), _Settings(),
            today=TODAY, now=NOW)
        self.assertEqual(due, [])

    def test_horizon_respects_last_check(self):
        # A check made earlier today shrinks the window to today: an
        # episode that aired yesterday predates the last check and is
        # no longer picked up (same-day items stay due; dedup table
        # prevents double-notifying those).
        recent_last = NOW - 7200
        due = find_due_airings(
            _FakeRepo(shows=[_show(7, "Lost")]),
            _FakeMeta(episodes=[_ep(air_iso=str(TODAY - datetime.timedelta(days=1)))]),
            _Settings(last=recent_last), today=TODAY, now=NOW)
        self.assertEqual(due, [])


class NotificationStoreTest(unittest.TestCase):
    def _repo(self, directory):
        db = os.path.join(directory, "db.sqlite")
        repo = LocalMediaRepository(db)
        repo.initialize()
        return repo

    def test_override_round_trip(self):
        with tempfile.TemporaryDirectory() as d:
            repo = self._repo(d)
            self.assertEqual(repo.get_notification_overrides(), set())
            repo.add_notification_override(42, "movie")
            repo.add_notification_override(42, "movie")  # idempotent
            repo.add_notification_override(7, "show")
            self.assertEqual(repo.get_notification_overrides(),
                             {(42, "movie"), (7, "show")})
            repo.remove_notification_override(42, "movie")
            self.assertEqual(repo.get_notification_overrides(),
                             {(7, "show")})

    def test_notified_round_trip(self):
        with tempfile.TemporaryDirectory() as d:
            repo = self._repo(d)
            key = f"movie:{int(time.time())}"
            self.assertFalse(repo.has_notified(key))
            repo.mark_notified(key)
            self.assertTrue(repo.has_notified(key))
            repo.mark_notified(key)  # replace is fine


class _BellRepo:
    """In-memory stand-in for the override API the bell touches."""

    def __init__(self, in_watchlist=True, overrides=None):
        self.in_watchlist = in_watchlist
        self.overrides = set(overrides or ())

    # watchlist state read by the page
    def is_in_watchlist(self, tmdb_id, media_type=None):
        return self.in_watchlist

    # overrides API
    def get_notification_overrides(self):
        return set(self.overrides)

    def add_notification_override(self, tmdb_id, media_type):
        self.overrides.add((tmdb_id, media_type))

    def remove_notification_override(self, tmdb_id, media_type):
        self.overrides.discard((tmdb_id, media_type))

    # unrelated calls the page may make while refreshing UI
    def get_watched_at(self, *a, **k):
        return None

    def get_latest_watched_at_for_show(self, *a, **k):
        return None


def _make_movie_page(repo, settings):
    import gi as _gi

    _gi.require_version("Gtk", "4.0")
    _gi.require_version("Adw", "1")
    from gi.repository import Gtk, Adw

    try:
        Gtk.init()
        Adw.init()
    except TypeError:
        pass
    from src.domain.models import Movie
    from src.ui.detail_page import DetailPage
    from types import SimpleNamespace as NS

    win = NS(settings=settings)
    item = Movie(tmdb_id=42, title="Dune")
    page = DetailPage(win, repo, object(), "movie", item)
    page._in_watchlist = getattr(repo, "in_watchlist", True)
    return page


class BellButtonTest(unittest.TestCase):
    def test_hidden_when_master_switch_off(self):
        page = _make_movie_page(_BellRepo(), _Settings(enabled=False))
        self.assertFalse(page.notify_btn.get_visible())

    def test_visible_when_enabled_and_watchedlisted(self):
        page = _make_movie_page(_BellRepo(), _Settings())
        page._refresh_notify_ui()
        self.assertTrue(page.notify_btn.get_visible())

    def test_hidden_when_not_in_watchlist(self):
        page = _make_movie_page(
            _BellRepo(in_watchlist=False), _Settings())
        page._in_watchlist = False
        page._refresh_notify_ui()
        self.assertFalse(page.notify_btn.get_visible())

    def test_scope_all_toggle_mutes_and_unmutes(self):
        repo = _BellRepo()
        settings = _Settings(scope="all")
        page = _make_movie_page(repo, settings)
        page._refresh_notify_ui()
        self.assertEqual(page.notify_label.get_text(), "Notify me")

        page._toggle_notify(None)          # mute
        self.assertIn((42, "movie"), repo.overrides)
        self.assertEqual(page.notify_label.get_text(), "Muted")

        page._toggle_notify(None)          # unmute
        self.assertNotIn((42, "movie"), repo.overrides)
        self.assertEqual(page.notify_label.get_text(), "Notify me")

    def test_scope_selected_toggle_opts_in_and_out(self):
        repo = _BellRepo()
        settings = _Settings(scope="selected")
        page = _make_movie_page(repo, settings)
        page._refresh_notify_ui()
        self.assertEqual(page.notify_label.get_text(), "Notify me")

        page._toggle_notify(None)          # opt in
        self.assertIn((42, "movie"), repo.overrides)
        self.assertEqual(page.notify_label.get_text(), "Notifications on")

        page._toggle_notify(None)          # opt out
        self.assertNotIn((42, "movie"), repo.overrides)


if __name__ == "__main__":
    unittest.main()
