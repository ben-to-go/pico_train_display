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
"""Module for communicating with the Realtime Trains API.

This talks to the next generation API at https://data.rtt.io. Authentication is
a bearer token: the long-life token from the API portal is a refresh token,
which is exchanged for a short-lived access token.
"""

import collections
import errno
import json
import gc
import select
import socket
import ssl
import time
import _thread

import utils


_REQUEST_TIMEOUT = 10
_MAXRESPONSE_SIZE = 40 * 1024

# How far ahead to ask for departures. The panel only has room for a handful,
# but a short window leaves quiet stations looking empty.
_TIME_WINDOW_MINS = 180


class AuthError(ValueError):
  """Raised when the API rejects our token.

  Subclasses ValueError so that a bad token is retried by the caller rather
  than taking down the device.
  """


def _to_hhmm(timestamp: str) -> int:
  """'2026-08-15T18:30:00' -> 1830."""
  return int(timestamp[11:13] + timestamp[14:16])


def _to_epoch(timestamp: str) -> int:
  """'2026-08-15T18:30:00' -> seconds since the epoch."""
  return time.mktime((
      int(timestamp[0:4]),
      int(timestamp[5:7]),
      int(timestamp[8:10]),
      int(timestamp[11:13]),
      int(timestamp[14:16]),
      0,
      0,
      0,
  ))


# TODO: Make this a dataclass when MicroPython supports it.
class Response:

  def __init__(self, status_code: int, headers: dict[str, str], content):
    self._status_code = status_code
    self._headers = headers
    self._content = content

  @property
  def status_code(self):
    return self._status_code

  @property
  def content(self):
    return self._content

  @property
  def headers(self):
    return self._headers

  def __repr__(self) -> str:
    return 'Response(status_code={}, headers={}, content={}'.format(
        self.status_code, self.headers, self.content
    )


def _http_request(
    url: str,
    *,
    bearer_token: str | None = None,
    timeout: int | None = None,
    buffer: memoryview | None = None,
    ssl_context: ssl.SSLContext | None = None,
) -> Response:
  """Send HTTP GET request and return Response.

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
    s.connect(addr[-1])

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

    s.write('GET /{} HTTP/1.0\r\n'.format(path))
    s.write('Host: {}\r\n'.format(host))
    if bearer_token is not None:
      s.write('Authorization: Bearer {}\r\n'.format(bearer_token))
    s.write('Connection: close\r\n\r\n')

    http_status = s.readline().split(None, 2)
    if len(http_status) < 2:
      raise ValueError('HTTP error: bad status "{}"'.format(http_status))

    status = int(http_status[1])

    # Parse response headers.
    headers = {}
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
        headers[k] = v.strip()

  except Exception:
    # Always close socket on any exception
    s.close()
    raise

  if redirect is not None:
    s.close()
    _http_request(
        redirect,
        bearer_token=bearer_token,
        timeout=timeout,
        buffer=buffer,
        ssl_context=ssl_context,
    )

  try:
    if buffer is not None:
      content_length = int(headers.get('Content-Length', -1))
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

  return Response(status, headers, content)


# TODO: Make this a dataclass when MicroPython supports dataclasses
class Departure:
  """Class that encapsulates a train departure's data to be displayed."""

  def __init__(
      self,
      destination: str,
      departure_time: int,
      actual_departure_time: int,
      cancelled: bool,
  ):
    self._destination = destination
    self._departure_time = departure_time
    self._actual_departure_time = actual_departure_time
    self._cancelled = cancelled

  @property
  def destination(self) -> str:
    return self._destination

  @property
  def departure_time(self) -> int:
    return self._departure_time

  @property
  def actual_departure_time(self) -> int:
    return self._actual_departure_time

  @property
  def cancelled(self) -> bool:
    return self._cancelled

  def __repr__(self) -> str:
    return (
        'Departure(destination="{}", departure_time={},'
        'actual_departure_time={}, cancelled={})'
    ).format(
        self.destination,
        self.departure_time,
        self.actual_departure_time,
        self.cancelled,
    )

  def __eq__(self, other: object) -> bool:
    return (
        isinstance(other, Departure)
        and self.departure_time == other.departure_time
        and self.actual_departure_time == other.actual_departure_time
        and self.cancelled == other.cancelled
        and self.destination == other.destination
    )


Station = collections.namedtuple('Station', ('name', 'departures'))


def _lineup_url(endpoint: str, station: str, filter_to: str) -> str:
  return (
      endpoint
      + '/gb-nr/location?code={}&filterTo={}&timeWindow={}'.format(
          station, filter_to, _TIME_WINDOW_MINS
      )
  )


def _get_json(url: str, access_token: str, buffer, ssl_context):
  """GETs a URL and decodes the JSON body."""
  response = _http_request(
      url,
      bearer_token=access_token,
      timeout=_REQUEST_TIMEOUT,
      buffer=buffer,
      ssl_context=ssl_context,
  )
  if response.status_code == 401:
    raise AuthError('Token rejected by API.')
  if response.status_code != 200:
    raise ValueError('API request failed! {}'.format(response.status_code))
  # TODO: JSON decoding allocates a lot of small objects, which can put pressure
  # on memory fragmentation. Might be worth writing custom parsing of content.
  return json.loads(response.content)


def get_access_token(
    endpoint: str,
    refresh_token: str,
    *,
    buffer: memoryview | None = None,
    ssl_context: ssl.SSLContext | None = None,
) -> str:
  """Exchanges the long-life refresh token for a short-lived access token."""
  return _get_json(
      endpoint + '/api/get_access_token', refresh_token, buffer, ssl_context
  )['token']


def get_departures(
    station: str,
    destination: str,
    access_token: str,
    endpoint: str,
    *,
    min_departure_time: int = 0,
    buffer: memoryview | None = None,
    ssl_context: ssl.SSLContext | None = None,
) -> Station:
  """Requests set of departures from->to provided stations."""
  response_json = _get_json(
      _lineup_url(endpoint, station, destination),
      access_token,
      buffer,
      ssl_context,
  )

  now = time.mktime(utils.get_uk_time())
  departures = []
  for service in response_json.get('services') or []:
    departure = service['temporalData'].get('departure')
    if departure is None:
      continue  # An arrival, so nothing to show on a departure board.

    # Services that aren't advertised to the public have no advertised time.
    booked = departure.get('scheduleAdvertised')
    if booked is None:
      continue

    if min_departure_time > 0:
      if now + (min_departure_time * 60) > _to_epoch(booked):
        continue

    # We could have multiple destinations, so concatenate them together.
    destinations = ','.join(
        d['location']['description'] for d in service['destination']
    )

    departures.append(
        Departure(
            destinations,
            _to_hhmm(booked),
            _to_hhmm(departure.get('realtimeForecast') or booked),
            departure.get('isCancelled', False),
        )
    )

  results = Station(response_json['query']['location']['description'], departures)
  del response_json
  gc.collect()  # Explicitly delete and GC JSON objects.
  return results


class DepartureUpdater:
  """Class that updates departures for a given station periodically."""

  def __init__(
      self,
      station: str,
      destination: str,
      endpoint: str,
      token: str,
      min_departure_time: int,
  ):
    self._station = station
    self._destination = destination
    self._endpoint = endpoint
    self._token = token
    self._access_token = None
    self._min_departure_time = min_departure_time

    self._lock = _thread.allocate_lock()
    self._departures = Station(station, tuple())
    self._buffer = bytearray(_MAXRESPONSE_SIZE)
    self._memoryview = memoryview(self._buffer)
    self._ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)

  def _get_departures(self) -> Station:
    if self._access_token is None:
      self._access_token = get_access_token(
          self._endpoint,
          self._token,
          buffer=self._memoryview,
          ssl_context=self._ssl_context,
      )
    return get_departures(
        self._station,
        self._destination,
        self._access_token,
        self._endpoint,
        min_departure_time=self._min_departure_time,
        buffer=self._memoryview,
        ssl_context=self._ssl_context,
    )

  def update(self):
    """Updates the set of departures for a given station."""
    try:
      departures = self._get_departures()
    except AuthError:
      # Access tokens are short lived, so get a new one and try once more.
      self._access_token = None
      departures = self._get_departures()

    with self._lock:
      self._departures = departures

  def departures(self) -> tuple[Departure, ...]:
    """Returns tuple of departures."""
    with self._lock:
      return self._departures.departures

  def station(self) -> str:
    with self._lock:
      return self._departures.name
