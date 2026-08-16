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


# SIO, where the RP2 keeps its GPIO output register.
#
# Deliberately GPIO_OUT rather than the atomic GPIO_OUT_SET alias. SET is at
# +0x14 on RP2040 but +0x18 on RP2350, where +0x14 is GPIO_HI_OUT instead, so
# an alias hardcoded for one board writes nowhere on the other. GPIO_OUT is at
# +0x10 on both, and read-modify-write is safe here because the render thread
# is the only thing driving these lines.
_GPIO_OUT = micropython.const(0xD0000010)

# The strobe is GP8, and _blast has that baked in as 0x100 because viper wants
# a literal. Moving it means changing both.
_WR_PIN = micropython.const(8)
_DC_PIN = micropython.const(9)
_RST_PIN = micropython.const(10)
_CS_PIN = micropython.const(11)

# The data lines and the strobe: everything the write loop owns.
_BUS_MASK = micropython.const(0x1FF)

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
def _blast(idle: int, buf: ptr8, count: int, pad: int):
  """Puts every byte of buf on the bus, one strobe each.

  idle is the rest of the output register with the data lines and the strobe
  already masked out, so the loop only ever puts back the bits it owns and
  leaves data/command, reset and chip select where the caller set them.

  The panel latches on the rising edge of the strobe, so the byte is settled
  on the bus a store before the strobe goes down, and pad decides how long it
  stays down.
  """
  out = ptr32(0xD0000010)  # _GPIO_OUT; viper wants the literal.
  i = 0
  while i < count:
    high = idle | int(buf[i]) | 0x100
    low = high ^ 0x100
    out[0] = high  # byte on the bus, strobe still high
    j = 0
    while j < pad:
      out[0] = low  # strobe down, and held there
      j += 1
    out[0] = high  # and up: the panel takes the byte on this edge
    i += 1


def _measure_pad() -> int:
  """Finds the smallest pad that makes a byte take the datasheet's 300ns.

  Measured rather than worked out on paper, because it depends on the core
  clock and on how viper compiles for the board, and the failure it guards
  against is silent. Safe to run: chip select is still high, so the panel is
  not listening to any of it.
  """
  scratch = bytearray(_CALIBRATION_BYTES)
  idle = machine.mem32[_GPIO_OUT] & ~_BUS_MASK

  pad = 1
  while pad < _MAX_PAD:
    start = time.ticks_us()
    _blast(idle, scratch, _CALIBRATION_BYTES, pad)
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
    idle = machine.mem32[_GPIO_OUT] & ~_BUS_MASK
    _blast(idle, buf, len(buf), self._pad)
    self._cs(1)
