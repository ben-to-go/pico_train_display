import os
import select
import socket
import ssl
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import firmware_path  # noqa: E402,F401

from models import Response
from net import http


class MockSocket:

  def __init__(self, responses=None):
    default_resp = b'HTTP/1.0 200 OK\r\nContent-Length: 13\r\n\r\n{"status":"ok"}'
    self.responses = list(responses or [default_resp])
    self.written = bytearray()
    self.timeout = None
    self.closed = False

  def connect(self, addr):
    pass

  def settimeout(self, t):
    self.timeout = t

  def write(self, data):
    self.written.extend(data if isinstance(data, (bytes, bytearray)) else data.encode('utf-8'))

  def readline(self):
    if not self.responses:
      return b''
    resp = self.responses[0]
    idx = resp.find(b'\r\n')
    if idx != -1:
      line = resp[:idx + 2]
      self.responses[0] = resp[idx + 2:]
      return line
    line = self.responses.pop(0)
    return line

  def readinto(self, buf):
    if not self.responses:
      return 0
    data = self.responses.pop(0)
    n = min(len(buf), len(data))
    buf[:n] = data[:n]
    return n

  def read(self, size=-1):
    if not self.responses:
      return b''
    if size is None or size < 0:
      return self.responses.pop(0)
    data = self.responses[0]
    if len(data) <= size:
      return self.responses.pop(0)
    chunk = data[:size]
    self.responses[0] = data[size:]
    return chunk

  def close(self):
    self.closed = True


class HttpTimingComprehensiveTest(unittest.TestCase):

  def test_response_timing_properties_and_log(self):
    resp = Response(
        status_code=200,
        headers={'content-type': 'application/json'},
        content=b'{"trains":[]}',
        duration_ms=1250,
        dns_ms=35,
        tcp_ms=55,
        tls_ms=750,
        ttfb_ms=310,
        body_ms=100,
    )
    self.assertEqual(200, resp.status_code)
    self.assertEqual(b'{"trains":[]}', resp.content)
    self.assertEqual(1250, resp.duration_ms)
    self.assertEqual(35, resp.dns_ms)
    self.assertEqual(55, resp.tcp_ms)
    self.assertEqual(750, resp.tls_ms)
    self.assertEqual(310, resp.ttfb_ms)
    self.assertEqual(100, resp.body_ms)
    self.assertEqual(
        '1250ms (dns=35ms tcp=55ms tls=750ms ttfb=310ms body=100ms)',
        resp.timing_log(),
    )

  def test_http_request_captures_all_timing_phases(self):
    mock_sock = MockSocket([
        b'HTTP/1.0 200 OK\r\nContent-Length: 18\r\n\r\n{"departures":[]}',
    ])

    orig_socket = socket.socket
    orig_getaddrinfo = socket.getaddrinfo
    orig_wrap_socket = getattr(ssl, 'wrap_socket', None)
    orig_poll = select.poll

    class MockPoll:
      def register(self, *a): pass
      def poll(self, timeout): return [1]

    socket.socket = lambda *a, **k: mock_sock
    socket.getaddrinfo = lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 0, '', ('127.0.0.1', 443))]
    ssl.wrap_socket = lambda s, **k: s
    select.poll = lambda: MockPoll()

    self.addCleanup(setattr, socket, 'socket', orig_socket)
    self.addCleanup(setattr, socket, 'getaddrinfo', orig_getaddrinfo)
    if orig_wrap_socket is not None:
      self.addCleanup(setattr, ssl, 'wrap_socket', orig_wrap_socket)
    elif hasattr(ssl, 'wrap_socket'):
      self.addCleanup(delattr, ssl, 'wrap_socket')
    self.addCleanup(setattr, select, 'poll', orig_poll)

    response = http.http_request('https://data.rtt.io/gb-nr/location?code=SKM', timeout=15)
    self.assertEqual(200, response.status_code)
    self.assertEqual(b'{"departures":[]}', response.content)
    self.assertGreaterEqual(response.duration_ms, 0)
    self.assertGreaterEqual(response.dns_ms, 0)
    self.assertGreaterEqual(response.tcp_ms, 0)
    self.assertGreaterEqual(response.tls_ms, 0)
    self.assertGreaterEqual(response.ttfb_ms, 0)
    self.assertGreaterEqual(response.body_ms, 0)
    self.assertIn('dns=', response.timing_log())
    self.assertIn('tcp=', response.timing_log())
    self.assertIn('tls=', response.timing_log())
    self.assertIn('ttfb=', response.timing_log())
    self.assertIn('body=', response.timing_log())

  def test_connect_socket_returns_socket_and_timings(self):
    mock_sock = MockSocket()
    orig_socket = socket.socket
    orig_getaddrinfo = socket.getaddrinfo
    orig_poll = select.poll

    class MockPoll:
      def register(self, *a): pass
      def poll(self, timeout): return [1]

    socket.socket = lambda *a, **k: mock_sock
    socket.getaddrinfo = lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 0, '', ('127.0.0.1', 80))]
    select.poll = lambda: MockPoll()

    self.addCleanup(setattr, socket, 'socket', orig_socket)
    self.addCleanup(setattr, socket, 'getaddrinfo', orig_getaddrinfo)
    self.addCleanup(setattr, select, 'poll', orig_poll)

    s, dns_ms, tcp_ms = http._connect_socket('example.com', 80, timeout=15)
    self.assertEqual(mock_sock, s)
    self.assertGreaterEqual(dns_ms, 0)
    self.assertGreaterEqual(tcp_ms, 0)

  def test_redirect_request_timings(self):
    sock_301 = MockSocket([
        b'HTTP/1.1 301 Moved Permanently\r\nLocation: https://data.rtt.io/dest\r\nConnection: close\r\nContent-Length: 0\r\n\r\n',
    ])
    sock_200 = MockSocket([
        b'HTTP/1.1 200 OK\r\nConnection: close\r\nContent-Length: 2\r\n\r\n{}',
    ])

    sockets = [sock_301, sock_200]
    orig_socket = socket.socket
    orig_getaddrinfo = socket.getaddrinfo
    orig_wrap_socket = getattr(ssl, 'wrap_socket', None)
    orig_poll = select.poll

    class MockPoll:
      def register(self, *a): pass
      def poll(self, timeout): return [1]

    socket.socket = lambda *a, **k: sockets.pop(0)
    socket.getaddrinfo = lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 0, '', ('127.0.0.1', 443))]
    ssl.wrap_socket = lambda s, **k: s
    select.poll = lambda: MockPoll()

    self.addCleanup(setattr, socket, 'socket', orig_socket)
    self.addCleanup(setattr, socket, 'getaddrinfo', orig_getaddrinfo)
    if orig_wrap_socket is not None:
      self.addCleanup(setattr, ssl, 'wrap_socket', orig_wrap_socket)
    elif hasattr(ssl, 'wrap_socket'):
      self.addCleanup(delattr, ssl, 'wrap_socket')
    self.addCleanup(setattr, select, 'poll', orig_poll)

    response = http.http_request('https://data.rtt.io/orig', timeout=15)
    self.assertEqual(200, response.status_code)
    self.assertEqual(b'{}', response.content)
    self.assertGreaterEqual(response.duration_ms, 0)
    self.assertTrue(sock_301.closed)
    self.assertTrue(sock_200.closed)

  def test_dns_failure_annotates_phase(self):
    import socket
    orig_getaddrinfo = socket.getaddrinfo

    def failing_getaddrinfo(*a, **k):
      raise OSError(socket.EAI_NONAME, 'Name or service not known')

    socket.getaddrinfo = failing_getaddrinfo
    self.addCleanup(setattr, socket, 'getaddrinfo', orig_getaddrinfo)

    with self.assertRaises(OSError) as ctx:
      http._connect_socket('invalid.host.xyz', 80, timeout=5)
    self.assertIn('during dns', str(ctx.exception))

  def test_tcp_timeout_annotates_phase(self):
    import socket
    import select

    mock_sock = MockSocket()
    orig_socket = socket.socket
    orig_getaddrinfo = socket.getaddrinfo
    orig_poll = select.poll

    class TimeoutPoll:
      def register(self, *a): pass
      def poll(self, timeout): return []

    socket.socket = lambda *a, **k: mock_sock
    socket.getaddrinfo = lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 0, '', ('127.0.0.1', 80))]
    select.poll = lambda: TimeoutPoll()

    self.addCleanup(setattr, socket, 'socket', orig_socket)
    self.addCleanup(setattr, socket, 'getaddrinfo', orig_getaddrinfo)
    self.addCleanup(setattr, select, 'poll', orig_poll)

    with self.assertRaises(OSError) as ctx:
      http._connect_socket('example.com', 80, timeout=1)
    self.assertIn('during tcp_connect', str(ctx.exception))

  def test_keep_alive_reuses_connection(self):
    mock_sock = MockSocket([
        b'HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}',
        b'HTTP/1.1 200 OK\r\nContent-Length: 15\r\n\r\n{"status":"ok"}',
    ])

    orig_socket = socket.socket
    orig_getaddrinfo = socket.getaddrinfo
    orig_wrap_socket = getattr(ssl, 'wrap_socket', None)
    orig_poll = select.poll

    class MockPoll:
      def register(self, *a): pass
      def poll(self, timeout): return [1]

    socket_created_count = 0
    def mock_socket_factory(*a, **k):
      nonlocal socket_created_count
      socket_created_count += 1
      return mock_sock

    socket.socket = mock_socket_factory
    socket.getaddrinfo = lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 0, '', ('127.0.0.1', 443))]
    ssl.wrap_socket = lambda s, **k: s
    select.poll = lambda: MockPoll()

    self.addCleanup(setattr, socket, 'socket', orig_socket)
    self.addCleanup(setattr, socket, 'getaddrinfo', orig_getaddrinfo)
    if orig_wrap_socket is not None:
      self.addCleanup(setattr, ssl, 'wrap_socket', orig_wrap_socket)
    elif hasattr(ssl, 'wrap_socket'):
      self.addCleanup(delattr, ssl, 'wrap_socket')
    self.addCleanup(setattr, select, 'poll', orig_poll)
    self.addCleanup(http.close_cached_connection)

    # Request 1: Fresh connect
    resp1 = http.http_request('https://data.rtt.io/api/first', timeout=15)
    self.assertEqual(200, resp1.status_code)
    self.assertEqual(b'{}', resp1.content)
    self.assertEqual(1, socket_created_count)

    # Request 2: Reused connection (same host)
    resp2 = http.http_request('https://data.rtt.io/api/second', timeout=15)
    self.assertEqual(200, resp2.status_code)
    self.assertEqual(b'{"status":"ok"}', resp2.content)
    self.assertEqual(1, socket_created_count)
    self.assertEqual(0, resp2.dns_ms)
    self.assertEqual(0, resp2.tcp_ms)
    self.assertEqual(0, resp2.tls_ms)

  def test_keep_alive_reconnects_when_server_closes_idle_connection(self):
    sock1 = MockSocket([
        b'HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}',
        # Next read from sock1 returns EOF (server closed connection)
        b'',
    ])
    sock2 = MockSocket([
        b'HTTP/1.1 200 OK\r\nContent-Length: 15\r\n\r\n{"status":"ok"}',
    ])

    sockets = [sock1, sock2]
    orig_socket = socket.socket
    orig_getaddrinfo = socket.getaddrinfo
    orig_wrap_socket = getattr(ssl, 'wrap_socket', None)
    orig_poll = select.poll

    class MockPoll:
      def register(self, *a): pass
      def poll(self, timeout): return [1]

    socket.socket = lambda *a, **k: sockets.pop(0)
    socket.getaddrinfo = lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 0, '', ('127.0.0.1', 443))]
    ssl.wrap_socket = lambda s, **k: s
    select.poll = lambda: MockPoll()

    self.addCleanup(setattr, socket, 'socket', orig_socket)
    self.addCleanup(setattr, socket, 'getaddrinfo', orig_getaddrinfo)
    if orig_wrap_socket is not None:
      self.addCleanup(setattr, ssl, 'wrap_socket', orig_wrap_socket)
    elif hasattr(ssl, 'wrap_socket'):
      self.addCleanup(delattr, ssl, 'wrap_socket')
    self.addCleanup(setattr, select, 'poll', orig_poll)
    self.addCleanup(http.close_cached_connection)

    # Request 1 on sock1
    resp1 = http.http_request('https://data.rtt.io/api/first', timeout=15)
    self.assertEqual(200, resp1.status_code)

    # Request 2: sock1 returns EOF, client discards sock1 and connects sock2
    resp2 = http.http_request('https://data.rtt.io/api/second', timeout=15)
    self.assertEqual(200, resp2.status_code)
    self.assertEqual(b'{"status":"ok"}', resp2.content)
    self.assertTrue(sock1.closed)


if __name__ == '__main__':
  unittest.main()
