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
"""What reaches the log collector, and what happens when it cannot be reached.

The display keeps running either way: somewhere to read the log from is not
something the departures depend on. What these check is that everything the
firmware logs ends up in a batch, that the batch says what a collector needs
it to say, and that a collector which is down costs lines nothing.

test_otel_integration.py sends one of these batches over a real socket.

Run with:
  python3 -m unittest discover -s tests
"""

import json
import os
import re
import sys
import time
import unittest

_ROOT = os.path.join(os.path.dirname(__file__), '..')
_SRC = os.path.join(_ROOT, 'src')
sys.path.insert(0, _SRC)

# src/logging.py shadows the standard library's, which unittest has imported
# by now. Put back in tearDownModule, for whatever runs after this.
_REPLACED_LOGGING = sys.modules.pop('logging', None)

import config  # noqa: E402
import logging  # noqa: E402
import otel  # noqa: E402
import trains  # noqa: E402

# MicroPython's sys has this and CPython's does not.
if not hasattr(sys, 'print_exception'):

  def _print_exception(e, file=None):
    print('Traceback (most recent call last):\n  {!r}'.format(e), file=file)

  sys.print_exception = _print_exception


def tearDownModule():
  sys.modules.pop('logging', None)
  if _REPLACED_LOGGING is not None:
    sys.modules['logging'] = _REPLACED_LOGGING


# A clock NTP has been to. A float, as time.time() is on a desktop and in the
# simulator; the board deals in whole seconds and would not have caught the
# difference.
_NOW = 1755000000.75


class _Collector:
  """Stands in for the collector, and remembers what it was sent."""

  def __init__(self, status=200, error=None):
    self.status = status
    self.error = error
    self.batches = []

  def __call__(self, url, **kwargs):
    if self.error is not None:
      raise self.error
    self.batches.append((url, kwargs))
    return trains.Response(self.status, {}, b'')

  def sent(self, batch=0):
    return json.loads(self.batches[batch][1]['body'])

  def records(self, batch=0):
    return self.sent(batch)['resourceLogs'][0]['scopeLogs'][0]['logRecords']

  def lines(self, batch=0):
    return [r['body']['stringValue'] for r in self.records(batch)]


class _SinkTestCase(unittest.TestCase):
  """A sink wired up the way install() wires one, against a fake collector."""

  def setUp(self):
    self.addCleanup(setattr, trains, 'http_request', trains.http_request)
    self.addCleanup(setattr, time, 'time', time.time)
    time.time = lambda: _NOW

    self.sink = otel.Sink('https://collector.example/otlp', 'Basic abc123')
    self.addCleanup(logging.set_sink, None)
    self.addCleanup(setattr, logging, '_write', logging._write)
    logging._write = lambda msg: None
    logging.set_sink(self.sink)

  def collector(self, **kwargs):
    collector = _Collector(**kwargs)
    trains.http_request = collector
    return collector


class BatchTest(_SinkTestCase):
  """What a batch says."""

  def test_posts_lines_to_the_logs_endpoint(self):
    collector = self.collector()
    self.sink.write('[12:00:00] Starting...')
    self.sink.write('[12:00:01] Connected!')

    self.assertTrue(self.sink.send())
    url, request = collector.batches[0]
    self.assertEqual('https://collector.example/otlp/v1/logs', url)
    self.assertEqual('POST', request['method'])
    self.assertEqual('Basic abc123', request['headers']['Authorization'])
    self.assertEqual('application/json', request['headers']['Content-Type'])
    self.assertEqual(['[12:00:00] Starting...', '[12:00:01] Connected!'],
                     collector.lines())

  def test_stamps_lines_in_nanoseconds(self):
    collector = self.collector()
    self.sink.write('a line')
    self.sink.send()

    stamp = collector.records()[0]['timeUnixNano']
    self.assertEqual(str(int(_NOW)) + '000000000', stamp)
    self.assertEqual(19, len(stamp), 'nanoseconds, not seconds or millis')

  def test_a_traceback_arrives_as_error_lines(self):
    collector = self.collector()
    self.sink.write('Traceback (most recent call last):\n  ValueError\n',
                    logging.ERROR)
    self.sink.send()

    self.assertEqual(['Traceback (most recent call last):', '  ValueError'],
                     collector.lines())
    self.assertEqual(['ERROR', 'ERROR'],
                     [r['severityText'] for r in collector.records()])
    self.assertEqual([17, 17],
                     [r['severityNumber'] for r in collector.records()])

  def test_every_batch_is_the_same_service(self):
    collector = self.collector()
    self.sink.write('a line')
    self.sink.send()

    attributes = collector.sent()['resourceLogs'][0]['resource']['attributes']
    self.assertEqual({'key': 'service.name',
                      'value': {'stringValue': 'pico-train-display'}},
                     attributes[0])

  def test_says_whether_it_is_the_board_or_the_simulator(self):
    # The two send the same lines to the same place, so without this a board
    # being debugged on a laptop reads exactly like the one on the wall.
    collector = self.collector()
    self.sink.write('a line')
    self.sink.send()

    attributes = collector.sent()['resourceLogs'][0]['resource']['attributes']
    self.assertEqual({'key': 'deployment.environment.name',
                      'value': {'stringValue': 'simulator'}},
                     attributes[1], 'these tests are not a Pico')

  def test_the_board_is_what_reports_rp2(self):
    self.assertEqual('device', otel._environment('rp2'))
    self.assertEqual('simulator', otel._environment('darwin'))
    self.assertEqual('simulator', otel._environment('linux'))

  def test_the_service_name_is_not_a_setting(self):
    # It names the log at the collector, and a name that can be changed per
    # board is a name that has to be searched for. Nothing in the config, and
    # so nothing on the setup page, can reach it.
    self.assertNotIn(
        'service',
        ' '.join(config.OtelConfig.__init__.__code__.co_varnames).lower(),
    )
    self.assertNotIn('service', str(otel.Sink.__init__.__code__.co_varnames))


class BufferTest(_SinkTestCase):
  """What is held between sends."""

  def test_splits_lines_and_drops_the_blanks(self):
    collector = self.collector()
    self.sink.write('one\n\ntwo\n')
    self.sink.send()
    self.assertEqual(['one', 'two'], collector.lines())

  def test_nothing_to_say_is_nothing_to_send(self):
    collector = self.collector()
    self.assertTrue(self.sink.send())
    self.assertEqual([], collector.batches)

  def test_remembers_the_newest_lines_when_it_fills_up(self):
    # What finally went wrong is in the last few lines, not the first.
    collector = self.collector()
    for i in range(otel._MAX_LINES * 2):
      self.sink.write('line {}'.format(i))
    self.sink.send()

    lines = collector.lines()
    self.assertEqual(otel._MAX_LINES, len(lines))
    self.assertEqual('line {}'.format(otel._MAX_LINES * 2 - 1), lines[-1])
    self.assertNotIn('line 0', lines)


class ClockTest(_SinkTestCase):
  """Stamps from before NTP, which a collector would throw away."""

  def _seconds(self, collector):
    return [int(r['timeUnixNano']) // 10**9 for r in collector.records()]

  def test_the_boot_keeps_its_shape_and_its_place(self):
    # A Pico has no clock across a power cut: it starts at zero and counts,
    # so these readings are six seconds of boot and not 1970. Anchoring them
    # to the first real one puts them where they happened, in order, instead
    # of in a heap at whenever the display first managed to send.
    collector = self.collector()
    time.time = lambda: 100.0
    self.sink.write('Starting...')
    time.time = lambda: 106.0
    self.sink.write('Connected!')
    time.time = lambda: _NOW  # NTP answers.
    self.sink.write('Time set to UTC')
    self.sink.send()

    self.assertEqual(['Starting...', 'Connected!', 'Time set to UTC'],
                     collector.lines())
    self.assertEqual([int(_NOW) - 6, int(_NOW), int(_NOW)],
                     self._seconds(collector))

  def test_nothing_is_sent_until_there_is_a_clock(self):
    # There is no stamp a collector would take, and the boot is the most
    # worth keeping of anything a display logs.
    collector = self.collector()
    time.time = lambda: 100.0
    self.sink.write('Starting...')

    self.assertTrue(self.sink.send())
    self.assertEqual([], collector.batches)

    time.time = lambda: _NOW
    self.sink.send()
    self.assertEqual(['Starting...'], collector.lines())
    self.assertEqual([int(_NOW)], self._seconds(collector))


class FailureTest(_SinkTestCase):
  """A collector that cannot be reached, which costs lines nothing."""

  def test_keeps_the_lines_it_could_not_deliver(self):
    self.collector(error=OSError('no route to host'))
    self.sink.write('worth keeping')
    self.assertFalse(self.sink.send())

    collector = self.collector()
    self.sink.write('and this')
    self.assertTrue(self.sink.send())
    lines = collector.lines()
    self.assertEqual('worth keeping', lines[0])
    self.assertEqual('and this', lines[-1])

  def test_a_rejected_batch_is_a_failure_like_any_other(self):
    collector = self.collector(status=401)
    self.sink.write('a line')
    self.assertFalse(self.sink.send())
    self.assertEqual(1, len(collector.batches))

  def test_says_so_once_rather_than_once_a_try(self):
    # A line an attempt would be most of the log, and would fill the buffer
    # it cannot empty.
    self.collector(error=OSError('no route to host'))
    self.sink.write('a line')
    for _ in range(5):
      self.sink.send()

    collector = self.collector()
    self.sink.send()
    complaints = [l for l in collector.lines() if 'Could not reach' in l]
    self.assertEqual(1, len(complaints))
    self.assertIn('no route to host', complaints[0])


class CaptureTest(_SinkTestCase):
  """That everything the firmware logs is something the collector sees."""

  def lines(self):
    return [line for _, _, line in self.sink._lines]

  def test_every_logged_line_reaches_the_sink(self):
    logging.log('Connecting to SSID: {}', 'a-network')
    self.assertEqual(['Connecting to SSID: a-network'], self.lines())

  def test_the_shipped_line_carries_no_clock_of_its_own(self):
    # The collector stamps it. A second time beside that one is the board's
    # clock, which reads 00:00:00 until NTP answers and is only confusing.
    written = []
    logging._write = written.append
    logging.log('Starting...')

    self.assertEqual(['Starting...'], self.lines())
    self.assertTrue(written[0].endswith('] Starting...'), written)

  def test_tracebacks_reach_the_sink_too(self):
    # The whole point: sys.print_exception() writes straight to stdout, where
    # nothing but a serial cable can see it.
    try:
      raise ValueError('boom')
    except ValueError as e:
      logging.exception(e)

    self.assertTrue(any('boom' in line for line in self.lines()))
    self.assertEqual([logging.ERROR] * len(self.sink._lines),
                     [severity for _, severity, _ in self.sink._lines])

  def test_nothing_in_the_firmware_writes_behind_the_sinks_back(self):
    # The sink is fed by logging, so anything printing for itself is a line
    # that only a serial cable will ever see. os.dupterm would catch those
    # too, but the RP2040 has one slot and set_logging_file() wants it, and
    # the simulator's MicroPython has no dupterm at all.
    writes = re.compile(r'(?<!\.)\bprint\s*\(|\bsys\.print_exception\b')
    offenders = []
    for directory, subdirectories, files in os.walk(_SRC):
      subdirectories[:] = [d for d in subdirectories if d != 'assets']
      for name in sorted(files):
        # logging.py is the one that is allowed to; content.py is generated.
        if not name.endswith('.py') or name in ('logging.py', 'content.py'):
          continue
        path = os.path.join(directory, name)
        with open(path, encoding='utf-8', errors='replace') as f:
          if writes.search(f.read()):
            offenders.append(os.path.relpath(path, _ROOT))

    self.assertEqual([], offenders, 'log these through logging instead')


class ConfigTest(unittest.TestCase):
  """Where the endpoint and the header come from."""

  def setUp(self):
    for name in ('OTEL_EXPORTER_OTLP_ENDPOINT', 'OTEL_EXPORTER_OTLP_HEADERS'):
      self.addCleanup(os.environ.pop, name, None)
      os.environ.pop(name, None)

  def test_the_endpoint_defaults_to_grafanas(self):
    # So that turning the collector on is one token, not two settings.
    self.assertEqual(config.DEFAULT_OTEL_ENDPOINT,
                     config.OtelConfig().endpoint)
    self.assertEqual('https://elsewhere.example/otlp',
                     config.OtelConfig(endpoint='https://elsewhere.example/otlp'
                                      ).endpoint)

  def test_config_json_needs_no_environment(self):
    otel_config = config.OtelConfig(endpoint='https://collector.example',
                                    auth='Basic abc')
    otel_config.validate()
    self.assertTrue(otel_config.enabled)

  def test_takes_the_token_however_it_was_pasted(self):
    # A real 401 from a real board: Grafana shows the header the way the
    # environment variable wants it, and pasting that into the setup page
    # sends 'Basic%20...', which the gateway answers with "no credentials
    # provided". Every way of writing it means the same thing.
    blob = 'MTc4MzIzMTpnbGNfZXlK'
    for pasted in ('Basic ' + blob,
                   'Basic%20' + blob,
                   'Authorization=Basic%20' + blob,
                   'Authorization: Basic ' + blob,
                   '  Basic%20' + blob + '  ',
                   blob):
      self.assertEqual('Basic ' + blob,
                       config.OtelConfig(auth=pasted).auth, pasted)

  def test_no_token_stays_no_token(self):
    self.assertEqual('', config.OtelConfig(auth='').auth)
    self.assertFalse(config.OtelConfig(auth='  ').enabled)

  def test_reads_the_variables_grafana_hands_out(self):
    # Including the %20, which is how Grafana's console writes the space and
    # which is a 401 if it reaches the wire.
    os.environ['OTEL_EXPORTER_OTLP_ENDPOINT'] = 'https://collector.example/otlp'
    os.environ['OTEL_EXPORTER_OTLP_HEADERS'] = 'Authorization=Basic%20abc123'

    otel_config = config.OtelConfig()
    self.assertEqual('https://collector.example/otlp', otel_config.endpoint)
    self.assertEqual('Basic abc123', otel_config.auth)

  def test_an_endpoint_with_no_token_is_simply_off(self):
    # Which is what the setup page submits when the two fields are left as
    # they came: the endpoint has a default, so it is never empty, and
    # refusing that config would make the collector compulsory.
    otel_config = config.OtelConfig()
    otel_config.validate()
    self.assertFalse(otel_config.enabled)
    self.assertIsNone(otel.install(otel_config))

  def test_the_settings_never_stop_a_config_loading(self):
    # Both are optional, so no combination of them is worth refusing a whole
    # config over and sending someone back to the setup page.
    for otel_config in (config.OtelConfig(),
                        config.OtelConfig(endpoint='https://collector.example'),
                        config.OtelConfig(auth='Basic abc'),
                        config.OtelConfig(endpoint='', auth='')):
      otel_config.validate()


if __name__ == '__main__':
  unittest.main()
