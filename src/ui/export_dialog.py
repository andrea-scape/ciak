"""Export format picker dialog and file save flow."""

from __future__ import annotations

from gi.repository import Gtk, Adw, Gio, GLib

from ..data.export import (
    ExportData,
    write_trakt_csv,
    write_letterboxd_csv,
    write_imdb_csv,
    write_json,
)
from .. import threads


_FORMATS = [
    {
        "id": "trakt-csv",
        "label": "Trakt CSV",
        "subtitle": "Compatible with Trakt import",
        "extension": ".csv",
        "mime": "text/csv",
        "writer": write_trakt_csv,
    },
    {
        "id": "letterboxd-csv",
        "label": "Letterboxd CSV",
        "subtitle": "Compatible with Letterboxd import",
        "extension": ".csv",
        "mime": "text/csv",
        "writer": write_letterboxd_csv,
    },
    {
        "id": "imdb-csv",
        "label": "IMDb CSV",
        "subtitle": "Compatible with IMDb list import",
        "extension": ".csv",
        "mime": "text/csv",
        "writer": write_imdb_csv,
    },
    {
        "id": "json",
        "label": "JSON",
        "subtitle": "Full structured data dump",
        "extension": ".json",
        "mime": "application/json",
        "writer": write_json,
    },
]


class ExportFormatDialog(Adw.Dialog):
    """Modal dialog that lets the user choose an export format."""

    def __init__(self, parent: Gtk.Widget, repository):
        super().__init__()
        self.set_title("Export Data")
        self.set_content_width(400)
        self.set_presentation_mode(Adw.DialogPresentationMode.AUTOMATIC)
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
        group.set_selection_mode(Gtk.SelectionMode.SINGLE)

        for fmt in _FORMATS:
            row = Adw.ActionRow()
            row.set_title(fmt["label"])
            row.set_subtitle(fmt["subtitle"])
            row.set_activatable(True)
            row.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))
            row._format_id = fmt["id"]
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
        self.close()
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

        dialog.save(self, None, self._on_file_save_response)

    def _on_file_save_response(self, dialog, result):
        try:
            file = dialog.save_finish(result)
        except GLib.Error:
            return  # user cancelled

        path = file.get_path()
        if path is None:
            return

        writer = self._selected_format["writer"]

        def _work():
            data = self._repository.get_export_data()
            writer(data, path)
            return path

        def _done(future):
            try:
                saved_path = future.result()
                toast = Adw.Toast.new(f"Exported to {saved_path}")
                self._show_toast(toast)
            except Exception as exc:
                toast = Adw.Toast.new(f"Export failed: {exc}")
                self._show_toast(toast)

        future = threads.submit(_work)
        future.add_done_callback(lambda f: GLib.idle_add(_done, f))

    def _show_toast(self, toast: Adw.Toast):
        """Find the nearest ToastOverlay ancestor and present a toast."""
        widget = self.get_parent()
        while widget is not None:
            if isinstance(widget, Adw.ToastOverlay):
                widget.add_toast(toast)
                return
            widget = widget.get_parent() if hasattr(widget, "get_parent") else None


def show_export_dialog(parent: Gtk.Widget, repository) -> None:
    """Entry point: show the export format picker."""
    ExportFormatDialog(parent, repository)
