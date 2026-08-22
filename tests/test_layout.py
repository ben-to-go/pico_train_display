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
"""Tests for how the rows are spread down the screen.

Run with:
  python3 -m unittest discover -s tests
"""

import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import firmware_path  # noqa: E402,F401  see its docstring

# widgets imports display and fonts, which need framebuf and so cannot be
# imported off-device. _distribute is plain arithmetic and needs neither, but
# the annotations around it are evaluated at import, so the names must exist.
# Another test may have stubbed some of these already, so fill in what is
# missing rather than replacing what is there.
for name, attrs in (
    ('framebuf', ()),
    ('display', ('Display',)),
    ('fonts', ('Font',)),
    ('trains', ('Departure', 'DepartureUpdater')),
):
  module = sys.modules.setdefault(name, types.ModuleType(name))
  for attr in attrs:
    if not hasattr(module, attr):
      setattr(module, attr, type(attr, (), {}))

import widgets


class DistributeTest(unittest.TestCase):

  def test_gap_above_the_first_matches_the_gap_below_the_last(self):
    # Four rows of nine on a 64 pixel panel, as the display uses.
    height, rows = 64, (9, 9, 9, 9)
    tops = widgets._distribute(height, rows)

    above = tops[0]
    below = height - (tops[-1] + rows[-1])
    self.assertLessEqual(abs(above - below), 1, '{} vs {}'.format(above, below))

  def test_rows_are_evenly_spaced(self):
    rows = (9, 9, 9, 9)
    tops = widgets._distribute(64, rows)

    gaps = [tops[i + 1] - (tops[i] + rows[i]) for i in range(len(rows) - 1)]
    self.assertLessEqual(max(gaps) - min(gaps), 1, gaps)

  def test_rows_do_not_overlap_or_run_off_the_screen(self):
    rows = (9, 9, 9, 9)
    tops = widgets._distribute(64, rows)

    for i in range(len(rows) - 1):
      self.assertLess(tops[i] + rows[i], tops[i + 1])
    self.assertLessEqual(tops[-1] + rows[-1], 64)

  def test_copes_with_rows_of_different_heights(self):
    rows = (9, 9, 9, 18)
    tops = widgets._distribute(64, rows)

    self.assertEqual(len(rows), len(tops))
    self.assertLessEqual(tops[-1] + rows[-1], 64)


class ClockTest(unittest.TestCase):
  """The clock as the real board draws it: spaced colons, smaller seconds.

  The stub fonts are proportional in the same way the real ones are - a '1'
  narrower than the rest - because that is what the fixed cell per digit is
  for. With uniform widths none of this would be visible.
  """

  class _Screen:

    def __init__(self):
      self.rects = []

    def fill_rect(self, x, y, w, h, colour):
      self.rects.append((x, y, w, h, colour))

  class _Font:

    def __init__(self, widths):
      self.widths = widths
      self.drawn = []

    def calculate_bounds(self, text):
      return sum(self.widths.get(c, self.widths['0']) for c in text), 9

    def render_text(self, text, _screen, x, y):
      self.drawn.append((text, x, y))

  def _render(self):
    screen = self._Screen()
    tall = self._Font({'0': 9, '1': 5})
    small = self._Font({'0': 5, '1': 4})
    clock = widgets.ClockWidget(screen, tall, small)
    clock.render((0, 0, 0, 12, 34, 56), 0, 0, *clock.bounds())
    return screen, tall, small, clock

  def test_the_colon_dots_are_further_apart_than_the_fonts(self):
    screen, _, _, _ = self._render()
    # The font's colon has its dots at rows 2 and 5. Anything wider than that
    # three-row pitch is the spacing this is here for.
    dots = sorted({y for _, y, w, h, c in screen.rects if c and w == 2})
    self.assertEqual(2, len(dots))
    self.assertGreater(dots[1] - dots[0], 3)

  def test_every_digit_gets_the_same_cell_and_sits_in_the_middle_of_it(self):
    _, tall, _, _ = self._render()

    # Nine apart whatever the digit, and the narrow 1 inset by two rather than
    # left against the digit before it.
    self.assertEqual([('1', 2), ('2', 9), ('3', 21), ('4', 30)],
                     [(text, x) for text, x, _ in tall.drawn])

  def test_the_seconds_use_the_smaller_font_on_the_same_bottom_edge(self):
    _, _, small, _ = self._render()

    # Two rows down, so its shorter digits end level with the tall ones, and
    # on the smaller font's own five-pixel cell.
    self.assertEqual([('5', 42, 2), ('6', 47, 2)], small.drawn)

  def test_the_clock_is_the_same_width_whatever_it_reads(self):
    _, _, _, clock = self._render()
    self.assertEqual(52, clock.bounds()[0])


class ScrollingTextWidgetTest(unittest.TestCase):

  class _Screen:
    def __init__(self):
      self.rects = []
    def fill_rect(self, x, y, w, h, colour):
      self.rects.append((x, y, w, h, colour))

  class _Font:
    def calculate_bounds(self, text):
      return len(text) * 6, 9
    def render_text(self, text, _screen, x, y):
      pass

  def test_static_text_renders_once_and_skips_subsequent_frames(self):
    screen = self._Screen()
    font = self._Font()
    widget = widgets.ScrollingTextWidget(screen, font, label='Label: ')
    widget.set_text('Short')

    # First render returns True
    self.assertTrue(widget.render(0, 0, 200, 9))
    # Subsequent render without text change returns False (zero bus write / zero flicker)
    self.assertFalse(widget.render(0, 0, 200, 9))

  def test_scrolling_text_skips_frames_until_pixel_advances(self):
    screen = self._Screen()
    font = self._Font()
    # 12 px/sec
    widget = widgets.ScrollingTextWidget(screen, font, label='Calling at: ', pixels_per_second=12)
    widget.set_text('A very long station list that definitely overflows the narrow window width')

    # First render draws initial frame
    self.assertTrue(widget.render(0, 0, 50, 9))

    # Immediate next frame (<1ms elapsed, scroll did not change integer pixel) -> returns False
    self.assertFalse(widget.render(0, 0, 50, 9))

    # Simulate advancing time by 100ms (12 px/s * 0.1s = 1.2px) -> returns True
    widget._scrolled_at -= 100
    self.assertTrue(widget.render(0, 0, 50, 9))


if __name__ == '__main__':
  unittest.main()
