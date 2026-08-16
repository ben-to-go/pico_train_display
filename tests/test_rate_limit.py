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

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import fallback
import trains


def _response(status, headers):
  return trains.Response(status, headers, b'{}')


class RateLimitTest(unittest.TestCase):

  def setUp(self):
    self.addCleanup(setattr, trains, '_http_request', trains._http_request)

  def _answer_with(self, response):
    trains._http_request = lambda *args, **kwargs: response

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
    self.assertEqual(1550, trains.retry_wait(limited, 1, 120))
    # However many times in a row: the header is the answer, not a starting
    # point to double from.
    self.assertEqual(1550, trains.retry_wait(limited, 5, 120))

  def test_a_short_retry_after_never_shortens_the_interval(self):
    # A retry-after under the polling interval would otherwise speed the
    # board up in response to being told to slow down.
    self.assertEqual(120, trains.retry_wait(trains.RateLimitError(30), 1, 120))

  def test_a_rate_limit_with_no_header_backs_off_anyway(self):
    # Being limited is reason enough to wait, even with nothing to go on.
    self.assertEqual(120, trains.retry_wait(trains.RateLimitError(0), 1, 120))
    self.assertEqual(240, trains.retry_wait(trains.RateLimitError(0), 2, 120))

  def test_other_failures_double_each_time_up_to_the_cap(self):
    waits = [trains.retry_wait(OSError('down'), n, 120) for n in range(1, 6)]
    # 1920 would be next, but the cap lands first.
    self.assertEqual([120, 240, 480, 960, trains._MAX_BACKOFF_SECS], waits)

  def test_the_wait_is_capped(self):
    # A board left overnight should still notice the API within the hour.
    self.assertEqual(
        trains._MAX_BACKOFF_SECS, trains.retry_wait(OSError('down'), 40, 120)
    )




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
    self.addCleanup(setattr, trains, '_http_request', trains._http_request)
    self.requests = []

  def _serve(self, status=200, headers=None):
    """Answers like the real API, and counts what was asked for."""

    def request(url, **kwargs):
      self.requests.append(url)
      if status != 200:
        return trains.Response(status, headers or {}, b'{}')
      if 'get_access_token' in url:
        return trains.Response(200, {}, '{"token": "access-token"}')
      if '/service' in url:
        return trains.Response(200, {}, fallback.SERVICE)
      return trains.Response(200, {}, fallback.RESPONSE)

    trains._http_request = request

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
        wait = trains.retry_wait(e, failures, interval)
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

  def test_an_outage_costs_less_than_working(self):
    # Same again for the failures that carry no retry-after, where the
    # doubling is all there is to go on.
    self._serve(500)
    down = self._run_for(60 * 60)

    self.requests = []
    self._serve()
    working = self._run_for(60 * 60)

    self.assertLess(down, working)


if __name__ == '__main__':
  unittest.main()
