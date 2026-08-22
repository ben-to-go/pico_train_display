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


from models import BoardSnapshot, Departure, Response, Station
from net.errors import AuthError, RateLimitError
from net.http import http_request

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


import services.rtt as rtt
from services.rtt import (
    fallback_calling_points,
    fallback_departures,
    lineup_url as _lineup_url,
    parse_calling_points,
    parse_departures,
    to_epoch as _to_epoch,
    to_hhmm as _to_hhmm,
)


def _get_json(url: str, access_token: str, buffer=None, ssl_context=None):
  rtt.http_request = http_request
  return rtt.get_json(url, access_token, buffer, ssl_context)


def get_access_token(
    endpoint: str,
    refresh_token: str,
    *,
    buffer: memoryview | None = None,
    ssl_context: ssl.SSLContext | None = None,
) -> str:
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
  return parse_departures(
      _get_json(
          _lineup_url(endpoint, station, destination),
          access_token,
          buffer,
          ssl_context,
      ),
      min_departure_time,
  )


def get_calling_points(
    identity: str,
    station: str,
    access_token: str,
    endpoint: str,
    *,
    buffer: memoryview | None = None,
    ssl_context: ssl.SSLContext | None = None,
) -> tuple[str, ...]:
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
