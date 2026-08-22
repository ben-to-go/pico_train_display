import unittest

from models import BoardSnapshot, Departure, Response, Station


class ModelsTest(unittest.TestCase):

  def test_departure_equality(self):
    d1 = Departure('London Marylebone', 1830, 1835, False, 'id123')
    d2 = Departure('London Marylebone', 1830, 1835, False, 'id123')
    d3 = Departure('Aylesbury', 1830, 1835, False, 'id123')

    self.assertEqual(d1, d2)
    self.assertNotEqual(d1, d3)
    self.assertEqual('London Marylebone', d1.destination)
    self.assertEqual(1830, d1.departure_time)
    self.assertEqual(1835, d1.actual_departure_time)
    self.assertFalse(d1.cancelled)
    self.assertEqual('id123', d1.identity)

  def test_station_tuple(self):
    d = Departure('London Marylebone', 1830, 1830, False)
    station = Station('Stoke Mandeville', (d,))
    self.assertEqual('Stoke Mandeville', station.name)
    self.assertEqual((d,), station.departures)

  def test_board_snapshot(self):
    d = Departure('London Marylebone', 1830, 1830, False)
    snapshot = BoardSnapshot(
        'Stoke Mandeville', (d,), ('Great Missenden',), False, True, 1000
    )
    self.assertEqual('Stoke Mandeville', snapshot.station)
    self.assertEqual((d,), snapshot.departures)
    self.assertEqual(('Great Missenden',), snapshot.calling_points)
    self.assertFalse(snapshot.stale)
    self.assertTrue(snapshot.fetched)
    self.assertEqual(1000, snapshot.last_updated_ms)

  def test_response_properties(self):
    resp = Response(200, {'content-type': 'application/json'}, b'{"ok": true}')
    self.assertEqual(200, resp.status_code)
    self.assertEqual({'content-type': 'application/json'}, resp.headers)
    self.assertEqual(b'{"ok": true}', resp.content)


if __name__ == '__main__':
  unittest.main()
