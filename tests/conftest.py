"""Shared pytest setup.

Ensures the src.config stub (tests/testsupport.py) is in place on the very
first import.  Deprecation warnings fired inside pygobject at import time,
for which no app-side fix exists, are silenced centrally via filterwarnings
in pytest.ini.
"""

import tests.testsupport  # noqa: F401 (injects the src.config stub)