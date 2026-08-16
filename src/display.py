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
"""Base class and factory for creating displays instances."""

import framebuf
import machine


_DEFAULT_DISPLAY = 'ssd1322'


# TODO: Make this a proper ABC when Micropython supports abc module.
class Display(framebuf.FrameBuffer):
  """Base class for displays."""

  @property
  def width(self) -> int:
    """Width in pixels of the display."""
    ...

  @property
  def height(self) -> int:
    """Height in pixels of the display."""
    ...

  def flush(self) -> None:
    """Flushes frame buffer to the display."""
    ...

  def close(self) -> None:
    """Clears and closes the display."""
    ...

  def sleep(self) -> None:
    """Puts display to sleep."""
    ...

  def awake(self) -> None:
    """Wakes up a display."""
    ...


def displays():
  return {_DEFAULT_DISPLAY}


def create(name: str = _DEFAULT_DISPLAY, flip_display: bool = False):
  """Factory function to create display."""
  if name.lower() != _DEFAULT_DISPLAY:
    raise ValueError('Unrecognized display "{}"!'.format(name))

  import ssd1322

  spi = machine.SPI(
      0, baudrate=8_000_000, sck=machine.Pin(18), mosi=machine.Pin(19)
  )
  return ssd1322.SSD1322(
      spi,
      dc=machine.Pin(20),
      cs=machine.Pin(17),
      rst=machine.Pin(21),
      flip_display=flip_display,
  )
