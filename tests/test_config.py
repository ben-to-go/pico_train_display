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
import types
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

# Stand-ins for what only exists on the device. display subclasses
# framebuf.FrameBuffer, so it cannot be imported here at all; only displays()
# is needed to validate a config. time_range uses micropython.const.
_display = types.ModuleType('display')
_display.displays = lambda: {'epd29b', 'ssd1322'}
sys.modules.setdefault('display', _display)

_micropython = types.ModuleType('micropython')
_micropython.const = lambda value: value
sys.modules.setdefault('micropython', _micropython)

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


if __name__ == '__main__':
  unittest.main()
