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
"""The log push, end to end, over a real socket under real MicroPython.

The unit tests stop at trains.http_request, because a CPython socket has no
readline() or write() and so cannot run it. Everything below that is the part
worth checking on a wire: a hand written HTTP/1.0 request with a body, and a
config read from the variables Grafana hands out. This runs the firmware's own
modules under the simulator's MicroPython against a collector on localhost.

It is what caught time.time() being a float on the simulator and whole seconds
on the board, which no CPython test could have.

Needs the MicroPython unix port, the same binary `make sim` uses, and skips
without one:
  make unix-port
  python3 -m unittest discover -s tests
"""

import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler
from http.server import HTTPServer

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
_MICROPYTHON = os.environ.get(
    'MICROPYTHON',
    os.path.expanduser('~/micropython/ports/unix/build-standard/micropython'),
)

# Base64 of nothing in particular. What matters is that it survives the
# %20 that Grafana writes the space in 'Basic <token>' as.
_AUTH = 'Basic dGVzdDp0b2tlbg=='

# Logs the way the firmware logs, then sends as many batches as asked for.
_CLIENT = """
import sys

import config
import logging
import otel

sink = otel.install(config.OtelConfig())
logging.log('Connecting to SSID: {}', 'a-network')
try:
  raise ValueError('a departure board that would not load')
except ValueError as e:
  logging.exception(e)

for _ in range(int(sys.argv[1])):
  print('SEND', sink.send())
"""


class _Handler(BaseHTTPRequestHandler):

  def do_POST(self):
    body = self.rfile.read(int(self.headers['Content-Length']))
    self.server.received.append((self.path, dict(self.headers), body))
    status = self.server.statuses.pop(0) if self.server.statuses else 200
    self.send_response(status)
    self.send_header('Content-Type', 'application/json')
    self.send_header('Content-Length', '2')
    self.end_headers()
    self.wfile.write(b'{}')

  def log_message(self, *args):
    pass  # Quiet: the test reports what arrived, not the server.


@unittest.skipUnless(
    os.path.exists(_MICROPYTHON),
    'needs the MicroPython unix port; build it with `make unix-port` or set '
    'MICROPYTHON',
)
class LogPushTest(unittest.TestCase):

  def setUp(self):
    self.server = HTTPServer(('127.0.0.1', 0), _Handler)
    self.server.received = []
    self.server.statuses = []
    thread = threading.Thread(target=self.server.serve_forever, daemon=True)
    thread.start()
    # Cleanups run in reverse, and the order matters: stop the loop, wait for
    # it, and only then close the socket it was polling.
    self.addCleanup(self.server.server_close)
    self.addCleanup(thread.join, 5)
    self.addCleanup(self.server.shutdown)

    self.endpoint = 'http://127.0.0.1:{}/otlp'.format(
        self.server.server_address[1]
    )

  def _run_firmware(self, sends=1):
    """Runs the client under MicroPython, and returns what it printed."""
    with tempfile.NamedTemporaryFile('w', suffix='.py', delete=False) as f:
      f.write(_CLIENT)
      client = f.name
    self.addCleanup(os.unlink, client)

    environment = dict(os.environ)
    environment.update({
        # src alone would not find ssl, which the unix port keeps in .frozen.
        'MICROPYPATH': 'src:.frozen',
        'OTEL_EXPORTER_OTLP_ENDPOINT': self.endpoint,
        'OTEL_EXPORTER_OTLP_HEADERS': 'Authorization=' + _AUTH.replace(
            ' ', '%20'
        ),
    })
    result = subprocess.run(
        [_MICROPYTHON, client, str(sends)],
        cwd=_ROOT, env=environment, capture_output=True, text=True, timeout=60,
    )
    self.assertEqual(0, result.returncode, result.stdout + result.stderr)
    return result.stdout

  def _batch(self, index=0):
    path, headers, body = self.server.received[index]
    return path, headers, json.loads(body)

  def _lines(self, index=0):
    _, _, sent = self._batch(index)
    return [r['body']['stringValue']
            for r in sent['resourceLogs'][0]['scopeLogs'][0]['logRecords']]

  def test_a_batch_arrives_at_the_collector(self):
    output = self._run_firmware()

    self.assertIn('SEND True', output)
    self.assertEqual(1, len(self.server.received))
    path, headers, sent = self._batch()
    self.assertEqual('/otlp/v1/logs', path)
    self.assertEqual('application/json', headers['Content-Type'])
    # The %20 undone, which is a 401 at Grafana if it reaches the wire.
    self.assertEqual(_AUTH, headers['Authorization'])
    self.assertEqual(
        [{'key': 'service.name',
          'value': {'stringValue': 'pico-train-display'}},
         {'key': 'deployment.environment.name',
          'value': {'stringValue': 'simulator'}}],
        sent['resourceLogs'][0]['resource']['attributes'])

  def test_it_carries_the_lines_and_the_traceback(self):
    self._run_firmware()

    lines = self._lines()
    self.assertTrue(any('Connecting to SSID: a-network' in l for l in lines),
                    lines)
    self.assertTrue(
        any('a departure board that would not load' in l for l in lines), lines)

    records = self._batch()[2][
        'resourceLogs'][0]['scopeLogs'][0]['logRecords']
    errors = [r for r in records if r['severityText'] == 'ERROR']
    self.assertTrue(errors, 'the traceback should arrive as ERROR')
    self.assertTrue(any('Traceback' in r['body']['stringValue']
                        for r in errors))

  def test_stamps_are_nanoseconds_the_collector_will_accept(self):
    import time

    before = int(time.time())
    self._run_firmware()
    after = int(time.time())

    records = self._batch()[2][
        'resourceLogs'][0]['scopeLogs'][0]['logRecords']
    for record in records:
      stamp = record['timeUnixNano']
      self.assertEqual(19, len(stamp), stamp)
      self.assertTrue(before <= int(stamp) // 10**9 <= after, stamp)

  def test_a_rejected_batch_is_kept_and_sent_again(self):
    # Nothing is lost to a collector having a bad minute, which is the whole
    # reason send() puts the lines back.
    self.server.statuses = [500]
    output = self._run_firmware(sends=2)

    self.assertIn('SEND False', output)
    self.assertIn('SEND True', output)
    self.assertEqual(2, len(self.server.received))
    first, second = self._lines(0), self._lines(1)
    self.assertTrue(set(first).issubset(set(second)),
                    'the rejected lines should come back: {} then {}'.format(
                        first, second))
    self.assertTrue(any('Could not reach the log collector' in l
                        for l in second))


if __name__ == '__main__':
  unittest.main()
