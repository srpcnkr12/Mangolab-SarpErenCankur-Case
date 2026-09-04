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

Error codes are listed in README.md.
"""

from __future__ import annotations

import logging
import math
import os
import re
from contextlib import asynccontextmanager
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

import httpx
from fastapi import FastAPI, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

SOURCE = "ECB via frankfurter.dev"
CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
ECB_SERIES_START = date(1999, 1, 4)

logger = logging.getLogger("fx")


# --------------------------------------------------------------------------- #
# Configuration (read from the environment on every use, so tests can override)
# --------------------------------------------------------------------------- #

def _upstream_base() -> str:
    return os.environ.get("FX_UPSTREAM_BASE", "https://api.frankfurter.dev").rstrip("/")


def _upstream_prefix() -> str:
    return os.environ.get("FX_UPSTREAM_PREFIX", "v1").strip("/")


def _upstream_timeout() -> float:
    return float(os.environ.get("FX_UPSTREAM_TIMEOUT", "5"))


def _upstream_url(path: str) -> str:
    return f"{_upstream_base()}/{_upstream_prefix()}/{path}"


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #

class FxError(Exception):
    """An error we can explain to the caller as ``{"error", "message"}``."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def _error_body(code: str, message: str) -> dict:
    return {"error": code, "message": message}


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


@app.exception_handler(FxError)
async def _handle_fx_error(_, exc: FxError) -> JSONResponse:
    return JSONResponse(_error_body(exc.code, exc.message), status_code=exc.status_code)


@app.exception_handler(RequestValidationError)
async def _handle_validation_error(_, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(_error_body("invalid_parameter", _validation_message(exc)), status_code=422)


@app.exception_handler(StarletteHTTPException)
async def _handle_http_exception(_, exc: StarletteHTTPException) -> JSONResponse:
    code, message = {
        404: ("not_found", "No such endpoint."),
        405: ("method_not_allowed", "That method is not allowed on this endpoint."),
    }.get(exc.status_code, ("http_error", str(exc.detail)))
    return JSONResponse(_error_body(code, message), status_code=exc.status_code)


@app.exception_handler(Exception)
async def _handle_unexpected(_, exc: Exception) -> JSONResponse:
    logger.exception("unhandled error")
    return JSONResponse(
        _error_body("internal_error", "The service hit an unexpected error."),
        status_code=500,
    )


def _validation_message(exc: RequestValidationError) -> str:
    errors = exc.errors()
    if not errors:
        return "The request parameters are invalid."
    first = errors[0]
    loc = first.get("loc") or ()
    field = loc[-1] if loc else "?"
    field = {"from_": "from"}.get(field, field)
    return f"Parameter '{field}': {first.get('msg', 'invalid')}."


# --------------------------------------------------------------------------- #
# Upstream
# --------------------------------------------------------------------------- #

_known_currencies: set[str] | None = None


async def known_currencies() -> set[str] | None:
    """The ECB currency codes, cached for the process lifetime.

    Returns ``None`` if the list cannot be fetched, so the caller falls back to
    letting the rate request surface the error instead of guessing.
    """
    global _known_currencies
    if _known_currencies is None:
        try:
            response = await _http().get(_upstream_url("currencies"))
            response.raise_for_status()
            _known_currencies = {code.upper() for code in response.json()}
        except Exception:
            logger.warning("could not fetch the currency list", exc_info=True)
            return None
    return _known_currencies


def _no_rate_message(on: date | None) -> str:
    if on is not None and on < ECB_SERIES_START:
        return (
            f"The ECB rate series begins on {ECB_SERIES_START.isoformat()}; "
            f"there is no rate for {on.isoformat()}."
        )
    if on is not None:
        return f"The ECB has no published rate for {on.isoformat()}."
    return "The ECB has no published rate for the requested date."


async def fetch_rate(base: str, target: str, on: date | None) -> tuple[float, date]:
    """Return ``(rate, rate_date)`` for one unit of ``base`` in ``target``.

    ``rate_date`` is taken from the upstream's own ``date`` field — the day the
    rate actually belongs to, which is not always the day that was asked for.
    """
    path = on.isoformat() if on else "latest"
    url = _upstream_url(path)
    params = {"base": base, "symbols": target}

    try:
        response = await _http().get(url, params=params)
    except httpx.TimeoutException:
        logger.warning("upstream timeout for %s", url)
        raise FxError(504, "upstream_timeout",
                      "The exchange-rate service did not respond in time.")
    except httpx.RequestError as exc:
        logger.warning("upstream unreachable for %s: %s", url, exc)
        raise FxError(502, "upstream_unreachable",
                      "Could not reach the exchange-rate service.")

    if response.status_code == 404:
        raise FxError(404, "no_rate_available", _no_rate_message(on))
    if response.status_code >= 500:
        logger.warning("upstream returned %s for %s", response.status_code, url)
        raise FxError(502, "upstream_error",
                      "The exchange-rate service returned an error.")
    if response.status_code != 200:
        logger.warning("upstream returned %s for %s", response.status_code, url)
        raise FxError(502, "upstream_error",
                      f"The exchange-rate service returned HTTP {response.status_code}.")

    try:
        payload = response.json()
        rate = payload["rates"][target]
        rate_date = date.fromisoformat(payload["date"])
    except (ValueError, KeyError, TypeError):
        logger.warning("unreadable upstream response for %s", url)
        raise FxError(502, "upstream_bad_response",
                      "The exchange-rate service returned an unexpected response.")

    return rate, rate_date


# --------------------------------------------------------------------------- #
# Endpoint
# --------------------------------------------------------------------------- #

def _build_response(amount: float, base: str, target: str, rate: float,
                    rate_date: date, asked_date: date) -> dict:
    result = (Decimal(str(amount)) * Decimal(str(rate))).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    body = {
        "amount": amount,
        "from": base,
        "to": target,
        "rate": rate,
        "result": float(result),
        "rate_date": rate_date.isoformat(),
        "asked_date": asked_date.isoformat(),
        "source": SOURCE,
    }
    if rate_date != asked_date:
        # The ECB published no rate for the date asked (weekend, holiday, or not
        # published yet today). We answer with the most recent earlier rate and
        # say so plainly, so the model can tell the customer which day it is from.
        days = (asked_date - rate_date).days
        earlier = f" ({days} day{'s' if days != 1 else ''} earlier)" if days > 0 else ""
        body["notice"] = (
            f"No ECB rate was published for {asked_date.isoformat()}; "
            f"using the rate from {rate_date.isoformat()}{earlier}."
        )
    return body


@app.get("/tools/convert")
async def convert(
    amount: float,
    from_: str = Query(alias="from"),
    to: str = Query(),
    on: date | None = Query(default=None, alias="date"),
) -> dict:
    """Convert ``amount`` from one currency to another at an ECB rate."""
    if not math.isfinite(amount) or amount <= 0:
        raise FxError(400, "invalid_amount", "The amount must be a positive number.")

    base = from_.strip().upper()
    target = to.strip().upper()
    for code in (base, target):
        if not CURRENCY_RE.match(code):
            raise FxError(400, "invalid_currency",
                          f"'{code}' is not a three-letter currency code.")

    known = await known_currencies()
    if known is not None:
        for code in (base, target):
            if code not in known:
                raise FxError(400, "unknown_currency",
                              f"The ECB does not publish a rate for '{code}'.")

    asked_date = on or date.today()

    if on is not None and on > date.today():
        raise FxError(400, "future_date",
                      f"The date {on.isoformat()} is in the future; no rate exists yet.")

    if base == target:
        return _build_response(amount, base, target, 1.0, asked_date, asked_date)

    rate, rate_date = await fetch_rate(base, target, on)
    return _build_response(amount, base, target, rate, rate_date, asked_date)
