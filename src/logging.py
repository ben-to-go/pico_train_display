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
"""Simple logging library that both logs to screen and file."""

import io
import os
import sys
import time

_logging_file = None
_sink = None

# What a line is. The sink passes these on to wherever it ships them.
INFO = 'INFO'
ERROR = 'ERROR'


def set_logging_file(path: str):
  # Open file in append mode so that we accumulate logs.
  global _logging_file
  _logging_file = open(path, 'a')
  os.dupterm(_logging_file)


def set_sink(sink):
  """Also hands every line to sink, which ships them off the display.

  A sink rather than os.dupterm, which would be the obvious way to catch
  everything printed: the RP2040 has one dupterm slot and set_logging_file()
  already wants it, and the simulator's MicroPython has no dupterm at all.
  Tapping the log here works the same on both, and on the simulator it taps
  the log rather than the panel, which is what stdout is over there.
  """
  global _sink
  _sink = sink


def _write(msg: str):
  """Where a log line ends up. The simulator replaces this."""
  print(msg)


def _format_logfmt(**kwargs) -> str:
  parts = []
  for k, v in kwargs.items():
    if isinstance(v, str) and (' ' in v or '"' in v or '=' in v):
      v = '"' + v.replace('"', '\\"') + '"'
    parts.append(f'{k}={v}')
  return ' '.join(parts)


def _log_message(prefix: str, msg, *args, severity: str = INFO, **kwargs):
  msg = str(msg)
  if args:
    try:
      msg = msg.format(*args)
    except (IndexError, ValueError):
      pass

  if kwargs:
    if '{' in msg:
      try:
        msg = msg.format(**kwargs)
      except (KeyError, IndexError, ValueError):
        pairs = _format_logfmt(**kwargs)
        msg = f'{msg} {pairs}' if pairs else msg
    else:
      pairs = _format_logfmt(**kwargs)
      msg = f'{msg} {pairs}' if pairs else msg

  _write('{} {}'.format(prefix, msg))
  if _sink is not None:
    # Without the prefix. Whatever the line is shipped to stamps it, and a
    # board that has not reached NTP yet would be putting 00:00:00 next to
    # a perfectly good timestamp from the other end.
    _sink.write(msg, severity)


def log(msg, *args, **kwargs):
  now = time.localtime()
  prefix = '[{:0>2}:{:0>2}:{:0>2}]'.format(now[3], now[4], now[5])
  _log_message(prefix, msg, *args, severity=INFO, **kwargs)


def error(msg, *args, **kwargs):
  now = time.localtime()
  prefix = '[{:0>2}:{:0>2}:{:0>2}]'.format(now[3], now[4], now[5])
  _log_message(prefix, msg, *args, severity=ERROR, **kwargs)


def exception(e):
  """Logs a traceback.

  Replaces bare sys.print_exception() calls, which write straight to stdout
  and so are invisible to both the sink and the simulator's log file. A
  traceback is the most useful thing a display ever has to say.
  """
  buffer = io.StringIO()
  sys.print_exception(e, buffer)
  traceback = buffer.getvalue().rstrip() or repr(e)
  _write(traceback)
  if _sink is not None:
    _sink.write(traceback, ERROR)


def on_exit():
  if _logging_file is not None:
    os.dupterm(None)
    _logging_file.flush()
    _logging_file.close()
