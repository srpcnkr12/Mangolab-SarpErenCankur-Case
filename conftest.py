"""Test configuration and shared fixtures.

Point the app at a fake upstream host *before* anything imports ``app``, so no
test can accidentally reach the real Frankfurter API. Individual tests still
override these with ``monkeypatch`` as needed.
"""

import os

os.environ.setdefault("FX_UPSTREAM_BASE", "https://fake-upstream.test")
os.environ.setdefault("FX_UPSTREAM_PREFIX", "v1")

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

# A small stand-in for GET /v1/currencies.
CURRENCIES = {
    "EUR": "Euro",
    "USD": "United States Dollar",
    "TRY": "Turkish Lira",
    "JPY": "Japanese Yen",
    "GBP": "British Pound",
}


def rate_payload(rate_date: str, target: str, rate: float) -> dict:
    """The shape Frankfurter returns for a single pair."""
    return {"amount": 1.0, "base": "EUR", "date": rate_date, "rates": {target: rate}}


@pytest.fixture
def fx():
    """The freshly-imported app module with all in-process caches cleared."""
    import app

    app._rate_cache.clear()
    app._known_currencies = None
    app._currencies_retry_at = 0.0
    app._client = None
    return app


@pytest.fixture
def upstream(fx):
    """An active respx router with /currencies already stubbed."""
    base = f"{fx._upstream_base()}/{fx._upstream_prefix()}"
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{base}/currencies").mock(
            return_value=httpx.Response(200, json=CURRENCIES)
        )
        router.base = base  # convenience for tests
        yield router


@pytest.fixture
def client(fx):
    """A TestClient that returns 500 bodies instead of re-raising."""
    return TestClient(fx.app, raise_server_exceptions=False)
