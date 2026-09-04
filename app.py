"""FX conversion tool — an HTTP endpoint an AI agent can call.

    GET /tools/convert?amount=250&from=EUR&to=TRY&date=2026-08-28

Answers from the public Frankfurter API (European Central Bank reference rates).
The caller is a language model talking to a paying customer, so the endpoint
never invents a rate and never presents a rate as belonging to a date it does
not belong to: when the ECB published nothing for the date asked, it returns the
most recent earlier rate, labelled with the date that rate actually belongs to.

Configuration (all from the environment, nothing hardcodes the real host):

    FX_UPSTREAM_BASE     upstream base URL   (default https://api.frankfurter.dev)
    FX_UPSTREAM_PREFIX   upstream path prefix (default "v1")
    FX_UPSTREAM_TIMEOUT  per-request timeout, seconds (default 5)
    PORT                 handled by run.sh   (default 8080)
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

import httpx
from fastapi import FastAPI, Query

SOURCE = "ECB via frankfurter.dev"


# --------------------------------------------------------------------------- #
# Configuration (read from the environment on every use, so tests can override)
# --------------------------------------------------------------------------- #

def _upstream_base() -> str:
    return os.environ.get("FX_UPSTREAM_BASE", "https://api.frankfurter.dev").rstrip("/")


def _upstream_prefix() -> str:
    return os.environ.get("FX_UPSTREAM_PREFIX", "v1").strip("/")


def _upstream_timeout() -> float:
    return float(os.environ.get("FX_UPSTREAM_TIMEOUT", "5"))


# --------------------------------------------------------------------------- #
# Shared HTTP client
# --------------------------------------------------------------------------- #

_client: httpx.AsyncClient | None = None


@asynccontextmanager
async def lifespan(_: FastAPI):
    global _client
    _client = httpx.AsyncClient(timeout=_upstream_timeout())
    try:
        yield
    finally:
        await _client.aclose()
        _client = None


def _http() -> httpx.AsyncClient:
    """The shared client, created on demand if the lifespan has not run (tests)."""
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=_upstream_timeout())
    return _client


app = FastAPI(title="fx-tool", version="1.0", lifespan=lifespan)


# --------------------------------------------------------------------------- #
# Upstream
# --------------------------------------------------------------------------- #

async def fetch_rate(base: str, target: str, on: date | None) -> tuple[float, date]:
    """Return ``(rate, rate_date)`` for one unit of ``base`` in ``target``.

    ``rate_date`` is taken from the upstream's own ``date`` field — the day the
    rate actually belongs to, which is not always the day that was asked for.
    """
    path = on.isoformat() if on else "latest"
    url = f"{_upstream_base()}/{_upstream_prefix()}/{path}"
    response = await _http().get(url, params={"base": base, "symbols": target})
    payload = response.json()
    rate = payload["rates"][target]
    rate_date = date.fromisoformat(payload["date"])
    return rate, rate_date


# --------------------------------------------------------------------------- #
# Endpoint
# --------------------------------------------------------------------------- #

@app.get("/tools/convert")
async def convert(
    amount: float,
    from_: str = Query(alias="from"),
    to: str = Query(),
    on: date | None = Query(default=None, alias="date"),
) -> dict:
    """Convert ``amount`` from one currency to another at an ECB rate."""
    base = from_.strip().upper()
    target = to.strip().upper()
    asked_date = on or date.today()

    rate, rate_date = await fetch_rate(base, target, on)
    result = (Decimal(str(amount)) * Decimal(str(rate))).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )

    return {
        "amount": amount,
        "from": base,
        "to": target,
        "rate": rate,
        "result": float(result),
        "rate_date": rate_date.isoformat(),
        "asked_date": asked_date.isoformat(),
        "source": SOURCE,
    }
