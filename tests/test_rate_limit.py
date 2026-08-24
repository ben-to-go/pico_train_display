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
"""What the board is allowed to ask for, and what it does when refused.

The API allows this account ten requests a minute and a hundred an hour, and
answers a 429 with the seconds to wait. Retrying through that is how a board
that is briefly over the limit stays over it.

RequestBudgetTest at the bottom counts the requests the firmware would really
make, rather than reasoning about the arithmetic that decides them.

Run with:
  python3 -m unittest discover -s tests
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import firmware_path  # noqa: E402,F401  see its docstring

import fallback
import logging
import trains
from models import Response


def _response(status, headers):
  return Response(status, headers, b'{}')


class RateLimitTest(unittest.TestCase):

  def setUp(self):
    self.addCleanup(setattr, trains, 'http_request', trains.http_request)

  def _answer_with(self, response):
    trains.http_request = lambda *args, **kwargs: response

  def test_429_carries_the_wait_the_api_asked_for(self):
    self._answer_with(_response(429, {'retry-after': '1550'}))
    with self.assertRaises(trains.RateLimitError) as caught:
      trains._get_json('https://example/x', 'token', None, None)

    self.assertEqual(1550, caught.exception.retry_after)

  def test_429_without_a_header_is_still_a_rate_limit(self):
    # Worth not crashing over: the wait is then whatever the caller decides.
    self._answer_with(_response(429, {}))
    with self.assertRaises(trains.RateLimitError) as caught:
      trains._get_json('https://example/x', 'token', None, None)

    self.assertEqual(0, caught.exception.retry_after)

  def test_a_401_is_still_an_auth_error(self):
    self._answer_with(_response(401, {}))
    with self.assertRaises(trains.AuthError):
      trains._get_json('https://example/x', 'token', None, None)


class RetryWaitTest(unittest.TestCase):
  """How long the board leaves it after a failed update."""

  def test_the_api_decides_when_it_has_said_so(self):
    limited = trains.RateLimitError(1550)
    self.assertEqual(1550, trains.retry_wait(limited, 120))

  def test_a_short_retry_after_never_shortens_the_interval(self):
    # A retry-after under the polling interval would otherwise speed the
    # board up in response to being told to slow down.
    self.assertEqual(120, trains.retry_wait(trains.RateLimitError(30), 120))

  def test_a_rate_limit_with_no_header_never_gets_the_blip_treatment(self):
    # Being limited is reason enough to wait the usual interval.
    self.assertEqual(120, trains.retry_wait(trains.RateLimitError(0), 120))

  def test_api_outages_wait_normal_interval_without_reboot_loop(self):
    # When the API server is down or returns 5xx/4xx, wait the nominal interval
    # quietly without spamming the server or triggering a reboot loop.
    self.assertEqual(120, trains.retry_wait(OSError('server down'), 120))
    self.assertEqual(120, trains.retry_wait(ValueError('503 Service Unavailable'), 120))




# The allowance this account gets, read off the API's own headers:
#   x-ratelimit-limit-minute: 10
#   x-ratelimit-limit-hour:   100
#   x-ratelimit-limit-day:    1000
#   x-ratelimit-limit-week:   10000
_PER_HOUR = 100
_PER_DAY = 1000
_PER_WEEK = 10000

_DEFAULT_INTERVAL = 120


class RequestBudgetTest(unittest.TestCase):
  """What the board actually spends, counted through the real update path.

  Not a model of the loop's arithmetic: these drive DepartureUpdater.update()
  with the network stubbed one layer down, so every request the firmware would
  put on the wire is counted, including the token exchanges and the calling
  points fetches that are easy to forget.
  """

  def setUp(self):
    self.addCleanup(setattr, trains, 'http_request', trains.http_request)
    self.addCleanup(setattr, logging, '_write', logging._write)
    logging._write = lambda msg: None
    self.requests = []

  def _serve(self, status=200, headers=None):
    """Answers like the real API, and counts what was asked for."""

    def request(url, **kwargs):
      self.requests.append(url)
      if status != 200:
        return Response(status, headers or {}, b'{}')
      if 'get_access_token' in url:
        return Response(200, {}, '{"token": "access-token"}')
      if '/service' in url:
        return Response(200, {}, fallback.SERVICE)
      return Response(200, {}, fallback.RESPONSE)

    trains.http_request = request

  def _run_for(self, seconds, interval=_DEFAULT_INTERVAL):
    """Drives the update loop's policy over a stretch of time."""
    updater = trains.DepartureUpdater(
        'SKM', 'MYB', 'https://data.rtt.io', 'refresh-token', 0
    )
    elapsed, failures = 0, 0
    while elapsed < seconds:
      try:
        updater.update()
        failures = 0
        wait = interval
      except Exception as e:  # what run() does, minus the display
        failures += 1
        wait = trains.retry_wait(e, interval)
      elapsed += wait
    return len(self.requests)

  def test_a_working_day_fits_the_daily_allowance(self):
    self._serve()
    spent = self._run_for(24 * 60 * 60)

    self.assertLess(spent, _PER_DAY)

  def test_a_working_week_fits_the_weekly_allowance(self):
    self._serve()
    spent = self._run_for(7 * 24 * 60 * 60)

    self.assertLess(spent, _PER_WEEK)

  def test_a_working_hour_fits_the_hourly_allowance(self):
    self._serve()
    spent = self._run_for(60 * 60)

    self.assertLess(spent, _PER_HOUR)

  def test_twenty_seconds_is_what_broke_it(self):
    # The old default, kept as a test so the reason for the current one does
    # not have to be taken on trust.
    self._serve()
    spent = self._run_for(60 * 60, interval=20)

    self.assertGreater(spent, _PER_HOUR)

  def test_being_rate_limited_costs_less_than_working(self):
    # The failure that must not feed itself: if a limited hour spent more
    # requests than a healthy one, the board could never climb back out.
    self._serve(429, {'retry-after': '1550'})
    limited = self._run_for(60 * 60)

    self.requests = []
    self._serve()
    working = self._run_for(60 * 60)

    self.assertLess(limited, working)

  def test_an_outage_does_not_exceed_hourly_allowance(self):
    # API 500 error retries at the nominal interval and stays well inside
    # the request budget.
    self._serve(500)
    down = self._run_for(60 * 60)

    self.assertLess(down, _PER_HOUR)


if __name__ == '__main__':
  unittest.main()


class EveryRequestSaysSoTest(unittest.TestCase):
  """A line per request, which is how the budget above can be counted.

  Every call the firmware makes of the API goes through http_request, and the
  ones that used to leave no trace were the ones easiest to forget: the token
  exchange, the calling points, and any request answered by nothing at all.
  """

  def setUp(self):
    self.lines = []
    self.addCleanup(setattr, logging, '_write', logging._write)
    logging._write = self.lines.append

  def test_says_the_url_and_what_came_back(self):
    import socket
    import select
    from tests.test_http import MockSocket

    mock_sock = MockSocket([b'HTTP/1.0 200 OK\r\nContent-Length: 2\r\n\r\n{}'])
    orig_socket = socket.socket
    orig_getaddrinfo = socket.getaddrinfo
    orig_poll = select.poll

    class MockPoll:
      def register(self, *a): pass
      def poll(self, timeout): return [1]

    socket.socket = lambda *a, **k: mock_sock
    socket.getaddrinfo = lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 0, '', ('127.0.0.1', 80))]
    select.poll = lambda: MockPoll()

    self.addCleanup(setattr, socket, 'socket', orig_socket)
    self.addCleanup(setattr, socket, 'getaddrinfo', orig_getaddrinfo)
    self.addCleanup(setattr, select, 'poll', orig_poll)

    trains._get_json('http://data.rtt.io/gb-nr/location?code=SKM', 't', None, None)
    self.assertEqual(1, len(self.lines), self.lines)
    self.assertIn('http://data.rtt.io/gb-nr/location?code=SKM', self.lines[0])
    self.assertIn('200', self.lines[0])

  def test_a_refusal_is_still_a_request(self):
    import socket
    import select
    from tests.test_http import MockSocket

    mock_sock = MockSocket([b'HTTP/1.0 429 Too Many Requests\r\nRetry-After: 600\r\nContent-Length: 0\r\n\r\n'])
    orig_socket = socket.socket
    orig_getaddrinfo = socket.getaddrinfo
    orig_poll = select.poll

    class MockPoll:
      def register(self, *a): pass
      def poll(self, timeout): return [1]

    socket.socket = lambda *a, **k: mock_sock
    socket.getaddrinfo = lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 0, '', ('127.0.0.1', 80))]
    select.poll = lambda: MockPoll()

    self.addCleanup(setattr, socket, 'socket', orig_socket)
    self.addCleanup(setattr, socket, 'getaddrinfo', orig_getaddrinfo)
    self.addCleanup(setattr, select, 'poll', orig_poll)

    with self.assertRaises(trains.RateLimitError):
      trains._get_json('http://data.rtt.io/gb-nr/location', 't', None, None)

    self.assertTrue(any('429' in line for line in self.lines), self.lines)

  def test_a_request_answered_by_nothing_says_so(self):
    import socket
    orig_getaddrinfo = socket.getaddrinfo

    def refuse(*args, **kwargs):
      raise OSError('no route to host')

    socket.getaddrinfo = refuse
    self.addCleanup(setattr, socket, 'getaddrinfo', orig_getaddrinfo)

    with self.assertRaises(OSError):
      trains._get_json('http://data.rtt.io/gb-nr/location', 't', None, None)

    self.assertTrue(any('no route to host' in line for line in self.lines),
                    self.lines)
