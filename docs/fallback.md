# What the board does when something breaks

The display hangs off a chain: mains power, then wifi, then a clock, then two
API calls. Every link in it fails sooner or later. The rule this project
follows is that **the board keeps showing departures**, because a platform
indicator showing yesterday's train is more use than one showing nothing, and
none of it is worth resetting the device over.

This is what happens at each link, in the order the firmware meets them.

## The chain, from cold

| # | step | where | if it fails |
|---|---|---|---|
| 1 | read `config.json` | `main.main` | no config means first boot: run the [setup portal](#first-boot-no-config) instead |
| 2 | bring the panel up | `display.create` | nothing catches this. See [not covered](#what-is-not-covered) |
| 3 | join wifi, 15s | `main._connect` | log it, carry on with no network |
| 4 | set the clock from NTP, 15s | `main._configure_time` | log it, carry on with the clock unset |
| 5 | first departures fetch | `trains.DepartureUpdater.update` | show the [baked-in board](#the-baked-in-board), mark it stale |
| 6 | start drawing | `main._render_thread` | — |
| 7 | refetch, forever | the loop in `main.run` | keep the last good board, [dot on](#the-stale-dot), try again |

Steps 3, 4 and 5 are each allowed to fail without stopping the ones after
them. That is the whole design: **by step 6 there is always something on the
screen**, whether or not any of 3 to 5 worked.

## The refetch loop

Every `rtt.update_interval` seconds (`config.json`), the loop rebuilds the
chain from wherever it broke:

1. **Is wifi up?** `wlan is None or not wlan.isconnected()` → try to join again,
   quietly, without disturbing what is on the panel.
2. **Is the clock set?** If wifi is up and NTP never answered, try again.
3. **Fetch departures**, once.
   - `ECONNABORTED` mid-request means the connection dropped while still
     associated, which the check in step 1 would not catch, so that one
     reassociates before the next cycle.
4. **Sleep**, for the interval if that worked and for longer if it did not.
   See [the rate limit](#the-rate-limit).

Nothing in here raises. A failed fetch is not an error, it is a cycle where the
board carried on showing what it already had.

One request a cycle, and no retrying inside one. Retrying is the obvious
response to a failure and the wrong one here, because the request budget is
small enough that a few retries a cycle can spend it all and leave nothing for
the recovery.

## The rate limit

The API counts requests per minute, hour, day and week, and this account gets
**10 a minute and 100 an hour, 1000 a day**. Over any of them it answers 429
with a `Retry-After` of minutes rather than seconds.

Each update costs one request, plus one more whenever the train at the top of
the board changes and its calling points have to be fetched, plus two every
twenty minutes when the access token expires and has to be exchanged again.

Counted through the real update path, by `tests/test_rate_limit.py`:

| `update_interval` | an hour | a day | a week | |
|---|---|---|---|---|
| 20s | 182 | 4,322 | 30,242 | over all three |
| 60s | 62 | 1,442 | 10,082 | over the daily and weekly |
| 90s | 42 | 962 | 6,722 | inside, barely |
| **120s** | **32** | **722** | **5,042** | the default |

The daily allowance is what sets the pace, and departures do not change fast
enough for anything quicker to be worth it.

## Backing off

There is no retry loop. A failure just brings the next go forward, by less or
more depending on how many have failed in a row:

| failures in a row | wait |
|---|---|
| 1 | 1s |
| 2 | 5s |
| 3 | 30s |
| 4 | 120s |
| 5 | 600s |
| 6 and after | 1800s |

So a blip costs one extra request and a second of staleness, while something
properly broken is asked about less and less. A 429 is not on this ladder at
all: the API sends the seconds to wait, and never less than the interval.

The point is that **failing costs fewer requests than working does**, so a bad
patch cannot spend the budget the recovery needs. Measured over an hour by
`tests/test_rate_limit.py`: 32 requests while healthy, 7 while the API is
down, 3 while rate limited.

## The baked-in board

`src/fallback.py` holds two real API responses, captured from a weekday
morning at Stoke Mandeville: a line-up of three trains to London Marylebone,
and the calling points of the first of them. They are stored exactly as the
API returned them and parsed by the same code as live responses, so changing
how responses are read does not mean regenerating them.

It is used in exactly one situation: **a fetch has failed and no fetch has
ever succeeded** (`trains.py`, `if not self._fetched`). So:

- fresh boot with the API unreachable → the baked-in board
- API dies after an hour of working → the *last live board* stays up, not this
- API comes back → live data replaces it and it is never shown again

The departures in it are a fixed snapshot, so `min_departure_time` is not
applied to them: "departing in the next few minutes" means nothing for a train
that left in the past.

## The stale dot

A single pixel in the bottom right corner, drawn while the departures on show
failed to refresh, cleared when they are current. Small enough to be invisible
across a room, obvious if you know to look.

It is redrawn every frame rather than only when it changes, so it survives
anything that clears the screen.

**The dot says the data is old. It does not say why.** Wifi down, token
revoked, API retired, or a response that no longer parses all look the same
from the pavement.

## First boot, no config

`config.json` missing is not a failure, it is the setup path:

1. the Pico starts its own access point, `Pico Train Display` / `12345678`
2. the panel shows that name, the password, and an IP
3. you join it and fill in the form the Pico serves
4. it validates, writes `config.json`, and reboots into the board

The config is only ever written when there isn't one. There is no settings
page on a running display; to change anything, delete `config.json` over USB
or reset the flash.

## What is not covered

Worth being explicit about the edges:

- **The panel itself.** If `display.create()` fails there is nowhere to report
  it, and the board resets.
- **A corrupt `config.json`.** Malformed JSON raises where only `OSError` is
  caught, so the board resets, and the setup portal does not appear because
  the file exists. Recovery is over USB.
- **The clock, when NTP never answers.** The board draws whatever the RTC
  says, which on a Pico with no network is wrong rather than absent.
- **A full flash.** `debug.txt` is appended to and never rotated.
