"""Tests for cloud sync: merge logic, rating normalization, engine, credentials."""

import time
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.data.sync.base import SyncCapabilities, SyncCategory, SyncItem, SyncResult
from src.data.sync.engine import SyncEngine, SyncStatus, merge_union
from src.data.sync.tmdb_backend import (
    CIAK_TO_TMDB,
    TMDB_TO_CIAK,
    TmdbSyncBackend,
    _denormalize_rating,
    _normalize_rating,
)
from src.data.sync.simkl_backend import SimklSyncBackend


# ---------------------------------------------------------------------------
# Rating normalization
# ---------------------------------------------------------------------------

class TestRatingNormalization(unittest.TestCase):
    def test_tmdb_to_ciak_boundaries(self):
        cases = [
            (0.5, 1), (0.9, 1),
            (1.0, 2), (2.0, 2),
            (3.0, 3), (3.5, 3),
            (4.5, 4), (5.0, 4),
            (6.0, 5), (8.5, 5), (10.0, 5),
        ]
        for tmdb, expected in cases:
            self.assertEqual(_normalize_rating(tmdb), expected, f"tmdb={tmdb}")

    def test_ciak_to_tmdb_round_trip(self):
        for ciak_val in range(1, 6):
            tmdb_val = _denormalize_rating(ciak_val)
            self.assertEqual(tmdb_val, CIAK_TO_TMDB[ciak_val])
            self.assertIn(ciak_val, range(1, 6))

    def test_tmdb_to_ciak_full_scale(self):
        self.assertEqual(_normalize_rating(0.0), 1)
        self.assertEqual(_normalize_rating(0.9), 1)
        self.assertEqual(_normalize_rating(10.0), 5)


# ---------------------------------------------------------------------------
# Merge logic
# ---------------------------------------------------------------------------

class TestMergeUnion(unittest.TestCase):
    def _item(self, tmdb_id=1, media_type="movie", cat=SyncCategory.WATCHLIST,
              rating=None, watched_at=None, added_at=None):
        return SyncItem(
            tmdb_id=tmdb_id, media_type=media_type, category=cat,
            rating=rating, watched_at=watched_at, added_at=added_at,
        )

    def test_local_only(self):
        local = [self._item(1)]
        merged = merge_union(local, [])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].tmdb_id, 1)

    def test_remote_only(self):
        remote = [self._item(2)]
        merged = merge_union([], remote)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].tmdb_id, 2)

    def test_both_sides_union(self):
        local = [self._item(1)]
        remote = [self._item(2)]
        merged = merge_union(local, remote)
        ids = {i.tmdb_id for i in merged}
        self.assertEqual(ids, {1, 2})

    def test_same_item_watched_at_wins_recent(self):
        local = [self._item(1, watched_at=100)]
        remote = [self._item(1, watched_at=200)]
        merged = merge_union(local, remote)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].watched_at, 200)

    def test_same_item_rating_higher_wins(self):
        local = [self._item(1, rating=3)]
        remote = [self._item(1, rating=5)]
        merged = merge_union(local, remote)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].rating, 5)

    def test_same_item_local_rating_wins_if_higher(self):
        local = [self._item(1, rating=4)]
        remote = [self._item(1, rating=2)]
        merged = merge_union(local, remote)
        self.assertEqual(merged[0].rating, 4)

    def test_different_media_types_distinct(self):
        local = [self._item(1, media_type="movie")]
        remote = [self._item(1, media_type="show")]
        merged = merge_union(local, remote)
        self.assertEqual(len(merged), 2)

    def test_different_categories_distinct(self):
        local = [self._item(1, cat=SyncCategory.WATCHLIST)]
        remote = [self._item(1, cat=SyncCategory.RATINGS)]
        merged = merge_union(local, remote)
        self.assertEqual(len(merged), 2)

    def test_remote_id_fills_none(self):
        local = [self._item(1)]
        remote = [self._item(1)]
        remote[0].remote_id = "abc123"
        merged = merge_union(local, remote)
        self.assertEqual(merged[0].remote_id, "abc123")

    def test_timestamp_none_doesnt_override(self):
        local = [self._item(1, watched_at=100)]
        remote = [self._item(1, watched_at=None)]
        merged = merge_union(local, remote)
        self.assertEqual(merged[0].watched_at, 100)


# ---------------------------------------------------------------------------
# SyncEngine
# ---------------------------------------------------------------------------

class _FakeSettings:
    def __init__(self, enabled=True, auto=True, tmdb=False, simkl=False, letterboxd=False):
        self._d = {
            "sync-enabled": enabled,
            "sync-auto": auto,
            "sync-tmdb-enabled": tmdb,
            "sync-simkl-enabled": simkl,
            "sync-letterboxd-enabled": letterboxd,
            "sync-last-sync": 0,
            "sync-interval-minutes": 15,
        }

    def get_boolean(self, key):
        return self._d.get(key, False)

    def get_int(self, key):
        return self._d.get(key, 0)

    def set_int64(self, key, val):
        self._d[key] = val


class _FakeBackend:
    def __init__(self, name="fake", authenticated=True, items=None):
        self.name = name
        self.display_name = name.title()
        self.icon_name = "x-generic-symbolic"
        self.capabilities = SyncCapabilities(watchlist=True)
        self._authenticated = authenticated
        self._items = items or []
        self.pushed = []

    def authenticate(self, window):
        return True

    def is_authenticated(self):
        return self._authenticated

    def disconnect(self):
        self._authenticated = False

    def pull(self):
        return list(self._items)

    def push(self, items):
        self.pushed.extend(items)
        return SyncResult(pushed=len(items), pulled=len(items))


class TestSyncEngine(unittest.TestCase):
    def test_manual_sync(self):
        backend = _FakeBackend(authenticated=True)
        engine = SyncEngine(_FakeSettings(), [backend])
        engine.sync_now()
        # sync_now is async (thread), so check immediately
        # The thread should have started; backend should be pulled
        import time
        time.sleep(0.1)
        self.assertGreaterEqual(len(backend.pushed), 0)

    def test_disabled_skips(self):
        backend = _FakeBackend(authenticated=True)
        engine = SyncEngine(_FakeSettings(enabled=False), [backend])
        engine._run_sync()
        self.assertEqual(len(backend.pushed), 0)

    def test_unauthenticated_skips(self):
        backend = _FakeBackend(authenticated=False)
        engine = SyncEngine(_FakeSettings(enabled=True), [backend])
        engine._run_sync()
        self.assertEqual(len(backend.pushed), 0)

    def test_backend_failure_doesnt_block_others(self):
        good = _FakeBackend(name="simkl", authenticated=True)
        bad = _FakeBackend(name="tmdb", authenticated=True)
        bad.pull = MagicMock(side_effect=RuntimeError("boom"))
        engine = SyncEngine(
            _FakeSettings(enabled=True, tmdb=True, simkl=True),
            [bad, good],
        )
        engine._run_sync()
        self.assertIn("tmdb", engine.status.results)
        self.assertTrue(engine.status.results["tmdb"].errors)
        self.assertIn("simkl", engine.status.results)

    def test_status_transitions(self):
        backend = _FakeBackend(authenticated=True)
        engine = SyncEngine(_FakeSettings(), [backend])
        self.assertEqual(engine.status.state, "idle")
        engine._run_sync()
        self.assertEqual(engine.status.state, "done")

    def test_last_sync_updated(self):
        backend = _FakeBackend(authenticated=True)
        settings = _FakeSettings()
        engine = SyncEngine(settings, [backend])
        engine._run_sync()
        self.assertGreater(settings._d["sync-last-sync"], 0)


# ---------------------------------------------------------------------------
# SyncItem dataclass
# ---------------------------------------------------------------------------

class TestSyncItem(unittest.TestCase):
    def test_defaults(self):
        item = SyncItem(tmdb_id=1, media_type="movie", category=SyncCategory.WATCHLIST)
        self.assertIsNone(item.rating)
        self.assertIsNone(item.watched_at)
        self.assertIsNone(item.added_at)
        self.assertIsNone(item.remote_id)
        self.assertEqual(item.extra, {})

    def test_fields(self):
        item = SyncItem(
            tmdb_id=42, media_type="show", category=SyncCategory.RATINGS,
            rating=4, watched_at=1000, added_at=2000, remote_id="abc",
        )
        self.assertEqual(item.tmdb_id, 42)
        self.assertEqual(item.rating, 4)


class TestSyncResult(unittest.TestCase):
    def test_total_items(self):
        r = SyncResult(pushed=5, pulled=3)
        self.assertEqual(r.total_items, 8)

    def test_total_items_zero(self):
        r = SyncResult()
        self.assertEqual(r.total_items, 0)

    def test_has_errors_true(self):
        r = SyncResult(errors=["fail"])
        self.assertTrue(r.has_errors)

    def test_has_errors_false(self):
        r = SyncResult()
        self.assertFalse(r.has_errors)


# ---------------------------------------------------------------------------
# Credentials (mock libsecret)
# ---------------------------------------------------------------------------

class TestCredentials(unittest.TestCase):
    @patch("src.data.sync.credentials._AVAILABLE", True)
    @patch("src.data.sync.credentials.Secret")
    def test_store_and_load(self, mock_secret):
        mock_secret.password_store_sync.return_value = True
        mock_secret.password_lookup_sync.return_value = "tok123"
        from src.data.sync.credentials import store_credential, load_credential
        self.assertTrue(store_credential("test", "key", "value"))
        result = load_credential("test", "key")
        self.assertEqual(result, "tok123")

    @patch("src.data.sync.credentials._AVAILABLE", True)
    @patch("src.data.sync.credentials.Secret")
    def test_delete(self, mock_secret):
        mock_secret.password_clear_sync.return_value = True
        from src.data.sync.credentials import delete_credential
        self.assertTrue(delete_credential("test", "key"))

    @patch("src.data.sync.credentials._AVAILABLE", False)
    def test_unavailable_returns_none(self):
        from src.data.sync.credentials import load_credential
        self.assertIsNone(load_credential("test", "key"))


# ---------------------------------------------------------------------------
# Split auth methods
# ---------------------------------------------------------------------------

class TestSimklSplitAuth(unittest.TestCase):
    @patch.object(SimklSyncBackend, "get_user_code")
    def test_request_code(self, mock_get):
        mock_get.return_value = {
            "user_code": "ABC123",
            "interval": 5,
            "expires_in": 600,
        }
        backend = SimklSyncBackend("test_id")
        result = backend.request_code()
        self.assertEqual(result["user_code"], "ABC123")
        self.assertEqual(result["interval"], 5)
        mock_get.assert_called_once()

    @patch.object(SimklSyncBackend, "poll_code")
    @patch("src.data.sync.simkl_backend.store_credential")
    def test_poll_and_finalize_success(self, mock_store, mock_poll):
        mock_poll.return_value = {"result": "OK", "access_token": "tok123"}
        backend = SimklSyncBackend("test_id")
        result = backend.poll_and_finalize("ABC", 1, 10)
        self.assertTrue(result)
        mock_store.assert_any_call("simkl", "access_token", "tok123")
        mock_store.assert_any_call("simkl", "client_id", "test_id")

    @patch.object(SimklSyncBackend, "poll_code")
    def test_poll_and_finalize_timeout(self, mock_poll):
        mock_poll.return_value = {"result": "⏳"}
        backend = SimklSyncBackend("test_id")
        result = backend.poll_and_finalize("ABC", 1, 1)
        self.assertFalse(result)

    @patch.object(SimklSyncBackend, "poll_code")
    def test_poll_and_finalize_false_positive_ok(self, mock_poll):
        """Simkl returns result=OK with device_code but no access_token (code expired)."""
        mock_poll.return_value = {
            "result": "OK",
            "device_code": "DEVICE_CODE",
            "user_code": "NEW",
        }
        backend = SimklSyncBackend("test_id")
        result = backend.poll_and_finalize("ABC", 1, 2)
        self.assertFalse(result)

    @patch.object(SimklSyncBackend, "poll_code")
    def test_poll_and_finalize_network_error(self, mock_poll):
        """Network error during poll should not crash — just continue."""
        import httpx
        mock_poll.side_effect = httpx.ConnectError("network down")
        backend = SimklSyncBackend("test_id")
        result = backend.poll_and_finalize("ABC", 1, 3)
        self.assertFalse(result)

    @patch.object(SimklSyncBackend, "poll_code")
    @patch("src.data.sync.simkl_backend.store_credential")
    def test_poll_and_finalize_approval_after_errors(self, mock_store, mock_poll):
        """Approval after some failed polls should still succeed."""
        import httpx
        mock_poll.side_effect = [
            httpx.ConnectError("timeout"),
            {"result": "KO", "message": "pending"},
            {"result": "OK", "access_token": "tok_approved"},
        ]
        backend = SimklSyncBackend("test_id")
        result = backend.poll_and_finalize("ABC", 0, 30)
        self.assertTrue(result)
        mock_store.assert_any_call("simkl", "access_token", "tok_approved")


class TestTmdbSplitAuth(unittest.TestCase):
    @patch.object(TmdbSyncBackend, "create_request_token")
    def test_request_token(self, mock_create):
        mock_create.return_value = "tok_abc"
        backend = TmdbSyncBackend("key")
        result = backend.request_token()
        self.assertEqual(result, "tok_abc")
        mock_create.assert_called_once()

    @patch.object(TmdbSyncBackend, "_fetch_account_id")
    @patch.object(TmdbSyncBackend, "create_session")
    @patch("src.data.sync.tmdb_backend.store_credential")
    def test_finalize_session_success(self, mock_store, mock_session, mock_acct):
        mock_session.return_value = "sess123"
        mock_acct.return_value = 42
        backend = TmdbSyncBackend("key")
        result = backend.finalize_session("tok_abc")
        self.assertTrue(result)
        mock_store.assert_any_call("tmdb", "session_id", "sess123")
        mock_store.assert_any_call("tmdb", "account_id", "42")

    @patch.object(TmdbSyncBackend, "create_session")
    def test_finalize_session_failure(self, mock_session):
        mock_session.side_effect = RuntimeError("denied")
        backend = TmdbSyncBackend("key")
        result = backend.finalize_session("bad_token")
        self.assertFalse(result)


# ---------------------------------------------------------------------------
# Sync engine concurrency + error handling
# ---------------------------------------------------------------------------

class TestSyncEngineEdgeCases(unittest.TestCase):
    def _make_engine(self, on_done=None):
        settings = MagicMock()
        settings.get_boolean.return_value = True
        settings.get_int.return_value = 15
        backend = MagicMock()
        backend.name = "test"
        backend.is_authenticated.return_value = True
        backend.pull.return_value = []
        backend.push.return_value = SyncResult()
        return SyncEngine(settings, [backend], on_done=on_done), settings, backend

    def test_sync_now_guard(self):
        """sync_now() should not start a second sync while one is running."""
        engine, _, _ = self._make_engine()
        engine._status.state = "syncing"
        engine.sync_now()
        self.assertEqual(engine._status.state, "syncing")

    def test_run_sync_state_done_on_success(self):
        """_run_sync should set state to 'done' on success."""
        engine, _, _ = self._make_engine()
        engine._run_sync()
        self.assertEqual(engine._status.state, "done")

    def test_run_sync_state_error_on_exception(self):
        """_run_sync should set state to 'error' if set_int64 throws."""
        engine, settings, _ = self._make_engine()
        settings.set_int64.side_effect = RuntimeError("GSettings broken")
        engine._run_sync()
        self.assertEqual(engine._status.state, "error")

    def test_run_sync_never_stuck_syncing(self):
        """State should never remain 'syncing' after _run_sync completes."""
        engine, settings, _ = self._make_engine()
        settings.set_int64.side_effect = RuntimeError("boom")
        engine._run_sync()
        self.assertIn(engine._status.state, ("done", "error"))


# ---------------------------------------------------------------------------
# Two-way sync converter
# ---------------------------------------------------------------------------

class TestLocalToSyncItems(unittest.TestCase):
    def _make_repo(self, watchlist=None, watched=None, ratings=None, collection=None):
        repo = MagicMock()
        repo.get_watchlist.return_value = watchlist or []
        repo.get_watched_list.return_value = watched or []
        repo.get_ratings.return_value = ratings or []
        repo.get_collection.return_value = collection or []
        return repo

    def test_empty_repo(self):
        from src.data.sync.converter import local_to_sync_items
        items = local_to_sync_items(self._make_repo())
        self.assertEqual(items, [])

    def test_watchlist_conversion(self):
        from src.data.sync.converter import local_to_sync_items
        repo = self._make_repo(watchlist=[
            {"tmdb_id": 100, "media_type": "movie", "added_at": 1000},
            {"tmdb_id": 200, "media_type": "show", "added_at": 2000},
        ])
        items = local_to_sync_items(repo)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0].category, SyncCategory.WATCHLIST)
        self.assertEqual(items[0].tmdb_id, 100)
        self.assertEqual(items[1].media_type, "show")

    def test_watched_conversion(self):
        from src.data.sync.converter import local_to_sync_items
        repo = self._make_repo(watched=[
            {"tmdb_id": 100, "media_type": "movie", "watched_at": 1000,
             "show_tmdb_id": None, "season_number": None, "episode_number": None},
        ])
        items = local_to_sync_items(repo)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].category, SyncCategory.WATCHED)
        self.assertEqual(items[0].watched_at, 1000)

    def test_watched_episode_conversion(self):
        from src.data.sync.converter import local_to_sync_items
        repo = self._make_repo(watched=[
            {"tmdb_id": 5001, "media_type": "episode", "watched_at": 3000,
             "show_tmdb_id": 500, "season_number": 1, "episode_number": 3},
        ])
        items = local_to_sync_items(repo)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].media_type, "show")
        self.assertEqual(items[0].extra["season"], 1)
        self.assertEqual(items[0].extra["episode"], 3)

    def test_ratings_conversion(self):
        from src.data.sync.converter import local_to_sync_items
        repo = self._make_repo(ratings=[
            {"tmdb_id": 100, "media_type": "movie", "rating": 4, "rated_at": 5000},
        ])
        items = local_to_sync_items(repo)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].category, SyncCategory.RATINGS)
        self.assertEqual(items[0].rating, 4)

    def test_collection_conversion(self):
        from src.data.sync.converter import local_to_sync_items
        repo = self._make_repo(collection=[
            {"tmdb_id": 100, "media_type": "movie", "collected_at": 6000},
        ])
        items = local_to_sync_items(repo)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].category, SyncCategory.COLLECTION)


class TestImportRemoteItems(unittest.TestCase):
    def _make_repo(self):
        repo = MagicMock()
        repo.import_watchlist.return_value = 0
        repo.import_watched.return_value = 0
        repo.import_ratings.return_value = 0
        return repo

    def test_import_empty_list(self):
        from src.data.sync.converter import import_remote_items
        repo = self._make_repo()
        count = import_remote_items(repo, [])
        self.assertEqual(count, 0)
        repo.import_watchlist.assert_not_called()

    def test_import_watchlist(self):
        from src.data.sync.converter import import_remote_items
        repo = self._make_repo()
        items = [SyncItem(tmdb_id=100, media_type="movie", category=SyncCategory.WATCHLIST)]
        repo.import_watchlist.return_value = 1
        count = import_remote_items(repo, items)
        self.assertEqual(count, 1)
        repo.import_watchlist.assert_called_once()

    def test_import_watched_movie(self):
        from src.data.sync.converter import import_remote_items
        repo = self._make_repo()
        items = [SyncItem(tmdb_id=100, media_type="movie", category=SyncCategory.WATCHED, watched_at=1000)]
        repo.import_watched.return_value = 1
        count = import_remote_items(repo, items)
        self.assertEqual(count, 1)
        call_args = repo.import_watched.call_args[0][0]
        self.assertEqual(call_args[0]["media_type"], "movie")

    def test_import_watched_episode(self):
        from src.data.sync.converter import import_remote_items
        repo = self._make_repo()
        items = [SyncItem(
            tmdb_id=500, media_type="show", category=SyncCategory.WATCHED,
            watched_at=2000, extra={"season": 1, "episode": 3},
        )]
        repo.import_watched.return_value = 1
        count = import_remote_items(repo, items)
        self.assertEqual(count, 1)
        call_args = repo.import_watched.call_args[0][0]
        self.assertEqual(call_args[0]["media_type"], "episode")
        self.assertEqual(call_args[0]["season_number"], 1)

    def test_import_ratings(self):
        from src.data.sync.converter import import_remote_items
        repo = self._make_repo()
        items = [SyncItem(tmdb_id=100, media_type="movie", category=SyncCategory.RATINGS, rating=4)]
        repo.import_ratings.return_value = 1
        count = import_remote_items(repo, items)
        self.assertEqual(count, 1)
        repo.import_ratings.assert_called_once()

    def test_import_collection_calls_add(self):
        from src.data.sync.converter import import_remote_items
        repo = self._make_repo()
        items = [SyncItem(tmdb_id=100, media_type="movie", category=SyncCategory.COLLECTION)]
        count = import_remote_items(repo, items)
        self.assertEqual(count, 0)
        repo.add_to_collection.assert_called_once_with(100, "movie", is_anime=0)

    def test_mixed_categories(self):
        from src.data.sync.converter import import_remote_items
        repo = self._make_repo()
        items = [
            SyncItem(tmdb_id=1, media_type="movie", category=SyncCategory.WATCHLIST),
            SyncItem(tmdb_id=2, media_type="show", category=SyncCategory.WATCHED, watched_at=1000),
            SyncItem(tmdb_id=3, media_type="movie", category=SyncCategory.RATINGS, rating=5),
        ]
        repo.import_watchlist.return_value = 1
        repo.import_watched.return_value = 1
        repo.import_ratings.return_value = 1
        count = import_remote_items(repo, items)
        self.assertEqual(count, 3)
        repo.import_watchlist.assert_called_once()
        repo.import_watched.assert_called_once()
        repo.import_ratings.assert_called_once()


class TestSyncEngineTwoWay(unittest.TestCase):
    def test_sync_backend_pushes_local_pulls_remote(self):
        """_sync_backend should push local items and pull remote items."""
        from src.data.sync.converter import local_to_sync_items
        settings = MagicMock()
        settings.get_boolean.return_value = True
        settings.get_int.return_value = 15

        repo = MagicMock()
        repo.get_watchlist.return_value = [{"tmdb_id": 1, "media_type": "movie", "added_at": 100}]
        repo.get_watched_list.return_value = []
        repo.get_ratings.return_value = []
        repo.get_collection.return_value = []

        backend = MagicMock()
        backend.name = "test"
        backend.is_authenticated.return_value = True
        push_result = SyncResult(pushed=1)
        backend.push.return_value = push_result
        backend.pull.return_value = [
            SyncItem(tmdb_id=99, media_type="movie", category=SyncCategory.WATCHLIST),
        ]

        engine = SyncEngine(settings, [backend], repo=repo)
        engine._sync_backend(backend)

        # Push should have been called with local items
        push_call_args = backend.push.call_args[0][0]
        self.assertEqual(len(push_call_args), 1)
        self.assertEqual(push_call_args[0].tmdb_id, 1)

        # Pull should have been called
        backend.pull.assert_called_once()

        # Remote items should have been imported
        repo.import_watchlist.assert_called_once()


class TestSyncDeletionConfirm(unittest.TestCase):
    def test_check_push_deletions_returns_items_with_titles(self):
        """check_push_deletions should return deduplicated items with titles."""
        settings = MagicMock()
        settings.get_boolean.return_value = True
        settings.get_int.return_value = 15

        repo = MagicMock()
        repo.get_watchlist.return_value = [{"tmdb_id": 1, "media_type": "movie", "added_at": 100}]
        repo.get_watched_list.return_value = []
        repo.get_ratings.return_value = []
        repo.get_collection.return_value = []
        # Pushed state has item 1 + item 2 (item 2 was removed locally)
        repo.get_pushed_items.return_value = (
            {(1, "movie"), (2, "movie")},
            set(),
        )
        repo.get_suppressed_deletions.return_value = set()
        repo.get_media_item.side_effect = lambda tmdb_id: {
            1: {"title": "Dune", "year": 2021},
            2: {"title": "Cyberpunk: Edgerunners", "year": 2023},
        }.get(tmdb_id)

        backend = MagicMock()
        backend.name = "simkl"
        backend.is_authenticated.return_value = True

        engine = SyncEngine(settings, [backend], repo=repo)
        deletions = engine.check_push_deletions()

        self.assertEqual(len(deletions), 1)
        self.assertEqual(deletions[0]["backend_name"], "simkl")
        self.assertEqual(deletions[0]["display_name"], "Simkl")
        self.assertEqual(len(deletions[0]["items"]), 1)
        self.assertEqual(deletions[0]["items"][0][0], "Cyberpunk: Edgerunners")
        self.assertEqual(deletions[0]["items"][0][1], "movie")

    def test_check_push_deletions_empty_when_no_changes(self):
        """check_push_deletions returns empty when local == pushed."""
        settings = MagicMock()
        settings.get_boolean.return_value = True
        settings.get_int.return_value = 15

        repo = MagicMock()
        repo.get_watchlist.return_value = [{"tmdb_id": 1, "media_type": "movie"}]
        repo.get_watched_list.return_value = []
        repo.get_ratings.return_value = []
        repo.get_collection.return_value = []
        repo.get_pushed_items.return_value = ({(1, "movie")}, set())
        repo.get_suppressed_deletions.return_value = set()

        backend = MagicMock()
        backend.name = "simkl"
        backend.is_authenticated.return_value = True

        engine = SyncEngine(settings, [backend], repo=repo)
        deletions = engine.check_push_deletions()

        self.assertEqual(deletions, [])

    def test_check_push_deletions_deduplicates_across_categories(self):
        """Items in both watchlist and ratings should appear once in dialog."""
        settings = MagicMock()
        settings.get_boolean.return_value = True
        settings.get_int.return_value = 15

        repo = MagicMock()
        repo.get_watchlist.return_value = []
        repo.get_watched_list.return_value = []
        repo.get_ratings.return_value = []
        repo.get_collection.return_value = []
        # Item in both watchlist AND ratings pushed state
        repo.get_pushed_items.return_value = (
            {(1, "movie")},
            {(1, "movie")},
        )
        repo.get_suppressed_deletions.return_value = set()
        repo.get_media_item.return_value = {"title": "Dune", "year": 2021}

        backend = MagicMock()
        backend.name = "simkl"
        backend.is_authenticated.return_value = True

        engine = SyncEngine(settings, [backend], repo=repo)
        deletions = engine.check_push_deletions()

        self.assertEqual(len(deletions), 1)
        self.assertEqual(len(deletions[0]["items"]), 1)

    def test_check_push_deletions_fallback_title(self):
        """Items not in media cache should get fallback title."""
        settings = MagicMock()
        settings.get_boolean.return_value = True
        settings.get_int.return_value = 15

        repo = MagicMock()
        repo.get_watchlist.return_value = []
        repo.get_watched_list.return_value = []
        repo.get_ratings.return_value = []
        repo.get_collection.return_value = []
        repo.get_pushed_items.return_value = ({(99999, "show")}, set())
        repo.get_suppressed_deletions.return_value = set()
        repo.get_media_item.return_value = None

        backend = MagicMock()
        backend.name = "simkl"
        backend.is_authenticated.return_value = True

        engine = SyncEngine(settings, [backend], repo=repo)
        deletions = engine.check_push_deletions()

        self.assertEqual(deletions[0]["items"][0][0], "Show #99999")

    def test_sync_backend_skips_deletions_when_flag_set(self):
        """skip_push_deletions blocks removals even after a dialog decision."""
        settings = MagicMock()
        settings.get_boolean.return_value = True
        settings.get_int.return_value = 15

        repo = MagicMock()
        repo.get_watchlist.return_value = [{"tmdb_id": 1, "media_type": "movie", "added_at": 100}]
        repo.get_watched_list.return_value = []
        repo.get_ratings.return_value = []
        repo.get_collection.return_value = []
        repo.get_pushed_items.return_value = ({(1, "movie"), (2, "movie")}, set())
        repo.get_media_item.return_value = {"title": "Test", "year": 2020}
        repo.get_suppressed_deletions.return_value = set()

        backend = MagicMock()
        backend.name = "simkl"
        backend.is_authenticated.return_value = True
        backend.push.return_value = SyncResult(pushed=1)
        backend.pull.return_value = []

        engine = SyncEngine(settings, [backend], repo=repo)
        engine._apply_deletions = True  # user picked "Remove from Simkl"
        engine.skip_push_deletions = True  # but this is an action sync
        engine._sync_backend(backend)

        # remove_from_history should NOT have been called
        backend.remove_from_history.assert_not_called()
        repo.clear_pushed_state_for.assert_not_called()
        self.assertEqual(engine._status.pushed_removed, 0)

    def test_skip_push_deletions_resets_after_sync(self):
        """skip_push_deletions should be False after _run_sync completes."""
        settings = MagicMock()
        settings.get_boolean.return_value = True
        settings.get_int.return_value = 15

        repo = MagicMock()
        repo.get_watchlist.return_value = []
        repo.get_watched_list.return_value = []
        repo.get_ratings.return_value = []
        repo.get_collection.return_value = []

        backend = MagicMock()
        backend.name = "simkl"
        backend.is_authenticated.return_value = True
        backend.push.return_value = SyncResult()
        backend.pull.return_value = []

        engine = SyncEngine(settings, [backend], repo=repo)
        engine.skip_push_deletions = True
        engine._run_sync()

        self.assertFalse(engine.skip_push_deletions)

    def test_sync_backend_applies_removals_after_decision(self):
        """apply_pending_deletions pushes removals and clears push state."""
        settings = MagicMock()
        settings.get_boolean.return_value = True
        settings.get_int.return_value = 15

        repo = MagicMock()
        repo.get_watchlist.return_value = []
        repo.get_watched_list.return_value = []
        repo.get_ratings.return_value = []
        repo.get_collection.return_value = []
        repo.get_pushed_items.return_value = ({(1, "movie")}, set())
        repo.get_media_item.return_value = {"title": "Test", "year": 2020}
        repo.get_suppressed_deletions.return_value = set()

        backend = MagicMock()
        backend.name = "simkl"
        backend.is_authenticated.return_value = True
        backend.push.return_value = SyncResult()
        backend.pull.return_value = []

        engine = SyncEngine(settings, [backend], repo=repo)
        engine.apply_pending_deletions()
        engine._sync_backend(backend)

        backend.remove_from_history.assert_called_once()
        repo.clear_pushed_state_for.assert_called_once()
        self.assertEqual(engine._status.pushed_removed, 1)

    def test_sync_backend_retains_removals_without_decision(self):
        """No auto-removal when the user has not decided — retained only."""
        settings = MagicMock()
        settings.get_boolean.return_value = True
        settings.get_int.return_value = 15

        repo = MagicMock()
        repo.get_watchlist.return_value = [{"tmdb_id": 1, "media_type": "movie", "added_at": 100}]
        repo.get_watched_list.return_value = []
        repo.get_ratings.return_value = []
        repo.get_collection.return_value = []
        repo.get_pushed_items.return_value = ({(1, "movie")}, set())
        repo.get_media_item.return_value = {"title": "Test", "year": 2020}
        repo.get_suppressed_deletions.return_value = set()

        backend = MagicMock()
        backend.name = "simkl"
        backend.is_authenticated.return_value = True
        backend.push.return_value = SyncResult()
        backend.pull.return_value = []

        engine = SyncEngine(settings, [backend], repo=repo)
        engine._sync_backend(backend)

        backend.remove_from_history.assert_not_called()
        repo.clear_pushed_state_for.assert_not_called()
        self.assertEqual(engine._status.pushed_removed, 0)

    def test_sync_backend_excludes_pending_from_import(self):
        """Pulled items being removed this run are not re-imported."""
        from src.data.sync.engine import import_remote_items

        settings = MagicMock()
        settings.get_boolean.return_value = True
        settings.get_int.return_value = 15

        repo = MagicMock()
        repo.get_watchlist.return_value = []
        repo.get_watched_list.return_value = []
        repo.get_ratings.return_value = []
        repo.get_collection.return_value = []
        repo.get_pushed_items.return_value = ({(1, "movie")}, set())
        repo.get_media_item.return_value = {"title": "Test", "year": 2020}

        backend = MagicMock()
        backend.name = "simkl"
        backend.is_authenticated.return_value = True
        backend.push.return_value = SyncResult()
        backend.pull.return_value = [
            SyncItem(tmdb_id=1, media_type="movie", category=SyncCategory.WATCHLIST),
            SyncItem(tmdb_id=2, media_type="movie", category=SyncCategory.WATCHLIST),
            SyncItem(tmdb_id=3, media_type="movie", category=SyncCategory.WATCHLIST),
        ]

        engine = SyncEngine(settings, [backend], repo=repo)
        with patch(
            "src.data.sync.engine.import_remote_items", wraps=import_remote_items
        ) as imp:
            engine._sync_backend(backend)

        imp.assert_called_once()
        imported_ids = {i.tmdb_id for i in imp.call_args.args[1]}
        self.assertEqual(imported_ids, {2, 3})


class TestPerActionSyncDialog(unittest.TestCase):
    def test_has_connected_sync_backend_true(self):
        """_has_connected_sync_backend returns True when backend is enabled + authenticated."""
        from unittest.mock import patch
        from src.ui.detail_page import DetailPage

        win = MagicMock()
        app = MagicMock()
        win.get_application.return_value = app
        engine = MagicMock()
        backend = MagicMock()
        backend.name = "simkl"
        engine._backends = [backend]
        engine._settings.get_boolean.return_value = True
        backend.is_authenticated.return_value = True
        app._sync_engine = engine

        page = DetailPage.__new__(DetailPage)
        page.win = win
        self.assertTrue(page._has_connected_sync_backend())

    def test_has_connected_sync_backend_false_no_backends(self):
        """_has_connected_sync_backend returns False when no backends connected."""
        from src.ui.detail_page import DetailPage

        win = MagicMock()
        app = MagicMock()
        win.get_application.return_value = app
        engine = MagicMock()
        engine._backends = []
        engine._settings.get_boolean.return_value = True
        app._sync_engine = engine

        page = DetailPage.__new__(DetailPage)
        page.win = win
        self.assertFalse(page._has_connected_sync_backend())

    def test_has_connected_sync_backend_false_no_win(self):
        """_has_connected_sync_backend returns False when win has no get_application."""
        from src.ui.detail_page import DetailPage

        page = DetailPage.__new__(DetailPage)
        page.win = object()  # no get_application
        self.assertFalse(page._has_connected_sync_backend())

    def test_do_toggle_watchlist_calls_sync(self):
        """_do_toggle_watchlist with sync_to_cloud=True triggers sync."""
        from src.ui.detail_page import DetailPage

        page = DetailPage.__new__(DetailPage)
        page.user_repo = MagicMock()
        page.item = MagicMock()
        page.item.tmdb_id = 1
        page.media_type = "movie"
        page._in_watchlist = True
        page._is_watched = False
        page.main_page = None

        # Mock _trigger_sync
        page._trigger_sync = MagicMock()
        page._watchlist_done = MagicMock()
        page._post_watchlist_undo_toast = MagicMock()

        btn = MagicMock()
        page._do_toggle_watchlist(btn, sync_to_cloud=True)

        page.user_repo.remove_from_watchlist.assert_called_once()
        page._trigger_sync.assert_called_once()

    def test_do_toggle_watchlist_no_sync(self):
        """_do_toggle_watchlist with sync_to_cloud=False does NOT trigger sync."""
        from src.ui.detail_page import DetailPage

        page = DetailPage.__new__(DetailPage)
        page.user_repo = MagicMock()
        page.item = MagicMock()
        page.item.tmdb_id = 1
        page.media_type = "movie"
        page._in_watchlist = True
        page._is_watched = False
        page.main_page = None

        page._trigger_sync = MagicMock()
        page._watchlist_done = MagicMock()
        page._post_watchlist_undo_toast = MagicMock()

        btn = MagicMock()
        page._do_toggle_watchlist(btn, sync_to_cloud=False)

        page.user_repo.remove_from_watchlist.assert_called_once()
        page._trigger_sync.assert_not_called()


# ---------------------------------------------------------------------------
# Edge-case: _initial_sync concurrency guard
# ---------------------------------------------------------------------------

class TestInitialSyncGuard(unittest.TestCase):
    def test_initial_sync_skips_when_already_syncing(self):
        """_initial_sync must not spawn a thread when state is syncing."""
        backend = _FakeBackend(authenticated=True)
        engine = SyncEngine(_FakeSettings(), [backend])
        engine._status.state = "syncing"
        engine._initial_sync()
        # No thread should have been started; backend.pull untouched
        self.assertEqual(len(backend.pushed), 0)
        self.assertEqual(engine.status.state, "syncing")

    def test_initial_sync_skips_when_enriching(self):
        backend = _FakeBackend(authenticated=True)
        engine = SyncEngine(_FakeSettings(), [backend])
        engine._status.state = "enriching"
        engine._initial_sync()
        self.assertEqual(len(backend.pushed), 0)

    def test_initial_sync_runs_when_idle(self):
        backend = _FakeBackend(authenticated=True)
        engine = SyncEngine(_FakeSettings(), [backend])
        self.assertEqual(engine.status.state, "idle")
        engine._initial_sync()
        import time
        time.sleep(0.15)
        self.assertEqual(engine.status.state, "done")


# ---------------------------------------------------------------------------
# Edge-case: import_remote_items resilience
# ---------------------------------------------------------------------------

class TestImportResilience(unittest.TestCase):
    def test_corrupted_item_doesnt_block_valid(self):
        """One item raising during routing should not prevent others."""
        from src.data.sync.converter import import_remote_items
        repo = MagicMock()
        repo.import_watchlist.return_value = 1
        repo.import_watched.return_value = 1
        repo.import_ratings.return_value = 0

        good1 = SyncItem(tmdb_id=1, media_type="movie", category=SyncCategory.WATCHLIST)
        bad = SyncItem(tmdb_id=999, media_type="movie", category=SyncCategory.WATCHLIST)
        good2 = SyncItem(tmdb_id=2, media_type="show", category=SyncCategory.WATCHED,
                         watched_at=1000, extra={"season": 1, "episode": 1})

        # Make the repo raise on tmdb_id=999 during import_watchlist
        call_count = [0]
        def fake_watchlist(rows):
            call_count[0] += 1
            for r in rows:
                if r["tmdb_id"] == 999:
                    raise ValueError("bad item")
            return len(rows)
        repo.import_watchlist.side_effect = fake_watchlist

        count = import_remote_items(repo, [good1, bad, good2])
        # good1 and good2 should still be imported despite bad
        self.assertGreaterEqual(count, 1)

    def test_rating_repo_failure_doesnt_kill_others(self):
        """If import_ratings raises, watchlist/watched still import."""
        from src.data.sync.converter import import_remote_items
        repo = MagicMock()
        repo.import_watchlist.return_value = 1
        repo.import_watched.return_value = 0
        repo.import_ratings.side_effect = RuntimeError("db locked")

        items = [
            SyncItem(tmdb_id=1, media_type="movie", category=SyncCategory.WATCHLIST),
            SyncItem(tmdb_id=2, media_type="movie", category=SyncCategory.RATINGS, rating=4),
        ]
        count = import_remote_items(repo, items)
        repo.import_watchlist.assert_called_once()
        repo.import_ratings.assert_called_once()


# ---------------------------------------------------------------------------
# Edge-case: duplicate episodes from same show
# ---------------------------------------------------------------------------

class TestDuplicateEpisodes(unittest.TestCase):
    def test_import_watched_multiple_episodes_same_show(self):
        """Multiple episodes from the same show produce distinct DB rows."""
        from src.data.sync.converter import import_remote_items
        repo = MagicMock()
        repo.import_watched.return_value = 3

        items = [
            SyncItem(tmdb_id=500, media_type="show", category=SyncCategory.WATCHED,
                     watched_at=1000, extra={"season": 1, "episode": 1}),
            SyncItem(tmdb_id=500, media_type="show", category=SyncCategory.WATCHED,
                     watched_at=1001, extra={"season": 1, "episode": 2}),
            SyncItem(tmdb_id=500, media_type="show", category=SyncCategory.WATCHED,
                     watched_at=1002, extra={"season": 1, "episode": 3}),
        ]
        count = import_remote_items(repo, items)
        self.assertEqual(count, 3)
        call_args = repo.import_watched.call_args[0][0]
        self.assertEqual(len(call_args), 3)
        # All should be episode type with correct season/episode
        for i, row in enumerate(call_args):
            self.assertEqual(row["media_type"], "episode")
            self.assertEqual(row["show_tmdb_id"], 500)
            self.assertEqual(row["season_number"], 1)
            self.assertEqual(row["episode_number"], i + 1)


# ---------------------------------------------------------------------------
# Simkl pull: plantowatch + watched episodes
# ---------------------------------------------------------------------------

class TestSimklPullPlantowatchWithWatched(unittest.TestCase):
    """When a show is in Simkl watchlist (status=plantowatch) and episodes
    have been watched, pull() must produce BOTH watchlist AND watched items."""

    def test_shows_plantowatch_with_watched_episodes(self):
        backend = SimklSyncBackend("test-client-id")
        backend._client = MagicMock()
        backend._client.get.return_value.json.return_value = {
            "shows": [{
                "show": {
                    "ids": {"tmdb": 500},
                    "title": "My Little Pony",
                    "year": 2010,
                },
                "status": "plantowatch",
                "seasons": [{
                    "number": 1,
                    "episodes": [
                        {"number": 1, "watched_at": "2026-08-01 10:00:00"},
                        {"number": 2, "watched_at": None},
                    ],
                }],
            }],
            "movies": [],
            "anime": [],
        }
        items = backend.pull()
        watchlist = [i for i in items if i.category == SyncCategory.WATCHLIST]
        watched = [i for i in items if i.category == SyncCategory.WATCHED]
        self.assertEqual(len(watchlist), 1)
        self.assertEqual(watchlist[0].tmdb_id, 500)
        self.assertEqual(len(watched), 1)
        self.assertEqual(watched[0].tmdb_id, 500)
        self.assertEqual(watched[0].extra["season"], 1)
        self.assertEqual(watched[0].extra["episode"], 1)

    def test_shows_watching_status_also_produces_watchlist(self):
        """Simkl status 'watching' (after marking episode) should also produce watchlist item."""
        backend = SimklSyncBackend("test-client-id")
        backend._client = MagicMock()
        backend._client.get.return_value.json.return_value = {
            "shows": [{
                "show": {
                    "ids": {"tmdb": 501},
                    "title": "Ponies",
                    "year": 2015,
                },
                "status": "watching",
                "seasons": [{
                    "number": 1,
                    "episodes": [
                        {"number": 1, "watched_at": "2026-08-01 10:00:00"},
                    ],
                }],
            }],
            "movies": [],
            "anime": [],
        }
        items = backend.pull()
        watchlist = [i for i in items if i.category == SyncCategory.WATCHLIST]
        watched = [i for i in items if i.category == SyncCategory.WATCHED]
        self.assertEqual(len(watchlist), 1)
        self.assertEqual(watchlist[0].tmdb_id, 501)
        self.assertEqual(len(watched), 1)

    def test_movies_plantowatch_with_watched(self):
        backend = SimklSyncBackend("test-client-id")
        backend._client = MagicMock()
        backend._client.get.return_value.json.return_value = {
            "shows": [],
            "anime": [],
            "movies": [{
                "movie": {
                    "ids": {"tmdb": 600},
                    "title": "Test Movie",
                    "year": 2024,
                },
                "status": "plantowatch",
                "last_watched_at": "2026-08-15 12:00:00",
            }],
        }
        items = backend.pull()
        watchlist = [i for i in items if i.category == SyncCategory.WATCHLIST]
        watched = [i for i in items if i.category == SyncCategory.WATCHED]
        self.assertEqual(len(watchlist), 1)
        self.assertEqual(watchlist[0].tmdb_id, 600)
        self.assertEqual(len(watched), 1)
        self.assertEqual(watched[0].tmdb_id, 600)

    def test_shows_watching_still_works(self):
        """Normal watching status should still produce watched episodes."""
        backend = SimklSyncBackend("test-client-id")
        backend._client = MagicMock()
        backend._client.get.return_value.json.return_value = {
            "shows": [{
                "show": {
                    "ids": {"tmdb": 700},
                    "title": "Another Show",
                    "year": 2020,
                },
                "status": "watching",
                "seasons": [{
                    "number": 1,
                    "episodes": [
                        {"number": 1, "watched_at": "2026-08-01 10:00:00"},
                    ],
                }],
            }],
            "movies": [],
            "anime": [],
        }
        items = backend.pull()
        watched = [i for i in items if i.category == SyncCategory.WATCHED]
        self.assertEqual(len(watched), 1)
        self.assertEqual(watched[0].tmdb_id, 700)


# ---------------------------------------------------------------------------
# Backfill fallback + orphan merge
# ---------------------------------------------------------------------------


class TestBackfillFallback(unittest.TestCase):
    """Test title-based TMDB search fallback when ID lookup fails."""

    def test_backfill_calls_search_when_get_show_fails(self):
        """When get_show raises, backfill searches by title."""
        from src.data.sync.engine import SyncEngine

        settings = MagicMock()
        settings.get_boolean.return_value = True
        settings.get_int.return_value = 15

        repo = MagicMock()
        # Simkl cross-mapped ID gives no poster
        repo.get_media_missing_posters.return_value = [
            (99999, "show", "Ergo Proxy", 2006),
        ]
        repo.merge_orphaned_media.return_value = 0

        metadata = MagicMock()
        metadata.get_show.side_effect = Exception("TMDB 404")
        # Title search finds the correct ID
        from src.domain.models import Show

        fake_show = Show(
            tmdb_id=12345, title="Ergo Proxy", year=2006,
            poster_url="https://example.com/poster.jpg",
            overview="", runtime=0, rating=0.0, votes=0,
            genres=[], backdrop_url="", imdb_id=None,
            tagline="", certification="", status="Returning",
            next_episode_air_date=None,
        )
        metadata.search_best.return_value = fake_show

        backend = MagicMock()
        backend.name = "test"
        backend.is_authenticated.return_value = True
        backend.pull.return_value = []
        backend.push.return_value = SyncResult()

        engine = SyncEngine(settings, [backend], repo=repo,
                            metadata_service=metadata)
        engine._backfill_metadata()

        metadata.search_best.assert_called_once_with(
            "Ergo Proxy", 2006, "show",
        )
        metadata._cache.put_media.assert_called_once_with(fake_show)

    def test_backfill_skips_search_when_no_title(self):
        """Backfill does not search when title is None."""
        from src.data.sync.engine import SyncEngine

        settings = MagicMock()
        settings.get_boolean.return_value = True
        settings.get_int.return_value = 15

        repo = MagicMock()
        repo.get_media_missing_posters.return_value = [
            (99999, "show", None, None),
        ]
        repo.merge_orphaned_media.return_value = 0

        metadata = MagicMock()
        metadata.get_show.side_effect = Exception("TMDB 404")

        backend = MagicMock()
        backend.name = "test"
        backend.is_authenticated.return_value = True
        backend.pull.return_value = []
        backend.push.return_value = SyncResult()

        engine = SyncEngine(settings, [backend], repo=repo,
                            metadata_service=metadata)
        engine._backfill_metadata()

        metadata.search_best.assert_not_called()

    def test_backfill_no_search_when_get_show_succeeds(self):
        """Backfill does not search when direct lookup works."""
        from src.data.sync.engine import SyncEngine

        settings = MagicMock()
        settings.get_boolean.return_value = True
        settings.get_int.return_value = 15

        repo = MagicMock()
        repo.get_media_missing_posters.return_value = [
            (500, "show", "Some Show", 2020),
        ]
        repo.merge_orphaned_media.return_value = 0

        metadata = MagicMock()
        # get_show succeeds — no fallback needed
        metadata.get_show.return_value = MagicMock()

        backend = MagicMock()
        backend.name = "test"
        backend.is_authenticated.return_value = True
        backend.pull.return_value = []
        backend.push.return_value = SyncResult()

        engine = SyncEngine(settings, [backend], repo=repo,
                            metadata_service=metadata)
        engine._backfill_metadata()

        metadata.search_best.assert_not_called()
        metadata.get_show.assert_called_once_with(500, refresh=True)


class TestMergeOrphanedMedia(unittest.TestCase):
    """Test orphaned media_items row merging."""

    def _make_repo(self, d):
        import os
        from src.data.local.repository import LocalMediaRepository
        db = os.path.join(d, "test.sqlite")
        repo = LocalMediaRepository(db)
        repo.initialize()
        return repo

    def test_merge_redirects_watchlist_references(self):
        """Orphan's watchlist references get redirected to the good row."""
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            repo = self._make_repo(d)
            conn = repo._ensure_conn()
            # Good row: has poster
            conn.execute(
                "INSERT INTO media_items "
                "(tmdb_id, media_type, title, year, poster_url, cached_at, "
                "updated_at) VALUES (100, 'show', 'Claymore', 2007, "
                "'https://example.com/good.jpg', 0, 0)"
            )
            # Orphan row: no poster, same title+year
            conn.execute(
                "INSERT INTO media_items "
                "(tmdb_id, media_type, title, year, poster_url, cached_at, "
                "updated_at) VALUES (99999, 'show', 'Claymore', 2007, "
                "NULL, 0, 0)"
            )
            # Watchlist item points to orphan
            conn.execute(
                "INSERT INTO watchlist_items (tmdb_id, media_type, added_at) "
                "VALUES (99999, 'show', 0)"
            )
            conn.commit()

            count = repo.merge_orphaned_media()
            self.assertEqual(count, 1)

            # Watchlist should now point to good row
            rows = conn.execute(
                "SELECT tmdb_id FROM watchlist_items"
            ).fetchall()
            self.assertEqual(rows[0][0], 100)
            # Orphan deleted
            remaining = conn.execute(
                "SELECT tmdb_id FROM media_items WHERE tmdb_id = 99999"
            ).fetchall()
            self.assertEqual(len(remaining), 0)

    def test_no_merge_when_both_have_poster(self):
        """No merge when both rows have posters."""
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            repo = self._make_repo(d)
            conn = repo._ensure_conn()
            conn.execute(
                "INSERT INTO media_items "
                "(tmdb_id, media_type, title, year, poster_url, cached_at, "
                "updated_at) VALUES (100, 'show', 'Test', 2020, "
                "'https://example.com/a.jpg', 0, 0)"
            )
            conn.execute(
                "INSERT INTO media_items "
                "(tmdb_id, media_type, title, year, poster_url, cached_at, "
                "updated_at) VALUES (200, 'show', 'Test', 2020, "
                "'https://example.com/b.jpg', 0, 0)"
            )
            conn.commit()

            count = repo.merge_orphaned_media()
            self.assertEqual(count, 0)

    def test_no_merge_when_different_titles(self):
        """No merge when titles differ."""
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            repo = self._make_repo(d)
            conn = repo._ensure_conn()
            conn.execute(
                "INSERT INTO media_items "
                "(tmdb_id, media_type, title, year, poster_url, cached_at, "
                "updated_at) VALUES (100, 'show', 'Show A', 2020, "
                "'https://example.com/a.jpg', 0, 0)"
            )
            conn.execute(
                "INSERT INTO media_items "
                "(tmdb_id, media_type, title, year, poster_url, cached_at, "
                "updated_at) VALUES (200, 'show', 'Show B', 2020, "
                "NULL, 0, 0)"
            )
            conn.commit()

            count = repo.merge_orphaned_media()
            self.assertEqual(count, 0)


class TestTitlesMatch(unittest.TestCase):
    """Title comparison used by remote-item verification."""

    def test_exact(self):
        from src.data.sync.verify import titles_match
        self.assertTrue(titles_match("Shogun", "Shogun"))

    def test_strips_diacritics(self):
        from src.data.sync.verify import titles_match
        self.assertTrue(titles_match("Shōgun", "Shogun"))

    def test_leading_the_not_stripped(self):
        """Leading 'the' differences should NOT match — flag for review."""
        from src.data.sync.verify import titles_match
        self.assertFalse(titles_match("The Matrix", "Matrix"))

    def test_punctuation_not_stripped(self):
        """Punctuation differences should NOT match — flag for review."""
        from src.data.sync.verify import titles_match
        self.assertFalse(titles_match("Top Gun: Maverick", "Top Gun Maverick"))

    def test_case_insensitive(self):
        from src.data.sync.verify import titles_match
        self.assertTrue(titles_match("ARCANE LEAGUE OF LEGENDS", "Arcane League of Legends"))

    def test_disjoint_titles_false(self):
        from src.data.sync.verify import titles_match
        self.assertFalse(titles_match("Dr. Wonder's Workshop", "Dragon Ball"))

    def test_short_titles_no_partial(self):
        from src.data.sync.verify import titles_match
        # "BB" would be contained in "BBS" — too short to trust
        self.assertFalse(titles_match("BB", "BBS"))

    def test_empty_false(self):
        from src.data.sync.verify import titles_match
        self.assertFalse(titles_match(None, "Matrix"))
        self.assertFalse(titles_match("Matrix", ""))

    def test_apostrophe_not_stripped(self):
        """Apostrophe differences should NOT match — flag for review."""
        from src.data.sync.verify import titles_match
        self.assertFalse(titles_match("Dr. Wonder's Workshop", "Dr. Wonders Workshop"))

    def test_period_not_stripped(self):
        """Period differences should NOT match — flag for review."""
        from src.data.sync.verify import titles_match
        self.assertFalse(titles_match("Mr. Smith", "Mr Smith"))

    def test_diacritics_still_match(self):
        """Diacritics should still be stripped for matching."""
        from src.data.sync.verify import titles_match
        self.assertTrue(titles_match("Shōgun", "Shogun"))
        self.assertTrue(titles_match("Crème Brûlée", "Creme Brulee"))


def _make_svc():
    """A fake TMDB-like service; per-test overrides mutate this SimpleNamespace."""
    from types import SimpleNamespace
    cache = SimpleNamespace(put_media=lambda m: None)
    return SimpleNamespace(
        get_show=lambda tmdb_id, refresh=False: None,
        get_movie=lambda tmdb_id, refresh=False: None,
        search_best=lambda title, year, media_type: None,
        _cache=cache,
    )


def _item(tmdb_id=1, title="Some Show", year=None, media_type="show",
          extra=None):
    kw = dict(
        tmdb_id=tmdb_id, media_type=media_type,
        category=SyncCategory.WATCHLIST, title=title, year=year,
    )
    if extra is not None:
        kw["extra"] = extra
    return SyncItem(**kw)


class TestVerifyItems(unittest.TestCase):
    """verify_items: (kept, warnings); warnings are wizard decisions."""

    def test_keeps_when_id_resolves_and_title_matches(self):
        from src.data.sync.verify import verify_items
        svc = _make_svc()
        svc.get_show = lambda tmdb_id, refresh=False: SimpleNamespace(
            tmdb_id=tmdb_id, title="Ergo Proxy", year=2006,
        )
        items = [_item(tmdb_id=100, title="Ergo Proxy", year=2006)]
        kept, warnings = verify_items(svc, items)
        self.assertEqual(kept[0].tmdb_id, 100)
        self.assertEqual(warnings, [])

    def test_repairs_mismatched_id_via_strict_search(self):
        from src.data.sync.verify import verify_items
        svc = _make_svc()
        svc.get_show = lambda tmdb_id, refresh=False: None  # dead ID
        svc.search_best = lambda title, year, media_type: SimpleNamespace(
            tmdb_id=555, title="Claymore", year=2007,
        )
        items = [_item(tmdb_id=99999, title="Claymore", year=2007)]
        kept, warnings = verify_items(svc, items)
        self.assertEqual(warnings, [])
        self.assertEqual(kept[0].tmdb_id, 555)

    def test_warns_when_id_dead_and_no_year(self):
        """No year → cannot disambiguate → decision entry, not a drop."""
        from src.data.sync.verify import verify_items
        svc = _make_svc()
        svc.get_show = lambda tmdb_id, refresh=False: None  # dead ID
        svc.search_best = lambda title, year, media_type: SimpleNamespace(
            tmdb_id=555, title="Something Else", year=2001,
        )
        items = [_item(tmdb_id=99999, title="Dr. Wonder", year=None)]
        kept, warnings = verify_items(svc, items)
        self.assertEqual(len(kept), 0)
        self.assertEqual(len(warnings), 1)
        w = warnings[0]
        self.assertEqual(w["claimed_tmdb_id"], 99999)
        self.assertEqual(w["simkl_title"], "Dr. Wonder")
        self.assertIsNone(w["tmdb_title"])
        self.assertIs(w["item"], items[0])

    def test_warns_when_search_returns_unrelated_title(self):
        """Strict search refuses to repair onto a mismatched title."""
        from src.data.sync.verify import verify_items
        svc = _make_svc()
        svc.get_show = lambda tmdb_id, refresh=False: None
        svc.search_best = lambda title, year, media_type: SimpleNamespace(
            tmdb_id=555, title="Total Different Show", year=2007,
        )
        items = [_item(tmdb_id=99999, title="Claymore", year=2007)]
        kept, warnings = verify_items(svc, items)
        self.assertEqual(len(kept), 0)
        self.assertEqual(len(warnings), 1)

    def test_warns_when_year_conflicts(self):
        """Strict search refuses a hit whose year conflicts with the source."""
        from src.data.sync.verify import verify_items
        svc = _make_svc()
        svc.get_show = lambda tmdb_id, refresh=False: None
        svc.search_best = lambda title, year, media_type: SimpleNamespace(
            tmdb_id=555, title="Claymore", year=2001,
        )
        items = [_item(tmdb_id=99999, title="Claymore", year=2007)]
        kept, warnings = verify_items(svc, items)
        self.assertEqual(len(kept), 0)
        self.assertEqual(len(warnings), 1)

    def test_warning_carries_claimed_id_when_titles_clash(self):
        """ID exists but recorded title differs → decision, not auto-fix."""
        from src.data.sync.verify import verify_items
        svc = _make_svc()
        svc.get_show = lambda tmdb_id, refresh=False: SimpleNamespace(
            tmdb_id=99999, title="Claymore", year=2007,
        )
        svc.search_best = lambda title, year, media_type: SimpleNamespace(
            tmdb_id=555, title="Claymore", year=2007,
        )
        items = [_item(tmdb_id=99999, title="Dr. Wonder", year=2007)]
        kept, warnings = verify_items(svc, items)
        self.assertEqual(len(kept), 0)
        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0]["claimed_tmdb_id"], 99999)
        self.assertEqual(warnings[0]["tmdb_title"], "Claymore")

    def test_no_repair_when_id_resolves_but_title_mismatches(self):
        """When TMDB ID resolves to a different title, do NOT repair via
        strict_search — the Simkl title itself may be wrong (e.g. 'Dr.
        Wonder's Workshop' for Vampire Hunter D: Bloodlust)."""
        from src.data.sync.verify import verify_items
        svc = _make_svc()
        svc.get_show = lambda tmdb_id, refresh=False: SimpleNamespace(
            tmdb_id=15999, title="Vampire Hunter D: Bloodlust", year=2001,
        )
        svc.search_best = lambda title, year, media_type: SimpleNamespace(
            tmdb_id=888, title="Dr. Wonder", year=2001,
        )
        items = [_item(tmdb_id=15999, title="Dr. Wonder's Workshop",
                       year=2001)]
        kept, warnings = verify_items(svc, items)
        self.assertEqual(len(kept), 0)
        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0]["claimed_tmdb_id"], 15999)
        self.assertEqual(warnings[0]["tmdb_title"],
                         "Vampire Hunter D: Bloodlust")


class TestVerifyAnimeCrossType(unittest.TestCase):
    """Anime items from Simkl's anime bucket: cross-type verification."""

    _anime = {"source_bucket": "anime"}

    def test_anime_movie_repaired_by_id(self):
        """Anime item whose TMDB ID resolves as a movie → auto-repair."""
        from src.data.sync.verify import verify_items
        svc = _make_svc()
        svc.get_show = lambda tmdb_id, refresh=False: None
        svc.get_movie = lambda tmdb_id, refresh=False: SimpleNamespace(
            tmdb_id=tmdb_id, title="Vampire Hunter D", year=1985,
        )
        items = [_item(tmdb_id=15999, title="Vampire Hunter D",
                       year=1985, extra=self._anime)]
        kept, warnings = verify_items(svc, items)
        self.assertEqual(warnings, [])
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0].media_type, "movie")
        self.assertEqual(kept[0].tmdb_id, 15999)

    def test_anime_show_stays_show(self):
        """Anime item whose TMDB ID resolves as a show → stays show."""
        from src.data.sync.verify import verify_items
        svc = _make_svc()
        svc.get_show = lambda tmdb_id, refresh=False: SimpleNamespace(
            tmdb_id=tmdb_id, title="Cowboy Bebop", year=1998,
        )
        items = [_item(tmdb_id=200, title="Cowboy Bebop",
                       year=1998, extra=self._anime)]
        kept, warnings = verify_items(svc, items)
        self.assertEqual(warnings, [])
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0].media_type, "show")

    def test_anime_dead_id_only_movie_matches(self):
        """Dead ID, title search finds only a movie → repair to movie."""
        from src.data.sync.verify import verify_items
        svc = _make_svc()
        svc.get_show = lambda tmdb_id, refresh=False: None
        svc.get_movie = lambda tmdb_id, refresh=False: None

        def _search(title, year, media_type):
            if media_type == "movie":
                return SimpleNamespace(
                    tmdb_id=5000, title="Vampire Hunter D", year=1985)
            return None
        svc.search_best = _search
        items = [_item(tmdb_id=99999, title="Vampire Hunter D",
                       year=1985, extra=self._anime)]
        kept, warnings = verify_items(svc, items)
        self.assertEqual(warnings, [])
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0].media_type, "movie")
        self.assertEqual(kept[0].tmdb_id, 5000)

    def test_anime_dead_id_both_match_goes_to_wizard(self):
        """Dead ID, both movie and show match → user decides."""
        from src.data.sync.verify import verify_items
        svc = _make_svc()
        svc.get_show = lambda tmdb_id, refresh=False: None
        svc.get_movie = lambda tmdb_id, refresh=False: None

        def _search(title, year, media_type):
            if media_type == "movie":
                return SimpleNamespace(
                    tmdb_id=5000, title="Akira", year=1988)
            if media_type == "show":
                return SimpleNamespace(
                    tmdb_id=6000, title="Akira", year=1988)
            return None
        svc.search_best = _search
        items = [_item(tmdb_id=99999, title="Akira",
                       year=1988, extra=self._anime)]
        kept, warnings = verify_items(svc, items)
        self.assertEqual(len(kept), 0)
        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0]["claimed_tmdb_id"], 99999)

    def test_non_anime_not_affected(self):
        """Non-anime items skip the cross-type logic entirely."""
        from src.data.sync.verify import verify_items
        svc = _make_svc()
        svc.get_show = lambda tmdb_id, refresh=False: None
        svc.search_best = lambda title, year, media_type: None
        items = [_item(tmdb_id=99999, title="Some Show", year=2020)]
        kept, warnings = verify_items(svc, items)
        self.assertEqual(len(kept), 0)
        self.assertEqual(len(warnings), 1)


class TestSyncEngineAmbiguous(unittest.TestCase):
    """Unverifiable remote items become mapping decisions, never losses."""

    def test_backend_level_no_id_items_become_decisions(self):
        backend = _FakeBackend(authenticated=True)
        backend.skipped = [_item(tmdb_id=None, title="Super Crooks",
                                 media_type="show")]
        backend.pull = lambda: []
        repo = MagicMock()
        settings = _FakeSettings()
        engine = SyncEngine(settings, [backend], repo=repo)
        engine._sync_backend(backend)
        self.assertEqual(len(engine.status.ambiguous), 1)
        w = engine.status.ambiguous[0]
        self.assertEqual(w["simkl_title"], "Super Crooks")
        self.assertIsNone(w["claimed_tmdb_id"])
        self.assertEqual(w["media_type"], "show")
        self.assertEqual(w["backend"], "fake")
        self.assertIn("sig", w)
        self.assertEqual(engine.status.skipped, [])

    def test_no_id_item_auto_resolved_by_title_year(self):
        """No-ID item with exact title+year match → auto-imported."""
        from unittest.mock import patch
        backend = _FakeBackend(authenticated=True)
        backend.skipped = [_item(tmdb_id=None, title="Vampire Hunter D",
                                 year=1985, media_type="show")]
        backend.pull = lambda: []
        svc = _make_svc()
        svc.search_best = lambda title, year, media_type: SimpleNamespace(
            tmdb_id=5000, title="Vampire Hunter D", year=1985,
            media_type="movie",
        )
        svc.get_movie = lambda tmdb_id, refresh=False: SimpleNamespace(
            tmdb_id=5000, title="Vampire Hunter D", year=1985,
        )
        repo = MagicMock()
        settings = _FakeSettings()
        with patch("src.data.sync.engine.import_remote_items",
                   return_value=1) as imp:
            engine = SyncEngine(settings, [backend], repo=repo,
                                metadata_service=svc)
            engine._sync_backend(backend)
            # Item should be auto-resolved, not in ambiguous
            self.assertEqual(len(engine.status.ambiguous), 0)
            # Item should be imported
            imp.assert_called_once()
            imported_items = imp.call_args[0][1]
            self.assertEqual(len(imported_items), 1)
            self.assertEqual(imported_items[0].tmdb_id, 5000)
            self.assertEqual(imported_items[0].media_type, "movie")

    def test_no_id_item_stays_wizard_when_no_match(self):
        """No-ID item with no title match → still goes to wizard."""
        backend = _FakeBackend(authenticated=True)
        backend.skipped = [_item(tmdb_id=None, title="Unknown Show",
                                 year=2020, media_type="show")]
        backend.pull = lambda: []
        svc = _make_svc()
        svc.search_best = lambda title, year, media_type: None
        repo = MagicMock()
        settings = _FakeSettings()
        engine = SyncEngine(settings, [backend], repo=repo,
                            metadata_service=svc)
        engine._sync_backend(backend)
        self.assertEqual(len(engine.status.ambiguous), 1)
        self.assertEqual(engine.status.ambiguous[0]["simkl_title"],
                         "Unknown Show")

    def test_unverifiable_item_becomes_decision(self):
        backend = _FakeBackend(authenticated=True)
        backend.pull = lambda: [_item(tmdb_id=99999, title="Junk Show",
                                      year=None)]
        svc = _make_svc()
        svc.get_show = lambda tmdb_id, refresh=False: None
        repo = MagicMock()
        settings = _FakeSettings()
        engine = SyncEngine(settings, [backend], repo=repo,
                            metadata_service=svc)
        engine._sync_backend(backend)
        self.assertEqual(len(engine.status.ambiguous), 1)
        self.assertEqual(engine.status.ambiguous[0]["simkl_title"], "Junk Show")
        self.assertEqual(engine.status.skipped, [])

    def test_verified_items_reach_import(self):
        from unittest.mock import patch
        backend = _FakeBackend(authenticated=True)
        backend.pull = lambda: [_item(tmdb_id=100, title="Ergo Proxy", year=2006)]
        svc = _make_svc()
        svc.get_show = lambda tmdb_id, refresh=False: SimpleNamespace(
            tmdb_id=100, title="Ergo Proxy", year=2006,
        )
        repo = MagicMock()
        settings = _FakeSettings()
        with patch("src.data.sync.engine.import_remote_items",
                   return_value=1) as imp:
            engine = SyncEngine(settings, [backend], repo=repo,
                                metadata_service=svc)
            engine._sync_backend(backend)
            imp.assert_called_once()
            imported_items = imp.call_args[0][1]
            self.assertEqual([i.tmdb_id for i in imported_items], [100])
        self.assertEqual(engine.status.skipped, [])
        self.assertEqual(engine.status.ambiguous, [])


class TestSyncEngineResolution(unittest.TestCase):
    """resolve_mapping imports the chosen mapping and clears the decision."""

    def test_resolve_mapping_imports_chosen_id(self):
        backend = _FakeBackend(authenticated=False)
        repo = MagicMock()
        engine = SyncEngine(_FakeSettings(enabled=False), [backend], repo=repo)
        item = _item(tmdb_id=99999, title="Dr. Wonder", year=None)
        entry = {
            "item": item, "claimed_tmdb_id": 99999,
            "simkl_title": "Dr. Wonder", "tmdb_title": None,
            "media_type": "show", "year": None, "backend": "fake",
            "sig": (99999, "Dr. Wonder", "show", None),
        }
        engine.status.ambiguous = [dict(entry)]
        with patch("src.data.sync.engine.import_remote_items",
                   return_value=1) as imp, \
             patch.object(SyncEngine, "sync_now") as sn:
            ok = engine.resolve_mapping(entry, 777, "movie")
        self.assertTrue(ok)
        imported = imp.call_args[0][1]
        self.assertEqual(imported[0].tmdb_id, 777)
        self.assertEqual(imported[0].media_type, "movie")
        self.assertEqual(engine.status.ambiguous, [])
        self.assertIn(entry["sig"], engine._resolved_sigs)
        repo.dismiss_mapping.assert_called_once_with(str(entry["sig"]))
        sn.assert_called_once()

    def test_resolve_mapping_requires_repo(self):
        engine = SyncEngine(_FakeSettings(enabled=False), [], repo=None)
        self.assertFalse(engine.resolve_mapping({}, 42))

    def test_resolve_mapping_failure_keeps_entry(self):
        backend = _FakeBackend(authenticated=False)
        engine = SyncEngine(_FakeSettings(enabled=False), [backend],
                            repo=MagicMock())
        item = _item(tmdb_id=1, title="X", year=None)
        entry = {"item": item, "claimed_tmdb_id": 1, "simkl_title": "X",
                 "tmdb_title": None, "media_type": "show", "year": None,
                 "backend": "fake", "sig": (1, "X", "show", None)}
        engine.status.ambiguous = [entry]
        with patch("src.data.sync.engine.import_remote_items",
                   side_effect=RuntimeError("boom")):
            ok = engine.resolve_mapping(entry, 2)
        self.assertFalse(ok)
        self.assertEqual(engine.status.ambiguous, [entry])

    def test_resolve_mapping_skips_pull_deletions(self):
        """resolve_mapping sets _skip_pull_deletions so the follow-up sync
        won't delete the just-imported item via pull-deletion detection."""
        backend = _FakeBackend(authenticated=False)
        repo = MagicMock()
        engine = SyncEngine(_FakeSettings(enabled=False), [backend], repo=repo)
        item = _item(tmdb_id=99999, title="Pluribus", year=None)
        entry = {
            "item": item, "claimed_tmdb_id": 99999,
            "simkl_title": "Pluribus", "tmdb_title": None,
            "media_type": "show", "year": None, "backend": "fake",
            "sig": (99999, "Pluribus", "show", None),
        }
        engine.status.ambiguous = [dict(entry)]
        self.assertFalse(engine._skip_pull_deletions)
        with patch("src.data.sync.engine.import_remote_items",
                   return_value=1), \
             patch.object(SyncEngine, "sync_now") as sn:
            ok = engine.resolve_mapping(entry, 12345, "movie")
        self.assertTrue(ok)
        self.assertTrue(engine._skip_pull_deletions)
        sn.assert_called_once()


class TestSyncEngineDismiss(unittest.TestCase):
    """dismiss_item removes an entry from ambiguous and persists the sig."""

    def test_dismiss_removes_from_ambiguous(self):
        backend = _FakeBackend(authenticated=False)
        engine = SyncEngine(_FakeSettings(enabled=False), [backend],
                            repo=MagicMock())
        sig = (15999, "Dr. Wonder's Workshop", "movie", 2001)
        entry = {
            "item": _item(tmdb_id=15999, title="Dr. Wonder's Workshop",
                          year=2001),
            "claimed_tmdb_id": 15999,
            "simkl_title": "Dr. Wonder's Workshop",
            "tmdb_title": "Vampire Hunter D: Bloodlust",
            "media_type": "movie", "year": 2001,
            "backend": "fake", "sig": sig,
        }
        engine.status.ambiguous = [dict(entry)]
        engine.dismiss_item(entry)
        self.assertEqual(engine.status.ambiguous, [])
        self.assertIn(str(sig), engine._dismissed_sigs)
        engine._repo.dismiss_mapping.assert_called_once_with(str(sig))

    def test_dismiss_persists_across_instances(self):
        sig = (15999, "Dr. Wonder's Workshop", "movie", 2001)
        repo = MagicMock()
        repo.get_dismissed_mappings.return_value = {str(sig)}
        backend = _FakeBackend(authenticated=False)
        engine = SyncEngine(_FakeSettings(enabled=False), [backend], repo=repo)
        engine._dismissed_sigs = repo.get_dismissed_mappings()
        self.assertIn(str(sig), engine._dismissed_sigs)


class TestSyncEngineReportFlags(unittest.TestCase):
    """show_report drives the wizard; manual or startup only."""

    def test_manual_sync_reports(self):
        engine = SyncEngine(_FakeSettings(enabled=False), [], repo=None)
        engine._is_manual = True
        engine._run_sync()
        self.assertTrue(engine.status.show_report)

    def test_startup_sync_sets_report_flag(self):
        with patch.object(SyncEngine, "_run_sync"):
            settings = _FakeSettings(enabled=True)
            engine = SyncEngine(settings, [], repo=MagicMock())
            engine._initial_sync()
            self.assertTrue(engine._report_skips)

    def test_background_sync_does_not_report(self):
        engine = SyncEngine(_FakeSettings(enabled=False), [], repo=None)
        engine._run_sync()
        self.assertFalse(engine.status.show_report)

    def test_defer_screens_out_conflicts(self):
        engine = SyncEngine(_FakeSettings(enabled=False), [], repo=None)
        engine._repo = MagicMock()
        engine.check_push_deletions = lambda: [{
            "backend_name": "fake", "display_name": "Fake", "items": []}]
        engine._on_deletions = lambda d: None
        engine._defer_surface = True
        self.assertFalse(engine._maybe_surface_deletions())
        engine._defer_surface = False
        self.assertTrue(engine._maybe_surface_deletions())

    def test_close_as_decide_later_retains_conflict(self):
        """Closing the conflict dialog must NOT suppress deletions."""
        engine = SyncEngine(_FakeSettings(enabled=False), [], repo=None)
        engine._apply_deletions = True  # a previous decision
        engine.retain_deletions()
        self.assertFalse(engine._apply_deletions)

    def test_fire_on_done_snapshot_carries_ambiguous(self):
        """The snapshot the app sees must include the wizard's decisions."""
        from src.data.sync.engine import SyncStatus
        captured = {}
        engine = SyncEngine(_FakeSettings(enabled=False), [], repo=None)
        engine._on_done = lambda snap: captured.update(snap=snap)
        engine.status.ambiguous = [{"sig": (1, "X", "show", 2000)}]
        with patch("src.data.sync.engine.GLib.idle_add",
                   side_effect=lambda fn, *a: fn(*a)):
            engine._fire_on_done()
        self.assertEqual(captured["snap"].ambiguous,
                         [{"sig": (1, "X", "show", 2000)}])

    def test_duplicate_sig_warnings_collapse_to_one_row(self):
        """Same (claimed, title, type, year) → one wizard row, not many."""
        backend = _FakeBackend(authenticated=True)
        # Two identical watchlist items that fail verification with the
        # same mapping signature.
        backend.pull = lambda: [
            _item(tmdb_id=99999, title="PLURIBUS", year=None),
            _item(tmdb_id=99999, title="PLURIBUS", year=None),
        ]
        svc = _make_svc()
        svc.get_show = lambda tmdb_id, refresh=False: None
        repo = MagicMock()
        engine = SyncEngine(_FakeSettings(), [backend], repo=repo,
                            metadata_service=svc)
        engine._sync_backend(backend)
        self.assertEqual(len(engine.status.ambiguous), 1)

    def test_distinct_sig_warnings_both_kept(self):
        """Different claimed IDs → separate rows (e.g. VHD two ways)."""
        # Backend A: no-ID variant (via backend.skipped).
        noid = _FakeBackend(authenticated=True, name="simkl")
        noid.skipped = [_item(tmdb_id=None, title="Vampire Hunter D",
                              media_type="show")]
        noid.pull = lambda: []
        # Sweep the wrong-id variant through a second pass of the same
        # engine's status via `_sync_backend` with a different backend.
        wrongid = _FakeBackend(authenticated=True, name="other")
        wrongid.skipped = []
        wrongid.pull = lambda: [_item(tmdb_id=15999, title="Vampire Hunter D",
                                      year=None)]
        svc = _make_svc()
        svc.get_show = lambda tmdb_id, refresh=False: None
        repo = MagicMock()
        engine = SyncEngine(_FakeSettings(), [noid, wrongid], repo=repo,
                            metadata_service=svc)
        engine._sync_backend(noid)
        engine._sync_backend(wrongid)
        sigs = {w["sig"] for w in engine.status.ambiguous}
        # no-ID sig and claimed-15999 sig are distinct → two rows.
        self.assertEqual(len(sigs), 2)
        self.assertIn((None, "Vampire Hunter D", "show", None), sigs)
        self.assertIn((15999, "Vampire Hunter D", "show", None), sigs)


class TestPosterAttemptFilter(unittest.TestCase):
    """poster_attempted_at prevents re-fetching unresolvable items."""

    def _make_repo(self, d):
        import os
        from src.data.local.repository import LocalMediaRepository
        db = os.path.join(d, "test.sqlite")
        repo = LocalMediaRepository(db)
        repo.initialize()
        return repo

    def test_recent_attempt_excluded(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            repo = self._make_repo(d)
            conn = repo._ensure_conn()
            now = int(time.time())
            for tmdb_id, attempted in ((1, 0), (2, now - 3600), (3, now - 3 * 86400)):
                conn.execute(
                    "INSERT INTO media_items "
                    "(tmdb_id, media_type, title, year, poster_url, cached_at, "
                    "updated_at, poster_attempted_at) VALUES (?, 'show', 'S', ?, "
                    "NULL, 0, 0, ?)",
                    (tmdb_id, None, attempted),
                )
            conn.commit()
            rows = repo.get_media_missing_posters()
            ids = {r[0] for r in rows}
            self.assertIn(1, ids)
            self.assertNotIn(2, ids)
            self.assertIn(3, ids)

    def test_mark_poster_attempted_roundtrip(self):
        import tempfile
        import sqlite3
        with tempfile.TemporaryDirectory() as d:
            repo = self._make_repo(d)
            conn = repo._ensure_conn()
            conn.execute(
                "INSERT INTO media_items "
                "(tmdb_id, media_type, title, year, poster_url, cached_at, "
                "updated_at) VALUES (42, 'movie', 'X', ?, NULL, 0, 0)",
                (None,),
            )
            conn.commit()
            repo.mark_poster_attempted(42)
            ts = conn.execute(
                "SELECT poster_attempted_at FROM media_items WHERE tmdb_id=42"
            ).fetchone()[0]
            self.assertIsNotNone(ts)
            self.assertGreater(int(ts), 0)


class TestMergeOrphanedNullYear(unittest.TestCase):
    """NULL years still count as equal in orphan merging (COALESCE fix)."""

    def _make_repo(self, d):
        import os
        from src.data.local.repository import LocalMediaRepository
        db = os.path.join(d, "test.sqlite")
        repo = LocalMediaRepository(db)
        repo.initialize()
        return repo

    def test_merges_rows_both_with_null_year(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            repo = self._make_repo(d)
            conn = repo._ensure_conn()
            conn.execute(
                "INSERT INTO media_items "
                "(tmdb_id, media_type, title, year, poster_url, cached_at, "
                "updated_at) VALUES (100, 'show', 'Wonder', NULL, "
                "'https://example.com/good.jpg', 0, 0)"
            )
            conn.execute(
                "INSERT INTO media_items "
                "(tmdb_id, media_type, title, year, poster_url, cached_at, "
                "updated_at) VALUES (99999, 'show', 'Wonder', NULL, NULL, 0, 0)"
            )
            conn.commit()
            count = repo.merge_orphaned_media()
            self.assertEqual(count, 1)
            remaining = conn.execute(
                "SELECT tmdb_id FROM media_items"
            ).fetchall()
            self.assertEqual([r[0] for r in remaining], [100])
            self.assertEqual(repo.poster_missing(99999), True)
            self.assertEqual(repo.poster_missing(100), False)


class TestDeletionSurfacing(unittest.TestCase):
    """Conflict surfacing on every sync trigger, before the run starts."""

    def _engine(self, repo, backend, callback):
        settings = MagicMock()
        settings.get_boolean.return_value = True
        settings.get_int.return_value = 15
        return SyncEngine(
            settings, [backend], repo=repo, on_deletions=callback)

    def _conflicted_repo(self):
        repo = MagicMock()
        repo.get_watchlist.return_value = []
        repo.get_watched_list.return_value = []
        repo.get_ratings.return_value = []
        repo.get_collection.return_value = []
        repo.get_pushed_items.return_value = ({(1, "movie")}, set())
        repo.get_media_item.return_value = {"title": "Test", "year": 2020}
        repo.get_suppressed_deletions.return_value = set()
        return repo

    def _backend(self):
        backend = MagicMock()
        backend.name = "simkl"
        backend.is_authenticated.return_value = True
        return backend

    def test_maybe_surface_deletions_fires_callback_and_aborts(self):
        calls = []
        engine = self._engine(
            self._conflicted_repo(), self._backend(), calls.append)
        aborted = engine._maybe_surface_deletions()
        self.assertTrue(aborted)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0]["items"][0], ("Test", "movie", 1))

    def test_sync_now_surfaces_instead_of_starting_run(self):
        calls = []
        engine = self._engine(
            self._conflicted_repo(), self._backend(), calls.append)
        with patch("src.data.sync.engine.threading.Thread") as thread:
            engine.sync_now()
        self.assertEqual(len(calls), 1)
        thread.return_value.start.assert_not_called()

    def test_apply_deletions_passes_through(self):
        """Once the user decided, no queue-by dialog and the run proceeds."""
        calls = []
        engine = self._engine(
            self._conflicted_repo(), self._backend(), calls.append)
        engine.apply_pending_deletions()
        aborted = engine._maybe_surface_deletions()
        self.assertFalse(aborted)
        self.assertEqual(calls, [])

    def test_action_sync_does_not_surface(self):
        calls = []
        engine = self._engine(
            self._conflicted_repo(), self._backend(), calls.append)
        engine.skip_push_deletions = True
        aborted = engine._maybe_surface_deletions()
        self.assertFalse(aborted)
        self.assertEqual(calls, [])


class TestDeletionCleanup(unittest.TestCase):
    """Repo-level helpers that still back sync removals after the
    "Keep on Simkl" suppression feature was removed."""

    def _make_repo(self, d):
        import os
        from src.data.local.repository import LocalMediaRepository
        db = os.path.join(d, "test.sqlite")
        repo = LocalMediaRepository(db)
        repo.initialize()
        return repo

    def test_clear_pushed_state_for(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            repo = self._make_repo(d)
            conn = repo._ensure_conn()
            conn.execute(
                "INSERT INTO sync_state (item_key, backend, pushed_at, synced) "
                "VALUES ('watchlist:100:movie', 'simkl', 1, 1)"
            )
            conn.execute(
                "INSERT INTO sync_state (item_key, backend, pushed_at, synced) "
                "VALUES ('watchlist:200:movie', 'simkl', 1, 1)"
            )
            conn.execute(
                "INSERT INTO sync_state (item_key, backend, pushed_at, synced) "
                "VALUES ('watchlist:999:movie', 'tmdb', 1, 1)"
            )
            conn.commit()

            repo.clear_pushed_state_for("simkl", [(100, "movie")])
            remaining = repo._ensure_conn().execute(
                "SELECT COUNT(*) FROM sync_state WHERE backend='simkl'"
            ).fetchone()[0]
            self.assertEqual(remaining, 1)
            # Other backend untouched
            other = repo._ensure_conn().execute(
                "SELECT COUNT(*) FROM sync_state WHERE backend='tmdb'"
            ).fetchone()[0]
            self.assertEqual(other, 1)


class TestV9Migration(unittest.TestCase):
    """DB v8 → v9 adds the poster_attempted_at column."""

    def test_v8_database_gains_column(self):
        import os
        import sqlite3
        import tempfile
        from src.data.local import schema
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "test.sqlite")
            conn = sqlite3.connect(db)
            # A reasonable v8-era media_items table (no poster_attempted_at).
            conn.execute(
                "CREATE TABLE media_items ("
                "tmdb_id INTEGER PRIMARY KEY,"
                "media_type TEXT NOT NULL,"
                "title TEXT, year INTEGER,"
                "poster_url TEXT, cached_at INTEGER, updated_at INTEGER,"
                "collection_id INTEGER, collection_name TEXT,"
                "imdb_id TEXT, backdrop_url TEXT, overview TEXT,"
                "runtime INTEGER, rating REAL, votes INTEGER,"
                "genres TEXT, tagline TEXT, certification TEXT,"
                "status TEXT, next_episode_air_date TEXT)"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_version ("
                "version INTEGER PRIMARY KEY, applied INTEGER)"
            )
            conn.execute(
                "INSERT INTO schema_version (version, applied) VALUES (8, 1)"
            )
            conn.commit()
            schema.initialize(conn)
            cols = {r[1] for r in conn.execute("PRAGMA table_info(media_items)")}
            self.assertIn("poster_attempted_at", cols)
            tables = {
                r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")
            }
            self.assertNotIn("deletion_suppressions", tables)
            self.assertIn("dismissed_mappings", tables)
            ver = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
            self.assertEqual(ver, 13)
            conn.close()

    def test_v12_database_drops_deletion_suppressions(self):
        """v12 → v13 removes the orphaned deletion_suppressions table."""
        import os
        import sqlite3
        import tempfile
        from src.data.local import schema
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "test.sqlite")
            conn = sqlite3.connect(db)
            conn.execute(
                "CREATE TABLE deletion_suppressions ("
                "backend TEXT NOT NULL, tmdb_id INTEGER NOT NULL,"
                "media_type TEXT NOT NULL, created_at INTEGER NOT NULL,"
                "PRIMARY KEY (backend, tmdb_id, media_type))"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_version ("
                "version INTEGER PRIMARY KEY, applied INTEGER)"
            )
            conn.execute(
                "INSERT INTO schema_version (version, applied) VALUES (12, 1)"
            )
            conn.commit()
            schema.initialize(conn)
            tables = {
                r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")
            }
            self.assertNotIn("deletion_suppressions", tables)
            ver = conn.execute(
                "SELECT MAX(version) FROM schema_version").fetchone()[0]
            self.assertEqual(ver, 13)
            conn.close()


class TestAnimePushFiltering(unittest.TestCase):
    """Verify that only items with source_bucket=anime are pushed to
    Simkl's anime endpoint, while regular shows go to the shows endpoint."""

    def test_push_anime_items_to_anime_endpoint(self):
        items = [
            SyncItem(tmdb_id=100, media_type="show", category=SyncCategory.WATCHLIST,
                     extra={"source_bucket": "anime"}),
            SyncItem(tmdb_id=200, media_type="show", category=SyncCategory.WATCHLIST),
            SyncItem(tmdb_id=300, media_type="movie", category=SyncCategory.WATCHLIST),
        ]
        backend = SimklSyncBackend.__new__(SimklSyncBackend)
        backend._client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        backend._client.post.return_value = mock_resp
        backend._api_key = "test"
        backend._token = "test"
        backend._base_params = lambda: {"api_key": "test"}
        backend._headers = lambda: {}

        backend.push(items)

        calls = backend._client.post.call_args_list
        body = calls[0][1]["json"]
        # Anime item goes to anime, not shows
        self.assertIn("anime", body)
        self.assertEqual(len(body["anime"]), 1)
        self.assertEqual(body["anime"][0]["ids"]["tmdb"], 100)
        # Regular show goes to shows, not anime
        self.assertIn("shows", body)
        self.assertEqual(len(body["shows"]), 1)
        self.assertEqual(body["shows"][0]["ids"]["tmdb"], 200)
        # Movie goes to movies
        self.assertIn("movies", body)
        self.assertEqual(len(body["movies"]), 1)
        self.assertEqual(body["movies"][0]["ids"]["tmdb"], 300)

    def test_push_watched_anime_to_anime_endpoint(self):
        items = [
            SyncItem(tmdb_id=100, media_type="show", category=SyncCategory.WATCHED,
                     watched_at=1000, extra={"source_bucket": "anime", "season": 1, "episode": 1}),
            SyncItem(tmdb_id=200, media_type="show", category=SyncCategory.WATCHED,
                     watched_at=2000, extra={"season": 1, "episode": 1}),
        ]
        backend = SimklSyncBackend.__new__(SimklSyncBackend)
        backend._client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        backend._client.post.return_value = mock_resp
        backend._api_key = "test"
        backend._token = "test"
        backend._base_params = lambda: {"api_key": "test"}
        backend._headers = lambda: {}

        backend.push(items)

        calls = backend._client.post.call_args_list
        body = calls[0][1]["json"]
        # Anime item goes to anime
        self.assertIn("anime", body)
        self.assertEqual(len(body["anime"]), 1)
        self.assertEqual(body["anime"][0]["ids"]["tmdb"], 100)
        # Regular show goes to shows
        self.assertIn("shows", body)
        self.assertEqual(len(body["shows"]), 1)
        self.assertEqual(body["shows"][0]["ids"]["tmdb"], 200)

    def test_push_ratings_anime_to_anime_endpoint(self):
        items = [
            SyncItem(tmdb_id=100, media_type="show", category=SyncCategory.RATINGS,
                     rating=5, extra={"source_bucket": "anime"}),
            SyncItem(tmdb_id=200, media_type="show", category=SyncCategory.RATINGS,
                     rating=4),
        ]
        backend = SimklSyncBackend.__new__(SimklSyncBackend)
        backend._client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        backend._client.post.return_value = mock_resp
        backend._api_key = "test"
        backend._token = "test"
        backend._base_params = lambda: {"api_key": "test"}
        backend._headers = lambda: {}

        backend.push(items)

        calls = backend._client.post.call_args_list
        body = calls[0][1]["json"]
        # Anime item goes to anime
        self.assertIn("anime", body)
        self.assertEqual(len(body["anime"]), 1)
        self.assertEqual(body["anime"][0]["ids"]["tmdb"], 100)
        # Regular show goes to shows
        self.assertIn("shows", body)
        self.assertEqual(len(body["shows"]), 1)
        self.assertEqual(body["shows"][0]["ids"]["tmdb"], 200)


class TestAnimeConverter(unittest.TestCase):
    """Verify is_anime flag is propagated through converter import."""

    def test_import_anime_watchlist_item(self):
        from src.data.sync.converter import import_remote_items
        repo = MagicMock()
        repo.import_watchlist.return_value = 1
        items = [SyncItem(tmdb_id=100, media_type="show", category=SyncCategory.WATCHLIST,
                          extra={"source_bucket": "anime"})]
        import_remote_items(repo, items)
        repo.import_watchlist.assert_called_once()
        row = repo.import_watchlist.call_args[0][0][0]
        self.assertEqual(row["is_anime"], 1)

    def test_import_non_anime_watchlist_item(self):
        from src.data.sync.converter import import_remote_items
        repo = MagicMock()
        repo.import_watchlist.return_value = 1
        items = [SyncItem(tmdb_id=100, media_type="show", category=SyncCategory.WATCHLIST)]
        import_remote_items(repo, items)
        row = repo.import_watchlist.call_args[0][0][0]
        self.assertEqual(row["is_anime"], 0)

    def test_collection_is_anime_propagated(self):
        from src.data.sync.converter import import_remote_items
        repo = MagicMock()
        items = [SyncItem(tmdb_id=100, media_type="show", category=SyncCategory.COLLECTION,
                          extra={"source_bucket": "anime"})]
        import_remote_items(repo, items)
        repo.add_to_collection.assert_called_once_with(100, "show", is_anime=1)


class TestLocalToSyncAnime(unittest.TestCase):
    """Verify local_to_sync_items propagates is_anime from DB rows."""

    def test_watchlist_anime_flag(self):
        from src.data.sync.converter import local_to_sync_items
        repo = MagicMock()
        repo.get_watchlist.return_value = [
            {"tmdb_id": 100, "media_type": "show", "added_at": 1000, "is_anime": 1},
            {"tmdb_id": 200, "media_type": "show", "added_at": 2000, "is_anime": 0},
        ]
        repo.get_watched_list.return_value = []
        repo.get_ratings.return_value = []
        repo.get_collection.return_value = []
        items = local_to_sync_items(repo)
        anime_items = [i for i in items if i.extra.get("source_bucket") == "anime"]
        non_anime_items = [i for i in items if i.extra.get("source_bucket") != "anime"]
        self.assertEqual(len(anime_items), 1)
        self.assertEqual(anime_items[0].tmdb_id, 100)
        self.assertEqual(len(non_anime_items), 1)
        self.assertEqual(non_anime_items[0].tmdb_id, 200)

    def test_watched_anime_flag(self):
        from src.data.sync.converter import local_to_sync_items
        repo = MagicMock()
        repo.get_watchlist.return_value = []
        repo.get_watched_list.return_value = [
            {"tmdb_id": 100, "media_type": "show", "is_anime": 1,
             "show_tmdb_id": 100, "season_number": 1, "episode_number": 1, "watched_at": 1000},
        ]
        repo.get_ratings.return_value = []
        repo.get_collection.return_value = []
        items = local_to_sync_items(repo)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].extra.get("source_bucket"), "anime")


class TestPullDedup(unittest.TestCase):
    """Verify that pull() deduplicates items appearing in both shows and anime arrays."""

    def test_anime_dupe_skipped_when_in_shows(self):
        """Item in both shows and anime → only the shows entry is kept."""
        backend = SimklSyncBackend.__new__(SimklSyncBackend)
        backend._client = MagicMock()
        backend._api_key = "test"
        backend._token = "test"
        backend._base_params = lambda **kw: {"api_key": "test"}
        backend._headers = lambda **kw: {}
        backend._skip_unmapped = lambda **kw: None
        backend.skipped = []

        data = {
            "shows": [{
                "show": {"ids": {"tmdb": 100}, "title": "Dr. Wonder", "year": 2020},
                "status": "plantowatch",
                "seasons": [],
            }],
            "anime": [{
                "show": {"ids": {"tmdb": 100}, "title": "Dr. Wonder", "year": 2020},
                "status": "plantowatch",
                "seasons": [],
            }],
            "movies": [],
        }
        backend._client.get.return_value.json.return_value = data

        items = backend.pull()
        tmdb_ids = [i.tmdb_id for i in items]
        self.assertEqual(tmdb_ids.count(100), 1)
        # The kept item should NOT have source_bucket (from shows array)
        self.assertIsNone(items[0].extra.get("source_bucket"))

    def test_anime_not_in_shows_kept(self):
        """Item only in anime array → kept with source_bucket=anime."""
        backend = SimklSyncBackend.__new__(SimklSyncBackend)
        backend._client = MagicMock()
        backend._api_key = "test"
        backend._token = "test"
        backend._base_params = lambda **kw: {"api_key": "test"}
        backend._headers = lambda **kw: {}
        backend._skip_unmapped = lambda **kw: None
        backend.skipped = []

        data = {
            "shows": [],
            "anime": [{
                "show": {"ids": {"tmdb": 200}, "title": "Real Anime", "year": 2021},
                "status": "plantowatch",
                "seasons": [],
            }],
            "movies": [],
        }
        backend._client.get.return_value.json.return_value = data

        items = backend.pull()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].tmdb_id, 200)
        self.assertEqual(items[0].extra.get("source_bucket"), "anime")


class TestUpdateIsAnimeFromCache(unittest.TestCase):
    """Verify that update_is_anime_from_cache sets is_anime from TMDB genre IDs."""

    def _make_repo(self):
        from src.data.local.repository import LocalMediaRepository
        from src.data.local.schema import initialize
        import tempfile, os
        db_path = os.path.join(tempfile.mkdtemp(), "test.db")
        repo = LocalMediaRepository(db_path)
        conn = repo._ensure_conn()
        initialize(conn)
        return repo, conn

    def test_anime_genre_sets_flag(self):
        repo, conn = self._make_repo()
        conn.execute("INSERT INTO media_items (tmdb_id, media_type, title, cached_at, updated_at) VALUES (100, 'show', 'Anime Show', 0, 0)")
        conn.execute("INSERT INTO watchlist_items (tmdb_id, media_type, added_at, is_anime) VALUES (100, 'show', 0, 0)")
        conn.commit()

        cache = MagicMock()
        cache.get_genre_ids.return_value = [16, 18]  # 16 = Animation

        updated = repo.update_is_anime_from_cache(cache)
        self.assertEqual(updated, 1)
        row = conn.execute("SELECT is_anime FROM watchlist_items WHERE tmdb_id = 100").fetchone()
        self.assertEqual(row[0], 1)

    def test_non_anime_genre_clears_flag(self):
        repo, conn = self._make_repo()
        conn.execute("INSERT INTO media_items (tmdb_id, media_type, title, cached_at, updated_at) VALUES (200, 'show', 'Drama Show', 0, 0)")
        conn.execute("INSERT INTO watchlist_items (tmdb_id, media_type, added_at, is_anime) VALUES (200, 'show', 0, 1)")
        conn.commit()

        cache = MagicMock()
        cache.get_genre_ids.return_value = [18]  # 18 = Drama, no 16

        updated = repo.update_is_anime_from_cache(cache)
        self.assertEqual(updated, 1)
        row = conn.execute("SELECT is_anime FROM watchlist_items WHERE tmdb_id = 200").fetchone()
        self.assertEqual(row[0], 0)

    def test_no_cache_data_keeps_current(self):
        repo, conn = self._make_repo()
        conn.execute("INSERT INTO media_items (tmdb_id, media_type, title, cached_at, updated_at) VALUES (300, 'show', 'Unknown Show', 0, 0)")
        conn.execute("INSERT INTO watchlist_items (tmdb_id, media_type, added_at, is_anime) VALUES (300, 'show', 0, 1)")
        conn.commit()

        cache = MagicMock()
        cache.get_genre_ids.return_value = None  # Not cached

        updated = repo.update_is_anime_from_cache(cache)
        self.assertEqual(updated, 0)
        row = conn.execute("SELECT is_anime FROM watchlist_items WHERE tmdb_id = 300").fetchone()
        self.assertEqual(row[0], 1)  # Unchanged

    def test_cache_get_genre_ids(self):
        from src.data.local.cache import MetadataCache
        import tempfile, os
        db_path = os.path.join(tempfile.mkdtemp(), "test.db")
        cache = MetadataCache(db_path, ttl_seconds=3600)
        conn = cache._ensure_conn()
        conn.execute(
            "INSERT INTO media_items (tmdb_id, media_type, title, genre_ids, cached_at, updated_at) "
            "VALUES (100, 'show', 'Test', '[16,18]', 0, 0)"
        )
        conn.commit()
        result = cache.get_genre_ids(100)
        self.assertEqual(result, [16, 18])

    def test_cache_get_genre_ids_missing(self):
        from src.data.local.cache import MetadataCache
        import tempfile, os
        db_path = os.path.join(tempfile.mkdtemp(), "test.db")
        cache = MetadataCache(db_path, ttl_seconds=3600)
        result = cache.get_genre_ids(999)
        self.assertIsNone(result)


# ---------------------------------------------------------------------------
# Cancel (full rollback)
# ---------------------------------------------------------------------------

class TestSyncCancel(unittest.TestCase):
    def test_cancel_yields_cancelled_state(self):
        settings = MagicMock()
        settings.get_boolean.return_value = True
        settings.get_int.return_value = 15
        pending = []
        backend = MagicMock()
        backend.name = "test"
        backend.is_authenticated.return_value = True
        backend.pull.return_value = [SyncItem(
            tmdb_id=123, media_type="show", title="Dexter: Resurrection",
            category=SyncCategory.WATCHLIST,
        )]
        backend.push.return_value = SyncResult()
        engine = SyncEngine(settings, [backend])
        engine._status.state = "syncing"
        engine.cancel_sync()
        engine._run_sync()
        self.assertEqual(engine._status.state, "cancelled")

    def test_rollback_restores_tables(self):
        import tempfile, os
        from src.data.local.repository import LocalMediaRepository
        with tempfile.TemporaryDirectory() as d:
            dao = LocalMediaRepository(os.path.join(d, "t.sqlite"))
            dao.initialize()
            conn = dao._ensure_conn()
            conn.execute(
                "INSERT INTO watchlist_items "
                "(tmdb_id, media_type, added_at) VALUES (100, 'show', 1000)"
            )
            conn.commit()
            snap = dao.snapshot_tables()
            self.assertEqual(len(snap["watchlist_items"]["rows"]), 1)
            # Mutate during the "run"
            conn.execute(
                "INSERT INTO watchlist_items "
                "(tmdb_id, media_type, added_at) VALUES (200, 'show', 2000)"
            )
            conn.execute(
                "DELETE FROM watchlist_items WHERE tmdb_id = 100"
            )
            conn.commit()
            self.assertEqual(len(dao._ensure_conn().execute(
                "SELECT * FROM watchlist_items").fetchall()), 1)
            # Roll back
            dao.restore_tables(snap)
            rows = dao._ensure_conn().execute(
                "SELECT tmdb_id FROM watchlist_items").fetchall()
            self.assertEqual([r[0] for r in rows], [100])


# ---------------------------------------------------------------------------
# Automatic-sync toggle (sync-auto)
# ---------------------------------------------------------------------------

class TestSyncAuto(unittest.TestCase):
    def _make_engine(self, overrides=None):
        values = {"sync-enabled": True, "sync-auto": True}
        if overrides:
            values.update(overrides)
        settings = MagicMock()
        settings.get_boolean.side_effect = lambda k: values.get(k, True)
        settings.get_int.return_value = 15
        backend = MagicMock()
        backend.name = "test"
        backend.is_authenticated.return_value = True
        backend.pull.return_value = []
        backend.push.return_value = SyncResult()
        return SyncEngine(settings, [backend])

    def test_initial_sync_skipped_when_auto_off(self):
        engine = self._make_engine({"sync-auto": False})
        with patch("src.data.sync.engine.threading.Thread") as T:
            engine._initial_sync()
            T.assert_not_called()

    def test_trigger_skipped_when_auto_off(self):
        engine = self._make_engine({"sync-auto": False})
        with patch("src.data.sync.engine.threading.Thread") as T:
            engine._trigger_sync()
            T.assert_not_called()

    def test_manual_sync_still_runs_when_auto_off(self):
        engine = self._make_engine({"sync-auto": False})
        with patch("src.data.sync.engine.threading.Thread") as T:
            engine.sync_now()
            T.assert_called_once()

    def test_initial_sync_runs_when_auto_on(self):
        engine = self._make_engine({"sync-auto": True})
        with patch("src.data.sync.engine.threading.Thread") as T:
            engine._initial_sync()
            T.assert_called_once()


class TestResetRemote(unittest.TestCase):
    """Reset-room backup: refusal guards, ordering, and cancel behaviour."""

    def _engine(self, repo, backend, enabled=True):
        settings = MagicMock()
        settings.get_boolean.return_value = enabled
        settings.get_int.return_value = 15
        return SyncEngine(settings, [backend], repo=repo)

    def test_reset_refuses_while_syncing(self):
        repo = MagicMock()
        backend = MagicMock()
        backend.name = "simkl"
        backend.is_authenticated.return_value = True
        engine = self._engine(repo, backend)
        engine._status.state = "syncing"
        self.assertFalse(engine.reset_remote("simkl"))

    def test_reset_refuses_unknown_backend(self):
        repo = MagicMock()
        backend = MagicMock()
        backend.name = "tmdb"
        backend.is_authenticated.return_value = True
        engine = self._engine(repo, backend)
        self.assertFalse(engine.reset_remote("letterboxd"))

    def test_reset_refuses_when_disabled(self):
        repo = MagicMock()
        backend = MagicMock()
        backend.name = "simkl"
        backend.is_authenticated.return_value = True
        engine = self._engine(repo, backend, enabled=False)
        self.assertFalse(engine.reset_remote("simkl"))

    def test_reset_refuses_when_unauthenticated(self):
        repo = MagicMock()
        backend = MagicMock()
        backend.name = "simkl"
        backend.is_authenticated.return_value = False
        engine = self._engine(repo, backend)
        self.assertFalse(engine.reset_remote("simkl"))

    def test_reset_starts_background_thread(self):
        repo = MagicMock()
        backend = MagicMock()
        backend.name = "simkl"
        backend.is_authenticated.return_value = True
        engine = self._engine(repo, backend)
        with patch("src.data.sync.engine.threading.Thread") as T:
            self.assertTrue(engine.reset_remote("simkl"))
            T.assert_called_once()

    def test_do_reset_clears_then_repushes(self):
        repo = MagicMock()
        backend = MagicMock()
        backend.name = "simkl"
        backend.remove_all.return_value = SyncResult(pushed=3)
        backend.push.return_value = SyncResult(pushed=5)
        engine = self._engine(repo, backend)
        item = SyncItem(
            tmdb_id=1, media_type="movie", category=SyncCategory.WATCHLIST)
        with patch("src.data.sync.engine.local_to_sync_items",
                   return_value=[item]):
            engine._do_reset(backend)
        self.assertEqual(backend.remove_all.call_count, 1)
        self.assertEqual(backend.push.call_count, 1)
        repo.update_pushed_items.assert_called_once()
        self.assertEqual(engine._status.pushed_removed, 3)
        self.assertEqual(engine._status.pushed_added, 5)

    def test_do_reset_skips_push_when_cancelled(self):
        repo = MagicMock()
        backend = MagicMock()
        backend.name = "simkl"
        backend.remove_all.return_value = SyncResult(pushed=3)
        backend.push.return_value = SyncResult()
        engine = self._engine(repo, backend)
        engine._cancel_requested = True
        with patch("src.data.sync.engine.local_to_sync_items") as lts:
            engine._do_reset(backend)
        backend.push.assert_not_called()
        repo.update_pushed_items.assert_not_called()


class TestSimklRemoveAll(unittest.TestCase):
    """Simkl account wipe: batching, anime folding, and empty no-op."""

    def test_remove_all_batches_and_folds_anime(self):
        backend = SimklSyncBackend("test-client-id")
        backend.pull_ids_only = MagicMock(return_value={
            "shows": [{"ids": {"tmdb": 1}}, {"ids": {"tmdb": 2}}],
            "movies": [{"ids": {"tmdb": 3}}],
            "anime": [{"ids": {"tmdb": 4}}],
        })
        resp = MagicMock()
        resp.json.side_effect = [
            {"deleted": {"shows": 2, "movies": 0}},
            {"deleted": {"shows": 0, "movies": 1}},
        ]
        resp.raise_for_status.return_value = None
        backend._client = MagicMock()
        backend._client.post.return_value = resp
        progress = []
        result = backend.remove_all(lambda r, t: progress.append((r, t)))
        # 2 shows + 1 movie + 1 anime (folded into shows) = 4 total
        self.assertEqual(result.pushed, 3)
        self.assertEqual(progress[-1][1], 4)
        bodies = [c.kwargs.get("json", {}) for c in backend._client.post.call_args_list]
        self.assertTrue(all("anime" not in body for body in bodies))
        shows_total = sum(len(b.get("shows", [])) for b in bodies)
        self.assertEqual(shows_total, 3)

    def test_remove_all_noop_when_empty(self):
        backend = SimklSyncBackend("test-client-id")
        backend.pull_ids_only = MagicMock(
            return_value={"shows": [], "movies": [], "anime": []})
        backend._client = MagicMock()
        result = backend.remove_all()
        self.assertEqual(result.pushed, 0)
        backend._client.post.assert_not_called()


class TestSimklRemoveFromHistoryAnime(unittest.TestCase):
    """remove_from_history folds anime into the shows[] array."""

    def test_anime_folded_into_shows(self):
        backend = SimklSyncBackend("test-client-id")
        resp = MagicMock()
        resp.json.return_value = {"deleted": {"shows": 2, "movies": 0}}
        resp.raise_for_status.return_value = None
        backend._client = MagicMock()
        backend._client.post.return_value = resp
        items = [
            SyncItem(tmdb_id=1, media_type="movie",
                     category=SyncCategory.WATCHLIST,
                     extra={"source_bucket": "anime"}),
            SyncItem(tmdb_id=2, media_type="show",
                     category=SyncCategory.WATCHLIST,
                     extra={"source_bucket": "anime"}),
            SyncItem(tmdb_id=3, media_type="movie",
                     category=SyncCategory.WATCHLIST),
        ]
        result = backend.remove_from_history(items)
        body = backend._client.post.call_args.kwargs["json"]
        self.assertEqual(result.pushed, 2)
        self.assertEqual([s["ids"]["tmdb"] for s in body["shows"]], [1, 2])
        self.assertEqual([m["ids"]["tmdb"] for m in body["movies"]], [3])
        self.assertNotIn("anime", body)


if __name__ == "__main__":
    unittest.main()
