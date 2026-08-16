"""Runs the firmware on a desktop, with the panel drawn in the terminal.

The project is not modified and knows nothing about this. Four modules on
MICROPYPATH stand in for what a desktop does not have, and the rest is
main.py:

  machine.py   Pin and SPI; opening the display's bus creates the panel
  panel.py     the SSD1322 itself, decoding the driver's SPI stream and
               drawing what it reconstructs
  network.py   a CYW43 that is always associated
  ntptime.py   NTP, since the host clock is already right

Two things still need doing from out here: the firmware's log would scribble
over the panel, and the setup portal asks for port 80, which needs root.

  sim/run.sh    the display, at its real 256x64
"""

import sys
import time

# The setup portal binds port 80, which a normal user may not.
_SETUP_PORT = 8088
_LOG_PATH = 'sim/out/firmware.log'


def _divert_logging():
  """Sends the firmware's log to a file, leaving the panel legible."""
  import logging
  import os

  try:
    os.mkdir('sim/out')
  except OSError:
    pass
  log_file = open(_LOG_PATH, 'a')

  def log(msg, *args, **kwargs):
    now = time.localtime()
    log_file.write('[{:0>2}:{:0>2}:{:0>2}] {}\n'.format(
        now[3], now[4], now[5], str(msg).format(*args, **kwargs)))
    log_file.flush()

  logging.log = log


def main():
  import asyncio
  import network

  _divert_logging()
  network.IP_ADDRESS = '127.0.0.1:{}'.format(_SETUP_PORT)
  start_server = asyncio.start_server
  asyncio.start_server = lambda cb, host, port, *a, **kw: start_server(
      cb, '127.0.0.1', _SETUP_PORT, *a, **kw)

  import main as firmware

  sys.stdout.write('\x1b[2J\x1b[?25l')  # clear, hide the cursor
  try:
    firmware.main()
  except KeyboardInterrupt:
    pass
  finally:
    sys.stdout.write('\x1b[?25h\n')
    print('Firmware log: {}'.format(_LOG_PATH))


main()
