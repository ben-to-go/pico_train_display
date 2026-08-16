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
"""Tests for reading the RTT token from the environment.

Run with:
  python3 -m unittest discover -s tests
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import config


class TokenFromEnvironmentTest(unittest.TestCase):

  def setUp(self):
    self._original = os.environ.get('RTT_TOKEN')
    self.addCleanup(self._restore)

  def _restore(self):
    if self._original is None:
      os.environ.pop('RTT_TOKEN', None)
    else:
      os.environ['RTT_TOKEN'] = self._original

  def test_uses_the_token_in_the_config(self):
    os.environ['RTT_TOKEN'] = 'from-environment'
    rtt = config.RttConfig('https://data.rtt.io', 'from-config', 20)
    self.assertEqual('from-config', rtt.token)

  def test_falls_back_to_the_environment(self):
    os.environ['RTT_TOKEN'] = 'from-environment'
    rtt = config.RttConfig('https://data.rtt.io', '', 20)
    self.assertEqual('from-environment', rtt.token)

  def test_no_token_anywhere_is_rejected(self):
    os.environ.pop('RTT_TOKEN', None)
    rtt = config.RttConfig('https://data.rtt.io', '', 20)
    with self.assertRaises(ValueError):
      rtt.validate()


class ScrollSpeedTest(unittest.TestCase):
  """Scroll speed is its own setting, in pixels a second."""

  def _display(self, **kwargs):
    settings = {'refresh': 60}
    settings.update(kwargs)
    return config.DisplayConfig(**settings)

  def test_defaults_when_not_configured(self):
    self.assertEqual(60, self._display().scroll_speed)

  def test_is_independent_of_the_refresh_rate(self):
    slow = self._display(refresh=10, scroll_speed=90)
    fast = self._display(refresh=60, scroll_speed=90)
    self.assertEqual(slow.scroll_speed, fast.scroll_speed)

  def test_must_be_positive(self):
    for speed in (0, -30):
      with self.subTest(scroll_speed=speed):
        with self.assertRaises(ValueError):
          self._display(scroll_speed=speed).validate()

  def test_a_valid_pair_passes(self):
    self._display(refresh=60, scroll_speed=45).validate()


if __name__ == '__main__':
  unittest.main()
