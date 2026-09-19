"""Cloud sync backends for Ciak.

Supports TMDB (partial), Simkl (full), and Letterboxd (experimental).
"""

from .letterboxd_backend import LetterboxdSyncBackend
from .simkl_backend import SimklSyncBackend
from .tmdb_backend import TmdbSyncBackend

BACKEND_DISPLAY_NAMES = {
    cls.name: cls.display_name
    for cls in (TmdbSyncBackend, SimklSyncBackend, LetterboxdSyncBackend)
}