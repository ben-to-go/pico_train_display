import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import firmware_path  # noqa: E402,F401

import logging


class MockSink:
  def __init__(self):
    self.records = []

  def write(self, msg, severity):
    self.records.append((msg, severity))


class LoggingTest(unittest.TestCase):

  def setUp(self):
    self.sink = MockSink()
    logging.set_sink(self.sink)

  def tearDown(self):
    logging.set_sink(None)

  def test_positional_format(self):
    logging.log('Found {} networks for {}', 3, 'SKM')
    self.assertEqual(1, len(self.sink.records))
    self.assertEqual('Found 3 networks for SKM', self.sink.records[0][0])

  def test_structured_logfmt_kwargs(self):
    logging.log('api_request', url='https://data.rtt.io', status=200, duration_ms=842)
    self.assertEqual(1, len(self.sink.records))
    msg = self.sink.records[0][0]
    self.assertIn('api_request', msg)
    self.assertIn('status=200', msg)
    self.assertIn('duration_ms=842', msg)
    self.assertIn('url=https://data.rtt.io', msg)

  def test_unmatched_braces_in_message_do_not_crash(self):
    raw_json = 'Received raw json: {"unmatched": true}'
    logging.log(raw_json)
    self.assertEqual(1, len(self.sink.records))
    self.assertEqual(raw_json, self.sink.records[0][0])


if __name__ == '__main__':
  unittest.main()
