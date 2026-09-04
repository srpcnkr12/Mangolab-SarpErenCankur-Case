"""Offline tests for the FX conversion tool.

``respx`` intercepts httpx at the transport layer, so nothing here touches the
network: the suite passes with ``FX_UPSTREAM_BASE`` pointing at a closed port.
"""

import httpx
import pytest

from conftest import CURRENCIES, rate_payload

CONVERT = "/tools/convert"


# --------------------------------------------------------------------------- #
# Happy path
# --------------------------------------------------------------------------- #

def test_weekday_conversion(client, upstream):
    upstream.get(f"{upstream.base}/2025-08-29").mock(
        return_value=httpx.Response(200, json=rate_payload("2025-08-29", "TRY", 47.9536))
    )
    r = client.get(CONVERT, params={"amount": 250, "from": "EUR", "to": "TRY",
                                    "date": "2025-08-29"})
    assert r.status_code == 200
    body = r.json()
    assert body == {
        "amount": 250.0,
        "from": "EUR",
        "to": "TRY",
        "rate": 47.9536,
        "result": 11988.40,
        "rate_date": "2025-08-29",
        "asked_date": "2025-08-29",
        "source": "ECB via frankfurter.dev",
    }
    assert "notice" not in body


def test_response_has_exactly_the_documented_keys(client, upstream):
    upstream.get(f"{upstream.base}/2025-08-29").mock(
        return_value=httpx.Response(200, json=rate_payload("2025-08-29", "USD", 1.08))
    )
    body = client.get(CONVERT, params={"amount": 10, "from": "EUR", "to": "USD",
                                       "date": "2025-08-29"}).json()
    assert set(body) == {"amount", "from", "to", "rate", "result",
                         "rate_date", "asked_date", "source"}
    assert body["source"] == "ECB via frankfurter.dev"


def test_rate_keeps_full_precision_result_rounded_to_cents(client, upstream):
    upstream.get(f"{upstream.base}/2025-08-29").mock(
        return_value=httpx.Response(200, json=rate_payload("2025-08-29", "USD", 1.23456789))
    )
    body = client.get(CONVERT, params={"amount": 1000000, "from": "EUR", "to": "USD",
                                       "date": "2025-08-29"}).json()
    assert body["rate"] == 1.23456789
    assert body["result"] == 1234567.89


def test_no_date_uses_latest(client, upstream):
    latest = upstream.get(f"{upstream.base}/latest").mock(
        return_value=httpx.Response(200, json=rate_payload("2026-09-03", "JPY", 156.01))
    )
    r = client.get(CONVERT, params={"amount": 100, "from": "USD", "to": "JPY"})
    assert r.status_code == 200
    assert latest.called


def test_currency_codes_are_normalised(client, upstream):
    upstream.get(f"{upstream.base}/2025-08-29").mock(
        return_value=httpx.Response(200, json=rate_payload("2025-08-29", "USD", 1.08))
    )
    body = client.get(CONVERT, params={"amount": 1, "from": " eur ", "to": "usd",
                                       "date": "2025-08-29"}).json()
    assert body["from"] == "EUR" and body["to"] == "USD"


# --------------------------------------------------------------------------- #
# Stale rates (no ECB rate for the exact date asked)
# --------------------------------------------------------------------------- #

def test_weekend_returns_earlier_rate_and_says_so(client, upstream):
    # Upstream backfills a Sunday request with Friday's data and reports the
    # Friday date in its own "date" field.
    upstream.get(f"{upstream.base}/2025-08-31").mock(
        return_value=httpx.Response(200, json=rate_payload("2025-08-29", "TRY", 47.9536))
    )
    body = client.get(CONVERT, params={"amount": 250, "from": "EUR", "to": "TRY",
                                       "date": "2025-08-31"}).json()
    assert body["rate"] == 47.9536
    assert body["rate_date"] == "2025-08-29"
    assert body["asked_date"] == "2025-08-31"
    assert body["notice"] == (
        "No ECB rate was published for 2025-08-31; using the rate from "
        "2025-08-29 (2 days earlier)."
    )


# --------------------------------------------------------------------------- #
# Same currency
# --------------------------------------------------------------------------- #

def test_same_currency_returns_identity_without_calling_upstream(client, upstream):
    rate_route = upstream.get(f"{upstream.base}/2025-08-29")
    body = client.get(CONVERT, params={"amount": 250, "from": "EUR", "to": "EUR",
                                       "date": "2025-08-29"}).json()
    assert body["rate"] == 1.0
    assert body["result"] == 250.0
    assert body["rate_date"] == body["asked_date"] == "2025-08-29"
    assert "notice" not in body
    assert not rate_route.called


# --------------------------------------------------------------------------- #
# Input validation
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("amount", [0, -5, "nan", "inf", "-inf"])
def test_non_positive_or_non_finite_amount_is_rejected(client, upstream, amount):
    r = client.get(CONVERT, params={"amount": amount, "from": "EUR", "to": "USD"})
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_amount"
    assert "NaN" not in r.text and "Infinity" not in r.text


def test_missing_amount_is_invalid_parameter(client, upstream):
    r = client.get(CONVERT, params={"from": "EUR", "to": "USD"})
    assert r.status_code == 422
    assert r.json()["error"] == "invalid_parameter"


def test_ten_decimal_amount_is_accepted_and_result_rounded(client, upstream):
    upstream.get(f"{upstream.base}/2025-08-29").mock(
        return_value=httpx.Response(200, json=rate_payload("2025-08-29", "USD", 1.10))
    )
    body = client.get(CONVERT, params={"amount": "1.1234567891", "from": "EUR",
                                       "to": "USD", "date": "2025-08-29"}).json()
    assert body["amount"] == 1.1234567891
    assert body["result"] == 1.24  # 1.1234567891 * 1.10, half-up to cents


def test_malformed_currency_is_invalid_currency(client, upstream):
    r = client.get(CONVERT, params={"amount": 1, "from": "EU", "to": "USD"})
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_currency"


def test_unknown_currency_is_rejected_before_calling_upstream(client, upstream):
    rate_route = upstream.get(f"{upstream.base}/latest")
    r = client.get(CONVERT, params={"amount": 1, "from": "XXX", "to": "USD"})
    assert r.status_code == 400
    assert r.json()["error"] == "unknown_currency"
    assert not rate_route.called


def test_malformed_date_is_invalid_parameter(client, upstream):
    r = client.get(CONVERT, params={"amount": 1, "from": "EUR", "to": "USD",
                                    "date": "2026-13-40"})
    assert r.status_code == 422
    assert r.json()["error"] == "invalid_parameter"


def test_future_date_is_rejected_without_calling_upstream(client, upstream):
    rate_route = upstream.get(f"{upstream.base}/2999-01-01")
    r = client.get(CONVERT, params={"amount": 1, "from": "EUR", "to": "USD",
                                    "date": "2999-01-01"})
    assert r.status_code == 400
    assert r.json()["error"] == "future_date"
    assert not rate_route.called


def test_currency_list_unavailable_still_converts(client, upstream, fx):
    upstream.get(f"{upstream.base}/currencies").mock(side_effect=httpx.ConnectError("down"))
    upstream.get(f"{upstream.base}/2025-08-29").mock(
        return_value=httpx.Response(200, json=rate_payload("2025-08-29", "USD", 1.08))
    )
    r = client.get(CONVERT, params={"amount": 1, "from": "EUR", "to": "USD",
                                    "date": "2025-08-29"})
    assert r.status_code == 200


# --------------------------------------------------------------------------- #
# Pre-series / no rate
# --------------------------------------------------------------------------- #

def test_pre_series_date_returns_no_rate_available(client, upstream):
    upstream.get(f"{upstream.base}/1990-01-01").mock(
        return_value=httpx.Response(404, json={"message": "not found"})
    )
    r = client.get(CONVERT, params={"amount": 1, "from": "EUR", "to": "USD",
                                    "date": "1990-01-01"})
    assert r.status_code == 404
    body = r.json()
    assert body["error"] == "no_rate_available"
    assert "1999-01-04" in body["message"]  # names the ECB series start


# --------------------------------------------------------------------------- #
# Upstream failures
# --------------------------------------------------------------------------- #

def test_upstream_timeout_maps_to_504(client, upstream):
    upstream.get(f"{upstream.base}/2025-08-29").mock(side_effect=httpx.ReadTimeout("slow"))
    r = client.get(CONVERT, params={"amount": 1, "from": "EUR", "to": "USD",
                                    "date": "2025-08-29"})
    assert r.status_code == 504
    assert r.json()["error"] == "upstream_timeout"


def test_upstream_500_maps_to_502(client, upstream):
    upstream.get(f"{upstream.base}/2025-08-29").mock(return_value=httpx.Response(500))
    r = client.get(CONVERT, params={"amount": 1, "from": "EUR", "to": "USD",
                                    "date": "2025-08-29"})
    assert r.status_code == 502
    assert r.json()["error"] == "upstream_error"


def test_upstream_non_json_maps_to_502(client, upstream):
    upstream.get(f"{upstream.base}/2025-08-29").mock(
        return_value=httpx.Response(200, text="<html>gateway</html>")
    )
    r = client.get(CONVERT, params={"amount": 1, "from": "EUR", "to": "USD",
                                    "date": "2025-08-29"})
    assert r.status_code == 502
    assert r.json()["error"] == "upstream_bad_response"


@pytest.mark.parametrize("payload", [{"foo": "bar"}, {"rates": {"USD": 1.0}}, {"date": "2025-08-29"}])
def test_upstream_json_missing_fields_maps_to_502(client, upstream, payload):
    upstream.get(f"{upstream.base}/2025-08-29").mock(
        return_value=httpx.Response(200, json=payload)
    )
    r = client.get(CONVERT, params={"amount": 1, "from": "EUR", "to": "USD",
                                    "date": "2025-08-29"})
    assert r.status_code == 502
    assert r.json()["error"] == "upstream_bad_response"


def test_closed_port_maps_to_502_unreachable(client, fx, monkeypatch):
    # No respx here: a real connection to a closed port.
    monkeypatch.setenv("FX_UPSTREAM_BASE", "http://127.0.0.1:1")
    fx._client = None
    r = client.get(CONVERT, params={"amount": 1, "from": "EUR", "to": "USD",
                                    "date": "2025-08-29"})
    assert r.status_code == 502
    assert r.json()["error"] == "upstream_unreachable"


# --------------------------------------------------------------------------- #
# Caching
# --------------------------------------------------------------------------- #

def test_repeat_question_does_not_reask_upstream(client, upstream):
    route = upstream.get(f"{upstream.base}/2025-08-29").mock(
        return_value=httpx.Response(200, json=rate_payload("2025-08-29", "USD", 1.08))
    )
    for _ in range(3):
        client.get(CONVERT, params={"amount": 250, "from": "EUR", "to": "USD",
                                    "date": "2025-08-29"})
    assert route.call_count == 1


def test_cache_key_ignores_amount(client, upstream):
    route = upstream.get(f"{upstream.base}/2025-08-29").mock(
        return_value=httpx.Response(200, json=rate_payload("2025-08-29", "USD", 2.0))
    )
    a = client.get(CONVERT, params={"amount": 250, "from": "EUR", "to": "USD",
                                    "date": "2025-08-29"}).json()
    b = client.get(CONVERT, params={"amount": 1000, "from": "EUR", "to": "USD",
                                    "date": "2025-08-29"}).json()
    assert route.call_count == 1
    assert a["result"] == 500.0 and b["result"] == 2000.0


def test_weekend_and_resolved_date_share_one_call(client, upstream):
    wknd = upstream.get(f"{upstream.base}/2025-08-31").mock(
        return_value=httpx.Response(200, json=rate_payload("2025-08-29", "USD", 1.08))
    )
    weekday = upstream.get(f"{upstream.base}/2025-08-29").mock(
        return_value=httpx.Response(200, json=rate_payload("2025-08-29", "USD", 1.08))
    )
    client.get(CONVERT, params={"amount": 1, "from": "EUR", "to": "USD", "date": "2025-08-31"})
    client.get(CONVERT, params={"amount": 1, "from": "EUR", "to": "USD", "date": "2025-08-29"})
    assert wknd.call_count == 1
    assert weekday.call_count == 0


def test_latest_and_explicit_today_share_one_call(client, upstream):
    latest = upstream.get(f"{upstream.base}/latest").mock(
        return_value=httpx.Response(200, json=rate_payload("2026-09-03", "USD", 1.16))
    )
    today = upstream.get(f"{upstream.base}/2026-09-03").mock(
        return_value=httpx.Response(200, json=rate_payload("2026-09-03", "USD", 1.16))
    )
    client.get(CONVERT, params={"amount": 1, "from": "EUR", "to": "USD"})
    client.get(CONVERT, params={"amount": 1, "from": "EUR", "to": "USD", "date": "2026-09-03"})
    assert latest.call_count == 1
    assert today.call_count == 0


def test_latest_honours_cache_ttl(client, upstream, monkeypatch):
    monkeypatch.setenv("FX_CACHE_TTL", "0")
    latest = upstream.get(f"{upstream.base}/latest").mock(
        return_value=httpx.Response(200, json=rate_payload("2026-09-03", "USD", 1.16))
    )
    client.get(CONVERT, params={"amount": 1, "from": "EUR", "to": "USD"})
    client.get(CONVERT, params={"amount": 1, "from": "EUR", "to": "USD"})
    assert latest.call_count == 2


def test_pre_series_404_is_cached(client, upstream):
    route = upstream.get(f"{upstream.base}/1990-01-01").mock(
        return_value=httpx.Response(404, json={"message": "not found"})
    )
    statuses = [
        client.get(CONVERT, params={"amount": 1, "from": "EUR", "to": "USD",
                                    "date": "1990-01-01"}).status_code
        for _ in range(3)
    ]
    assert route.call_count == 1
    assert statuses == [404, 404, 404]


def test_transient_failure_is_not_cached(client, upstream):
    route = upstream.get(f"{upstream.base}/2025-07-01").mock(return_value=httpx.Response(500))
    for _ in range(3):
        client.get(CONVERT, params={"amount": 1, "from": "EUR", "to": "USD",
                                    "date": "2025-07-01"})
    assert route.call_count == 3


# --------------------------------------------------------------------------- #
# Wiring
# --------------------------------------------------------------------------- #

def test_upstream_base_env_is_honoured(client, upstream, fx):
    route = upstream.get(f"{upstream.base}/2025-08-29").mock(
        return_value=httpx.Response(200, json=rate_payload("2025-08-29", "USD", 1.08))
    )
    client.get(CONVERT, params={"amount": 1, "from": "EUR", "to": "USD", "date": "2025-08-29"})
    assert route.called
    called_url = str(route.calls.last.request.url)
    # the request went to whatever FX_UPSTREAM_BASE says, never the real host
    assert called_url.startswith(f"{fx._upstream_base()}/{fx._upstream_prefix()}/")
    assert "api.frankfurter.dev" not in called_url


def test_unknown_path_returns_structured_404(client):
    r = client.get("/not-a-real-endpoint")
    assert r.status_code == 404
    assert r.json() == {"error": "not_found", "message": "No such endpoint."}


def test_wrong_method_returns_structured_405(client):
    r = client.post(CONVERT)
    assert r.status_code == 405
    assert r.json()["error"] == "method_not_allowed"


def test_lifespan_starts_and_stops_cleanly(fx):
    from fastapi.testclient import TestClient

    with TestClient(fx.app):
        pass
    assert fx._client is None
