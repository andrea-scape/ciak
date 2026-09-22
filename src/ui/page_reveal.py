"""Shared first-load reveal for pages.

arm_launch_reveal(page_box) hides the page content at construction (when
animations are enabled) and returns a one-shot reveal() callback. Pages
call reveal() on their first populate / error path; a safety timer
guarantees the page can never stay stuck invisible.
"""

from .anim import ENTRANCE_MS, ENTRANCE_PX, animations_enabled

_SAFETY_TIMEOUT_MS = 2500
_MAX_WAIT_MS = 700


def arm_launch_reveal(page_box, settle_fn=None, max_wait_ms=_MAX_WAIT_MS):
    """Pre-hide page_box and return its one-shot reveal() callback.

    When animations are disabled the box is never hidden and reveal() is a
    no-op, so callers can wire it unconditionally.

    If settle_fn is provided, reveal() waits (polling) until settle_fn()
    reports 0 outstanding items — e.g. poster.pending_loads — before fading
    the page in; max_wait_ms caps the wait so hangs can't stall the page."""
    state = {
        "revealed": not animations_enabled(),
        "polling": False,
        "box": page_box,
        "settle_fn": settle_fn,
        "max_wait_ms": max_wait_ms,
    }

    def _ensure_revealed():
        # Hard cap: force the reveal even if posters are still pending.
        _do_reveal()
        return False

    if not state["revealed"]:
        page_box.set_opacity(0.0)
        from gi.repository import GLib

        GLib.timeout_add(state["max_wait_ms"], _ensure_revealed)

    def _do_reveal():
        if state["revealed"]:
            return
        state["revealed"] = True
        from .anim import rise_fade_in
        rise_fade_in([state["box"]], ENTRANCE_MS, ENTRANCE_PX)

    def _tick(*_args):
        # Polls from the frame clock instead of a wall-clock timer, so the
        # reveal decision lands on a frame boundary rather than up to 100ms
        # late.  One callback per drawn frame; returns False to stop.
        if state["revealed"]:
            return False
        pending = 0
        if state["settle_fn"] is not None:
            try:
                pending = state["settle_fn"]()
            except Exception:
                pending = 0
        if pending <= 0:
            _do_reveal()
            return False
        return True

    def reveal():
        if state["revealed"]:
            return
        if state["settle_fn"] is None or state["polling"]:
            _do_reveal()
            return

        # Settle immediately when nothing is outstanding; otherwise poll.
        try:
            settled = state["settle_fn"]() <= 0
        except Exception:
            settled = True
        if settled:
            _do_reveal()
            return
        state["polling"] = True
        state["box"].add_tick_callback(_tick)

    return reveal


def defer_initial_work(fn, *args, delay_ms=70):
    """Run fn(*args) shortly after construction so launch work never
    competes with the page transition."""
    from gi.repository import GLib

    def _run():
        fn(*args)
        return False

    GLib.timeout_add(delay_ms, _run)
