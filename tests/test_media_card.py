"""Media card: the info caption column (title + year + type) stays hidden
until the card's poster pixels are painted — the on_load callback reveals
it together with the texture or the clapperboard placeholder."""

import types
import unittest
from unittest import mock

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk

from src.ui import anim
from src.ui.media_card import make_media_card


def _item(poster_url="https://image.tmdb.org/t/p/abc.jpg"):
    return types.SimpleNamespace(
        title="A Title", year=2024, media_type="movie",
        poster_url=poster_url, season_number=None,
        episode_number=None,
    )


def _children(widget):
    out = []
    child = widget.get_first_child()
    while child is not None:
        out.append(child)
        child = child.get_next_sibling()
    return out


def _find_info(card):
    """The vertical info box directly containing the heading title label."""
    def walk(widget):
        if isinstance(widget, Gtk.Box):
            for child in _children(widget):
                if (isinstance(child, Gtk.Label)
                        and "heading" in child.get_css_classes()):
                    return widget
        for child in _children(widget):
            found = walk(child)
            if found is not None:
                return found
        return None
    return walk(card)


class MediaCardInfoRevealTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            Gtk.init()
        except TypeError:
            pass

    def setUp(self):
        self._was_anim = anim.animations_enabled()

    def tearDown(self):
        anim.set_animations_enabled(self._was_anim)

    def _build_card(self, item=_item(), on_poster_ready=None):
        """Build a card, capturing the load_poster contract (picture +
        on_load) and the safety-timeout registration. The real poster
        pipeline is bypassed; GLib.timeout_add is stubbed so no stray
        timer leaks into later tests."""
        with mock.patch("src.ui.media_card.load_poster") as lp, \
                mock.patch("src.ui.media_card.GLib.timeout_add") as ta:
            button = make_media_card(item, on_poster_ready=on_poster_ready)
        args, kwargs = lp.call_args
        return types.SimpleNamespace(
            button=button,
            picture=args[1],
            on_load=kwargs["on_load"],
            info=_find_info(button),
            timeout_args=ta.call_args,
            on_poster_ready=kwargs.get("on_poster_ready"),
        )

    def test_info_hidden_until_poster_loads(self):
        card = self._build_card()
        self.assertEqual(0.0, card.info.get_opacity())
        self.assertTrue(card.info.get_visible())
        self.assertFalse(card.info._info_revealed)

    def test_reveal_fades_info_in_on_load(self):
        anim.set_animations_enabled(False)
        card = self._build_card()
        card.on_load()
        self.assertEqual(1.0, card.info.get_opacity())
        self.assertTrue(card.info._info_revealed)

    def test_reveal_is_idempotent(self):
        card = self._build_card()
        with mock.patch("src.ui.media_card.fade_in") as fade:
            card.on_load()
            card.on_load()
        fade.assert_called_once_with(card.info, anim.CONTENT_MS)

    def test_posterless_item_uses_same_reveal_contract(self):
        anim.set_animations_enabled(False)
        card = self._build_card(_item(poster_url=None))
        card.on_load()
        self.assertEqual(1.0, card.info.get_opacity())

    def test_safety_timeout_reveals_if_load_never_fires(self):
        anim.set_animations_enabled(False)
        card = self._build_card()
        args = card.timeout_args.args
        self.assertEqual(4000, args[0])
        args[1]()
        self.assertTrue(card.info._info_revealed)

    def test_on_poster_ready_fires_with_poster_load(self):
        ready = mock.Mock()
        card = self._build_card(on_poster_ready=ready)
        card.on_load()
        ready.assert_called_once_with()

    def test_on_poster_ready_idempotent_like_reveal(self):
        ready = mock.Mock()
        card = self._build_card(on_poster_ready=ready)
        card.on_load()
        card.on_load()
        ready.assert_called_once_with()

    def test_on_poster_ready_fires_via_safety_timeout(self):
        ready = mock.Mock()
        card = self._build_card(on_poster_ready=ready)
        card.timeout_args.args[1]()
        ready.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()