"""Date display helpers.

Default style is comma-free day-first ("12 Aug 2026"); when the
``american-date-format`` GSettings key is set, US convention with commas
("Aug 12, 2026").  All functions are pure and take ``american`` explicitly
so they stay trivially testable; call sites read the flag at render time.
"""

import datetime

_MONTHS_SHORT = [
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
]
_MONTHS_FULL = [
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
        return f"{wd}, {_MONTHS_FULL[d.month - 1]} {d.day}, {d.year}"
    return f"{wd}, {d.day} {_MONTHS_FULL[d.month - 1]} {d.year}"


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
