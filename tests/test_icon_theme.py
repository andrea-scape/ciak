import os
import tempfile
import unittest

from src.icon_theme import _icon_theme_available


class IconThemeAvailableTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def test_found_when_theme_directory_exists(self):
        os.makedirs(os.path.join(self._tmp.name, "Adwaita"))
        self.assertTrue(_icon_theme_available("Adwaita", [self._tmp.name]))

    def test_missing_when_theme_not_in_search_path(self):
        os.makedirs(os.path.join(self._tmp.name, "Adwaita"))
        self.assertFalse(_icon_theme_available("hatter", [self._tmp.name]))

    def test_missing_when_search_path_empty(self):
        self.assertFalse(_icon_theme_available("Adwaita", []))

    def test_found_in_any_search_path_directory(self):
        os.makedirs(os.path.join(self._tmp.name, "Adwaita"))
        dirs = ["/nonexistent/a", self._tmp.name, "/nonexistent/b"]
        self.assertTrue(_icon_theme_available("Adwaita", dirs))
