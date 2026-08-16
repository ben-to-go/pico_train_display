"""Fake `ntptime`: the host clock is already correct (and set to UTC)."""

host = 'pool.ntp.org'
timeout = 1


def time():
  import time as _time

  return _time.time()


def settime():
  pass
