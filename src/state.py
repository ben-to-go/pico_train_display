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
"""State management and thread-safe cross-core snapshot swapping."""

import _thread
import time

from models import BoardSnapshot, Departure, Station


def _ticks_ms() -> int:
  if hasattr(time, 'ticks_ms'):
    return time.ticks_ms()
  return int(time.time() * 1000)


class StateController:
  """Thread-safe state controller coordinating Core 0 and Core 1.

  Core 0 writes new immutable BoardSnapshots upon network fetch completions.
  Core 1 reads the current BoardSnapshot for jitter-free display rendering
  without blocking on network I/O or lock contention.
  """

  def __init__(
      self,
      station: str = '',
      *,
      telemetry_enabled: bool = True,
      mock_mode: bool = False,
  ):
    self._lock = _thread.allocate_lock()
    self._snapshot = BoardSnapshot(
        station=station,
        departures=(),
        calling_points=(),
        stale=True,
        fetched=False,
        last_updated_ms=0,
    )
    # Debug / feature toggles
    self.telemetry_enabled = telemetry_enabled
    self.mock_mode = mock_mode

  @property
  def snapshot(self) -> BoardSnapshot:
    """Returns the current immutable snapshot (safe for Core 1 reader)."""
    with self._lock:
      return self._snapshot

  def set_snapshot(self, snapshot: BoardSnapshot) -> None:
    """Atomically swaps the current immutable snapshot (from Core 0 writer)."""
    with self._lock:
      self._snapshot = snapshot

  def mark_stale(
      self,
      fallback_board: Station | None = None,
      fallback_points: tuple[str, ...] = (),
  ) -> tuple[bool, bool]:
    """Marks current state stale on fetch failure, returning (never_fetched, first_failure)."""
    with self._lock:
      current = self._snapshot
      first_failure = not current.stale
      never_fetched = not current.fetched
      if never_fetched and fallback_board is not None:
        self._snapshot = BoardSnapshot(
            station=fallback_board.name,
            departures=fallback_board.departures,
            calling_points=fallback_points,
            stale=True,
            fetched=False,
            last_updated_ms=current.last_updated_ms,
        )
      else:
        self._snapshot = BoardSnapshot(
            station=current.station,
            departures=current.departures,
            calling_points=current.calling_points,
            stale=True,
            fetched=current.fetched,
            last_updated_ms=current.last_updated_ms,
        )
      return never_fetched, first_failure

  def update_departures(
      self, board: Station, calling_points: tuple[str, ...]
  ) -> bool:
    """Updates departures & calling points, returning True if state recovered from stale."""
    with self._lock:
      current = self._snapshot
      recovered = current.stale and current.fetched
      self._snapshot = BoardSnapshot(
          station=board.name,
          departures=board.departures,
          calling_points=calling_points,
          stale=False,
          fetched=True,
          last_updated_ms=_ticks_ms(),
      )
      return recovered

  # Protocol methods for MainWidget compatibility
  def departures(self) -> tuple[Departure, ...]:
    with self._lock:
      return self._snapshot.departures

  def calling_points(self) -> tuple[str, ...]:
    with self._lock:
      return self._snapshot.calling_points

  def stale(self) -> bool:
    with self._lock:
      return self._snapshot.stale

  def station(self) -> str:
    with self._lock:
      return self._snapshot.station
