# 🚂 Pico train departure display 🚂

A MicroPython-based application for displaying near-realtime UK railway
departure times. It runs on a
[Raspberry Pi Pico 2 W](https://www.raspberrypi.com/products/raspberry-pi-pico-2/)
microcontroller, with an SSD1322-based 256x64 OLED display, driven over its
8080 8-bit parallel interface.

This project uses the
[Realtime Trains API](https://api-portal.rtt.io/) as its data source,
and is heavily inspired by [several other projects](#credits).

The layout copies a real National Rail platform indicator, down to the row
geometry: four rows of nine pixels, text using seven of them with two for
descenders, and the clock, which has none, filling all nine.

| A real platform indicator | This display |
|---|---|
| <img src="https://blog.balena.io/wp-content/uploads/2019/07/hu788k5bih421.jpg" width="380"> | <img src="docs/images/platform_indicator.png" width="380"> |

Both are real: the right-hand board is a genuine Chiltern Railways morning
from Stoke Mandeville, calling points and all, exactly as the API returned it,
in the amber the panel actually glows.

How that was measured, and what it means for the fonts, is in
[docs/display-format.md](docs/display-format.md). What the board does when the
wifi, the clock or the API give out is in
[docs/fallback.md](docs/fallback.md).

## Introduction

The goal of this project is to display a live departure board for a station,
showing trains departing for a specific destination. It's written entirely in
Python and should be able to run on any microcontroller that is capable of
running MicroPython.

It runs on a
[Raspberry Pi Pico 2 W](https://www.raspberrypi.com/products/raspberry-pi-pico-2/)
and an SSD1322 panel, which is the only combination anyone has to hand to test
against.

## Building your own display

Nothing to solder and nothing to print. The Pico comes with its headers
already on, the panel has its own, and jumper wires go straight between the
two, so it is one sitting and no tools you do not already own.

[docs/build-your-own.md](docs/build-your-own.md) has the parts and the wiring.

## Installation

The easiest way is to install the Pico Train Dispaly software is to download the
pre-built image from the
[latest release](https://github.com/ben-to-go/pico_train_display/releases/latest).
To install:

1. Press and hold down the BOOTSEL button while you connect the other end of the
   micro-USB cable to your computer. This will put the Raspberry Pi Pico into
   USB mass storage device mode.
1. Copy
   [`pico_train_display_RPI_PICO2_W.uf2`](https://github.com/ben-to-go/pico_train_display/releases/latest/download/pico_train_display_RPI_PICO2_W.uf2)
   to the mounted device. Once complete, it should automatically disconnect.
1. Connect the Raspberry Pi Pico to a power supply. The display should now show
   a welcome message with details on how to connect to the setup website.
1. Follow the on-screen instructions. Once the settings are saved, the device
   should automatically restart.

You should now have a fully configured Pico-powered train display!

### Reset settings

Settings are stored in flash memory as a JSON file called `config.json`. To
reset all settings, simply delete this file. One easy way to do this is to reset
the entire flash memory, which can be done by following the official
[resetting flash memory](https://www.raspberrypi.com/documentation/microcontrollers/raspberry-pi-pico.html#resetting-flash-memory)
instructions. Once flashed, you'll need to re-install the software again.

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
