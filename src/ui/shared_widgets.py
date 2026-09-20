import gi

gi.require_version("Gtk", "4.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gtk, GLib

from .anim import animations_enabled
from .media_card import add_watched_badge, make_media_card


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


def clear_children(widget):
    """Remove every child of a Gtk widget."""
    child = widget.get_first_child()
    while child:
        nxt = child.get_next_sibling()
        widget.remove(child)
        child = nxt


def pump_media_chunks(page, schedule, *, is_stale, movie_kw=None, show_kw=None,
                      set_pre_reveal=False, on_first_batch=None):
    """Append one _CHUNK_SIZE batch of pending cards to page.movies_grid /
    page.shows_grid.

    page._pending_chunk is (token, iterator, watched movie ids,
    fully-watched show ids); is_stale(token) marks an obsolete build. Cards
    accumulate in page._build_cards. Reschedules itself over idle ticks
    while work remains. Returns True when the queue drained on this call
    (the caller runs its finish tail), False otherwise.
    """
    pending = getattr(page, "_pending_chunk", None)
    if not pending:
        return False
    token, queue_iter, watched_movie_ids, fully_watched_shows = pending
    if is_stale(token):
        page._pending_chunk = None
        return False

    cards = getattr(page, "_build_cards", None)
    if cards is None:
        cards = page._build_cards = []

    built = []
    for _ in range(getattr(page, "_CHUNK_SIZE", 12)):
        try:
            kind, item = next(queue_iter)
        except StopIteration:
            page._pending_chunk = None
            break
        if kind == "movie":
            card = make_media_card(
                item, page.main_page,
                watched=item.tmdb_id in watched_movie_ids,
                **(movie_kw or {}))
            page.movies_grid.append(card)
        else:
            # Read the LIVE badge set, not the snapshot captured at populate
            # time: checks that resolved while earlier chunks were building
            # must badge later-built cards too.
            live_fully = frozenset(
                getattr(page, "_last_fully_shows", frozenset()))
            card = make_media_card(
                item, page.main_page,
                watched=(item.tmdb_id in fully_watched_shows
                         or item.tmdb_id in live_fully),
                **(show_kw or {}))
            page.shows_grid.append(card)
        built.append(card)
        if set_pre_reveal:
            card._pre_reveal = not page._revealed
        if page._revealed and animations_enabled():
            # Repopulate pass: hide the card BEFORE any frame can paint it,
            # so the delayed rise-fade never flashes.
            card.set_opacity(0.0)

    cards.extend(built)

    if on_first_batch is not None and built and not page._revealed:
        on_first_batch()

    if page._pending_chunk is not None:
        if schedule:
            GLib.idle_add(page._pump_build, True)
        return False
    return True


def retrofit_watched_badges(grids, fully_ids):
    """Add watched badges to rendered show cards whose id is in fully_ids."""
    for grid in grids:
        child = grid.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            button = getattr(child, "get_child", lambda: None)()
            item = getattr(button, "_media_item", None) if button else None
            if (item is not None
                    and getattr(item, "media_type", "") == "show"
                    and item.tmdb_id in fully_ids):
                add_watched_badge(button)
            child = nxt
