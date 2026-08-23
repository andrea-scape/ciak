import gi
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib


_animations_enabled = True


def set_animations_enabled(enabled):
    global _animations_enabled
    _animations_enabled = bool(enabled)


def animations_enabled():
    return _animations_enabled


def fade_in(widget, duration_ms=300, on_done=None):
    if not _animations_enabled:
        widget.set_opacity(1.0)
        if on_done:
            GLib.idle_add(on_done)
        return None
    widget.set_opacity(0.0)
    target = Adw.PropertyAnimationTarget.new(widget, "opacity")
    anim = Adw.TimedAnimation.new(widget, 0.0, 1.0, duration_ms, target)
    anim.set_easing(Adw.Easing.EASE_OUT_CUBIC)
    if on_done:
        def _done(*_args):
            GLib.idle_add(on_done)
        anim.connect("done", _done)
    anim.play()
    return anim


def fade_out(widget, duration_ms=300, on_done=None):
    if not _animations_enabled:
        widget.set_opacity(0.0)
        if on_done:
            GLib.idle_add(on_done)
        return None
    target = Adw.PropertyAnimationTarget.new(widget, "opacity")
    anim = Adw.TimedAnimation.new(widget, 1.0, 0.0, duration_ms, target)
    anim.set_easing(Adw.Easing.EASE_IN_CUBIC)
    if on_done:
        def _done(*_args):
            GLib.idle_add(on_done)
        anim.connect("done", _done)
    anim.play()
    return anim


def fade_out_group(widgets, duration_ms=300, on_done=None):
    widgets = [w for w in widgets if w.get_visible() and w.get_opacity() > 0.0]
    if not widgets or not _animations_enabled:
        if on_done:
            on_done()
        return

    remaining = len(widgets)

    def _on_done():
        nonlocal remaining
        remaining -= 1
        if remaining == 0 and on_done:
            on_done()

    for w in widgets:
        fade_out(w, duration_ms, _on_done)


# Motion tiers — the whole app draws from these so pacing stays coherent.
#   ENTRANCE: whole-page reveal once content settles (arm_launch_reveal)
#   CONTENT : card groups on filter/sort/mode repopulates and section
#             labels appearing with their first cards
#   MICRO   : fade-only chrome (badges, poster pop-ins, skeletons,
#             headerbar) — no positional motion by design.
ENTRANCE_MS = 380
ENTRANCE_PX = 12
CONTENT_MS = 340
CONTENT_PX = 8
MICRO_FADE_MS = 200
NAV_SLIDE_MS = 400       # detail open / back slides
NAV_CROSSFADE_MS = 225   # main page switches


def rise_fade_in(widgets, duration_ms=200, rise_px=8, on_done=None):
    """Fade widgets in while they drift up `rise_px` into their natural
    position. Easing is ease-in-out so motion starts gently, flows and
    settles gently — same even character as the page-switch crossfade.
    GTK4 exposes no CSS transforms for widgets, so the rise animates
    margin_top back to the captured original alongside opacity — same
    manual tick style as the other helpers here. With animations
    disabled everything restores instantly."""
    batch = [w for w in widgets if w.get_visible()]
    if not batch:
        if on_done:
            on_done()
        return

    # Capture natural margins before offsetting. Cards built during a
    # repopulate pass stash their true margin in _rise_orig_margin at
    # append time; without this a second rise would compound the offset.
    state = []
    for w in batch:
        orig = getattr(w, "_rise_orig_margin", None)
        if orig is None:
            orig = w.get_margin_top()
            w._rise_orig_margin = orig
        state.append((w, orig))

    def _restore():
        for w, orig in state:
            w.set_opacity(1.0)
            w.set_margin_top(orig)

    if not _animations_enabled:
        _restore()
        if on_done:
            on_done()
        return

    start_time = GLib.get_monotonic_time()
    duration_us = duration_ms * 1000

    def _tick(*_args):
        # Driven by the widget's frame clock: exactly one callback per
        # drawn frame, so motion stays vsync-aligned even when the main
        # loop is busy (a plain timeout would fire late and stutter).
        elapsed = GLib.get_monotonic_time() - start_time
        if elapsed >= duration_us:
            _restore()
            if on_done:
                on_done()
            return False  # removes the tick callback
        p = max(elapsed, 0) / duration_us
        # smoothstep: gentle start, even flow, gentle settle
        eased = p * p * (3.0 - 2.0 * p)
        for w, orig in state:
            w.set_opacity(eased)
            w.set_margin_top(orig + round(rise_px * (1.0 - eased)))
        return True  # keep ticking

    for w, orig in state:
        w.set_opacity(0.0)
        w.set_margin_top(orig + rise_px)
    state[0][0].add_tick_callback(_tick)


def fade_in_group(widgets, duration_ms=300, on_done=None):
    widgets = [w for w in widgets if w.get_visible()]
    if not widgets or not _animations_enabled:
        for w in widgets:
            w.set_opacity(1.0)
        if on_done:
            on_done()
        return

    for w in widgets:
        w.set_opacity(0.0)

    remaining = len(widgets)

    def _on_done():
        nonlocal remaining
        remaining -= 1
        if remaining == 0 and on_done:
            on_done()

    for w in widgets:
        fade_in(w, duration_ms, _on_done)


def stagger_fade_in(children, delay_ms=50, duration_ms=300, after_ms=0,
                    max_children=None):
    """Cascade-fade the given widgets in. Every child passed is animated —
    nothing is silently truncated (the old max_children cap left the tail
    popping in instantly)."""
    batch = list(children)
    if not batch:
        return
    if not _animations_enabled:
        for w in batch:
            w.set_opacity(1.0)
        return

    for w in batch:
        w.set_opacity(0.0)

    start_time = GLib.get_monotonic_time()
    delay_us = after_ms * 1000
    stagger_us = delay_ms * 1000
    duration_us = duration_ms * 1000

    def _tick():
        now = GLib.get_monotonic_time()
        elapsed = now - start_time - delay_us
        if elapsed < 0:
            return True

        any_active = False
        for i, w in enumerate(batch):
            t_start = i * stagger_us
            t = elapsed - t_start
            if t < 0:
                w.set_opacity(0.0)
                any_active = True
            elif t < duration_us:
                progress = t / duration_us
                eased = 1.0 - (1.0 - progress) ** 3
                w.set_opacity(eased)
                any_active = True
            else:
                w.set_opacity(1.0)

        return any_active

    GLib.timeout_add(16, _tick)
