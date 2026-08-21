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
"""The setup portal, and how much of it a display needs to ask about."""

import config

# The section of the page holding everything that is not the wifi.
ADVANCED = 'advanced'

_OPENER = '<script>document.getElementById("{}").open=true</script>'


def open_advanced() -> bytes:
  """Script opening that section, on a firmware with no token of its own.

  A display given away as a present asks for the wifi and nothing else: the
  rest is defaulted or was built in, and sits collapsed out of the way. An API
  token is the one thing on the page with no usable default, so a firmware
  without one opens the section rather than hiding that field inside it.

  Here rather than in server.py so it can be read without asyncio: the
  standard library's asyncio wants the standard library's logging, and the
  tests run with src/logging.py in its place.
  """
  if config.from_firmware('RTT_TOKEN'):
    return b''
  return _OPENER.format(ADVANCED).encode()
