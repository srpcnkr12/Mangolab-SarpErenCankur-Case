# Notes

## Decisions

The case that drove most of the design is a weekend or holiday date, where the
ECB never published a rate. I decided to answer rather than refuse. For the
customer, a rate from the previous working day that is clearly labelled as such
is usually more useful than an error, and refusing doesn't really protect
anyone. What matters is that the answer is honest about which day the number
belongs to. Frankfurter already does the hard part: ask it for a Sunday and it
returns Friday's numbers with a `date` field of Friday. So I read that `date`
field and use it as `rate_date`, keep `asked_date` as whatever the caller sent,
and when the two differ I add a `notice` that says so in a sentence. The
endpoint never makes up a number for a day it has nothing for. If the upstream
has nothing at all, because the date is in the future or before the series
starts, it returns `future_date` or `no_rate_available`.

A few smaller calls. `from` equal to `to` returns a rate of 1.0 without touching
the upstream, since that is the one rate I can be certain of and it isn't
inventing anything. `amount` is only rejected when it isn't a positive finite
number; ten decimal places is fine, I keep the rate at full precision and round
the final `result` to cents with `Decimal`. Currency codes are checked against
Frankfurter's own currency list before any rate call, so a typo comes back as
`unknown_currency` with a readable message instead of an opaque upstream 404.
Anything that goes wrong, including a bug I didn't foresee, returns a non-2xx
`{error, message}`; nothing ever returns a number it isn't sure of. Repeated
questions are served from an in-process cache: past dates kept permanently since
they can't change, `latest` kept for an hour (`FX_CACHE_TTL`), keyed on the
currency pair and the date but not the amount.

## With another day

I'd pin the transitive dependencies, not just the five direct ones. I'd move the
logging to structured JSON with a request id, and add a retry with backoff
before giving up on a 5xx from the upstream. A small table of per-currency
series start dates would let a pre-series date fail immediately instead of after
a round trip. Right now two identical requests that miss the cache at the same
moment both call the upstream; a per-key lock would fix that. And I'd add one
test that runs against the real Frankfurter API, kept out of the normal run, to
catch the upstream changing shape.


## AI tools

Claude Code. I used it to poke at the real Frankfurter API with curl
first, to see how it handles weekends, what a 404 versus a 422 looks like, and
that the `/v1` prefix is required, then to draft `app.py`, the tests and the
docs, and to work through `tool.py` for Part B. I checked each upstream
behaviour myself rather than take its word for it, and wrote the tests against
what I saw.
For the coding part of course claude did the job, but I checked every step of it and gave directions when its necessary. For example claude suggested that we should directly give an error when customer asks in weekends but I find it unhandy, so I wanted to give the last currency but with a warning message to the customer.

## One thing the AI got wrong

The first version of the stale-rate `notice` asserted the cause: "No ECB rate
for 2026-08-31 (weekend/holiday); ...". But the upstream never tells us why a
day has no rate. It could be a weekend, an ECB holiday, or just that today's
rate hasn't been published yet, since it lands around 16:00 CET. Saying
"weekend" would sometimes be a guess presented as fact, which is the exact thing
this task is about. I noticed it while writing the assertion for that `notice`
and thinking through what the response would say for a request made on a weekday
morning. I changed it to state only what we actually know: the date asked for,
the date the rate is from, and how many days apart they are.

For the review part, of course I got help from claude since I am not that experienced with writing service, but the problems about the basic human reasoning like every problem returning 200 with a message "your money worth 0" or the holiday problem were could be thought of simply. 
