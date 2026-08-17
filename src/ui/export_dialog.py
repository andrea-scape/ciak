"""Export format picker dialog and file save flow."""

from __future__ import annotations

import logging
import os
import sys

from gi.repository import Gtk, Adw, Gio, GLib

from ..data.export import (
    write_trakt_csv,
    write_letterboxd_csv,
    write_imdb_csv,
    write_json,
)
from .. import threads

log = logging.getLogger(__name__)


_FORMATS = [
    {
        "id": "trakt-csv",
        "label": "Trakt CSV",
        "subtitle": "Compatible with Trakt import",
        "extension": ".csv",
        "writer": write_trakt_csv,
    },
    {
        "id": "letterboxd-csv",
        "label": "Letterboxd CSV",
        "subtitle": "Compatible with Letterboxd import",
        "extension": ".csv",
        "writer": write_letterboxd_csv,
    },
    {
        "id": "imdb-csv",
        "label": "IMDb CSV",
        "subtitle": "Compatible with IMDb list import",
        "extension": ".csv",
        "writer": write_imdb_csv,
    },
    {
        "id": "json",
        "label": "JSON",
        "subtitle": "Full structured data dump",
        "extension": ".json",
        "writer": write_json,
    },
]


def _get_main_window() -> Gtk.Window | None:
    """Get the application's main window."""
    app = Gio.Application.get_default()
    if app is None:
        log.warning("No application default")
        return None
    win = app.get_active_window()
    if win is not None:
        return win
    windows = app.get_windows()
    return windows[0] if windows else None


def _show_toast_on_main(toast: Adw.Toast) -> None:
    """Show a toast on the main window's ToastOverlay."""
    window = _get_main_window()
    if window is None:
        log.warning("No main window for toast")
        return
    # Prefer the stored _toast_overlay reference (set by MainWindow.set_page)
    overlay = getattr(window, "_toast_overlay", None)
    if overlay is None:
        overlay = _find_toast_overlay(window)
    if overlay is not None:
        overlay.add_toast(toast)
    else:
        log.warning("No ToastOverlay found on main window")


def _find_toast_overlay(widget: Gtk.Widget) -> Adw.ToastOverlay | None:
    """Walk the widget tree to find a ToastOverlay."""
    if isinstance(widget, Adw.ToastOverlay):
        return widget
    if not hasattr(widget, "get_first_child"):
        return None
    child = widget.get_first_child()
    while child is not None:
        result = _find_toast_overlay(child)
        if result is not None:
            return result
        child = child.get_next_sibling()
    return None


class ExportFormatDialog(Adw.Dialog):
    """Modal dialog that lets the user choose an export format."""

    def __init__(self, parent: Gtk.Widget, repository):
        super().__init__()
        self.set_title("Export Data")
        self.set_content_width(400)
        self.set_presentation_mode(Adw.DialogPresentationMode.AUTO)
        self._repository = repository
        self._selected_format = None

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_margin_top(24)
        box.set_margin_bottom(24)
        box.set_margin_start(24)
        box.set_margin_end(24)

        title_label = Gtk.Label(label="Choose export format")
        title_label.add_css_class("title-1")
        title_label.set_halign(Gtk.Align.START)
        box.append(title_label)

        group = Adw.PreferencesGroup()

        for fmt in _FORMATS:
            row = Adw.ActionRow()
            row.set_title(fmt["label"])
            row.set_subtitle(fmt["subtitle"])
            row.set_activatable(True)
            row.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))
            row.connect("activated", self._on_format_selected, fmt)
            group.add(row)

        box.append(group)

        cancel_btn = Gtk.Button(label="Cancel")
        cancel_btn.set_halign(Gtk.Align.END)
        cancel_btn.add_css_class("flat")
        cancel_btn.connect("clicked", lambda _: self.close())
        box.append(cancel_btn)

        self.set_child(box)
        self.present(parent)

    def _on_format_selected(self, _row, fmt):
        self._selected_format = fmt
        self._open_file_save_dialog()

    def _open_file_save_dialog(self):
        dialog = Gtk.FileDialog()
        dialog.set_title("Save Export")
        ext = self._selected_format["extension"]
        basename = f"ciak-export{ext}"
        dialog.set_initial_name(basename)

        # Set file type filter
        filter_csv = Gtk.FileFilter()
        filter_csv.set_name(f"{self._selected_format['label']} (*{ext})")
        filter_csv.add_pattern(f"*{ext}")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(filter_csv)
        all_filter = Gtk.FileFilter()
        all_filter.set_name("All files")
        all_filter.add_pattern("*")
        filters.append(all_filter)
        dialog.set_filters(filters)

        # Get a Gtk.Window for the FileDialog parent.
        # get_native() works while this Adw.Dialog is still open.
        # Fall back to the app's active window.
        native = self.get_native()
        if native is None:
            native = _get_main_window()
        log.info("FileDialog parent: %r", native)
        dialog.save(native, None, self._on_file_save_response)

    def _on_file_save_response(self, dialog, result):
        try:
            file = dialog.save_finish(result)
        except GLib.Error as exc:
            log.info("FileDialog cancelled or failed: %s", exc.message)
            self.close()
            return

        # get_path() may return None for remote locations; fall back to URI
        path = file.get_path()
        if path is None:
            uri = file.get_uri()
            log.info("get_path() returned None, URI: %s", uri)
            if uri and uri.startswith("file://"):
                from urllib.parse import urlparse
                path = urlparse(uri).path
        if path is None:
            log.warning("Cannot resolve file path from %s", file.get_uri())
            _show_toast_on_main(Adw.Toast.new("Cannot save to this location"))
            self.close()
            return

        log.info("Export path resolved: %s", path)

        # Ensure parent directory exists
        parent_dir = os.path.dirname(path)
        if parent_dir and not os.path.isdir(parent_dir):
            os.makedirs(parent_dir, exist_ok=True)

        # Close the format picker now that we have the save path
        self.close()

        writer = self._selected_format["writer"]

        def _work():
            log.info("Export worker started for %s", path)
            data = self._repository.get_export_data()
            log.info(
                "Export data: watched=%d watchlist=%d ratings=%d collection=%d",
                len(data.watched), len(data.watchlist),
                len(data.ratings), len(data.collection),
            )
            writer(data, path)
            exists = os.path.exists(path)
            size = os.path.getsize(path) if exists else 0
            log.info("Export written: %s exists=%s size=%d", path, exists, size)
            return path

        def _done(future):
            try:
                saved_path = future.result()
                log.info("Export succeeded: %s", saved_path)
                _show_toast_on_main(
                    Adw.Toast.new(f"Exported to {os.path.basename(saved_path)}")
                )
            except Exception as exc:
                log.error("Export failed: %s", exc, exc_info=True)
                _show_toast_on_main(
                    Adw.Toast.new(f"Export failed: {exc}")
                )

        future = threads.submit(_work)
        future.add_done_callback(lambda f: GLib.idle_add(_done, f))


def show_export_dialog(parent: Gtk.Widget, repository) -> None:
    """Entry point: show the export format picker."""
    ExportFormatDialog(parent, repository)
