import sys
import types
import unittest
from unittest import mock

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw

# config.py is generated at build time; provide the bits used at import time.
_cfg = types.ModuleType("src.config")
_cfg.APP_ID = "io.github.andrea_scape.ciak.Devel"
sys.modules.setdefault("src.config", _cfg)

from src.data.importers import ImportItem, MatchResult
from src.ui.import_dialog import ImportPreviewDialog


def make_result(status, title, target="watched"):
    return MatchResult(
        status=status,
        tmdb_id=1,
        media_type="movie",
        item=ImportItem(title=title, target=target),
    )


def make_dialog(results):
    dialog = ImportPreviewDialog.__new__(ImportPreviewDialog)
    dialog._results = []
    dialog._checkboxes = []
    dialog._import_btn = None
    dialog._summary_label = None
    dialog._list_box = None
    dialog._toggle_btn = None
    dialog.set_child = lambda _w: None
    dialog._build_preview(results)
    return dialog


class ImportToggleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            Gtk.init()
            Adw.init()
        except TypeError:
            pass

    def test_toggle_checks_all_selectable_rows(self):
        dialog = make_dialog([
            make_result("matched", "A"),
            make_result("matched", "B"),
            make_result("unmatched", "C"),
        ])
        for cb in dialog._checkboxes:
            cb.set_active(False)
        dialog._on_toggle_all_clicked(None)
        states = [cb.get_active() for cb in dialog._checkboxes]
        self.assertEqual(states, [True, True, False])

    def test_toggle_unchecks_all_selectable_rows(self):
        dialog = make_dialog([
            make_result("matched", "A"),
            make_result("duplicate", "B"),
        ])
        dialog._on_toggle_all_clicked(None)
        states = [cb.get_active() for cb in dialog._checkboxes]
        self.assertEqual(states, [False, False])

    def test_toggle_label_tracks_state(self):
        dialog = make_dialog([make_result("matched", "A")])
        self.assertEqual(dialog._toggle_btn.get_label(), "Uncheck all")
        dialog._on_toggle_all_clicked(None)
        self.assertEqual(dialog._toggle_btn.get_label(), "Check all")
        dialog._on_toggle_all_clicked(None)
        self.assertEqual(dialog._toggle_btn.get_label(), "Uncheck all")

    def test_toggle_updates_import_count(self):
        dialog = make_dialog([
            make_result("matched", "A"),
            make_result("unmatched", "B"),
        ])
        dialog._on_toggle_all_clicked(None)
        self.assertEqual(dialog._import_btn.get_label(), "Import 0 items")


class ImportRowOrderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            Gtk.init()
            Adw.init()
        except TypeError:
            pass

    def test_checkbox_comes_before_media_name(self):
        dialog = make_dialog([make_result("matched", "A")])
        row = dialog._list_box.get_first_child()
        hbox = row.get_child()
        children = []
        child = hbox.get_first_child()
        while child:
            children.append(child)
            child = child.get_next_sibling()
        # status dot, checkbox, then the text box with the media name.
        self.assertIsInstance(children[1], Gtk.CheckButton)
        self.assertIsInstance(children[2], Gtk.Box)


if __name__ == "__main__":
    unittest.main()