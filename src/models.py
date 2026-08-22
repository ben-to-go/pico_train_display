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
"""Immutable domain models for train departures and HTTP responses."""

import collections


class Departure:
  """Class that encapsulates a single train departure's data."""

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
        'Departure(destination="{}", departure_time={}, '
        'actual_departure_time={}, cancelled={}, identity="{}")'
    ).format(
        self.destination,
        self.departure_time,
        self.actual_departure_time,
        self.cancelled,
        self.identity,
    )

  def __eq__(self, other: object) -> bool:
    return (
        isinstance(other, Departure)
        and self.departure_time == other.departure_time
        and self.actual_departure_time == other.actual_departure_time
        and self.cancelled == other.cancelled
        and self.destination == other.destination
        and self.identity == other.identity
    )


Station = collections.namedtuple('Station', ('name', 'departures'))

BoardSnapshot = collections.namedtuple(
    'BoardSnapshot',
    ('station', 'departures', 'calling_points', 'stale', 'fetched', 'last_updated_ms'),
)


class Response:
  """Encapsulates an HTTP response code, headers, and body."""

  def __init__(self, status_code: int, headers: dict[str, str], content):
    self._status_code = status_code
    self._headers = headers
    self._content = content

  @property
  def status_code(self) -> int:
    return self._status_code

  @property
  def content(self):
    return self._content

  @property
  def headers(self) -> dict[str, str]:
    return self._headers

  def __repr__(self) -> str:
    return 'Response(status_code={}, headers={}, content={})'.format(
        self.status_code, self.headers, self.content
    )
