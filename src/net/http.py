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

from models import Response

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


def _connect_socket(host: str, port: int, timeout: int | None) -> socket.socket:
  """Opens a TCP connection to the host and port with timeout."""
  addr = socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM)[0]
  s = socket.socket(addr[0], socket.SOCK_STREAM, addr[2])
  try:
    try:
      s.connect(addr[-1])
    except OSError as e:
      if e.errno not in _CONNECT_UNDERWAY:
        raise

    p = select.poll()
    p.register(s, select.POLLOUT)
    timeout_ms = int(timeout * 1000) if timeout is not None else -1
    if not p.poll(timeout_ms):
      raise OSError(errno.ETIMEDOUT, 'Timed out connecting to socket.')

    if timeout is not None:
      s.settimeout(timeout)
    return s
  except Exception:
    s.close()
    raise


def _wrap_tls(s: socket.socket, host: str, ssl_context: ssl.SSLContext | None):
  """Wraps socket in TLS with SNI server hostname."""
  if ssl_context is not None:
    return ssl_context.wrap_socket(s, server_hostname=host)
  return ssl.wrap_socket(s, server_hostname=host)


def _send_request(
    s: socket.socket,
    method: str,
    path: str,
    host: str,
    headers: dict[str, str] | None,
    bearer_token: str | None,
    body: str | bytes | None,
) -> None:
  """Writes HTTP 1.0 request headers and payload to socket."""
  if body is not None and not isinstance(body, bytes):
    body = body.encode('utf-8')

  s.write('{} /{} HTTP/1.0\r\n'.format(method, path))
  s.write('Host: {}\r\n'.format(host))
  if bearer_token is not None:
    s.write('Authorization: Bearer {}\r\n'.format(bearer_token))
  for name, value in (headers or {}).items():
    s.write('{}: {}\r\n'.format(name, value))
  if body is not None:
    s.write('Content-Length: {}\r\n'.format(len(body)))
  s.write('Connection: close\r\n\r\n')
  if body is not None:
    s.write(body)


def _read_headers(s: socket.socket) -> tuple[int, dict[str, str], str | None]:
  """Parses HTTP status code, headers, and location redirect."""
  status_parts = s.readline().split(None, 2)
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
    buffer: memoryview | None,
    content_length_header: str | None,
) -> bytes | memoryview:
  """Reads response body into pre-allocated buffer or dynamically allocated bytes."""
  if buffer is not None:
    content_length = int(content_length_header or -1)
    if content_length > -1 and len(buffer) < content_length:
      raise ValueError(
          'Content length > buffer! Content-length: {} Buffer {}'.format(
              content_length, len(buffer)
          )
      )
    length = s.readinto(buffer)
    return buffer[:length]
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
  """Sends an HTTP request and returns Response with status, headers, and body."""
  proto, host, port, path = _parse_url(url)
  s = _connect_socket(host, port, timeout)

  try:
    if proto == 'https:':
      s = _wrap_tls(s, host, ssl_context)

    _send_request(s, method, path, host, headers, bearer_token, body)
    status, response_headers, redirect = _read_headers(s)
  except Exception:
    s.close()
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

  try:
    content = _read_body(s, buffer, response_headers.get('content-length'))
  finally:
    s.close()

  return Response(status, response_headers, content)
