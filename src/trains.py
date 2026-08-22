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

import fallback
import logging
import utils


# What connect() reports when the connection is not refused but simply not
# finished being made, none of which is a failure: the poll that follows is
# what waits for it either way. EISCONN is in there because MicroPython
# retries a connect the system interrupted, and by the retry it has succeeded;
# the simulator provokes this every time, since collecting garbage on the
# render thread is what does the interrupting. It has no name in MicroPython's
# errno and no one number across platforms, hence both: 56 on a Mac, 106 on
# the board.
_CONNECT_UNDERWAY = (errno.EINPROGRESS, errno.EALREADY, 56, 106)

# Long enough for slow TLS handshakes, DNS resolution, and packet retries
# over 2.4GHz Wi-Fi without prematurely aborting.
_REQUEST_TIMEOUT = 15
_MAXRESPONSE_SIZE = 40 * 1024

# How far ahead to ask for departures. The panel only has room for a handful,
# but a short window leaves quiet stations looking empty.
_TIME_WINDOW_MINS = 180


class AuthError(ValueError):
  """Raised when the API rejects our token.

  Subclasses ValueError so that a bad token is retried by the caller rather
  than taking down the device.
  """


class RateLimitError(ValueError):
  """Raised when the API says we have asked too often.

  Carries the seconds the API asked us to wait, because retrying straight
  away is how a board that is over the limit stays over it.
  """

  def __init__(self, retry_after: int):
    super().__init__('Rate limited, retry after {}s'.format(retry_after))
    self.retry_after = retry_after


# Long enough that a night-long outage costs a handful of requests, short
# enough that the board is current again within half an hour of the API
# coming back.
# How long to wait after a failure, then after a second one in a row, and so
# on. A blip deserves another go almost straight away; something still broken
# after five minutes does not deserve asking every two.
_BACKOFF_SECS = (1, 5, 30, 120, 600, 1800)


def retry_wait(error, failures_in_a_row: int, interval: int) -> int:
  """How long to leave it after a failed update. Failures count from one.

  A 429 answers the question itself: the API sends the seconds to wait, and
  never less than the interval we would have waited anyway.

  Anything else is treated as a blip until it proves otherwise. The first
  retries come quickly, and if it keeps failing they stretch out, because the
  request budget is small and it is the recovery that needs it.
  """
  if isinstance(error, RateLimitError):
    return max(interval, error.retry_after)

  step = min(failures_in_a_row, len(_BACKOFF_SECS))
  return _BACKOFF_SECS[step - 1]


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
    http_request(
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


# TODO: Make this a dataclass when MicroPython supports dataclasses
class Departure:
  """Class that encapsulates a train departure's data to be displayed."""

  def __init__(
      self,
      destination: str,
      departure_time: int,
      actual_departure_time: int,
      cancelled: bool,
      identity: str = '',
  ):
    self._destination = destination
    self._departure_time = departure_time
    self._actual_departure_time = actual_departure_time
    self._cancelled = cancelled
    self._identity = identity

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

  @property
  def identity(self) -> str:
    """The service's API identity, used to look up its calling points."""
    return self._identity

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


def fallback_departures() -> Station:
  """The board baked into the firmware, for when the API can't be reached.

  Not filtered by min_departure_time: these departures are a fixed snapshot,
  so "departing in the next few minutes" means nothing for them.
  """
  return parse_departures(json.loads(fallback.RESPONSE))


def fallback_calling_points(station: str) -> tuple[str, ...]:
  """Calling points for the first departure of the baked-in board."""
  return parse_calling_points(json.loads(fallback.SERVICE), station)


def _lineup_url(endpoint: str, station: str, filter_to: str) -> str:
  return (
      endpoint
      + '/gb-nr/location?code={}&filterTo={}&timeWindow={}'.format(
          station, filter_to, _TIME_WINDOW_MINS
      )
  )


def _get_json(url: str, access_token: str, buffer, ssl_context):
  """GETs a URL and decodes the JSON body, saying so either way.

  Every request the firmware makes of the API comes through here, and each one
  says so: the board is allowed a hundred an hour, and counting what it spends
  meant counting the lines that happened to mention a request rather than the
  requests. The token travels in a header, so there is nothing in a URL worth
  keeping out of the log.
  """
  try:
    response = http_request(
        url,
        bearer_token=access_token,
        timeout=_REQUEST_TIMEOUT,
        buffer=buffer,
        ssl_context=ssl_context,
    )
  except Exception as e:
    # Answered by nothing at all, which is a request spent the same as any
    # other and the only kind that would otherwise go unmentioned.
    logging.log('API GET {} failed: {}', url, e)
    raise

  logging.log('API GET {} -> {}', url, response.status_code)
  if response.status_code == 401:
    raise AuthError('Token rejected by API.')
  if response.status_code == 429:
    # The API tells us how long to wait, and it is generous: minutes, not
    # seconds. Guessing shorter just spends requests we do not have.
    raise RateLimitError(int(response.headers.get('retry-after', 0) or 0))
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


def parse_departures(response_json, min_departure_time: int = 0) -> Station:
  """Turns a location line-up response into the board to display.

  Kept separate from fetching so that the departures baked into the firmware
  go through exactly the same parsing as live ones.
  """
  now = time.mktime(utils.get_uk_time())
  departures = []
  services = response_json.get('services') or []
  for service in services:
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

    metadata = service.get('scheduleMetadata', {})

    departures.append(
        Departure(
            destinations,
            _to_hhmm(booked),
            _to_hhmm(departure.get('realtimeForecast') or booked),
            departure.get('isCancelled', False),
            metadata.get('uniqueIdentity', ''),
        )
    )

  results = Station(response_json['query']['location']['description'], departures)
  logging.log(
      '{} services for {}, {} to show{}',
      len(services),
      results.name,
      len(departures),
      '' if min_departure_time <= 0
      else ' (skipping the next {} mins)'.format(min_departure_time),
  )
  del response_json
  gc.collect()  # Explicitly delete and GC JSON objects.
  return results


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
  return parse_departures(
      _get_json(
          _lineup_url(endpoint, station, destination),
          access_token,
          buffer,
          ssl_context,
      ),
      min_departure_time,
  )


def parse_calling_points(response_json, station: str) -> tuple[str, ...]:
  """Stations a service calls at after the one we are standing at.

  Kept separate from fetching so that the service baked into the firmware
  goes through exactly the same parsing as a live one.
  """
  names = []
  passed_us = False
  for location in response_json.get('service', {}).get('locations') or []:
    detail = location.get('location', {})
    if passed_us:
      names.append(detail.get('description', ''))
    elif station in (detail.get('shortCodes') or []):
      passed_us = True

  del response_json
  gc.collect()
  return tuple(names)


def get_calling_points(
    identity: str,
    station: str,
    access_token: str,
    endpoint: str,
    *,
    buffer: memoryview | None = None,
    ssl_context: ssl.SSLContext | None = None,
) -> tuple[str, ...]:
  """Requests the calling points for a service.

  A separate request per service, which is why only the first departure's
  calling points are ever fetched.
  """
  return parse_calling_points(
      _get_json(
          endpoint + '/rtt/service?uniqueIdentity=' + identity,
          access_token,
          buffer,
          ssl_context,
      ),
      station,
  )


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
    self._stale = True
    self._fetched = False
    self._calling_points = ()
    self._calling_points_identity = None
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
    """Updates the set of departures for a given station.

    Marks the board stale and re-raises if the fetch fails. Whatever was last
    fetched stays on the display; if nothing ever has, the departures baked
    into the firmware are shown instead.
    """
    try:
      try:
        departures = self._get_departures()
      except AuthError:
        # Access tokens are short lived, so get a new one and try once more.
        logging.log('The API rejected our access token, asking for another.')
        self._access_token = None
        departures = self._get_departures()
    except Exception:
      with self._lock:
        first_failure = not self._stale
        never_fetched = not self._fetched
        self._stale = True
        if never_fetched:
          self._departures = fallback_departures()
          self._calling_points = fallback_calling_points(self._station)
      if never_fetched:
        logging.log(
            'Nothing has ever loaded for {}, so the board is the one baked '
            'into the firmware.', self._station)
      elif first_failure:
        logging.log(
            'Departures for {} are now stale, still showing the last ones '
            'that loaded.', self._station)
      raise

    with self._lock:
      # Stale and fetched before, so this is a recovery rather than the first
      # board of the day: an updater starts stale, having nothing to show yet.
      recovered = self._stale and self._fetched
      self._departures = departures
      self._stale = False
      self._fetched = True
    if recovered:
      logging.log('Departures for {} are current again.', self._station)

    self._update_calling_points(departures)

  def _update_calling_points(self, board: Station):
    """Fetches calling points for the first departure, if they've changed.

    One extra request per board at most, and none at all while the same train
    is still at the top, which keeps well clear of the API's rate limit.
    """
    identity = board.departures[0].identity if board.departures else None
    if identity == self._calling_points_identity:
      return

    points = ()
    if identity:
      try:
        points = get_calling_points(
            identity,
            self._station,
            self._access_token,
            self._endpoint,
            buffer=self._memoryview,
            ssl_context=self._ssl_context,
        )
      except Exception as e:
        # Rate limited, or the service went away. The board is still worth
        # showing, just without the calling points.
        logging.log(
            'No calling points for service {}, showing the board without '
            'them: {}', identity, e)
        logging.exception(e)
        identity = None

    with self._lock:
      self._calling_points = points
      self._calling_points_identity = identity

  def calling_points(self) -> tuple[str, ...]:
    """Stations the first departure calls at."""
    with self._lock:
      return self._calling_points

  def stale(self) -> bool:
    """Whether the departures on show failed to refresh."""
    with self._lock:
      return self._stale

  def departures(self) -> tuple[Departure, ...]:
    """Returns tuple of departures."""
    with self._lock:
      return self._departures.departures

  def station(self) -> str:
    with self._lock:
      return self._departures.name
