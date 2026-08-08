import sys
import os
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
from gi.repository import Gtk, Adw, Gio, Gdk, GLib

from . import config
from . import poster_cache
from .theme import apply_theme
from .icon_theme import _icon_theme_available
from .domain.onboarding import needs_onboarding
from .data.local.repository import LocalMediaRepository
from .data.local.cache import MetadataCache
from .data.tmdb.client import TmdbClient
from .data.tmdb.service import TmdbMetadataService
from .ui.main_page import MainPage
from .window import MainWindow


def _load_css():
    css_path = os.path.join(os.path.dirname(__file__), "style.css")
    if os.path.exists(css_path):
        provider = Gtk.CssProvider()
        provider.load_from_path(css_path)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )


def _ensure_icon_theme():
    settings = Gtk.Settings.get_default()
    if settings is None:
        return
    theme = settings.get_property("gtk-icon-theme-name") or ""
    if not theme:
        return
    icon_theme = Gtk.IconTheme.new()
    icon_theme.set_theme_name(theme)
    if not _icon_theme_available(theme, icon_theme.get_search_path()):
        settings.set_property("gtk-icon-theme-name", "Adwaita")


class CiakApp(Adw.Application):
    def __init__(self):
        super().__init__(
            application_id=config.APP_ID,
            flags=Gio.ApplicationFlags.DEFAULT_FLAGS,
        )
        self.win = None
        self._user_repo = None
        self._metadata_service = None
        self._onboarding_win = None

    def do_activate(self):
        _load_css()
        _ensure_icon_theme()
        settings = Gio.Settings.new(config.APP_ID)
        apply_theme(settings)

        if self.win:
            self.win.present()
            return

        if needs_onboarding(settings.get_boolean("onboarding-completed")):
            if self._onboarding_win:
                self._onboarding_win.present()
                return
            self._show_onboarding(settings)
            return

        self._build_main_window()

    def _show_onboarding(self, settings):
        from .ui.onboarding import OnboardingWindow

        tmdb_client = TmdbClient(
            settings.get_string("tmdb-api-key"),
            hide_adult_fn=lambda: settings.get_boolean("hide-adult-content"),
        )
        self._onboarding_win = OnboardingWindow(
            settings,
            tmdb_client,
            on_finish=self._build_main_window,
            application=self,
        )
        self._onboarding_win.present()

    def _build_main_window(self):
        settings = Gio.Settings.new(config.APP_ID)

        self._user_repo = LocalMediaRepository(config.get_db_path())
        self._user_repo.initialize()

        api_key = settings.get_string("tmdb-api-key")
        tmdb_client = TmdbClient(
            api_key,
            hide_adult_fn=lambda: settings.get_boolean("hide-adult-content"),
        )
        cache = MetadataCache(
            config.get_db_path(),
            ttl_seconds=settings.get_int("metadata-cache-ttl"),
        )
        self._metadata_service = TmdbMetadataService(tmdb_client, cache)

        self.win = MainWindow(
            application=self,
            user_repo=self._user_repo,
            metadata_service=self._metadata_service,
        )
        self.win.set_page(MainPage(
            self.win,
            user_repo=self._user_repo,
            metadata_service=self._metadata_service,
        ))
        self.win.present()

        onboarding = self._onboarding_win
        self._onboarding_win = None
        if onboarding is not None:
            onboarding.close()

    def relaunch_onboarding(self):
        if self.win is not None:
            self.win.close()
            self.win = None
        if self._user_repo is not None:
            self._user_repo.close()
            self._user_repo = None
        if self._metadata_service is not None:
            self._metadata_service.close()
            self._metadata_service = None
        self._show_onboarding(Gio.Settings.new(config.APP_ID))

    def delete_local_database(self):
        """Close all open handles, erase the SQLite database and poster
        cache, then rebuild the main window from scratch."""
        if self.win is not None:
            self.win.close()
            self.win = None
        if self._user_repo is not None:
            self._user_repo.close()
            self._user_repo = None
        if self._metadata_service is not None:
            self._metadata_service.close()
            self._metadata_service = None
        try:
            os.remove(config.get_db_path())
        except OSError:
            pass
        poster_cache.clear()
        self._build_main_window()

    def do_shutdown(self):
        if self._onboarding_win is not None:
            try:
                self._onboarding_win.close()
            except GLib.Error:
                pass
        if self._user_repo is not None:
            self._user_repo.close()
        if self._metadata_service is not None:
            self._metadata_service.close()
        from .threads import shutdown as _shutdown_threads

        _shutdown_threads(wait=False)
        Adw.Application.do_shutdown(self)


def main():
    app = CiakApp()
    return app.run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
