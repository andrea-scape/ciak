"""Streaming region selection.

The detail page shows providers for a single ISO-3166-1 region. It comes
from the "streaming-region" GSettings key, or is auto-detected from the
system locale when the key is unset ("auto").
"""

import locale
import os
import re

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gio, GLib

_glib_language_names = getattr(GLib, "get_language_names", None)

FALLBACK_REGION = "IT"


def streaming_region(settings: Gio.Settings | None) -> str:
    """Return the effective region code (upper-case, 2 letters).

    Preference wins; "auto" (or unset) falls back to locale detection,
    which itself falls back to FALLBACK_REGION.
    """
    value = ""
    if settings is not None:
        try:
            value = settings.get_string("streaming-region") or ""
        except (GLib.Error, TypeError):
            value = ""
    value = value.strip().upper()
    if value and value != "AUTO":
        return value
    return detect_region_from_locale()


def detect_region_from_locale() -> str:
    """Best-effort ISO-3166-1 code from the system locale/GLib language."""
    candidates: list[str] = []
    for var in ("LC_ALL", "LC_CTYPE", "LANG"):
        value = os.environ.get(var) or ""
        if value:
            candidates.append(value)
    try:
        lang, _encoding = locale.getdefaultlocale()
        if lang:
            candidates.append(lang)
    except (ImportError, ValueError):
        pass
    try:
        names = GLib.get_language_names() or []
        if names:
            candidates.extend(names)
    except (AttributeError, GLib.Error):
        pass

    for cand in candidates:
        code = _region_from_string(cand)
        if code:
            return code
    return FALLBACK_REGION


def _region_from_string(value: str) -> str | None:
    """Extract an upper-case 2-letter region from e.g. "it_IT.UTF-8"."""
    match = re.search(r"[_-]([A-Za-z]{2})", value)
    if match:
        return match.group(1).upper()
    return None


def region_label(country_code: str) -> str:
    """Human label for the preferences dropdown."""
    return country_code.upper()
