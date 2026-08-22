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
"""Module for communicating with the Realtime Trains API."""

import ssl

import logging
from models import BoardSnapshot, Departure, Response, Station
from net.errors import AuthError, RateLimitError
from net.http import http_request
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
from state import StateController

_REQUEST_TIMEOUT = 15
_MAXRESPONSE_SIZE = 40 * 1024
_TIME_WINDOW_MINS = 180

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
      *,
      state_controller: StateController | None = None,
  ):
    self._station = station
    self._destination = destination
    self._endpoint = endpoint
    self._token = token
    self._access_token = None
    self._min_departure_time = min_departure_time
    self._state = (
        state_controller
        if state_controller is not None
        else StateController(station)
    )

    self._calling_points_identity = None
    self._buffer = bytearray(_MAXRESPONSE_SIZE)
    self._memoryview = memoryview(self._buffer)
    self._ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)

  @property
  def state(self) -> StateController:
    return self._state

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
      never_fetched, first_failure = self._state.mark_stale(
          fallback_departures(), fallback_calling_points(self._station)
      )
      if never_fetched:
        logging.log(
            'Nothing has ever loaded for {}, so the board is the one baked '
            'into the firmware.', self._station)
      elif first_failure:
        logging.log(
            'Departures for {} are now stale, still showing the last ones '
            'that loaded.', self._station)
      raise

    points = self._fetch_calling_points(departures)
    recovered = self._state.update_departures(departures, points)
    if recovered:
      logging.log('Departures for {} are current again.', self._station)

  def _fetch_calling_points(self, board: Station) -> tuple[str, ...]:
    """Fetches calling points for the first departure, if they've changed.

    One extra request per board at most, and none at all while the same train
    is still at the top, which keeps well clear of the API's rate limit.
    """
    identity = board.departures[0].identity if board.departures else None
    if identity == self._calling_points_identity:
      return self._state.calling_points()

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

    self._calling_points_identity = identity
    return points

  def calling_points(self) -> tuple[str, ...]:
    """Stations the first departure calls at."""
    return self._state.calling_points()

  def stale(self) -> bool:
    """Whether the departures on show failed to refresh."""
    return self._state.stale()

  def departures(self) -> tuple[Departure, ...]:
    """Returns tuple of departures."""
    return self._state.departures()

  def station(self) -> str:
    return self._state.station()
