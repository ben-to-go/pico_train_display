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
"""Time subsystem: bounded NTP time synchronization."""

import time

try:
  import ntptime
except ImportError:
  ntptime = None


import logging

_DEFAULT_TIMEOUT = 15


def sync_time(timeout: int = _DEFAULT_TIMEOUT) -> bool:
  """Sets the hardware real-time clock from NTP with a bounded retry window."""
  logging.log('Configure datetime.')
  last_error = None
  for _ in range(timeout):
    try:
      if ntptime is not None:
        ntptime.settime()
      t = time.localtime()
      logging.log('Time set to UTC {}/{}/{} {}:{}', *t[:5])
      return True
    except Exception as e:
      last_error = e
      time.sleep(1)

  logging.log('Failed to reach NTP in {} secs: {}', timeout, last_error)
  if last_error is not None:
    logging.exception(last_error)
  return False
