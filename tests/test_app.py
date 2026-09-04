"""Offline test suite — the real upstream is never contacted.

Filled in once the endpoint exists.
"""

import app as fx_app


def test_module_imports() -> None:
    assert fx_app.app.title == "fx-tool"
