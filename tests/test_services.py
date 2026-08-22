import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import firmware_path  # noqa: E402,F401

import fallback
from models import Departure, Station
from services import ntp, rtt, wifi


class ServicesRttTest(unittest.TestCase):

  def test_to_hhmm_and_to_epoch(self):
    ts = '2026-08-15T18:30:00'
    self.assertEqual(1830, rtt.to_hhmm(ts))
    self.assertIsInstance(rtt.to_epoch(ts), (int, float))

  def test_fallback_departures(self):
    board = rtt.fallback_departures()
    self.assertIsInstance(board, Station)
    self.assertGreater(len(board.departures), 0)

  def test_fallback_calling_points(self):
    points = rtt.fallback_calling_points('SKM')
    self.assertIsInstance(points, tuple)
    self.assertGreater(len(points), 0)

  def test_lineup_url(self):
    url = rtt.lineup_url('https://data.rtt.io', 'SKM', 'MYB')
    self.assertIn('code=SKM', url)
    self.assertIn('filterTo=MYB', url)


class ServicesWifiTest(unittest.TestCase):

  def test_status_desc(self):
    self.assertEqual('STAT_GOT_IP', wifi.wifi_status_desc(3))
    self.assertEqual('STAT_CONNECT_FAIL', wifi.wifi_status_desc(-1))
    self.assertEqual('STAT_NO_AP_FOUND', wifi.wifi_status_desc(-2))
    self.assertEqual('STAT_WRONG_PASSWORD', wifi.wifi_status_desc(-3))


if __name__ == '__main__':
  unittest.main()
