# What the board does when something breaks

The display hangs off a chain: mains power, then wifi, then a clock, then two
API calls. Every link in it fails sooner or later.

There are two rules, and which applies depends on whether the board has got
going yet. **At startup, no wifi means the settings get asked for again**,
because a board that cannot reach its network is no use and the screen that
fixes it is the one it already knows how to show. **Once it is running, it
keeps showing departures**, because a platform indicator showing yesterday's
train is more use than one showing nothing, and nothing after that point is
worth interrupting a working display over.

This is what happens at each link, in the order the firmware meets them.

## The chain, from cold

| # | step | where | if it fails |
|---|---|---|---|
| 1 | read `config.json` | `main.main` | no config means first boot: run the [setup portal](#first-boot-no-config) instead |
| 2 | bring the panel up | `display.create` | nothing catches this. See [not covered](#what-is-not-covered) |
| 3 | join wifi, 15s | `main._connect` | [ask for the settings again](#no-wifi-at-startup) |
| 4 | set the clock from NTP, 15s | `main._configure_time` | log it, carry on with the clock unset |
| 5 | first departures fetch | `trains.DepartureUpdater.update` | show the [baked-in board](#the-baked-in-board), mark it stale |
| 6 | start drawing | `main._render_thread` | — |
| 7 | refetch, forever | the loop in `main.run` | keep the last good board, [dot on](#the-stale-dot), try again |

Step 3 is the only one that stops the rest. Steps 4 and 5 are each allowed to
fail without stopping what follows, so **by step 6 there is always something on
the screen**, whether or not the clock or the API answered.

## No wifi at startup

The board shows the setup screen, the same one a board with no config shows.

Not being able to join a network looks identical whether the password was
typed in wrong a minute ago or the network was renamed last week, and the same
screen fixes both, so the board does not try to tell them apart. It is also
the only thing it can do about either.

This is startup only. A network that drops out later is the refetch loop's
problem, and it deals with it by carrying on.

The cost is that a power cut takes out the router as well, and the Pico comes
back faster than the router does. Fifteen seconds is not always long enough to
wait for one, and a board that gives up lands in setup until someone notices.

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

## Recovery and error handling

The device differentiates between three distinct error scenarios:

```
                                  Departure Update Failed
                                             |
                   +-------------------------+-------------------------+
                   |                                                   |
           RateLimitError (429)                               Other Exception
                   |                                                   |
        Wait error.retry_after (e.g. 120s)              +--------------+--------------+
            (NO REBOOT)                                 |                             |
                                                Socket/Radio Error?            API Server Error?
                                           (EHOSTUNREACH, ETIMEDOUT,         (500, 502, 503, 404,
                                            isconnected() == False)           bad JSON format)
                                                        |                             |
                                                   REBOOT NOW                 Wi-Fi is healthy!
                                                (machine.reset())             Mark board stale,
                                                                             keep clock ticking,
                                                                             wait update_interval
                                                                                   (120s)
                                                                                 (NO REBOOT)
```

### 1. Unrecoverable Wi-Fi / Radio Error -> Immediate Reboot
When the CYW43 Wi-Fi driver wedges (`STAT_CONNECTING` / `EHOSTUNREACH`), software reconnection calls fail 100% of the time. Rather than wasting time attempting reconnects, the device immediately triggers `machine.reset()`. The Pico 2 reboots and re-associates with Wi-Fi within **1.2 seconds**, bringing fresh departures back immediately rather than leaving the display stale for 30 minutes.

### 2. API 429 Rate Limit -> Backoff & Respect Server
When Realtime Trains responds with HTTP 429 (`RateLimitError`), the device sleeps for `max(update_interval, error.retry_after)` seconds without rebooting, respecting the API budget.

### 3. API Server Outage (500, 502, 503, 404, Bad Format) -> No Reboot Loop
If Realtime Trains is down for maintenance or returning 5xx errors while local Wi-Fi is connected, the device **does not reboot**. The UI remains fully active (clock ticking smoothly, last known departures shown on the panel, stale dot illuminated), and the update loop retries quietly every `update_interval` (120s).

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

`config.json` missing is not a failure, it is the setup path. So is one this
firmware cannot read: a setting that has since been removed, a value out of
range, a file that got truncated. All of them mean there is nothing to run on,
so all of them ask:

1. the Pico starts its own access point, `Pico Train Display` / `12345678`
2. the panel shows that name, the password, and an IP
3. you join it and fill in the form the Pico serves
4. it validates, writes `config.json`, and reboots into the board

There is no settings page on a running display. To change the station, delete
`config.json` over USB or reset the flash. To change the network, you can also
just move the board somewhere its old one is not: it will fail to join at
startup and ask.

## What is not covered

Worth being explicit about the edges:

- **The panel itself.** If `display.create()` fails there is nowhere to report
  it, and the board resets.
- **The clock, when NTP never answers.** The board draws whatever the RTC
  says, which on a Pico with no network is wrong rather than absent.
- **A full flash.** `debug.txt` is appended to and never rotated.
