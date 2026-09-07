"""Sync engine: orchestrates pull/merge/push across backends."""

import logging
import threading
import time
from dataclasses import dataclass, field

from gi.repository import GLib

from .base import SyncBackend, SyncItem, SyncCategory, SyncResult
from .converter import local_to_sync_items, import_remote_items, compute_push_deletions, compute_pull_deletions
from .verify import verify_items

_log = logging.getLogger(__name__)


@dataclass
class SyncStatus:
    state: str = "idle"  # "idle" | "syncing" | "enriching" | "done" | "error"
    results: dict = field(default_factory=dict)  # backend_name -> SyncResult
    backfill_current: int = 0
    backfill_total: int = 0
    pushed_added: int = 0
    pushed_removed: int = 0
    pulled_added: int = 0
    pulled_removed: int = 0
    remote_removed: list = field(default_factory=list)  # (title, category) for warning toast
    skipped: list = field(default_factory=list)  # "Title — reason" for unverifiable items
    ambiguous: list = field(default_factory=list)  # mapping decisions needing the user
    show_report: bool = False  # true when this run was user-initiated (dialog shown)
    status_text: str = ""  # real-time phase description for sidebar


def _warning_sig(claimed_tmdb_id, title, media_type, year) -> tuple:
    """Stable identity for a mapping-decision entry (for removal)."""
    return (claimed_tmdb_id, title, media_type, year)


def _warning_entry(item: SyncItem, backend: str) -> dict:
    """Build a mapping-decision entry for an item with no TMDB identity."""
    return {
        "item": item,
        "claimed_tmdb_id": None,
        "simkl_title": item.title,
        "tmdb_title": None,
        "media_type": item.media_type,
        "year": item.year,
        "backend": backend,
        "sig": _warning_sig(None, item.title, item.media_type, item.year),
    }


def merge_union(
    local: list[SyncItem],
    remote: list[SyncItem],
) -> list[SyncItem]:
    """Merge two item lists using union strategy.

    Items on either side appear in the result. When both sides have the
    same (tmdb_id, media_type, category), the most recent timestamp wins
    and the higher rating wins.
    """
    merged: dict[tuple, SyncItem] = {}
    for item in local:
        key = (item.tmdb_id, item.media_type, item.category)
        merged[key] = item
    for item in remote:
        key = (item.tmdb_id, item.media_type, item.category)
        if key not in merged:
            merged[key] = item
        else:
            existing = merged[key]
            # More recent timestamp wins
            for attr in ("watched_at", "added_at"):
                old_ts = getattr(existing, attr)
                new_ts = getattr(item, attr)
                if new_ts is not None and (old_ts is None or new_ts > old_ts):
                    setattr(existing, attr, new_ts)
            # Higher rating wins
            if item.rating is not None and (
                existing.rating is None or item.rating > existing.rating
            ):
                existing.rating = item.rating
            # Prefer non-None remote_id
            if item.remote_id is not None and existing.remote_id is None:
                existing.remote_id = item.remote_id
    return list(merged.values())


class SyncEngine:
    """Runs sync in background threads, fires callbacks on completion."""

    def __init__(
        self,
        settings,
        backends: list[SyncBackend],
        repo=None,
        metadata_service=None,
        on_done=None,
        on_deletions=None,
    ):
        self._settings = settings
        self._backends = backends
        self._repo = repo
        self._metadata_service = metadata_service
        self._on_done = on_done
        self._on_deletions = on_deletions
        self._status = SyncStatus()
        self._timer = None
        self.skip_push_deletions = False
        self._apply_deletions = False
        self._is_manual = False
        self._report_skips = False
        self._defer_surface = False
        self._resolved_sigs: set = set()
        self._dismissed_sigs: set = set()
        self._skip_pull_deletions = False
        self._cancel_requested = False

    @property
    def status(self) -> SyncStatus:
        return self._status

    def start_auto_sync(self):
        """Start periodic auto-sync. Called once at app startup."""
        interval = self._settings.get_int("sync-interval-minutes") * 60
        # One-shot initial sync after 2 seconds
        GLib.timeout_add(2000, self._initial_sync)
        # Recurring auto-sync
        self._timer = GLib.timeout_add_seconds(interval, self._trigger_sync)

    def apply_pending_deletions(self):
        """Mark the next sync run to push the pending remote removals.

        Called after the user confirms "Remove from Simkl" in the
        lightweight confirm dialog — the one sanctioned way deletions
        reach the remote.
        """
        self._apply_deletions = True

    def retain_deletions(self):
        """Abort any pending removal without deciding ("Cancel").

        Leaves sync_state untouched so the conflict is re-offered on the
        next sync trigger.  Called when the user dismisses the confirm
        dialog instead of confirming the removal.
        """
        self._apply_deletions = False

    def resolve_mapping(self, entry: dict, chosen_tmdb_id: int | None,
                        media_type: str | None = None) -> bool:
        """Import a wizard-resolved item using the user-chosen mapping.

        entry: a mapping-decision dict from status.ambiguous.  The chosen
        TMDB entry is trusted (no re-verification) and the corrected item
        is pushed on the follow-up sync, fixing the Simkl mapping too.
        Returns True on success.
        """
        if self._repo is None:
            return False
        item = entry.get("item")
        if item is None:
            return False
        old_tmdb_id = item.tmdb_id
        if chosen_tmdb_id:
            item.tmdb_id = chosen_tmdb_id
        if media_type:
            item.media_type = media_type
        try:
            import_remote_items(self._repo, [item])
        except Exception:
            _log.exception("Failed to import mapped item %r", item.title)
            return False
        if old_tmdb_id and old_tmdb_id != item.tmdb_id:
            try:
                self._repo.remove_local_items(
                    [(old_tmdb_id, item.media_type)])
            except Exception:
                _log.exception(
                    "Failed to remove old mapping tmdb_id=%s", old_tmdb_id)
        _log.info("Imported user-mapped item %r (tmdb=%s)",
                  item.title, item.tmdb_id)
        self._status.ambiguous = [
            w for w in self._status.ambiguous
            if w.get("sig") != entry.get("sig")
        ]
        self._resolved_sigs.add(entry.get("sig"))
        if self._repo is not None:
            try:
                self._repo.dismiss_mapping(str(entry.get("sig")))
            except Exception:
                _log.exception("Failed to persist resolved mapping %s",
                               entry.get("sig"))
        self._skip_pull_deletions = True
        self.sync_now()
        return True

    def dismiss_item(self, entry: dict) -> None:
        """Mark a mapping-decision entry as dismissed (not on TMDB).

        The item is removed from the active ambiguous list and its sig is
        persisted so future sync runs skip it automatically.
        """
        sig = entry.get("sig")
        if sig is None:
            return
        self._dismissed_sigs.add(str(sig))
        if self._repo is not None:
            try:
                self._repo.dismiss_mapping(str(sig))
            except Exception:
                _log.exception("Failed to persist dismissed mapping %s", sig)
        self._status.ambiguous = [
            w for w in self._status.ambiguous
            if w.get("sig") != sig
        ]

    def _maybe_surface_deletions(self) -> bool:
        """Offer pending sync conflicts to the UI before a run starts.

        Called on the main thread from every sync trigger.  When items
        were deleted locally but still exist on the remote, the UI shows a
        lightweight confirm ("Remove from Simkl too?"); returns True to
        indicate the run was aborted in favour of that flow.  A follow-up
        sync applies the decision.
        """
        if self._repo is None or self._on_deletions is None:
            return False
        if self._status.state in ("syncing", "enriching"):
            return False
        if self._apply_deletions:
            return False  # user already decided via the dialog
        if getattr(self, "skip_push_deletions", False):
            return False  # action-triggered sync: never offer conflicts
        if self._defer_surface:
            return False  # another decision dialog is open; wait for it
        try:
            deletions = self.check_push_deletions()
        except Exception:
            _log.exception("Failed to compute push deletions")
            return False
        if not deletions:
            return False
        _log.info("Surface %d sync conflicts before run", len(deletions))
        self._on_deletions(deletions)
        return True

    def _initial_sync(self):
        """First sync on startup — runs once, does not repeat.

        Only fires when both the master (sync-enabled) and the automatic
        (sync-auto) switches are on; the automatic switch lets users turn
        off background sync without touching manual "Sync Now".
        """
        if (self._settings.get_boolean("sync-enabled")
                and self._settings.get_boolean("sync-auto")):
            if self._status.state in ("syncing", "enriching"):
                return False
            if self._maybe_surface_deletions():
                return False  # user decides; follow-up sync comes from dialog
            # Startup sync surfaces mapping decisions (wizard), manual-style.
            self._report_skips = True
            thread = threading.Thread(target=self._run_sync, daemon=True)
            thread.start()
        return False  # Do not repeat

    def stop(self):
        if self._timer is not None:
            GLib.source_remove(self._timer)
            self._timer = None

    def cancel_sync(self):
        """Request cancellation of the running sync.

        The engine checks the flag at safe points; on seeing it, it stops
        pulling/pushing and rolls the local DB back to its pre-run state.
        Tolerates being called when no sync is running.
        """
        self._cancel_requested = True

    def sync_now(self):
        """Manual sync trigger."""
        if self._status.state in ("syncing", "enriching"):
            _log.debug("Sync already in progress, skipping manual trigger")
            return
        if self._maybe_surface_deletions():
            return
        self._is_manual = True
        thread = threading.Thread(target=self._run_sync, daemon=True)
        thread.start()

    def reset_remote(self, backend_name: str, progress_cb=None) -> bool:
        """Reset a remote backup in place: wipe it, then re-push Ciak as truth.

        Only supported for backends that expose ``remove_all`` (Simkl).
        The work runs on a background thread and reports progress via
        on_done.  Guarded so it never runs while a sync is active and
        never clears an unauthenticated or disabled account by accident.
        Returns True when the reset was started.
        """
        if self._status.state in ("syncing", "enriching"):
            _log.debug("Cannot reset %s while a sync is active", backend_name)
            return False
        backend = next(
            (b for b in self._backends if b.name == backend_name), None)
        if backend is None or not hasattr(backend, "remove_all"):
            _log.info("Reset not supported for %s", backend_name)
            return False
        key = f"sync-{backend_name}-enabled"
        if not self._settings.get_boolean(key):
            return False
        if not backend.is_authenticated():
            return False
        self._cancel_requested = False
        thread = threading.Thread(
            target=self._do_reset, args=(backend, progress_cb), daemon=True)
        thread.start()
        return True

    def _do_reset(self, backend: SyncBackend, progress_cb=None) -> None:
        display = {"simkl": "Simkl", "tmdb": "TMDB",
                   "letterboxd": "Letterboxd"}.get(backend.name, backend.name)
        self._status.state = "syncing"
        self._status.results = {}
        self._status.pushed_added = 0
        self._status.pushed_removed = 0
        self._status.status_text = f"Resetting {display}…"
        self._fire_on_done()
        start = time.monotonic()
        error_msg = None
        try:
            def _cb(removed, total):
                self._status.status_text = (
                    f"Removed {removed} of {total} from {display}…")
                self._fire_on_done()
                if progress_cb:
                    progress_cb(removed, total)

            clear_result = backend.remove_all(_cb)
            self._status.results[backend.name] = clear_result
            self._status.pushed_removed += clear_result.pushed
            if self._cancel_requested:
                _log.info("Reset of %s cancelled after clearing", backend.name)
                return
            self._status.status_text = f"Re-pushing {display}…"
            self._fire_on_done()
            local_items = local_to_sync_items(self._repo)
            if local_items:
                push_result = backend.push(local_items)
                self._status.pushed_added += push_result.pushed
                self._repo.update_pushed_items(backend.name, local_items)
            self._settings.set_int64("sync-last-sync", int(time.time()))
        except Exception as e:
            _log.exception("Reset of %s failed", backend.name)
            error_msg = str(e)
        finally:
            self._status.state = (
                "cancelled" if self._cancel_requested
                else ("error" if error_msg else "done"))
            self._status.status_text = ""
            self._cancel_requested = False
            _log.info("Reset of %s finished in %dms (state=%s)",
                      backend.name,
                      int((time.monotonic() - start) * 1000),
                      self._status.state)
            self._fire_on_done()

    def check_push_deletions(self) -> list[dict]:
        """Pre-compute push-deletions for all enabled backends.

        Returns list of dicts, one per backend with pending deletions:
        {"backend_name": str, "display_name": str, "items": [(title, media_type, tmdb_id), ...]}
        Called on the main thread BEFORE spawning the sync thread.
        """
        if self._repo is None:
            return []
        results = []
        backend_names = {"simkl": "Simkl", "tmdb": "TMDB", "letterboxd": "Letterboxd"}
        for backend in self._backends:
            key = f"sync-{backend.name}-enabled"
            if not self._settings.get_boolean(key):
                continue
            if not backend.is_authenticated():
                continue
            push_removals = compute_push_deletions(self._repo, backend.name)
            all_rems = push_removals["watchlist"] + push_removals["ratings"]
            if not all_rems:
                continue
            # Deduplicate by (tmdb_id, media_type) and look up titles
            seen = set()
            items = []
            for rem in all_rems:
                key_id = (rem.tmdb_id, rem.media_type)
                if key_id in seen:
                    continue
                seen.add(key_id)
                title = None
                if self._repo is not None:
                    try:
                        meta = self._repo.get_media_item(rem.tmdb_id)
                        if meta:
                            title = meta.get("title")
                    except Exception:
                        pass
                if not title:
                    label = "Movie" if rem.media_type == "movie" else "Show"
                    title = f"{label} #{rem.tmdb_id}"
                items.append((title, rem.media_type, rem.tmdb_id))
            display_name = backend_names.get(backend.name, backend.name)
            results.append({
                "backend_name": backend.name,
                "display_name": display_name,
                "items": items,
            })
        return results

    def _ambig_sig_seen(self, sig: tuple) -> bool:
        """True when a mapping decision with this sig is already queued.

        Guides the wizard: one row per distinct (claimed_id, title,
        media_type, year), not one per affected Simkl episode/listing.
        """
        for w in self._status.ambiguous:
            if w.get("sig") == sig:
                return True
        return False

    def _trigger_sync(self):
        if not self._settings.get_boolean("sync-enabled"):
            return True
        if not self._settings.get_boolean("sync-auto"):
            return True
        if self._status.state in ("syncing", "enriching"):
            return True
        if self._maybe_surface_deletions():
            return True  # user decides; follow-up sync comes from dialog
        thread = threading.Thread(target=self._run_sync, daemon=True)
        thread.start()
        return True

    def _fire_on_done(self):
        """Fire callback on main thread with a snapshot of current status."""
        if self._on_done is None:
            return
        snapshot = SyncStatus(
            state=self._status.state,
            results=dict(self._status.results),
            backfill_current=self._status.backfill_current,
            backfill_total=self._status.backfill_total,
            pushed_added=self._status.pushed_added,
            pushed_removed=self._status.pushed_removed,
            pulled_added=self._status.pulled_added,
            pulled_removed=self._status.pulled_removed,
            remote_removed=list(self._status.remote_removed),
            skipped=list(self._status.skipped),
            ambiguous=list(self._status.ambiguous),
            show_report=self._status.show_report,
            status_text=self._status.status_text,
        )
        GLib.idle_add(self._on_done, snapshot)

    def _run_sync(self):
        self._status.state = "syncing"
        self._status.results = {}
        self._status.backfill_current = 0
        self._status.backfill_total = 0
        self._status.pushed_added = 0
        self._status.pushed_removed = 0
        self._status.pulled_added = 0
        self._status.pulled_removed = 0
        self._status.remote_removed = []
        self._status.skipped = []
        self._status.ambiguous = []
        self._status.show_report = self._is_manual or self._report_skips
        self._is_manual = False
        self._report_skips = False
        self._status.status_text = "Syncing…"
        self._fire_on_done()
        start = time.monotonic()
        error_msg = None
        snapshot = None
        if self._repo is not None:
            try:
                snapshot = self._repo.snapshot_tables()
            except Exception:
                _log.exception("Failed to snapshot tables for cancel-rollback")
                snapshot = None

        try:
            cancelled = False
            for backend in self._backends:
                if self._cancel_requested:
                    cancelled = True
                    break
                key = f"sync-{backend.name}-enabled"
                if not self._settings.get_boolean(key):
                    continue
                if not backend.is_authenticated():
                    continue
                try:
                    result = self._sync_backend(backend)
                    self._status.results[backend.name] = result
                except Exception as e:
                    _log.exception("Sync failed for %s", backend.name)
                    self._status.results[backend.name] = SyncResult(errors=[str(e)])
                if self._cancel_requested:
                    cancelled = True
                    break

            if not cancelled:
                self._settings.set_int64("sync-last-sync", int(time.time()))

            total_pulled = sum(
                r.pulled for r in self._status.results.values()
            )
            if (not cancelled and total_pulled > 0
                    and self._metadata_service is not None):
                self._status.state = "enriching"
                self._fire_on_done()
                self._backfill_metadata()
                # Sync is_anime flags from TMDB genre data in cache to
                # user-data tables.  This is the authoritative source for
                # anime status — Simkl bucket assignments are just hints.
                if self._repo is not None:
                    try:
                        cache = self._metadata_service._cache
                        updated = self._repo.update_is_anime_from_cache(cache)
                        if updated:
                            _log.info("Synced is_anime on %d rows", updated)
                    except Exception:
                        _log.exception("Failed to sync is_anime from cache")
            elif cancelled and self._repo is not None and snapshot:
                _log.info(
                    "Sync cancelled — rolling back %d tables",
                    len(snapshot),
                )
                try:
                    self._repo.restore_tables(snapshot)
                except Exception:
                    _log.exception("Cancel rollback failed")
        except Exception as e:
            _log.exception("Sync engine error")
            error_msg = str(e)
        finally:
            self._status.state = (
                "cancelled" if self._cancel_requested
                else ("error" if error_msg else "done")
            )
            self._status.status_text = ""
            self.skip_push_deletions = False
            self._apply_deletions = False
            self._skip_pull_deletions = False
            self._cancel_requested = False
            elapsed_ms = int((time.monotonic() - start) * 1000)
            _log.info("Sync finished in %dms (state=%s)", elapsed_ms, self._status.state)
            self._fire_on_done()

    def _sync_backend(self, backend: SyncBackend) -> SyncResult:
        result = SyncResult()
        display = {"simkl": "Simkl", "tmdb": "TMDB", "letterboxd": "Letterboxd"}.get(
            backend.name, backend.name
        )

        # Lazy-load dismissed sigs from DB on first sync run.
        if not self._dismissed_sigs and self._repo is not None:
            try:
                self._dismissed_sigs = self._repo.get_dismissed_mappings()
            except Exception:
                _log.exception("Failed to load dismissed mappings")

        # ---- Push deletions are decided BEFORE the pull ----
        # The removal is confirmed on the main thread before a run starts;
        # here we snapshot what that decision applies to so a re-imported
        # row can't resurrect mid-removal.
        pending_removals = []
        pending_keys = set()
        if self._repo is not None:
            try:
                push_removals = compute_push_deletions(self._repo, backend.name)
                pending_removals = (
                    push_removals["watchlist"] + push_removals["ratings"]
                )
                pending_keys = {(r.tmdb_id, r.media_type) for r in pending_removals}
            except Exception:
                _log.exception("Failed to compute push deletions for %s", backend.name)

        # ---- Pull additions (always first — startup needs remote changes) ----
        self._status.status_text = f"Importing from {display}…"
        self._fire_on_done()
        time.sleep(0.8)  # let sidebar render the label
        remote_items = backend.pull()
        result.pulled = len(remote_items)
        _log.info("Pulled %d items from %s", len(remote_items), backend.name)
        if self._cancel_requested:
            return result

        # Pull-level items too incomplete to resolve (e.g. Simkl entries
        # without any TMDB ID) are never silently dropped — they become
        # user mapping decisions too.  When a title+year search can
        # confidently resolve the item, auto-import instead.
        backend_skipped = getattr(backend, "skipped", []) or []
        auto_resolved = []
        for b_item in backend_skipped:
            if not isinstance(b_item, SyncItem):
                self._status.skipped.append(str(b_item))
                continue
            # Try title+year search for items with no TMDB ID.
            if (b_item.tmdb_id is None
                    and b_item.title and b_item.year
                    and self._metadata_service is not None):
                from .verify import titles_match, strict_search
                hit = strict_search(
                    self._metadata_service,
                    b_item.title, b_item.year, b_item.media_type)
                if hit is None:
                    hit = strict_search(
                        self._metadata_service,
                        b_item.title, b_item.year, None)
                if hit is not None and titles_match(
                        getattr(hit, "title", ""), b_item.title):
                    _log.info(
                        "Auto-resolved no-ID %r: %s -> %s (%s)",
                        b_item.title, b_item.tmdb_id,
                        hit.tmdb_id, getattr(hit, "title", ""),
                    )
                    b_item.tmdb_id = hit.tmdb_id
                    b_item.media_type = getattr(hit, "media_type",
                                                b_item.media_type)
                    auto_resolved.append(b_item)
                    continue
            w = _warning_entry(b_item, backend.name)
            if self._ambig_sig_seen(w["sig"]):
                continue
            if w["sig"] in self._resolved_sigs:
                continue
            if str(w["sig"]) in self._dismissed_sigs:
                continue
            self._status.ambiguous.append(w)
        if auto_resolved:
            remote_items = list(remote_items) + auto_resolved

        # Verify each item against TMDB before importing so corrupt
        # remote mappings (wrong TMDB ID / title) never reach the DB.
        # Anything unverifiable is queued for the mapping wizard.
        if remote_items and self._metadata_service is not None:
            _log.info("Verifying %d items against TMDB", len(remote_items))
            remote_items, verify_warnings = verify_items(
                self._metadata_service, remote_items,
            )
            _log.info("Verification: %d kept, %d need mapping",
                      len(remote_items), len(verify_warnings))
            for w in verify_warnings:
                w["backend"] = backend.name
                w["sig"] = _warning_sig(
                    w.get("claimed_tmdb_id"), w.get("simkl_title"),
                    w.get("media_type"), w.get("year"))
                if self._ambig_sig_seen(w["sig"]):
                    continue
                if w["sig"] in self._resolved_sigs:
                    continue
                if str(w["sig"]) in self._dismissed_sigs:
                    continue
                _log.info("Ambiguous entry: %r (claimed tmdb=%s, "
                          "tmdb says %r, sig=%s)",
                          w.get("simkl_title"), w.get("claimed_tmdb_id"),
                          w.get("tmdb_title"), w["sig"])
                self._status.ambiguous.append(w)
        elif remote_items and self._metadata_service is None:
            _log.warning("metadata_service is None — %d items "
                         "imported WITHOUT verification", len(remote_items))

        # Items being removed from the remote are not imported this run —
        # no resurrection.
        exclude = pending_keys
        if exclude:
            before = len(remote_items)
            remote_items = [
                i for i in remote_items
                if (i.tmdb_id, i.media_type) not in exclude
            ]
            if len(remote_items) != before:
                _log.info(
                    "Excluded %d pulled items from import (%s)",
                    before - len(remote_items), backend.name,
                )

        if remote_items and self._repo is not None:
            imported = import_remote_items(self._repo, remote_items)
            _log.info("Imported %d / %d items from %s into local DB",
                       imported, len(remote_items), backend.name)
            self._status.pulled_added += imported
            if self._cancel_requested:
                return result
        elif (not remote_items and self._repo is not None
              and not self._status.skipped and not self._status.ambiguous):
            _log.warning("No items pulled from %s — check API response or auth", backend.name)

        # ---- Pull deletion detection (Simkl only) ----
        if (not getattr(self, '_skip_pull_deletions', False)
                and self._repo is not None and hasattr(backend, "get_activities")):
            try:
                activities = backend.get_activities()
                rl = activities.get("removed_from_list", {})
                simkl_changed = any(v is not None for v in rl.values())
                if simkl_changed:
                    self._status.status_text = f"Checking {display} for changes…"
                    self._fire_on_done()
                    _log.info("Remote removal detected, fetching IDs-only payload")
                    remote_ids = backend.pull_ids_only()
                    removals = compute_pull_deletions(self._repo, remote_ids)
                    if removals:
                        _log.info("Removing %d items locally (no longer on Simkl)", len(removals))
                        self._repo.remove_local_items(removals)
                        self._status.pulled_removed += len(removals)
                        for tmdb_id, mt in removals:
                            self._status.remote_removed.append(
                                (f"{mt} #{tmdb_id}", "Simkl")
                            )
            except Exception:
                _log.exception("Remote deletion detection failed")

        if self._repo is not None:
            # ---- Apply pending push deletions (only after user decision) ----
            # Removals only reach the remote when apply_pending_deletions()
            # was called (the "Remove from Simkl" dialog choice).  Auto and
            # unexpected conflicts are retained for the next surfacing.
            if pending_removals:
                if self._apply_deletions and not getattr(
                        self, "skip_push_deletions", False):
                    self._status.status_text = f"Removing from {display}…"
                    self._fire_on_done()
                    _log.info(
                        "Pushing %d removals to %s",
                        len(pending_removals), backend.name,
                    )
                    backend.remove_from_history(pending_removals)
                    self._status.pushed_removed += len(pending_removals)
                    self._repo.clear_pushed_state_for(
                        backend.name,
                        [(r.tmdb_id, r.media_type) for r in pending_removals],
                    )
                else:
                    _log.info(
                        "Retaining %d pending deletions for %s "
                        "(no user decision this run)",
                        len(pending_removals), backend.name,
                    )

            # ---- Push additions ----
            local_items = local_to_sync_items(self._repo)
            if self._cancel_requested:
                return result
            if not local_items:
                _log.info("No local items to push to %s, skipping push", backend.name)
            else:
                self._status.status_text = f"Pushing to {display}…"
                self._fire_on_done()
                time.sleep(0.8)  # let sidebar render the label
                _log.info("Pushing %d local items to %s", len(local_items), backend.name)
                push_result = backend.push(local_items)
                result.pushed = push_result.pushed
                result.errors.extend(push_result.errors)
                self._status.pushed_added += push_result.pushed

                # ---- Record pushed state ----
                self._repo.update_pushed_items(backend.name, local_items)

        return result

    def _backfill_search_fallback(self, tmdb_id, title, year, media_type):
        """Title search fallback for items whose TMDB ID lookup failed."""
        if not title:
            return
        try:
            result = self._metadata_service.search_best(
                title, year, media_type,
            )
        except Exception:
            _log.debug("Title search failed for %r", title)
            return
        if result and result.tmdb_id != tmdb_id:
            _log.info(
                "Title search found %s (tmdb=%d) for '%s' — caching",
                media_type, result.tmdb_id, title,
            )
            self._metadata_service._cache.put_media(result)

    def _backfill_metadata(self):
        """Fetch full TMDB metadata for items missing poster_url/collection_id.

        Runs inline during sync (enriching phase).  Populates poster_url,
        collection_id, collection_name via the TMDB cache so sagas and
        posters appear on cards.  Reports progress every 10 items.
        Falls back to title+year search when the TMDB ID lookup fails
        (common for anime cross-mapped by Simkl).
        """
        if self._repo is None or self._metadata_service is None:
            return
        try:
            targets = self._repo.get_media_missing_posters()
            if not targets:
                return
            total = len(targets)
            self._status.backfill_total = total
            self._status.backfill_current = 0
            _log.info("Backfilling metadata for %d items", total)
            for i, (tmdb_id, media_type, title, year) in enumerate(targets):
                if self._cancel_requested:
                    break
                if media_type == "show":
                    try:
                        self._metadata_service.get_show(
                            tmdb_id, refresh=True,
                        )
                    except Exception as exc:
                        _log.debug(
                            "TMDB ID %d (%s) lookup failed: %s — "
                            "trying title search",
                            tmdb_id, title, exc,
                        )
                        self._backfill_search_fallback(
                            tmdb_id, title, year, media_type,
                        )
                else:
                    try:
                        self._metadata_service.get_movie(
                            tmdb_id, refresh=True,
                        )
                    except Exception as exc:
                        _log.debug(
                            "TMDB ID %d (%s) lookup failed: %s — "
                            "trying title search",
                            tmdb_id, title, exc,
                        )
                        self._backfill_search_fallback(
                            tmdb_id, title, year, media_type,
                        )
                # Still no poster after a fresh fetch (junk ID or a
                # genuinely posterless unreleased title)?  Don't hammer
                # TMDB for it again on the next syncs.
                if self._repo.poster_missing(tmdb_id):
                    self._repo.mark_poster_attempted(tmdb_id)
                self._status.backfill_current = i + 1
                if (i + 1) % 10 == 0 or i + 1 == total:
                    self._fire_on_done()
            _log.info("Metadata backfill complete")
            merged = self._repo.merge_orphaned_media()
            if merged:
                _log.info("Merged %d orphaned media_items rows", merged)
        except Exception:
            _log.exception("Metadata backfill failed")
