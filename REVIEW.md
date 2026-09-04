# Review of tool.py

I ran `tool.py` with `uvicorn tool:app` against the real frankfurter.dev and
reproduced each of these. They're ranked by what they do to a paying customer.

## 1. Every upstream problem comes back as "your money is worth zero", with a 200

`convert()` wraps the whole body in `except Exception`, prints the error, and
returns `rate: 0.0, result: 0.0` with a normal-looking `rate_date` and `source`
and a 200 status. A timeout, a 500, a non-JSON body, a refused connection and an
unknown currency all end up there. The model calling this tool has no way to
tell that anything went wrong, so it will read "250 EUR is 0 TRY" back to the
customer as a fact. Transient upstream trouble isn't rare, so this happens in
normal operation, not just in a disaster.

To see it: `GET /tools/convert?amount=250&from_=EUR&to=ZZZ` returns
`{"rate":0.0,"result":0.0,"rate_date":"2026-09-03", ...}` with a 200. Pointing
the upstream at a closed port gives the same response.

## 2. The cache never uses the date, so the first lookup poisons the pair

The cache key is `f"{base}-{target}"` and entries never expire. The first
request for a currency pair fixes its rate for as long as the process runs;
every later request for that pair, any date and `latest` included, gets that
first number back, stamped with whatever date the new caller asked for. No
upstream failure is needed for this. On completely healthy traffic the service
quietly returns wrong conversions that look entirely normal.

To see it: ask for `EUR->USD` on `2015-01-05` and you get about 1.19, then ask
for `EUR->USD` with no date and you get 1.19 again, dated today, when the real
rate is around 1.16.

## 3. It ignores `from` and `date`, and labels the rate with the wrong day

Two problems that add up to the same thing: the answer doesn't match the
question. The handler's parameters are `from_` and `on`, not `from` and `date`,
and neither has an alias, so the brief's own URL
(`?amount=250&from=USD&to=JPY&date=2020-01-02`) binds neither one. You get EUR to
JPY at today's rate. On top of that, `rate_date` is always
`str(on or date.today())` and the upstream's `date` field is never read, so on
any weekend or holiday the rate is the previous working day's while the response
says it's the day that was asked for. There's no `asked_date` field at all, so
nothing in the response reveals the substitution.

To see it: the URL above comes back with `"from":"EUR"` and
`"rate_date":"<today>"`. And `?...&on=2025-08-31`, a Sunday, comes back with
`"rate_date":"2025-08-31"` while the upstream response for that same call
carries `"date":"2025-08-29"`.

## Smaller, but still wrong

`rate = round(rate, 2)` rounds the rate before multiplying, not just the final
result. EUR->TRY at 47.9536 becomes 47.95, which is 3,600 TRY off on a million
and roughly 0.1 to 0.5 percent off on every conversion. There's no check on
`amount` either: a negative value gives a negative result, and `nan` or a huge
number give `result: null`, a success-shaped response with no number in it.
Finally the upstream URL is hardcoded, `tool.py` never reads `FX_UPSTREAM_BASE`
or `PORT`, so it can't be pointed at the fake upstream for review, and the
shared `AsyncClient` has no timeout set and is never closed.

## The one I would fix before shipping tonight

Finding 1. Removing the `except Exception` / `0.0` block and returning a non-2xx
error instead turns every one of those failures from a confident wrong number
into an honest "no answer", which is the whole point of the brief, and it's the
smallest change of the three. Findings 2 and 3 are close behind and I wouldn't
really ship without them, but if I could only touch one thing tonight it's that
one.

## Things that look suspicious but are fine

Creating the `httpx.AsyncClient` at import time looks wrong but works under a
normal `uvicorn tool:app` run, since it binds to the event loop on first use.
The plain dict cache has no lock, but there's no corruption risk in
single-threaded async code; the missing date in the key is the real bug, not the
missing lock. Doing `amount * rate` locally instead of using Frankfurter's own
`amount` parameter is the right choice, because one cached unit rate then serves
any amount. Rounding the `result` to two decimals is correct; it's rounding the
rate that's the problem. And `payload.get("rates", {})` on the first lookup is
genuinely defensive; it's the unguarded `payload["rates"][target]` after the
fallback that feeds finding 1.
