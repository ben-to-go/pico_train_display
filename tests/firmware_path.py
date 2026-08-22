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
"""What a desktop needs arranging before it can import a firmware module.

Imported first by every test that imports one, because each of these has to
happen before the firmware is imported rather than after.

  src/ on the path, which is where the firmware lives.

  src/logging.py in front of the standard library's. unittest imports that one
  before any test runs, so a firmware module doing `import logging` binds it
  instead, and logging.log('{} services', n) reaches a function whose first
  argument is a level and raises "level must be an integer". Which of the two
  won used to depend on whichever test was imported first, so the suite could
  pass while a single test file run on its own failed.

  sys.print_exception, which MicroPython has and CPython does not.
"""

import os
import sys

try:
  import asyncio  # noqa: F401
except ImportError:
  pass

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

# Imported here rather than merely uncached, so that it is this one every
# firmware module imported afterwards is given.
sys.modules.pop('logging', None)
import logging  # noqa: E402,F401  pylint: disable=unused-import

if not hasattr(sys, 'print_exception'):

  def _print_exception(e, file=None):
    print('Traceback (most recent call last):\n  {!r}'.format(e), file=file)

  sys.print_exception = _print_exception

import builtins

class _MockPtr:
  def __init__(self, addr=None):
    self.addr = addr
  def __getitem__(self, idx):
    return 0
  def __setitem__(self, idx, val):
    pass

if not hasattr(builtins, 'ptr8'):
  builtins.ptr8 = _MockPtr
if not hasattr(builtins, 'ptr32'):
  builtins.ptr32 = _MockPtr

import time

if not hasattr(time, 'ticks_us'):
  time.ticks_us = lambda: int(time.time() * 1_000_000)
if not hasattr(time, 'ticks_ms'):
  time.ticks_ms = lambda: int(time.time() * 1_000)
if not hasattr(time, 'ticks_diff'):
  time.ticks_diff = lambda a, b: a - b
if not hasattr(time, 'sleep_ms'):
  time.sleep_ms = lambda ms: time.sleep(ms / 1000.0)
if not hasattr(time, 'sleep_us'):
  time.sleep_us = lambda us: time.sleep(us / 1_000_000.0)
