import gi

gi.require_version("Adw", "1")
from gi.repository import Adw


def apply_theme(settings):
    value = settings.get_string("theme")
    manager = Adw.StyleManager.get_default()

    if value == "light":
        manager.set_color_scheme(Adw.ColorScheme.FORCE_LIGHT)
    elif value == "dark":
        manager.set_color_scheme(Adw.ColorScheme.FORCE_DARK)
    else:
        manager.set_color_scheme(Adw.ColorScheme.DEFAULT)
