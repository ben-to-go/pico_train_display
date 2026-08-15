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
"""Tests for the calling points shown on the middle line of the board.

Run with:
  python3 -m unittest discover -s tests
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import trains


def _service(*stops):
  return {
      'service': {
          'locations': [
              {'location': {'description': name, 'shortCodes': [code]}}
              for code, name in stops
          ]
      }
  }


# The Chiltern line through Stoke Mandeville, from the API's own ordering.
_CHILTERN = _service(
    ('AVP', 'Aylesbury Vale Parkway'),
    ('AYS', 'Aylesbury'),
    ('SKM', 'Stoke Mandeville'),
    ('WND', 'Wendover'),
    ('GMN', 'Great Missenden'),
    ('AMR', 'Amersham'),
    ('MYB', 'London Marylebone'),
)


class CallingPointsTest(unittest.TestCase):

  def setUp(self):
    self.requests = []
    self.addCleanup(setattr, trains, '_get_json', trains._get_json)
    self.addCleanup(
        setattr, trains, 'get_access_token', trains.get_access_token
    )
    trains.get_access_token = lambda *args, **kwargs: 'access-token'

  def _respond_with(self, payload):
    def get_json(url, *args, **kwargs):
      self.requests.append(url)
      if isinstance(payload, Exception):
        raise payload
      return payload

    trains._get_json = get_json

  def test_lists_only_the_stops_after_this_station(self):
    self._respond_with(_CHILTERN)
    points = trains.get_calling_points(
        'gb-nr:C1:2026-08-17', 'SKM', 'token', 'https://data.rtt.io'
    )
    self.assertEqual(
        ('Wendover', 'Great Missenden', 'Amersham', 'London Marylebone'),
        points,
    )

  def test_station_not_on_the_route_gives_nothing(self):
    # Better to show no calling points than somebody else's.
    self._respond_with(_CHILTERN)
    self.assertEqual(
        (),
        trains.get_calling_points(
            'gb-nr:C1:2026-08-17', 'KGX', 'token', 'https://data.rtt.io'
        ),
    )

  def test_terminating_here_gives_nothing(self):
    self._respond_with(_CHILTERN)
    self.assertEqual(
        (),
        trains.get_calling_points(
            'gb-nr:C1:2026-08-17', 'MYB', 'token', 'https://data.rtt.io'
        ),
    )


class UpdaterCallingPointsTest(unittest.TestCase):

  def setUp(self):
    self.requests = []
    self.addCleanup(setattr, trains, '_get_json', trains._get_json)
    self.addCleanup(setattr, trains, 'get_departures', trains.get_departures)
    self.addCleanup(
        setattr, trains, 'get_access_token', trains.get_access_token
    )
    trains.get_access_token = lambda *args, **kwargs: 'access-token'

    def get_json(url, *args, **kwargs):
      self.requests.append(url)
      return _CHILTERN

    trains._get_json = get_json

  def _board(self, identity):
    return trains.Station(
        'Stoke Mandeville',
        (trains.Departure('London Marylebone', 1950, 1950, False, identity),),
    )

  def _updater(self):
    return trains.DepartureUpdater(
        'SKM', 'MYB', 'https://data.rtt.io', 'token', 0
    )

  def test_fetched_for_the_first_departure(self):
    updater = self._updater()
    trains.get_departures = lambda *a, **k: self._board('gb-nr:C1:2026-08-17')
    updater.update()

    self.assertEqual(
        ('Wendover', 'Great Missenden', 'Amersham', 'London Marylebone'),
        updater.calling_points(),
    )
    self.assertEqual(1, len(self.requests))

  def test_not_refetched_while_the_same_train_is_next(self):
    updater = self._updater()
    trains.get_departures = lambda *a, **k: self._board('gb-nr:C1:2026-08-17')
    updater.update()
    updater.update()
    updater.update()

    self.assertEqual(1, len(self.requests))

  def test_refetched_when_the_first_departure_changes(self):
    updater = self._updater()
    trains.get_departures = lambda *a, **k: self._board('gb-nr:C1:2026-08-17')
    updater.update()
    trains.get_departures = lambda *a, **k: self._board('gb-nr:C2:2026-08-17')
    updater.update()

    self.assertEqual(2, len(self.requests))

  def test_a_failed_lookup_does_not_fail_the_board(self):
    # Rate limited, most likely. The departures still matter.
    updater = self._updater()
    trains.get_departures = lambda *a, **k: self._board('gb-nr:C1:2026-08-17')

    def get_json(url, *args, **kwargs):
      raise ValueError('API request failed! 429')

    trains._get_json = get_json

    updater.update()
    self.assertFalse(updater.stale())
    self.assertTrue(updater.departures())
    self.assertEqual((), updater.calling_points())

  def test_empty_board_clears_the_calling_points(self):
    updater = self._updater()
    trains.get_departures = lambda *a, **k: self._board('gb-nr:C1:2026-08-17')
    updater.update()
    trains.get_departures = lambda *a, **k: trains.Station('SKM', ())
    updater.update()

    self.assertEqual((), updater.calling_points())


if __name__ == '__main__':
  unittest.main()
