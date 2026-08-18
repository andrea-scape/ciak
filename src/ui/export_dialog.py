"""Export format picker dialog and file save flow."""

from __future__ import annotations

import os
import traceback
from datetime import date

from gi.repository import Gtk, Adw, Gio, GLib, Gdk

from ..data.export import (
    write_trakt_csv,
    write_letterboxd_csv,
    write_imdb_csv,
    write_json,
)
from .. import threads


_FORMATS = [
    {
        "id": "trakt-csv",
        "short": "trakt",
        "label": "Trakt CSV",
        "subtitle": "Compatible with Trakt import",
        "extension": ".csv",
        "writer": write_trakt_csv,
    },
    {
        "id": "letterboxd-csv",
        "short": "letterboxd",
        "label": "Letterboxd CSV",
        "subtitle": "Compatible with Letterboxd import",
        "extension": ".csv",
        "writer": write_letterboxd_csv,
    },
    {
        "id": "imdb-csv",
        "short": "imdb",
        "label": "IMDb CSV",
        "subtitle": "Compatible with IMDb list import",
        "extension": ".csv",
        "writer": write_imdb_csv,
    },
    {
        "id": "json",
        "short": "json",
        "label": "JSON",
        "subtitle": "Full structured data dump",
        "extension": ".json",
        "writer": write_json,
    },
]


def _get_main_window() -> Gtk.Window | None:
    """Get the application's main Gtk.Window."""
    app = Gio.Application.get_default()
    if app is None:
        return None
    win = app.get_active_window()
    if win is not None:
        return win
    windows = app.get_windows()
    return windows[0] if windows else None


def _show_toast(toast: Adw.Toast, parent: Gtk.Widget | None = None) -> None:
    """Show a toast, preferring a dialog's own overlay over the main window."""
    if parent is not None and hasattr(parent, "add_toast"):
        parent.add_toast(toast)
        return
    window = _get_main_window()
    if window is None:
        return
    overlay = getattr(window, "_toast_overlay", None)
    if overlay is not None:
        overlay.add_toast(toast)


def _reveal_file_in_file_manager(path: str) -> None:
    """Reveal a file in the system file manager, if possible."""
    file_uri = Gio.File.new_for_path(path).get_uri()
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        proxy = Gio.DBusProxy.new_sync(
            bus,
            Gio.DBusProxyFlags.NONE,
            None,
            "org.freedesktop.FileManager1",
            "/org/freedesktop/FileManager1",
            "org.freedesktop.FileManager1",
            None,
        )
        proxy.call_sync(
            "ShowItems",
            GLib.Variant("(ass)", ([file_uri], "")),
            Gio.DBusCallFlags.NONE,
            -1,
            None,
        )
        return
    except Exception:
        pass
    folder_uri = Gio.File.new_for_path(os.path.dirname(path)).get_uri()
    Gio.app_info_launch_default_for_uri(folder_uri, None)


def _show_error_dialog(
    message: str, tb_text: str, parent: Gtk.Widget | None = None
) -> None:
    """Show an error dialog with the full error and a Copy button."""
    target = parent if parent is not None else _get_main_window()
    if target is None:
        return

    dialog = Adw.AlertDialog.new("Export Failed", message)
    dialog.add_response("copy", "Copy Error")
    dialog.add_response("close", "Close")
    dialog.set_default_response("close")
    dialog.set_close_response("close")

    def _on_response(_dlg, response):
        if response == "copy":
            text = f"{message}\n\n{tb_text}"
            clipboard = Gdk.Display.get_default().get_clipboard()
            clipboard.set(text)
            _show_toast(Adw.Toast.new("Error copied to clipboard"), parent)

    dialog.connect("response", _on_response)
    dialog.present(target)


class ExportFormatDialog(Adw.Dialog):
    """Modal dialog that lets the user choose an export format."""

    def __init__(self, parent: Gtk.Widget, repository):
        super().__init__()
        self.set_title("Export Data")
        self.set_content_width(400)
        self.set_presentation_mode(Adw.DialogPresentationMode.AUTO)
        self._repository = repository
        self._parent = parent
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
        self._file_dialog = Gtk.FileDialog()
        self._file_dialog.set_title("Save Export")
        ext = self._selected_format["extension"]
        short = self._selected_format["short"]
        basename = f"ciak-export-{date.today().isoformat()}({short}){ext}"
        self._file_dialog.set_initial_name(basename)

        filter_fmt = Gtk.FileFilter()
        filter_fmt.set_name(f"{self._selected_format['label']} (*{ext})")
        filter_fmt.add_pattern(f"*{ext}")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(filter_fmt)
        all_filter = Gtk.FileFilter()
        all_filter.set_name("All files")
        all_filter.add_pattern("*")
        filters.append(all_filter)
        self._file_dialog.set_filters(filters)

        # Gtk.FileDialog.save() requires a Gtk.Window, not a Gtk.Native.
        # Always use the application's main window.
        window = _get_main_window()
        self._file_dialog.save(window, None, self._on_file_save_response)

    def _on_file_save_response(self, dialog, result):
        try:
            file = dialog.save_finish(result)
        except GLib.Error as e:
            if e.code in (Gtk.DialogError.CANCELLED, Gtk.DialogError.DISMISSED):
                self.close()
                return
            _show_error_dialog(
                "Could not save file",
                f"{e}\n\nA common cause is a Flatpak permission issue. "
                "Try saving to a folder you can access.",
                self._parent,
            )
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
                "Cannot save to this location",
                f"No local path resolved.\nURI: {file.get_uri()}",
                self._parent,
            )
            self.close()
            return

        ext = self._selected_format["extension"]
        if not path.lower().endswith(ext):
            path += ext

        parent_dir = os.path.dirname(path)
        if parent_dir and not os.path.isdir(parent_dir):
            try:
                os.makedirs(parent_dir, exist_ok=True)
            except OSError as exc:
                _show_error_dialog(
                    "Could not create the export folder",
                    str(exc),
                    self._parent,
                )
                self.close()
                return

        fmt = self._selected_format
        self.close()

        def _work():
            data = self._repository.get_export_data()
            count = fmt["writer"](data, path)
            return path, count

        def _done(future):
            try:
                saved_path, count = future.result()
            except Exception as exc:
                tb_lines = traceback.format_exception(
                    type(exc), exc, exc.__traceback__
                )
                tb_text = "".join(tb_lines)
                _show_error_dialog(str(exc), tb_text, self._parent)
                return

            if count == 0:
                toast = Adw.Toast.new(
                    f"No items to export for {fmt['label']} — "
                    "the file only contains headers."
                )
            else:
                toast = Adw.Toast.new(
                    f"Exported to {os.path.basename(saved_path)}"
                )
            toast.set_timeout(10)
            toast.set_button_label("Open")
            toast.connect(
                "button-clicked",
                lambda _t, p=saved_path: _reveal_file_in_file_manager(p),
            )
            _show_toast(toast, self._parent)

        future = threads.submit(_work)
        future.add_done_callback(lambda f: GLib.idle_add(_done, f))


def show_export_dialog(parent: Gtk.Widget, repository) -> None:
    """Entry point: show the export format picker."""
    ExportFormatDialog(parent, repository)
