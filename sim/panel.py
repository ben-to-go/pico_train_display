"""Emulator for an SSD1322 panel, driven by the real SPI command stream.

The firmware's `ssd1322.py` driver runs completely unmodified: the fake
`machine.SPI` forwards every byte it would put on the wire to `Panel.write()`,
which decodes the SSD1322 command set and reconstructs the picture the physical
panel would be showing.
"""

import sys
import time

# Redrawing faster than this shows a terminal nothing it can keep up with.
_MIN_REDRAW_MS = 60

# Braille cells, for terminals narrower than the panel.
_COMPACT = '--compact' in sys.argv

# Nibble-per-pixel greyscale value -> 8-bit sample.
_GREY = bytes(min(255, v * 17) for v in range(16))

# Commands that carry no argument bytes.
_NO_ARG = {0xA4, 0xA5, 0xA6, 0xA7, 0xA9, 0xAE, 0xAF, 0xB9, 0xE3, 0x5C}

# Commands we understand, mapped to how many argument bytes they take.
_ARG_COUNT = {
    0x15: 2,  # set column address
    0x75: 2,  # set row address
    0xA0: 2,  # set re-map / dual COM line mode
    0xA1: 1,  # display start line
    0xA2: 1,  # display offset
    0xAB: 1,  # function select
    0xB1: 1,  # phase length
    0xB3: 1,  # clock divide / oscillator frequency
    0xB4: 2,  # display enhancement A
    0xB5: 1,  # GPIO
    0xB6: 1,  # second pre-charge period
    0xBB: 1,  # pre-charge voltage
    0xBE: 1,  # VCOMH
    0xC1: 1,  # contrast current
    0xC7: 1,  # master contrast
    0xCA: 1,  # MUX ratio
    0xD1: 2,  # display enhancement B
    0xFD: 1,  # command lock
}


class Panel:
  """Reconstructs panel contents from the SSD1322 SPI stream."""

  def __init__(self, width: int = 256, height: int = 64, on_frame=None):
    self.width = width
    self.height = height
    self.pixels = bytearray(width * height)  # 4-bit values, one per byte
    # Drawing the terminal is the default: a fake display that shows nothing
    # would not be much of one.
    self.on_frame = self.draw if on_frame is None else on_frame
    self._drawn_at = None

    self.display_on = False
    self.all_pixels_off = False
    self.sleeping = False
    self.flipped = False
    self.frames = 0
    self.commands = 0

    self._cmd = None
    self._args = []
    self._pending = None  # odd leftover RAM byte
    self._col_start = self._col_end = 0
    self._row_start = self._row_end = 0
    self._col = self._row = 0

  # --- SPI side -------------------------------------------------------------

  def write(self, data, dc: int) -> None:
    """Accepts bytes from the fake SPI bus. dc=0 => command, dc=1 => data."""
    if dc == 0:
      for b in data:
        self._command(b)
    elif self._cmd == 0x5C:
      self._write_ram(data)
    else:
      self._argument(data)

  def _command(self, cmd: int) -> None:
    self.commands += 1
    self._cmd = cmd
    self._args = []
    self._pending = None

    if cmd == 0x5C:  # write RAM: reset the write pointer to window origin
      self._col, self._row = self._col_start, self._row_start
    elif cmd == 0xA4:
      self.all_pixels_off = True
    elif cmd in (0xA6, 0xA5, 0xA7):
      self.all_pixels_off = False
    elif cmd == 0xAF:
      self.display_on = True
      self.sleeping = False
    elif cmd == 0xAE:
      self.display_on = False

  def _argument(self, data) -> None:
    if self._cmd is None:
      return
    self._args.extend(data)
    if len(self._args) < _ARG_COUNT.get(self._cmd, 0):
      return

    a = self._args
    if self._cmd == 0x15:
      self._col_start, self._col_end = a[0], a[1]
      self._col, self._row = self._col_start, self._row_start
    elif self._cmd == 0x75:
      self._row_start, self._row_end = a[0], a[1]
      self._col, self._row = self._col_start, self._row_start
    elif self._cmd == 0xA0:
      # bit 1 = column address re-map, bit 4 = COM scan direction re-map.
      self.flipped = bool(a[0] & 0x02)
    elif self._cmd == 0xAB:
      self.sleeping = a[0] == 0x00

  def _write_ram(self, data) -> None:
    if self._pending is not None:
      data = bytes([self._pending]) + bytes(data)
      self._pending = None
    if len(data) % 2:
      self._pending = data[-1]
      data = data[:-1]

    for i in range(0, len(data), 2):
      x = (self._col - self._col_start) * 4
      y = self._row - self._row_start
      if 0 <= y < self.height and 0 <= x <= self.width - 4:
        b0, b1 = data[i], data[i + 1]
        o = y * self.width + x
        self.pixels[o] = b0 >> 4
        self.pixels[o + 1] = b0 & 0x0F
        self.pixels[o + 2] = b1 >> 4
        self.pixels[o + 3] = b1 & 0x0F

      self._col += 1
      if self._col > self._col_end:
        self._col = self._col_start
        self._row += 1
        if self._row > self._row_end:
          self._row = self._row_start
          self.frames += 1
          if self.on_frame:
            self.on_frame(self)

  # --- output ---------------------------------------------------------------

  def _visible(self):
    """Pixels as the eye would see them, honouring flip and display state."""
    if not self.display_on or self.all_pixels_off or self.sleeping:
      return bytearray(self.width * self.height)
    if not self.flipped:
      return self.pixels
    return bytearray(reversed(self.pixels))  # 180 degree rotation

  def draw(self, _=None) -> None:
    """Redraws the panel where it was, as often as a terminal can follow."""
    now = time.ticks_ms()
    if (self._drawn_at is not None
        and time.ticks_diff(now, self._drawn_at) < _MIN_REDRAW_MS):
      return
    self._drawn_at = now
    sys.stdout.write('\x1b[H' + self.to_ansi(_COMPACT) + '\x1b[K\n')

  def to_ansi(self, compact: bool = False) -> str:
    """Renders the panel as text.

    Default is one terminal cell per 1x2 pixels (256 columns x 32 rows), which
    is a 1:1 horizontal reproduction of the panel. `compact` uses braille cells
    of 2x4 pixels instead (128 columns x 16 rows) for narrower terminals.
    """
    return self._to_braille() if compact else self._to_halfblock()

  def _amber(self, value: int) -> str:
    # The real panel is a warm amber-on-black OLED.
    return '{};{};{}'.format(value, (value * 3) // 4, 0)

  def _to_halfblock(self) -> str:
    src = self._visible()
    w = self.width
    lines = []
    for y in range(0, self.height, 2):
      parts = []
      prev = None
      for x in range(w):
        top = _GREY[src[y * w + x]]
        bot = _GREY[src[(y + 1) * w + x]] if y + 1 < self.height else 0
        if (top, bot) != prev:
          parts.append(
              '\x1b[38;2;{}m\x1b[48;2;{}m'.format(
                  self._amber(top), self._amber(bot)
              )
          )
          prev = (top, bot)
        parts.append('▀')
      parts.append('\x1b[0m')
      lines.append(''.join(parts))
    return '\n'.join(lines)

  # Braille dot bit for each (dx, dy) within a 2x4 cell.
  _DOTS = ((0x01, 0x02, 0x04, 0x40), (0x08, 0x10, 0x20, 0x80))

  def _to_braille(self) -> str:
    src = self._visible()
    w = self.width
    lines = []
    for y in range(0, self.height, 4):
      parts = ['\x1b[38;2;{}m'.format(self._amber(255))]
      for x in range(0, w, 2):
        bits = 0
        for dx in range(2):
          for dy in range(4):
            px, py = x + dx, y + dy
            if px < w and py < self.height and src[py * w + px] >= 8:
              bits |= self._DOTS[dx][dy]
        parts.append(chr(0x2800 + bits))
      parts.append('\x1b[0m')
      lines.append(''.join(parts))
    return '\n'.join(lines)
