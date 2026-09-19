"""Date display helpers.

Default style is comma-free day-first ("12 Aug 2026"); when the
``american-date-format`` GSettings key is set, US convention with commas
("Aug 12, 2026").  All functions are pure and take ``american`` explicitly
so they stay trivially testable; call sites read the flag at render time.
"""

import datetime
import time

_MONTHS_SHORT = [
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
]
MONTH_NAMES = [
    "January", "February", "March", "April", "May", "June", "July",
    "August", "September", "October", "November", "December",
]
_WEEKDAYS = [
    "Monday", "Tuesday", "Wednesday", "Thursday",
    "Friday", "Saturday", "Sunday",
]


def format_date(d, american=False):
    """12 Aug 2026 / Aug 12, 2026."""
    if american:
        return f"{_MONTHS_SHORT[d.month - 1]} {d.day}, {d.year}"
    return f"{d.day} {_MONTHS_SHORT[d.month - 1]} {d.year}"


def format_date_long(d, american=False):
    """Saturday, 15 August 2026 / Saturday, August 15, 2026."""
    wd = _WEEKDAYS[d.weekday()]
    if american:
        return f"{wd}, {MONTH_NAMES[d.month - 1]} {d.day}, {d.year}"
    return f"{wd}, {d.day} {MONTH_NAMES[d.month - 1]} {d.year}"


def format_day_short(d, american=False):
    """26 Aug / Aug 26."""
    if american:
        return f"{_MONTHS_SHORT[d.month - 1]} {d.day}"
    return f"{d.day} {_MONTHS_SHORT[d.month - 1]}"


def format_weekday_date(d, american=False):
    """Tuesday, 26 Aug / Tuesday, Aug 26."""
    wd = _WEEKDAYS[d.weekday()]
    if american:
        return f"{wd}, {_MONTHS_SHORT[d.month - 1]} {d.day}"
    return f"{wd}, {d.day} {_MONTHS_SHORT[d.month - 1]}"


def format_epoch(ts, american=False):
    """Unix timestamp -> format_date()."""
    return format_date(datetime.date.fromtimestamp(ts), american)


def format_sync_ago(ts, never="", short=True):
    """One-line 'Synced … ago' label for an epoch-second timestamp.

    ``ts == 0`` renders *never* (no sync recorded).  With *short* the
    units abbreviate ("Synced 5m ago"); otherwise they spell out and
    pluralize ("Synced 5 minutes ago").  Pure aside from the clock, so
    it stays trivially testable.
    """
    if not ts:
        return never
    diff = int(time.time()) - ts
    if diff < 60:
        return "Synced just now"
    if diff < 3600:
        count, unit = diff // 60, "m"
    elif diff < 86400:
        count, unit = diff // 3600, "h"
    else:
        count, unit = diff // 86400, "d"
    if short:
        return f"Synced {count}{unit} ago"
    words = {"m": "minute", "h": "hour", "d": "day"}[unit]
    if count != 1:
        words += "s"
    return f"Synced {count} {words} ago"


def format_iso(iso, american=False):
    """ISO date string -> format_date(); empty/None-safe."""
    if not iso:
        return ""
    try:
        return format_date(datetime.date.fromisoformat(str(iso)[:10]), american)
    except ValueError:
        return ""


def is_american(settings):
    """Read the GSettings flag; None-safe for tests/headless use."""
    if settings is None:
        return False
    try:
        return bool(settings.get_boolean("american-date-format"))
    except Exception:
        return False
