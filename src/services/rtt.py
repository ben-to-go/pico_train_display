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
"""Realtime Trains (RTT) API client and response parser."""

import gc
import json
import ssl
import time

import fallback
import logging
from models import Departure, Station
from net.errors import AuthError, RateLimitError
import net.http as http
import utils

http_request = http.http_request

_REQUEST_TIMEOUT = 15
_TIME_WINDOW_MINS = 180


def to_hhmm(timestamp: str) -> int:
  """'2026-08-15T18:30:00' -> 1830."""
  return int(timestamp[11:13] + timestamp[14:16])


def to_epoch(timestamp: str) -> int:
  """'2026-08-15T18:30:00' -> seconds since the epoch."""
  return int(
      time.mktime((
          int(timestamp[0:4]),
          int(timestamp[5:7]),
          int(timestamp[8:10]),
          int(timestamp[11:13]),
          int(timestamp[14:16]),
          0,
          0,
          0,
          0,
      ))
  )


def lineup_url(endpoint: str, station: str, filter_to: str) -> str:
  return (
      endpoint
      + '/gb-nr/location?code={}&filterTo={}&timeWindow={}'.format(
          station, filter_to, _TIME_WINDOW_MINS
      )
  )


def get_json(
    url: str,
    access_token: str,
    buffer: memoryview | None = None,
    ssl_context: ssl.SSLContext | None = None,
):
  """GETs a URL and decodes the JSON body."""
  try:
    response = http_request(
        url,
        bearer_token=access_token,
        timeout=_REQUEST_TIMEOUT,
        buffer=buffer,
        ssl_context=ssl_context,
    )
  except Exception as e:
    logging.error('API GET {} failed: {}', url, e)
    raise

  logging.log(
      'API GET {} -> {} ({}, {} bytes)',
      url,
      response.status_code,
      response.timing_log(),
      len(response.content),
  )
  if response.status_code == 401:
    raise AuthError('Token rejected by API.')
  if response.status_code == 429:
    raise RateLimitError(int(response.headers.get('retry-after', 0) or 0))
  if response.status_code != 200:
    raise ValueError('API request failed! {}'.format(response.status_code))
  return json.loads(response.content)


def get_access_token(
    endpoint: str,
    refresh_token: str,
    *,
    buffer: memoryview | None = None,
    ssl_context: ssl.SSLContext | None = None,
) -> str:
  """Exchanges the long-life refresh token for a short-lived access token."""
  return get_json(
      endpoint + '/api/get_access_token', refresh_token, buffer, ssl_context
  )['token']


def parse_departures(response_json, min_departure_time: int = 0) -> Station:
  """Turns a location line-up response into the board to display."""
  now = time.mktime(utils.get_uk_time())
  departures = []
  services = response_json.get('services') or []
  for service in services:
    departure = service['temporalData'].get('departure')
    if departure is None:
      continue

    booked = departure.get('scheduleAdvertised')
    if booked is None:
      continue

    if min_departure_time > 0:
      if now + (min_departure_time * 60) > to_epoch(booked):
        continue

    destinations = ','.join(
        d['location']['description'] for d in service['destination']
    )
    metadata = service.get('scheduleMetadata', {})

    departures.append(
        Departure(
            destinations,
            to_hhmm(booked),
            to_hhmm(departure.get('realtimeForecast') or booked),
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
  gc.collect()
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
      get_json(
          lineup_url(endpoint, station, destination),
          access_token,
          buffer,
          ssl_context,
      ),
      min_departure_time,
  )


def parse_calling_points(response_json, station: str) -> tuple[str, ...]:
  """Stations a service calls at after the one we are standing at."""
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
  """Requests the calling points for a service."""
  return parse_calling_points(
      get_json(
          endpoint + '/rtt/service?uniqueIdentity=' + identity,
          access_token,
          buffer,
          ssl_context,
      ),
      station,
  )


def fallback_departures() -> Station:
  """The board baked into the firmware, for when the API can't be reached."""
  return parse_departures(json.loads(fallback.RESPONSE))


def fallback_calling_points(station: str) -> tuple[str, ...]:
  """Calling points for the first departure of the baked-in board."""
  return parse_calling_points(json.loads(fallback.SERVICE), station)
