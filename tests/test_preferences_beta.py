"""Settings BETA chip: the Cloud Sync group carries an accent "BETA" pill."""

import types
import unittest

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw


class _Settings:
    def __init__(self, values=None):
        self._values = dict(values or {})
        self._bools = dict(self._values.get("bools", {}))
        self._strings = dict(self._values.get("strings", {}))
        self._ints = dict(self._values.get("ints", {}))
        self.writes = []

    def get_boolean(self, key):
        return self._bools.get(key, False)

    def get_string(self, key):
        return self._strings.get(key, "")

    def get_int(self, key):
        return self._ints.get(key, 0)

    def get_int64(self, key):
        return self._ints.get(key, 0)

    def set_boolean(self, key, value):
        self.writes.append(("ssl", key, value))
        self._bools[key] = value

    def set_string(self, key, value):
        self.writes.append(("str", key, value))
        self._strings[key] = value

    def set_int(self, key, value):
        self._ints[key] = value
        self.writes.append(("int", key, value))

    def bind(self, key, widget, prop, flags):
        self.writes.append(("bind", key))

    def connect(self, key, callback):
        return 0


class PreferencesBetaChipTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            Gtk.init()
            Adw.init()
        except TypeError:
            pass

    def test_cloud_sync_group_has_beta_chip_suffix(self):
        from src.ui.preferences_page import PreferencesPage

        win = types.SimpleNamespace(settings=_Settings())
        page = PreferencesPage(win, main_page=None)
        pill = page._sync_beta_pill

        self.assertIsInstance(pill, Gtk.Label)
        self.assertEqual(pill.get_text(), "BETA")
        self.assertTrue(pill.has_css_class("beta-chip-pill"))
        self.assertTrue(pill.has_css_class("beta-chip"))


if __name__ == "__main__":
    unittest.main()