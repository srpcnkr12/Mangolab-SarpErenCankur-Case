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

from fastapi import FastAPI, Query

app = FastAPI(title="fx-tool", version="1.0")


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
# Endpoint
# --------------------------------------------------------------------------- #

@app.get("/tools/convert")
async def convert(
    amount: float,
    from_: str = Query(alias="from"),
    to: str = Query(),
    on: str | None = Query(default=None, alias="date"),
) -> dict:
    """Convert ``amount`` from one currency to another at an ECB rate."""
    raise NotImplementedError  # filled in the next commit
