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

sys.path.insert(0, os.path.dirname(__file__))
import firmware_path  # noqa: E402,F401  see its docstring

import logging
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

  def test_comes_with_calling_points_for_the_first_train(self):
    # The calling points are a second request the device cannot make when the
    # API is down, so they are baked in beside the board.
    self.assertEqual(
        (
            'Wendover',
            'Great Missenden',
            'Amersham',
            'Chalfont and Latimer',
            'Chorleywood',
            'Harrow-on-the-Hill',
            'London Marylebone',
        ),
        trains.fallback_calling_points('SKM'),
    )

  def test_calling_points_are_empty_for_another_station(self):
    self.assertEqual((), trains.fallback_calling_points('KGX'))

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
    # Including the line that scrolls.
    self.assertIn('Wendover', updater.calling_points())

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


class WhySaysSoTest(unittest.TestCase):
  """Every reason the baked-in board goes up says so in the log.

  The dot in the corner is the only thing on the panel that admits the
  departures are not current, and one lit dot looks the same whatever lit it.
  """

  def setUp(self):
    self.lines = []
    self.addCleanup(setattr, logging, '_write', logging._write)
    self.addCleanup(setattr, trains, 'get_departures', trains.get_departures)
    self.addCleanup(
        setattr, trains, 'get_access_token', trains.get_access_token
    )
    logging._write = self.lines.append
    trains.get_access_token = lambda *args, **kwargs: 'access-token'
    self.updater = _updater()

  def _fail_with(self, error):
    def get_departures(*args, **kwargs):
      raise error
    trains.get_departures = get_departures

  def _succeed(self):
    trains.get_departures = lambda *args, **kwargs: _live_board()

  def _logged(self, fragment):
    return [line for line in self.lines if fragment in line]

  def test_says_when_nothing_has_ever_loaded(self):
    self._fail_with(OSError('no route to host'))
    with self.assertRaises(OSError):
      self.updater.update()

    self.assertTrue(self.updater.stale())
    self.assertTrue(self._logged('baked into the firmware'), self.lines)

  def test_says_when_the_board_it_is_showing_has_gone_stale(self):
    # A different thing to say: there are real departures on the panel, they
    # are just the ones from last time.
    self._succeed()
    self.updater.update()
    self._fail_with(OSError('no route to host'))
    with self.assertRaises(OSError):
      self.updater.update()

    self.assertTrue(self._logged('are now stale'), self.lines)
    self.assertFalse(self._logged('baked into the firmware'), self.lines)

  def test_says_so_once_rather_than_every_two_minutes(self):
    self._succeed()
    self.updater.update()
    self._fail_with(OSError('no route to host'))
    for _ in range(3):
      with self.assertRaises(OSError):
        self.updater.update()

    self.assertEqual(1, len(self._logged('are now stale')), self.lines)

  def test_says_when_the_departures_are_current_again(self):
    # Otherwise the log says a display broke and never says it came back.
    self._succeed()
    self.updater.update()
    self._fail_with(OSError('down'))
    with self.assertRaises(OSError):
      self.updater.update()
    self._succeed()
    self.updater.update()

    self.assertFalse(self.updater.stale())
    self.assertTrue(self._logged('are current again'), self.lines)

  def test_the_first_board_of_the_day_has_not_recovered_from_anything(self):
    # An updater starts stale, having nothing to show yet, so the first
    # success would otherwise announce a recovery that never happened.
    self._succeed()
    self.updater.update()

    self.assertFalse(self._logged('are current again'), self.lines)

  def test_says_how_much_of_what_the_api_sent_is_being_shown(self):
    # An empty board and an API that sent nothing look identical on the panel.
    trains.fallback_departures()
    self.assertTrue(self._logged('to show'), self.lines)
