# 🚂 Pico train departure display 🚂

A MicroPython-based application for displaying near-realtime UK railway
departure times. It runs on a
[Raspberry Pi Pico 2 W](https://www.raspberrypi.com/products/raspberry-pi-pico-2/)
microcontroller, with an SSD1322-based 256x64 OLED display, driven over its
8080 8-bit parallel interface.

| A real platform indicator | This display |
|---|---|
| <img src="https://blog.balena.io/wp-content/uploads/2019/07/hu788k5bih421.jpg" width="380"> | <img src="docs/images/platform_indicator.png" width="380"> |

Both are real. The right-hand board is a genuine Chiltern Railways morning from
Stoke Mandeville, calling points and all, exactly as the API returned it, in
the amber the panel actually glows. The layout was measured off the photograph
beside it: [docs/display-format.md](docs/display-format.md).

- Live times, delays and cancellations, from
  [Realtime Trains](https://api-portal.rtt.io/).
- Set up from your phone. Moving house does not mean reflashing it.
- [Keeps showing departures](docs/fallback.md) when the wifi or the API
  drops out.
- [Remote observability & logging](docs/observability.md) via OpenTelemetry
  and Grafana Cloud Loki.
- [Execution lifecycle & features guide](docs/execution-stages.md) detailing what the app does at every stage.
- Two boards and fifteen wires. Nothing to solder, nothing to print.

## Installation

Off-the-shelf parts, all of it bought online, running the firmware this repo
builds and publishes to its
[releases](https://github.com/ben-to-go/pico_train_display/releases/latest).

[docs/build-your-own.md](docs/build-your-own.md) has the parts, the wiring, and
how to put the firmware on it.

## Development

The display can be worked on without a Pico or a screen: the
[simulator](sim/README.md) runs the firmware unmodified and draws the panel in
your terminal.

| | |
|---|---|
| `make sim` | the panel, in this terminal |
| `make test` | the unit tests, exactly as CI runs them |
| `make firmware` | the uf2, into `build/` |
| `make firmware-depend` | the cross toolchain, from nothing |
| `make sim-depend` | everything the simulator needs, from nothing |
| `make unix-port` | rebuild just the MicroPython the simulator runs on |

[`.github/workflows/build.yml`](.github/workflows/build.yml) runs `make test`
and `make firmware`, and nothing else. So the build CI does is the build you
run, and there is no second copy of it to keep in step.

`make firmware` is incremental: it builds from a MicroPython checkout at
`~/micropython`, which it clones once and then leaves alone. A first build
takes a few minutes, a rebuild after changing `src/` takes about ten seconds.

## Credits

This is a fork. [Tom Ward](https://github.com/tomwardio) wrote
[pico_train_display](https://github.com/tomwardio/pico_train_display), which is
everything this stands on: the driver, the widgets, the setup portal and the
layout. Thank you for building it, and for the licence that let this exist.

The thanks that follow are his.

Firstly, a massive thank you to [Dave Ingram](https://github.com/dingram) for
inspiring me to work on this project in the first place, and helping me with the
hardware and low-level driver software!

Thanks also goes to various other incantations of this project, namely
[Chris Crocker-White](https://github.com/chrisys/train-departure-display),
[Chris Hutchinson](https://github.com/chrishutchinson/train-departure-screen),
and of course [Dave](https://github.com/dingram/uk-train-display).

Also a big thank you to the wonderful folk at
[Realtime Trains](https://www.realtimetrains.co.uk/) for providing a brilliant
API for train departures.

The photograph of a real indicator above is from
[balena's write-up](https://blog.balena.io/) of Chris Crocker-White's build.

Finally thank you to Daniel Hart who created the wonderful
[Dot Matrix](https://github.com/DanielHartUK/Dot-Matrix-Typeface) type face, and
Peter Hinch for his
[font-to-python](https://github.com/peterhinch/micropython-font-to-py) tool,
which saved my sanity.
