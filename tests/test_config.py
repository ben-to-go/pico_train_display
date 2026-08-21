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
"""Tests for the three places a token can come from.

config.json, the environment, and the firmware itself, in that order. The last
is what lets a display be given away: whoever unwraps it fills in the wifi and
leaves the token fields blank.

Run with:
  python3 -m unittest discover -s tests
"""

import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import firmware_path  # noqa: E402,F401  see its docstring

import config

# No module frozen in at all, as opposed to one holding an empty string. Both
# happen: the second is what a build with no .env writes.
_NOT_BAKED = None


class TokenSourceTest(unittest.TestCase):

  def setUp(self):
    # Two of the three are ambient, so a test that reads one it did not set
    # would pass or fail by what the machine happens to have.
    for name in ('RTT_TOKEN', 'OTEL_EXPORTER_OTLP_HEADERS'):
      original = os.environ.pop(name, None)
      if original is not None:
        self.addCleanup(os.environ.__setitem__, name, original)
    self.addCleanup(sys.modules.pop, 'baked', None)

  def _bake(self, **values):
    """What the frozen module holds, or nothing frozen in if given nothing.

    Into sys.modules, because config.py imports it inside the function that
    reads it. None is what makes that import raise, as it does on a board
    built without the module.
    """
    module = types.ModuleType('baked')
    for name, value in values.items():
      setattr(module, name, value)
    sys.modules['baked'] = module if values else _NOT_BAKED

  def test_the_rtt_token_comes_from_the_first_of_the_three_to_have_one(self):
    # config.json, environment, firmware -> the token used. The config winning
    # is how a revoked one gets replaced without a rebuild; an empty baked one
    # behaving as absent is every build with no .env, CI included.
    cases = (
        ('from-config', 'from-env', 'from-fw', 'from-config'),
        ('', 'from-env', 'from-fw', 'from-env'),
        ('', None, 'from-fw', 'from-fw'),
        ('', None, '', None),
        ('', None, _NOT_BAKED, None),
    )
    for in_config, in_env, baked, token in cases:
      with self.subTest(config=in_config, env=in_env, baked=baked):
        if in_env is None:
          os.environ.pop('RTT_TOKEN', None)
        else:
          os.environ['RTT_TOKEN'] = in_env
        self._bake() if baked is _NOT_BAKED else self._bake(RTT_TOKEN=baked)

        rtt = config.RttConfig('https://data.rtt.io', in_config, 20)
        self.assertEqual(token, rtt.token)
        if token is None:
          with self.assertRaises(ValueError):
            rtt.validate()
        else:
          rtt.validate()

  def test_the_collector_token_is_read_out_of_a_baked_grafana_header(self):
    # Baked as the whole OTEL_EXPORTER_OTLP_HEADERS value, leaving config.py
    # the only reader of what one means. baked, configured -> the header sent.
    cases = (
        ('Authorization=Basic%20abc', '', 'Basic abc'),
        ('X-Scope-OrgID=42,Authorization=Basic%20abc', '', 'Basic abc'),
        ('Authorization=Basic%20baked', 'Basic typed', 'Basic typed'),
        ('X-Scope-OrgID=42', '', ''),
        ('', '', ''),
        (_NOT_BAKED, '', ''),
    )
    for baked, configured, auth in cases:
      with self.subTest(baked=baked, configured=configured):
        self._bake() if baked is _NOT_BAKED else self._bake(OTEL_HEADERS=baked)

        otel = config.OtelConfig(auth=configured)
        self.assertEqual(auth, otel.auth)
        self.assertEqual(bool(auth), otel.enabled)


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


class UnreadableConfigTest(unittest.TestCase):
  """What a config this firmware cannot use raises.

  main() catches these and shows the setup screen, so a board that has had a
  setting removed underneath it asks to be set up again rather than resetting
  in a loop. The types are the contract between the two.
  """

  _CAUGHT_BY_MAIN = (OSError, ValueError, TypeError)

  def _assert_caught(self, cfg):
    with self.assertRaises(self._CAUGHT_BY_MAIN):
      config.load(cfg)

  def _valid(self, **display):
    settings = {'refresh': 60, 'flip': False, 'scroll_speed': 15}
    settings.update(display)
    return {
        'station': 'SKM',
        'destination': 'MYB',
        'wifi': {'ssid': 'x', 'password': 'y'},
        'rtt': {'endpoint': 'https://data.rtt.io', 'token': 't',
                'update_interval': 120},
        'display': settings,
        'debug': {'log': False},
    }

  def test_a_setting_this_firmware_no_longer_has(self):
    # What an old config looks like after a setting is dropped.
    self._assert_caught(self._valid(some_removed_setting=''))

  def test_a_value_out_of_range(self):
    self._assert_caught(self._valid(refresh=0))

  def test_a_missing_section(self):
    cfg = self._valid()
    del cfg['rtt']
    self._assert_caught(cfg)

  def test_the_config_we_ship_still_loads(self):
    # The other direction: none of the above should catch a good one.
    config.load(self._valid())
