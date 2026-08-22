# Copyright (c) 2023 Tom Ward
#
# Permission is hereby granted, free of charge, to any person obtaining a copy of
# this software and associated documentation files (the "Software"), to deal in
# the Software without restriction, including without limitation the rights to
# use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of
# the Software, and to permit persons to whom the Software is furnished to do so,
# subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS
# FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR
# COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER
# IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN
# CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
"""The SSD1322's 8080 8-bit parallel bus, bit-banged from the Pico's GPIO.

The panel is strapped for parallel rather than SPI, so there is no peripheral
to hand a buffer to: every byte is put on eight data lines and clocked in with
a write strobe.

Speed is the whole difficulty. A frame is 8,192 bytes and the display is
refreshed sixty times a second, so the loop has about two microseconds a byte.
Toggling nine Pin objects per byte is an order of magnitude short of that, so
the inner loop is viper and writes the GPIO output register directly.

Wiring, which is fixed: GP0-GP7 are the data bus, low bit first, because viper
puts a byte on them with a single store only if they are consecutive and start
at zero. GP8 is the write strobe, GP9 data/command, GP10 reset, GP11 chip
select.
"""

import machine
import micropython
import time

import logging


# SIO atomic register addresses on RP2040 and RP2350. Using the atomic aliases
# (GPIO_OUT_SET and GPIO_OUT_CLR) ensures that writes from Core 1 only touch
# GP0-GP8, completely preventing bus collisions with the CYW43 Wi-Fi chip driven
# on GP23, GP24, GP25, GP29 by Core 0. Section 3.1.4 / SIO register map:
# https://datasheets.raspberrypi.com/rp2350/rp2350-datasheet.pdf
_GPIO_OUT_SET = micropython.const(0xD0000014)
_GPIO_OUT_CLR = micropython.const(0xD0000018)

# The strobe is GP8, and _blast has that baked in as 0x100 because viper wants
# a literal. Moving it means changing both.
_WR_PIN = micropython.const(8)
_DC_PIN = micropython.const(9)
_RST_PIN = micropython.const(10)
_CS_PIN = micropython.const(11)

# Table 13-3 of the datasheet: a write cycle is at least 300ns, of which the
# strobe is low for at least 60ns. Viper is far quicker than that, and a byte
# clocked in too fast is simply not seen, so the strobe is padded out.
#
# Sixty frames a second is 8,192 bytes sixty times over, which at 300ns a byte
# is 2.5ms of every 16.7ms. Meeting the datasheet costs nothing worth having.
_CYCLE_NS = micropython.const(300)
_CALIBRATION_BYTES = micropython.const(1024)
_MAX_PAD = micropython.const(64)


@micropython.viper
def _blast(buf: ptr8, count: int, pad: int):
  """Puts every byte of buf on the bus using atomic SIO registers.

  Writing to GPIO_OUT_CLR and GPIO_OUT_SET modifies only the bits that are
  explicitly set to 1. Bits 9-31 (including CYW43 Wi-Fi pins 23, 24, 25, 29)
  are completely untouched, guaranteeing zero dual-core GPIO clobbering.

  The panel latches on the rising edge of the strobe (GP8), so the byte is
  settled on the bus while the strobe is down, and pad decides how long it
  stays down.
  """
  set_reg = ptr32(0xD0000014)  # _GPIO_OUT_SET; viper wants literal.
  clr_reg = ptr32(0xD0000018)  # _GPIO_OUT_CLR; viper wants literal.
  i = 0
  while i < count:
    b = int(buf[i])
    # 1. Clear data bus GP0-GP7 while strobe (GP8) remains HIGH:
    clr_reg[0] = 0xFF
    # 2. Put data byte bits onto GP0-GP7 (strobe is still HIGH, settling data):
    if b != 0:
      set_reg[0] = b
    # 3. Pull write strobe GP8 LOW:
    clr_reg[0] = 0x100
    # 4. Hold strobe LOW for minimum pulse width (>= 60ns):
    j = 0
    while j < pad:
      clr_reg[0] = 0x100  # Strobe hold / delay
      j += 1
    # 5. Pull write strobe GP8 HIGH (rising edge latches data into SSD1322):
    set_reg[0] = 0x100
    i += 1


def _measure_pad() -> int:
  """Finds the smallest pad that makes a byte take the datasheet's 300ns.

  Measured rather than worked out on paper, because it depends on the core
  clock and on how viper compiles for the board, and the failure it guards
  against is silent. Safe to run: chip select is still high, so the panel is
  not listening to any of it.
  """
  scratch = bytearray(_CALIBRATION_BYTES)

  pad = 1
  while pad < _MAX_PAD:
    start = time.ticks_us()
    _blast(scratch, _CALIBRATION_BYTES, pad)
    elapsed_ns = time.ticks_diff(time.ticks_us(), start) * 1000
    if elapsed_ns // _CALIBRATION_BYTES >= _CYCLE_NS:
      return pad
    pad += 1
  return _MAX_PAD


class ParallelBus:
  """The bus as the driver sees it: bytes, and whether they are commands."""

  def __init__(self):
    # The data lines and the strobe are set up and then let go: configuring a
    # pin leaves the pad an output for good, and the write loop drives them
    # through the register rather than through these objects.
    for pin in range(8):
      machine.Pin(pin, machine.Pin.OUT, value=0)
    machine.Pin(_WR_PIN, machine.Pin.OUT, value=1)

    self._dc = machine.Pin(_DC_PIN, machine.Pin.OUT, value=0)
    self._rst = machine.Pin(_RST_PIN, machine.Pin.OUT, value=1)
    self._cs = machine.Pin(_CS_PIN, machine.Pin.OUT, value=1)

    self._pad = _measure_pad()
    logging.log(
        'Display bus: {} pad stores a byte, {}MHz core',
        self._pad,
        machine.freq() // 1_000_000,
    )

  def reset(self):
    """Pulses the panel's reset line and waits for it to come back."""
    self._rst(0)
    time.sleep_ms(50)
    self._rst(1)
    time.sleep_ms(100)

  def write(self, buf, dc: int):
    """Writes buf to the panel, as commands when dc is 0 and data when 1."""
    self._dc(dc)
    self._cs(0)
    _blast(buf, len(buf), self._pad)
    self._cs(1)
