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
"""Tests for falling back to the departures baked into the firmware.

Run with:
  python3 -m unittest discover -s tests
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import trains


def _updater(station='SKM', destination='MYB'):
  return trains.DepartureUpdater(
      station, destination, 'https://data.rtt.io', 'token', 0
  )


def _live_board():
  return trains.Station(
      'Somewhere Else',
      (trains.Departure('Birmingham', 1015, 1020, False),),
  )


class FallbackDeparturesTest(unittest.TestCase):

  def test_parses_the_stored_response(self):
    board = trains.fallback_departures()
    self.assertEqual('Stoke Mandeville', board.name)
    self.assertTrue(board.departures)
    for departure in board.departures:
      self.assertEqual('London Marylebone', departure.destination)
      self.assertFalse(departure.cancelled)

  def test_is_not_filtered_by_departure_time(self):
    # The snapshot is fixed, so "departing in the next N minutes" is
    # meaningless for it and must not empty the board.
    self.assertTrue(trains.fallback_departures().departures)


class StaleBoardTest(unittest.TestCase):

  def setUp(self):
    # Nothing in these tests should touch the network.
    self.addCleanup(setattr, trains, 'get_departures', trains.get_departures)
    self.addCleanup(
        setattr, trains, 'get_access_token', trains.get_access_token
    )
    trains.get_access_token = lambda *args, **kwargs: 'access-token'

  def _fail_with(self, error):
    def raiser(*args, **kwargs):
      raise error

    trains.get_departures = raiser

  def _succeed_with(self, board):
    trains.get_departures = lambda *args, **kwargs: board

  def test_starts_empty_rather_than_showing_the_fallback(self):
    # Nothing has failed yet, so there is nothing to fall back from.
    updater = _updater()
    self.assertTrue(updater.stale())
    self.assertEqual((), updater.departures())

  def test_successful_update_replaces_the_fallback(self):
    updater = _updater()
    self._succeed_with(_live_board())
    updater.update()

    self.assertFalse(updater.stale())
    self.assertEqual('Somewhere Else', updater.station())
    self.assertEqual(1, len(updater.departures()))

  def test_failure_keeps_the_last_good_board_and_marks_it_stale(self):
    updater = _updater()
    self._succeed_with(_live_board())
    updater.update()

    self._fail_with(OSError('network down'))
    with self.assertRaises(OSError):
      updater.update()

    # Never goes back to the fallback once a fetch has succeeded.
    self.assertTrue(updater.stale())
    self.assertEqual('Somewhere Else', updater.station())
    self.assertEqual(_live_board().departures, updater.departures())

  def test_failure_before_any_success_shows_the_fallback(self):
    updater = _updater()
    self._fail_with(OSError('network down'))
    with self.assertRaises(OSError):
      updater.update()

    self.assertTrue(updater.stale())
    self.assertEqual('Stoke Mandeville', updater.station())
    self.assertTrue(updater.departures())

  def test_fallback_is_used_whatever_station_is_configured(self):
    updater = _updater('KGX', 'YRK')
    self._fail_with(OSError('network down'))
    with self.assertRaises(OSError):
      updater.update()

    self.assertTrue(updater.departures())

  def test_recovers_when_the_api_comes_back(self):
    updater = _updater()
    self._fail_with(OSError('network down'))
    with self.assertRaises(OSError):
      updater.update()

    self._succeed_with(_live_board())
    updater.update()
    self.assertFalse(updater.stale())

  def test_expired_token_is_retried_once_then_falls_back(self):
    # One retry covers a merely expired access token. If it still fails the
    # token is revoked or blocked, and that is no different to any other
    # outage as far as the display is concerned.
    updater = _updater()
    self._fail_with(trains.AuthError('token rejected'))
    with self.assertRaises(trains.AuthError):
      updater.update()

    self.assertTrue(updater.stale())
    self.assertEqual('Stoke Mandeville', updater.station())
    self.assertTrue(updater.departures())

  def test_any_failure_falls_back(self):
    # An API that has been retired might answer with anything, including a
    # 200 whose shape we cannot parse. Every one of these leaves the board
    # showing something rather than nothing.
    failures = (
        OSError('host does not resolve'),
        ValueError('API request failed! 403'),
        ValueError('API request failed! 404'),
        trains.AuthError('token rejected'),
        KeyError('temporalData'),
        TypeError('NoneType is not subscriptable'),
    )
    for failure in failures:
      with self.subTest(failure=type(failure).__name__):
        updater = _updater()
        self._fail_with(failure)
        with self.assertRaises(type(failure)):
          updater.update()

        self.assertTrue(updater.stale())
        self.assertTrue(updater.departures())


if __name__ == '__main__':
  unittest.main()
