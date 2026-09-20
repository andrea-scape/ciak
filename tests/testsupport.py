"""Shared test bootstrap.

Injects a build-time "src.config" stub: meson generates the real module from
src/config.py.in, so a bare unittest/pytest run never builds it. Import this
module at the top of any test file (before importing src.*) to install it.

This consolidates the per-file copy that 24 test files used to carry and
replaces the old tests/test_harness.py placeholder.
"""

import sys
import types

if "src.config" not in sys.modules:
    _cfg = types.ModuleType("src.config")
    _cfg.APP_ID = "io.github.andrea_scape.ciak.Devel"
    _cfg.APP_VERSION = "0.0.0-test"
    _cfg.APP_NAME = "Ciak"
    sys.modules["src.config"] = _cfg