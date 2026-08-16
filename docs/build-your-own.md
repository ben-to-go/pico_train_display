# Build one yourself

Two parts, fourteen jumper wires, no soldering. About twenty minutes.

## What to buy

| | why this one |
|---|---|
| **Raspberry Pi Pico 2 WH** | the **W** is the wifi, the **H** is the headers already soldered on |
| **3.12" 256x64 SSD1322 OLED**, 16 pin | the size and controller the firmware is written for. Sold by Wanjorlay among others |
| **14 female-to-female jumper wires** | both boards have male pins, so both ends need sockets |
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

Twelve signals, plus power. The twelve signals are all on the left edge of the
Pico, pins 1 to 15, so they stay tidy; power comes off the other side.

| OLED pin | | Pico GP | Pico physical pin |
|---|---|---|---|
| 1 | GND | GND | 3 |
| 2 | VCC | 3V3(OUT) | 36 |
| 4 | D0 | GP0 | 1 |
| 5 | D1 | GP1 | 2 |
| 6 | D2 | GP2 | 4 |
| 7 | D3 | GP3 | 5 |
| 8 | D4 | GP4 | 6 |
| 9 | D5 | GP5 | 7 |
| 10 | D6 | GP6 | 9 |
| 11 | D7 | GP7 | 10 |
| 13 | /WR | GP8 | 11 |
| 14 | D/C | GP9 | 12 |
| 15 | /RES | GP10 | 14 |
| 16 | /CS | GP11 | 15 |

Pin 3 is not connected and pin 12 is `/RD`, which the firmware never uses.
Leave both alone unless nothing appears, in which case see below.

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
2. Copy
   [`pico_train_display_RPI_PICO2_W.uf2`](https://github.com/ben-to-go/pico_train_display/releases/latest/download/pico_train_display_RPI_PICO2_W.uf2)
   onto it. The drive disconnects by itself when it is done.

## Set it up

Power it from a plug rather than your computer, and the panel will show a wifi
name, a password and an address.

1. Join `Pico Train Display` from a phone, password `12345678`.
2. Open the address shown on the panel.
3. Fill in your wifi, a
   [Realtime Trains API token](https://api-portal.rtt.io/), and the three
   letter codes for the station and where you are heading, e.g. `SKM` to
   `MYB`.
4. Save. It restarts and the board appears.

Those settings live in flash and survive a firmware update. To change them,
delete `config.json` over USB or erase the flash with
[flash_nuke.uf2](https://datasheets.raspberrypi.com/soft/flash_nuke.uf2) and
flash the firmware again.

## If nothing appears

- **Blank panel.** Check 3V3 and GND first, then that all eight data lines are
  in GP0 to GP7 order. One swapped data wire gives noise, not nothing; nothing
  usually means power, `/CS` or `/RES`.
- **Noise or a partial picture.** Try tying OLED pin 12 (`/RD`) to 3V3. Most
  modules pull it up on board, but not all, and a floating `/RD` lets the
  panel fight the Pico for the bus.
- **Wifi name never appears.** The panel is working if you see anything at
  all, so the problem is later: plug into a computer and read the log with
  `mpremote connect auto`.
