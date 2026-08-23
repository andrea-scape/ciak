"""page_reveal: launch reveal waits until posters settle."""

import sys
import types
import unittest
from unittest import mock

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, GLib

if "src.config" not in sys.modules:
    _cfg = types.ModuleType("src.config")
    _cfg.APP_ID = "io.github.andrea_scape.ciak.Devel"
    _cfg.APP_VERSION = "0.0.0-test"
    _cfg.APP_NAME = "Ciak"
    sys.modules["src.config"] = _cfg

from src.ui import anim, page_reveal


class SettleRevealTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            Gtk.init()
            Adw.init()
        except TypeError:
            pass

    def setUp(self):
        self.box = Gtk.Box()
        self._was = anim.animations_enabled()
        anim.set_animations_enabled(True)
        self.timeouts = []

    def tearDown(self):
        anim.set_animations_enabled(self._was)

    def _arm(self, settle, max_wait_ms=10000):
        return page_reveal.arm_launch_reveal(
            self.box, settle_fn=lambda: settle["v"],
            max_wait_ms=max_wait_ms)

    def _capturing(self):
        return mock.patch.object(
            GLib, "timeout_add",
            side_effect=lambda ms, cb, *a:
            self.timeouts.append((ms, cb)) or len(self.timeouts))

    def _poll_cbs(self):
        return [cb for ms, cb in self.timeouts if ms == 100]

    def test_prehides_and_polls_until_settled(self):
        settle = {"v": 1}
        with self._capturing():
            reveal = self._arm(settle)
            self.assertEqual(self.box.get_opacity(), 0.0)

            reveal()
            self.assertEqual(self.box.get_opacity(), 0.0)  # still waiting
            polls = self._poll_cbs()
            self.assertEqual(len(polls), 1)

            anim.set_animations_enabled(False)  # final fade becomes instant
            settle["v"] = 0
            before = len(self.timeouts)
            self.assertFalse(polls[0]())       # tick reveals; stops polling
        self.assertEqual(self.box.get_opacity(), 1.0)
        self.assertEqual(len(self.timeouts), before)  # no new poll scheduled

    def test_immediate_reveal_when_already_settled(self):
        import time

        settle = {"v": 0}
        ticks = []

        def _capture(*args):
            # Called unbound through the class patch: either
            # (widget, cb, data) or (cb,) depending on binding.
            cb = args[1] if len(args) >= 2 else args[0]
            ticks.append(cb)
            return 0  # source id placeholder

        with self._capturing(), \
                mock.patch.object(Gtk.Box, "add_tick_callback",
                                  side_effect=_capture):
            reveal = self._arm(settle)
            reveal()
        # Reveal is now a frame-clock rise: one tick callback drives the
        # animation. Pump it past the real duration and expect full
        # restore.
        self.assertTrue(ticks, "rise tick should be registered")
        time.sleep(0.45)  # outlive the 380ms rise
        while ticks[0](self.box, None):
            pass
        self.assertEqual(self.box.get_opacity(), 1.0)
        self.assertEqual(self.box.get_margin_top(), 0)
        self.assertEqual(self._poll_cbs(), [])

    def test_reveal_is_one_shot(self):
        settle = {"v": 0}
        with self._capturing():
            reveal = self._arm(settle)
            reveal()
            n = len(self.timeouts)
            reveal()
            reveal()
        self.assertEqual(len(self.timeouts), n)

    def test_hard_cap_reveals_despite_pending(self):
        settle = {"v": 1}
        with self._capturing():
            reveal = self._arm(settle, max_wait_ms=50)
            caps = [cb for ms, cb in self.timeouts if ms == 50]
            self.assertEqual(len(caps), 1)
            anim.set_animations_enabled(False)
            caps[0]()   # hard cap forces the fade even while pending
        self.assertEqual(self.box.get_opacity(), 1.0)

    def test_no_prehide_when_animations_disabled(self):
        anim.set_animations_enabled(False)
        with self._capturing():
            reveal = page_reveal.arm_launch_reveal(
                self.box, settle_fn=lambda: 5)
        self.assertEqual(self.box.get_opacity(), 1.0)
        reveal()  # one-shot safe call
        self.assertEqual(self.box.get_opacity(), 1.0)


if __name__ == "__main__":
    unittest.main()
