# Desktop simulator

Runs the firmware on your machine and draws the 256x64 panel in the terminal,
so the display can be worked on without a Pico or a screen.

It is a fake display and nothing else. `main.py` runs unmodified, against the
project's own `config.json`, talking to the real Realtime Trains API. Four
modules ahead of `src/` on `MICROPYPATH` stand in for what a desktop does not
have:

| | |
|---|---|
| `machine.py` | `Pin` and `SPI`. Opening the display's bus creates the panel |
| `panel.py` | the SSD1322 itself, decoding the driver's SPI stream and drawing what it reconstructs |
| `network.py` | a CYW43 that is always associated |
| `ntptime.py` | NTP, since the host clock is already right |

`panel.py` is not a mock of the driver, it is a mock of the *chip*. It decodes
the real SSD1322 command stream `src/ssd1322.py` writes (`0x15` column
address, `0x75` row address, `0x5C` write-RAM, re-map, sleep and wake) and
reconstructs the picture the panel would be showing. The driver, the GS4_HMSB
framebuffer packing and the widget layout are all genuinely exercised.

`run.py` exists for the two things a stand-in module cannot do: the firmware's
log would scribble over the panel, so it goes to `sim/out/firmware.log`, and
the setup portal asks for port 80, which a normal user may not bind.

## Setup

Build the MicroPython unix port once:

```sh
make unix-port          # or: make unix-port MICROPYTHON_DIR=/somewhere/else
```

`MICROPY_PY_FFI=0` avoids needing `libffi-dev`. `MICROPY_PY_THREAD_GIL=1`
matters because `main.run()` renders on a second thread, and the unix port
builds threads without a GIL by default. Override the location with
`MICROPYTHON=/path/to/micropython`.

Then fill in `config.json`: at minimum `station`, `destination` and
`rtt.token`. A token in a `.env` file beside it is picked up too, so it need
not be committed.

## Running

```sh
make sim                # the panel, at its real 256x64
make sim-compact        # braille cells, for terminals narrower than 256
```

Or `sim/run.sh` directly, which is all the Makefile does.

The default view is one terminal cell per 1x2 pixels, so the panel appears at
its true width and wants a 256 column terminal. With no `config.json` you get
the setup portal on http://127.0.0.1:8088, exactly as the device serves it.

## None of this reaches the device

`manifest.py` freezes `src/` and nothing else, so no part of this directory is
in the firmware. Searching a built image for the modules confirms it:

```
sim modules frozen into it:
  panel.py   False
  run.py     False
  network.py False
  machine.py False
```

(`ntptime` does appear in the firmware, but that is MicroPython's own, frozen
by the board manifest. `machine` is a builtin C module there.)

## Limits

- Wifi association and flash wear are not exercised.
- Timing is host timing, not RP2350 timing, so this says nothing about whether
  a frame fits in the refresh budget on real hardware.
- Every run makes real API calls, which count against your rate limit.
