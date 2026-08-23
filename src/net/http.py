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
"""Low-level socket and TLS HTTP transport."""

import errno
import select
import socket
import ssl
import time

import logging
from models import Response


def _ticks_ms() -> int:
  if hasattr(time, 'ticks_ms'):
    return time.ticks_ms()
  return int(time.time() * 1000)


def _ticks_diff(t1: int, t2: int) -> int:
  if hasattr(time, 'ticks_diff'):
    return time.ticks_diff(t1, t2)
  return t1 - t2

# What connect() reports when the connection is not refused but simply not
# finished being made, none of which is a failure: the poll that follows is
# what waits for it either way. EISCONN is in there because MicroPython
# retries a connect the system interrupted, and by the retry it has succeeded;
# the simulator provokes this every time, since collecting garbage on the
# render thread is what does the interrupting. It has no name in MicroPython's
# errno and no one number across platforms, hence both: 56 on a Mac, 106 on
# the board.
_CONNECT_UNDERWAY = (errno.EINPROGRESS, errno.EALREADY, 56, 106)
_REDIRECT_CODES = (301, 302, 303, 307, 308)

# Connection pool: (proto, host, port) -> (socket, cached_at_ms)
_conn_pool: dict[tuple[str, str, int], tuple[socket.socket, int]] = {}
_MAX_IDLE_MS = 60_000  # 60 seconds max idle keep-alive duration


def close_cached_connections():
  """Closes all open cached keep-alive connections."""
  global _conn_pool
  for sock, _ in _conn_pool.values():
    try:
      sock.close()
    except Exception:
      pass
  _conn_pool.clear()


# Alias for backward compatibility
close_cached_connection = close_cached_connections


def _parse_url(url: str) -> tuple[str, str, int, str]:
  """Extracts (protocol, host, port, path) from a URL string."""
  proto, _, host, path = url.split('/', 3)
  if proto == 'http:':
    port = 80
  elif proto == 'https:':
    port = 443
  else:
    raise ValueError('Unsupported protocol: ' + proto)

  if ':' in host:
    host, port_str = host.split(':', 1)
    port = int(port_str)
  return proto, host, port, path


def _connect_socket(
    host: str, port: int, timeout: int | None
) -> tuple[socket.socket, int, int]:
  """Opens a TCP connection to the host and port with timeout.

  Returns (socket, dns_ms, tcp_ms).
  """
  t0 = _ticks_ms()
  try:
    addr = socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM)[0]
  except Exception as e:
    elapsed = _ticks_diff(_ticks_ms(), t0)
    err = getattr(e, 'errno', errno.EHOSTUNREACH)
    raise OSError(err, f'{e} (during dns after {elapsed}ms)') from e

  t_dns = _ticks_ms()
  dns_ms = _ticks_diff(t_dns, t0)

  s = socket.socket(addr[0], socket.SOCK_STREAM, addr[2])
  try:
    try:
      s.connect(addr[-1])
    except OSError as e:
      if e.errno not in _CONNECT_UNDERWAY:
        elapsed = _ticks_diff(_ticks_ms(), t_dns)
        raise OSError(e.errno, f'{e} (during tcp_connect after {elapsed}ms)') from e

    p = select.poll()
    p.register(s, select.POLLOUT)
    timeout_ms = int(timeout * 1000) if timeout is not None else -1
    if not p.poll(timeout_ms):
      elapsed = _ticks_diff(_ticks_ms(), t_dns)
      raise OSError(errno.ETIMEDOUT, f'Timed out connecting to socket (during tcp_connect after {elapsed}ms)')

    if timeout is not None:
      s.settimeout(timeout)
    tcp_ms = _ticks_diff(_ticks_ms(), t_dns)
    return s, dns_ms, tcp_ms
  except Exception:
    s.close()
    raise


def _wrap_tls(s: socket.socket, host: str, ssl_context: ssl.SSLContext | None):
  """Wraps socket in TLS with SNI server hostname."""
  if ssl_context is not None:
    return ssl_context.wrap_socket(s, server_hostname=host)
  return ssl.wrap_socket(s, server_hostname=host)


def _get_connection(
    proto: str,
    host: str,
    port: int,
    timeout: int | None,
    ssl_context: ssl.SSLContext | None,
) -> tuple[socket.socket, int, int, int, bool]:
  """Gets a cached connection or connects a new TCP/TLS socket."""
  global _conn_pool
  key = (proto, host, port)
  now = _ticks_ms()
  if key in _conn_pool:
    sock, cached_at = _conn_pool.pop(key)
    if _ticks_diff(now, cached_at) <= _MAX_IDLE_MS:
      if timeout is not None:
        sock.settimeout(timeout)
      return sock, 0, 0, 0, True
    else:
      try:
        sock.close()
      except Exception:
        pass

  s, dns_ms, tcp_ms = _connect_socket(host, port, timeout)
  t_tls_start = _ticks_ms()
  if proto == 'https:':
    try:
      s = _wrap_tls(s, host, ssl_context)
    except Exception as e:
      elapsed = _ticks_diff(_ticks_ms(), t_tls_start)
      err = getattr(e, 'errno', errno.ECONNABORTED)
      raise OSError(err, f'{e} (during tls_handshake after {elapsed}ms)') from e
  tls_ms = _ticks_diff(_ticks_ms(), t_tls_start)
  return s, dns_ms, tcp_ms, tls_ms, False


def _send_request(
    s: socket.socket,
    method: str,
    path: str,
    host: str,
    headers: dict[str, str] | None,
    bearer_token: str | None,
    body: str | bytes | None,
) -> None:
  """Writes HTTP 1.1 request headers and payload to socket."""
  if body is not None and not isinstance(body, bytes):
    body = body.encode('utf-8')

  s.write('{} /{} HTTP/1.1\r\n'.format(method, path))
  s.write('Host: {}\r\n'.format(host))
  s.write('Connection: keep-alive\r\n')
  if bearer_token is not None:
    s.write('Authorization: Bearer {}\r\n'.format(bearer_token))
  for name, value in (headers or {}).items():
    s.write('{}: {}\r\n'.format(name, value))
  if body is not None:
    s.write('Content-Length: {}\r\n'.format(len(body)))
  s.write('\r\n')
  if body is not None:
    s.write(body)


def _read_headers(s: socket.socket) -> tuple[int, dict[str, str], str | None]:
  """Parses HTTP status code, headers, and location redirect."""
  status_line = s.readline()
  if not status_line:
    raise OSError(errno.ECONNRESET, 'Connection closed by server.')
  status_parts = status_line.split(None, 2)
  if len(status_parts) < 2:
    raise ValueError('HTTP error: bad status "{}"'.format(status_parts))
  status = int(status_parts[1])

  headers = {}
  redirect = None
  while True:
    line = s.readline()
    if not line or line == b'\r\n':
      break
    if line.startswith(b'Location:') and not 200 <= status <= 299:
      if status in _REDIRECT_CODES:
        redirect = str(line[10:-2], 'utf-8')
      else:
        raise NotImplementedError('Redirect %d not yet supported!' % status)
    else:
      line_str = str(line, 'utf-8')
      name, value = line_str.split(':', 1)
      headers[name.lower()] = value.strip()

  return status, headers, redirect


def _read_body(
    s: socket.socket,
    status: int,
    buffer: memoryview | bytearray | None,
    content_length_header: str | None,
) -> bytes | memoryview | bytearray:
  """Reads response body into pre-allocated buffer or dynamically allocated bytes."""
  if status in (204, 304):
    return buffer[:0] if buffer is not None else b''

  content_length = int(content_length_header) if content_length_header is not None else -1
  if content_length == 0:
    return buffer[:0] if buffer is not None else b''

  if buffer is not None:
    if content_length > len(buffer):
      raise ValueError(
          'Content length > buffer! Content-length: {} Buffer {}'.format(
              content_length, len(buffer)
          )
      )
    mv = memoryview(buffer)
    if content_length >= 0:
      total = 0
      while total < content_length:
        n = s.readinto(mv[total:content_length])
        if not n:
          break
        total += n
      return buffer[:total]
    length = s.readinto(mv)
    return buffer[:length]

  if content_length >= 0:
    chunks = []
    total = 0
    while total < content_length:
      chunk = s.read(content_length - total)
      if not chunk:
        break
      chunks.append(chunk)
      total += len(chunk)
    return b''.join(chunks)

  return s.read()


def http_request(
    url: str,
    *,
    method: str = 'GET',
    body: str | bytes | None = None,
    headers: dict[str, str] | None = None,
    bearer_token: str | None = None,
    timeout: int | None = None,
    buffer: memoryview | None = None,
    ssl_context: ssl.SSLContext | None = None,
) -> Response:
  """Sends an HTTP request and returns Response with status, headers, body, and timing."""
  start_ms = _ticks_ms()
  proto, host, port, path = _parse_url(url)
  reused_attempt = True
  s = None
  try:
    while True:
      try:
        s, dns_ms, tcp_ms, tls_ms, is_reused = _get_connection(
            proto, host, port, timeout, ssl_context
        )
        t_req_start = _ticks_ms()
        _send_request(s, method, path, host, headers, bearer_token, body)
        status, response_headers, redirect = _read_headers(s)
        ttfb_ms = _ticks_diff(_ticks_ms(), t_req_start)
        break
      except Exception as e:
        if is_reused and reused_attempt:
          reused_attempt = False
          try:
            s.close()
          except Exception:
            pass
          s = None
          continue
        raise

    if redirect is not None:
      s.close()
      return http_request(
          redirect,
          method=method,
          body=body,
          headers=headers,
          bearer_token=bearer_token,
          timeout=timeout,
          buffer=buffer,
          ssl_context=ssl_context,
      )

    t_body_start = _ticks_ms()
    try:
      try:
        content = _read_body(s, status, buffer, response_headers.get('content-length'))
      except Exception as e:
        elapsed = _ticks_diff(_ticks_ms(), t_body_start)
        err = getattr(e, 'errno', errno.ECONNRESET)
        raise OSError(err, f'{e} (during read_body after {elapsed}ms)') from e
    except Exception:
      s.close()
      raise
    body_ms = _ticks_diff(_ticks_ms(), t_body_start)

    can_keep_alive = (
        response_headers.get('connection', '').lower() != 'close'
        and response_headers.get('content-length') is not None
    )
    if can_keep_alive:
      _conn_pool[(proto, host, port)] = (s, _ticks_ms())
    else:
      s.close()

    duration_ms = _ticks_diff(_ticks_ms(), start_ms)
    response = Response(
        status,
        response_headers,
        content,
        duration_ms=duration_ms,
        dns_ms=dns_ms,
        tcp_ms=tcp_ms,
        tls_ms=tls_ms,
        ttfb_ms=ttfb_ms,
        body_ms=body_ms,
    )
    logging.log(
        'HTTP {} {} -> {} ({}, {} bytes)',
        method,
        url,
        response.status_code,
        response.timing_log(),
        len(response.content),
    )
    return response
  except Exception as e:
    if s is not None:
      try:
        s.close()
      except Exception:
        pass
    _conn_pool.pop((proto, host, port), None)
    logging.error('HTTP {} {} failed: {}', method, url, e)
    raise
