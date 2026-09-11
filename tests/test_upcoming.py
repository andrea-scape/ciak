"""Watchlist Upcoming section: movies/episodes airing within 5 days."""

import datetime
import sys
import types
import unittest
from types import SimpleNamespace
from unittest import mock

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw

if "src.config" not in sys.modules:
    _cfg = types.ModuleType("src.config")
    _cfg.APP_ID = "io.github.andrea_scape.ciak.Devel"
    _cfg.APP_VERSION = "0.0.0-test"
    _cfg.APP_NAME = "Ciak"
    sys.modules["src.config"] = _cfg
else:
    _cfg = sys.modules["src.config"]

from src.ui.watchlist_page import WatchlistPage


class _FakeRepo:
    def __init__(self, wl_movies=None, wl_shows=None, watched=None,
                 watched_eps=None, data_version=0):
        self._wl_movies = wl_movies or []
        self._wl_shows = wl_shows or []
        self._watched = watched or set()
        self._watched_eps = watched_eps or set()
        self.data_version = data_version

    def get_watchlist(self, mode=None):
        return self._wl_movies if mode == "movie" else self._wl_shows

    def get_watched_ids(self, media_type):
        return set(self._watched)

    def get_watched_show_ids(self):
        return set()

    def get_ratings(self, media_type):
        return []

    def get_watched_episodes_for_show(self, tmdb_id):
        return set(self._watched_eps)

    def get_watchlist_ids(self):
        return {r["tmdb_id"] for r in self._wl_movies + self._wl_shows}

    def get_media_item(self, tmdb_id):
        return None

    def get_watchlist_with_dates(self, media_type):
        return []


def _wl_movie(tmdb_id, title="Movie", year=None):
    return dict(tmdb_id=tmdb_id, media_type="movie", added_at=1,
                title=title, year=year, poster_url=None, runtime=None,
                imdb_id=None, genres=None)


def _wl_show(tmdb_id, title="Show"):
    return dict(tmdb_id=tmdb_id, media_type="show", added_at=1,
                title=title, year=2024, poster_url=None, runtime=None,
                imdb_id=None, genres=None)


def _iso(d):
    return d.isoformat()


class _FakeMetadata:
    """release_dates: {id: iso}; next_eps: {id: (season, episode, iso)}"""

    def __init__(self, release_dates=None, next_eps=None, episodes=None):
        self.release_dates = release_dates or {}
        self.next_eps = next_eps or {}
        self.episodes = episodes or {}

    def get_movie(self, tmdb_id):
        rd = self.release_dates.get(tmdb_id)
        return SimpleNamespace(release_date=_iso(rd) if rd else None)

    def get_show(self, tmdb_id):
        nxt = self.next_eps.get(tmdb_id)
        if nxt:
            season, episode, date = nxt
            return SimpleNamespace(
                next_episode_air_date=_iso(date),
                next_episode_season=season,
                next_episode_number=episode,
                next_episode_name="Ep",
                next_episode_still=None,
            )
        return SimpleNamespace(next_episode_air_date=None)

    def get_latest_season_episodes(self, tmdb_id):
        out = []
        for season, episode, date in self.episodes.get(tmdb_id, []):
            out.append(SimpleNamespace(
                air_date=_iso(date),
                season_number=season,
                episode_number=episode,
                title="Ep",
                poster_url=None,
            ))
        return out


def _make_page(repo, metadata):
    with mock.patch("gi.repository.GLib.Thread.new"):
        page = WatchlistPage(object(), repo, metadata, None)
    return page


class _CountingMetadata(_FakeMetadata):
    """Counts get_movie calls to prove skips/caching."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.movie_calls = 0
        self.episode_calls = 0

    def get_movie(self, tmdb_id):
        self.movie_calls += 1
        return super().get_movie(tmdb_id)

    def get_latest_season_episodes(self, tmdb_id):
        self.episode_calls += 1
        return super().get_latest_season_episodes(tmdb_id)


def _grid_cards(grid):
    cards = []
    child = grid.get_first_child()
    while child:
        cards.append(child.get_child())
        child = child.get_next_sibling()
    return cards


def _populate_from_fetch(page):
    entries = page._compute_upcoming()
    page._populate_upcoming(page._reload_token, entries)


class UpcomingSectionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            Gtk.init()
            Adw.init()
        except TypeError:
            pass

    def test_section_sits_above_movies_section(self):
        page = _make_page(_FakeRepo(), _FakeMetadata())
        children = []
        child = page.dashboard_box.get_first_child()
        while child:
            children.append(child)
            child = child.get_next_sibling()
        self.assertLess(children.index(page.upcoming_revealer),
                        children.index(page.movies_section[0]))

    def test_history_page_has_no_upcoming_section(self):
        from src.ui.history_page import HistoryPage
        with mock.patch("gi.repository.GLib.Thread.new"):
            page = HistoryPage(object(), _FakeRepo(), _FakeMetadata(), None)
        self.assertFalse(hasattr(page, "upcoming_grid"))
        self.assertFalse(page.upcoming_enabled)

    def test_upcoming_hidden_when_nothing_in_window(self):
        today = datetime.date.today()
        repo = _FakeRepo(wl_movies=[_wl_movie(1)])
        meta = _FakeMetadata(
            release_dates={1: today + datetime.timedelta(days=30)})
        page = _make_page(repo, meta)
        _populate_from_fetch(page)
        self.assertFalse(page.upcoming_section[0].get_visible())
        self.assertEqual(len(page.upcoming_grid.observe_children()), 0)

    def test_past_released_movie_excluded(self):
        today = datetime.date.today()
        repo = _FakeRepo(wl_movies=[_wl_movie(1)])
        meta = _FakeMetadata(release_dates={1: today - datetime.timedelta(days=3)})
        page = _make_page(repo, meta)
        _populate_from_fetch(page)
        self.assertFalse(page.upcoming_section[0].get_visible())
        self.assertEqual(len(page.upcoming_grid.observe_children()), 0)

    def test_unreleased_movie_in_window_included(self):
        today = datetime.date.today()
        repo = _FakeRepo(wl_movies=[_wl_movie(1)])
        meta = _FakeMetadata(release_dates={1: today + datetime.timedelta(days=2)})
        page = _make_page(repo, meta)
        _populate_from_fetch(page)
        self.assertTrue(page.upcoming_section[0].get_visible())
        self.assertEqual(len(page.upcoming_grid.observe_children()), 1)

    def test_watched_movies_excluded(self):
        today = datetime.date.today()
        repo = _FakeRepo(wl_movies=[_wl_movie(1)], watched={1})
        meta = _FakeMetadata(release_dates={1: today})
        page = _make_page(repo, meta)
        _populate_from_fetch(page)
        self.assertFalse(page.upcoming_section[0].get_visible())

    def test_next_episode_card_with_footer(self):
        today = datetime.date.today()
        air = today + datetime.timedelta(days=2)
        repo = _FakeRepo(wl_shows=[_wl_show(10)])
        meta = _FakeMetadata(next_eps={10: (1, 2, air)})
        page = _make_page(repo, meta)
        _populate_from_fetch(page)
        self.assertTrue(page.upcoming_section[0].get_visible())
        card = _grid_cards(page.upcoming_grid)[0]
        texts = []

        def walk(w):
            if isinstance(w, Gtk.Label):
                texts.append(w.get_text())
            c = w.get_first_child()
            while c:
                walk(c)
                c = c.get_next_sibling()

        walk(card)
        expected = f"S01E02 · {air:%a %-d %b}"
        self.assertIn(expected, texts)

    def test_upcoming_uses_show_poster_not_episode_still(self):
        today = datetime.date.today()
        air = today + datetime.timedelta(days=2)
        repo = _FakeRepo(wl_shows=[_wl_show(10)])
        repo._wl_shows[0]["poster_url"] = "http://img/show-poster.jpg"
        meta = _FakeMetadata(episodes={10: [(1, 2, air)]},
                             next_eps={10: (1, 2, air)})

        from src.ui import poster as poster_mod
        loaded = []
        with mock.patch("src.ui.media_card.load_poster",
                        side_effect=lambda url, pic, *a, **k: loaded.append(url)):
            page = _make_page(repo, meta)
            _populate_from_fetch(page)

        self.assertIn("http://img/show-poster.jpg", loaded)
        # the synthesized Episode still must never win
        still_urls = [u for u in loaded if u not in ("http://img/show-poster.jpg",)]
        self.assertEqual(still_urls, [])

    def test_episode_beyond_horizon_excluded(self):
        today = datetime.date.today()
        repo = _FakeRepo(wl_shows=[_wl_show(10)])
        meta = _FakeMetadata(next_eps={10: (1, 2, today + datetime.timedelta(days=6))})
        page = _make_page(repo, meta)
        _populate_from_fetch(page)
        self.assertFalse(page.upcoming_section[0].get_visible())

    def test_sorted_nearest_date_first(self):
        today = datetime.date.today()
        repo = _FakeRepo(
            wl_movies=[_wl_movie(1), _wl_movie(2)],
            wl_shows=[_wl_show(10)],
        )
        meta = _FakeMetadata(
            release_dates={
                1: today + datetime.timedelta(days=4),
                2: today + datetime.timedelta(days=1),
            },
            next_eps={10: (1, 5, today + datetime.timedelta(days=2))},
        )
        page = _make_page(repo, meta)
        entries = page._compute_upcoming()
        dates = [e[0] for e in entries]
        self.assertEqual(dates, sorted(dates))
        self.assertEqual(dates[0], today + datetime.timedelta(days=1))
        self.assertEqual(dates[1], today + datetime.timedelta(days=2))
        self.assertEqual(dates[2], today + datetime.timedelta(days=4))
        _populate_from_fetch(page)
        cards = _grid_cards(page.upcoming_grid)
        self.assertEqual(len(cards), 3)

        texts = []

        def walk_collect(w, acc):
            if isinstance(w, Gtk.Label):
                acc.append(w.get_text())
            c = w.get_first_child()
            while c:
                walk_collect(c, acc)
                c = c.get_next_sibling()

        walk_collect(cards[1], texts)
        self.assertIn(f"S01E05 · {(today + datetime.timedelta(days=2)):%a %-d %b}",
                      texts)

    def test_stale_token_ignored(self):
        today = datetime.date.today()
        repo = _FakeRepo(wl_movies=[_wl_movie(1)])
        meta = _FakeMetadata(release_dates={1: today})
        page = _make_page(repo, meta)
        page._populate_upcoming(page._reload_token + 1,
                                [(today, 0, _wl_movie(1))])
        self.assertFalse(page.upcoming_section[0].get_visible())




class UpcomingSubtitleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            Gtk.init()
            Adw.init()
        except TypeError:
            pass

    def test_movie_card_subtitle_has_air_date(self):
        today = datetime.date.today()
        repo = _FakeRepo(wl_movies=[_wl_movie(1)])
        meta = _FakeMetadata(release_dates={1: today + datetime.timedelta(days=2)})
        page = _make_page(repo, meta)
        _populate_from_fetch(page)
        card = _grid_cards(page.upcoming_grid)[0]
        texts = []

        def walk(w):
            if isinstance(w, Gtk.Label):
                texts.append(w.get_text())
            c = w.get_first_child()
            while c:
                walk(c)
                c = c.get_next_sibling()

        walk(card)
        self.assertIn(f"Movie · {(today + datetime.timedelta(days=2)):%a %-d %b}",
                      texts)
        self.assertNotIn("Movie", [t for t in texts if t != "Movie"
                                   and not t.startswith("Movie · ")])


class SwapSectionsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            Gtk.init()
            Adw.init()
        except TypeError:
            pass

    class _Win:
        def __init__(self, swap):
            self.settings = SimpleNamespace(
                get_boolean=lambda key, _s=swap: swap
            )

    def test_watchlist_shows_before_movies_when_swapped(self):
        with mock.patch("gi.repository.GLib.Thread.new"):
            page = WatchlistPage(self._Win(True), _FakeRepo(),
                                 _FakeMetadata(), None)
        children = []
        child = page.dashboard_box.get_first_child()
        while child:
            children.append(child)
            child = child.get_next_sibling()
        self.assertLess(children.index(page.shows_section[0]),
                        children.index(page.movies_section[0]))

    def test_watchlist_default_order_kept(self):
        with mock.patch("gi.repository.GLib.Thread.new"):
            page = WatchlistPage(self._Win(False), _FakeRepo(),
                                 _FakeMetadata(), None)
        children = []
        child = page.dashboard_box.get_first_child()
        while child:
            children.append(child)
            child = child.get_next_sibling()
        self.assertLess(children.index(page.movies_section[0]),
                        children.index(page.shows_section[0]))

    def test_search_shows_before_movies_when_swapped(self):
        from src.ui.search_page import SearchPage
        with mock.patch("gi.repository.GLib.Thread.new"):
            page = SearchPage(self._Win(True), _FakeRepo(), object(), None)
        children = []
        child = page.dashboard_box.get_first_child()
        while child:
            children.append(child)
            child = child.get_next_sibling()
        self.assertLess(children.index(page.shows_section[0]),
                        children.index(page.movies_section[0]))




class UpcomingPerfTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            Gtk.init()
            Adw.init()
        except TypeError:
            pass

    def test_old_release_year_skips_movie_lookup(self):
        today = datetime.date.today()
        repo = _FakeRepo(wl_movies=[_wl_movie(1, year=1986)])
        meta = _CountingMetadata(release_dates={1: today})
        page = _make_page(repo, meta)
        entries = page._compute_upcoming()
        self.assertEqual(entries, [])
        self.assertEqual(meta.movie_calls, 0)

    def test_current_year_movie_still_looked_up(self):
        today = datetime.date.today()
        repo = _FakeRepo(wl_movies=[_wl_movie(1)])
        meta = _CountingMetadata(
            release_dates={1: today + datetime.timedelta(days=2)})
        page = _make_page(repo, meta)
        entries = page._compute_upcoming()
        self.assertEqual(meta.movie_calls, 1)
        self.assertEqual(len(entries), 1)

    def test_watched_episode_excluded_from_upcoming(self):
        today = datetime.date.today()
        air = today + datetime.timedelta(days=2)
        repo = _FakeRepo(wl_shows=[_wl_show(10)],
                         watched_eps={(1, 2)})
        meta = _FakeMetadata(episodes={10: [(1, 2, air)]},
                             next_eps={10: (1, 2, air)})
        page = _make_page(repo, meta)
        entries = page._compute_upcoming()
        self.assertEqual(entries, [])

    def test_cache_reused_until_version_or_day_changes(self):
        today = datetime.date.today()
        repo = _FakeRepo(wl_movies=[_wl_movie(1)])
        meta = _CountingMetadata(
            release_dates={1: today + datetime.timedelta(days=2)})
        page = _make_page(repo, meta)

        page._load()  # cache empty -> fetch (thread mocked; call manually)
        entries = page._compute_upcoming()
        page._populate_upcoming(page._reload_token, entries)
        page._upcoming_cache = {
            "day": datetime.date.today(),
            "version": 0,
            "entries": entries,
        }
        calls_after_first = meta.movie_calls

        # same day + same version -> served from cache, no new lookups
        page._upcoming_cache = {
            "day": datetime.date.today(),
            "version": 0,
            "entries": [],
        }
        self.assertTrue(page.upcoming_enabled)
        cached = page._upcoming_cache
        self.assertEqual(cached["version"], repo.data_version)
        self.assertEqual(cached["day"], datetime.date.today())
        self.assertEqual(meta.movie_calls, calls_after_first)

        # version bump invalidates the cache
        repo.data_version = 5
        self.assertNotEqual(page._upcoming_version(),
                            page._upcoming_cache["version"])


class DataVersionTest(unittest.TestCase):
    def test_mutations_bump_data_version(self):
        import os
        import tempfile
        from src.data.local.repository import LocalMediaRepository
        with tempfile.TemporaryDirectory() as d:
            repo = LocalMediaRepository(os.path.join(d, "db.sqlite"))
            repo.initialize()
            v0 = repo.data_version
            repo.add_to_watchlist(1, "movie")
            v1 = repo.data_version
            repo.mark_watched(1, "movie")
            v2 = repo.data_version
            repo.mark_unwatched(1, "movie")
            v3 = repo.data_version
            repo.remove_from_watchlist(1, "movie")
            v4 = repo.data_version
            self.assertEqual([v0, v1, v2, v3, v4], [0, 1, 2, 3, 4])

    def test_reads_do_not_bump_version(self):
        import os
        import tempfile
        from src.data.local.repository import LocalMediaRepository
        with tempfile.TemporaryDirectory() as d:
            repo = LocalMediaRepository(os.path.join(d, "db.sqlite"))
            repo.initialize()
            repo.get_watchlist("movie")
            repo.get_watched_ids("movie")
            self.assertEqual(repo.data_version, 0)




class LaunchRevealTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            Gtk.init()
            Adw.init()
        except TypeError:
            pass

    def test_dashboard_prehidden_when_animations_enabled(self):
        from src.ui import anim
        was = anim.animations_enabled()
        anim.set_animations_enabled(True)
        try:
            page = _make_page(_FakeRepo(), _FakeMetadata())
            self.assertFalse(page._revealed)
            self.assertEqual(page.dashboard_box.get_opacity(), 0.0)
        finally:
            anim.set_animations_enabled(was)

    def test_no_prehide_when_animations_disabled(self):
        from src.ui import anim
        was = anim.animations_enabled()
        anim.set_animations_enabled(False)
        try:
            page = _make_page(_FakeRepo(), _FakeMetadata())
            self.assertEqual(page.dashboard_box.get_opacity(), 1.0)
        finally:
            anim.set_animations_enabled(was)

    def test_populate_reveals_page_with_animations_off(self):
        from src.ui import anim
        was = anim.animations_enabled()
        anim.set_animations_enabled(False)
        try:
            today = datetime.date.today()
            repo = _FakeRepo(wl_movies=[_wl_movie(1)])
            meta = _FakeMetadata(release_dates={1: today + datetime.timedelta(days=2)})
            page = _make_page(repo, meta)
            _populate_from_fetch(page)
            self.assertEqual(page.dashboard_box.get_opacity(), 1.0)
        finally:
            anim.set_animations_enabled(was)




class LaunchRevealFlagTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            Gtk.init()
            Adw.init()
        except TypeError:
            pass

    def test_history_prehides_like_watchlist(self):
        from src.ui import anim
        from src.ui.history_page import HistoryPage
        was = anim.animations_enabled()
        anim.set_animations_enabled(True)
        try:
            with mock.patch("gi.repository.GLib.Thread.new"):
                page = HistoryPage(object(), _FakeRepo(), _FakeMetadata(), None)
            self.assertFalse(page._revealed)
            self.assertEqual(page.dashboard_box.get_opacity(), 0.0)
        finally:
            anim.set_animations_enabled(was)

    def test_watchlist_still_prehides(self):
        from src.ui import anim
        was = anim.animations_enabled()
        anim.set_animations_enabled(True)
        try:
            page = _make_page(_FakeRepo(), _FakeMetadata())
            self.assertFalse(page._revealed)
            self.assertEqual(page.dashboard_box.get_opacity(), 0.0)
        finally:
            anim.set_animations_enabled(was)


if __name__ == "__main__":
    unittest.main()


class PageRevealAllPagesTest(unittest.TestCase):
    """Every sidebar page pre-hides at construction and reveals via its
    populate/render path."""

    @classmethod
    def setUpClass(cls):
        try:
            Gtk.init()
            Adw.init()
        except TypeError:
            pass

    def setUp(self):
        from src.ui import anim, poster
        self.anim = anim
        self._was = anim.animations_enabled()
        anim.set_animations_enabled(True)
        poster._pending_loads = 0

    def tearDown(self):
        self.anim.set_animations_enabled(self._was)

    def _settings(self):
        return SimpleNamespace(get_boolean=lambda key: False,
                               get_string=lambda key: "",
                               get_int=lambda key: 0)

    def test_search_prehides_and_reveals_offline_path(self):
        from src.ui.search_page import SearchPage
        page = SearchPage(SimpleNamespace(settings=self._settings()),
                          _FakeRepo(), object(), None)
        self.assertEqual(page.get_opacity(), 0.0)
        self.anim.set_animations_enabled(False)
        page._populate([], [])
        page._apply_late_badges(frozenset())   # unified gate: content + late
        self.assertEqual(page.get_opacity(), 1.0)

    def test_profile_prehides_and_reveals(self):
        from src.ui.profile_page import ProfileGallery
        repo = SimpleNamespace(
            get_stats=lambda: SimpleNamespace(
                movies_watched=0, shows_watched=0, episodes_watched=0),
            get_watchlist_stats=lambda: {"movie_count": 0, "show_count": 0,
                                         "episodes_to_watch": 0,
                                         "total_runtime": 0},
            get_watched_runtime=lambda: 0,
            get_ratings=lambda: [],
            get_watched_list=lambda m=None: [],
        )
        page = ProfileGallery(SimpleNamespace(settings=self._settings()),
                              repo, object(), None)
        self.assertEqual(page.get_opacity(), 0.0)
        self.anim.set_animations_enabled(False)
        stats = repo.get_stats()
        page._populate(page._reload_gen, stats, repo.get_watchlist_stats(),
                       0, [], [])
        self.assertEqual(page.get_opacity(), 1.0)

    def test_calendar_prehides_and_reveals(self):
        from src.ui.calendar_page import CalendarPage
        repo = SimpleNamespace(get_watchlist=lambda mode=None: [])
        page = CalendarPage(SimpleNamespace(settings=self._settings()),
                            repo, object(), None)
        self.assertEqual(page.get_opacity(), 0.0)
        self.anim.set_animations_enabled(False)
        page._render_empty(page._fetch_gen)
        self.assertEqual(page.get_opacity(), 1.0)


if __name__ == "__main__":
    unittest.main()


class LateBadgesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            Gtk.init()
            Adw.init()
        except TypeError:
            pass

    def test_media_card_attaches_item_and_badge_helper(self):
        from src.ui.media_card import make_media_card, add_watched_badge, _find_watched_badge
        item = SimpleNamespace(tmdb_id=7, title="S", media_type="show",
                               year=2024, poster_url=None)
        button = make_media_card(item, None)
        self.assertIs(button._media_item, item)

        box = button.get_child()
        add_watched_badge(box)
        self.assertIsNotNone(_find_watched_badge(box))
        # idempotent
        add_watched_badge(box)
        overlays = 0
        c = box.get_first_child()
        while c:
            if isinstance(c, Gtk.Overlay):
                overlays += 1
            c = c.get_next_sibling()
        self.assertEqual(overlays, 1)
        self.assertIsNotNone(_find_watched_badge(box))

    def test_history_populate_not_blocked_by_late_badges(self):
        from src.ui.history_page import HistoryPage
        from src.ui import watched_state

        class SlowMeta:
            def get_show_seasons(self, i):
                raise AssertionError("network touched during early phase")

            def get_show(self, i):
                raise AssertionError("network touched during early phase")

        rows = [dict(tmdb_id=1, media_type="movie", show_tmdb_id=None,
                     season_number=None, episode_number=None, watched_at=100,
                     title="A", year=2020, poster_url=None, imdb_id=None,
                     runtime=None, collection_id=None, collection_name=None,
                     genres=None)]
        repo = _FakeRepo()
        repo._wl = []

        def get_watched_list(m=None):
            return rows if m in (None, "movie") else []

        repo.get_watched_list = get_watched_list
        with mock.patch("gi.repository.GLib.Thread.new"):
            page = HistoryPage(object(), repo, SlowMeta(), None)

        queued = []
        with mock.patch("gi.repository.GLib.idle_add",
                        side_effect=lambda cb, *a: queued.append((cb, a)) or 123), \
                mock.patch("gi.repository.GLib.timeout_add"), \
                mock.patch.object(watched_state, "caught_up_show_ids",
                                  return_value=frozenset()) as fw:
            page._fetch(page._reload_token, "all")
            self.assertGreaterEqual(len(queued), 1)
        self.assertEqual(queued[0][0].__name__, "_populate")
        fw.assert_called_once()

    def test_apply_late_badges_marks_matching_show_cards(self):
        from src.ui.media_card import make_media_card, _find_watched_badge
        with mock.patch("gi.repository.GLib.Thread.new"):
            page = WatchlistPage(object(), _FakeRepo(), object(), None)
        show = SimpleNamespace(tmdb_id=10, title="Show", media_type="show",
                               year=2024, poster_url=None)
        card = make_media_card(show, None)
        page.shows_grid.append(card)

        page._apply_late_badges(page._reload_token, {10})
        self.assertIsNotNone(_find_watched_badge(card))

    def test_apply_late_badges_stale_token_ignored(self):
        from src.ui.media_card import make_media_card, _find_watched_badge
        with mock.patch("gi.repository.GLib.Thread.new"):
            page = WatchlistPage(object(), _FakeRepo(), object(), None)
        show = SimpleNamespace(tmdb_id=10, title="Show", media_type="show",
                               year=2024, poster_url=None)
        card = make_media_card(show, None)
        page.shows_grid.append(card)
        page._apply_late_badges(page._reload_token + 5, {10})
        self.assertIsNone(_find_watched_badge(card))


if __name__ == "__main__":
    unittest.main()
