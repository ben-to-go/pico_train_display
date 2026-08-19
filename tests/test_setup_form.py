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
"""The setup page is the only way to write config.json, so it has to offer
every setting the config understands. Nothing else checks that: the page is a
hand-written HTML file and the schema is Python, and a setting added to one
and not the other simply becomes unreachable.

Run with:
  python3 -m unittest discover -s tests
"""

import inspect
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import firmware_path  # noqa: E402,F401  see its docstring

import config

_SETUP_HTML = os.path.join(
    os.path.dirname(__file__), '..', 'assets', 'setup.html'
)

# <input name="display[flip]:bool"> -> the form key, without the type suffix
# the server strips. Mirrors setup/server.py's own key parsing. Inputs only,
# so the page's own <meta name="viewport"> is not mistaken for a setting.
_FIELD = re.compile(r'<input\b[^>]*\bname="(\w+)(?:\[(\w+)\])?(?::\w+)?"')


def _form_fields() -> set[str]:
  with open(_SETUP_HTML) as f:
    html = f.read()
  return {
      '{}[{}]'.format(key, sub) if sub else key
      for key, sub in _FIELD.findall(html)
  }


def _settings_of(cls, prefix: str = '') -> set[str]:
  """Every setting the class takes, sub-configs flattened into form keys."""
  fields = set()
  for name in inspect.signature(cls).parameters:
    nested = getattr(config, '{}Config'.format(name.capitalize()), None)
    if nested is not None:
      fields |= _settings_of(nested, prefix='{}'.format(name))
    elif prefix:
      fields.add('{}[{}]'.format(prefix, name))
    else:
      fields.add(name)
  return fields


class SetupFormTest(unittest.TestCase):

  def test_offers_every_setting_the_config_understands(self):
    # A setting in config.py with no input on the page can only be set by
    # editing config.json over USB, which defeats the point of the portal.
    missing = _settings_of(config.Config) - _form_fields()
    self.assertEqual(set(), missing)

  def test_does_not_offer_settings_that_no_longer_exist(self):
    # The other direction, which is how the display type and the fast train
    # options lingered on the page after being removed from the firmware:
    # posting one now raises rather than being quietly ignored.
    extra = _form_fields() - _settings_of(config.Config)
    self.assertEqual(set(), extra)
