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
"""Implementation of SSD 1322 display driver.

Datasheet: https://www.hpinfotech.ro/SSD1322.pdf
"""

import framebuf

import display
import logging


def _find_changed_rows(
    new_buf, old_buf, row_bytes: int, rows: int
) -> tuple[int, int] | None:
  """Finds first and last row where new_buf and old_buf differ."""
  first = -1
  last = -1
  for r in range(rows):
    start = r * row_bytes
    stop = start + row_bytes
    if new_buf[start:stop] != old_buf[start:stop]:
      if first < 0:
        first = r
      last = r
  if first < 0:
    return None
  return first, last


class SSD1322(display.Display):
  """SSD1322 driver, talking to the panel over an 8080 8-bit parallel bus."""

  def __init__(
      self,
      bus,
      width: int = 256,
      height: int = 64,
      flip_display: bool = False,
  ):
    self._bus = bus
    # Commands go out one byte at a time, often enough that allocating a
    # buffer for each would be churn the collector does not need.
    self._cmd = bytearray(1)

    self._width = width
    self._height = height
    self._buffer = bytearray(self._width // 2 * self._height)
    self._shadow = bytearray(len(self._buffer))
    self._all_dirty = True

    super().__init__(self._buffer, width, height, framebuf.GS4_HMSB)
    self.fill(0)

    self._init_display(flip_display)

  def _init_display(self, flip_display: bool):
    logging.log(
        'Initializing SSD1322 OLED panel ({}x{}, flip={})...',
        self._width,
        self._height,
        flip_display,
    )
    self._bus.reset()

    # fmt: off
    self.write_cmd(0xFD, 0x12)        # Unlock IC
    self.write_cmd(0xA4)              # Display off (all pixels off)
    self.write_cmd(0xB3, 0x91)        # Display divide clockratio/freq
    self.write_cmd(0xCA, 0x3F)        # Set MUX ratio
    self.write_cmd(0xA2, 0x00)        # Display offset
    self.write_cmd(0xA1, 0x00)        # Display start Line
    arg = 0x06 if flip_display else 0x14
    self.write_cmd(0xA0, arg, 0x11)   # Set remap & dual COM Line
    self.write_cmd(0xB5, 0x00)        # Set GPIO (disabled)
    self.write_cmd(0xAB, 0x01)        # Function select (internal Vdd)
    self.write_cmd(0xB4, 0xA0, 0xFD)  # Display enhancement A (External VSL)
    self.write_cmd(0xC1, 0x7F)        # Set contrast current (default)
    self.write_cmd(0xC7, 0x0F)        # Master contrast (reset)
    self.write_cmd(0xB9)              # Set default greyscale table
    # The three analog settings below are the values this panel was brought up
    # with on the bench, not the ones this project used over SPI, which were
    # never run against it. A VcomH of 0x00 in particular is low enough that a
    # correctly initialised panel can still show nothing at all.
    self.write_cmd(0xB1, 0xE2)        # Phase length
    self.write_cmd(0xD1, 0x82, 0x20)  # Display enhancement B (reset)
    self.write_cmd(0xBB, 0x1F)        # Pre-charge voltage
    self.write_cmd(0xB6, 0x08)        # 2nd precharge period
    self.write_cmd(0xBE, 0x07)        # Set VcomH
    self.write_cmd(0xA6)              # Normal display (reset)
    self.write_cmd(0xA9)              # Exit partial display
    self.write_cmd(0xAF)              # Display on
    # fmt: on

    self.fill(0)
    self.flush()
    logging.log('SSD1322 OLED panel ready and display on.')


  @property
  def width(self) -> int:
    return self._width

  @property
  def height(self) -> int:
    return self._height

  def close(self):
    self.fill(0)
    self.sleep()
    self.write_cmd(0xA4)  # Display off

  def sleep(self):
    self.write_cmd(0xAE)
    self.write_cmd(0xAB, 0x00)

  def write_cmd(self, cmd, *args):
    self._cmd[0] = cmd
    self._bus.write(self._cmd, 0)

    if len(args) > 0:
      self.write_data(bytearray(args))

  def write_data(self, data):
    self._bus.write(data, 1)

  def flush(self):
    """Sends the rows that have changed, and nothing else.

    A whole frame is 8,192 bytes, which at 300ns a byte takes 2.5ms. The panel
    carries on scanning out to the glass while being written, so sending only
    the modified rows (e.g. 9 rows for scrolling text = 1,152 bytes) shrinks
    the tearing collision window by ~86%.
    """
    row_bytes = self._width // 2
    if self._all_dirty:
      first, last = 0, self._height - 1
      self._all_dirty = False
    else:
      changed = _find_changed_rows(
          self._buffer, self._shadow, row_bytes, self._height
      )
      if changed is None:
        return
      first, last = changed

    start = first * row_bytes
    stop = (last + 1) * row_bytes

    offset = (480 - self._width) // 2
    col_start = offset // 4
    col_end = col_start + self.width // 4 - 1
    self.write_cmd(0x15, col_start, col_end)
    self.write_cmd(0x75, first, last)
    self.write_cmd(0x5C)
    self.write_data(self._buffer[start:stop])
    self._shadow[start:stop] = self._buffer[start:stop]
