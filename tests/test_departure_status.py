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
"""What the panel says about a train that is late, cancelled, or on time.

Both halves of it: the API's realtimeForecast becoming a departure's expected
time, and that becoming the three words on the right of the row.

Worth pinning because nothing else touches it. The departures baked into the
firmware are all on time and carry no forecast field at all, so every test
that uses them takes the "On time" branch and only that one. A renamed field
or an inverted comparison would leave every train on the panel quietly reading
"On time", with the suite still green.

Run with:
  python3 -m unittest discover -s tests
"""

import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import firmware_path  # noqa: E402,F401  see its docstring

import trains  # noqa: E402

# widgets imports display and fonts, which need framebuf and so cannot be
# imported off-device. _status is plain string work and needs none of it.
# Another test may have stubbed some of these already, so fill in what is
# missing rather than replacing what is there.
for _name, _attrs in (
    ('framebuf', ()),
    ('display', ('Display',)),
    ('fonts', ('Font',)),
):
  _module = sys.modules.setdefault(_name, types.ModuleType(_name))
  for _attr in _attrs:
    if not hasattr(_module, _attr):
      setattr(_module, _attr, type(_attr, (), {}))

import widgets  # noqa: E402


def _board(departure):
  """A location line-up carrying one service, as the API sends them."""
  return {
      'query': {'location': {'description': 'Clapham Junction'}},
      'services': [{
          'temporalData': {'departure': departure},
          'destination': [{'location': {'description': 'London Victoria'}}],
          'scheduleMetadata': {'uniqueIdentity': 'gb-nr:C29893:2026-08-19'},
      }],
  }


def _only(departure):
  return trains.parse_departures(_board(departure)).departures[0]


class ExpectedTimeTest(unittest.TestCase):
  """realtimeForecast, which is where a delay lives."""

  def test_a_late_train_is_expected_when_the_forecast_says(self):
    # Taken off the wire at Clapham Junction: booked 22:56, running 23:11.
    departure = _only({
        'scheduleAdvertised': '2026-08-19T22:56:00',
        'realtimeForecast': '2026-08-19T23:11:00',
        'isCancelled': False,
    })

    self.assertEqual(2256, departure.departure_time)
    self.assertEqual(2311, departure.actual_departure_time)
    self.assertEqual('Exp 23:11', widgets._status(departure))

  def test_a_forecast_matching_the_timetable_is_on_time(self):
    departure = _only({
        'scheduleAdvertised': '2026-08-19T23:05:00',
        'realtimeForecast': '2026-08-19T23:05:00',
        'isCancelled': False,
    })

    self.assertEqual('On time', widgets._status(departure))

  def test_no_forecast_at_all_is_on_time(self):
    # Which is how the departures baked into the firmware arrive, and how the
    # API answers before a train has any realtime data against it.
    departure = _only({
        'scheduleAdvertised': '2026-08-19T23:05:00',
        'isCancelled': False,
    })

    self.assertEqual(2305, departure.actual_departure_time)
    self.assertEqual('On time', widgets._status(departure))

  def test_a_delay_past_midnight_reads_as_the_next_day(self):
    # The times are hhmm integers, so this is the one that would come out as
    # "Exp 24:10" or worse if they were arithmetic rather than a clock.
    departure = _only({
        'scheduleAdvertised': '2026-08-19T23:50:00',
        'realtimeForecast': '2026-08-20T00:10:00',
        'isCancelled': False,
    })

    self.assertEqual('Exp 00:10', widgets._status(departure))


class CancelledTest(unittest.TestCase):

  def test_a_cancelled_train_says_so(self):
    departure = _only({
        'scheduleAdvertised': '2026-08-19T23:05:00',
        'isCancelled': True,
    })

    self.assertTrue(departure.cancelled)
    self.assertEqual('Cancelled', widgets._status(departure))

  def test_cancelled_beats_a_forecast(self):
    # A cancelled service can still carry one, and an expected time is not
    # what anyone waiting for it needs told.
    departure = _only({
        'scheduleAdvertised': '2026-08-19T23:05:00',
        'realtimeForecast': '2026-08-19T23:20:00',
        'isCancelled': True,
    })

    self.assertEqual('Cancelled', widgets._status(departure))


if __name__ == '__main__':
  unittest.main()
