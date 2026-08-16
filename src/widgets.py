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
"""Collection of UI widgets for rendering to a display."""

import time

import display
import fonts
import trains


# The next train carries no number and the board counts no further than 3rd,
# as a real indicator does.
_ORDINALS = ('', '2nd', '3rd')
_CALLING_AT = 'Calling at: '
_NO_DEPARTURES = 'No more departures today'
_ELLIPSIS = '..'

# Gap between the columns of a departure row.
_COLUMN_GAP = 4

# The blank run between the end of the calling points and the start of them
# coming round again. How fast they move is display.scroll_speed, in pixels a
# second rather than per frame, so the refresh rate can change without the
# stations suddenly reading at a different pace.
_SCROLL_GAP = 24

# How long each of the later departures gets on the third line.
_ALTERNATE_SECONDS = 8

# Pixels square, in the bottom right corner.
_STALE_DOT_SIZE = 1


def _distribute(height: int, row_heights) -> tuple[int, ...]:
  """Spreads rows down the screen with the space shared out evenly.

  The gap above the first row matches the one below the last, so the board
  doesn't look bunched against the top.
  """
  free = height - sum(row_heights)
  slots = len(row_heights) + 1  # above each row, and below the last one
  tops = []
  y = 0
  for i, row_height in enumerate(row_heights):
    y += free * (i + 1) // slots - free * i // slots
    tops.append(y)
    y += row_height
  return tuple(tops)


def _truncate(font: fonts.Font, text: str, max_width: int) -> str:
  """Shortens text with a trailing '..' until it fits."""
  width = font.calculate_bounds(text)[0]
  if width <= max_width:
    return text

  ellipsis = font.calculate_bounds(_ELLIPSIS)[0]
  while text and width + ellipsis > max_width:
    width -= font.calculate_bounds(text[-1])[0]
    text = text[:-1]
  return text + _ELLIPSIS


def _time_to_str(hh_mm: int) -> str:
  """Helper to convert integer time [h]h[m]m to HH:mm string."""
  hh, mm = divmod(hh_mm, 100)
  return '{:0>2}:{:0>2}'.format(hh, mm)


class Widget:
  """Base class for all Widgets"""

  def __init__(self, screen: display.Display):
    self._screen = screen

  def render(self, x: int, y: int, w: int, h: int) -> bool:
    """Renders the widget to the display.

    Returns whether the display needs to flush the back buffer to the display.
    """
    ...


class ClockWidget(Widget):
  """The clock, alone and centred on the bottom row of the board."""

  def __init__(self, screen: display.Display, font: fonts.Font):
    super().__init__(screen)
    self._font = font
    self._bounds = font.calculate_bounds('00:00:00')
    self._last_update = None

  def bounds(self):
    return self._bounds

  def render(self, now: tuple[int, ...], x: int, y: int, w: int, h: int):
    current_update = now[3:6]
    if self._last_update is not None and self._last_update == current_update:
      return False

    self._screen.fill_rect(x, y, w, h, 0)
    self._font.render_text(
        '{:02d}:{:02d}:{:02d}'.format(now[3], now[4], now[5]), self._screen, x, y
    )

    self._last_update = current_update
    return True


class NoDeparturesWidget(Widget):
  """What a platform indicator shows once the last train has gone."""

  def __init__(self, screen: display.Display, font: fonts.Font):
    super().__init__(screen)
    self._font = font
    self._bounds = font.calculate_bounds(_NO_DEPARTURES)
    self._rendered = False

  def bounds(self):
    return self._bounds

  def render(self, x: int, y: int, w: int, h: int):
    if self._rendered:
      return False

    self._font.render_text(
        _NO_DEPARTURES, self._screen, x + (w - self._bounds[0]) // 2, y
    )
    self._rendered = True
    return True

  def clear(self):
    self._rendered = False


class ScrollingTextWidget(Widget):
  """A fixed label with a line of text scrolling right to left beside it.

  As on a real indicator, "Calling at:" stays put and the stations run past
  it. Only the characters actually on screen are drawn: the list can be a
  couple of hundred characters long and this runs on every frame.
  """

  def __init__(
      self,
      screen: display.Display,
      font: fonts.Font,
      label: str = '',
      pixels_per_second: int = 60,
  ):
    super().__init__(screen)
    self._font = font
    self._label = label
    self._pixels_per_second = pixels_per_second
    self._label_width = font.calculate_bounds(label)[0] if label else 0
    self._text = ''
    self._offsets = [0]  # cumulative pixel width before each character
    self._scroll = 0
    self._scrolled_at = None
    self._needs_clear = False

  def set_text(self, text: str) -> None:
    if text == self._text:
      return

    self._text = text
    offsets = [0]
    for char in text:
      offsets.append(offsets[-1] + self._font.calculate_bounds(char)[0])
    self._offsets = offsets
    self._scroll = 0
    self._scrolled_at = None
    self._needs_clear = True

  def render(self, x: int, y: int, w: int, h: int) -> bool:
    if not self._text:
      if not self._needs_clear:
        return False
      self._screen.fill_rect(x, y, w, h, 0)
      self._needs_clear = False
      return True

    self._needs_clear = False
    self._screen.fill_rect(x, y, w, h, 0)

    window_x = x + self._label_width
    window_w = w - self._label_width
    width = self._offsets[-1]

    if width <= window_w:
      self._font.render_text(self._text, self._screen, window_x, y)
      self._render_label(x, y, h)
      return True

    # Find the run of characters visible in the window beside the label.
    scroll = int(self._scroll)
    start = 0
    while start < len(self._text) and self._offsets[start + 1] <= scroll:
      start += 1
    end = start
    while end < len(self._text) and self._offsets[end] < scroll + window_w:
      end += 1

    self._font.render_text(
        self._text[start:end],
        self._screen,
        window_x + self._offsets[start] - scroll,
        y,
    )

    self._render_label(x, y, h)

    # Advance by elapsed time, not by one frame, so the stations read at the
    # same pace whatever the refresh rate is and however long a frame took.
    now = time.ticks_ms()
    if self._scrolled_at is not None:
      elapsed = time.ticks_diff(now, self._scrolled_at)
      self._scroll += self._pixels_per_second * elapsed / 1000
    self._scrolled_at = now

    if self._scroll > width + _SCROLL_GAP:
      # Off the right hand edge of the window, ready to come round again.
      self._scroll = -window_w
    return True

  def _render_label(self, x: int, y: int, h: int) -> None:
    """Draws the label over the scroll, so it never moves.

    A part-scrolled character starts left of the window, which would otherwise
    creep under the label: blitting clips to the screen, not to the window.
    """
    if not self._label:
      return
    self._screen.fill_rect(x, y, self._label_width, h, 0)
    self._font.render_text(self._label, self._screen, x, y)


class MessageWidget(Widget):
  """Renders a message in the middle of screen."""

  def __init__(self, screen: display.Display, message: str, font: fonts.Font):
    super().__init__(screen)
    self._default_message = message
    self._font = font
    w, h = 0, 0
    for m in message.split('\n'):
      bounds = font.calculate_bounds(m)
      w = max(w, bounds[0])
      h += bounds[1]

    self._x = (screen.width - w) // 2
    self._y = (screen.height - h) // 2

  def render(self, message: str | None = None) -> bool:
    self._screen.fill(0)
    messages = (self._default_message if message is None else message).split(
        '\n'
    )
    for i, message in enumerate(messages):
      self._font.render_text(
          message,
          self._screen,
          self._x,
          self._y + (i * self._font.max_bounds()[1]),
      )
    return True


class DepartureWidget(Widget):
  """Class that renders a departure to provided display."""

  def __init__(
      self,
      screen: display.Display,
      font: fonts.Font,
      width: int,
      status_font: fonts.Font | None = None,
  ):
    super().__init__(screen)
    self._font = font
    self._width = width
    self._status_font = status_font if status_font else font
    self._max_clock_width = self._font.calculate_bounds('00:00')[0]
    self._max_ordinal_width = self._font.calculate_bounds(_ORDINALS[-1])[0]

    self._last_departure = None
    self._last_ordinal = None

  def bounds(self) -> tuple[int, int]:
    max_height = max(
        self._font.max_bounds()[1], self._status_font.max_bounds()[1]
    )
    return self._width, max_height

  def render(
      self,
      departure: trains.Departure | None,
      ordinal: str,
      x: int,
      y: int,
      w: int,
      h: int,
  ) -> bool:
    if self._last_departure == departure and self._last_ordinal == ordinal:
      return False

    self._last_departure = departure
    self._last_ordinal = ordinal
    self._screen.fill_rect(x, y, w, self._font.max_bounds()[1], 0)

    if departure is None:
      return True

    # The rotating row is numbered; the next train needs no telling.
    if ordinal:
      self._font.render_text(ordinal, self._screen, x, y)
      x += self._max_ordinal_width + _COLUMN_GAP

    self._font.render_text(
        _time_to_str(departure.departure_time), self._screen, x, y
    )
    x += self._max_clock_width + _COLUMN_GAP

    if departure.cancelled:
      status = 'Cancelled'
    elif departure.departure_time != departure.actual_departure_time:
      status = 'Exp {}'.format(_time_to_str(departure.actual_departure_time))
    else:
      status = 'On time'
    status_w = self._status_font.calculate_bounds(status)[0]
    status_x = w - status_w

    # Whatever room is left belongs to the destination.
    self._font.render_text(
        _truncate(self._font, departure.destination, status_x - _COLUMN_GAP - x),
        self._screen,
        x,
        y,
    )
    self._status_font.render_text(status, self._screen, status_x, y)
    return True


class MainWidget(Widget):
  """Class for the main display rendering."""

  def __init__(
      self,
      screen: display.Display,
      departure_updater: trains.DepartureUpdater,
      font: fonts.Font,
      clock_font: fonts.Font,
      scroll_speed: int = 60,
  ):
    super().__init__(screen)
    self._departure_updater = departure_updater

    # Four rows of the same height. Text uses seven of each row's nine pixels
    # and leaves two for descenders; the clock, having none, fills all nine.
    self._clock_widget = ClockWidget(screen, clock_font)
    self._no_departures_widget = NoDeparturesWidget(screen, font)
    self._calling_at_widget = ScrollingTextWidget(
        screen, font, _CALLING_AT, scroll_speed
    )
    self._first_widget = DepartureWidget(screen, font, screen.width)
    self._later_widget = DepartureWidget(screen, font, screen.width)

    self._text_height = font.max_bounds()[1]
    self._rows = _distribute(
        screen.height,
        (
            self._text_height,
            self._text_height,
            self._text_height,
            self._clock_widget.bounds()[1],
        ),
    )
    self._last_stale = None

  def render(self, now: tuple[int, ...]):
    """Render display. Currently assumes we're rendering entire display."""
    need_refresh = False
    departures = self._departure_updater.departures()

    if departures:
      self._no_departures_widget.clear()
      need_refresh |= self._first_widget.render(
          departures[0], '', 0, self._rows[0], *self._first_widget.bounds()
      )

      points = self._departure_updater.calling_points()
      # The label belongs to the widget; only the stations scroll.
      self._calling_at_widget.set_text(', '.join(points) if points else '')
      need_refresh |= self._calling_at_widget.render(
          0, self._rows[1], self._screen.width, self._text_height
      )

      # The third line works through the rest of the departures in turn.
      later, ordinal = None, ''
      later_count = min(len(departures), len(_ORDINALS)) - 1
      if later_count > 0:
        seconds = now[3] * 3600 + now[4] * 60 + now[5]
        index = 1 + (seconds // _ALTERNATE_SECONDS) % later_count
        later, ordinal = departures[index], _ORDINALS[index]
      need_refresh |= self._later_widget.render(
          later, ordinal, 0, self._rows[2], *self._later_widget.bounds()
      )
    else:
      need_refresh |= self._first_widget.render(
          None, '', 0, self._rows[0], *self._first_widget.bounds()
      )
      self._calling_at_widget.set_text('')
      need_refresh |= self._calling_at_widget.render(
          0, self._rows[1], self._screen.width, self._text_height
      )
      need_refresh |= self._later_widget.render(
          None, '', 0, self._rows[2], *self._later_widget.bounds()
      )
      need_refresh |= self._no_departures_widget.render(
          0, self._rows[1], self._screen.width,
          self._no_departures_widget.bounds()[1]
      )

    clock_bounds = self._clock_widget.bounds()
    x = (self._screen.width - clock_bounds[0]) // 2
    y = self._rows[3]

    need_refresh |= self._clock_widget.render(now, x, y, *clock_bounds)
    need_refresh |= self._render_stale_dot()
    return need_refresh

  def _render_stale_dot(self) -> bool:
    """A dot in the corner while the departures on show failed to refresh.

    Small enough to be invisible across a room, obvious if you know to look.
    Nothing at all is drawn while the data is current.
    """
    stale = self._departure_updater.stale()
    # Drawn every time rather than only on change, so that it comes back after
    # anything else clears the screen, such as waking from out of hours.
    self._screen.fill_rect(
        self._screen.width - _STALE_DOT_SIZE,
        self._screen.height - _STALE_DOT_SIZE,
        _STALE_DOT_SIZE,
        _STALE_DOT_SIZE,
        15 if stale else 0,
    )
    changed = stale != self._last_stale
    self._last_stale = stale
    return changed
