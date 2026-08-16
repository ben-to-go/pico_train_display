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
"""The panel emulator has to put a partial update where it was addressed.

The driver sends only the rows that changed, so most writes cover a band in
the middle of the screen rather than the whole of it. Row addresses on the
SSD1322 are absolute, and an emulator that treats them as relative to the
window draws every one of those bands at the top instead, which looks like the
driver is broken when it is not.

Run with:
  python3 -m unittest discover -s tests
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'sim'))

import panel as panel_module


# The visible 256 pixels sit in the middle of the 480 the chip can address, so
# the driver addresses columns 28 to 91. Four pixels to a column.
_COL_START = 28
_COL_END = 91


def _panel():
  # on_frame stops it drawing to the terminal, which also keeps it off
  # time.ticks_ms, which a desktop does not have.
  return panel_module.Panel(on_frame=lambda _: None)


def _write_rows(p, first, last, value):
  """Fills rows first..last with a greyscale value, as the driver would."""
  p.write(bytes([0x15]), 0)
  p.write(bytes([_COL_START, _COL_END]), 1)
  p.write(bytes([0x75]), 0)
  p.write(bytes([first, last]), 1)
  p.write(bytes([0x5C]), 0)
  nibble = (value << 4) | value
  p.write(bytes([nibble]) * (128 * (last - first + 1)), 1)


def _rows_lit(p):
  return {y for y in range(p.height)
          if any(p.pixels[y * p.width + x] for x in range(p.width))}


class PartialUpdateTest(unittest.TestCase):

  def test_a_band_lands_where_it_was_addressed(self):
    # The calling points row, which is what most flushes send.
    p = _panel()
    _write_rows(p, 20, 28, 15)

    self.assertEqual(set(range(20, 29)), _rows_lit(p))

  def test_the_clock_row_lands_at_the_bottom(self):
    p = _panel()
    _write_rows(p, 49, 57, 15)

    self.assertEqual(set(range(49, 58)), _rows_lit(p))

  def test_a_second_band_leaves_the_first_alone(self):
    # Two flushes in a row must not overwrite each other, which is the whole
    # point of only sending what changed.
    p = _panel()
    _write_rows(p, 20, 28, 15)
    _write_rows(p, 49, 57, 15)

    self.assertEqual(set(range(20, 29)) | set(range(49, 58)), _rows_lit(p))

  def test_a_full_frame_still_covers_everything(self):
    p = _panel()
    _write_rows(p, 0, 63, 15)

    self.assertEqual(set(range(64)), _rows_lit(p))

  def test_the_pixels_are_where_they_should_be_across_the_width(self):
    # Columns stay relative, because the window is the middle of the panel.
    p = _panel()
    _write_rows(p, 20, 20, 15)

    self.assertTrue(all(p.pixels[20 * p.width + x] == 15
                        for x in range(p.width)))


if __name__ == '__main__':
  unittest.main()
