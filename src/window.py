import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, Gio, GLib

from . import poster_cache
from . import config
from .ui import anim


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, user_repo=None, metadata_service=None, **kwargs):
        super().__init__(**kwargs)
        self.set_title("Ciak")
        self.set_default_icon_name(config.APP_ID)
        self.user_repo = user_repo
        self.metadata_service = metadata_service
        self.settings = Gio.Settings.new(config.APP_ID)

        self.set_default_size(
            self.settings.get_int("window-width"),
            self.settings.get_int("window-height"),
        )

        if self.settings.get_boolean("window-maximized"):
            self.maximize()

        self.set_size_request(750, 750)

        self._apply_animations_pref()
        self.settings.connect(
            "changed::disable-animations", self._on_animations_pref_changed
        )

        self.connect("close-request", self._on_close)

    def _apply_animations_pref(self):
        enabled = not self.settings.get_boolean("disable-animations")
        anim.set_animations_enabled(enabled)
        try:
            Gtk.Settings.get_default().set_property(
                "gtk-enable-animations", enabled
            )
        except GLib.Error:
            pass

    def _on_animations_pref_changed(self, settings, _key):
        self._apply_animations_pref()

    def set_page(self, widget):
        self._toast_overlay = Adw.ToastOverlay()
        self._toast_overlay.set_child(widget)
        Adw.ApplicationWindow.set_content(self, self._toast_overlay)

    def _on_close(self, *args):
        width, height = self.get_default_size()
        self.settings.set_int("window-width", width)
        self.settings.set_int("window-height", height)
        self.settings.set_boolean("window-maximized", self.is_maximized())

        if self.settings.get_boolean("clear-cache-on-exit"):
            poster_cache.clear()
