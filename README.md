# fx-tool

A currency-conversion endpoint an AI agent can call as a tool. It answers from
the public [Frankfurter API](https://frankfurter.dev) (European Central Bank
reference rates — no key, no signup).

The caller is a language model talking to a paying customer, so **a wrong number
is worse than no number**: the endpoint never invents a rate, and never presents
a rate as belonging to a date it does not belong to.

## Run

```sh
./run.sh
```

Listens on `$PORT` (default `8080`). Upstream base URL is `$FX_UPSTREAM_BASE`
(default `https://api.frankfurter.dev`) — nothing hardcodes the real host.

```sh
PORT=9000 FX_UPSTREAM_BASE=https://api.frankfurter.dev ./run.sh
```

## Test

```sh
./test.sh
```

No network required — the upstream is faked with `respx`. Passes with
`FX_UPSTREAM_BASE` pointing at a closed port.

## Endpoint

```
GET /tools/convert?amount=250&from=EUR&to=TRY&date=2026-08-28
```

| param | required | notes |
|---|---|---|
| `amount` | yes | a positive number |
| `from` | yes | three-letter currency code |
| `to` | yes | three-letter currency code |
| `date` | no | `YYYY-MM-DD`; omitted ⇒ latest published rates |

### Success — `200`

```json
{
  "amount": 250,
  "from": "EUR",
  "to": "TRY",
  "rate": 47.9536,
  "result": 11988.40,
  "rate_date": "2026-08-28",
  "asked_date": "2026-08-28",
  "source": "ECB via frankfurter.dev"
}
```

- **`rate_date`** — the day the rate you were given actually belongs to.
- **`asked_date`** — the day you asked about.
- When they differ, the response also carries a **`notice`** and `result` is
  computed from the most recent *earlier* rate:

```json
{
  "...": "...",
  "rate_date": "2026-08-28",
  "asked_date": "2026-08-30",
  "notice": "No ECB rate was published for 2026-08-30; using the rate from 2026-08-28 (2 days earlier)."
}
```

### Failure — non-2xx

```json
{ "error": "<machine_code>", "message": "<a sentence a person could read>" }
```

## Error codes

| code | HTTP | when |
|---|---|---|
| `invalid_parameter` | 422 | `amount` missing or not a number, `date` not `YYYY-MM-DD`, `from`/`to` missing |
| `invalid_amount` | 400 | `amount` is zero, negative, `NaN`, or infinite |
| `invalid_currency` | 400 | a currency code is not three letters |
| `unknown_currency` | 400 | a three-letter code the ECB does not publish |
| `future_date` | 400 | `date` is after today |
| `no_rate_available` | 404 | a valid, non-future `date` the ECB never covered (before the pair's series begins) |
| `upstream_timeout` | 504 | the upstream did not answer within `FX_UPSTREAM_TIMEOUT` seconds |
| `upstream_unreachable` | 502 | could not connect to the upstream |
| `upstream_error` | 502 | the upstream returned 5xx or another unexpected status |
| `upstream_bad_response` | 502 | the upstream returned non-JSON, or JSON without the `rates`/`date` we need |
| `not_found` | 404 | unknown path |
| `method_not_allowed` | 405 | wrong HTTP method on `/tools/convert` |
| `internal_error` | 500 | an unhandled bug — still `{error, message}`, never a fabricated rate |

## What it does in each case

| situation | behaviour |
|---|---|
| ECB published no rate for the date asked (weekend, holiday, not published yet) | `200` with the most recent **earlier** rate; `rate_date` shows the real day, `asked_date` shows yours, `notice` explains. Never a made-up number for the asked day. |
| `date` in the future | `400 future_date` — no upstream call |
| `date` before the series starts | `404 no_rate_available` — message names `1999-01-04` for pre-1999 dates |
| currency code does not exist | `400 unknown_currency` — checked against the ECB currency list *before* any rate call |
| `from` and `to` are the same | `200`, `rate` `1.0`, `result` == `amount` — no upstream call |
| upstream slow | `504 upstream_timeout` after `FX_UPSTREAM_TIMEOUT` seconds |
| upstream returns 500 | `502 upstream_error` |
| upstream returns non-JSON | `502 upstream_bad_response` |
| `amount` missing | `422 invalid_parameter` |
| `amount` zero or negative | `400 invalid_amount` |
| `amount` with ten decimal places | `200` — accepted as given; `rate` kept at full precision, `result` rounded to 2 dp (half-up) |

## Caching

ECB rates are published once per working day, not streamed. A repeated question
is not re-asked upstream:

- a rate for a **past date** is cached permanently (it can never change);
- **`latest`** / today is cached for `FX_CACHE_TTL` seconds (default `3600`);
- the cache key is `(from, to, date)` — **independent of `amount`** — and every
  answer is stored under both the date asked and the date the rate belongs to,
  so `latest`, an explicit today, and the resolved date all share one call;
- "no rate for this past date" is cached too; timeouts and 5xx are not.

## Configuration

| env var | default | |
|---|---|---|
| `PORT` | `8080` | listen port (used by `run.sh`) |
| `FX_UPSTREAM_BASE` | `https://api.frankfurter.dev` | upstream base URL |
| `FX_UPSTREAM_PREFIX` | `v1` | upstream path prefix; requests go to `{BASE}/{PREFIX}/{date|latest}` |
| `FX_UPSTREAM_TIMEOUT` | `5` | per-request timeout, seconds |
| `FX_CACHE_TTL` | `3600` | seconds a `latest`/today rate stays cached |
