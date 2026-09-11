"""Shared pytest setup.

Injects a build-time "src.config" stub (meson generates the real module
from src/config.py.in; a bare pytest run never builds it).  Deprecation
warnings fired inside pygobject at import time, for which no app-side fix
exists, are silenced centrally via filterwarnings in pytest.ini.
"""

import sys
import types

if "src.config" not in sys.modules:
    _cfg = types.ModuleType("src.config")
    _cfg.APP_ID = "io.github.andrea_scape.ciak.Devel"
    _cfg.APP_VERSION = "0.0.0-test"
    _cfg.APP_NAME = "Ciak"
    sys.modules["src.config"] = _cfg