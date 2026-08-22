# Build one yourself

Two parts, fifteen jumper wires, no soldering. About twenty minutes.

## What to buy

| | why this one |
|---|---|
| **Raspberry Pi Pico 2 WH** | the **W** is the wifi, the **H** is the headers already soldered on |
| **3.12" 256x64 SSD1322 OLED**, 16 pin | the size and controller the firmware is written for. Sold by Wanjorlay among others |
| **15 female-to-female jumper wires** | both boards have male pins, so both ends need sockets |
| a micro-USB cable and a USB power supply | any phone charger |

## Why 8080 parallel

The panel can speak either SPI or 8080 8-bit parallel, and which one it uses
is set by two links, BS0 and BS1, on the back of the module. They are solder
bridges.

These modules generally arrive strapped for parallel, so parallel is the
option that needs no iron. It costs pins rather than solder: twelve signal
wires instead of four. The Pico has plenty.

If yours arrives strapped for SPI, this firmware will not drive it until the
links are moved.

## Wiring

Twelve signals and three wires to power. The signals all land on the left edge
of the Pico, pins 1 to 15, so they stay tidy; the power comes off the other
side.

| OLED pin | | Pico pin | | what it does |
|---|---|---|---|---|
| 1 | VSS | 38 | GND | ground |
| 2 | VDD | 36 | 3V3(OUT) | 3.3V power |
| 3 | NC | — | — | leave unconnected |
| 4 | D0 | 1 | GP0 | data bit 0 |
| 5 | D1 | 2 | GP1 | data bit 1 |
| 6 | D2 | 4 | GP2 | data bit 2 |
| 7 | D3 | 5 | GP3 | data bit 3 |
| 8 | D4 | 6 | GP4 | data bit 4 |
| 9 | D5 | 7 | GP5 | data bit 5 |
| 10 | D6 | 9 | GP6 | data bit 6 |
| 11 | D7 | 10 | GP7 | data bit 7 |
| 12 | E / RD# | 36 | 3V3(OUT) | read strobe, held high because we only write |
| 13 | R/W# / WR# | 11 | GP8 | write strobe: the panel takes a byte on its rising edge |
| 14 | D/C# | 12 | GP9 | is this byte a command or pixels |
| 15 | RES# | 14 | GP10 | hardware reset, pulsed once at startup |
| 16 | CS# | 15 | GP11 | chip select, low while we are talking |

`RD#` matters more than it looks. The firmware never reads from the panel, but
if that line is left floating the panel can drive the data bus at the same
time as the Pico, which shows up as a picture that is nearly right rather than
as nothing at all. Some modules pull it up on board; wiring it is the way to
not have to find out.

Two wires therefore want 3V3, and the Pico has one 3V3(OUT) pin. A breadboard
or a two-into-one lead solves it.

Two things to check before powering up:

- **3V3, not VBUS.** The panel is a 3.3V part and 5V will damage it.
- **Count the OLED header from pin 1.** Numbering varies between modules, so
  match the *names* silkscreened on the board rather than trusting the numbers
  above. The
  [SSD1322 datasheet](https://www.hpinfotech.ro/SSD1322.pdf) is the reference
  for what each signal does, and the
  [Pico 2 W pinout](https://datasheets.raspberrypi.com/picow/pico-2-w-pinout.pdf)
  for the other end.

## Flash it

1. Hold **BOOTSEL** while plugging the Pico into your computer. It appears as
   a USB drive.
2. Copy the `.uf2` from the
   [latest release](https://github.com/ben-to-go/pico_train_display/releases/latest)
   onto it. The drive disconnects by itself when it is done.

## Set it up

Power it from a plug rather than your computer, and the panel will show a wifi
name, a password and an address.

1. Join `Pico Train Display` from a phone, password `12345678`.
2. Open the address shown on the panel.
3. Fill in your wifi.
4. Open **Advanced settings** for a
   [Realtime Trains API token](https://api-portal.rtt.io/) and the three
   letter codes for the station and where you are heading, e.g. `SKM` to
   `MYB`. It opens itself if the firmware has no token of its own.
5. Save. It restarts and the board appears.

Everything under **Advanced settings** has a default that works, apart from
the token. A display built with one already in it needs nothing but the wifi:
see [Giving one away](#giving-one-away).

## Giving one away

A present should ask for the wifi and nothing else. Nobody wants to register
for a train API on Christmas morning, so the tokens can travel in the firmware
instead of being typed into the setup page.

Put them in a `.env` next to the Makefile — the same file the simulator
reads — and build:

```
RTT_TOKEN=your-token
OTEL_EXPORTER_OTLP_HEADERS=Authorization=Basic%20...
```

```
make firmware
```

The build prints what it baked in, and how long each value was, so a `.env` it
could not read is obvious rather than silent:

```
  Baked in: RTT_TOKEN (32 chars), OTEL_HEADERS (96 chars)
```

Flash that image and the setup page asks for the wifi and nothing else.
Everything else — the tokens, the station codes, the display options — is
folded into **Advanced settings**, collapsed, with the defaults the page has
always had. Anything left blank there falls back to what was baked in, and
anything typed in wins, which is how a revoked token gets replaced without a
rebuild.

A build with no `.env` bakes nothing and opens that section by itself, since
then there is an API token in it that has to be filled in. So the released
firmware is set up exactly as it always was.

**A baked image is not a secret.** Frozen strings sit in the `.uf2` as plain
text:

```
strings firmware.uf2 | grep -i token
```

Anyone holding the board can read them back the same way, over USB. Which is
fine for a present, and means two things worth doing:

- **Use a token issued for the gift**, not your own. Realtime Trains rate
  limits per account, so a shared token means their display eats your
  allowance, and a separate one can be revoked on its own.
- **Never publish a baked image.** Tagging a release attaches the `.uf2` to
  it, and that would publish the token with it. CI has no `.env`, so images
  built there carry nothing — only your own local builds do.

## Changing the settings later

They are kept in a `config.json` in flash, above where the firmware lives, so
they survive a firmware update. There is no settings page on a running board.

**Changing wifi** takes care of itself. If the board cannot join the network in
its config when it starts up, it shows the setup screen again, so moving house
means plugging it in and filling the form in once more.

**Changing the station** means removing the config, because a board that is
working has no reason to ask. Hold **BOOTSEL**, plug in, and copy two files
across in turn:

1. [`flash_nuke.uf2`](https://datasheets.raspberrypi.com/soft/flash_nuke.uf2),
   which erases the lot. It works on either board.
2. the `.uf2` from the
   [latest release](https://github.com/ben-to-go/pico_train_display/releases/latest)
   again.

The setup screen comes back.

## Reading the log from somewhere else

A display on a wall is a display you cannot see the log of, and the log is the
only thing that says why a board is showing yesterday's departures. Fill in the
two collector fields on the setup page and every line it logs is sent to an
OpenTelemetry collector as well as to the serial port, tracebacks included.

For [Grafana Cloud](https://grafana.com/products/cloud/), both come off your
stack's OTLP page:

- **Log collector token** is the whole `Basic ...` value from the
  `Authorization` header it shows you. If it is written with a `%20` in it,
  that is the space, and the board copes with it either way. This is the only
  one you have to fill in.
- **Log collector URL** comes filled in, and only needs changing if your stack
  is in another region. It is the endpoint ending in `/otlp`.

They arrive under `{service_name="pico-train-display"}`, with
`deployment_environment_name` telling the board apart from the simulator, which
sends the same log from the same code, and `service_instance_id` naming the
run: eight hex characters, fixed for as long as the board is powered and new
after a reset. A display has no clock until NTP answers and no name of its own,
so without it every boot looks like the one before, and:

```
{service_name="pico-train-display", service_instance_id="4736a1a7"}
```

is how you read one night's run rather than all of them at once. A new one
appearing is a board that restarted, which is worth knowing on its own.

Leave the token blank and nothing is sent and nothing is different. The collector is
somewhere to read the log, never something the departures wait for: one that
cannot be reached costs a few seconds a fetch and keeps its lines for the next
go.

The simulator reads the same two settings from `OTEL_EXPORTER_OTLP_ENDPOINT`
and `OTEL_EXPORTER_OTLP_HEADERS` in a `.env` file, which is the pair of
variables Grafana hands out, so `sim/run.sh` ships its log without a
`config.json` entry. `make firmware` reads the same file, which is how a
display given away sends its log home without anyone pasting a header into the
setup page. See [Giving one away](#giving-one-away).

For querying logs from Grafana Cloud or using the CLI tool `tools/logs.py`, see
[docs/observability.md](observability.md).

## If nothing appears

- **Blank panel.** Check 3V3 and GND first, then that all eight data lines are
  in GP0 to GP7 order. One swapped data wire gives noise, not nothing; nothing
  usually means power, `/CS` or `/RES`.
- **Noise or a partial picture.** Check pin 12 (`RD#`) really is at 3V3. A
  floating one lets the panel fight the Pico for the data bus.
- **Wifi name never appears.** The panel is working if you see anything at
  all, so the wiring is fine and the problem is later. Plug it into a computer
  and read the serial output, which says what it was doing. A board with a
  collector configured has already sent you the same thing.
