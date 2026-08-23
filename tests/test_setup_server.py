import asyncio
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import firmware_path  # noqa: E402,F401

from setup import server


class MockStreamWriter:

  def __init__(self):
    self.data = bytearray()
    self.drained = False
    self.closed = False

  def write(self, b: bytes):
    self.data.extend(b)

  async def drain(self):
    self.drained = True

  def close(self):
    self.closed = True

  async def wait_closed(self):
    pass


class MockStreamReader:

  def __init__(self, lines):
    self.lines = list(lines)
    self.raw_data = b''.join(lines)

  async def readline(self):
    if self.lines:
      return self.lines.pop(0)
    return b''

  async def readexactly(self, n):
    res = self.raw_data[:n]
    self.raw_data = self.raw_data[n:]
    return res


def _run(coro):
  try:
    while True:
      coro.send(None)
  except StopIteration as e:
    return e.value


class SetupServerTest(unittest.TestCase):

  def test_parse_json_request_valid_and_invalid_bool(self):
    parsed = server._parse_json_request({
        'display[flip]:bool': 'true',
        'debug[log]:bool': 'off',
        'rtt[update_interval]:int': '120',
        'station': 'SKM',
    })
    self.assertEqual(True, parsed['display']['flip'])
    self.assertEqual(False, parsed['debug']['log'])
    self.assertEqual(120, parsed['rtt']['update_interval'])
    self.assertEqual('SKM', parsed['station'])

    with self.assertRaises(ValueError):
      server._parse_json_request({'display[flip]:bool': 'invalid_bool'})

  def test_parse_headers_with_and_without_space(self):
    reader = MockStreamReader([
        b'Host:192.168.4.1\r\n',
        b'Content-Type: application/json\r\n',
        b'Content-Length:  42 \r\n',
        b'\r\n',
    ])
    headers = _run(server._parse_headers(reader))
    self.assertEqual('192.168.4.1', headers['host'])
    self.assertEqual('application/json', headers['content-type'])
    self.assertEqual('42', headers['content-length'])

  def test_write_response_content_type_header(self):
    writer = MockStreamWriter()
    _run(
        server._write_response(
            writer,
            200,
            content=b'<h1>Hello</h1>',
            content_type='text/html',
        )
    )
    self.assertTrue(writer.drained)
    self.assertTrue(writer.closed)
    response = writer.data.decode('utf-8')
    self.assertIn('Content-Type: text/html\r\n', response)
    self.assertIn('Content-Length: 14\r\n', response)
    self.assertNotIn('type', response)


if __name__ == '__main__':
  unittest.main()
