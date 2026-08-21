"""Fake `machine` module so firmware can run on the MicroPython unix port.

Pin state is kept in a shared table. The panel hangs off parallel8080 rather
than anything in here, so this is only ever asked to remember pin levels.
"""

PIN_STATE = {}


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


class RTC:

  def datetime(self, dt=None):
    import time

    if dt is None:
      t = time.localtime()
      return (t[0], t[1], t[2], t[6], t[3], t[4], t[5], 0)


class WDT:
  """A watchdog that watches nothing.

  The firmware arms one on the way down, so that a board which cannot ship its
  last log still reboots. There is no hardware timer here and nothing to
  reboot, so this only records the timeout: the simulator wants to reach its
  exit rather than be killed on the way to it.
  """

  def __init__(self, id=0, timeout=5000):
    self.id = id
    self.timeout = timeout

  def feed(self):
    pass


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
