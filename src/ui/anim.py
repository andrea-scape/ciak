import gi
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib


_animations_enabled = True


def set_animations_enabled(enabled):
    global _animations_enabled
    _animations_enabled = bool(enabled)


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


def stagger_fade_in(children, delay_ms=50, duration_ms=300, after_ms=0, max_children=12):
    batch = list(children[:max_children])
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
