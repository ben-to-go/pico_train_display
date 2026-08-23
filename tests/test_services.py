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


class ConnectKnownTest(unittest.TestCase):

  def setUp(self):
    self.addCleanup(setattr, wifi, 'scan_networks', wifi.scan_networks)
    self.addCleanup(setattr, wifi, 'connect', wifi.connect)

  def test_connects_to_first_matching_scanned_network(self):
    wifi.scan_networks = lambda *a, **k: ['OtherNet', 'WorkNet', 'HomeNet']
    wifi.connect = (
        lambda ssid, password, *a, **k: (
            'wlan_obj' if ssid == 'WorkNet' else None
        )
    )

    res = wifi.connect_known((('HomeNet', 'h1'), ('WorkNet', 'w1')))
    self.assertEqual('wlan_obj', res)

  def test_tries_subsequent_matching_networks_if_first_fails(self):
    attempts = []
    wifi.scan_networks = lambda *a, **k: ['WorkNet', 'HomeNet']

    def fake_connect(ssid, password, *a, **k):
      attempts.append((ssid, password))
      if ssid == 'HomeNet':
        return 'wlan_obj'
      return None

    wifi.connect = fake_connect

    res = wifi.connect_known((('HomeNet', 'h1'), ('WorkNet', 'w1')))
    self.assertEqual('wlan_obj', res)
    self.assertEqual([('WorkNet', 'w1'), ('HomeNet', 'h1')], attempts)

  def test_returns_none_when_no_known_networks_found_in_scan(self):
    wifi.scan_networks = lambda *a, **k: ['Unrelated1', 'Unrelated2']
    res = wifi.connect_known((('HomeNet', 'h1'), ('WorkNet', 'w1')))
    self.assertIsNone(res)

  def test_falls_back_to_direct_connect_when_scan_empty(self):
    attempts = []
    wifi.scan_networks = lambda *a, **k: []

    def fake_connect(ssid, password, *a, **k):
      attempts.append((ssid, password))
      return 'wlan_obj'

    wifi.connect = fake_connect

    res = wifi.connect_known((('HomeNet', 'h1'), ('WorkNet', 'w1')))
    self.assertEqual('wlan_obj', res)
    self.assertEqual([('HomeNet', 'h1')], attempts)


import net.http as http
import select
import socket


class HttpSocketConnectTest(unittest.TestCase):

  def test_poll_receives_timeout_in_milliseconds(self):
    poll_calls = []

    class MockPoll:
      def register(self, s, flags):
        pass
      def poll(self, timeout_ms):
        poll_calls.append(timeout_ms)
        return [(1, select.POLLOUT)]

    orig_poll = select.poll
    select.poll = MockPoll
    self.addCleanup(setattr, select, 'poll', orig_poll)

    class MockSocket:
      def connect(self, addr):
        pass
      def settimeout(self, timeout):
        pass
      def close(self):
        pass

    orig_socket = socket.socket
    orig_getaddrinfo = socket.getaddrinfo
    socket.socket = lambda *a, **k: MockSocket()
    socket.getaddrinfo = lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 0, '', ('127.0.0.1', 80))]
    self.addCleanup(setattr, socket, 'socket', orig_socket)
    self.addCleanup(setattr, socket, 'getaddrinfo', orig_getaddrinfo)

    s = http._connect_socket('example.com', 80, timeout=15)
    self.assertIsNotNone(s)
    self.assertEqual([15000], poll_calls)

  def test_response_records_duration_ms(self):
    r = http.Response(200, {'content-type': 'application/json'}, b'{}', duration_ms=125)
    self.assertEqual(125, r.duration_ms)
    self.assertEqual(200, r.status_code)
    self.assertEqual(b'{}', r.content)


if __name__ == '__main__':
  unittest.main()


