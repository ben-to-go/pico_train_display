import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import firmware_path  # noqa: E402,F401

from models import BoardSnapshot, Departure, Station
from state import StateController


class StateControllerTest(unittest.TestCase):

  def test_initial_state(self):
    sc = StateController('SKM')
    self.assertEqual('SKM', sc.station())
    self.assertEqual((), sc.departures())
    self.assertEqual((), sc.calling_points())
    self.assertTrue(sc.stale())
    self.assertFalse(sc.snapshot.fetched)

  def test_update_departures(self):
    sc = StateController('SKM')
    dept = Departure('London Marylebone', 1015, 1015, False)
    board = Station('Stoke Mandeville', (dept,))
    points = ('Wendover', 'Amersham', 'London Marylebone')

    recovered = sc.update_departures(board, points)
    self.assertFalse(recovered, 'First load is not a recovery from stale')
    self.assertEqual('Stoke Mandeville', sc.station())
    self.assertEqual((dept,), sc.departures())
    self.assertEqual(points, sc.calling_points())
    self.assertFalse(sc.stale())
    self.assertTrue(sc.snapshot.fetched)

  def test_mark_stale_never_fetched_uses_fallback(self):
    sc = StateController('SKM')
    fallback_dept = Departure('Fallback Dest', 900, 900, False)
    fallback_board = Station('Fallback Station', (fallback_dept,))
    fallback_points = ('Station A', 'Station B')

    never_fetched, first_failure = sc.mark_stale(fallback_board, fallback_points)
    self.assertTrue(never_fetched)
    self.assertFalse(first_failure)
    self.assertTrue(sc.stale())
    self.assertEqual('Fallback Station', sc.station())
    self.assertEqual((fallback_dept,), sc.departures())
    self.assertEqual(fallback_points, sc.calling_points())

  def test_mark_stale_after_fetch_preserves_last_board(self):
    sc = StateController('SKM')
    dept = Departure('Live Train', 1100, 1100, False)
    board = Station('Live Station', (dept,))
    sc.update_departures(board, ('Live Point',))

    never_fetched, first_failure = sc.mark_stale()
    self.assertFalse(never_fetched)
    self.assertTrue(first_failure)
    self.assertTrue(sc.stale())
    self.assertEqual('Live Station', sc.station())
    self.assertEqual((dept,), sc.departures())

    # Recovery
    recovered = sc.update_departures(board, ('Live Point',))
    self.assertTrue(recovered)
    self.assertFalse(sc.stale())

  def test_custom_snapshot_atomic_swap(self):
    sc = StateController('SKM')
    snap = BoardSnapshot(
        station='Custom',
        departures=(),
        calling_points=(),
        stale=False,
        fetched=True,
        last_updated_ms=12345,
    )
    sc.set_snapshot(snap)
    self.assertEqual(snap, sc.snapshot)
    self.assertEqual('Custom', sc.station())

  def test_feature_flags_and_debugging_toggles(self):
    sc = StateController('SKM', telemetry_enabled=False, mock_mode=True)
    self.assertFalse(sc.telemetry_enabled)
    self.assertTrue(sc.mock_mode)


if __name__ == '__main__':
  unittest.main()
