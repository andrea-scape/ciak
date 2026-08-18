"""Import dialog: pick a CSV/JSON file, preview matches, bulk-import."""

from __future__ import annotations

import os
import traceback

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, Gio, GLib

from .. import config
from .. import threads
from ..data.importers import (
    ImportParseError,
    Matcher,
    date_to_ts,
    select_parser,
)
from .export_dialog import (
    _get_main_window,
    _show_error_dialog,
    _show_toast,
)

_STATUS_COLORS = {
    "matched": (0.25, 0.72, 0.25, 1.0),
    "duplicate": (0.95, 0.76, 0.05, 1.0),
    "unmatched": (0.90, 0.30, 0.30, 1.0),
}


def _status_dot(status: str) -> Gtk.Widget:
    """Small filled-circle indicator for a match status."""
    import cairo

    color = _STATUS_COLORS.get(status, (0.6, 0.6, 0.6, 1.0))
    area = Gtk.DrawingArea()
    area.set_size_request(14, 14)

    def _draw(_widget, cr):
        cr.set_source_rgba(*color)
        cr.arc(7, 7, 6, 0, 2 * 3.14159)
        cr.fill()

    area.set_draw_func(_draw)
    return area


class ImportPreviewDialog(Adw.Dialog):
    """Shows parsed+matched items and imports the checked ones."""

    def __init__(self, parent: Gtk.Widget, repository, metadata_service):
        super().__init__()
        self.set_title("Import Data")
        self.set_content_width(640)
        self.set_content_height(520)
        self.set_presentation_mode(Adw.DialogPresentationMode.AUTO)
        self._parent = parent
        self._repository = repository
        self._metadata_service = metadata_service
        self._results: list = []
        self._checkboxes: list[Gtk.CheckButton] = []
        self._import_btn = None
        self._summary_label = None
        self._list_box = None
        self.present(parent)

        self._open_file_dialog()

    # ------------------------------------------------------------------
    # File selection
    # ------------------------------------------------------------------

    def _open_file_dialog(self):
        self._file_dialog = Gtk.FileDialog()
        self._file_dialog.set_title("Choose a file to import")
        filter_csv = Gtk.FileFilter()
        filter_csv.set_name("CSV, JSON or Trakt export")
        filter_csv.add_pattern("*.csv")
        filter_csv.add_pattern("*.json")
        filter_csv.add_pattern("*.zip")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(filter_csv)
        all_filter = Gtk.FileFilter()
        all_filter.set_name("All files")
        all_filter.add_pattern("*")
        filters.append(all_filter)
        self._file_dialog.set_filters(filters)

        window = _get_main_window()
        self._file_dialog.open(window, None, self._on_open_response)

    def _on_open_response(self, dialog, result):
        try:
            file = dialog.open_finish(result)
        except GLib.Error as e:
            if e.code in (Gtk.DialogError.CANCELLED, Gtk.DialogError.DISMISSED):
                self.close()
                return
            _show_error_dialog("Could not open file", str(e), self._parent)
            self.close()
            return

        path = file.get_path()
        if path is None:
            uri = file.get_uri()
            if uri and uri.startswith("file://"):
                from urllib.parse import urlparse
                path = urlparse(uri).path
        if path is None:
            _show_error_dialog(
                "Cannot read this location",
                f"No local path resolved.\nURI: {file.get_uri()}",
                self._parent,
            )
            self.close()
            return

        self._build_loading_view(os.path.basename(path))
        self._run_parse_and_match(path)

    # ------------------------------------------------------------------
    # Background parse + match
    # ------------------------------------------------------------------

    def _build_loading_view(self, filename: str):
        self._clear_content()
        spinner = Gtk.Spinner()
        spinner.set_size_request(48, 48)
        spinner.start()
        label = Gtk.Label(
            label=f"Parsing and matching {filename}…"
        )
        label.add_css_class("dim-label")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_valign(Gtk.Align.CENTER)
        box.append(spinner)
        box.append(label)
        self.set_child(box)

    def _clear_content(self):
        self.set_child(Gtk.Box(orientation=Gtk.Orientation.VERTICAL))

    def _run_parse_and_match(self, path: str):
        parser = select_parser(path)

        def _work():
            items = parser.parse(path)
            matcher = Matcher(self._repository, self._metadata_service)
            return [matcher.match(item) for item in items]

        def _done(future):
            try:
                results = future.result()
            except ImportParseError as exc:
                self.close()
                _show_error_dialog("Cannot import this file", str(exc), self._parent)
                return
            except Exception as exc:
                self.close()
                tb_text = "".join(
                    traceback.format_exception(type(exc), exc, exc.__traceback__)
                )
                _show_error_dialog(str(exc), tb_text, self._parent)
                return
            self._build_preview(results)

        future = threads.submit(_work)
        future.add_done_callback(lambda f: GLib.idle_add(_done, f))

    # ------------------------------------------------------------------
    # Preview
    # ------------------------------------------------------------------

    def _build_preview(self, results):
        self._results = results
        self._checkboxes = []

        matched = sum(1 for r in results if r.status == "matched")
        duplicates = sum(1 for r in results if r.status == "duplicate")
        unmatched = sum(1 for r in results if r.status == "unmatched")
        selectable = matched + duplicates

        header = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        header.set_margin_top(16)
        header.set_margin_start(16)
        header.set_margin_end(16)
        header.set_margin_bottom(8)

        title = Gtk.Label(label="Items to import")
        title.add_css_class("title-1")
        title.set_halign(Gtk.Align.START)
        header.append(title)

        self._summary_label = Gtk.Label(
            label=(
                f"{matched} matched · {duplicates} duplicates · "
                f"{unmatched} unmatched"
            )
        )
        self._summary_label.add_css_class("dim-label")
        self._summary_label.set_halign(Gtk.Align.START)
        header.append(self._summary_label)

        self._list_box = Gtk.ListBox()
        self._list_box.add_css_class("boxed-list")
        self._list_box.set_selection_mode(Gtk.SelectionMode.NONE)
        for result in results:
            self._list_box.append(self._make_row(result))

        scroller = Gtk.ScrolledWindow()
        scroller.set_vexpand(True)
        scroller.set_child(self._list_box)

        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        footer.set_margin_top(12)
        footer.set_margin_bottom(16)
        footer.set_margin_start(16)
        footer.set_margin_end(16)

        cancel_btn = Gtk.Button(label="Cancel")
        cancel_btn.add_css_class("flat")
        cancel_btn.connect("clicked", lambda _b: self.close())

        self._import_btn = Gtk.Button(label="")
        self._import_btn.add_css_class("suggested-action")
        self._import_btn.connect("clicked", self._on_import_clicked)
        self._update_import_button()

        footer.append(cancel_btn)
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        box.set_hexpand(True)
        footer.append(box)
        footer.append(self._import_btn)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        outer.append(header)
        outer.append(scroller)
        outer.append(footer)
        self.set_child(outer)

    def _make_row(self, result) -> Gtk.Widget:
        row = Gtk.ListBoxRow()
        hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        hbox.set_margin_top(6)
        hbox.set_margin_bottom(6)
        hbox.set_margin_start(8)
        hbox.set_margin_end(8)
        hbox.set_valign(Gtk.Align.CENTER)

        hbox.append(_status_dot(result.status))

        text_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        title = Gtk.Label(label=result.item.title or "Unknown")
        title.set_halign(Gtk.Align.START)
        title.add_css_class("body")
        text_box.append(title)

        subtitle_parts = []
        if result.item.season_number is not None:
            subtitle_parts.append(
                f"S{result.item.season_number:02d}"
                f"E{result.item.episode_number or 0:02d}"
            )
        if result.item.year:
            subtitle_parts.append(str(result.item.year))
        if result.tmdb_id:
            subtitle_parts.append(f"TMDB {result.tmdb_id}")
        if result.status == "unmatched":
            subtitle_parts.append("no match")
        elif result.status == "duplicate":
            subtitle_parts.append("already in library")
        else:
            subtitle_parts.append(result.item.target)
        subtitle = Gtk.Label(label=" · ".join(subtitle_parts))
        subtitle.set_halign(Gtk.Align.START)
        subtitle.add_css_class("caption")
        subtitle.add_css_class("dim-label")
        text_box.append(subtitle)

        hbox.append(text_box)

        checkbox = Gtk.CheckButton()
        checkbox.set_active(result.status != "unmatched")
        checkbox.set_valign(Gtk.Align.CENTER)
        checkbox.connect("toggled", lambda _c: self._update_import_button())
        self._checkboxes.append(checkbox)
        hbox.append(checkbox)

        row.set_child(hbox)
        return row

    def _update_import_button(self):
        if self._import_btn is None:
            return
        count = sum(
            1 for cb in self._checkboxes if cb.get_active()
        )
        self._import_btn.set_label(
            f"Import {count} item{'s' if count != 1 else ''}"
        )
        self._import_btn.set_sensitive(count > 0)

    # ------------------------------------------------------------------
    # Commit
    # ------------------------------------------------------------------

    def _selected_results(self):
        for result, checkbox in zip(self._results, self._checkboxes):
            if checkbox.get_active() and result.status != "unmatched":
                yield result

    def _on_import_clicked(self, _btn):
        selected = list(self._selected_results())
        if not selected:
            return

        watched: list[dict] = []
        watchlist: list[dict] = []
        ratings: list[dict] = []
        collection: list[dict] = []

        for result in selected:
            item = result.item
            base = {
                "tmdb_id": result.tmdb_id,
                "media_type": result.media_type or item.media_type or "movie",
                "title": item.title,
                "year": item.year,
                "imdb_id": item.imdb_id,
            }
            if item.show_tmdb_id:
                base["show_tmdb_id"] = item.show_tmdb_id
            if item.season_number is not None:
                base["season_number"] = item.season_number
            if item.episode_number is not None:
                base["episode_number"] = item.episode_number
            ts = date_to_ts(item.watched_date) or None
            target = item.target
            if target == "watchlist":
                watchlist.append({**base, "added_at": ts})
            elif target == "ratings":
                ratings.append({**base, "rating": item.rating, "rated_at": ts})
            elif target == "collection":
                collection.append({**base, "collected_at": ts})
            else:
                watched.append({**base, "watched_at": ts})

        self._import_btn.set_sensitive(False)
        self._import_btn.set_label("Importing…")
        repo = self._repository

        def _work():
            counts = {
                "watched": repo.import_watched(watched),
                "watchlist": repo.import_watchlist(watchlist),
                "ratings": repo.import_ratings(ratings),
            }
            return sum(counts.values())

        def _done(future):
            try:
                total = future.result()
            except Exception as exc:
                tb_text = "".join(
                    traceback.format_exception(type(exc), exc, exc.__traceback__)
                )
                _show_error_dialog(str(exc), tb_text, self._parent)
                self.close()
                return
            self.close()
            toast = Adw.Toast.new(
                f"Imported {total} item{'s' if total != 1 else ''}"
            )
            toast.set_timeout(10)
            _show_toast(toast, self._parent)

        future = threads.submit(_work)
        future.add_done_callback(lambda f: GLib.idle_add(_done, f))


def show_import_dialog(parent: Gtk.Widget, repository, metadata_service) -> None:
    """Entry point. Requires a configured TMDB API key for matching."""
    settings = Gio.Settings.new(config.APP_ID)
    if not settings.get_string("tmdb-api-key").strip():
        dialog = Adw.AlertDialog.new(
            "TMDB API Key Required",
            "Importing needs a TMDB API key so titles can be matched. "
            "Add one in Preferences to continue.",
        )
        dialog.add_response("ok", "OK")
        dialog.set_default_response("ok")
        dialog.set_close_response("ok")
        dialog.present(parent)
        return

    ImportPreviewDialog(parent, repository, metadata_service)