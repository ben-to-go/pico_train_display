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
  """Send an HTTP request and return Response.

  This is heavily influenced by urequests.get(), with a couple of modifications:
    - Simplify code by not supporting sending params with GET
    - Support passing a pre-allocated buffer for response body, to help
      alleviate memory fragmentation.
    - Fix for transient EINPROGRESS error thrown from connect when using
      timeouts.
  """
  proto, _, host, path = url.split('/', 3)
  redirect = None

  if proto == 'http:':
    port = 80
  elif proto == 'https:':
    port = 443
  else:
    raise ValueError('Unsupported protocol: ' + proto)

  if ':' in host:
    host, port = host.split(':', 1)
    port = int(port)

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
    result = p.poll(timeout if timeout is not None else -1)
    if not result:
      raise OSError(errno.ETIMEDOUT, 'Timed out connecting to socket.')

    if timeout is not None:
      s.settimeout(timeout)

    if proto == 'https:':
      if ssl_context is not None:
        s = ssl_context.wrap_socket(s, server_hostname=host)
      else:
        s = ssl.wrap_socket(s, server_hostname=host)

    if body is not None and not isinstance(body, bytes):
      body = body.encode('utf-8')

    s.write('{} /{} HTTP/1.0\r\n'.format(method, path))
    s.write('Host: {}\r\n'.format(host))
    if bearer_token is not None:
      s.write('Authorization: Bearer {}\r\n'.format(bearer_token))
    for name, value in (headers or {}).items():
      s.write('{}: {}\r\n'.format(name, value))
    # Length rather than chunked, because the body is already a string in
    # memory and a server that only speaks HTTP/1.0 would not take chunks.
    if body is not None:
      s.write('Content-Length: {}\r\n'.format(len(body)))
    s.write('Connection: close\r\n\r\n')
    if body is not None:
      s.write(body)

    http_status = s.readline().split(None, 2)
    if len(http_status) < 2:
      raise ValueError('HTTP error: bad status "{}"'.format(http_status))

    status = int(http_status[1])

    # Parse response headers.
    response_headers = {}
    while True:
      header = s.readline()
      if not header or header == b'\r\n':
        break
      if header.startswith(b'Location:') and not 200 <= status <= 299:
        if status in [301, 302, 303, 307, 308]:
          redirect = str(header[10:-2], 'utf-8')
        else:
          raise NotImplementedError('Redirect %d not yet supported!' % status)
      else:
        header = str(header, 'utf-8')
        k, v = header.split(':', 1)
        # Lowercased, because header names are case insensitive and the only
        # thing that reads one wants to find it whatever the server sent.
        response_headers[k.lower()] = v.strip()

  except Exception:
    # Always close socket on any exception
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
    if buffer is not None:
      content_length = int(response_headers.get('Content-Length', -1))
      if content_length > -1 and len(buffer) < content_length:
        raise ValueError(
            'Content length > buffer! Content-length: {} Buffer {}'.format(
                content_length, len(buffer)
            )
        )
      else:
        length = s.readinto(buffer)
        content = buffer[:length]
    else:
      content = s.read()
  finally:
    s.close()

  return Response(status, response_headers, content)
