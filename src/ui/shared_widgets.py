import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk


def make_error_row(message, on_retry, icon_name="network-error-symbolic"):
    """Returns a Gtk.Box with icon, message, and Retry button."""
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                  halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER,
                  vexpand=True)
    box.set_margin_top(48)
    box.set_margin_bottom(48)

    icon = Gtk.Image(icon_name=icon_name, pixel_size=48)
    icon.add_css_class("dimmed")
    box.append(icon)

    lbl = Gtk.Label(label=message, wrap=True, justify=Gtk.Justification.CENTER)
    lbl.add_css_class("dimmed")
    box.append(lbl)

    btn = Gtk.Button(label="Retry")
    btn.add_css_class("suggested-action")
    btn.connect("clicked", lambda _b: on_retry())
    box.append(btn)

    return box
