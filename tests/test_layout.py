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

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

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


if __name__ == '__main__':
  unittest.main()
