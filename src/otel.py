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
"""Ships the log to an OTLP collector, as well as to stdout.

A log is only useful where someone can read it, and nobody is plugged into a
train display. Every line the firmware logs goes to an OpenTelemetry collector
as well as to stdout, so what a display said before it went quiet can be read
from somewhere else.

Lines are buffered as they are logged and sent in batches by send(), called
from the main loop. Logging happens on both cores and inside every failure
path, so it must stay cheap and must never itself go near the network; only
send() does that, and it is the same shape of request the departures already
make.

The collector is Grafana Cloud's OTLP gateway, which takes JSON-encoded
protobuf as well as the binary sort. JSON costs a little bandwidth, which this
board has, and saves a protobuf encoder, which it does not. The traffic is a
few lines a minute.
"""

import json
import os
import sys
import time

import logging
import trains
import wal


# The board counts seconds from 2000 and a desktop from 1970. OTLP wants the
# latter from both.
_EPOCH_OFFSET = 946684800 if time.gmtime(0)[0] == 2000 else 0

# A clock reading earlier than this is one NTP has not reached yet, showing
# whenever the port thinks it booted. Collectors drop stamps that old.
_CLOCK_IS_SET = 1735689600  # 2025-01-01, before any real boot.

# How many lines are held between sends, and so how much a display that cannot
# reach the collector remembers. A boot and a few failures over.
_MAX_LINES = 64

# Short, for the same reason the departures request is short: the display has
# a board to draw, and this is the least important thing it does.
_TIMEOUT = 5

# What the log shows up as at the collector, for every display that sends one.
# Deliberately not a setting: a name that can be changed per board is a name
# that has to be searched for, and there is nothing here worth telling apart
# that the log lines do not already say.
SERVICE_NAME = 'pico-train-display'


def _environment(platform: str) -> str:
  """Which of the two things this is running on.

  The board reports rp2 and the simulator reports whichever desktop it is on,
  which is the only difference between them that a log line cannot show. It
  matters because the two send the same lines to the same place: without this,
  a departure board being debugged on a laptop is indistinguishable from the
  one on the wall.
  """
  return DEVICE if platform == 'rp2' else SIMULATOR


DEVICE = 'device'
SIMULATOR = 'simulator'

# Grafana promotes this one to a Loki label, so it can be selected on rather
# than filtered for: {service_name="pico-train-display", environment="device"}.
_ENVIRONMENT_KEY = 'deployment.environment.name'
ENVIRONMENT = _environment(sys.platform)


def _new_run_id() -> str:
  """Eight hex characters, different every time the firmware starts.

  A display has no clock until NTP answers and no name of its own, so there is
  nothing about a board that says which of its runs a line came from. Every
  boot looks like the one before it, and a board that reset in the night is
  indistinguishable in the log from one that ran through.

  From urandom rather than the clock, which reads the same at every boot until
  NTP has been, or machine.unique_id(), which is the board and so is the same
  across a power cut. Hex by hand because it is four bytes and that is cheaper
  than a module.
  """
  return ''.join('{:02x}'.format(byte) for byte in os.urandom(4))


# Which run of the firmware this is. Grafana promotes it to a Loki label too,
# so one boot can be selected out of a week of them. It is fixed for as long
# as the board is powered, and a new one after a reset, whether that reset was
# the plug or the firmware giving up: both end a run.
_RUN_KEY = 'service.instance.id'
RUN_ID = _new_run_id()

# Severity numbers from the OTLP spec, so that a collector can filter on them.
# The names themselves belong to logging, which is what decides them.
_SEVERITY_NUMBER = {logging.INFO: 9, logging.ERROR: 17}

_sink = None


def _payload(lines, now: int) -> str:
  """A batch of (when, severity, line, [run_id]) as OTLP/JSON.

  Lines logged before NTP answered carry a reading from whenever the port
  thinks it booted, which a collector drops as far too old: a Pico has no
  clock across a power cut, so it starts at zero and counts. The spacing
  between them is still right, so they are anchored to end where the first
  real reading begins, and the boot lands just before the line that set the
  clock, in the order it happened. Whichever epoch the port booted at cancels
  out of the subtraction.
  """
  by_run = {}
  for entry in lines:
    when, severity, line = entry[0], entry[1], entry[2]
    run_id = entry[3] if len(entry) > 3 else RUN_ID
    by_run.setdefault(run_id, []).append((when, severity, line))

  resource_logs = []
  for run_id, run_lines in by_run.items():
    before = [when for when, _, _ in run_lines if when < _CLOCK_IS_SET]
    real = [when for when, _, _ in run_lines if when >= _CLOCK_IS_SET]
    anchor = 0
    if before:
      anchor = (min(real) if real else now) - max(before)

    records = [
        {
            'timeUnixNano': '{}000000000'.format(
                when if when >= _CLOCK_IS_SET else when + anchor
            ),
            'severityText': severity,
            'severityNumber': _SEVERITY_NUMBER[severity],
            'body': {'stringValue': line},
        }
        for when, severity, line in run_lines
    ]
    resource_logs.append({
        'resource': {
            'attributes': [
                {
                    'key': 'service.name',
                    'value': {'stringValue': SERVICE_NAME},
                },
                {
                    'key': _ENVIRONMENT_KEY,
                    'value': {'stringValue': ENVIRONMENT},
                },
                {
                    'key': _RUN_KEY,
                    'value': {'stringValue': run_id},
                },
            ],
        },
        'scopeLogs': [{'logRecords': records}],
    })

  return json.dumps({'resourceLogs': resource_logs})


class Sink:
  """Holds recent log lines and sends them to the collector in batches."""

  def __init__(self, endpoint: str, auth: str, wal_path: str = wal.DEFAULT_PATH):
    self._url = endpoint.rstrip('/') + '/v1/logs'
    self._auth = auth
    self._wal_path = wal_path
    self._lines = []
    self._failed = False

    # Load un-shipped logs persisted across reboots
    persisted = wal.load(self._wal_path)
    for item in persisted:
      if isinstance(item, (list, tuple)) and len(item) >= 3:
        run_id = item[3] if len(item) > 3 else RUN_ID
        self._lines.append((item[0], item[1], item[2], run_id))
    if len(self._lines) > _MAX_LINES:
      self._lines = self._lines[-_MAX_LINES:]

  def write(self, text: str, severity: str = logging.INFO):
    """Buffers a log line. Called from whichever core did the logging.

    Only ever appends, because this runs inside logging.log() and so inside
    every failure path in the firmware. When it is full the oldest line goes:
    for a display that has been unable to reach the collector for a week, what
    finally went wrong is in the last few lines, not the first.
    """
    when = int(time.time()) + _EPOCH_OFFSET
    for line in text.split('\n'):
      line = line.rstrip()
      if not line:
        continue
      if len(self._lines) >= _MAX_LINES:
        self._lines.pop(0)
      self._lines.append((when, severity, line, RUN_ID))

  def send(self) -> bool:
    """Sends what has been logged since the last call. Never raises.

    A failure keeps its lines for the next go, so a collector that cannot be
    reached costs nothing but the delay, and says so once rather than once a
    try: a line per attempt would be most of the log.
    """
    if not self._lines:
      return True

    # int(), because time.time() is whole seconds on the board and a float on
    # a desktop, and these are formatted as integers.
    now = int(time.time()) + _EPOCH_OFFSET
    if now < _CLOCK_IS_SET:
      # NTP has not answered yet, so nothing here can be stamped with a time
      # the collector would accept. The boot is the most worth keeping of
      # anything a display logs, so it waits rather than being thrown away.
      return True

    lines, self._lines = self._lines, []
    try:
      self._post(_payload(lines, now))
    except Exception as e:
      # Not worth interrupting the departures for, let alone resetting over.
      self._lines = (lines + self._lines)[-_MAX_LINES:]
      wal.save(self._lines, self._wal_path)
      if not self._failed:
        self._failed = True
        logging.log('Could not reach the log collector: {}', e)
      return False

    wal.clear(self._wal_path)
    self._failed = False
    return True

  def flush_wal(self):
    """Saves unsent lines to disk on shutdown so they survive resets."""
    if self._lines:
      wal.save(self._lines, self._wal_path)

  def _post(self, payload: str):
    response = trains.http_request(
        self._url,
        method='POST',
        body=payload,
        headers={
            'Content-Type': 'application/json',
            'Authorization': self._auth,
        },
        timeout=_TIMEOUT,
    )
    if not 200 <= response.status_code <= 299:
      raise ValueError('Collector rejected the batch! {} {}'.format(
          response.status_code, response.content))


def install(config) -> Sink | None:
  """Starts shipping the log, if there is somewhere to ship it to.

  Everything logged from here on is buffered, including everything logged
  while the wifi is still coming up, so a display that takes a while to
  connect still explains itself once it has.
  """
  global _sink
  if not config.enabled:
    return None

  _sink = Sink(config.endpoint, config.auth)
  logging.set_sink(_sink)
  logging.log('Shipping the log to {} as run {}', config.endpoint, RUN_ID)
  return _sink


def send():
  """Sends whatever has been logged since the last call.

  A no-op when no collector is configured, so the main loop does not have to
  know whether there is one.
  """
  if _sink is not None:
    _sink.send()


def flush_wal():
  """Flushes unsent logs to disk on shutdown. Safe no-op if disabled."""
  if _sink is not None:
    _sink.flush_wal()
