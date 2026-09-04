"""Test configuration.

Point the app at a fake upstream host *before* anything imports ``app``, so no
test can accidentally reach the real Frankfurter API.
"""

import os

os.environ.setdefault("FX_UPSTREAM_BASE", "https://fake-upstream.test")
os.environ.setdefault("FX_UPSTREAM_PREFIX", "v1")
