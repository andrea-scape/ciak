"""Scroll-position preservation for sidebar pages.

Data-driven refreshes (invalidations, stale returns) rebuild a page's
content; without help the viewport snaps to the top because the cleared
grid briefly measures zero. Pages capture the vertical adjustment before
tearing content down and restore it once the new content is queued:

    y = scroll_restore.capture(self._scrolled)   # in _load, pre-clear
    ...rebuild...
    scroll_restore.restore(self._scrolled, y)    # after populate

restore() is deferred to an idle and clamped to the new maximum so it
lands after GTK has laid out the fresh children.
"""

from gi.repository import GLib


def clamp_value(value, upper, page_size):
    """Largest legal scroll offset for the given metrics (pure, tested)."""
    max_value = max(upper - page_size, 0.0)
    return min(max(value, 0.0), max_value)


def capture(scroll):
    """Vertical offset of a Gtk.ScrolledWindow right now."""
    try:
        return scroll.get_vadjustment().get_value()
    except Exception:
        return 0.0


def had_content(scroll):
    """True when the window currently shows scrolled content — first
    loads should not "preserve" position 0 over an empty grid."""
    try:
        adj = scroll.get_vadjustment()
        return adj.get_upper() > adj.get_page_size() + 1.0
    except Exception:
        return False


def restore(scroll, value):
    """Re-apply a captured offset on the next idle tick.

    Deferred so populate code can call it synchronously at the end of
    its finisher, before GTK has measured the new children."""
    if value <= 0.0:
        return False

    def _apply():
        try:
            adj = scroll.get_vadjustment()
            target = clamp_value(
                value, adj.get_upper(), adj.get_page_size())
            # Only meaningful when there is something to scroll back to.
            if adj.get_upper() > adj.get_page_size():
                adj.set_value(target)
        except Exception:
            pass
        return False

    GLib.idle_add(_apply)
    return False
