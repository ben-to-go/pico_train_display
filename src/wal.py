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
"""Write-ahead log for persisting unsent telemetry lines across device reboots.

When the network is unreachable or the device is rebooting, unsent log lines
are spooled to flash so crash context and outage causes survive resets. On the
next boot, persisted records are loaded back into the telemetry sink and
shipped as soon as connectivity is restored.
"""

import json
import os

DEFAULT_PATH = 'otel_wal.json'


def save(entries: list, path: str = DEFAULT_PATH):
  """Saves a list of log entry tuples to disk."""
  if not entries:
    clear(path)
    return

  tmp_path = path + '.tmp'
  try:
    with open(tmp_path, 'w') as f:
      json.dump(entries, f)
    try:
      os.rename(tmp_path, path)
    except OSError:
      try:
        os.remove(path)
      except OSError:
        pass
      os.rename(tmp_path, path)
  except OSError:
    # If flash is unwritable, read-only, or out of space, ignore cleanly.
    try:
      os.remove(tmp_path)
    except OSError:
      pass


def _exists(path: str) -> bool:
  try:
    os.stat(path)
    return True
  except OSError:
    return False


def load(path: str = DEFAULT_PATH) -> list:
  """Loads persisted log entries from disk. Returns [] on missing or invalid.

  Automatically purges any unreadable, corrupted, or leftover temporary files.
  """
  # Clean up any leftover temporary file from a previously interrupted write
  clear(path + '.tmp')

  if not _exists(path):
    return []

  try:
    with open(path, 'r') as f:
      data = json.load(f)
      if isinstance(data, list):
        return data
  except (OSError, ValueError):
    pass

  # If the file exists but was corrupted or not a list, remove it from flash
  clear(path)
  return []


def clear(path: str = DEFAULT_PATH):
  """Deletes the persisted log file and any leftover temporary file."""
  for p in (path, path + '.tmp'):
    try:
      os.remove(p)
    except OSError:
      pass
