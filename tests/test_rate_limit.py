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
"""A 429 is not like other failures: asking again makes it worse.

The API allows this account ten requests a minute and a hundred an hour, and
answers a 429 with the seconds to wait. Retrying through that is how a board
that is briefly over the limit stays over it.

Run with:
  python3 -m unittest discover -s tests
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

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

  def test_it_is_not_mistaken_for_a_bad_token(self):
    # AuthError makes the caller fetch a new token and try again, which is
    # exactly the wrong move here, so the two must not be confused.
    self._answer_with(_response(429, {'retry-after': '60'}))
    with self.assertRaises(trains.RateLimitError):
      trains._get_json('https://example/x', 'token', None, None)

  def test_a_401_is_still_an_auth_error(self):
    self._answer_with(_response(401, {}))
    with self.assertRaises(trains.AuthError):
      trains._get_json('https://example/x', 'token', None, None)


if __name__ == '__main__':
  unittest.main()
