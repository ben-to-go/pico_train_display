"""Fake `machine` module so firmware can run on the MicroPython unix port.

Pin state is kept in a shared table; the fake SPI looks up the D/C pin on every
transfer so it can tell the attached panel emulator whether the bytes it is
being handed are commands or data.
"""

PIN_STATE = {}

# GPIO the SSD1322 driver uses for D/C, and the bus it hangs off. Both from
# display.create(), which is where this project wires the panel up.
DC_PIN = 20
DISPLAY_BUS = 0


class Pin:
  """Minimal GPIO stand-in that just remembers its level."""

  IN = 0
  OUT = 1
  OPEN_DRAIN = 2
  PULL_UP = 1
  PULL_DOWN = 2

  def __init__(self, id, mode=-1, pull=None, *, value=None):
    self.id = id
    PIN_STATE.setdefault(id, 0)
    if value is not None:
      PIN_STATE[id] = int(value)

  def init(self, mode=-1, pull=None, *, value=None):
    if value is not None:
      PIN_STATE[self.id] = int(value)

  def value(self, val=None):
    if val is None:
      return PIN_STATE[self.id]
    PIN_STATE[self.id] = int(val)

  def __call__(self, val=None):
    return self.value(val)

  def on(self):
    self.value(1)

  def off(self):
    self.value(0)

  def low(self):
    self.value(0)

  def high(self):
    self.value(1)

  def __repr__(self):
    return 'Pin({}, value={})'.format(self.id, PIN_STATE[self.id])


class SPI:
  """SPI bus that forwards everything written to an attached sink."""

  MSB = 0
  LSB = 1

  def __init__(self, id, baudrate=1000000, polarity=0, phase=0, bits=8,
               firstbit=MSB, sck=None, mosi=None, miso=None):
    self.id = id
    self.baudrate = baudrate
    self.bytes_written = 0
    # Opening the display's bus is what brings the panel into being, so
    # nothing outside has to wire the two together.
    if id == DISPLAY_BUS:
      import panel

      self._panel = panel.Panel()
    else:
      self._panel = None

  def init(self, *args, **kwargs):
    pass

  def deinit(self):
    pass

  def write(self, buf):
    self.bytes_written += len(buf)
    if self._panel is not None:
      self._panel.write(bytes(buf), PIN_STATE.get(DC_PIN, 0))

  def write_readinto(self, tx, rx):
    self.write(tx)
    for i in range(len(rx)):
      rx[i] = 0

  def readinto(self, buf, write=0):
    for i in range(len(buf)):
      buf[i] = 0


class RTC:

  def datetime(self, dt=None):
    import time

    if dt is None:
      t = time.localtime()
      return (t[0], t[1], t[2], t[6], t[3], t[4], t[5], 0)


def reset():
  raise SystemExit('machine.reset() called')


def soft_reset():
  raise SystemExit('machine.soft_reset() called')


def freq(hz=None):
  return 150_000_000


def unique_id():
  return b'\x00\x01\x02\x03\x04\x05\x06\x07'


def idle():
  pass
